"""Real Tk + full PC TCP -> dual scheduler -> FakeSerial. No robot/camera."""
from dataclasses import replace
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import tkinter as tk

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"tests"))
from test_control import hand_points
from left_hand_drive.capture import Sample
from left_hand_drive.core import PoseReference, extract_hand, extract_left
from left_hand_drive.upper import ArmCalibration
from left_hand_drive.dual_gui import RobotApp
from dual_link.profile import load
from dual_link.bus import Bus
from dual_link.control import Controller
from dual_link.server import Server


def main():
    class FakeSerial:
        frames = []
        def write(self,data): self.frames.append(data);return len(data)
        def close(self): pass
    class FakeWorker:
        item = None
        def latest(self): item,self.item = self.item,None;return item
        def is_alive(self): return True
        def stop(self): pass
        def join(self,timeout): pass
    serial = FakeSerial()
    profile = load(ROOT)
    controller = Controller(profile,Bus(profile,True,lambda **kw:serial),baseline_confirmed=True)
    server = Server(controller,profile,"127.0.0.1",0)
    thread = threading.Thread(target=server.serve,daemon=True)
    thread.start();assert server.listening.wait(2)
    import numpy as np
    with tempfile.TemporaryDirectory() as temporary:
        for name in ("robot_config.json","arm_config.json"):
            shutil.copy2(ROOT/name,Path(temporary)/name)
        root = tk.Tk()
        failures = []
        root.report_callback_exception = lambda *error:failures.append(str(error))
        app = RobotApp(root,temporary)
        def pump(seconds):
            deadline = time.monotonic()+seconds
            while time.monotonic() < deadline:
                root.update();time.sleep(.01)
        try:
            root.focus_force();pump(.2)
            root.geometry("1050x720");root.update()
            assert app.connect_button.winfo_ismapped()
            assert app.connect_button.winfo_width() >= app.connect_button.winfo_reqwidth()
            app.connection_dialog();root.update()
            assert app.connection_window.winfo_exists()
            app.connection_window.destroy();root.focus_force()
            app.connect_robot("127.0.0.1",server.port)
            deadline = time.monotonic()+2
            while not app.client.status()["connected"] and time.monotonic() < deadline:pump(.03)
            assert app.client.status()["connected"],app.client.status()
            app.start_demo()
            assert not app.demo, "Hardware mode must reject synthetic demos"
            from left_hand_drive.runlog import RunLog
            app.log = RunLog(temporary,"validation-fixture")
            app.log.info["synthetic_camera_fixture"] = True
            app.upper_preview.start_log()
            app.worker = worker = FakeWorker()
            hands = (("Right",.99,hand_points()),)
            features = extract_hand(hands,640,480,1,time.monotonic(),side="Right").fingers
            cal = ArmCalibration(*(PoseReference(tuple(v+d for v in features),0,"camera") for d in (-.2,-.4,0)))
            app.upper_preview.controller.set_calibration(cal)
            app.enable()
            frame = 0
            def feed(hands):
                nonlocal frame
                frame += 1
                now = time.monotonic()
                worker.item = Sample(extract_left(hands,640,480,frame,now),np.zeros((480,640,3),dtype=np.uint8),hands)
                pump(.06)
            for _ in range(22):feed(hands)
            assert app.upper_preview.controller.state == "ON",app.detail.get()
            assert app.client.phase == "ACTIVE",app.client.status()
            assert controller.issued_arm_segments == 0, "Matching initial pose must not move an arm"
            bent = list(hand_points())
            bent[8] = (bent[5][0],bent[5][1]-.01,0)
            for _ in range(22):feed((("Right",.99,bent),))
            assert controller.issued_arm_segments > 0,app.client.status()
            assert controller.positions["1"] > profile.positions["1"]
            assert controller.chassis.command == (0,0,0)
            root.event_generate("<space>")
            pump(.14)
            assert controller.chassis.active is None
            count = controller.issued_arm_segments
            for _ in range(3):feed(hands)
            assert controller.issued_arm_segments == count
            assert not failures,failures
            app.worker = None
            app.close_input()
            print("Dual robot Tk + actual TCP + real encoder/FakeSerial passed: software reference takeover,")
            print("right-only camera-shaped fixture, elbow movement, hardware-demo rejection and SPACE STOP.")
            print("No physical camera or UART used.")
        finally:
            app.worker = None
            app.close();server.stop();thread.join(2)
    assert not thread.is_alive()
    return 0


if __name__ == "__main__":raise SystemExit(main())
