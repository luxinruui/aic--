"""Create an isolated environment without changing the existing robot_vision env."""
from pathlib import Path
import os
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent.parent


def main():
    if not (3, 10) <= sys.version_info[:2] <= (3, 12):
        if os.name == "nt":
            for version in ("3.10", "3.11", "3.12"):
                try:
                    probe = subprocess.run(["py", "-" + version, "--version"], capture_output=True)
                    if probe.returncode == 0:
                        return subprocess.call(["py", "-" + version, str(Path(__file__).resolve())])
                except FileNotFoundError:
                    break
        print("Python 3.10, 3.11 or 3.12 is required. Use your existing Python 3.10 installation.")
        return 2
    destination = ROOT / ".venv"
    python = destination / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        print("Creating project environment in .venv ...", flush=True)
        venv.EnvBuilder(with_pip=True).create(destination)
    print("Installing project dependencies ...", flush=True)
    subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(ROOT / "requirements.txt")],
                   cwd=ROOT, check=True)
    subprocess.run([str(python), "-m", "pip", "check"], cwd=ROOT, check=True)
    subprocess.run([str(python), "-m", "aic_hand", "--check"], cwd=ROOT, check=True)
    print("Setup complete. Run run_preview.cmd, or .venv/bin/python -m aic_hand .")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError) as error:
        print("Setup failed: %s\nCheck the connection and rerun setup_preview.cmd." % error, file=sys.stderr)
        raise SystemExit(2)
