#!/usr/bin/env bash
# Run Android Mirror straight from this source checkout (development).
#   ./start.sh                serve + open the browser
#   ./start.sh --no-browser   serve only
#   ./start.sh -v             debug logging
# For a regular installation use ./install.sh instead.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/android-mirror ]; then
  echo "Setting up the development environment (first run)…"
  python3 -m venv .venv || { echo "Python 3.9+ with venv is required (Debian/Ubuntu: sudo apt install python3-venv)." >&2; exit 1; }
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -e '.[dev]'
fi
exec .venv/bin/android-mirror serve "$@"
