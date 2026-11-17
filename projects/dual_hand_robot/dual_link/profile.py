"""Measured AIC calibration and explicit finger-flexion/legacy-angle conversion."""
import hashlib
import json
import math
from pathlib import Path

from robot_link.config import validate as validate_chassis

AXES = ("left_upper", "right_upper", "left_elbow", "left_lift", "right_lift", "right_elbow")
IDS = dict(zip(AXES, ("3", "33", "1", "4", "34", "31")))
GROUPS = (("left_upper", "right_upper"), ("left_elbow",), ("left_lift",), ("right_lift",), ("right_elbow",))
MAXIMUM = {name: 180. if "elbow" in name else 90. for name in AXES}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def interpolate(value, xs, ys):
    value = max(xs[0], min(xs[-1], value))
    for i in range(len(xs)-1):
        if value <= xs[i+1]:
            return ys[i]+(value-xs[i])*(ys[i+1]-ys[i])/(xs[i+1]-xs[i])
    return ys[-1]


class Profile:
    def __init__(self, arm, chassis):
        self.arm, self.chassis = json.loads(json.dumps(arm)), validate_chassis(chassis)
        if arm.get("calibrated") is not True or arm.get("waist_mode") != "hold":
            raise ValueError("Measured arm profile and waist hold are required")
        if arm.get("serial") != {"port": self.chassis["serial_port"], "baudrate": 115200}:
            raise ValueError("Arm/chassis must use the same 115200/8E1 UART")
        if set(arm.get("axes", {})) != set(AXES):
            raise ValueError("Expected all six measured axes")
        baseline = arm.get("baseline_positions", {})
        if set(baseline) != {"1", "2", "3", "4", "31", "32", "33", "34"}:
            raise ValueError("Incomplete measured baseline")
        if any(type(v) is not int or not 1 <= v <= 4095 for v in baseline.values()):
            raise ValueError("Invalid baseline position")
        if baseline["2"] != 2048 or baseline["32"] != 2048:
            raise ValueError("Keep the approved fixed IDs 2/32 at 2048")
        for name, axis in arm["axes"].items():
            xs, ys = axis.get("human_degrees", []), axis.get("positions", [])
            expected_points = 3 if "elbow" in name else 2
            if axis.get("servo_id") != int(IDS[name]) or len(xs) != expected_points or len(xs) != len(ys):
                raise ValueError("Invalid measured axis: "+name)
            if any(not finite(x) for x in xs) or any(type(y) is not int or not 1 <= y <= 4095 for y in ys):
                raise ValueError("Invalid axis numbers")
            if xs[0] != 0 or xs[-1] != MAXIMUM[name] or any(b <= a for a,b in zip(xs,xs[1:])):
                raise ValueError("Invalid angle curve")
            if not (all(b > a for a,b in zip(ys,ys[1:])) or all(b < a for a,b in zip(ys,ys[1:]))):
                raise ValueError("Servo curve must be monotonic")
            if axis.get("min_position") != min(ys) or axis.get("max_position") != max(ys):
                raise ValueError("Mechanical bounds do not match measured endpoints")
            if axis.get("initial_position") != baseline[IDS[name]] or baseline[IDS[name]] not in ys:
                raise ValueError("Inconsistent starting position")
        for key, low, high in (("control_hz", 20, 20), ("duration_ms", 50, 50),
                               ("max_position_rate", 1, 1200), ("max_acceleration", 1, 4800)):
            if not finite(arm.get(key)) or not low <= arm[key] <= high:
                raise ValueError("Unapproved arm motion parameter: "+key)
        self.period = 1./arm["control_hz"]
        self.positions = dict(baseline)
        self.signature = hashlib.sha256(json.dumps(dict(app="aic-dual-hand-robot/1", arm=arm,
            chassis=self.chassis), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def position(self, name, semantic_angle):
        axis = self.arm["axes"][name]
        # The old AIC elbow angle is the internal angle: 180 is STRAIGHT.
        legacy = 180-semantic_angle if "elbow" in name else semantic_angle
        return interpolate(legacy, axis["human_degrees"], axis["positions"])

    def angle(self, name, position):
        axis = self.arm["axes"][name]
        xs, ys = axis["positions"], axis["human_degrees"]
        if xs[0] > xs[-1]:
            xs, ys = xs[::-1], ys[::-1]
        angle = interpolate(position, xs, ys)
        return 180-angle if "elbow" in name else angle

    def group_scale(self, group):
        return max(abs(self.position(name,MAXIMUM[name])-self.position(name,0)) for name in group)

    def progress(self, name, semantic_angle):
        start, end = self.position(name,0), self.position(name,MAXIMUM[name])
        return (self.position(name,semantic_angle)-start)/(end-start)

    def progress_position(self, name, progress):
        start, end = self.position(name,0), self.position(name,MAXIMUM[name])
        return start+progress*(end-start)

    def progress_angle(self, name, progress):
        return self.angle(name,self.progress_position(name,progress))


def load(root):
    root = Path(root)
    return Profile(json.loads((root/"arm_config.json").read_text(encoding="utf-8-sig")),
                   json.loads((root/"robot_config.json").read_text(encoding="utf-8-sig")))
