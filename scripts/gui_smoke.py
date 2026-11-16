"""Run real Tk with demo input and verify mode switching, stop and log output."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    import tkinter as tk
    from aic_hand.config import load
    from aic_hand.gui import MODE_NAMES, PreviewApp
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        root = tk.Tk()
        failures = []
        root.report_callback_exception = lambda *error: failures.append(str(error))
        app = PreviewApp(root, load(), Path(temporary) / "settings.json", log_directory=Path(temporary) / "run")
        def pump(seconds):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                root.update()
                time.sleep(.01)
        try:
            app.start(True)
            pump(.7)
            assert sum(axis.valid for axis in app.mapping.axes.values()) == 1, app.mapping
            app.mode_var.set(next(key for key, value in MODE_NAMES.items() if value == "six"))
            app.change_mode()
            pump(.5)
            assert sum(axis.valid for axis in app.mapping.axes.values()) == 6
            assert "演示" in app.status_var.get()
            app.open_calibration()
            root.update()
            app.calibrate("pinch_closed")
            assert "演示数据不能用于标定" in app.status_var.get()
            app.calibration_window.destroy()
            app.save_settings()
            assert (Path(temporary) / "settings.json").is_file()
            pump(.15)
            if args.screenshot:
                from PIL import ImageGrab
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                x, y = root.winfo_rootx(), root.winfo_rooty()
                ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())).save(args.screenshot)
            directory = app.log.directory
            app.stop()
            root.update()
            assert all(not axis.valid and axis.target is None for axis in app.mapping.axes.values())
            assert all(app.table.item(axis, "values")[1] == "—" for axis in app.mapping.axes)
            manifest = json.loads((directory / "run.json").read_text(encoding="utf-8"))
            assert manifest["status"] == "stopped" and manifest["robot_connected"] is False
            pump(.15)
            app.log_directory = Path(temporary) / "restart"
            app.start(True)
            pump(.25)
            assert app.log.directory != directory
            assert not failures, failures
            print("GUI smoke passed: single/six modes, calibration guard, settings save, pause clears targets, restart and logs.")
        finally:
            app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
