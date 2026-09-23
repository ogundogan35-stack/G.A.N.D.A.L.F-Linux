# =============================================================================
# GANDALF (LINUX) - SETUP / MOVING TO ANOTHER PC (READ BEFORE RUNNING)
# =============================================================================
# 1) Python 3.10+ (64-bit) must be installed:  sudo apt install python3
# 2) One-time setup (system libs + Python packages):  bash setup.sh
#    (installs python3-venv, portaudio19-dev, libportaudio2, xdotool, wmctrl,
#     brightnessctl, pulseaudio-utils, espeak-ng, essentials, then pip installs)
# 3) The Vosk English speech model is downloaded automatically on first run.
#    If there is no internet, place it yourself at:
#       ~/.cache/vosk/vosk-model-small-en-us-0.15
# 4) The .env file (project root) must contain these variables:
#       GEMINI_API_KEY=<your Gemini API key>
#       OPENROUTER_API_KEY=<your OpenRouter API key>
#       TELEGRAM_BOT_TOKEN=<your bot token from BotFather>
#       TELEGRAM_CHAT_ID=<your Telegram chat id>  (empty = open to everyone)
# 5) Voice control uses a microphone + the "Hey Gandalf" wake word.
# 6) Run it with:
#       bash run.sh
#    (start.sh uses the project .venv so no system Python pollution happens.)
# 7) Live HUD on your local network (e.g. from a tablet): http://<PC-IP>:5000
#    (Find your PC IP with "hostname -I" in a terminal.)
# =============================================================================
import asyncio
import os
import re
import threading

import runtime_state
from speech_to_text import stop_listening_flag, VoiceCommandListener
from llm import get_llm_output_gemini_first
from tts import edge_speak, stop_speaking
from ui import GandalfUI

from telegram_bot import TelegramController
import dashboard_server

from actions.open_app import open_app
from actions.web_search import web_search
from actions.weather_report import weather_action
from actions.computer import run_computer_control
from actions.spotify import spotify_action
from actions.hand_control import (
    start_hand_control, stop_hand_control, is_hand_control_running,
)
from actions.security import run_security
from actions.file_ops import run_file_ops

from memory.memory_manager import load_memory, update_memory, get_app_alias, set_app_alias, get_corrections, get_topics
from memory.temporary_memory import TemporaryMemory

interrupt_commands = ["mute", "stop"]


def _looks_like_search(user_text: str, response: str) -> bool:
    """Trigger a Google search when Gandalf (Gemini) admits he cannot answer.

    With Gemini-first active, Gemini itself decides live/unknown questions and
    returns the "search" intent (which web_search performs in ai_loop). So here
    we only fall back to a web search if the reply literally says it doesn't
    know — we no longer treat every "how / what / where / who" as a search, or
    Gemini's direct factual answers would get overridden and re-searched.
    """
    r = (response or "").lower()
    unsure = any(ph in r for ph in [
        "don't know", "do not know", "not sure", "i'm not sure", "i am not sure",
        "can't answer", "cannot answer", "not able to", "i don't have information",
    ])
    return unsure


temp_memory = TemporaryMemory()

# Global handle to the TelegramController so background threads can also push
# messages to the last-active Telegram chat.
telegram_controller = None


async def get_voice_input(ui: GandalfUI):
    # Use text input only for now due to microphone issues
    while True:
        text_input = ui.get_text_input()
        if text_input:
            return text_input
        await asyncio.sleep(0.1)


async def ai_loop(ui: GandalfUI):
    while True:
        stop_listening_flag.clear()

        # Sleep/wake gate: while Gandalf is asleep, DON'T touch the voice
        # engine, the AI, or any queued input (Vosk + LLM stay off).
        if not runtime_state.is_active():
            await asyncio.sleep(0.2)
            continue

        user_text = await get_voice_input(ui)

        if not user_text:
            continue

        # "close gandalf" / "bye" etc. -> say goodbye and shut the app down.
        low = user_text.lower()
        if any(ph in low for ph in (
                "close gandalf", "close the app", "shut down", "shutdown",
                "bye", "goodbye", "good bye", "quit", "exit")):
            ui.write_log(f"You: {user_text}")
            ui.write_log("GANDALF: Bye, sir.")
            def _shutdown():
                os._exit(0)
            threading.Timer(1.0, _shutdown).start()
            continue

        if any(cmd in user_text.lower() for cmd in interrupt_commands) \
                and "hand" not in user_text.lower():
            stop_speaking()
            temp_memory.reset()
            continue

        ui.write_log(f"You: {user_text}")


        if temp_memory.get_current_question():
            param = temp_memory.get_current_question()
            temp_memory.update_parameters({param: user_text})
            temp_memory.clear_current_question()
            user_text = temp_memory.get_last_user_text()

        # Beni-bekleyen istek (pending intent): kullanici soruya (orn. "temizleme
        # onay'i") cevap verdiyse LLM'i atlayip dogrudan bekleyen aksiyonu
        # calistir. Boylece "yes, remove them" dogrudan run_security(clean)
        # gider; aksi halde LLM onayi tekrar sorar ve temizleme hic gerceklesmez.
        if temp_memory.has_pending_intent():
            pending = temp_memory.pending_intent
            collected = temp_memory.get_parameters()
            temp_memory.clear_pending_intent()
            if pending == "security":
                threading.Thread(
                    target=run_security,
                    kwargs={
                        "parameters": collected,
                        "response": None,
                        "player": ui,
                        "session_memory": temp_memory
                    },
                    daemon=True
                ).start()
                continue
            elif pending == "file_operations":
                threading.Thread(
                    target=run_file_ops,
                    kwargs={
                        "parameters": collected,
                        "response": None,
                        "player": ui,
                        "session_memory": temp_memory
                    },
                    daemon=True
                ).start()
                continue

        temp_memory.set_last_user_text(user_text)

        long_term_memory = load_memory()

        def minimal_memory_for_prompt(memory: dict) -> dict:
            result = {}
            identity = memory.get("identity", {})
            preferences = memory.get("preferences", {})
            relationships = memory.get("relationships", {})
            emotional_state = memory.get("emotional_state", {})

            if "name" in identity:
                result["user_name"] = identity["name"].get("value")

            for k in ["favorite_color", "favorite_food", "favorite_music"]:
                if k in preferences:
                    val = preferences[k].get("value")
                    if isinstance(val, dict) and "value" in val:
                        val = val["value"]
                    result[k] = val

            for rel, info in relationships.items():
                if isinstance(info, dict) and "name" in info and "value" in info["name"]:
                    result[f"{rel}_name"] = info["name"]["value"]

            for event, info in emotional_state.items():
                if "value" in info:
                    result[f"emotion_{event}"] = info["value"]

            corrections = get_corrections()
            if corrections:
                result["learned_corrections"] = corrections

            # AI priming: bu mesajdaki konuyla eşleşen hafıza notlarını ekle.
            # Sadece ilgili (eşleşen) notlar prompt'a gider; gereksiz bilgi
            # prompt'u şişirmez.
            topics = get_topics()
            if topics and user_text:
                text_lower = user_text.lower()
                matched = {}
                for topic, info in topics.items():
                    key = str(topic).lower().strip()
                    if not key:
                        continue
                    note = info.get("value") if isinstance(info, dict) else info
                    if not note:
                        continue
                    if re.search(r"\b" + re.escape(key) + r"\b", text_lower):
                        matched[key] = note
                if matched:
                    result["relevant_notes"] = matched

            return {k: v for k, v in result.items() if v}

        memory_for_prompt = minimal_memory_for_prompt(long_term_memory)
        
        history_lines = temp_memory.get_history_for_prompt()
        recent_history = "\n".join(history_lines.split("\n")[-5:])
        if recent_history:
            memory_for_prompt["recent_conversation"] = recent_history

        if temp_memory.has_pending_intent():
            memory_for_prompt["_pending_intent"] = temp_memory.pending_intent
            memory_for_prompt["_collected_params"] = str(temp_memory.get_parameters())

        try:
            llm_output = get_llm_output_gemini_first(
                user_text=user_text,
                memory_block=memory_for_prompt
            )
        except Exception as e:
            # Sanitize error message to remove problematic characters
            error_msg = str(e).encode('ascii', 'ignore').decode('ascii')
            ui.write_log(f"AI ERROR: {error_msg}")
            continue

        intent = llm_output.get("intent", "chat")
        parameters = llm_output.get("parameters", {})
        response = llm_output.get("text")
        memory_update = llm_output.get("memory_update")

        if memory_update and isinstance(memory_update, dict):
            update_memory(memory_update)

            # Handle app aliases specifically
            if "app_aliases" in memory_update and isinstance(memory_update["app_aliases"], dict):
                for alias, real_app in memory_update["app_aliases"].items():
                    set_app_alias(alias, real_app)

        temp_memory.set_last_ai_response(response)

        if intent == "open_app":
            app_name = parameters.get("app_name")
            if app_name:
                # Check if there's an alias for this app name
                real_app_name = get_app_alias(app_name) or app_name
                parameters["app_name"] = real_app_name

                threading.Thread(
                    target=open_app,
                    kwargs={
                        "parameters": parameters,
                        "response": response,
                        "player": ui,
                        "session_memory": temp_memory
                    },
                    daemon=True
                ).start()

        elif intent == "weather_report":
            city = parameters.get("city")
            if city:
                threading.Thread(
                    target=weather_action,
                    kwargs={
                        "parameters": parameters,
                        "player": ui,
                        "session_memory": temp_memory
                    },
                    daemon=True
                ).start()

        elif intent == "search":
            query = parameters.get("query")
            if query:
                threading.Thread(
                    target=web_search,
                    kwargs={
                        "parameters": parameters,
                        "player": ui,
                        "session_memory": temp_memory
                    },
                    daemon=True
                ).start()

        elif intent == "system_control":
            threading.Thread(
                target=run_computer_control,
                kwargs={
                    "parameters": parameters,
                    "response": response,
                    "player": ui,
                    "session_memory": temp_memory
                },
                daemon=True
            ).start()

        elif intent == "file_operations":
            threading.Thread(
                target=run_file_ops,
                kwargs={
                    "parameters": parameters,
                    "response": response,
                    "player": ui,
                    "session_memory": temp_memory
                },
                daemon=True
            ).start()

        elif intent == "spotify":
            threading.Thread(
                target=spotify_action,
                kwargs={
                    "parameters": parameters,
                    "player": ui,
                    "session_memory": temp_memory
                },
                daemon=True
            ).start()

        elif intent == "hand_control":
            threading.Thread(
                target=_hand_control_thread,
                kwargs={"parameters": parameters, "player": ui},
                daemon=True
            ).start()

        elif intent == "security":
            threading.Thread(
                target=run_security,
                kwargs={
                    "parameters": parameters,
                    "response": response,
                    "player": ui,
                    "session_memory": temp_memory
                },
                daemon=True
            ).start()

        elif intent == "homework":
            threading.Thread(
                target=_homework_thread,
                kwargs={"parameters": parameters, "player": ui},
                daemon=True
            ).start()

        else:
            # If Gandalf couldn't answer or the user asked something live /
            # uncertain, fall back to a Google search -- but not for everything.
            if _looks_like_search(user_text, response):
                query = user_text.strip() or (response or "").strip()
                threading.Thread(
                    target=web_search,
                    kwargs={
                        "parameters": {"query": query},
                        "player": ui,
                        "session_memory": temp_memory
                    },
                    daemon=True
                ).start()
            elif response:
                ui.write_log(f"AI: {response}")
                edge_speak(response, ui)

        await asyncio.sleep(0.01)


def _hand_control_thread(parameters: dict, player):
    """'the hands' / 'enough' komutlarını işler (start|stop) ve yanıt verir.
    Birleşik uygulama: parlaklık/ses + pencere kontrolü birlikte açılır."""
    cmd = (parameters or {}).get("command", "").strip().lower()
    running = is_hand_control_running()

    if cmd in ("stop", "off", "enough", "kapat", "durdur"):
        result = stop_hand_control()
        if result == "not_running":
            msg = "Sir, the hand control is not running."
        else:
            msg = "Sir, the hand control has been stopped."
    else:  # start
        if running:
            msg = "Sir, the hand control is already running."
        else:
            result = start_hand_control()
            msg = (("Sir, the hands are on. Show your right hand to adjust "
                    "brightness, left hand for volume, and use the skeleton "
                    "overlay to move, resize or close windows.")
                   if result == "started" else
                   "Sir, I could not start the hand control.")
    if player:
        player.write_log(f"AI: {msg}")
    edge_speak(msg, player)


def _homework_thread(parameters: dict, player):
    """Ödev listesi komutlarını işler (list / add / done / delete / update)."""
    from actions.homework import homework_action
    params = dict(parameters or {})
    action = str(params.get("action", "list") or "list").strip().lower()
    try:
        result = homework_action(
            action=action,
            ders=str(params.get("ders", "") or ""),
            sayfa=str(params.get("sayfa", "") or ""),
            tanim=str(params.get("tanim", "") or ""),
            teslim=str(params.get("teslim", "") or ""),
            no=params.get("no"),
            alan=str(params.get("alan", "") or ""),
            deger=str(params.get("deger", "") or ""),
        )
    except Exception as e:
        result = f"Error during homework operation: {e}"
    if player:
        player.write_log(f"AI: {result}")
    try:
        edge_speak(result, player)
    except Exception:
        pass


def main():
    ui = GandalfUI(None, size=(600, 600))  # Larger size for better visibility

    # Warm up the Gemini client in the background so the FIRST voice/typed
    # command is not slowed by TLS/connection setup + model warm-up.
    def _warmup():
        try:
            import gemini_handler
            if getattr(gemini_handler, "GEMINI_OK", False):
                gemini_handler._get_client()
                gemini_handler.handle_text("hi", use_tools=False)
        except Exception:
            pass
    threading.Thread(target=_warmup, daemon=True).start()

    # Background voice listener: "Hey Gandalf" wakes it, then it pushes the
    # recognised command into the UI input queue (like typed text).
    voice = VoiceCommandListener(ui=ui)
    voice.start()

    # Remote control over Telegram (reads token from .env).
    global telegram_controller
    telegram_controller = TelegramController(ui=ui, enabled=True, session_memory=temp_memory)
    telegram = telegram_controller
    telegram.start()

    # Local-network dashboard (Flask) in a daemon thread. Wraps ui.write_log so
    # every user line and Gandalf reply also flows to the HUD.
    dashboard_server.install(ui=ui)

    # Wake/sleep -> show/hide the main window. While Gandalf is asleep the
    # window stays hidden (looks closed) but the process keeps running in the
    # background. Telegram /start or /wake brings it back.
    def _mirror_window_state(active: bool):
        if active:
            ui.show_window()
        else:
            ui.hide_window()
    runtime_state.on_change(_mirror_window_state)

    def runner():
        asyncio.run(ai_loop(ui))

    threading.Thread(target=runner, daemon=True).start()

    # Start hidden if Gandalf begins in sleep mode (is_active defaults False).
    if not runtime_state.is_active():
        ui.root.after(300, ui.hide_window)
    ui.root.mainloop()


if __name__ == "__main__":
    main()