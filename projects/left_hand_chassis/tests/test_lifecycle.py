import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from left_hand_drive.capture import Sample, Worker, demo_observation
from left_hand_drive.core import Controller
from left_hand_drive.runlog import RunLog
from scripts import bootstrap


class LifecycleTests(unittest.TestCase):
    def test_capture_queue_overwrites_old_frames(self):
        worker=Worker()
        a=Sample(demo_observation(1,1),None,())
        b=Sample(demo_observation(2,2),None,())
        worker.publish(a)
        worker.publish(b)
        self.assertIs(worker.latest(),b)
        self.assertIsNone(worker.latest())

    def test_logs_hold_numbers_and_events_with_no_image_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            log=RunLog(temporary,"demo")
            c=Controller()
            c.update(demo_observation(1,100),100)
            log.record(c.command(100),c.last)
            log.close("test end")
            self.assertEqual({p.name for p in log.path.iterdir()},{"events.jsonl","commands.csv","run.json"})
            info=json.loads((log.path/"run.json").read_text(encoding="utf-8"))
            self.assertFalse(info["hardware_connected"])
            with (log.path/"commands.csv").open(encoding="utf-8") as source:
                self.assertEqual(len(list(csv.DictReader(source))),1)

    def test_ready_explicit_environment_is_read_only_reused(self):
        with tempfile.TemporaryDirectory() as temporary,patch("sys.stdout",new_callable=io.StringIO):
            root=Path(temporary)
            (root/"requirements.txt").write_text("Pillow==11.3.0\n")
            log=bootstrap.StartupLog(root)
            try:
                with patch.dict("os.environ",{"LEFT_HAND_PYTHON":"existing-python"}), \
                     patch.object(bootstrap,"find_runtime",return_value={"version":[3,10],"bits":64}), \
                     patch.object(bootstrap,"probe_runtime",return_value={"version":[3,10],"bits":64}), \
                     patch.object(bootstrap,"dependencies_ready",return_value=True), \
                     patch.object(log,"run") as run:
                    self.assertEqual(bootstrap.ensure_environment(root,log),Path("existing-python"))
                    run.assert_not_called()
                    self.assertFalse((root/".venv").exists())
            finally:
                log.close()

    def test_bootstrap_opens_only_new_module_and_propagates_failure(self):
        with tempfile.TemporaryDirectory() as temporary,patch("sys.stdout",new_callable=io.StringIO), \
             patch.object(bootstrap,"ensure_environment",return_value=Path("python")), \
             patch.object(bootstrap.StartupLog,"run",return_value=7) as run:
            self.assertEqual(bootstrap.main(["--demo"],root=temporary),7)
            command=run.call_args.args[0]
            self.assertIn("left_hand_drive",command)
            self.assertNotIn("aic_hand",command)


if __name__=="__main__":
    unittest.main()
