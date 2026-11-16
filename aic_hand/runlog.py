"""Numeric run logs suitable for Git: no camera images, video or credentials."""
import csv
from datetime import datetime
import json
from pathlib import Path
import platform
import time
import uuid

from . import __version__
from .config import AXES, ROOT


class RunLog:
    def __init__(self, config, source, directory=None):
        self.run_id = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        self.directory = Path(directory) if directory else ROOT / "logs" / "runs" / self.run_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.started = time.monotonic()
        self.frames, self.logged_frames = 0, 0
        self.valid_counts = dict.fromkeys(AXES, 0)
        self.inference_total_ms = 0.0
        self.last_logged = -float("inf")
        self.closed = False
        self.events = (self.directory / "events.jsonl").open("w", encoding="utf-8")
        self.csv_file = (self.directory / "frames.csv").open("w", encoding="utf-8-sig", newline="")
        fields = ["elapsed_s", "frame_id", "source", "mode", "inference_ms", "sample_age_ms"]
        fields += [side + "_" + field for side in ("Left", "Right") for field in ("x", "y", "pinch")]
        fields += [axis + "_" + field for axis in AXES for field in ("valid", "raw", "target", "reason")]
        self.writer = csv.DictWriter(self.csv_file, fieldnames=fields)
        self.writer.writeheader()
        self.manifest = {"schema_version": 1, "app_version": __version__, "run_id": self.run_id,
                         "started_at": datetime.now().astimezone().isoformat(), "source": source,
                         "robot_connected": False, "python": platform.python_version(),
                         "platform": platform.system(), "config": config, "status": "running"}
        self._manifest()
        self.event("start", source=source, robot_connected=False)

    def _manifest(self):
        path = self.directory / "run.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def event(self, kind, **data):
        if self.closed:
            return
        record = {"event": kind, "elapsed_s": round(time.monotonic() - self.started, 4), **data}
        self.events.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        self.events.flush()

    def frame(self, observation, mapping, now, mode):
        self.frames += 1
        self.inference_total_ms += observation.inference_ms
        for axis in AXES:
            self.valid_counts[axis] += int(mapping.axes[axis].valid)
        if now - self.last_logged < .1:
            return
        self.last_logged = now
        row = {"elapsed_s": round(now - self.started, 4), "frame_id": observation.frame_id,
               "source": observation.source, "mode": mode, "inference_ms": round(observation.inference_ms, 3),
               "sample_age_ms": round((now - observation.sampled_at) * 1000, 3)}
        for side, feature in mapping.features.items():
            for key in ("x", "y", "pinch"):
                row[side + "_" + key] = round(getattr(feature, key), 5)
        for axis, state in mapping.axes.items():
            row[axis + "_valid"] = int(state.valid)
            row[axis + "_reason"] = state.reason
            for key in ("raw", "target"):
                value = getattr(state, key)
                row[axis + "_" + key] = "" if value is None else round(value, 3)
        self.writer.writerow(row)
        self.csv_file.flush()
        self.logged_frames += 1

    def close(self, reason="用户停止"):
        if self.closed:
            return
        self.event("stop", reason=reason)
        self.manifest.update(status="stopped", stop_reason=reason, frames=self.frames,
                             logged_frames=self.logged_frames, valid_frames=self.valid_counts,
                             elapsed_s=round(time.monotonic() - self.started, 4),
                             mean_inference_ms=round(self.inference_total_ms / self.frames, 3) if self.frames else None)
        self._manifest()
        self.events.close()
        self.csv_file.close()
        self.closed = True
