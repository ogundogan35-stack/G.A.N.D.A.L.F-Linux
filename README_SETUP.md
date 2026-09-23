# ProjectGandalf — AI Assistant System

This folder contains all required files for the Gandalf AI assistant project.

## Portability Note
ProjectGandalf is arranged to **run on any computer**. Hard-coded paths have been
removed and dynamic path resolution was added. See `PORTABILITY_GUIDE.md` for details.

## Project Structure

```
ProjectGandalf/
├── main.py                   # Main application entry point
├── llm.py                    # OpenRouter + Gemini integration
├── gemini_handler.py         # Gemini function calling
├── telegram_bot.py           # Telegram control
├── speech_to_text.py         # Speech recognition (Vosk)
├── tts.py                    # Speech synthesis
├── ui.py                     # User interface
├── dashboard_server.py       # Web dashboard
├── runtime_state.py          # Runtime management
├── screen_utils.py           # Screen helpers
├── core/
│   └── prompt.txt            # System prompt
├── actions/
│   ├── file_ops.py           # File operations
│   ├── hand_control.py       # Hand control
│   ├── live_weather.py       # Live weather
│   ├── open_app.py           # App launcher
│   ├── spotify.py            # Spotify integration
│   ├── system_control.py     # System control
│   ├── weather_report.py     # Weather report
│   ├── web_search.py         # Web search
│   ├── screen_vision.py      # Screen vision (OCR / screen description)
│   ├── security.py           # Security scan/clean
│   ├── automation.py         # Automation helpers
│   ├── computer.py           # Computer info
│   ├── clipboard_screen.py   # Clipboard + OCR
│   ├── homework.py           # Homework helpers
│   ├── window_control.py     # Window management
│   └── system_diagnose.py    # System diagnostics
├── memory/
│   ├── memory_manager.py     # Memory management
│   └── temporary_memory.py   # Temporary memory
├── templates/
│   └── index.html            # Web dashboard HTML
└── WindowGesture/            # Camera hand-gesture control
```

## Setup Steps

### 1. Python Requirements
- Python 3.10+ (64-bit)

### 2. Install Required Libraries
Use `requirements.txt` — it contains per-platform markers, so Windows-only
packages (`pycaw`, `screen-brightness-control`) are skipped automatically on
Linux, and the Linux-only `python-xlib` (needed by pyautogui) is skipped on
Windows:
```bash
cd ProjectGandalf
py -m pip install -r requirements.txt
```
Manual list (Windows: include `pycaw` and `screen-brightness-control`;
Linux: do NOT install them, use the system tools instead — see README-linux.md):
```bash
py -m pip install customtkinter vosk sounddevice soundfile numpy requests python-dotenv flask psutil pyautogui pillow python-telegram-bot google-genai opencv-python mediapipe python-xlib pystray
```
Linux users should prefer `bash setup.sh`, which installs the system packages
(`python3-tk`, `libgl1`, `portaudio`, ...) and the Python deps together.

### 3. Vosk Speech Model (AUTO-DOWNLOADED)
- The model is downloaded automatically on first run into
  `~/.cache/vosk/` (Linux) / `%LOCALAPPDATA%\vosk\` (Windows).
- Offline install: download `vosk-model-small-en-us-0.15` from
  https://alphacephei.com/vosk/models and place it in this folder.

### 4. Configure the .env File
The `.env` file should contain these variables:
```env
OPENROUTER_API_KEY=<your OpenRouter API key>
TELEGRAM_BOT_TOKEN=<bot token from BotFather>
TELEGRAM_CHAT_ID=<your Telegram chat id>
GEMINI_API_KEY=<your Google Gemini API key>
SPOTIFY_CLIENT_ID=<Spotify Client ID>
SPOTIFY_CLIENT_SECRET=<Spotify Client Secret>
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
```
Copy `.env.example` and fill in your own values. Never commit `.env`.

### 5. Run
Windows:
```bash
py main.py
```
Linux (see README-linux.md for the full setup):
```bash
bash setup.sh && bash run.sh
```

## Core Features

### Voice Commands
- Wake word: "Hey Gandalf"
- Open/close applications
- Weather queries
- Web search
- System control
- Commands are matched with a built-in **Vosk grammar** (English command phrases).
  Customise the word list in `speech_to_text.py` (`_COMMAND_PHRASES` and city names).

### Telegram Bot
- Remote control
- Voice message support
- Screen capture sending

### Spotify Integration
- Playlist/track playback
- Playback controls

### Hand Control
- Camera-based hand gestures
- Brightness/volume adjustment
- Window control

### Web Dashboard
- Live system statistics
- Chat history
- Mobile-friendly interface

## Security Notes

- Keep API keys in the `.env` file
- Do NOT upload the `.env` file to GitHub
- Use the Telegram Chat ID for allowed-user security

## Usage Warning

- For **personal and educational** purposes only
- No commercial use
- Be careful with code changes