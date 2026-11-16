"""Linux backpack entry point. Preview is default and imports no camera stack."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import signal
import sys
import threading

from robot_link.config import load
from robot_link.control import ChassisController
from robot_link.protocol import PORT
from robot_link.server import Server

ROOT = Path(__file__).resolve().parent


class Journal:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".jsonl")
        self.file = path.open("w", encoding="utf-8")
        self.lock = threading.Lock()
        print("Backpack log: " + str(path), flush=True)

    def __call__(self, item):
        line = json.dumps(dict(at=datetime.now().astimezone().isoformat(), **item), ensure_ascii=False)
        with self.lock:
            self.file.write(line + "\n")
            self.file.flush()
            print(line, flush=True)

    def close(self):
        self.file.close()


def main(argv=None):
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,"reconfigure"):
            stream.reconfigure(encoding="utf-8",errors="backslashreplace")
    parser = argparse.ArgumentParser(description="AIC left-hand chassis TCP receiver; default PREVIEW, no UART")
    parser.add_argument("--drive", action="store_true", help="explicitly open Linux robot UART and drive chassis")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--config", type=Path, default=ROOT / "robot_config.json")
    parser.add_argument("--log-dir", type=Path, default=ROOT / "logs/backpack")
    parser.add_argument("--self-test", action="store_true", help="software-only receiver/protocol/codec tests; no UART")
    parser.add_argument("--check-deps", action="store_true", help="verify offline pySerial import; do not open UART")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("Invalid TCP port")
    if args.check_deps:
        if args.drive:
            parser.error("--check-deps cannot be combined with --drive")
        from robot_link.offline import load_serial
        serial = load_serial()
        print("Offline pySerial %s loaded; UART NOT OPENED" % serial.VERSION)
        return 0
    if args.self_test:
        if args.drive:
            parser.error("--self-test cannot be combined with --drive")
        import unittest
        suite = unittest.defaultTestLoader.discover(str(ROOT / "robot_tests"))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    writer = controller = journal = server = None
    try:
        config = load(args.config)
        journal = Journal(args.log_dir)
        if args.drive:
            from robot_link.codec import SerialWriter
            writer = SerialWriter(config)
        controller = ChassisController(config, writer)
        server = Server(controller, config, args.bind, args.port, journal)
        signal.signal(signal.SIGTERM, lambda *_: server.stop())
        print("Listening %s:%s | %s | default OFF" %
              (args.bind, args.port, "HARDWARE / UART" if args.drive else "PREVIEW / NO UART"), flush=True)
        server.serve()
    except KeyboardInterrupt:
        print("Stopped by Ctrl+C.", flush=True)
    except (ValueError, OSError, RuntimeError, ImportError) as error:
        print("Cannot start: %s\nSee README_BACKPACK.md" % error, file=sys.stderr, flush=True)
        return 1
    finally:
        if server:
            server.stop()
        if controller:
            controller.close()
        elif writer:
            writer.close()
        if journal:
            journal.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
