"""Pure geometry, calibration and intent. No camera, network or serial imports."""
from dataclasses import dataclass, asdict
import math
import statistics
import uuid


FINGERS = ("thumb", "index", "middle", "ring", "pinky")


def wrap(angle):
    return (angle + 180) % 360 - 180


def clamp(value, low=-1.0, high=1.0):
    return max(low, min(high, value))


@dataclass(frozen=True)
class Observation:
    frame_id: int
    sampled_at: float
    source: str
    fingers: tuple = ()
    roll_deg: float = 0.0
    valid: bool = False
    reason: str = "未识别到左手"


def extract_left(hands, width, height, frame_id, sampled_at, source="camera"):
    """hands: (anatomical label, confidence, [(x,y,z), ...]).

    Coordinates are ALWAYS selfie-mirrored before detection, independent of UI.
    Pixel geometry avoids aspect-ratio distortion. Finger path ratios and the
    thumb-tip/pinky-root distance are calibrated per user/session.
    """
    def bad(reason):
        return Observation(frame_id, sampled_at, source, reason=reason)
    left = [h for h in hands if h[0] == "Left"]
    if len(left) != 1:
        return bad("请只让一只左手进入控制区域" if left else "未识别到左手；右手不控制")
    _, score, points = left[0]
    if not math.isfinite(score) or score < .80:
        return bad("左右手分类置信度不足")
    if len(points) != 21 or width <= 0 or height <= 0:
        return bad("手部关键点不完整")
    if any(len(p) != 3 or not all(math.isfinite(v) for v in p) for p in points):
        return bad("手部关键点包含无效数值")
    if any(not .015 < p[0] < .985 or not .015 < p[1] < .985 for p in points):
        return bad("请让整只左手离开画面边缘")
    p = [(v[0] * width, v[1] * height) for v in points]
    def dist(a, b):
        return math.hypot(p[a][0] - p[b][0], p[a][1] - p[b][1])
    palm_width, palm_length = dist(5, 17), dist(0, 9)
    if min(palm_width, palm_length) < 28 or palm_width / palm_length < .30:
        return bad("手掌过小或侧对镜头，请掌面朝向摄像头")
    # Four MCPs give a palm axis that does not depend on fingertip curling.
    cx = sum(p[i][0] for i in (5, 9, 13, 17)) / 4
    cy = sum(p[i][1] for i in (5, 9, 13, 17)) / 4
    roll = math.degrees(math.atan2(p[0][0] - cx, p[0][1] - cy))
    features = [dist(4, 17) / palm_width]
    for mcp in (5, 9, 13, 17):
        path = sum(dist(i, i + 1) for i in range(mcp, mcp + 3))
        if path < 20:
            return bad("手指关键点投影不足")
        features.append(dist(mcp, mcp + 3) / path)
    return Observation(frame_id, sampled_at, source, tuple(features), roll, True, "有效左手")


@dataclass(frozen=True)
class PoseReference:
    fingers: tuple
    roll_deg: float
    source: str


class CalibrationError(ValueError):
    pass


def reference(samples):
    """Require a continuous, fresh, stable one-second capture window."""
    if len(samples) < 12 or samples[-1].sampled_at - samples[0].sampled_at < .75:
        raise CalibrationError("有效采样不足，请稳定保持手势再采集")
    if any(not x.valid for x in samples) or len({x.source for x in samples}) != 1:
        raise CalibrationError("采集期间左手丢失或数据来源改变")
    if any(not 0 < b.sampled_at - a.sampled_at <= .20 for a, b in zip(samples, samples[1:])):
        raise CalibrationError("采样不连续或重复，请重新采集")
    angles = [wrap(x.roll_deg - samples[0].roll_deg) for x in samples]
    if max(angles) - min(angles) > 9:
        raise CalibrationError("手掌转动过大，请保持倾角稳定")
    means = tuple(statistics.median(x.fingers[i] for x in samples) for i in range(5))
    if any(max(x.fingers[i] for x in samples) - min(x.fingers[i] for x in samples) > limit
           for i, limit in enumerate((.22, .13, .13, .13, .13))):
        raise CalibrationError("手指姿态变化过大，请保持手势")
    return PoseReference(means, wrap(samples[0].roll_deg + statistics.median(angles)), samples[0].source)


@dataclass(frozen=True)
class Calibration:
    neutral: PoseReference
    fist: PoseReference
    opened: PoseReference

    def __post_init__(self):
        if len({p.source for p in (self.neutral, self.fist, self.opened)}) != 1:
            raise CalibrationError("三个参照必须来自同一种输入")
        for i, name in enumerate(FINGERS):
            values = [p.fingers[i] for p in (self.neutral, self.fist, self.opened)]
            if not all(math.isfinite(v) for v in values):
                raise CalibrationError("参照含无效数值")
            if values[2] - values[1] < (.25 if i == 0 else .16):
                raise CalibrationError("%s 的握拳与展开差异不足，请重新采集并完整伸展五指" % name)
        if any(abs(wrap(p.roll_deg - self.neutral.roll_deg)) > 25 for p in (self.fist, self.opened)):
            raise CalibrationError("采集握拳和展开时请保持与静止位接近的掌面倾角")
        u = self.normalized(self.neutral.fingers)
        if max(u) <= .40 or min(u) >= .60:
            raise CalibrationError("静止手势与握拳/展开重合，请用自然半握作为静止位，重新校准")

    def normalized(self, fingers):
        return tuple((x - a) / (b - a) for x, a, b in zip(fingers, self.fist.fingers, self.opened.fingers))

    def gesture(self, fingers):
        u = self.normalized(fingers)
        # Every digit, including the thumb, must match the corresponding end.
        if max(u) <= .25:
            return "FIST"
        if min(u) >= .75:
            return "OPEN"
        return "NEUTRAL"

    def at_neutral(self, observation):
        u = self.normalized(observation.fingers)
        n = self.normalized(self.neutral.fingers)
        return max(abs(a - b) for a, b in zip(u, n)) <= .20 and abs(wrap(observation.roll_deg - self.neutral.roll_deg)) <= 8


@dataclass(frozen=True)
class DriveCommand:
    schema: str
    session: str
    seq: int
    produced_at: float
    valid_for_ms: int
    enabled: bool
    input_valid: bool
    source: str
    frame_id: int
    sample_age_ms: float
    forward: float
    yaw_left: float
    gesture: str
    roll_delta_deg: float
    reason: str

    def as_dict(self):
        return asdict(self)


class Controller:
    """Call update for NEW observations, tick for an independent watchdog.

    The returned command is normalized intent; no physical units are implied.
    Missing/old input latches OFF. Recovery requires manual enable and neutral.
    """
    max_age = .30

    def __init__(self):
        self.session = uuid.uuid4().hex
        self.seq = 0
        self.calibration = None
        self.state = "OFF"
        self.reason = "请先打开摄像头并完成三步校准"
        self.last = None
        self.last_seen_id = -1
        self.last_sampled_at = None
        self.forward_gain = self.turn_gain = .35
        self.reset_filters()

    def reset_filters(self):
        self.neutral_since = self.gesture_since = None
        self.candidate = "NEUTRAL"
        self.smoothed_roll = None
        self.filter_at = None
        self.forward = self.yaw = self.delta = 0.0
        self.gesture = "NEUTRAL"

    def stop(self, reason="已暂停，请重新启用"):
        self.state, self.reason = "OFF", reason
        self.reset_filters()

    def invalidate(self, reason="输入改变，请重新校准"):
        self.stop(reason)
        self.calibration = self.last = None
        self.last_seen_id, self.last_sampled_at = -1, None

    def set_calibration(self, calibration):
        self.stop("校准完成；点击启用并回到静止位")
        self.calibration = calibration

    def enable(self):
        if self.calibration is None:
            self.stop("请先完成本次校准")
            return False
        self.reset_filters()
        self.state, self.reason = "ARMING", "回到校准静止位并稳定保持0.4秒"
        return True

    def update(self, observation, now):
        if (type(observation.frame_id) is not int or observation.frame_id < 0 or
                not math.isfinite(observation.sampled_at) or observation.source not in ("camera", "demo") or
                (observation.valid and (len(observation.fingers) != 5 or
                 not all(math.isfinite(v) for v in observation.fingers) or not math.isfinite(observation.roll_deg)))):
            self.stop("采样结构或数值无效")
            self.last = None
            return self.command(now)
        # Repeated frames never refresh freshness or advance gesture dwell.
        if (observation.frame_id <= self.last_seen_id or
                (self.last_sampled_at is not None and observation.sampled_at <= self.last_sampled_at)):
            self.stop("重复或乱序采样，请重新启用")
            return self.command(now)
        self.last_seen_id = observation.frame_id
        self.last_sampled_at = observation.sampled_at
        self.last = observation
        age = now - observation.sampled_at
        if not observation.valid or not 0 <= age <= self.max_age:
            self.stop(observation.reason if not observation.valid else "采样超过300毫秒，请重新启用")
            return self.command(now)
        if self.calibration is None:
            return self.command(now)
        if observation.source != self.calibration.neutral.source:
            self.invalidate("输入来源改变，校准失效")
            return self.command(now)
        self.delta = wrap(observation.roll_deg - self.calibration.neutral.roll_deg)
        self.gesture = self.calibration.gesture(observation.fingers)
        if abs(self.delta) > 75:
            self.stop("掌面倾斜超过75度，请回正后重新启用")
            return self.command(now)
        if self.state == "ARMING":
            if self.calibration.at_neutral(observation):
                if self.neutral_since is None:
                    self.neutral_since = observation.sampled_at
                if observation.sampled_at - self.neutral_since >= .4:
                    self.state, self.reason = "ON", "已启用：握拳前进、展开后退、左右倾斜转向"
            else:
                self.neutral_since = None
            return self.command(now)
        if self.state != "ON":
            return self.command(now)
        if self.gesture != self.candidate:
            self.candidate, self.gesture_since = self.gesture, observation.sampled_at
            self.forward = 0.0
        if self.gesture_since is None:
            self.gesture_since = observation.sampled_at
        if observation.sampled_at - self.gesture_since >= .18:
            self.forward = {"FIST": self.forward_gain, "OPEN": -self.forward_gain}.get(self.gesture, 0.0)
        if self.gesture == "NEUTRAL":
            self.forward = 0.0
        dt = 0 if self.filter_at is None else observation.sampled_at - self.filter_at
        if self.smoothed_roll is None or not 0 < dt <= self.max_age:
            self.smoothed_roll = self.delta
        else:
            self.smoothed_roll += (1 - math.exp(-dt / .06)) * wrap(self.delta - self.smoothed_roll)
        self.filter_at = observation.sampled_at
        magnitude = max(0.0, abs(self.smoothed_roll) - 10) / 30
        self.yaw = math.copysign(clamp(magnitude, 0, 1), self.smoothed_roll) * self.turn_gain
        if abs(self.delta) <= 8:  # Immediate release at the calibrated center.
            self.yaw = 0.0
        return self.command(now)

    def tick(self, now, focused=True):
        if self.state != "OFF":
            if not focused:
                self.stop("窗口失去焦点，已暂停")
            elif self.last is None or not 0 <= now - self.last.sampled_at <= self.max_age:
                self.stop("没有新鲜左手采样，已暂停")
        return self.command(now)

    def command(self, now):
        self.seq += 1
        o = self.last
        age = (now - o.sampled_at) if o else 0.0
        valid = bool(o and o.valid and 0 <= age <= self.max_age and self.calibration and
                     o.source == self.calibration.neutral.source)
        enabled = self.state == "ON" and valid
        return DriveCommand("left-hand-chassis/1", self.session, self.seq, now, 150,
                            enabled, valid, o.source if o else "none", o.frame_id if o else 0,
                            max(0.0, age * 1000), self.forward if enabled else 0.0,
                            self.yaw if enabled else 0.0, self.gesture, self.delta, self.reason)
