#!/bin/sh
set -eu
cd "$(dirname "$0")"
exec python3 -u run_backpack.py --drive "$@"
