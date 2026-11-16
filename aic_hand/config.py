"""Validated, portable preview settings; no third-party dependencies."""
from copy import deepcopy
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "preview.json"
AXES = ("left_elbow", "right_elbow", "left_upper", "right_upper", "left_lift", "right_lift")
SERVO_IDS = dict(zip(AXES, (1, 31, 3, 33, 4, 34)))
AXIS_LABELS = dict(zip(AXES, ("左肘", "右肘", "左臂合拢", "右臂合拢", "左肩抬臂", "右肩抬臂")))


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate(config):
    if config.get("schema_version") != 1:
        raise ValueError("配置版本不支持。")
    for key, low, high in (("camera", 0, 30), ("width", 160, 3840), ("height", 120, 2160)):
        if type(config.get(key)) is not int or not low <= config[key] <= high:
            raise ValueError("配置 %s 超出允许范围。" % key)
    for key in ("mirror", "swap_hands"):
        if type(config.get(key)) is not bool:
            raise ValueError("配置 %s 必须是布尔值。" % key)
    if config.get("mode") not in ("single", "six") or config.get("single_axis") not in AXES:
        raise ValueError("控制模式或关节选择无效。")
    for key, low, high in (("min_handedness_score", 0, 1), ("max_age_seconds", .05, .5),
                           ("smoothing_seconds", 0, 1), ("deadband_degrees", 0, 5)):
        if not finite(config.get(key)) or not low <= config[key] <= high:
            raise ValueError("配置 %s 无效。" % key)
    for side in ("Left", "Right"):
        cal = config.get("calibration", {}).get(side, {})
        for key in ("y_top", "y_bottom", "x_out", "x_in", "pinch_closed", "pinch_open"):
            if not finite(cal.get(key)):
                raise ValueError("%s 的 %s 标定无效。" % (side, key))
        if not 0 <= cal["y_top"] < cal["y_bottom"] <= 1 or cal["y_bottom"] - cal["y_top"] < .05:
            raise ValueError("上下标定范围太小或方向相反。")
        if not all(0 <= cal[k] <= 1 for k in ("x_out", "x_in")) or abs(cal["x_in"] - cal["x_out"]) < .05:
            raise ValueError("内外侧标定范围太小。")
        if not 0 <= cal["pinch_closed"] < cal["pinch_open"] <= 5 or cal["pinch_open"] - cal["pinch_closed"] < .1:
            raise ValueError("手指开合标定范围太小或方向相反。")
    return config


def load(path=DEFAULT_CONFIG):
    with Path(path).open(encoding="utf-8") as stream:
        return validate(json.load(stream))


def save(config, path=DEFAULT_CONFIG):
    validate(config)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def copy_config(config):
    return deepcopy(config)
