"""Right-hand symmetric upper-body SOFTWARE targets. No robot I/O."""
from dataclasses import asdict, dataclass
import math
import uuid

from .core import CalibrationError, PoseReference, clamp

FINGER_NAMES = ("拇指", "食指", "中指", "无名指", "小指")
# Four fingers form: left elbow, left lift | right lift, right elbow.
AXES = ("left_upper", "right_upper", "left_elbow", "left_lift", "right_lift", "right_elbow")
SERVO_IDS = (3, 33, 1, 4, 34, 31)
AXIS_NAMES = ("左大臂合拢", "右大臂合拢", "左肘弯曲", "左肩抬臂", "右肩抬臂", "右肘弯曲")
GROUPS = ((0, 1), (2,), (3,), (4,), (5,))
GROUP_NAMES = ("双大臂合拢 / 展开", "左肘", "左肩", "右肩", "右肘")
DEFAULT_RANGES = ((0., 60.), (0., 60.), (0., 120.), (0., 90.), (0., 90.), (0., 120.))


@dataclass(frozen=True)
class Settings:
    ranges: tuple = DEFAULT_RANGES
    active: tuple = (True,) * 5
    reverse: tuple = (False,) * 5
    speed: float = 30.

    def __post_init__(self):
        if (len(self.ranges) != 6 or len(self.active) != 5 or len(self.reverse) != 5 or
                any(type(x) is not bool for x in (*self.active, *self.reverse)) or not any(self.active)):
            raise ValueError("请至少启用一组关节；双大臂必须作为一组")
        if type(self.speed) not in (int, float) or not math.isfinite(self.speed) or not 1 <= self.speed <= 90:
            raise ValueError("预览目标速度须为1～90°/秒")
        for i, pair in enumerate(self.ranges):
            maximum = 180 if "elbow" in AXES[i] else 90
            if (len(pair) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in pair)
                    or not 0 <= pair[0] < pair[1] <= maximum):
                raise ValueError("%s范围须在0～%d°内，且下限小于上限" % (AXIS_NAMES[i], maximum))


@dataclass(frozen=True)
class ArmCalibration:
    neutral: PoseReference
    fist: PoseReference
    opened: PoseReference

    def validate(self, active=(True,) * 5):
        poses = (self.neutral, self.fist, self.opened)
        if len({p.source for p in poses}) != 1 or poses[0].source not in ("camera", "demo"):
            raise CalibrationError("右手三个参照须来自同一种输入")
        if any(len(p.fingers) != 5 or any(not math.isfinite(v) for v in p.fingers) for p in poses):
            raise CalibrationError("右手参照不完整或包含无效数值")
        for i, enabled in enumerate(active):
            if not enabled:
                continue
            span = self.opened.fingers[i] - self.fist.fingers[i]
            if span < (.25 if i == 0 else .16):
                raise CalibrationError("%s收拢/弯曲与展开差异不足，请重采该手指端点" % FINGER_NAMES[i])
            u = (self.neutral.fingers[i] - self.fist.fingers[i]) / span
            if not .10 <= u <= .90:
                raise CalibrationError("%s起始位过于接近端点，请用自然半握重采起始位" % FINGER_NAMES[i])

    def normalized(self, fingers):
        return tuple(clamp((v-a)/(b-a), 0, 1) if b-a > 1e-8 else .5
                     for v, a, b in zip(fingers, self.fist.fingers, self.opened.fingers))


@dataclass(frozen=True)
class ArmCommand:
    session: str
    seq: int
    produced_at: float
    source: str
    frame_id: int
    sample_age_ms: float
    enabled: bool
    input_valid: bool
    state: str
    reason: str
    fingers: tuple
    targets: dict
    preview_targets: dict
    active_groups: tuple
    schema: str = "right-hand-upper-preview/1"
    preview_only: bool = True
    hardware_connected: bool = False

    def as_dict(self):
        return asdict(self)


class ArmController:
    max_age = .30

    def __init__(self):
        self.settings = Settings()
        self.session, self.seq = uuid.uuid4().hex, 0
        self.calibration = self.last = None
        self.last_frame, self.last_sampled_at = -1, None
        self.state, self.reason = "OFF", "请采集右手起始位、握拳和展开"
        self.inputs = self.anchor = None
        self.neutral_since = None
        self.reference_inputs = None
        self.any_pose_start = False

    def stop(self, reason="上肢已暂停，请重新启用"):
        self.state, self.reason = "OFF", reason
        self.inputs = self.anchor = self.neutral_since = None

    def invalidate(self, reason="输入改变，请重新校准右手"):
        self.stop(reason)
        self.calibration = self.last = None
        self.last_frame, self.last_sampled_at = -1, None
        self.reference_inputs = None

    def set_settings(self, settings):
        self.stop("预览设置已改变，请确认校准并重新启用")
        self.settings = settings
        self.reference_inputs = None
        if self.calibration:
            try:
                self.calibration.validate(settings.active)
            except CalibrationError:
                self.calibration = None

    def set_calibration(self, calibration):
        calibration.validate(self.settings.active)
        self.stop("右手校准完成，点击启用并回到起始位")
        self.calibration = calibration

    def enable(self, any_pose=False):
        if self.calibration is None:
            self.stop("请先完成右手三步校准")
            return False
        self.stop()
        self.any_pose_start = bool(any_pose)
        self.state = "ARMING"
        self.reason = ("等待新鲜右手采样，从当前手势平滑接管，无须回起始位" if self.any_pose_start else
                       "匹配背包软件参考姿态并保持0.5秒；首次通常需五指展开" if self.reference_inputs is not None else
                       "右手回到起始位并稳定保持0.5秒")
        return True

    def set_reference(self, reference):
        if reference is None:
            self.reference_inputs = None
            return
        values = [.5]*5
        for i, group in enumerate(GROUPS):
            if not self.settings.active[i]:
                continue
            candidates = []
            for j in group:
                angle = reference[AXES[j]]
                low, high = self.settings.ranges[j]
                if not math.isfinite(angle) or not low-1e-6 <= angle <= high+1e-6:
                    raise ValueError("背包参考姿态不在%s的软件范围内，请暂停后调整范围" % AXIS_NAMES[j])
                u = (angle-low)/(high-low)
                if self.settings.reverse[i]: u = 1-u
                if i not in (2,3): u = 1-u
                candidates.append(clamp(u,0,1))
            if max(candidates)-min(candidates) > 1e-5:
                raise ValueError("双大臂参考姿态不能由同一拇指进度表示")
            values[i] = candidates[0]
        self.reference_inputs = tuple(values)

    def update(self, observation, now):
        o = observation
        if (type(o.frame_id) is not int or o.frame_id < 0 or not math.isfinite(o.sampled_at)
                or o.source not in ("camera", "demo") or (o.valid and
                (len(o.fingers) != 5 or not all(math.isfinite(v) for v in o.fingers)))):
            self.stop("右手采样结构或数值无效")
            self.last = None
            return self.command(now)
        if (o.frame_id <= self.last_frame or
                (self.last_sampled_at is not None and o.sampled_at <= self.last_sampled_at)):
            self.stop("右手重复或乱序采样，请重新启用")
            return self.command(now)
        dt = .05 if self.last_sampled_at is None else o.sampled_at-self.last_sampled_at
        self.last_frame, self.last_sampled_at, self.last = o.frame_id, o.sampled_at, o
        if not o.valid or not 0 <= now-o.sampled_at <= self.max_age:
            self.stop(o.reason if not o.valid else "右手采样超过300毫秒")
            return self.command(now)
        if not self.calibration:
            return self.command(now)
        if o.source != self.calibration.neutral.source:
            self.invalidate("右手输入来源改变，请重新校准")
            return self.command(now)
        values = self.calibration.normalized(o.fingers)
        if self.state == "ARMING":
            if self.any_pose_start:
                # Start at the last commanded robot pose, then ramp toward the
                # hand. Human fingers never have to reproduce that robot pose.
                self.inputs = tuple(self.reference_inputs if self.reference_inputs is not None else values)
                self.anchor = tuple(values)
                self.state, self.reason = "ON", "已从当前右手姿势启用，正在平滑跟随"
                return self.command(now)
            neutral = self.reference_inputs if self.reference_inputs is not None else self.calibration.normalized(self.calibration.neutral.fingers)
            differences = [abs(a-b) if active else 0 for a,b,active in zip(values,neutral,self.settings.active)]
            if max(differences) <= (.025 if self.reference_inputs is not None else .22):
                if self.neutral_since is None:
                    self.neutral_since = o.sampled_at
                self.reason = "右手参考姿态已匹配，请稳定保持0.5秒"
                if o.sampled_at-self.neutral_since >= .5:
                    self.state, self.reason = "ON", "上肢软件预览已启用；拇指联动双大臂"
                    self.inputs = self.anchor = neutral
            else:
                self.neutral_since = None
                self.reason = "请调整%s，匹配%s" % (FINGER_NAMES[differences.index(max(differences))], "背包参考姿态（首次通常五指展开）" if self.reference_inputs is not None else "①采集的右手起始位")
        elif self.state == "ON":
            updated, anchors = [], list(self.anchor)
            for i, (old, value, group) in enumerate(zip(self.inputs, values, GROUPS)):
                if abs(value-anchors[i]) > .025:
                    anchors[i] = value
                filtered_step = (anchors[i]-old)*(1-math.exp(-min(dt, .30)/.10))
                # One common progress for IDs 3/33, limited by the slower side.
                span = max(self.settings.ranges[j][1]-self.settings.ranges[j][0] for j in group)
                limit = self.settings.speed*min(dt, .05)/span
                updated.append(old+clamp(filtered_step, -limit, limit))
            self.inputs, self.anchor = tuple(updated), tuple(anchors)
        return self.command(now)

    def tick(self, now, focused=True):
        if self.state != "OFF":
            if not focused:
                self.stop("窗口失去焦点，已暂停上肢")
            elif self.last is None or not 0 <= now-self.last.sampled_at <= self.max_age:
                self.stop("没有新鲜右手采样，已暂停上肢")
        return self.command(now)

    def map_targets(self, values):
        result = dict.fromkeys(AXES)
        if values is None:
            return result
        for i, group in enumerate(GROUPS):
            if not self.settings.active[i]:
                continue
            # u describes extension; elbow/upper semantic angles describe flexion.
            u = values[i] if i in (2, 3) else 1-values[i]
            if self.settings.reverse[i]:
                u = 1-u
            for j in group:
                low, high = self.settings.ranges[j]
                result[AXES[j]] = low+u*(high-low)
        return result

    def command(self, now):
        self.seq += 1
        o = self.last
        age = now-o.sampled_at if o else 0
        valid = bool(o and o.valid and 0 <= age <= self.max_age and self.calibration and
                     o.source == self.calibration.neutral.source)
        values = self.calibration.normalized(o.fingers) if valid else None
        enabled = self.state == "ON" and valid
        return ArmCommand(self.session, self.seq, now, o.source if o else "none", o.frame_id if o else 0,
                          max(0., age*1000), enabled, valid, self.state, self.reason,
                          values or (), self.map_targets(self.inputs if enabled else None),
                          self.map_targets(values), self.settings.active)


def demo_right(frame_id, now, elapsed):
    """Synthetic five-finger isolation, including the shared thumb group."""
    from .core import Observation
    closed, opened = (.55, .25, .25, .25, .25), (1.55, .96, .96, .96, .96)
    phase = elapsed % 18
    values = [.5] * 5
    if phase >= 2:
        index = min(4, int((phase-2)/3.2))
        values[index] += .48*math.sin((phase-2-index*3.2)*math.pi/1.6)
    fingers = tuple(a+u*(b-a) for a,b,u in zip(closed,opened,values))
    return Observation(frame_id, now, "demo", fingers, 0., True, "合成右手演示")


def demo_calibration():
    return ArmCalibration(*(PoseReference(values, 0., "demo") for values in
        ((1.05, .605, .605, .605, .605), (.55, .25, .25, .25, .25), (1.55, .96, .96, .96, .96))))
