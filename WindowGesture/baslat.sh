#!/usr/bin/env bash
# WindowGesture - El ile Pencere Kontrolu (Linux)
set -e
cd "$(dirname "$0")"
if [ -n "$VIRTUAL_ENV" ]; then
    PY=python
elif command -v python3 >/dev/null 2>&1; then
    PY=python3
else
    PY=python
fi
exec "$PY" window_gesture.py "$@"