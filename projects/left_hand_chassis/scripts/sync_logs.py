"""Commit/push ONLY this subproject's numeric logs, on an existing Git clone."""
from datetime import datetime
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = ("https://github.com/luxinruui/aic--.git", "git@github.com:luxinruui/aic--.git")


def git(*args):
    return subprocess.run(["git",*map(str,args)],cwd=ROOT,check=True,capture_output=True,text=True,encoding="utf-8").stdout.strip()


def sync():
    try:
        top=Path(git("rev-parse","--show-toplevel")).resolve()
        if git("remote","get-url","origin") not in ALLOWED:
            raise ValueError("Unexpected Git origin; only the AIC repository is supported")
        if git("diff","--cached","--name-only"):
            raise ValueError("Other staged changes exist; commit or unstage those first")
        branch=git("symbolic-ref","--quiet","--short","HEAD")
        if not branch:
            raise ValueError("Detached HEAD; switch to the intended branch first")
        prefix=ROOT.relative_to(top)
        files=[]
        for path in (ROOT/"logs").rglob("*"):
            if path.is_file() and path.suffix.lower() in (".csv",".json",".jsonl",".txt",".log",".md"):
                if path.is_symlink() or path.stat().st_size>5*1024*1024:
                    raise ValueError("Log is a link or exceeds 5 MiB")
                files.append(path.relative_to(top).as_posix())
        if not files:
            print("No log files to sync.")
            return 0
        git("add","--",*files)
        if git("diff","--cached","--name-only"):
            git("commit","-m","Record left-hand chassis PC logs "+datetime.now().astimezone().isoformat(timespec="seconds"))
        git("push","origin",branch)
        print("Numeric logs pushed to "+branch)
        return 0
    except (subprocess.CalledProcessError,ValueError,OSError) as error:
        print("Sync stopped; local logs are preserved.")
        print(getattr(error,"stderr",None) or str(error))
        print("ZIP downloads have no Git history. Clone https://github.com/luxinruui/aic--.git to use this tool.")
        return 2


if __name__=="__main__":
    raise SystemExit(sync())
