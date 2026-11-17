"""Verify an imported package and run a loopback PREVIEW check without UART."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import platform
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parent


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=5.)
    args = parser.parse_args(argv)
    if not .2 <= args.seconds <= 60:
        parser.error("Check duration must be between 0.2 and 60 seconds")
    directory = ROOT/"logs/verification"/("import-check-"+datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    directory.mkdir(parents=True)
    result = dict(at=datetime.now().astimezone().isoformat(), passed=False,
                  scope="package/software/loopback-preview", hardware=False,
                  serial_opened=False, motion_tested=False, python=platform.python_version(),
                  system=platform.system(), architecture=platform.machine(), checks={})
    server = controller = thread = journal = None
    errors = []
    try:
        from install_backpack import verify
        result["checks"]["package_files"] = len(verify(ROOT)["files"])
        for flag, name in (("--check-deps", "offline-dependencies"), ("--self-test", "self-test")):
            with (directory/(name+".txt")).open("w", encoding="utf-8") as output:
                process = subprocess.run([sys.executable, "-S", str(ROOT/"run_backpack.py"), flag],
                                         cwd=str(ROOT), stdout=output, stderr=subprocess.STDOUT, timeout=60)
            result["checks"][name] = process.returncode == 0
            if process.returncode:
                raise RuntimeError(name+" failed; inspect its text log")
        from dual_link.profile import load
        from dual_link.bus import Bus
        from dual_link.control import Controller
        from dual_link.server import Server
        from run_backpack import Journal
        from scripts.check_connection import check
        profile = load(ROOT)
        # Explicit PREVIEW, loopback and an allocated port: leave existing services alone.
        controller = Controller(profile, Bus(profile, drive=False))
        journal = Journal(directory/"receiver")
        server = Server(controller, profile, bind="127.0.0.1", port=0, logger=journal)

        def serve():
            try:
                server.serve()
            except Exception as error:
                errors.append(str(error))

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        if not server.listening.wait(3.) or errors or server.error:
            raise RuntimeError("Preview receiver failed to listen: "+str(errors or server.error))
        result["checks"]["loopback_off"] = check("127.0.0.1", server.port, args.seconds, "preview")
        server.stop()
        thread.join(3.)
        if thread.is_alive() or errors or server.error:
            raise RuntimeError("Preview receiver failed to stop: "+str(errors or server.error))
        result["passed"] = True
    except (OSError, ValueError, KeyError, RuntimeError, ImportError, subprocess.SubprocessError) as error:
        result["error"] = str(error)
    finally:
        if server:
            server.stop()
        if thread and thread.is_alive():
            thread.join(3.)
        if controller and not (thread and thread.is_alive()):
            controller.close()
        if journal and not (thread and thread.is_alive()):
            journal.close()
    report = directory/"summary.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS" if result["passed"] else "FAIL", "| UART NOT OPENED |", report, flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
