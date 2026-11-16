#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
python3 -c 'import sys; assert sys.version_info >= (3,8), "Python 3.8+ required"'
python3 run_backpack.py --check-deps
echo 'The bundled pure-Python wheel is loaded directly. No pip or network needed.'
echo 'Ready. Preview: python3 -u run_backpack.py'
