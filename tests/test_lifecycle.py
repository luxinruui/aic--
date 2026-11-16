import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from aic_hand.capture import Worker, demo_observation
from aic_hand.config import ROOT, load
from aic_hand.core import Mapper
from aic_hand.runlog import RunLog


class LifecycleTests(unittest.TestCase):
    def test_latest_queue_replaces_backlog(self):
        worker = Worker(load(), demo=True)
        for frame in range(100):
            worker.publish(frame)
        self.assertEqual(worker.messages.qsize(), 1)
        self.assertEqual(worker.latest(), 99)
        self.assertIsNone(worker.latest())

    def test_worker_stops_without_camera(self):
        worker = Worker(load(), demo=True)
        worker.start()
        try:
            item = worker.messages.get(timeout=5)
            self.assertEqual(item.observation.source, "demo")
        finally:
            worker.stop()
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())

    def test_log_invalid_targets_are_empty_and_summary_is_honest(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = load()
            config["mode"] = "six"
            log = RunLog(config, "demo", Path(temporary) / "run")
            mapper = Mapper(config)
            now = time.monotonic()
            observation = demo_observation(125, now)  # Left hand intentionally missing.
            mapping = mapper.process(observation, now)
            log.frame(observation, mapping, now, "six")
            log.close("测试结束")
            log.close("不应改变第一次停止原因")
            with (log.directory / "frames.csv").open(encoding="utf-8-sig", newline="") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row["left_elbow_valid"], "0")
            self.assertEqual(row["left_elbow_target"], "")
            self.assertEqual(row["right_elbow_valid"], "1")
            manifest = json.loads((log.directory / "run.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["robot_connected"])
            self.assertEqual(manifest["source"], "demo")
            self.assertEqual(manifest["stop_reason"], "测试结束")
            self.assertEqual(manifest["frames"], 1)

    def test_existing_log_directory_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(FileExistsError):
                RunLog(load(), "demo", temporary)

    def test_headless_entrypoint_records_complete_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "headless"
            result = subprocess.run([sys.executable, "-m", "aic_hand", "--headless", "--demo", "--mode", "six",
                                     "--frames", "12", "--log-dir", str(directory)], cwd=ROOT,
                                    text=True, encoding="utf-8", capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((directory / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["frames"], 12)
            self.assertTrue(all(count == 12 for count in manifest["valid_frames"].values()))
            events = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([event["event"] for event in events], ["start", "stop"])
            self.assertEqual({path.suffix for path in directory.iterdir()}, {".json", ".jsonl", ".csv"})


if __name__ == "__main__":
    unittest.main()
