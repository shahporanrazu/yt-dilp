#!/usr/bin/env bash
# Start Video Saver on a Mac or Linux:  bash start.sh
# The first start creates a private Python environment in .venv and asks
# for a password. Stop the app with Ctrl+C.
set -euo pipefail
cd "$(dirname "$0")"

# Find Python 3.11 or newer.
PY=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "Video Saver needs Python 3.11 or newer."
  echo "Mac: download it from https://www.python.org/downloads/ (or: brew install python)"
  echo "Linux: install python3 with your package manager."
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "First start: creating a Python environment in .venv ..."
  if ! "$PY" -m venv .venv; then
    echo "Couldn't create it. On Debian/Ubuntu run: sudo apt install python3-venv"
    exit 1
  fi
fi

exec .venv/bin/python main.py
