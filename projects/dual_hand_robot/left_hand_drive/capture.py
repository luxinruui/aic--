"""Camera ownership and an overwrite-only result queue; local CPU inference."""
from dataclasses import dataclass
import math
import os
import queue
import threading
import time

from .core import Observation, extract_left

CONNECTIONS = ((0,1),(1,2),(2,3),(3,4),(0,5),(5,6),(6,7),(7,8),
               (5,9),(9,10),(10,11),(11,12),(9,13),(13,14),(14,15),(15,16),
               (13,17),(0,17),(17,18),(18,19),(19,20))


@dataclass
class Sample:
    observation: Observation
    image: object
    hands: tuple


@dataclass
class Failure:
    message: str


def demo_observation(frame_id, now, gesture="NEUTRAL", roll=0):
    # Deliberately synthetic feature-level input, never passed off as detection.
    fingers = {"NEUTRAL": (1.05,.60,.60,.60,.60),
               "FIST": (.55,.25,.25,.25,.25),
               "OPEN": (1.55,.96,.96,.96,.96)}[gesture]
    return Observation(frame_id, now, "demo", fingers, roll, True, "合成演示")


class Worker(threading.Thread):
    def __init__(self, camera=0, swap=False):
        super().__init__(name="left-hand-camera", daemon=True)
        self.camera, self.swap = camera, swap
        self.stopping = threading.Event()
        self.messages = queue.Queue(maxsize=1)

    def latest(self):
        try:
            return self.messages.get_nowait()
        except queue.Empty:
            return None

    def publish(self, item):
        try:
            self.messages.get_nowait()
        except queue.Empty:
            pass
        self.messages.put_nowait(item)

    def stop(self):
        self.stopping.set()

    def run(self):
        cap = None
        try:
            import cv2
            import mediapipe as mp
            if not hasattr(mp, "solutions"):
                raise RuntimeError("请用 run_pc.cmd 启动项目独立环境（需要 MediaPipe 0.10.21）")
            cap = cv2.VideoCapture(self.camera, cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY)
            if not cap.isOpened():
                raise RuntimeError("摄像头%d打不开，请关闭其他相机程序或更换编号" % self.camera)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            with mp.solutions.hands.Hands(max_num_hands=2, model_complexity=1,
                                         min_detection_confidence=.65, min_tracking_confidence=.65) as detector:
                frame_id, failures = 0, 0
                while not self.stopping.is_set():
                    ok, bgr = cap.read()
                    sampled_at = time.monotonic()  # BEFORE inference.
                    if not ok:
                        failures += 1
                        self.publish(Sample(Observation(frame_id + 1, sampled_at, "camera", reason="摄像头取帧失败"), None, ()))
                        frame_id += 1
                        if failures >= 5:
                            raise RuntimeError("摄像头连续取帧失败，请检查设备")
                        self.stopping.wait(.03)
                        continue
                    failures = 0
                    # Legacy Hands assumes selfie coordinates. Mirror ONCE here;
                    # left/right labels and steering sign then share one convention.
                    rgb = cv2.cvtColor(cv2.flip(bgr, 1), cv2.COLOR_BGR2RGB)
                    rgb.flags.writeable = False
                    result = detector.process(rgb)
                    hands = []
                    for landmarks, labels in zip(result.multi_hand_landmarks or (), result.multi_handedness or ()):
                        label = labels.classification[0]
                        side = label.label
                        if self.swap:
                            side = "Left" if side == "Right" else "Right"
                        hands.append((side, float(label.score), tuple((p.x, p.y, p.z) for p in landmarks.landmark)))
                    frame_id += 1
                    h, w = rgb.shape[:2]
                    observation = extract_left(hands, w, h, frame_id, sampled_at)
                    self.publish(Sample(observation, rgb, tuple(hands)))
        except Exception as error:
            self.publish(Failure(str(error)))
        finally:
            if cap is not None:
                cap.release()
