"""Commit preview settings and numeric run logs; push with ordinary Git auth."""
from datetime import datetime
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_ORIGINS = ("https://github.com/luxinruui/aic--", "https://github.com/luxinruui/aic--.git",
                   "git@github.com:luxinruui/aic--.git")


def git(*arguments, capture=False):
    return subprocess.run(["git", *arguments], cwd=ROOT, check=True,
                          text=True, encoding="utf-8", capture_output=capture)


def sync():
    if not (ROOT / ".git").exists():
        raise ValueError("This is a ZIP download. Clone https://github.com/luxinruui/aic-- first, then copy logs into that clone.")
    if git("remote", "get-url", "origin", capture=True).stdout.strip() not in ALLOWED_ORIGINS:
        raise ValueError("Origin is not luxinruui/aic--; no files were staged or pushed.")
    branch = git("branch", "--show-current", capture=True).stdout.strip()
    if not branch:
        raise ValueError("Detached HEAD: select a branch before syncing.")
    if git("diff", "--cached", "--name-only", capture=True).stdout.strip():
        raise ValueError("Other files are already staged. Commit or unstage them before syncing run logs.")
    files = []
    for folder in (ROOT / "logs" / "runs",):
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path.suffix in (".json", ".jsonl", ".csv"):
                if path.is_symlink() or path.stat().st_size > 5 * 1024 * 1024:
                    raise ValueError("Run log is a link or exceeds 5 MB: " + str(path.relative_to(ROOT)))
                files.append(path.relative_to(ROOT).as_posix())
    files.append("config/preview.json")
    # This script is for logs and user calibration. Code changes are committed
    # deliberately by the developer, never swept into a runtime sync.
    for offset in range(0, len(files), 80):
        git("add", "--", *files[offset:offset + 80])
    if git("diff", "--cached", "--name-only", capture=True).stdout.strip():
        git("commit", "-m", "Record hand preview run logs " + datetime.now().strftime("%Y-%m-%d %H:%M"))
    git("push", "-u", "origin", branch)
    print("Synced preview logs and settings to GitHub branch " + branch + ".")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(sync())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print("Sync failed: %s\nNo force-push is used. Check Git login, network, or remote updates." % error, file=sys.stderr)
        raise SystemExit(2)
