"""Exercise real Tk with synthetic hands only; never opens a camera or robot."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from left_hand_drive.capture import Failure, Sample
from left_hand_drive.core import PoseReference, extract_hand, extract_left
from left_hand_drive.gui import App
from left_hand_drive.upper import AXES, ArmCalibration
from test_control import hand_points


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        root = tk.Tk()
        failures = []
        root.report_callback_exception = lambda *error: failures.append(str(error))
        app = App(root, temporary, upper=True, demo=True)
        panel = app.upper_preview
        def pump(seconds):
            deadline = time.monotonic()+seconds
            while time.monotonic() < deadline:
                root.update()
                time.sleep(.01)
        def snapshot():
            return json.loads((Path(temporary)/"runtime/latest_upper_preview.json").read_text(encoding="utf-8"))
        try:
            root.focus_force()
            pump(.95)
            assert panel.controller.state == "ON", panel.status.get()
            assert app.controller.state == "OFF"
            assert all(value is not None for value in snapshot()["targets"].values())
            assert app.photo is not None
            for width, height in ((1280, 860), (1050, 720)):
                root.geometry("%dx%d" % (width, height))
                pump(.1)
                for widget in (*panel.cal_buttons, app.enable_button, app.calibration_row):
                    assert widget.winfo_ismapped()
                    assert widget.winfo_rooty()+widget.winfo_height() <= root.winfo_rooty()+root.winfo_height()
            root.geometry("1280x860")
            app.demo_started = time.monotonic()-2.5
            pump(.4)
            if args.screenshot:
                from PIL import ImageGrab
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                x, y = root.winfo_rootx(), root.winfo_rooty()
                ImageGrab.grab(bbox=(x, y, x+root.winfo_width(), y+root.winfo_height())).save(args.screenshot)
            root.event_generate("<space>")
            pump(.1)
            assert not snapshot()["enabled"]
            assert all(value is None for value in snapshot()["targets"].values())
            assert all(panel.table.item(key, "values")[3] == "—" for key in AXES)
            panel.open_settings()
            root.update()
            assert panel.settings_window.winfo_exists()
            assert not snapshot()["enabled"]
            box = panel.settings_window.winfo_children()[0]
            entries = [widget for widget in box.winfo_children() if isinstance(widget, tk.ttk.Entry)]
            save = next(widget for widget in box.winfo_children() if isinstance(widget, tk.ttk.Button))
            entries[0].delete(0, "end")
            entries[0].insert(0, "nan")
            save.invoke()
            assert panel.settings_window.winfo_exists()
            entries[0].delete(0, "end")
            entries[0].insert(0, "5")
            save.invoke()
            assert not panel.settings_window.winfo_exists()
            assert panel.controller.settings.ranges[0][0] == 5
            root.focus_force()

            panel.mode.set("仅底盘")
            app.start_demo()
            pump(.9)
            assert app.controller.state == "ON", app.detail.get()
            assert panel.controller.state == "OFF"
            panel.mode.set("双手联合")
            app.start_demo()
            pump(.9)
            assert app.controller.state == panel.controller.state == "ON"
            last = panel.controller.last
            panel.controller.update(replace(last, frame_id=last.frame_id+1, sampled_at=time.monotonic(), valid=False,
                                            reason="合成丢右手"), time.monotonic())
            panel.tick(time.monotonic(), True)
            assert app.controller.state == panel.controller.state == "OFF"
            pump(.1)
            assert not snapshot()["enabled"]

            # A genuine Sample structure with only RIGHT landmarks must work even
            # when the chassis observation from that same frame is invalid.
            class FakeWorker:
                item = None
                def latest(self):
                    item, self.item = self.item, None
                    return item
                def is_alive(self): return True
                def stop(self): pass
                def join(self, timeout): pass
            import numpy as np
            app.demo = False
            panel.mode.set("仅上肢")
            panel.controller.invalidate()
            hands = (("Right", .99, hand_points()),)
            features = extract_hand(hands, 640, 480, 1, time.monotonic(), side="Right").fingers
            cal = ArmCalibration(*(PoseReference(tuple(v+delta for v in features), 0, "camera") for delta in (0, -.3, .3)))
            panel.controller.set_calibration(cal)
            app.worker = worker = FakeWorker()
            app.enable()
            for frame in range(1, 18):
                now = time.monotonic()
                left = extract_left(hands, 640, 480, frame, now)
                assert not left.valid
                worker.item = Sample(left, np.zeros((480,640,3), dtype=np.uint8), hands)
                pump(.06)
            assert panel.controller.state == "ON", panel.status.get()
            assert app.controller.state == "OFF"

            # Test real calibration widget flow, including single-finger repair.
            panel.begin_calibration("neutral")
            app.pending = None
            start = time.monotonic()-1.1
            panel.pending["start"] = start
            for i in range(27):
                now = start+i*.04
                panel.capture_reference(replace(panel.controller.last, frame_id=100+i, sampled_at=now), now)
            assert "neutral" in panel.references
            for stage, delta in (("fist", -.3), ("opened", .3)):
                panel.begin_calibration(stage)
                panel.pending["start"] = start
                for i in range(27):
                    now = start+i*.04
                    panel.capture_reference(replace(panel.controller.last, frame_id=120+i, sampled_at=now,
                                                     fingers=tuple(v+delta for v in features)), now)
            assert panel.controller.calibration is not None
            assert (app.log.path/"upper_calibration.json").is_file()
            baseline = panel.references["neutral"].fingers
            panel.selected_finger.set("食指")
            panel.begin_calibration("neutral")
            panel.pending["start"] = start
            for i in range(27):
                now = start+i*.04
                fingers = list(features)
                fingers[1] -= .02
                panel.capture_reference(replace(panel.controller.last, fingers=tuple(fingers), frame_id=150+i,
                                                 sampled_at=now), now)
            repaired = panel.references["neutral"].fingers
            assert repaired[1] != baseline[1]
            assert all(repaired[i] == baseline[i] for i in (0,2,3,4))

            worker.item = Failure("合成摄像头失败")
            pump(.08)
            assert panel.controller.state == app.controller.state == "OFF"
            assert all(v is None for v in snapshot()["targets"].values())
            app.worker = None
            app.close_input()
            assert panel.controller.calibration is None and not panel.references
            assert not failures, failures
            print("Real Tk dual-hand smoke passed: three modes, right-only Sample, paired targets, pause clears table,")
            print("single-finger calibration, camera failure, fresh-start calibration and responsive controls.")
            print("Synthetic fixtures only; no camera, TCP, UART or robot used.")
        finally:
            app.worker = None
            app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
