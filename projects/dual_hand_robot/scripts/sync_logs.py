"""Only this NEW project's numeric logs, only its dedicated Git branch."""
from datetime import datetime
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BRANCH = "dual-hand-robot-20261117"


def git(*args):
    return subprocess.run(["git",*map(str,args)],cwd=ROOT,check=True,capture_output=True,
                          text=True,encoding="utf-8").stdout.strip()


def main():
    try:
        top = Path(git("rev-parse","--show-toplevel")).resolve()
        if ROOT.relative_to(top).as_posix() != "projects/dual_hand_robot":
            raise ValueError("Use the NEW project directory in an AIC Git clone; ZIP logs remain local")
        if git("remote","get-url","origin") not in ("https://github.com/luxinruui/aic--.git","git@github.com:luxinruui/aic--.git"):
            raise ValueError("Unexpected GitHub repository")
        if git("branch","--show-current") != BRANCH:
            raise ValueError("Refusing to change main/legacy branches; switch to "+BRANCH)
        if git("diff","--cached","--name-only"):
            raise ValueError("Other staged changes exist; leave them for their own commit")
        paths = []
        for path in sorted((ROOT/"logs").rglob("*")):
            if path.is_file() and path.suffix.lower() in (".csv",".json",".jsonl",".txt",".log",".md"):
                if path.is_symlink() or path.stat().st_size > 5*1024*1024:
                    raise ValueError("Log is a link or exceeds 5MiB")
                paths.append(path.relative_to(top).as_posix())
        if not paths:
            print("No new project logs")
            return 0
        git("add","--",*paths)
        if git("diff","--cached","--name-only"):
            git("commit","-m","Record dual-hand robot logs "+datetime.now().astimezone().isoformat(timespec="seconds"))
        git("push","origin",BRANCH)
        print("New project logs pushed to "+BRANCH+"; legacy branch not modified")
        return 0
    except (OSError,ValueError,subprocess.CalledProcessError) as error:
        print("Log sync stopped; files preserved locally:",getattr(error,"stderr",None) or str(error))
        return 1


if __name__ == "__main__":raise SystemExit(main())
