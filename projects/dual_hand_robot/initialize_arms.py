"""Explicit one-time 5-second move to the measured baseline; default is check-only.

The vendor exposes a trajectory duration, but no verified position-readback or
immediate arm-cancel command. After submission the arm move can finish even if
this process is interrupted. Do not confuse elapsed time with position feedback.
"""
from datetime import datetime
import argparse
import json
from pathlib import Path
import struct
import sys
import time

ROOT = Path(__file__).resolve().parent
DURATION_MS = 5000
SLOTS = ('1','2','3','4','20','21','31','32','33','34')


def frame(profile):
    positions = dict(profile.arm['baseline_positions'], **{'20':0,'21':0})
    for name, axis in profile.arm['axes'].items():
        value = positions[str(axis['servo_id'])]
        if not axis['min_position'] <= value <= axis['max_position']:
            raise ValueError('Baseline outside measured joint bounds: '+name)
    payload = struct.pack('<11H', *(positions[key] for key in SLOTS), DURATION_MS)
    body = bytes((7,0x5f,len(payload)))+payload
    return b'\xf5\x5f'+body+bytes(((~sum(body)) & 255,))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--move',action='store_true',help='explicitly execute the single 5-second baseline move')
    args = parser.parse_args(argv)
    from dual_link.profile import load
    profile = load(ROOT)
    data = frame(profile)
    print('Baseline:',json.dumps(profile.arm['baseline_positions'],sort_keys=True),flush=True)
    print('Single arm trajectory: 5000ms; waist holds; chassis stays stopped.',flush=True)
    print('No verified arm cancel: after submission arms may continue for up to 5 seconds.',flush=True)
    if not args.move:
        print('CHECK ONLY; UART NOT OPENED; packet:',data.hex(),flush=True)
        return 0
    if not sys.platform.startswith('linux'):
        parser.error('Real motion runs only on the Linux backpack')
    from dual_link.bus import Bus
    directory = ROOT/'logs/verification'
    directory.mkdir(parents=True,exist_ok=True)
    report = directory/('baseline-command-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
    result = dict(at=datetime.now().astimezone().isoformat(),duration_ms=DURATION_MS,
                  baseline=profile.arm['baseline_positions'],profile=profile.signature,
                  packet=data.hex(),submitted=False,wait_completed=False,actual_pose_confirmed=False)
    bus = None
    try:
        bus = Bus(profile,drive=True)
        bus.send(data)
        result['submitted'] = True
        print('Baseline trajectory submitted. Observe robot; do not start another controller.',flush=True)
        time.sleep(DURATION_MS/1000.+.5)
        result['wait_completed'] = True
        print('Command duration elapsed. Confirm actual pose before starting the hand receiver.',flush=True)
    except (OSError,RuntimeError,ImportError,KeyboardInterrupt) as error:
        result['error'] = str(error) or type(error).__name__
        print('Stopped:',result['error'],'An already-submitted arm trajectory may still finish.',flush=True)
    finally:
        if bus:
            bus.close()
        report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Command report:',report,flush=True)
    return 0 if result['wait_completed'] else 1


if __name__ == '__main__':raise SystemExit(main())
