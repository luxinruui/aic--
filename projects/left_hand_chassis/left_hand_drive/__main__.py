import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent


def check_model():
    import importlib.metadata as metadata
    import cv2
    import mediapipe as mp
    import numpy as np
    import tkinter
    if not hasattr(mp, "solutions"):
        raise RuntimeError("Use the project environment: MediaPipe 0.10.21 is required")
    with mp.solutions.hands.Hands(static_image_mode=True, model_complexity=1, max_num_hands=2) as detector:
        result = detector.process(np.zeros((480,640,3),dtype=np.uint8))
        assert not result.multi_hand_landmarks
    print("CPU hand model loaded; blank-frame inference passed. No camera opened.")
    for name in ("mediapipe","opencv-contrib-python","numpy","Pillow"):
        print(name + "=" + metadata.version(name))


def run_demo(frames=280):
    from .capture import demo_observation
    from .core import Calibration, Controller, PoseReference
    from .runlog import RunLog
    controller=Controller()
    cal=Calibration(*(PoseReference(demo_observation(1,0,g).fingers,0,"demo") for g in ("NEUTRAL","FIST","OPEN")))
    controller.set_calibration(cal)
    controller.enable()
    log=RunLog(ROOT,"validation-demo")
    log.calibration(cal)
    seen=set()
    try:
        for i in range(frames):
            now=100+i*.05
            gesture="NEUTRAL" if i<20 or i>=230 else "FIST" if i<120 else "OPEN"
            roll=0 if i<20 else 28 if i<85 else -28 if i<180 else 0
            observation=demo_observation(i+1,now,gesture,roll)
            controller.update(observation,now)
            command=controller.tick(now)
            log.record(command,observation)
            if command.forward>0: seen.add("forward")
            if command.forward<0: seen.add("backward")
            if command.yaw_left>0: seen.add("left")
            if command.yaw_left<0: seen.add("right")
        command=controller.tick(now+.4)
        assert not command.enabled and command.forward==command.yaw_left==0
        log.record(command)
        assert seen=={"forward","backward","left","right"},seen
        print("Synthetic lifecycle passed:",", ".join(sorted(seen)),"+ stale-input STOP")
        print("Numeric run log:",log.path.relative_to(ROOT))
    finally:
        log.close("synthetic validation completed")


def main(argv=None):
    parser=argparse.ArgumentParser(description="PC-only left-hand chassis control preview; no robot outputs")
    parser.add_argument("--demo",action="store_true",help="synthetic input; no camera")
    parser.add_argument("--headless",action="store_true",help="run deterministic synthetic validation")
    parser.add_argument("--check",action="store_true",help="check model on blank pixels; no camera")
    parser.add_argument("--camera",type=int,default=0)
    args=parser.parse_args(argv)
    if args.check:
        check_model()
        return 0
    if args.headless:
        if not args.demo:
            parser.error("--headless requires --demo; real camera validation is interactive")
        run_demo()
        return 0
    import tkinter as tk
    from .gui import App
    root=tk.Tk()
    App(root,ROOT,demo=args.demo,camera=args.camera)
    root.mainloop()
    return 0


if __name__=="__main__":
    raise SystemExit(main())
