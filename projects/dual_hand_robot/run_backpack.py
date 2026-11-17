"""Independent dual-hand backpack service. Default PREVIEW opens no UART."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import signal
import sys
import threading

ROOT = Path(__file__).resolve().parent


class Journal:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory/(datetime.now().strftime("%Y%m%d-%H%M%S-%f")+".jsonl")
        self.file = self.path.open("w", encoding="utf-8")
        self.lock = threading.Lock()
        print("Backpack log:", self.path, flush=True)

    def __call__(self, item):
        with self.lock:
            line = json.dumps(dict(at=datetime.now().astimezone().isoformat(), **item), ensure_ascii=False, allow_nan=False)
            self.file.write(line+"\n")
            self.file.flush()
            print(line, flush=True)

    def close(self): self.file.close()


def main(argv=None):
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,"reconfigure"):
            stream.reconfigure(encoding="utf-8",errors="backslashreplace")
    parser = argparse.ArgumentParser(description="Independent dual-hand TCP8770 receiver; default NO UART")
    parser.add_argument("--drive", action="store_true")
    parser.add_argument("--confirm-arm-baseline", action="store_true", help="operator has set the original eight-slot baseline")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--check-deps", action="store_true")
    parser.add_argument("--log-dir", type=Path, default=ROOT/"logs/backpack")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("Invalid TCP port")
    if args.drive and (args.self_test or args.check_deps):
        parser.error("Software checks cannot be combined with --drive")
    if args.check_deps:
        from robot_link.offline import load_serial
        print("Offline pySerial", load_serial().VERSION, "verified; UART NOT OPENED")
        return 0
    if args.self_test:
        import unittest
        suite = unittest.defaultTestLoader.discover(str(ROOT/"dual_tests"))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    from dual_link.profile import load
    from dual_link.bus import Bus
    from dual_link.control import Controller
    from dual_link.server import Server
    bus = controller = server = journal = None
    try:
        profile = load(ROOT)
        journal = Journal(args.log_dir)
        bus = Bus(profile, drive=args.drive)
        controller = Controller(profile, bus, args.confirm_arm_baseline)
        server = Server(controller, profile, args.bind, args.port, journal)
        signal.signal(signal.SIGTERM, lambda *_: server.stop())
        print("Listening %s:%d | %s | starts OFF | arm baseline=%s" %
              (args.bind, args.port, "HARDWARE" if args.drive else "PREVIEW / NO UART",controller.baseline_confirmed), flush=True)
        server.serve()
    except KeyboardInterrupt:
        print("Stopped by Ctrl+C", flush=True)
    except (ValueError, OSError, RuntimeError, ImportError) as error:
        print("Cannot start:", error, file=sys.stderr, flush=True)
        return 1
    finally:
        if server: server.stop()
        if controller: controller.close()
        elif bus: bus.close()
        if journal: journal.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
