import hashlib
import json
from pathlib import Path

DEFAULT = dict(serial_port="/dev/ttyS0", baudrate=115200, max_translation=20,
               max_rotation=60, translation_acceleration=40, rotation_acceleration=120, duration_ms=150)


def validate(config):
    if not isinstance(config, dict) or set(config) != set(DEFAULT):
        raise ValueError("robot_config.json fields do not match this package")
    p = config["serial_port"]
    if not isinstance(p, str) or not p.startswith("/dev/") or any(c in p for c in "\r\n\x00"):
        raise ValueError("Expected the robot Linux /dev/ UART path")
    if type(config["baudrate"]) is not int or config["baudrate"] != 115200:
        raise ValueError("Robot UART must remain 115200 / 8E1")
    for key in ("max_translation", "max_rotation"):
        if type(config[key]) is not int or not 1 <= config[key] <= 100:
            raise ValueError("Invalid vendor speed cap: " + key)
    for key in ("translation_acceleration", "rotation_acceleration"):
        if type(config[key]) is not int or not 1 <= config[key] <= 200:
            raise ValueError("Invalid acceleration: " + key)
    if type(config["duration_ms"]) is not int or not 50 <= config["duration_ms"] <= 150:
        raise ValueError("Chassis command duration must be 50..150ms")
    return dict(config)


def load(path):
    return validate(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def signature(config):
    encoded = json.dumps(validate(config), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
