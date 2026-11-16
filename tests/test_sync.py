from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import sync_github


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.root, self.remote = base / "clone", base / "remote.git"
        self.root.mkdir()
        self.run_git("init", "--bare", str(self.remote), cwd=base)
        self.run_git("init", "-b", "main")
        self.run_git("config", "user.name", "Preview Test")
        self.run_git("config", "user.email", "preview@example.invalid")
        self.run_git("remote", "add", "origin", str(self.remote))
        (self.root / "config").mkdir()
        (self.root / "config" / "preview.json").write_text("{}\n")
        self.run_git("add", "config/preview.json")
        self.run_git("commit", "-m", "Initial config")
        self.run_git("push", "-u", "origin", "main")

    def run_git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.root, check=True,
                              text=True, encoding="utf-8", capture_output=True).stdout.strip()

    def test_sync_pushes_logs_but_does_not_sweep_code_or_media(self):
        folder = self.root / "logs" / "runs" / "sample"
        folder.mkdir(parents=True)
        (folder / "events.jsonl").write_text('{"event":"start"}\n')
        (folder / "private.png").write_bytes(b"not a real image")
        startup = self.root / "logs" / "startup"
        startup.mkdir(parents=True)
        (startup / "latest.log").write_text("startup failure detail\n")
        (self.root / "unrelated.py").write_text("# unrelated\n")
        with patch.object(sync_github, "ROOT", self.root), patch.object(sync_github, "ALLOWED_ORIGINS", (str(self.remote),)):
            self.assertEqual(sync_github.sync(), 0)
        tree = self.run_git("ls-tree", "-r", "--name-only", "HEAD")
        self.assertIn("events.jsonl", tree)
        self.assertIn("logs/startup/latest.log", tree)
        self.assertNotIn("private.png", tree)
        self.assertNotIn("unrelated.py", tree)
        self.assertEqual(self.run_git("rev-parse", "HEAD"), self.run_git("rev-parse", "refs/heads/main", cwd=self.remote))

    def test_other_staged_changes_block_sync(self):
        (self.root / "unrelated.txt").write_text("keep staged\n")
        self.run_git("add", "unrelated.txt")
        with patch.object(sync_github, "ROOT", self.root), patch.object(sync_github, "ALLOWED_ORIGINS", (str(self.remote),)):
            with self.assertRaises(ValueError):
                sync_github.sync()
        self.assertEqual(self.run_git("diff", "--cached", "--name-only"), "unrelated.txt")

    def test_wrong_origin_blocks_before_staging(self):
        (self.root / "config" / "preview.json").write_text('{"changed":true}\n')
        with patch.object(sync_github, "ROOT", self.root):
            with self.assertRaises(ValueError):
                sync_github.sync()
        self.assertEqual(self.run_git("diff", "--cached", "--name-only"), "")


if __name__ == "__main__":
    unittest.main()
