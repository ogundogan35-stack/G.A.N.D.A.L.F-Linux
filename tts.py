import io
import os
import subprocess
import threading
import requests
import sounddevice as sd
import soundfile as sf
 
#Take the API key from ElevenLabs

ELEVEN_API_KEY = "GO ELEVENLABS"
VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"

# Master switch for spoken output.
#   False = Gandalf does NOT speak (microphone hears his own voice and thinks it
#           is the user -> feedback loop). Only text is shown.
#   True  = speech enabled again (re-enable later).
VOICE_ENABLED = False

stop_speaking_flag = threading.Event()


def _run(cmd, timeout=120):
    """Run a native command, swallowing errors (best-effort TTS)."""
    try:
        subprocess.run(cmd, shell=False, capture_output=True, timeout=timeout)
    except Exception as e:
        print("VOICE ERROR:", e)


def _native_speak(text: str):
    """Speak using a local native TTS engine.

    Windows  -> System.Speech (SAPI) via PowerShell (no extra libraries).
    Linux    -> espeak-ng / espeak / spd-say (speech-dispatcher), then
                pyttsx3 as a final fallback. System packages are installed
                by setup.sh; if none is present this simply does nothing.
    """
    if not VOICE_ENABLED:
        return
    if not text or not text.strip():
        return

    if os.name == "nt":
        safe = text.replace("'", "''").replace("\"", "`\"")
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command",
             "Add-Type -AssemblyName System.Speech;"
             "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
             "foreach($n in @('Microsoft David Desktop','Microsoft George Desktop',"
             "'Microsoft David','Microsoft George')){"
             "  try{$s.SelectVoice($n);break}catch{}}"
             f"$s.Speak('{safe}')"],
            shell=False,
            capture_output=True,
            timeout=120,
        )
        return

    # Linux / macOS native engines.
    for cmd in (
        ["espeak-ng", "-v", "en", "--"],   # modern eSpeak
        ["espeak", "-v", "en", "--"],      # classic eSpeak
        ["spd-say", "--wait", "--"],       # speech-dispatcher
    ):
        try:
            subprocess.run(cmd + [text], shell=False, capture_output=True,
                           timeout=120)
            return
        except Exception:
            continue
    # Last resort: pyttsx3 if it is installed (setup.sh installs it).
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()
    except Exception:
        print("No native TTS engine available (install espeak-ng).")


def edge_speak(text: str, ui=None, blocking=False):
    if not VOICE_ENABLED:
        return
    if not text.strip():
        return
    

    finished_event = threading.Event()

    def _thread():
        if ui:
            ui.start_speaking()
        stop_speaking_flag.clear()

        # Prefer the ElevenLabs cloud voice if a real key is configured;
        # otherwise fall back to the platform's native TTS engine so the
        # assistant is always able to speak out loud.
        if ELEVEN_API_KEY and "GO" not in ELEVEN_API_KEY.upper():
            try:
                url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"
                headers = {
                    "xi-api-key": ELEVEN_API_KEY,
                    "Content-Type": "application/json"
                }
                payload = {
                    "text": text.strip(),
                    "voice_settings": {
                        "stability": 0.55,
                        "similarity_boost": 0.85
                    }
                }

                response = requests.post(url, json=payload, headers=headers)
                response.raise_for_status()

                audio_data = io.BytesIO(response.content)
                data, samplerate = sf.read(audio_data, dtype="float32")

                channels = data.shape[1] if len(data.shape) > 1 else 1
                with sd.OutputStream(
                    samplerate=samplerate,
                    channels=channels,
                    dtype="float32"
                ) as stream:
                    block_size = 1024
                    for start in range(0, len(data), block_size):
                        if stop_speaking_flag.is_set():
                            break
                        stream.write(data[start:start + block_size])
            except Exception as e:
                print("VOICE ERROR:", e)
                _native_speak(text)
        else:
            _native_speak(text)

        if ui:
            ui.stop_speaking()
        finished_event.set()

    threading.Thread(target=_thread, daemon=True).start()

    if blocking:
        finished_event.wait()

def stop_speaking():
    stop_speaking_flag.set()