"""Check a real receiver connection using OFF packets only; never arm motion."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from left_hand_drive.core import Controller
from robot_link.client import Client
from robot_link.config import load, signature


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="backpack IPv4")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--seconds", type=float, default=5)
    parser.add_argument("--expect", choices=("preview", "hardware"), required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not 2 <= args.seconds <= 30:
        parser.error("--seconds must be in 2..30")
    config = load(ROOT / "robot_config.json")
    controller = Controller()  # Remains OFF; no calibration, enable or camera.
    client = Client(args.host, config, args.port)
    report = dict(at=datetime.now().astimezone().isoformat(), check="off_only_connection",
                  expected=args.expect, profile=signature(config), success=False,
                  nonzero_sent=False, ack_seq=0, states=[])
    try:
        client.publish(controller.command(time.monotonic()))
        client.start()
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            client.publish(controller.command(time.monotonic()))
            status = client.status()
            if status.get("error"):
                raise RuntimeError(status["error"])
            if status["connected"]:
                if status["hardware"] != (args.expect == "hardware"):
                    raise RuntimeError("Receiver mode differs from --expect")
                if status["state"] != "DISARMED":
                    raise RuntimeError("Receiver did not stay DISARMED: " + status["state"])
                if any(status.get("command", [0, 0, 0])):
                    raise RuntimeError("Receiver reported a nonzero command")
                report["ack_seq"] = max(report["ack_seq"], status["ack_seq"])
                report["states"] = ["DISARMED"]
            time.sleep(.05)
        if report["ack_seq"] < 20:
            raise RuntimeError("Fewer than 20 acknowledged OFF packets")
        report["success"] = True
    except (OSError, ValueError, RuntimeError) as error:
        report["error"] = str(error)
    finally:
        client.close()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
