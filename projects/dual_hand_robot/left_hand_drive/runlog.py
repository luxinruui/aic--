"""Versionable numeric/event logs. Never records camera pixels or video."""
import csv
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import platform

from . import __version__


class RunLog:
    def __init__(self, root, source):
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
        self.path = Path(root) / "logs" / "runs" / (source + "-" + stamp)
        self.path.mkdir(parents=True)
        self.events = (self.path / "events.jsonl").open("w", encoding="utf-8")
        self.csv_file = (self.path / "commands.csv").open("w", encoding="utf-8", newline="")
        self.csv = csv.writer(self.csv_file)
        self.csv.writerow(("seq", "source", "frame", "enabled", "input_valid", "age_ms", "forward", "yaw_left",
                           "gesture", "roll_delta_deg", "thumb", "index", "middle", "ring", "pinky", "reason"))
        self.info = {"version": __version__, "source": source, "started_at": datetime.now().astimezone().isoformat(),
                     "platform": platform.system(), "python": platform.python_version(),
                     "hardware_connected": False, "image_recording": False, "rows": 0}
        self.last_write = -1e10
        self.previous = None
        self.upper_file = None
        self.upper_last_write = -1e10
        self.upper_previous = None
        self.event("start", source=source)

    def event(self, kind, **fields):
        self.events.write(json.dumps({"at": datetime.now().astimezone().isoformat(), "event": kind, **fields},
                                     ensure_ascii=False, allow_nan=False) + "\n")
        self.events.flush()

    def record(self, command, observation=None):
        key = (command.enabled, command.gesture, command.reason)
        if key != self.previous:
            self.event("state", enabled=command.enabled, gesture=command.gesture, reason=command.reason)
            self.previous = key
        if command.produced_at - self.last_write < .099:
            return
        self.last_write = command.produced_at
        features = observation.fingers if observation and observation.valid else ("",) * 5
        self.csv.writerow((command.seq, command.source, command.frame_id, command.enabled, command.input_valid,
                           round(command.sample_age_ms, 2), round(command.forward, 4), round(command.yaw_left, 4),
                           command.gesture, round(command.roll_delta_deg, 2), *features, command.reason))
        self.csv_file.flush()
        self.info["rows"] += 1

    def calibration(self, calibration):
        (self.path / "calibration.json").write_text(json.dumps(asdict(calibration), ensure_ascii=False, indent=2), encoding="utf-8")
        self.event("calibrated", source=calibration.neutral.source)

    def enable_upper(self, settings):
        from .core import FINGERS
        from .upper import AXES
        self.info.update(upper_preview_only=True, upper_settings=settings, upper_rows=0)
        self.upper_file = (self.path / "upper_commands.csv").open("w", encoding="utf-8", newline="")
        self.upper_csv = csv.writer(self.upper_file)
        self.upper_csv.writerow(("seq", "source", "frame", "state", "enabled", "input_valid", "age_ms",
                                 *FINGERS, *AXES, "reason"))
        self.event("upper_preview_start", settings=settings)

    def upper_calibration(self, calibration):
        (self.path / "upper_calibration.json").write_text(
            json.dumps(asdict(calibration), ensure_ascii=False, indent=2), encoding="utf-8")
        self.event("upper_calibrated", source=calibration.neutral.source)

    def record_upper(self, command):
        if self.upper_file is None:
            return
        key = (command.state, command.reason)
        changed = key != self.upper_previous
        if changed:
            self.event("upper_state", state=command.state, enabled=command.enabled, reason=command.reason)
            self.upper_previous = key
        if not changed and command.produced_at-self.upper_last_write < .099:
            return
        self.upper_last_write = command.produced_at
        self.upper_csv.writerow((command.seq, command.source, command.frame_id, command.state, command.enabled,
                                 command.input_valid, round(command.sample_age_ms, 2),
                                 *(command.fingers or ("",)*5), *command.targets.values(), command.reason))
        self.upper_file.flush()
        self.info["upper_rows"] += 1

    def close(self, reason):
        self.event("stop", reason=reason)
        self.info.update(ended_at=datetime.now().astimezone().isoformat(), ended_reason=reason)
        (self.path / "run.json").write_text(json.dumps(self.info, ensure_ascii=False, indent=2), encoding="utf-8")
        self.events.close()
        self.csv_file.close()
        if self.upper_file:
            self.upper_file.close()
