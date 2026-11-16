import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import bootstrap


class BootstrapTests(unittest.TestCase):
    def test_runtime_requires_supported_64bit_python(self):
        self.assertTrue(bootstrap.supported({"version": [3, 10], "bits": 64}))
        self.assertTrue(bootstrap.supported({"version": [3, 12], "bits": 64}))
        for info in (None, {}, {"version": [3, 13], "bits": 64}, {"version": [3, 10], "bits": 32}):
            self.assertFalse(bootstrap.supported(info))

    def test_runtime_search_skips_unsupported_default_python(self):
        with patch.object(bootstrap, "candidates", return_value=[["new"], ["working"]]), \
                patch.object(bootstrap, "probe_runtime", side_effect=[{"version": [3, 14], "bits": 64},
                                                                    {"version": [3, 10], "bits": 64, "executable": "working"}]), \
                patch("sys.stdout", new_callable=io.StringIO), tempfile.TemporaryDirectory() as temporary:
            log = bootstrap.StartupLog(Path(temporary))
            try:
                self.assertEqual(bootstrap.find_runtime(log)["executable"], "working")
            finally:
                log.close()

    def test_incompatible_venv_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary, patch("sys.stdout", new_callable=io.StringIO):
            root = Path(temporary)
            python = bootstrap.python_in(root / ".venv")
            python.parent.mkdir(parents=True)
            python.write_bytes(b"existing interpreter")
            log = bootstrap.StartupLog(root)
            try:
                with patch.object(bootstrap, "probe_runtime", return_value={"version": [3, 14], "bits": 64}):
                    selected = bootstrap.choose_environment(root, {"version": [3, 10]}, log)
                self.assertEqual(selected.name, ".preview-venv-310")
                self.assertEqual(python.read_bytes(), b"existing interpreter")
            finally:
                log.close()

    def test_existing_ready_environment_is_not_reinstalled(self):
        with tempfile.TemporaryDirectory() as temporary, patch("sys.stdout", new_callable=io.StringIO):
            root = Path(temporary)
            python = bootstrap.python_in(root / ".venv")
            python.parent.mkdir(parents=True)
            python.touch()
            (root / "requirements.txt").write_text("Pillow==11.3.0\n")
            log = bootstrap.StartupLog(root)
            try:
                with patch.object(bootstrap, "find_runtime", return_value={"version": [3, 10], "bits": 64}), \
                        patch.object(bootstrap, "probe_runtime", return_value={"version": [3, 10], "bits": 64}), \
                        patch.object(bootstrap, "dependencies_ready", return_value=True), patch.object(log, "run") as run:
                    self.assertEqual(bootstrap.ensure_environment(root, log), python)
                    run.assert_not_called()
            finally:
                log.close()

    def test_child_stdout_and_stderr_persist_with_exit_code(self):
        with tempfile.TemporaryDirectory() as temporary, patch("sys.stdout", new_callable=io.StringIO):
            root = Path(temporary)
            log = bootstrap.StartupLog(root)
            try:
                code = log.run([sys.executable, "-c", "import sys;print('progress');print('fatal detail',file=sys.stderr);sys.exit(7)"], root)
            finally:
                log.close()
            text = log.path.read_text(encoding="utf-8")
            self.assertEqual(code, 7)
            self.assertIn("progress", text)
            self.assertIn("fatal detail", text)
            self.assertIn("Process exit code: 7", text)

    def test_previous_startup_log_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary, patch("sys.stdout", new_callable=io.StringIO):
            root = Path(temporary)
            first = bootstrap.StartupLog(root)
            first.write("previous failure detail")
            first.close()
            second = bootstrap.StartupLog(root)
            second.close()
            self.assertIn("previous failure detail", (root / "logs/startup/previous.log").read_text(encoding="utf-8"))

    def test_bootstrap_failure_is_visible_and_logged(self):
        with tempfile.TemporaryDirectory() as temporary, patch("sys.stdout", new_callable=io.StringIO) as output:
            with patch.object(bootstrap, "ensure_environment", side_effect=RuntimeError("pip install failed")):
                self.assertEqual(bootstrap.main([], root=temporary), 2)
            self.assertIn("pip install failed", output.getvalue())
            self.assertIn("STARTUP FAILED", (Path(temporary) / "logs/startup/latest.log").read_text(encoding="utf-8"))

    def test_setup_only_never_launches_gui(self):
        with tempfile.TemporaryDirectory() as temporary, patch("sys.stdout", new_callable=io.StringIO), \
                patch.object(bootstrap, "ensure_environment", return_value=Path(sys.executable)), \
                patch.object(bootstrap.StartupLog, "run") as run:
            self.assertEqual(bootstrap.main([], setup_only=True, root=temporary), 0)
            run.assert_not_called()

    def test_preview_failure_propagates_to_batch_launcher(self):
        with tempfile.TemporaryDirectory() as temporary, patch("sys.stdout", new_callable=io.StringIO), \
                patch.object(bootstrap, "ensure_environment", return_value=Path(sys.executable)), \
                patch.object(bootstrap.StartupLog, "run", return_value=2) as run:
            self.assertEqual(bootstrap.main(["--demo"], root=temporary), 2)
            self.assertEqual(run.call_args.args[0][-1], "--demo")
            self.assertIn("Preview failed", (Path(temporary) / "logs/startup/latest.log").read_text(encoding="utf-8"))


@unittest.skipUnless(os.name == "nt", "Actual CMD execution requires Windows")
class WindowsLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "preview project with spaces"
        self.root.mkdir()
        source = Path(__file__).resolve().parent.parent
        for name in ("aic_hand", "scripts", "config"):
            shutil.copytree(source / name, self.root / name, ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("run_preview.cmd", "requirements.txt"):
            shutil.copy2(source / name, self.root / name)
        subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", "--without-pip", str(self.root / ".venv")], check=True)
        self.environment = os.environ.copy()
        self.environment.update(AIC_NO_PAUSE="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

    def run_launcher(self, *args):
        return subprocess.run(["cmd.exe", "/d", "/c", "run_preview.cmd", *args], cwd=self.root,
                              env=self.environment, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)

    def test_actual_cmd_starts_headless_preview_from_space_path(self):
        result = self.run_launcher("--headless", "--demo", "--frames", "5")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("[4/4]", result.stdout)
        self.assertIn("Process exit code: 0", (self.root / "logs/startup/latest.log").read_text(encoding="utf-8"))

    def test_actual_cmd_reports_child_failure_without_hanging(self):
        (self.root / "scripts/bootstrap.py").write_text("print('simulated startup failure', flush=True)\nraise SystemExit(2)\n", encoding="utf-8")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("STARTUP FAILED", result.stdout)
        self.assertIn("Launcher exit code: 2", (self.root / "logs/startup/launcher.log").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
