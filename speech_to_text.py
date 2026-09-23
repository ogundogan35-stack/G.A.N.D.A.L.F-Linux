import os
import queue
import sys
import json
import time
import threading

import numpy as np
import sounddevice as sd
import vosk

import runtime_state
from tts import edge_speak


def _find_model_dir(name: str):
    """Locate a vosk model dir across the usual places (bundle / cache / disk)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(base, name),
        os.path.join(base, "app", name),
        os.path.join(os.path.expanduser("~"), ".cache", "vosk", name),
        # Check parent directory for vosk-model folder (portable setup)
        os.path.join(os.path.dirname(base), "vosk-model", name),
    ]
    for cand in candidates:
        if cand and os.path.isdir(cand):
            return cand
    return None


def _load_model(name: str = "vosk-model-small-en-us-0.15"):
    """Load a vosk model (default the small English dictation model).

    The model is searched in this order:
      1. bundled into the portable .exe (sys._MEIPASS / app resource dir)
      2. the local vosk cache (~/.cache/vosk)
      3. a fallback manual folder on disk
    If found on disk it is used directly; otherwise vosk is asked to download.
    """
    cand = _find_model_dir(name)
    if cand:
        try:
            return vosk.Model(cand)
        except Exception:
            pass
    try:
        return vosk.Model(model_name=name)
    except Exception:
        return vosk.Model(cand or name)


# Voice recognition runs English (default) plus Turkish in parallel. The
# smaller Turkish dictation model is loaded alongside the English one so real
# Turkish speech is transcribed by the TR recognizer and then selected by
# _select_best. Each model is loaded lazily and skipped if missing on disk.
model = _load_model("vosk-model-small-en-us-0.15")

_tr_model = None
try:
    _tr_model = _load_model("vosk-model-small-tr-0.3")
except Exception:
    _tr_model = None

q = queue.Queue()
stop_listening_flag = threading.Event()

# Fix a fixed microphone gain to lift a weak (bad) microphone above noise.
MIC_GAIN = 2.5

_TR_CHARS = set("çğıöşüÇĞİÖŞÜ")
_TR_WORDS = frozenset((
    "bir", "ve", "ama", "ya", "de", "da", "ile", "için", "gibi",
    "mi", "mı", "mu", "mü", "ne", "nasıl", "nasil", "şimdi", "burada", "var",
    "yok", "hava", "durumu", "bugün", "bugun", "yarın", "yarin", "güzel",
    "günaydın", "teşekkür", "lütfen", "evet", "hayır", "hayir", "tamam",
    "olur", "olsun", "yap", "yapma", "aç", "ac", "kapat", "göster", "goster",
    "bana", "şu", "su", "bunu", "söyle", "soyle", "ara", "bul", "nerede",
    "nere", "kim", "kaç", "kac", "saat", "istanbul", "ankara", "izmir",
    "izmir", "antalya", "bursa", "eskişehir", "eskisehir", "adana",
))
_EN_WORDS = frozenset((
    "the", "is", "are", "what", "does", "do", "can", "could",
    "would", "should", "have", "has", "had", "will", "for", "with",
    "this", "that", "how", "when", "where", "why", "who", "which",
    "in", "on", "at", "to", "of", "and", "or", "not", "it",
    "my", "your", "his", "her", "its", "our", "their", "a", "an",
    "from", "by", "as", "but", "so", "if", "then", "about",
    "up", "out", "just", "also", "very", "too", "more", "most",
    "weather", "time", "today", "tomorrow", "yesterday",
    "open", "close", "play", "stop", "pause", "search",
    "music", "song", "video", "news", "tell", "show",
    "set", "turn", "volume", "brightness", "volume",
    "lock", "sleep", "restart", "shutdown", "screenshot",
    "temperature", "forecast", "degrees", "rain", "sunny",
    "file", "folder", "copy", "paste", "delete", "move",
    "make", "create", "write", "read", "send", "call",
))


def _detect_lang(text: str) -> str:
    """Return 'tr' or 'en' based on character + word analysis."""
    t = text.lower()
    chars = sum(1 for c in t if c in _TR_CHARS)
    words = set(t.split())
    tr_w = len(words & _TR_WORDS)
    en_w = len(words & _EN_WORDS)
    score = chars * 0.5 + tr_w * 0.3 - en_w * 0.2
    return "tr" if score > 0 else "en"


def _select_best(finals: list) -> str:
    """Pick the single best transcription instead of concatenating.

    Both Vosk models (English + Turkish) run in parallel on the same audio.
    Concatenating their outputs mixes garbage from one model into the other's
    clean result (e.g. English audio → Turkish model hallucinates Turkish
    words → combined text is garbled).  Instead, we score each candidate and
    keep the one that looks most linguistically coherent.
    """
    if len(finals) == 0:
        return ""
    if len(finals) == 1:
        return finals[0]

    def _score(text: str) -> float:
        t = text.lower()
        words = t.split()
        n = len(words) if words else 1
        tr_chars = sum(1 for c in t if c in _TR_CHARS)
        tr_w = len(set(words) & _TR_WORDS)
        en_w = len(set(words) & _EN_WORDS)
        # Balanced scorer: reward Turkish and English vocabulary equally, give a
        # modest bonus for Turkish characters (which the English model never
        # emits) so real Turkish speech is not beaten by English filler, and
        # penalise words that match neither vocabulary (hallucinated garbage).
        s = 0.0
        s += tr_w * 1.0
        s += en_w * 1.0
        s += tr_chars * 0.8
        s -= (n - tr_w - en_w) * 0.5
        return s

    scored = [(f, _score(f)) for f in finals if f]
    if not scored:
        return ""
    return max(scored, key=lambda x: x[1])[0]


def callback(indata, frames, time, status):
    if status:
        print(status, file=sys.stderr)

    # Convert to numpy array for processing
    audio_data = np.frombuffer(indata, dtype=np.int16)

    # Noise reduction: simple high-pass filter (removes hum / rumble)
    audio_data = audio_data.astype(np.float32)
    window_size = 50
    if len(audio_data) > window_size:
        moving_avg = np.convolve(
            audio_data, np.ones(window_size) / window_size, mode='same'
        )
        audio_data = audio_data - moving_avg * 0.5

    # Apply fixed gain boost (weak / bad microphones)
    audio_data = audio_data * MIC_GAIN

    # Soft clip to stay in int16 range
    np.clip(audio_data, -32767, 32767, out=audio_data)

    audio_data = audio_data.astype(np.int16)

    q.put(audio_data.tobytes())


def _pick_input_device():
    """
    Pick a microphone device that can actually be opened for input.

    Checks every device that reports input channels and returns the first one
    that passes sounddevice.check_input_settings (so we never hand an invalid
    index to RawInputStream). Prefers the system default input device.
    """
    default_idx = _dflt_input()
    if default_idx is not None:
        try:
            sd.check_input_settings(device=default_idx, samplerate=16000, channels=1, dtype='int16')
            return default_idx
        except Exception:
            pass
    tested_ok = None
    for i, device in enumerate(sd.query_devices()):
        if not device['max_input_channels']:
            continue
        try:
            sd.check_input_settings(device=i, samplerate=16000, channels=1, dtype='int16')
            if tested_ok is None:
                tested_ok = i
        except Exception:
            continue
    return tested_ok


def _dflt_input():
    try:
        return sd.default.device[0] \
            if isinstance(sd.default.device[0], int) and sd.default.device[0] >= 0 else None
    except Exception:
        return None


def record_voice(prompt="I'm listening, sir...", grammar=None, timeout=10.0, fast=False):
    """
    Blocking call, returns the first recognised sentence as text.

    Works on low-quality microphones using voice-activity detection (VAD) and
    partial-result accumulation instead of waiting forever for a Vosk 'final'.

    `fast=True` tightens the VAD/silence timing so a command spoken right
    after the wake word is captured without delay (used for the armed
    command path where the user is already talking to us).
    """
    try:
        print(prompt)
    except:
        print("Listening...")

    def make_rec():
        recs = [vosk.KaldiRecognizer(model, 16000, grammar) if grammar else
                vosk.KaldiRecognizer(model, 16000)]
        if _tr_model is not None:
            recs.append(vosk.KaldiRecognizer(_tr_model, 16000, grammar) if grammar else
                        vosk.KaldiRecognizer(_tr_model, 16000))
        return recs

    recs = make_rec()
    input_device = _pick_input_device()
    start = time.time()

    # Conversation timing / state
    VOICE_START_MS = 350 if fast else 700        # speech must last this long to count
    SPEECH_SILENCE = 0.6 if fast else 1.2        # seconds of silence that ends the utterance
    voice_started = False
    first_voice_time = None
    last_voice_time = None
    partial_text = ""

    try:
        with sd.RawInputStream(
            samplerate=16000, blocksize=8000, dtype='int16',
            channels=1, callback=callback, device=input_device,
        ):
            while not stop_listening_flag.is_set():
                if timeout and time.time() - start > timeout:
                    break
                try:
                    data = q.get(timeout=0.1)
                except queue.Empty:
                    continue

                block = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                rms = float(np.sqrt(np.mean(block * block))) if block.size else 0.0
                speaking = rms > 120.0   # threshold for audible speech

                now = time.time()

                # Feed the same audio to every recognizer (primary + TR).
                for r in recs:
                    r.AcceptWaveform(data)

                if speaking:
                    if not voice_started:
                        voice_started = True
                        first_voice_time = now
                    last_voice_time = now

                # Accumulate partial results (primary recognizer) while speaking
                if voice_started:
                    p = json.loads(recs[0].PartialResult()).get("partial", "").strip()
                    if p:
                        partial_text = _merge(partial_text, p)

                # End-of-utterance: silence long enough after speech began
                if voice_started and last_voice_time is not None and \
                        (now - first_voice_time) >= VOICE_START_MS / 1000.0 and \
                        (now - last_voice_time) >= SPEECH_SILENCE:
                    finals = [json.loads(r.FinalResult()).get("text", "").strip()
                              for r in recs]
                    finals = [f for f in finals if f]
                    result = _select_best(finals) or partial_text
                    if result:
                        try:
                            print("You:", result)
                        except:
                            print("Recognized:", result)
                        return result
                    # No usable text -> reset and keep listening
                    voice_started = False
                    partial_text = ""
                    recs = make_rec()

    except Exception as e:
        print(f"Error with input device: {e}, trying fallback...")
        try:
            with sd.RawInputStream(
                samplerate=16000, blocksize=8000, dtype='int16',
                channels=1, callback=callback,
            ):
                # Use only the primary recognizer in the fallback path.
                rec = recs[0]
                while not stop_listening_flag.is_set():
                    if timeout and time.time() - start > timeout:
                        break
                    try:
                        data = q.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if rec.AcceptWaveform(data):
                        result = json.loads(rec.Result())
                        text = result.get("text", "")
                        if text.strip():
                            try:
                                print("You:", text)
                            except:
                                print("Recognized:", text)
                            return text
        except Exception as e2:
            print(f"Fallback failed: {e2}")

    return partial_text


def _merge(a: str, b: str) -> str:
    a, b = a.strip(), b.strip()
    if not a:
        return b
    if not b:
        return a
    if b in a or a in b:
        return b if len(b) > len(a) else a
    return b


# ---------------------------------------------------------------------------
# Wake-word + command layer
# ---------------------------------------------------------------------------

WAKE_WORDS = ["gandalf"]

# Wake word grammar stays strict so "Hey Gandalf" is detected reliably even
# on noisy / low-quality microphones.
WAKE_GRAMMAR = '["hey gandalf", "gandalf", "[unk]"]'

# NOTE: After the wake word we deliberately use NO restricted grammar (see
# VoiceCommandListener._run) so the assistant can freely understand anything
# the user says (full dictation mode).

# ---------------------------------------------------------------------------
# PRIORITY COMMAND LIST  --  EDIT FREELY
# ---------------------------------------------------------------------------
# These phrases get matched FIRST when a voice command is heard, before the
# free-form behaviour kicks in. You can add / remove / reword them any time.
# If a spoken command doesn't match anything here, Gandalf still falls back
# to free dictation (normal chat / any other request) — so nothing is lost.
PRIORITY_APPS = [
    "chrome",
    "opera gx",
    "opera",
    "minecraft",
    "spotify",
    "reaper",
    "discord",
    "telegram",
    "notepad",
    "youtube",
]

# When the user says "close <app>", map the friendly name to a real Windows
# process so Gandalf can kill it reliably.
APP_EXE_MAP = {
    "chrome": "chrome.exe",
    "opera gx": "opera.exe",
    "opera": "opera.exe",
    "minecraft": "javaw.exe",
    "spotify": "Spotify.exe",
    "reaper": "reaper.exe",
    "discord": "Discord.exe",
    "telegram": "Telegram.exe",
    "notepad": "notepad.exe",
    "youtube": "chrome.exe",
}


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


# Vosk uses an English model, so Turkish city names get mangled into English
# sounding words ("antalya" -> "tania"/"tanya"). Map the common misrecognitions
# (and the correct spelling) onto the real Turkish city name.
_CITY_ALIASES = {
    "antalya": "Antalya",
    "antalia": "Antalya",
    "antaliya": "Antalya",
    "tania": "Antalya",
    "tanya": "Antalya",
    "taniya": "Antalya",
    "tan ya": "Antalya",
    "ta ya": "Antalya",
    "istanbul": "Istanbul",
    "istanbol": "Istanbul",
    "istambul": "Istanbul",
    "izmir": "Izmir",
    "smirna": "Izmir",
    "ankara": "Ankara",
    "ankira": "Ankara",
    "bodrum": "Bodrum",
    "mersin": "Mersin",
    "bursa": "Bursa",
    "trabzon": "Trabzon",
    "samsun": "Samsun",
    "sam sun": "Samsun",
    "samson": "Samsun",
    "samsom": "Samsun",
    "sanson": "Samsun",
    "eskişehir": "Eskisehir",
    "eskisehir": "Eskisehir",
    "eski sehir": "Eskisehir",
    "adana": "Adana",
    "adiyaman": "Adiyaman",
    "gaziantep": "Gaziantep",
    "gaziantab": "Gaziantep",
    "kayseri": "Kayseri",
    "konya": "Konya",
    "kocaeli": "Kocaeli",
    "kojaeli": "Kocaeli",
    "denizli": "Denizli",
    "sanliurfa": "Sanliurfa",
    "muğla": "Mugla",
    "mugla": "Mugla",
    "aydin": "Aydin",
    "kahramanmaras": "Kahramanmaras",
    "malatya": "Malatya",
    "erzurum": "Erzurum",
    "van": "Van",
    "elazig": "Elazig",
    "bolu": "Bolu",
    "düzce": "Duzce",
    "duzce": "Duzce",
    "sakarya": "Sakarya",
    "sakaria": "Sakarya",
    "canakkale": "Canakkale",
    "balikesir": "Balikesir",
    "manisa": "Manisa",
    "tozla": "Antalya",
    "antalja": "Antalya",
    "sanzun": "Samsun",
    # ... add more cities here as needed
}

# "weather" is sometimes heard as "whether" (both exist in the English model),
# and long weather phrases start with "what's the weather" etc.
_WEATHER_TRIGGERS = (
    "weather in",
    "weather for",
    "weather at",
    "what's the weather",
    "whats the weather",
    "what is the weather",
    "forecast in",
    "forecast for",
    "whether in",
    "whether for",
    "wheather",
    "weater",
)


def _resolve_city(city: str) -> str:
    """Fix misrecognised Turkish city names before sending to the LLM."""
    if not city:
        return city
    key = _norm(city)
    # Try longest key first so multi-word stays intact.
    for k, real in sorted(_CITY_ALIASES.items(), key=lambda kv: -len(kv[0])):
        # match whole city or city followed/preceded by a word boundary
        if k == key or key.startswith(k + " ") or key.endswith(" " + k) or (" " + k + " ") in (" " + key + " "):
            return real
    return city


# Turkish city names fed straight into the command grammar so Vosk's English
# model recognises them reliably (they otherwise get mangled or dropped).
_TR_CITIES = [
    "antalya", "istanbul", "ankara", "izmir", "bursa", "mersin", "adana",
    "trabzon", "bodrum", "eskişehir", "eskisehir", "muğla", "mugla",
    "konya", "gaziantep", "samsun", "denizli", "kayseri", "edirne",
    "çanakkale", "canakkale", "afyon", "aydın", "aydin", "kütahya",
    "kutahya", "sivas", "erzurum", "malatya", "diyarbakır", "diyarbakir",
    "van", "kocaeli", "sakarya", "balıkesir", "balikesir", "manisa",
    "uşak", "usak", "rize", "ordu", "giresun", "tokat", "çorum", "corum",
    "amasya", "sinop", "zonguldak", "bolu", "düzce", "duzce", "yalova",
    "kırklareli", "kirklareli", "tekirdağ", "tekirdag", "karabük", "karabuk",
    "osmaniye", "kilis", "hatay", "şanlıurfa", "sanliurfa", "adıyaman",
    "adiyaman", "elazığ", "elazig", "bingöl", "bingol", "tunceli", "ağrı",
    "agri", "kars", "ığdır", "igdir", "ardahan", "bitlis", "muş", "mus",
    "hakkari", "şırnak", "sirnak", "mardin", "batman", "siirt", "karaman",
    "niğde", "nigde", "nevşehir", "nevsehir", "kırşehir", "kirsehir",
    "aksaray", "bayburt", "gümüşhane", "gumushane",
]

# Command phrases (grammar) for the armed path. Vosk is far more accurate and
# fast when restricted to a known word set, and this list guarantees the
# Turkish city names are recognised. Command words are ~90% English (per user).
_COMMAND_PHRASES = [
    "open", "close", "play", "pause", "stop", "next", "previous",
    "what time is it", "what is the time", "what's the time",
    "who are you", "how are you", "hello", "hi gandalf", "good morning",
    "good afternoon", "good evening", "thank you", "thanks",
    "search for", "weather in", "weather for", "what's the weather in",
    "what is the weather in", "forecast for", "forecast in",
    "volume up", "volume down", "mute", "unmute",
    "set volume", "sleep", "lock", "restart", "shutdown",
    "screenshot", "take a screenshot", "tell me a joke", "tell me",
    "open facebook", "spotify", "youtube", "telegram",
]


def _build_command_grammar():
    """Combine command phrases + city names into a Vosk grammar word list."""
    phrases = list(_COMMAND_PHRASES) + list(_TR_CITIES)
    for app in PRIORITY_APPS:
        phrases.append("open " + app)
        phrases.append("close " + app)
    return phrases


# Vosk's KaldiRecognizer takes the grammar as a JSON-encoded string of phrases.
_COMMAND_GRAMMAR = json.dumps(_build_command_grammar())


def contains_wake(text: str) -> bool:
    t = _norm(text)
    return any(w in t for w in WAKE_WORDS)


def _find_app(t: str):
    """Return the app name from the priority list that occurs in t (longest first)."""
    for app in sorted(PRIORITY_APPS, key=len, reverse=True):
        if app in t:
            return app
    return None


# User-defined voice command aliases loaded from a side-by-side commands.txt
# (placed next to the .exe or this folder). Format:  <spoken> = <command line>
_USER_CMD_CACHE = {"path": None, "map": None}


def _user_cmds_path() -> str | None:
    candidates = [
        os.path.join(os.getcwd(), "commands.txt"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "commands.txt"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "commands.txt"),
        os.path.join(getattr(sys, "_MEIPASS", ""), "commands.txt"),
    ]
    for cand in candidates:
        if cand and os.path.isfile(cand):
            return cand
    return None


def _load_user_commands() -> dict:
    """Return {lowercased_spoken: command_line} from commands.txt (cached)."""
    path = _user_cmds_path()
    if _USER_CMD_CACHE["path"] == path and _USER_CMD_CACHE["map"] is not None:
        return _USER_CMD_CACHE["map"]
    mapping = {}
    if path:
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    spoken = _norm(key)
                    command = val.strip()
                    if spoken and command:
                        mapping[spoken] = command
        except Exception:
            pass
    _USER_CMD_CACHE["path"] = path
    _USER_CMD_CACHE["map"] = mapping
    return mapping


def classify_command(text: str, _visited=None):
    """
    Convert a recognised phrase into (intent, clean_command_line).
    Falls back to ("chat", text) when nothing matches.

    Command mapping:
        close <app>            -> system_control, "close <app>"  (kills process)
        open <app>             -> open_app
        weather <place>...     -> weather_report
        search for <q>         -> search
        anything else          -> chat (free dictation)
    """
    t = _norm(text)
    low_t = " " + t + " "

    # --- user-defined command aliases (commands.txt) -----------------
    # If the spoken phrase matches a user alias, resolve that alias' command
    # line through the normal classifier once. _visited guards against loops.
    if _visited is None:
        _visited = set()
    for spoken, command in _load_user_commands().items():
        if spoken and spoken in t and spoken not in _visited:
            _visited.add(spoken)
            return classify_command(command, _visited)

    # --- "close <app>" / "shut down <app>" --------------------------
    close_words = ("close", "shut down", "close down", "kill", "quit", "exit")
    for cw in close_words:
        if t.startswith(cw + " ") or f" {cw} " in low_t:
            rest = t.split(cw, 1)[1].strip()
            app = _find_app(rest) or rest
            return "system_control", f"close {app}"

    # --- weather ------------------------------------------------------
    # Check the long triggers first, then a bare "weather"/"whether" prefix.
    for marker in _WEATHER_TRIGGERS:
        if marker in t:
            rest = t.split(marker, 1)[1].strip()
            return "weather_report", "weather in " + _resolve_city(rest)

    # "weather antalya today" / "whether antalya today" -> rest = "antalya today"
    if t.startswith("weather") or t.startswith("whether") or t.startswith("wheather") or t.startswith("weater"):
        rest = t.split(None, 1)[1].strip() if " " in t else ""
        return "weather_report", "weather in " + _resolve_city(rest)
    for marker in ("hava durumu", "hava nasıl", "hava nasil", "hava"):
        if marker in t:
            rest = t.split(marker, 1)[1].strip()
            return "weather_report", "weather in " + _resolve_city(rest)

    # --- open / start <app> ------------------------------------------
    for verb in ("open", "start", "launch", "run", "open up"):
        if t.startswith(verb + " ") or f" {verb} " in low_t:
            rest = t.split(verb, 1)[1].strip()
            return "open_app", f"open {rest}"
    for verb in ("aç", "başlat", "ac", "baslat"):
        if t.startswith(verb + " ") or f" {verb} " in low_t:
            rest = t.split(verb, 1)[1].strip()
            return "open_app", f"open {rest}"

    # --- bare app name still opens it --------------------------------
    app = _find_app(t)
    if app:
        return "open_app", f"open {app}"

    # --- search --------------------------------------------------------
    for marker in ("search for", "look up", "find", "ara", "araştır", "search"):
        if marker in t:
            rest = t.split(marker, 1)[1].strip()
            return "search", f"search for {rest}"

    return "chat", text


class VoiceCommandListener:
    """
    Continuously watches the microphone on a background thread.

    State machine:
        armed=False -> waits for the wake word ("Hey Gandalf")
        armed=True  -> listens for the actual command (open/weather/search...)

    Recognised commands are pushed straight into the UI text input queue, so
    the rest of Gandalf treats them exactly like typed text.
    """

    def __init__(self, ui=None, arm_timeout: float = 8.0, log=True):
        self.ui = ui
        self.arm_timeout = arm_timeout
        self.log = log
        self.running = True

    def start(self):
        self.running = True
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        return t

    def stop(self):
        self.running = False
        stop_listening_flag.set()

    def _log(self, msg: str):
        if self.log and self.ui is not None:
            try:
                self.ui.write_log(msg)
            except Exception:
                pass

    def _say(self, msg: str):
        """Speak a short acknowledgement / question out loud (TTS)."""
        try:
            edge_speak(msg, self.ui)
        except Exception:
            pass

    def _push(self, command_line: str):
        """Send the command to the UI input queue (like typed text)."""
        if self.ui is not None:
            try:
                self.ui.text_input_queue.put(command_line)
                return
            except Exception:
                pass
        print("VOICE->", command_line)

    def _run(self):
        armed_until = 0.0

        while self.running:
            if stop_listening_flag.is_set():
                stop_listening_flag.clear()

            # Sleep/wake gate: while Gandalf is asleep, don't run the Vosk
            # microphone engine at all (no wake-word audio, no transcription).
            if not runtime_state.is_active():
                time.sleep(1.0)
                continue

            armed = time.time() < armed_until

            if not armed:
                # Wake word: strict grammar for reliable "Hey Gandalf" detection.
                phrase = record_voice(
                    prompt="(wake)",
                    grammar=WAKE_GRAMMAR,
                    timeout=40.0,
                )
                if not phrase:
                    continue
                if contains_wake(phrase):
                    self._log("GANDALF: At your service, sir.")
                    self._say("Yes, sir?")
                    armed_until = time.time() + self.arm_timeout
                continue

            # Armed -> command grammar. Vosk is restricted to a known word set
            # (English command phrases + Turkish city names) so it is both fast
            # and accurate, and reliably recognises cities like "Antalya".
            phrase = record_voice(
                prompt="(command)",
                grammar=_COMMAND_GRAMMAR,
                timeout=self.arm_timeout,
                fast=True,
            )

            if not phrase:
                armed_until = 0.0
                continue

            # A leftover [unk] is not a real word we can act on; send it to the
            # LLM as chat so Gandalf can still respond / clarify.
            armed_until = 0.0
            if "[unk]" in phrase:
                self._log(f"GANDALF: Something like: {phrase}")
                self._push(phrase)
                continue

            # Send the recognised phrase to the AI unchanged (Gemini-first).
            # Gemini (via ai_loop's get_llm_output_gemini_first) does the intent
            # classification itself: it understands "weather for antalya" as a
            # weather request and answers factual questions directly instead of
            # forcing a Google search. The old local classify_command used to
            # pre-parse the phrase and often mangled mixed-language commands or
            # turned conversational questions into web searches.
            self._log(f"GANDALF: {phrase}")
            self._push(phrase)
            self._log("GANDALF: On it, sir.")
            self._say("Right away, sir.")
