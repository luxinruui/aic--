import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from install_backpack import install, verify


class ImportTests(unittest.TestCase):
    def source(self, directory):
        source = Path(directory)/"incoming"
        source.mkdir()
        data = b"test-only-code\n"
        (source/"program.py").write_bytes(data)
        manifest = {"files":[dict(path="program.py",bytes=len(data),sha256=hashlib.sha256(data).hexdigest())]}
        (source/"package_manifest.json").write_text(json.dumps(manifest))
        return source

    def test_import_refuses_existing_destination_and_leaves_old_project_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.source(directory)
            old = Path(directory)/"aic_left_hand_chassis"
            old.mkdir(); (old/"sentinel").write_text("original control version")
            with self.assertRaises(ValueError):install(source,old)
            self.assertEqual((old/"sentinel").read_text(),"original control version")
            target = Path(directory)/"aic_dual_hand_robot"
            install(source,target)
            self.assertEqual(verify(source),verify(target))
            self.assertEqual(list(old.iterdir()),[old/"sentinel"])

    def test_corrupted_transfer_rejected_before_creating_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.source(directory)
            (source/"program.py").write_text("corrupted")
            target = Path(directory)/"new"
            with self.assertRaises(ValueError):install(source,target)
            self.assertFalse(target.exists())

    def test_manifest_cannot_escape_the_new_project(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.source(directory)
            for name in ("../old/program.py","/tmp/program.py","old\\program.py"):
                (source/"package_manifest.json").write_text(json.dumps({"files":[dict(path=name,bytes=0,sha256="")] }))
                with self.assertRaises(ValueError):verify(source)


if __name__ == "__main__":unittest.main()
