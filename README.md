# Gandalf — AI Desktop Assistant

Gandalf is a **modular AI desktop assistant** built from scratch in **Python**.
It takes control of your computer through voice commands, typed commands, an
intent-action pipeline backed by an LLM, and a remote Telegram control channel.

## Features

- Open applications and run system actions (volume, brightness, lock, shutdown,
  window management, clipboard, OCR, automation macros)
- Web search, live weather reports, Spotify playback control
- Camera-based hand gestures: finger-pinch brightness/volume plus a window
  pointer/grab/resize/close overlay
- Voice commands with a fast local **Vosk** engine ("Hey Gandalf" wake word)
- Temporary & long-term memory (identity, preferences, topics, corrections)
- Telegram bot for remote control (text / voice / photo / document / PDF)
- Web dashboard with live system stats
- Platform antivirus (Windows Defender / Linux ClamAV) + heuristic security scans (Trash-only cleanup)

## Voice Commands

- Wake word: say **"Hey Gandalf"** to get its attention.
- Speech is recognised locally by **Vosk** (small English model; a small Turkish
  dictation model also runs in parallel and both are score-selected).
- Command phrases are largely **English** (e.g. "open chrome", "what time is
  it", "close spotify", "weather antalya today") and are matched against a
  built-in command grammar; recognition support for Turkish city names is
  included.
- Add your own spoken shortcuts in `commands.txt`.

## Requirements

- Python 3.10+ (64-bit)
- Install dependencies:

```bash
py -m pip install -r requirements.txt
```

> `requirements.txt` uses per-platform markers: Windows-only packages
> (`pycaw`, `screen-brightness-control`) are skipped on Linux, and the
> Linux-only `python-xlib` (needed by pyautogui) is skipped on Windows.

- Camera-based hand control additionally needs a MediaPipe-ready environment
  and a working webcam (see `PORTABILITY_GUIDE.md`).
- **Linux**: system packages (`python3-tk`, `libgl1`, `portaudio`, ...) are
  needed too. Run `bash setup.sh` instead of the manual commands — see
  `README-linux.md` for the full Linux guide (works on Pardus, Parrot OS,
  Mint, Ubuntu; XFCE/X11 recommended).

## Configuration

Copy `.env.example` to `.env` and fill in your own API keys:

- `GEMINI_API_KEY` — primary brain (Gemini). Get one from
  https://aistudio.google.com/apikey
- `OPENROUTER_API_KEY` — fallback brain. Get one from https://openrouter.ai/keys
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` — remote Telegram control
- `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` — Spotify playback control

`TELEGRAM_CHAT_ID` acts as an allow-list: leave it empty to let anyone use the
bot, or set your numeric id to restrict control to yourself.

## Run

Windows:
```bash
py main.py
```

Linux (setup once, then run):
```bash
bash setup.sh    # one-time: apt packages + .venv + pip (sudo needed)
bash run.sh
```

- On-screen text chat opens (type at the bottom, Enter to send).
- Wake on voice with "Hey Gandalf".
- Telegram: start the bot and send `/start` to wake it, `/sleep` to put it away.
- Hand control (combined brightness/volume + window overlay):

```bash
py WindowGesture\window_gesture.py --hands        # Windows
bash WindowGesture/baslat.sh --hands              # Linux (venv otomatik)
```

## Talking to Gandalf with Telegram

| Command | What it does |
| --- | --- |
| `/start` | Wake Gandalf (active) |
| `/sleep` | Put Gandalf to sleep |
| `/screen on` / `/screen off` | Turn the PC display off/on remotely |
| `/pencere start` / `/pencere stop` | Start / stop the camera hand control |
| `/tarama [status\|clean\|<path>]` | Threat scan / status / cleanup |
| `/screenread [question]` | Take a screenshot and describe/answer it |
| Any other text | Regular chat through the LLM |

> The `/pencere` and `/tarama` command aliases are also registered in English
> (`/windowgesture`, `/scan`, `/screen`) so both spellings work.

## Project Layout

```
main.py                   Main loop: intent dispatch, voice, Telegram, UI
gemini_handler.py        Gemini brain: tool calls + intent contract
llm.py                   OpenRouter fallback + Gemini-first intent parse
speech_to_text.py        Vosk voice recognition + command grammar
telegram_bot.py          Telegram remote control
tts.py                   Text-to-speech (SAPI + ElevenLabs)
ui.py                    CustomTkinter chat UI
dashboard_server.py      Local web dashboard (system stats)
screen_utils.py          Remote monitor on/off helper
core/                    LLM system prompt
actions/                 Individual intents (weather, spotify, file ops, ...)
WindowGesture/           Camera hand control (pointer/gesture overlay)
templates/               Web dashboard html
memory/                  Long-term memory store (runtime JSON, not committed)
```

## Security & Responsibility

- Runs as a desktop app on your machine. The AI brain uses **cloud LLM APIs**
  (Gemini first, OpenRouter fallback) with **your own API keys** — voice input
  is recognised offline by Vosk, but text/prompts you send can leave your PC to
  those providers' servers. No remote access to your computer is enabled by
  default.
- Gandalf only executes what the local intent pipeline allows (file operations
  are restricted to your personal folders; security cleanup always requires
  confirmation and goes to the Recycle Bin).
- **Do NOT paste unknown code into this assistant.** Do not grant system control
  to untrusted scripts.
- Personal / educational use only. Do not sell, market, or present this project
  as your own without attribution.

## Further Reading

- `README_SETUP.md` — detailed setup, structure and per-file notes.
- `PORTABILITY_GUIDE.md` — moving Gandalf to another PC (source or portable exe).
- `commands.txt` — the built-in voice command grammar (English command phrases).