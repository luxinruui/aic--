"""OFF-only check for the NEW receiver. Never enables arms or chassis."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import socket
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from dual_link.profile import AXES, load
from dual_link.protocol import APP, VERSION, PORT, JsonLines, check_reply, host_address


def check(host, port=PORT, seconds=5., expect=None):
    profile = load(ROOT)
    with socket.create_connection((host_address(host),port),timeout=2.) as connection:
        wire = JsonLines(connection)
        hello = wire.read(timeout=2.)
        session, challenge = check_reply(hello,"hello",profile.signature,seq=0)
        if expect is not None and hello["hardware"] != (expect == "hardware"):
            raise ValueError("Unexpected receiver hardware mode")
        initial = hello.get("arm_segments",0)
        seq, deadline = 0,time.monotonic()+seconds
        while time.monotonic() < deadline:
            seq += 1
            wire.send(dict(app=APP,v=VERSION,type="intent",profile=profile.signature,session=session,challenge=challenge,
                seq=seq,generation=0,input_session="off-check-session",frame_id=0,enabled=False,source="none",age_ms=0.,
                stage="off",mode="both",forward=0.,yaw_left=0.,targets=dict.fromkeys(AXES),groups=[False]*5))
            reply = wire.read()
            _,challenge = check_reply(reply,"ack",profile.signature,session,seq)
            if (reply["state"] != "DISARMED" or reply["command"] != [0,0,0]
                    or reply.get("arm_segments",0) != initial or not reply["rearm_required"]):
                raise ValueError("Receiver was not continuously stopped")
            time.sleep(.05)
    return dict(at=datetime.now().astimezone().isoformat(),check="OFF only",passed=True,acknowledgements=seq,
                hardware=hello["hardware"],baseline_confirmed=hello["baseline_confirmed"],
                arm_segments_added=0,nonzero_chassis_commands=0,profile=profile.signature)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--port",type=int,default=PORT)
    parser.add_argument("--seconds",type=float,default=5)
    parser.add_argument("--expect",choices=("preview","hardware"))
    parser.add_argument("--output",type=Path)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not .2 <= args.seconds <= 60:
        parser.error("Invalid port/check duration")
    try:
        result = check(args.host,args.port,args.seconds,args.expect)
        text = json.dumps(result,ensure_ascii=False,indent=2)
        print(text)
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(text,encoding="utf-8")
        return 0
    except (ValueError,OSError,EOFError) as error:
        print("OFF check failed:",error)
        return 1


if __name__ == "__main__":raise SystemExit(main())
