#!/usr/bin/env bash
# GANDALF (Linux) - one-time setup.
# Installs system packages + a project virtualenv. Run once as your user; the
# apt part needs sudo and will prompt for your password.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Installing Linux system packages (sudo needed)..."
sudo apt-get update
sudo apt-get install -y \
  python3 python3-venv python3-pip python3-tk \
  portaudio19-dev libportaudio2 libsndfile1 \
  xdotool wmctrl brightnessctl \
  pulseaudio-utils alsa-utils \
  espeak-ng speech-dispatcher \
  xclip xsel wl-clipboard scrot \
  tesseract-ocr tesseract-ocr-tur \
  libgtk-3-bin libgtk2.0-0 libgl1 libglib2.0-0 \
  dbus-x11 \
  x11-utils x11-xserver-utils || {
    echo "Some packages could not be installed; continuing anyway."
  }

echo "==> Creating virtualenv (.venv)..."
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate

echo "==> Installing Python packages (requirements.txt)..."
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Making launchers executable..."
chmod +x run.sh WindowGesture/baslat.sh 2>/dev/null || true

echo ""
echo "Setup complete."
echo "  - Optional engines:  clamav clamav-daemon  (security scans)"
echo "                       sudo apt install clamav clamav-daemon"
echo "  - Run Gandalf now:   bash run.sh"