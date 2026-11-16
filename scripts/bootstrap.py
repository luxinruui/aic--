"""Visible, logged startup for the Windows double-click entry point."""
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parent.parent


class StartupLog:
    def __init__(self, root):
        self.path = root / "logs" / "startup" / "latest.log"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            previous = self.path.with_name("previous.log")
            previous.write_bytes(self.path.read_bytes())
        self.file = self.path.open("w", encoding="utf-8")
        self.write("AIC hand preview startup " + datetime.now().astimezone().isoformat())

    def write(self, message):
        message = str(message).rstrip("\r\n") + "\n"
        self.file.write(message)
        self.file.flush()
        try:
            sys.stdout.write(message)
            sys.stdout.flush()
        except UnicodeEncodeError:
            encoding = sys.stdout.encoding or "ascii"
            sys.stdout.write(message.encode(encoding, errors="backslashreplace").decode(encoding))
            sys.stdout.flush()

    def run(self, command, root):
        # All children are Python; make pip, model and app errors unbuffered UTF-8.
        environment = os.environ.copy()
        environment.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        self.write("$ " + subprocess.list2cmdline([str(arg) for arg in command]))
        with subprocess.Popen([str(arg) for arg in command], cwd=root, env=environment,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace") as process:
            for line in process.stdout:
                self.write(line)
            code = process.wait()
        self.write("Process exit code: " + str(code))
        return code

    def close(self):
        self.file.close()


def probe_runtime(command):
    source = ("import json,struct,sys; "
              "print(json.dumps({'version':list(sys.version_info[:2]),'bits':struct.calcsize('P')*8,"
              "'executable':sys.executable}))")
    try:
        result = subprocess.run([*map(str, command), "-c", source], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=10)
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def supported(info):
    return (isinstance(info, dict) and tuple(info.get("version", ())) in ((3, 10), (3, 11), (3, 12))
            and info.get("bits") == 64)


def python_in(environment):
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def candidates():
    output = [[getattr(sys, "_base_executable", sys.executable)], [sys.executable]]
    project_python = python_in(ROOT / ".venv")
    if project_python.is_file():
        output.append([str(project_python)])
    launcher = shutil.which("py")
    if launcher:
        output.extend([[launcher, "-" + version] for version in ("3.10", "3.11", "3.12")])
    if os.name == "nt":
        home = Path.home()
        for installation in ("miniconda3", "anaconda3", "miniforge3"):
            for path in (home / installation / "envs" / "robot_vision" / "python.exe",
                         home / installation / "python.exe"):
                if path.is_file():
                    output.append([str(path)])
        for version in ("Python310", "Python311", "Python312"):
            path = home / "AppData" / "Local" / "Programs" / "Python" / version / "python.exe"
            if path.is_file():
                output.append([str(path)])
    for name in ("python", "python3"):
        executable = shutil.which(name)
        if executable:
            output.append([executable])
    unique = []
    for command in output:
        if command not in unique:
            unique.append(command)
    return unique


def find_runtime(log):
    for command in candidates():
        info = probe_runtime(command)
        if supported(info):
            log.write("Using Python %s (%s bit): %s" % (".".join(map(str, info["version"])), info["bits"], info["executable"]))
            return info
    raise RuntimeError("No 64-bit Python 3.10, 3.11 or 3.12 was found. The existing robot_vision Python can be used.")


def choose_environment(root, info, log):
    environment = root / ".venv"
    interpreter = python_in(environment)
    if interpreter.is_file() and not supported(probe_runtime([interpreter])):
        # Preserve an incompatible or incomplete old environment. Never delete it.
        environment = root / (".preview-venv-%s%s" % tuple(info["version"]))
        log.write("The old .venv is incompatible; preserving it and using " + environment.name)
    return environment


def requirements(root):
    return dict(line.split("==", 1) for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#"))


def dependencies_ready(interpreter, expected, root):
    source = ("import importlib.metadata as m,json,tkinter; "
              "expected=json.loads(%r); "
              "assert all(m.version(k)==v for k,v in expected.items())") % json.dumps(expected)
    try:
        result = subprocess.run([str(interpreter), "-c", source], cwd=root, capture_output=True, timeout=20)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ensure_environment(root, log, force_setup=False):
    log.write("[1/4] Finding a supported Python...")
    info = find_runtime(log)
    environment = choose_environment(root, info, log)
    interpreter = python_in(environment)
    log.write("[2/4] Checking project environment...")
    if not interpreter.is_file():
        code = log.run([info["executable"], "-m", "venv", str(environment)], root)
        if code:
            raise RuntimeError("Could not create the project Python environment.")
    if not supported(probe_runtime([interpreter])):
        raise RuntimeError("The project environment Python is invalid. See the startup log.")
    expected = requirements(root)
    if force_setup or not dependencies_ready(interpreter, expected, root):
        log.write("[3/4] Installing or repairing dependencies. Keep this window open...")
        code = log.run([interpreter, "-m", "pip", "install", "--disable-pip-version-check", "-r", root / "requirements.txt"], root)
        if code:
            raise RuntimeError("Dependency installation did not finish. Full pip output is saved in the startup log.")
        if log.run([interpreter, "-m", "pip", "check"], root):
            raise RuntimeError("Dependency compatibility check failed.")
        if not dependencies_ready(interpreter, expected, root):
            raise RuntimeError("Dependencies or tkinter are still unavailable. Check that this Python includes Tcl/Tk.")
        if log.run([interpreter, "-m", "aic_hand", "--check"], root):
            raise RuntimeError("The hand model check failed.")
    else:
        log.write("[3/4] Dependencies already match; no installation needed.")
    return interpreter


def main(argv=None, setup_only=False, root=None):
    root = Path(root) if root is not None else ROOT
    log = None
    try:
        log = StartupLog(root)
        interpreter = ensure_environment(root, log, force_setup=setup_only)
        if setup_only:
            log.write("[4/4] Setup complete. Double-click run_preview.cmd to open the preview.")
            return 0
        log.write("[4/4] Opening hand preview. The camera window should appear shortly...")
        code = log.run([interpreter, "-u", "-m", "aic_hand", *(sys.argv[1:] if argv is None else argv)], root)
        if code:
            log.write("Preview failed. Please send logs/startup/latest.log for diagnosis.")
        return code
    except Exception as error:
        if log:
            log.write("STARTUP FAILED: " + str(error))
            log.write(traceback.format_exc())
        else:
            print("STARTUP FAILED: " + str(error), flush=True)
        return 2
    finally:
        if log:
            log.close()


if __name__ == "__main__":
    raise SystemExit(main())
