"""Install/repair the isolated preview environment and save all setup output."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.bootstrap import main


if __name__ == "__main__":
    raise SystemExit(main(argv=[], setup_only=True))
