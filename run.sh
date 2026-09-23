#!/usr/bin/env bash
# GANDALF (Linux) - launcher.
set -euo pipefail
cd "$(dirname "$0")"

if [ -d ".venv" ]; then
  source .venv/bin/activate
fi

if [ $# -gt 0 ] && [ "$1" = "--no-tray" ]; then
  shift
fi

exec python main.py "$@"