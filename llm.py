# llm.py

import os
import json
import requests
from dotenv import load_dotenv

# Load .env from multiple likely locations so the API key is always found:
#   1. beside this module (normal source run)
#   2. the frozen/exe temp dir (PyInstaller _MEIPASS)
#   3. the current working directory (portable run: place .env next to the exe)
# This fixes 401 / missing-key errors and lets the standalone .exe pick up a
# side-by-side .env file.
import sys as _sys

for _env_candidate in (
    os.path.join(os.path.dirname(__file__), ".env"),
    os.path.join(getattr(_sys, "_MEIPASS", ""), ".env"),
    os.path.join(os.getcwd(), ".env"),
):
    if _env_candidate and os.path.isfile(_env_candidate):
        load_dotenv(_env_candidate)
        break

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

MODEL = "openai/gpt-4o-mini"

PROMPT_PATH = "core/prompt.txt"


def _prompt_candidates() -> list:
    """Locations to look for the system prompt (source run / portable exe)."""
    candidates = []
    # 1. relative to current working directory (classic source run / beside exe)
    candidates.append(PROMPT_PATH)                       # core/prompt.txt
    candidates.append(os.path.join(os.getcwd(), "core", "prompt.txt"))
    candidates.append(os.path.join(os.getcwd(), "prompt.txt"))
    # 2. inside a frozen PyInstaller bundle (sys._MEIPASS) if prompt.txt bundled
    meipass = getattr(_sys, "_MEIPASS", "")
    if meipass:
        candidates.append(os.path.join(meipass, "core", "prompt.txt"))
        candidates.append(os.path.join(meipass, "prompt.txt"))
    # 3. beside this module
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "core", "prompt.txt"))
    return candidates


def load_system_prompt() -> str:
    for path in _prompt_candidates():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            continue
    print("WARNING: prompt.txt could not be loaded; using bare fallback.")
    return "You are Gandalf, a helpful AI assistant."


SYSTEM_PROMPT = load_system_prompt()


def safe_json_parse(text: str) -> dict | None:
    if not text:
        return None

    text = text.strip()

    if "```json" in text:
        try:
            start = text.index("```json") + 7
            end = text.index("```", start)
            text = text[start:end].strip()
        except:
            pass
    elif "```" in text:
        try:
            start = text.index("```") + 3
            end = text.index("```", start)
            text = text[start:end].strip()
        except:
            pass

    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        json_str = text[start:end]
        return json.loads(json_str)
    except Exception as e:
        print(f"WARNING: JSON parse error: {e}")
        print(f"The error text: {text[:200]}")
        return None


def get_llm_output_gemini_first(user_text: str, memory_block: dict = None) -> dict:
    """Local (computer) path brain: Gemini first, OpenRouter as fallback.

    The classic local flow (ui text + local microphone) previously went straight
    to OpenRouter, which answers facts unsure and triggers a Google search for
    things like \"how old is Jim Carrey\". Gemini answers those directly.

    We hand Gemini the SAME JSON intent contract that the local ai_loop expects
    (intent / parameters / needs_clarification / text / memory_update), so the
    whole command system (open_app, weather, search,
    system_control...) keeps working. If Gemini is down / out of quota / throws,
    we fall back to the original OpenRouter get_llm_output so the user is never
    left hanging.
    """
    try:
        import gemini_handler
        if getattr(gemini_handler, "GEMINI_OK", False):
            parsed = _gemini_to_intent(user_text, memory_block)
            if parsed:
                return parsed
    except Exception as e:
        print(f"[LLM] gemini-first error, using OpenRouter: {e}")
    return get_llm_output(user_text, memory_block)


# AIM: reuse the same structured intent contract as OpenRouter. This tells
# Gemini exactly how to classify commands so open_app / weather
# / search / system_control all still dispatch to their Python actions.
_INTENT_INSTRUCTION = """You are Gandalf, a personal AI assistant on a Windows/Linux computer.

Classify the user's request into ONE intent JSON. Return ONLY valid JSON, no
extra text. The JSON schema:

{
  "intent": "chat | open_app | search | weather_report | system_control | file_operations | spotify | hand_control | security | homework",
  "parameters": {},
  "needs_clarification": false,
  "text": "single short reply to show the user",
  "memory_update": null
}

Language rule: ALWAYS write the "text" field in ENGLISH, regardless of the
user's input language. For non-chat intents (homework, weather,
system_control, ...) the text is a short confirmation, always in English.

Rules:
- open_app -> parameters.app_name (e.g. \"open chrome\" -> app_name: chrome)
- weather_report -> parameters.city (e.g. "Antalya") and parameters.time ONLY
  when the user names a day: use the English value "today" or "tomorrow".
  If no day is mentioned, omit time (defaults to live/now). For weather do
  NOT use search.
- search -> parameters.query. Use only for live/unknown data the model cannot
  answer reliably (news, scores, prices, latest events). For normal chat and
  facts you know, use chat and answer directly.
- system_control -> parameters.action: volume_up, volume_set:40, brightness:70,
  lock, sleep, restart, shutdown, wifi_on, wifi_off, wallpaper:<path>, run:<name>,
  kill:<exe>; plus window management (window_list, window_focus/minimize/maximize/
  restore/close:<title>, window_move:<title>:<x>,<y>, window_resize:<title>:<w>,<h>),
  diagnostics (system_status, cpu, ram, disk, battery, network, system_info, uptime,
  screenshot[:path]), automation (type_text:<text>, hotkey:<combo>, press_key:<key>,
  mouse_click, mouse_move_to:<x>,<y>, scroll:<amt>, task_delay:<sec>:<cmd>), and
  clipboard/OCR (clipboard_get, clipboard_set:<text>, ocr[:path]).
- spotify -> parameters.action (search_and_play|play|pause|next|previous|volume|shuffle|repeat)
  and parameters.query (what to play, e.g. \"play my chill playlist\" -> query 'chill playlist').
  Show intent spotify for any spotify/music playback request.
- security -> parameters.action: scan[:<path>] to scan for viruses/threats,
  clean (remove reported threats AFTER user confirmation), status (is my
  antivirus on), stop (cancel a scan), whitelist:<path>. Removals always need
  user confirmation and always go to the Recycle Bin. Never claims deletion is
  permanent.
- hand_control -> parameters.command (start|stop). Start the camera-based
  COMBINED hand control (\"the hands\"): brightness with right-hand pinch,
  volume with left-hand pinch, AND the window-control overlay (index fingertip
  = cursor, middle+thumb = click, index+thumb = grab/resize/move/close
  windows). Use it for \"the hands\", \"start the hands\", \"hand control on\",
  \"window control on\"; STOP it on \"enough\", \"stop the hands\",
  \"hand control off\", \"window control off\". Infer command from the phrasing:
  a request to begin/activate -> start; to end/deactivate -> stop.
- homework -> the user's homework/assignment list. parameters.action: list |
  add | done | delete | update. parameters.ders/sayfa/teslim/tanim are used
  for add, parameters.no (number) for done/delete/update. The list is stored
  in odevler.json; when 'list' is requested, return the actual list.
- file_operations -> parameters.action (open|list|read|create_dir|delete|copy|move)
  and parameters.path (absolute path on the user's machine).
- LEARNING from corrections: when the user TEACHES or CORRECTS how Gandalf should
  understand something (e.g. "when I say XYZ I mean ABC", "I meant spotify, not
  search", "call it the weather app", or a playful recurring alias), put it in
  memory_update as {"corrections": {"what_the_user_said": "what_it_should_mean"}}
  so it is remembered for next time.
- LEARNING topic notes (AI priming): when the user shares a fact worth
  remembering about a person, project, place or recurring task, store it under a
  lowercase single-word topic key as
  memory_update = {"topics": {"<topic>": {"value": "short note"}}}
  e.g. user says "my sister Amy lives in Berlin" -> topics.amy value "lives in
  Berlin". Topics are read back on future requests about that topic, so keep each
  note short and factual.
- Missing required info -> needs_clarification true, text = one short question.
- Think like a helpful assistant: read what the user ACTUALLY said (including the
  day/time/context), don't fall back to a canned answer. Match the exact intent,
  and for "chat" answer the actual question/fact/wish concisely (1-2 sentences,
  warm Gandalf tone), never a generic placeholder. If a live detail is named
  (e.g. "tomorrow", a specific city), keep it in the right parameter so the
  action returns the right data.
- You DO have the ability to act; only classify the intent.
"""


def _gemini_to_intent(user_text: str, memory_block: dict = None):
    """Ask Gemini for the structured intent JSON of a computer-path command."""
    import gemini_handler
    memory_str = ""
    if memory_block:
        memory_str = "\n".join(f"{k}: {v}" for k, v in memory_block.items() if v)
    prompt = (
        _INTENT_INSTRUCTION + "\n\nKnown memory:\n" +
        (memory_str or "No memory available") +
        "\n\nUser request: \"" + str(user_text) + "\""
    )
    # IMPORTANT: request the intent JSON WITHOUT tool calling enabled, so Gemini
    # classifies the intent instead of invoking a tool (e.g. get_weather) during
    # the classification step.
    raw = gemini_handler.handle_text(prompt, use_tools=False)
    if not raw:
        return None
    # Gemini may wrap the JSON in ```json ... ``` fences. Pull out the object.
    text = raw.strip()
    if "```" in text:
        import re
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
        if m:
            text = m.group(1)
    try:
        start = text.index("{"); end = text.rindex("}")
        parsed = json.loads(text[start:end + 1])
    except Exception as e:
        print(f"[LLM] gemini intent parse failed ({e}); raw={raw[:120]!r}")
        return {
            "intent": "chat", "parameters": {},
            "needs_clarification": False, "text": raw, "memory_update": None,
        }
    intent = parsed.get("intent", "chat")
    if intent not in {"chat", "open_app", "search",
                      "weather_report", "system_control", "file_operations",
                      "spotify", "hand_control", "security", "homework"}:
        intent = "chat"
    text = parsed.get("text") or (raw if intent == "chat" else "")
    return {
        "intent": intent,
        "parameters": parsed.get("parameters", {}) or {},
        "needs_clarification": bool(parsed.get("needs_clarification", False)),
        "text": text,
        "memory_update": parsed.get("memory_update"),
    }


def get_llm_output(user_text: str, memory_block: dict = None) -> dict:

    if not user_text or not user_text.strip():
        return {
            "intent": "chat",
            "parameters": {},
            "needs_clarification": False,
            "text": "Sir, I didn't catch that.",
            "memory_update": None
        }

    if not OPENROUTER_API_KEY:
        print("ERROR: OPENROUTER_API_KEY not found!")
        return {
            "intent": "chat",
            "parameters": {},
            "text": "API key is missing, Sir.",
            "needs_clarification": False,
            "memory_update": None
        }

    # Memory'yi string'e çevir
    memory_str = ""
    if memory_block:
        memory_str = "\n".join(f"{k}: {v}" for k, v in memory_block.items())

    user_prompt = f"""User message: "{user_text}"

Known user memory:
{memory_str if memory_str else "No memory available"}"""

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.2,
        "max_tokens": 500
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost",
        "X-Title": "Gandalf-Assistant"
    }

    try:
        
        response = requests.post(
            OPENROUTER_URL,
            headers=headers,
            json=payload,
            timeout=30
        )

        if response.status_code != 200:
            print(f"ERROR: API Error: {response.text}")
            return {
                "intent": "chat",
                "parameters": {},
                "text": f"Sir, API error: {response.status_code}",
                "needs_clarification": False,
                "memory_update": None
            }

        data = response.json()
        content = data["choices"][0]["message"]["content"]

        # JSON parse et
        parsed = safe_json_parse(content)

        if parsed:
            return {
                "intent": parsed.get("intent", "chat"),
                "parameters": parsed.get("parameters", {}),
                "needs_clarification": parsed.get("needs_clarification", False),
                "text": parsed.get("text"),
                "memory_update": parsed.get("memory_update")
            }

        return {
            "intent": "chat",
            "parameters": {},
            "needs_clarification": False,
            "text": content,
            "memory_update": None
        }

    except requests.exceptions.Timeout:
        print("ERROR: API timeout!")
        return {
            "intent": "chat",
            "text": "Sir, the connection timed out.",
            "parameters": {},
            "needs_clarification": False,
            "memory_update": None
        }

    except Exception as e:
        print(f"ERROR: LLM ERROR: {e}")
        return {
            "intent": "chat",
            "text": "Sir, I encountered a system error.",
            "parameters": {},
            "needs_clarification": False,
            "memory_update": None
        }