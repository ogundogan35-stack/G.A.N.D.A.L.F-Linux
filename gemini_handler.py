"""Gemini-powered brain for Gandalf's Telegram control channel.

Replaces the local AI loop *for Telegram messages only* with Google Gemini
(google-genai SDK). Normal chat messages get a Gandalf-styled reply; when the
user asks for computer control, Gemini emits a function call that runs the
matching Python function:

    open_application(app_name)   -> opens an app using the platform launcher
    take_screenshot()            -> saves a PNG; caller sends it to Telegram
    run_system_command(cmd)      -> runs a shell command (captured output)

The existing TelegramController in telegram_bot.py is untouched. This module is
self-contained and falls back gracefully when \"google-genai\" or a
GEMINI_API_KEY is missing.
"""

import os
import time
import json
import subprocess

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

try:
    from google import genai
    from google.genai import types
    GEMINI_SDK = True
except Exception:
    genai = None
    types = None
    GEMINI_SDK = False

# Fast flash-lite model is used first (intent classification + quick replies
# are ~2-6x faster than gemini-flash-preview with equivalent accuracy). On
# 404/429/503 the handler falls back down the list below automatically.
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip() or "gemini-3.5-flash-lite"
MODELS = [MODEL, "gemini-3.1-flash-lite", "gemini-3-flash-preview", "gemini-3.6-flash"]
_client = None


def _load_key():
    if load_dotenv is not None:
        try:
            load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
        except Exception:
            pass
    return os.getenv("GEMINI_API_KEY", "").strip()


API_KEY = _load_key()
GEMINI_OK = bool(GEMINI_SDK and API_KEY)

SYSTEM_INSTRUCTION = (
    "You are Gandalf, a loyal personal AI assistant living on the user's "
    "Windows or Linux computer. You speak like Gandalf the Grey: wise, calm, a little "
    "dramatic, and always respectful ('sir/madam'). "
    "You answer general chat warmly and concisely (1-3 short sentences). "
    "When the user asks to control the computer, use the available functions "
    "instead of merely describing them: e.g. 'open chrome' -> open_application, "
    "'take a screenshot' -> take_screenshot, 'run this command' -> "
    "run_system_command, 'play X on spotify' -> spotify_play. "
    "Never invent commands that were not asked for."
)


def _function_declarations():
    return types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="open_application",
            description=(
                "Open an application by its casual name (e.g. 'chrome', "
                "'notepad', 'spotify'). Uses the platform launcher "
                "(Windows start-menu search / Linux desktop launcher)."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "app_name": types.Schema(
                        type="STRING",
                        description="The application name to open, e.g. 'chrome'.",
                    ),
                },
                required=["app_name"],
            ),
        ),
        types.FunctionDeclaration(
            name="take_screenshot",
            description=(
                "Take a screenshot of the whole computer screen. Saves a PNG to "
                "disk; the caller sends it to the user as a photo."
            ),
            parameters=types.Schema(type="OBJECT", properties={}),
        ),
        types.FunctionDeclaration(
            name="get_weather",
            description=(
"Get up-to-date, real weather for a city (e.g. 'Antalya'). "
        "Use this whenever the user asks about the weather anywhere. "
        "Returns live temperature, conditions (rain/snow/cloud), "
        "humidity, wind and today/tomorrow forecast. Then explain it "
        "advising whether it's OK to go out (hot/cold, rain or not, "
        "whether it's suitable to go outside)."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "city": types.Schema(
                        type="STRING",
                        description="The city name, e.g. 'Antalya', 'Istanbul'.",
                    ),
                },
                required=["city"],
            ),
        ),
        types.FunctionDeclaration(
            name="run_system_command",
            description=(
                "Run a command in the system shell to execute or check "
                "something on the machine (e.g. 'ipconfig' / 'hostname -i'). "
                "Returns the command output."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "cmd": types.Schema(
                        type="STRING",
                        description="The exact system command to run.",
                    ),
                },
                required=["cmd"],
            ),
        ),
        types.FunctionDeclaration(
            name="spotify_play",
            description=(
                "Play a playlist, album, artist or track on Spotify. Use this "
                "whenever the user asks to play music, a playlist or a specific "
                "song/album on Spotify. Searches Spotify and starts playback on "
                "the active device. Returns a short confirmation of what is "
                "playing."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "query": types.Schema(
                        type="STRING",
                        description="What to play, e.g. 'chill playlist', "
                                    "'lofi album', 'Somebody to Love'.",
                    ),
                    "type": types.Schema(
                        type="STRING",
                        description="Optional hint: playlist | album | artist | "
                                    "track. Empty string lets it auto-detect.",
                    ),
                },
                required=["query"],
            ),
        ),
        types.FunctionDeclaration(
            name="hand_control",
            description=(
                "Start or stop the camera-based COMBINED hand control (\"the "
                "hands\"). When started, the right-hand pinch adjusts screen "
                "brightness, the left-hand pinch adjusts volume, and a "
                "skeleton overlay lets the user control windows: index "
                "fingertip moves the cursor, middle+thumb together clicks, "
                "index+thumb together grabs a window to resize/move/close it. "
                "Use 'start'/'on' when the user says 'the hands', 'start the "
                "hands', 'hand control on', 'window "
                "control on'; use 'stop'/'off' when they say 'enough', 'stop "
                "the hands', 'hand control off', 'window control off'. "
                "Returns a short confirmation."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "command": types.Schema(
                        type="STRING",
                        description="start | on  OR  stop | off",
                    ),
                },
                required=["command"],
            ),
        ),
        types.FunctionDeclaration(
            name="describe_screen",
            description=(
                "Look at the user's screen right now (take a screenshot and "
                "read it with vision). Use whenever the user asks what is on "
                "the screen, asks to explain what they are looking at, or "
                "mentions something visible on the computer (e.g. 'what's on "
                "screen?'). Returns a "
                "description of what's visible."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "question": types.Schema(
                        type="STRING",
                        description="Optional user question about the screen.",
                    ),
},
            ),
        ),
        types.FunctionDeclaration(
            name="security_scan",
            description=(
                "Scan the computer for viruses/malware and optionally clean "
                "them. Use this when the user asks to scan for viruses, check "
                "their antivirus, or remove detected threats. action 'scan' "
                "scans the user's Downloads/Desktop/Temp using the platform antivirus "
                "engine (Windows Defender / Linux clamscan, if installed) "
                "plus Gandalf's own heuristics and lists any suspicious files. "
                "action 'status' reports whether the antivirus is on. action "
                "'stop' cancels. action 'clean:*' removes threats, but THIS "
                "ONLY EVER runs after the user has explicitly confirmed; it "
                "moves files to the Recycle Bin and never touches protected "
                "system folders."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "action": types.Schema(
                        type="STRING",
                        description="scan | scan:<path> | status | stop | "
                                    "whitelist:<path> | clean",
                    ),
                    "confirm": types.Schema(
                        type="STRING",
                        description="Set to 'yes' ONLY if the user explicitly "
                                    "approved removal. Leave empty otherwise; "
                                    "Gandalf must always ask first.",
                    ),
                },
                required=["action"],
            ),
        ),
        types.FunctionDeclaration(
            name="computer_control",
            description=(
                "Control the computer's windows, check system status, run "
                "keyboard/mouse automation, or use the clipboard/OCR. action "
                "examples: window_list, window_focus:<title>, "
                "window_minimize/maximize/restore/close:<title>, "
                "window_move:<title>:<x>,<y>, window_resize:<title>:<w>,<h>, "
                "system_status, cpu, ram, disk, battery, network, uptime, "
                "screenshot, type_text:<text>, hotkey:<combo>, press_key:<key>, "
                "mouse_click:<left|right>, mouse_move_to:<x>,<y>, scroll:<amt>, "
                "task_delay:<sec>:<cmd>, clipboard_get, clipboard_set:<text>, "
                "ocr. Returns a short confirmation/user-readable result."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "action": types.Schema(
                        type="STRING",
                        description="One of the computer-control actions above.",
                    ),
                },
                required=["action"],
            ),
        ),
        types.FunctionDeclaration(
            name="homework",
            description=(
                "Manage the user's homework/assignment list. "
                "action 'list' shows all entries with their number, subject, "
                "pages, due date and done status. action 'add' adds a new "
                "homework (use ders/sayfa/teslim/tanim). action 'done' marks "
                "a homework as completed by its number. action 'delete' "
                "removes it by number. action 'update' changes fields of an "
                "entry by number. Use whenever the user says 'show my "
                "homework', 'add homework', 'list my homework', 'mark that "
                "homework done', 'delete that homework', 'add 25-30 pages of "
                "math homework' etc."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "action": types.Schema(
                        type="STRING",
                        description="list | add | done | delete | update.",
                    ),
                    "ders": types.Schema(
                        type="STRING",
                        description="Subject (ders) for add/update, e.g. "
                                    "'Matematik'.",
                    ),
                    "sayfa": types.Schema(
                        type="STRING",
                        description="Page range (sayfa) for add/update, e.g. "
                                    "'25-30'.",
                    ),
                    "teslim": types.Schema(
                        type="STRING",
                        description="Due date (teslim) for add/update, e.g. "
                                    "'yarın' or '20 Eylül'.",
                    ),
                    "tanim": types.Schema(
                        type="STRING",
                        description="Extra description (tanim) for add/update.",
                    ),
                    "no": types.Schema(
                        type="STRING",
                        description="Entry number (1-based) for "
                                    "done/delete/update.",
                    ),
                    "alan": types.Schema(
                        type="STRING",
                        description="Field name for update: "
                                    "ders | sayfa | tanim | teslim.",
                    ),
                    "deger": types.Schema(
                        type="STRING",
                        description="New value for update.",
                    ),
                },
                required=["action"],
            ),
        ),
    ])


_CFG_TOOLS = None
_CFG_NOTOOLS = None


def _config(use_tools: bool = True):
    """Return a GenerateContentConfig, reusing cached instances.

    Building the 8 function declarations + schemas on every call is pointless
    (a mutable-free template), and rebuilding a fresh config for each request
    adds a little overhead. The config is treated as read-only by
    generate_content, so a single shared instance per (tools yes/no) is safe.
    """
    global _CFG_TOOLS, _CFG_NOTOOLS
    if use_tools:
        if _CFG_TOOLS is None:
            _CFG_TOOLS = types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                tools=[_function_declarations()],
                temperature=0.6,
            )
        return _CFG_TOOLS
    else:
        if _CFG_NOTOOLS is None:
            _CFG_NOTOOLS = types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                tools=[],
                temperature=0.6,
            )
        return _CFG_NOTOOLS


# --------------------------------------------------------------------------
# The three Python functions behind Gemini's tool calls
# --------------------------------------------------------------------------
def open_application(app_name: str) -> str:
    try:
        if os.name != "nt":
            try:
                from actions.open_app import _open_on_linux
                if _open_on_linux(app_name):
                    return f"Opened application '{app_name}'."
                return f"Could not open '{app_name}': no launcher entry found."
            except Exception as e:
                return f"Could not open '{app_name}': {e}"
        import pyautogui
        pyautogui.FAILSAFE = False  # corner-bound mouse must not abort automation
        pyautogui.PAUSE = 0.1
        pyautogui.press("win")
        time.sleep(0.3)
        pyautogui.write(app_name, interval=0.03)
        time.sleep(0.2)
        pyautogui.press("enter")
        time.sleep(0.6)
        return f"Opened application '{app_name}'."
    except Exception as e:
        return f"Could not open '{app_name}': {e}"


def take_screenshot() -> str:
    """Capture the screen; returns the path of the saved PNG."""
    try:
        shots_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
        os.makedirs(shots_dir, exist_ok=True)
        path = os.path.join(shots_dir, f"gandalf_{int(time.time())}.png")
        from actions.system_diagnose import _take_screenshot
        res = _take_screenshot(path)
        if isinstance(res, str) and not res.startswith("Screenshot failed"):
            return res
        import pyautogui
        pyautogui.FAILSAFE = False
        pyautogui.screenshot().save(path)
        return path
    except Exception as e:
        return f"Screenshot failed: {e}"


def run_system_command(cmd: str) -> str:
    try:
        out = subprocess.run(cmd, shell=True, capture_output=True, timeout=30, text=True)
        text = (out.stdout or out.stderr or "").strip()
        if not text:
            return "Command finished (no output)."
        return text if len(text) <= 2000 else text[:2000]
    except Exception as e:
        return f"Command error: {e}"


def get_weather(city: str) -> str:
    """Live weather for a city (Open-Meteo). Returns structured JSON for Gemini
    to interpret into a Turkish Gandalf-style answer with go/No-go advice."""
    try:
        from actions.live_weather import get_live_weather_data
        data = get_live_weather_data(city)
        return json.dumps(data, ensure_ascii=False)
    except Exception as e:
        return f"ERROR: {e}"


def spotify_play(query: str, type: str = "") -> str:
    """Play a playlist/album/artist/track on Spotify via the local action."""
    try:
        from actions.spotify import spotify_action
        return spotify_action({"action": "search_and_play", "query": query,
                               "type": type or ""})
    except Exception as e:
        return f"ERROR: {e}"


def hand_control(command: str) -> str:
    """Start/stop the combined \"the hands\" app (brightness/volume + window
    control in ONE process, since the camera cannot be shared by two)."""
    try:
        from actions.hand_control import (
            start_hand_control, stop_hand_control, is_hand_control_running,
        )
        cmd = (command or "").strip().lower()
        if cmd in ("stop", "off", "enough", "kapat", "durdur"):
            if not is_hand_control_running():
                return "The hand control is not running."
            stop_hand_control()
            return "The hand control has been stopped."
        else:
            if is_hand_control_running():
                return "The hand control is already running."
            result = start_hand_control()
            if result == "started":
                return ("The hands are on. Right-hand pinch adjusts brightness, "
                        "left-hand pinch adjusts volume, and the skeleton "
                        "overlay lets you move, resize or close windows.")
            return "Could not start the hand control."
    except Exception as e:
        return f"ERROR: {e}"


def security_scan(action: str, confirm: str = "") -> str:
    """Scan / status / clean for threats.

    ``action`` is e.g. "scan", "scan:/home/user/Downloads", "status", "stop",
    "whitelist:<path>", or "clean". Removal ("clean") only happens when the
    user has explicitly confirmed (``confirm`` = yes/evet/remove...) and even
    then only sends files to the Recycle Bin, never touching protected/system
    folders. Scan results are listed for the user to approve before anything
    is removed.
    """
    try:
        from actions.security import run_security
        params = {"action": action}
        if (confirm or "").strip().lower() in ("yes", "evet", "onay", "onaylıyorum",
                                               "temizle", "sil", "remove", "do it"):
            params["confirm"] = "yes"
        return run_security(params, response=None, player=None, session_memory=None)
    except Exception as e:
        return f"Security scan error: {e}"


def computer_control(action: str) -> str:
    """Control the computer: windows (list/focus/minimize/maximize/restore/
    close/move/resize), diagnostics (system_status, cpu, ram, disk, battery,
    network, uptime, screenshot), automation (type_text, hotkey, press_key,
    mouse_click, mouse_move_to, scroll, task_delay) and clipboard/OCR
    (clipboard_get/set, ocr). Delegate to the local computer-control router.
    """
    try:
        from actions.computer import run_computer_control
        return run_computer_control({"action": action}, response=None,
                                    player=None, session_memory=None)
    except Exception as e:
        return f"Computer control error: {e}"


def _dispatch(name: str, args: dict) -> str:
    if name == "open_application":
        return open_application(str(args.get("app_name", "")))
    if name == "take_screenshot":
        return take_screenshot()
    if name == "get_weather":
        return get_weather(str(args.get("city", "")))
    if name == "run_system_command":
        return run_system_command(str(args.get("cmd", "")))
    if name == "spotify_play":
        return spotify_play(str(args.get("query", "")), str(args.get("type", "") or ""))
    if name == "hand_control":
        return hand_control(str(args.get("command", "")))
    if name == "describe_screen":
        from actions.screen_vision import describe_screen
        return describe_screen(str(args.get("question", "") or ""))
    if name == "security_scan":
        return security_scan(str(args.get("action", "")),
                             str(args.get("confirm", "") or ""))
    if name == "computer_control":
        return computer_control(str(args.get("action", "")))
    if name == "homework":
        from actions.homework import homework_action
        return homework_action(
            action=str(args.get("action", "list")),
            ders=str(args.get("ders", "") or ""),
            sayfa=str(args.get("sayfa", "") or ""),
            teslim=str(args.get("teslim", "") or ""),
            tanim=str(args.get("tanim", "") or ""),
            no=args.get("no"),
            alan=str(args.get("alan", "") or ""),
            deger=str(args.get("deger", "") or ""),
        )
    return f"Unknown function: {name}"


# --------------------------------------------------------------------------
# Public API used by telegram_bot
# --------------------------------------------------------------------------
def _retryable(err: Exception) -> bool:
    """True if the error is a temporary/remote issue worth a model fallback."""
    msg = str(err)
    if any(c in msg for c in ("429", "503", "404", "500", "RESOURCE_EXHAUSTED",
                              "UNAVAILABLE", "NOT_FOUND", "HTTPError")):
        return True
    return False


def _generate(client, contents, cfg):
    """Try each model in MODELS until one answers. Returns (resp, model_name)
    or (None, [errors]) when every model fails."""
    errors = []
    for m in MODELS:
        try:
            return client.models.generate_content(model=m, contents=contents,
                                                  config=cfg), m
        except Exception as e:  # noqa: BLE001
            errors.append(f"{m}: {str(e)[:120]}")
            if not _retryable(e):
                break
    return None, errors


def _get_client():
    """Return a lazily-created, reused genai client.

    Creating a fresh genai.Client on every call spins up a new transport /
    connection pool (and re-does TLS + endpoint setup), which added ~20s to
    the first request. Reusing one client keeps the connection warm.
    """
    global _client
    if _client is None:
        _client = genai.Client(api_key=API_KEY)
    return _client


def handle(contents, on_screenshot=None, use_tools: bool = True):
    """Send a list of genai Contents with tool support to Gemini, execute any
    function call, feed the result back, and return the final text reply.

    on_screenshot: callback(path) invoked after take_screenshot runs so the
    caller can post the PNG as a Telegram photo.
    """
    client = _get_client()
    cfg = _config(use_tools=use_tools)
    resp = None
    for _ in range(6):  # bounded tool-calling loop
        resp, err = _generate(client, contents, cfg)
        if resp is None:
            first = err[0] if err else "no models available"
            return ("Gemini API is temporarily unavailable (" +
                    first[:100] + "). Try again in a moment.")
        # collect function_call parts AS RETURNED (each Part carries its own
        # thought_signature that must be echoed back, else Gemini 3.x rejects it)
        fc_parts = []
        try:
            for part in resp.candidates[0].content.parts:
                if part.function_call is not None:
                    fc_parts.append(part)
        except Exception:
            fc_parts = []
        if not fc_parts:
            try:
                return resp.text
            except Exception:
                return ""
        for part in fc_parts:
            fc = part.function_call
            args = dict(fc.args or {})
            result = _dispatch(fc.name, args)
            if fc.name == "take_screenshot" and on_screenshot is not None:
                if result.startswith("Screenshot failed"):
                    pass  # keep the error message as the result
                else:
                    try:
                        on_screenshot(result)
                    except Exception:
                        pass
                    result = "Screenshot taken and sent to the user."
            contents.append(types.Content(role="model", parts=[part]))
            contents.append(types.Content(
                role="user",
                parts=[types.Part.from_function_response(
                    name=fc.name, response={"result": result}
                )],
            ))
    # Tool chain exceeded the loop budget. If Gemini still produced a partial
    # reply, return that instead of a canned "unfinished" line.
    try:
        if resp is not None:
            return resp.text
    except Exception:
        pass
    return "I have done what I can, but the task is not finished."


def handle_text(user_text: str, on_screenshot=None, use_tools: bool = True) -> str:
    if not GEMINI_OK:
        return ""
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=user_text)])]
    return handle(contents, on_screenshot, use_tools=use_tools)


def handle_audio(data: bytes, mime_type: str = "audio/ogg", on_screenshot=None) -> str:
    if not GEMINI_OK:
        return ""
    part = types.Part.from_bytes(data=data, mime_type=mime_type)
    contents = [types.Content(role="user", parts=[part])]
    return handle(contents, on_screenshot)


def handle_image(data: bytes, mime_type: str = "image/jpeg",
                 user_text: str = "", on_screenshot=None) -> str:
    """Comment on a user-sent photo/document (no function calling).

    If ``user_text`` (e.g. the Telegram caption) is given, Gemini answers that
    question about the image; otherwise it describes what it sees and offers to
    help. Raster images are normalized to RGB PNG and resized (max 1600px wide)
    to keep Gemini tokens low; PDFs pass through untouched.
    """
    if not GEMINI_OK:
        return ""
    mt = (mime_type or "").lower()
    parts_bytes = data
    if mt.startswith("image/"):
        try:
            from io import BytesIO
            from PIL import Image as PILImage
            im = PILImage.open(BytesIO(data))
            if im.width > 1600:
                ratio = 1600 / im.width
                im = im.resize((1600, max(1, int(im.height * ratio))),
                               PILImage.LANCZOS)
            if im.mode != "RGB":
                im = im.convert("RGB")
            buf = BytesIO()
            im.save(buf, format="PNG")
            parts_bytes, mt = buf.getvalue(), "image/png"
        except Exception:
            pass
    parts = [types.Part.from_bytes(data=parts_bytes, mime_type=mt)]
    q = (user_text or "").strip()
    if q:
        parts.append(types.Part.from_text(text=q))
    else:
        parts.append(types.Part.from_text(
            text=(
                "Look at this image/document. Explain in a short, clear "
                "way what it is; if it is sheet music, mention whether "
                "you can infer things like key/chords, if it looks like "
                "homework offer help, if it is an ordinary photo talk "
                "about what is in it and chat normally."
            )
        ))
    contents = [types.Content(role="user", parts=parts)]
    return handle(contents, on_screenshot, use_tools=False)


def handle_text_with_history(user_text: str, history=None, on_screenshot=None,
                             use_tools: bool = True) -> str:
    """Like handle_text but prepends the short-term conversation history so
    Gemini can keep context (e.g. "izmir?" after "antalya hava durumu").

    history: iterable of {"role": ("user"|"ai"), "text": str} in chronological
    order. role "ai" is mapped to Gemini's "model" role.
    """
    if not GEMINI_OK:
        return ""
    contents = []
    if history:
        for m in history:
            role = "user" if m.get("role") == "user" else "model"
            text = m.get("text", "")
            if not text:
                continue
            try:
                contents.append(
                    types.Content(role=role,
                                  parts=[types.Part.from_text(text=text)])
                )
            except Exception:
                pass
    contents.append(
        types.Content(role="user",
                      parts=[types.Part.from_text(text=user_text)])
    )
    return handle(contents, on_screenshot, use_tools=use_tools)