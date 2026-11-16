"""Hand geometry and preview mapping, intentionally independent of camera/UI."""
from dataclasses import dataclass, field
import math

from .config import AXES, finite, copy_config, validate


@dataclass(frozen=True)
class Point:
    x: float
    y: float
    z: float = 0.0


@dataclass(frozen=True)
class Hand:
    side: str
    score: float
    points: tuple


@dataclass(frozen=True)
class Observation:
    frame_id: int
    sampled_at: float
    width: int
    height: int
    hands: tuple
    source: str = "camera"
    inference_ms: float = 0.0


@dataclass(frozen=True)
class Feature:
    x: float
    y: float
    pinch: float
    score: float


@dataclass(frozen=True)
class Axis:
    valid: bool = False
    raw: float | None = None
    target: float | None = None
    reason: str = "等待手部识别"


@dataclass
class Mapping:
    frame_id: int
    sampled_at: float
    source: str
    axes: dict
    features: dict = field(default_factory=dict)


def normalized(value, start, end):
    return max(0.0, min(1.0, (value - start) / (end - start)))


def hand_feature(hand, width, height):
    if hand.side not in ("Left", "Right") or not finite(hand.score) or not 0 <= hand.score <= 1:
        raise ValueError("左右手信息无效")
    if len(hand.points) != 21:
        raise ValueError("手部关键点不完整")
    if not all(finite(p.x) and finite(p.y) and finite(p.z) for p in hand.points):
        raise ValueError("关键点含无效数值")
    if not all(0 <= hand.points[i].x <= 1 and 0 <= hand.points[i].y <= 1 for i in (0, 4, 5, 8, 9, 13, 17)):
        raise ValueError("手部超出画面")
    def distance(a, b):
        p, q = hand.points[a], hand.points[b]
        return math.hypot((p.x - q.x) * width, (p.y - q.y) * height)
    palm_width = distance(5, 17)
    if palm_width < max(12.0, width * .015):
        raise ValueError("手部太小或掌宽不可用，请靠近摄像头")
    palm = [hand.points[i] for i in (0, 5, 9, 13, 17)]
    return Feature(sum(p.x for p in palm) / 5, sum(p.y for p in palm) / 5,
                   distance(4, 8) / palm_width, hand.score)


class Mapper:
    def __init__(self, config):
        self.config = copy_config(validate(config))
        self.last_frame = -1
        self.last_sampled_at = None
        self.filters = {}

    def invalid(self, reason, observation=None):
        self.filters.clear()
        return Mapping(observation.frame_id if observation else -1,
                       observation.sampled_at if observation else 0,
                       observation.source if observation else "none",
                       {axis: Axis(reason=reason) for axis in AXES})

    def _smooth(self, axis, raw, sampled_at):
        previous = self.filters.get(axis)
        value = raw
        if previous:
            prior, timestamp = previous
            dt = sampled_at - timestamp
            if dt <= 0:
                raise ValueError("采样时间未增加")
            tau = self.config["smoothing_seconds"]
            alpha = 1.0 if tau == 0 else 1 - math.exp(-dt / tau)
            value = prior + alpha * (raw - prior)
            if abs(raw - prior) < self.config["deadband_degrees"]:
                # Snap to endpoints so a small deadband cannot hide an endpoint.
                value = raw if raw in (0.0, 90.0, 180.0) else prior
        self.filters[axis] = (value, sampled_at)
        return Axis(True, raw, value, "有效")

    def process(self, observation, now):
        if (not finite(observation.sampled_at) or not finite(now) or
                now < observation.sampled_at or now - observation.sampled_at > self.config["max_age_seconds"]):
            return self.invalid("采样过期或时间无效", observation)
        if type(observation.frame_id) is not int or observation.frame_id <= self.last_frame:
            return self.invalid("重复或倒退的采样帧", observation)
        if self.last_sampled_at is not None and observation.sampled_at <= self.last_sampled_at:
            return self.invalid("采样时间未增加", observation)
        self.last_frame = observation.frame_id
        self.last_sampled_at = observation.sampled_at
        if (type(observation.width) is not int or type(observation.height) is not int or
                observation.width <= 0 or observation.height <= 0):
            return self.invalid("画面尺寸无效", observation)
        result = self.invalid("未检测到所需手部", observation) if not observation.hands else Mapping(
            observation.frame_id, observation.sampled_at, observation.source,
            {axis: Axis(reason="未选择该关节") for axis in AXES})
        for side, prefix in (("Left", "left"), ("Right", "right")):
            candidates = [hand for hand in observation.hands if hand.side == side]
            axes = [axis for axis in AXES if axis.startswith(prefix)]
            reason = "未检测到%s手" % ("左" if side == "Left" else "右")
            feature = None
            if len(candidates) > 1:
                reason = "左右手身份不确定，请分开双手"
            elif len(candidates) == 1:
                try:
                    if not finite(candidates[0].score) or candidates[0].score < self.config["min_handedness_score"]:
                        raise ValueError("左右手分类置信度不足")
                    feature = hand_feature(candidates[0], observation.width, observation.height)
                    result.features[side] = feature
                except ValueError as error:
                    reason = str(error)
            cal = self.config["calibration"][side]
            for axis in axes:
                selected = self.config["mode"] == "six" or axis == self.config["single_axis"]
                if not selected:
                    self.filters.pop(axis, None)
                    continue
                if feature is None:
                    result.axes[axis] = Axis(reason=reason)
                    self.filters.pop(axis, None)
                    continue
                if self.config["mode"] == "single":
                    u = 1 - normalized(feature.y, cal["y_top"], cal["y_bottom"])
                elif axis.endswith("elbow"):
                    u = normalized(feature.pinch, cal["pinch_closed"], cal["pinch_open"])
                elif axis.endswith("upper"):
                    u = normalized(feature.x, cal["x_out"], cal["x_in"])
                else:
                    u = 1 - normalized(feature.y, cal["y_top"], cal["y_bottom"])
                maximum = 180.0 if axis.endswith("elbow") else 90.0
                try:
                    result.axes[axis] = self._smooth(axis, u * maximum, observation.sampled_at)
                except ValueError as error:
                    result.axes[axis] = Axis(reason=str(error))
                    self.filters.pop(axis, None)
        return result
