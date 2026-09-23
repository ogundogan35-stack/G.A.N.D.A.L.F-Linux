# ProjectGandalf — Portability Guide

ProjectGandalf is arranged to **run on any computer**. Hard-coded absolute paths
have been replaced with dynamic path resolution.

## Portability Improvements

### 1. Vosk Speech Model Paths (speech_to_text.py)
- **Old:** Hard-coded absolute path (e.g. `C:\Users\...\vosk-model`)
- **New:** Dynamic path lookup, checking the project folder, the user cache
  folder, and automatic download support.
  - Download your model from https://alphacephei.com/vosk/models
  - Place `vosk-model-small-en-us-0.15` in the project folder.

### 2. Hand Control Script (hand_control.py)
- **Old:** Hard-coded absolute path to `window_gesture.py`
- **New:** Flexible path resolution:
  - Dynamic lookup relative to the project layout
  - Environment variable support: `HAND_GESTURE_CONTROL_SCRIPT`
  - Informative message if the script is not found

### 3. Improved Error Messages
- Clear message when the hand control script is missing
- Actionable suggestions for the user

## Installing on Another Computer

### Step 1: Copy the ProjectGandalf folder
Copy the whole `ProjectGandalf` folder to the target computer.

### Step 2: Python requirements
Python 3.10+ (64-bit) must be installed on the target computer.

### Step 3: Install libraries
Use `requirements.txt` (it handles per-platform markers automatically):
```bash
py -m pip install -r requirements.txt
```
Linux users also need system packages — run `bash setup.sh` once instead
(`python3-tk`, `libgl1`, portaudio, `xdotool`/`wmctrl`, ...); see README-linux.md.

### Step 4: Configure the .env file
Copy `.env.example` to `.env` and fill in your own keys:
```env
OPENROUTER_API_KEY=<new or existing key>
TELEGRAM_BOT_TOKEN=<new or existing token>
TELEGRAM_CHAT_ID=<your chat ID>
GEMINI_API_KEY=<new or existing key>
SPOTIFY_CLIENT_ID=<your client ID>
SPOTIFY_CLIENT_SECRET=<your client secret>
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
```

### Step 5: Run
Windows:
```bash
cd ProjectGandalf
py main.py
```
Linux:
```bash
cd ProjectGandalf
bash setup.sh    # one-time
bash run.sh
```

## Optional: Hand Control Extra Setup

The hand control feature is optional. If you want to use it:

### Option 1: WindowGesture project
1. Download the WindowGesture project
2. Put it in the same parent folder as ProjectGandalf:
   ```
   Dev/
   ├── ProjectGandalf/
   └── WindowGesture/
       └── window_gesture.py
   ```

### Option 2: Environment variable
```bash
:: Windows
set HAND_GESTURE_CONTROL_SCRIPT=C:\path\to\window_gesture.py
```
```bash
# Linux/Mac
export HAND_GESTURE_CONTROL_SCRIPT=/path/to/window_gesture.py
```

### Option 3: Without hand control
If the hand control script is not found, all other features keep working.

## Which Features Work on Any Computer?

### Works 100%
- Voice command system (Vosk model auto-downloaded or placed manually)
- AI brain system (OpenRouter + Gemini)
- Telegram bot control
- User interface
- Web dashboard
- Weather
- Web search
- File operations
- System control (Windows + Linux — see README-linux.md)
- Memory system

### Platform Specific
- **System control:** Windows fully; Linux via system tools (`pactl`,
  `brightnessctl`, `wmctrl`/`xdotool`, `systemctl`, `nmcli`). Requires an X11
  (Xorg) session — Wayland is degraded (window automation, screen on/off and
  screenshots are limited); XFCE (Pardus) works fully.
- **Hand control:** requires the WindowGesture project (optional); on Linux it
  needs `xdotool`, `wmctrl`, `brightnessctl`, `pactl` and a working webcam

### Needs Reconfiguration
- **Spotify:** OAuth token resets, re-authorize once
- **Memory:** starts fresh
- **API keys:** can be reused, but regenerating is safer

## Testing After Setup

1. **Voice:** say "Hey Gandalf" and give a command
2. **Text:** type "open chrome" in the UI
3. **Telegram:** send a command through the bot
4. **Dashboard:** open http://localhost:5000
5. **Spotify:** ask to play music (OAuth required)

## Troubleshooting

### Hand control script not found
```
Error: "The hand control script is not found"
Fix: install the WindowGesture project or set HAND_GESTURE_CONTROL_SCRIPT
```

### Vosk model could not be downloaded
```
Error: speech recognition is not working
Fix: check your internet connection or download the model manually
```

### Spotify token error
```
Error: "Spotify session has expired"
Fix: delete memory/spotify_token.json and re-authorize
```

## Summary

ProjectGandalf is **fully portable**:
- No hard-coded absolute paths
- Dynamic path resolution
- Platform-agnostic configuration
- User-friendly error messages
- Optional features degrade gracefully

Copy the folder to another computer and run it!