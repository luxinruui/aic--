"""One worker owns the camera. A one-item queue always exposes the latest frame."""
from dataclasses import dataclass
import math
import os
import queue
import threading
import time

from .core import Hand, Observation, Point

CONNECTIONS = ((0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
               (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15),
               (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20))


@dataclass
class Sample:
    observation: Observation
    image: object


@dataclass
class Failure:
    message: str


def anatomical_side(label, swap=False):
    # Legacy MediaPipe Hands assumes selfie-mirrored input. Inference here uses
    # the unmirrored camera frame, so swap its label; mirror only for display.
    side = "Right" if label == "Left" else "Left" if label == "Right" else "Unknown"
    if swap and side != "Unknown":
        side = "Right" if side == "Left" else "Left"
    return side


def detector_hands(result, swap=False):
    output = []
    for landmarks, handedness in zip(result.multi_hand_landmarks or (), result.multi_handedness or ()):
        label = handedness.classification[0]
        output.append(Hand(anatomical_side(label.label, swap), float(label.score),
                           tuple(Point(float(p.x), float(p.y), float(p.z)) for p in landmarks.landmark)))
    return tuple(output)


def synthetic_hand(side, x=.7, y=.5, pinch=1.0):
    points = [Point(x, y + .1)]
    points.extend((Point(x - .07, y + .04), Point(x - .1, y),
                   Point(x - .12, y - .04), Point(x - .13, y - .08)))
    for index in range(4):
        base_x = x - .055 + index * (.11 / 3)
        points.extend((Point(base_x, y), Point(base_x, y - .06),
                       Point(base_x, y - .12), Point(base_x, y - .18)))
    # At 640x480, palm roots #5/#17 are .11 * width apart. Both tips use
    # the same y so pinch remains a known ratio even at other resolutions.
    points[8] = Point(points[4].x + .11 * pinch, points[4].y)
    return Hand(side, .99, tuple(points))


def demo_observation(index, sampled_at):
    phase = index / 30.0
    hands = []
    for side, center, sign in (("Left", .72, 1), ("Right", .28, -1)):
        # Deliberately omit a hand periodically to exercise invalidation.
        if side == "Left" and index % 180 in range(120, 135):
            continue
        hands.append(synthetic_hand(side, center + .08 * math.sin(phase) * sign,
                                    .48 + .15 * math.sin(phase * .7 + sign),
                                    .85 + .6 * math.sin(phase * 1.2 + sign)))
    return Observation(index + 1, sampled_at, 640, 480, tuple(hands), "demo", 0.0)


class Worker(threading.Thread):
    def __init__(self, config, demo=False):
        super().__init__(name="aic-hand-capture", daemon=True)
        self.config = dict(config)
        self.demo = demo
        self.stop_event = threading.Event()
        self.messages = queue.Queue(maxsize=1)

    def latest(self):
        try:
            return self.messages.get_nowait()
        except queue.Empty:
            return None

    def publish(self, item):
        try:
            self.messages.put_nowait(item)
        except queue.Full:
            try:
                self.messages.get_nowait()
            except queue.Empty:
                pass
            self.messages.put_nowait(item)

    def stop(self):
        self.stop_event.set()

    def run(self):
        try:
            if self.demo:
                from PIL import Image
                index = 0
                while not self.stop_event.is_set():
                    self.publish(Sample(demo_observation(index, time.monotonic()),
                                        Image.new("RGB", (640, 480), "#122034")))
                    index += 1
                    self.stop_event.wait(1 / 30)
            else:
                self._camera()
        except Exception as error:
            self.publish(Failure("摄像头/识别启动失败：%s" % error))

    def _camera(self):
        import cv2
        import mediapipe as mp
        if not hasattr(mp, "solutions"):
            raise RuntimeError("MediaPipe 版本不匹配，请运行 setup_preview.cmd 安装项目独立环境。")
        backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
        camera = cv2.VideoCapture(self.config["camera"], backend)
        try:
            if not camera.isOpened():
                raise RuntimeError("无法打开摄像头 %s，请关闭其他相机程序，或改选编号。" % self.config["camera"])
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.config["width"])
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config["height"])
            camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            with mp.solutions.hands.Hands(static_image_mode=False, max_num_hands=2, model_complexity=1,
                                         min_detection_confidence=.6, min_tracking_confidence=.6) as detector:
                frame_id, failures = 0, 0
                while not self.stop_event.is_set():
                    ok, bgr = camera.read()
                    sampled_at = time.monotonic()
                    if not ok:
                        failures += 1
                        if failures >= 5:
                            raise RuntimeError("摄像头连续读取失败，请检查设备连接和相机权限。")
                        self.stop_event.wait(.03)
                        continue
                    failures = 0
                    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                    rgb.flags.writeable = False
                    started = time.monotonic()
                    result = detector.process(rgb)
                    elapsed = (time.monotonic() - started) * 1000
                    frame_id += 1
                    height, width = rgb.shape[:2]
                    observation = Observation(frame_id, sampled_at, width, height,
                                              detector_hands(result, self.config["swap_hands"]), "camera", elapsed)
                    self.publish(Sample(observation, rgb))
        finally:
            camera.release()
