"""Telegram remote-control bot for Gandalf.

Usage:
    from telegram_bot import TelegramController
    bridge = TelegramController(ui, enabled=True)
    bridge.start()
    ...
    bridge.stop()

Messages sent to the bot are handled in one of two ways:

* **Gemini path** (default when ``GEMINI_API_KEY`` is set): text and voice
  notes go straight to Google Gemini (``gemini_handler``). Gemini replies with
  the Gandalf persona and, when computer control is needed, calls functions
  (open_application / take_screenshot / run_system_command).
* **Local path** (fallback): messages are pushed into the same
  ``ui.text_input_queue`` the rest of Gandalf reads.

Gandalf's replies are forwarded back to Telegram via ``send_message_to_bot``.
"""

import os
import time
import asyncio
import threading
import queue

import runtime_state
import gemini_handler
import screen_utils

from actions.hand_control import (
    start_hand_control, stop_hand_control, is_hand_control_running,
)

try:
    from telegram import Update
    from telegram.ext import Application, MessageHandler, CommandHandler, filters
    PTB_OK = True
except Exception:
    PTB_OK = False


def _load_token():
    # Prefer .env next to this module, fall back to environment variable.
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except Exception:
        pass
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def _load_chat_id():
    """Optional TELEGRAM_CHAT_ID from .env. When set, ONLY messages coming
    from that chat are accepted (security). Empty string => open to anyone."""
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except Exception:
        pass
    raw = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


class TelegramController:
    """Bridges Telegram <-> the local Gandalf chat loop."""

    def __init__(self, ui=None, permitted_ids=None, enabled=True, chat_id=None,
                 session_memory=None):
        self.ui = ui
        self.session_memory = session_memory
        self.token = _load_token()
        # permitted_ids: set of ints. If empty/None, everyone can control.
        self.permitted = set(permitted_ids or [])
        # Only this Telegram chat may command the bot when set. .env wins.
        self.allowed_chat = chat_id if chat_id is not None else _load_chat_id()
        self.enabled = enabled and bool(self.token)
        self._out = queue.Queue()
        self._app = None
        self._thread = None
        self._running = threading.Event()

    def start(self):
        if not self.enabled or not PTB_OK:
            return False

        self._wrap_ui_log()

        self._running.set()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

        # Forwarder: pulls Gandalf replies off the queue and sends to Telegram.
        threading.Thread(target=self._forwarder, daemon=True).start()
        return True

    def _wrap_ui_log(self):
        """Proxy ui.write_log so every Gandalf output (chat + actions) also
        gets forwarded to Telegram without touching the UI code."""
        if not self.ui or getattr(self, "_wrapped", False):
            return
        ui = self.ui
        original = ui.write_log

        def proxy(text, *args, **kwargs):
            original(text, *args, **kwargs)
            if text and text.startswith(("AI: ", "Gandalf: ")):
                self.send_reply(text)

        ui.write_log = proxy
        self._wrapped = True

    def stop(self):
        self._running.clear()
        if self._app:
            try:
                self._app.stop()
            except Exception:
                pass

    # -- send a reply from Gandalf back to the user ---------------------
    def send_reply(self, text: str):
        self._out.put(text)

    def _forwarder(self):
        while self._running.is_set():
            try:
                msg = self._out.get(timeout=0.5)
            except queue.Empty:
                continue
            self._post(msg)

    # -- actual Telegram transport --------------------------------------
    def _run(self):
        try:
            app = Application.builder().token(self.token).build()
            self._app = app
            app.add_handler(CommandHandler("start", self._cmd_start))
            app.add_handler(CommandHandler("wake", self._cmd_start))
            app.add_handler(CommandHandler("stop", self._cmd_stop))
            app.add_handler(CommandHandler("sleep", self._cmd_stop))
            app.add_handler(CommandHandler("ekran", self._cmd_ekran))
            app.add_handler(CommandHandler("screen", self._cmd_ekran))
            app.add_handler(CommandHandler("pencere", self._cmd_pencere))
            app.add_handler(CommandHandler("windowgesture", self._cmd_pencere))
            app.add_handler(CommandHandler("tarama", self._cmd_tarama))
            app.add_handler(CommandHandler("scan", self._cmd_tarama))
            app.add_handler(CommandHandler("ekranoku", self._cmd_ekranoku))
            app.add_handler(CommandHandler("screenread", self._cmd_ekranoku))
            app.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text)
            )
            app.add_handler(MessageHandler(filters.VOICE, self._on_voice))
            app.add_handler(MessageHandler(filters.PHOTO, self._on_photo))
            app.add_handler(MessageHandler(filters.Document.ALL, self._on_document))
            app.run_polling(allowed_updates=Update.ALL_TYPES)
        except Exception as e:
            print(f"[Telegram] error: {e}")

    def _authorized(self, update: Update) -> bool:
        chat = update.effective_chat
        # CHAT_ID restriction takes priority when configured.
        if self.allowed_chat is not None:
            return chat is not None and chat.id == self.allowed_chat
        if not self.permitted:
            return True
        user = update.effective_user
        return user is not None and user.id in self.permitted

    async def _cmd_start(self, update: Update, context):
        if not self._authorized(update):
            await update.message.reply_text("Not authorized.")
            return
        runtime_state.set_active(True)
        await update.message.reply_text(
            "Gandalf is active and ready for commands.\n"
            "Send any command (e.g. 'open chrome', 'weather in antalya', "
            "'search for news')."
        )

    async def _cmd_stop(self, update: Update, context):
        if not self._authorized(update):
            await update.message.reply_text("Not authorized.")
            return
        runtime_state.set_active(False)
        await update.message.reply_text(
            "Gandalf went into sleep mode. (Wake me with /start or /wake.)"
        )

    async def _cmd_ekran(self, update: Update, context):
        """/ekran [ac|kapat] — ekranı uzaktan kapat/aç."""
        if not self._authorized(update):
            await update.message.reply_text("Not authorized.")
            return
        arg = (context.args[0] if context.args else "").lower()
        mode = {
            "on": "on", "off": "off", "0": "off", "false": "off",
        }.get(arg)
        if mode is None:
            await update.message.reply_text(
                "Screen control.\n"
                "Usage: /screen on | /screen off"
            )
            return
        ok = screen_utils.set_monitor_power(mode == "on")
        reply = {
            "on": "Screen ON." if ok else "⚠ Could not turn the screen on.",
            "off": "Screen OFF. Apps keep running in the background, "
                   "the system did not go to sleep." if ok else "⚠ Could not turn the screen off.",
        }[mode]
        await update.message.reply_text(reply)

    async def _cmd_pencere(self, update: Update, context):
        """/pencere [ac|kapat|durum] — birleşik "the hands" (parlaklık/ses + pencere kontrolü)."""
        if not self._authorized(update):
            await update.message.reply_text("Not authorized.")
            return
        arg = (context.args[0] if context.args else "").lower()
        if arg in ("", "durum", "status", "statu"):
            running = is_hand_control_running()
            durum = "RUNNING" if running else "OFF"
            await update.message.reply_text(
                f"Hand control (brightness/volume + window): {durum}\n"
                "Usage: /window on | /window off"
            )
            return
        if arg in ("on", "1", "start"):
            res = start_hand_control()
            reply = {
                "started": "Hand control STARTED (combined).\n"
                           "RIGHT hand pinch = brightness, LEFT hand pinch = volume; "
                           "index tip = cursor, middle+thumb = click, "
                           "index+thumb = grab (edge: resize, "
                           "inside: move, shake down: close).",
                "already_running": "Hand control is already running.",
                "failed": "⚠ Could not start hand control.",
            }[res]
            await update.message.reply_text(reply)
            return
        if arg in ("off", "0", "stop"):
            res = stop_hand_control()
            reply = {
                "stopped": "Hand control STOPPED.",
                "not_running": "Hand control is not running.",
            }.get(res, "⚠ Stop failed.")
            await update.message.reply_text(reply)
            return
        await update.message.reply_text(
            "Usage: /window on | /window off | /window status"
        )

    async def _cmd_tarama(self, update: Update, context):
        """/tarama — Gandalf Güvenlik: virüs taraması / durum / temizleme.

        Kullanım (yetkili chat):
          /tarama          -> Downloads/Desktop/Temp tara, şüphelileri listele
          /tarama status   -> antivirüs durumu
          /tarama <yol>    -> belirli klasörü tara
          /tarama temizle  -> listelenen şüphelileri (Geri Dönüşüm Kutusu'na)
                              taşı — silme asla kalıcı değildir ve korumalı
                              sistem klasörlerine dokunmaz.
        """
        if not self._authorized(update):
            await update.message.reply_text("Not authorized.")
            return
        arg = " ".join(context.args).strip()
        if update.effective_chat is not None:
            self._chat_id = update.effective_chat.id
        await update.message.reply_text(
            "Security scan started... I will report the result." if not arg
            else "Processing command...")

        # Yükleyici: scan uzun sürebilir, bu yüzden polling'i bloklamadan ayrı
        # iş parçacığında çalıştır, sonucu chate gönder.
        def _work():
            from actions.security import run_security
            low = arg.lower()
            if low in ("status", "durum", "antivirus", "antivirüs"):
                params = {"action": "status"}
            elif low.startswith("temizle") or low.startswith("clean") or low == "yes":
                params = {"action": "clean:yes"}
            elif low:
                params = {"action": f"scan:{arg}"}
            else:
                params = {"action": "scan:"}
            try:
                text = run_security(params, response=None, player=None,
                                    session_memory=None) or "Operation completed."
            except Exception as e:
                text = f"Scan error: {e}"
            reply = text if len(text) <= 3000 else text[:3000]
            self._dispatch_async(self._send_message, self._chat_id, reply)
        threading.Thread(target=_work, daemon=True).start()

    async def _cmd_ekranoku(self, update: Update, context):
        """/ekranoku [soru] - ekranda olan/goruneni Gemini goru ile acikla."""
        if not self._authorized(update):
            await update.message.reply_text("Not authorized.")
            return
        if update.effective_chat is not None:
            self._chat_id = update.effective_chat.id
        arg = " ".join(context.args).strip()
        await update.message.reply_text("Looking at the screen...")

        def _work():
            import pyautogui
            pyautogui.FAILSAFE = False  # corner-bound mouse must not abort automation
            from actions.screen_vision import describe_screen, screenshot_bytes
            try:
                # Show the user what was seen
                png = screenshot_bytes()
                shot_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "screenshots")
                os.makedirs(shot_dir, exist_ok=True)
                path = os.path.join(shot_dir,
                                    f"screenread_{int(time.time())}.png")
                with open(path, "wb") as f:
                    f.write(png)
                self._dispatch_async(self._send_photo, update, path)
                reply = describe_screen(arg or "")
            except Exception as e:
                reply = f"Screen reading error: {e}"
            reply = reply or "There is nothing readable on the screen or no reply came."
            self._dispatch_async(self._send_message, self._chat_id, reply)
        threading.Thread(target=_work, daemon=True).start()

    async def _on_text(self, update: Update, context):
        if not self._authorized(update):
            await update.message.reply_text("Not authorized.")
            return
        user_msg = update.message.text
        if not user_msg or not self.ui:
            return

        # Sleep/wake gate: while asleep, drop the message WITHOUT touching the
        # heavy Vosk / AI pipeline. Just reply that we are asleep.
        if not runtime_state.is_active():
            await update.message.reply_text(
                "Gandalf is currently in sleep mode. Type /start to wake me up."
            )
            return

        # Remember which chat to send Gandalf's replies back to.
        if update.effective_chat is not None:
            self._chat_id = update.effective_chat.id

        # Gemini path: route TG text straight to Gemini (function calling on).
        # If Gemini is down / out of quota / throws, fall back to the local
        # OpenRouter pipeline so the user ALWAYS gets a reply (never silent).
        if getattr(gemini_handler, "GEMINI_OK", False):
            reply, shots = "", []
            # Short-term memory: append this message and pass recent history
            # so follow-ups (e.g. "izmir?" after "antalya hava durumu") keep
            # context.
            mem = getattr(self, "session_memory", None)
            history = []
            if mem is not None:
                try:
                    mem.set_last_user_text(user_msg)
                    history = mem.conversation_history
                except Exception:
                    history = []
            try:
                reply = await asyncio.to_thread(
                    gemini_handler.handle_text_with_history,
                    user_msg,
                    history,
                    on_screenshot=lambda p: shots.append(p),
                )
            except Exception as e:
                print(f"[Telegram] gemini error: {e}")
            if reply:
                # Graceful note when Gemini hit a temporary outage, instead of
                # pretending the request was understood normally.
                if reply.startswith("Gemini API is temporarily unavailable"):
                    reply = (
                        "..Gemini is temporarily busy (quota/load). Switching "
                        "to the text channel:\n\n" + reply
                    )
                try:
                    for p in shots:
                        self._dispatch_async(self._send_photo, update, p)
                except Exception:
                    pass
                if mem is not None:
                    try:
                        mem.set_last_ai_response(reply)
                    except Exception:
                        pass
                self._post(reply)
                if self.ui:
                    try:
                        self.ui.write_log(f"You: {user_msg}")
                        self.ui.write_log(reply)
                    except Exception:
                        pass
                return

            # Gemini returned nothing or crashed -> fall back to OpenRouter so
            # the user is never left hanging.
            self._send_via_openrouter(user_msg)
            return

        # Local path: push into the shared input queue so the ai_loop picks it up.
        try:
            self.ui.text_input_queue.put(user_msg)
        except Exception as e:
            await update.message.reply_text(f"Error injecting command: {e}")

    async def _on_voice(self, update: Update, context):
        if not self._authorized(update):
            await update.message.reply_text("Not authorized.")
            return
        if not runtime_state.is_active():
            await update.message.reply_text(
                "Gandalf is currently in sleep mode. Type /start to wake me up."
            )
            return
        if update.effective_chat is not None:
            self._chat_id = update.effective_chat.id
        if not getattr(gemini_handler, "GEMINI_OK", False):
            await update.message.reply_text(
                "GEMINI_API_KEY is required for voice support (add it to the .env file)."
            )
            return
        try:
            tg_file = await update.message.voice.get_file()
            data = bytes(await tg_file.download_as_bytearray())
        except Exception as e:
            await update.message.reply_text(f"Could not get voice message: {e}")
            return
        shots = []
        reply = ""
        try:
            reply = await asyncio.to_thread(
                gemini_handler.handle_audio,
                data,
                "audio/ogg",
                lambda p: shots.append(p),
            )
        except Exception as e:
            print(f"[Telegram] gemini voice error: {e}")
        try:
            for p in shots:
                await self._send_photo(update, p)
        except Exception:
            pass
        if reply:
            await self._send_message(self._chat_id, reply)
            if self.ui:
                try:
                    self.ui.write_log(reply)
                except Exception:
                    pass
        else:
            await self._send_message(
                self._chat_id,
                "Sorry, I could not process your voice message right now "
                "(Gemini is temporarily busy/unavailable). Please try again "
                "later or send it as text.",
            )

    async def _on_photo(self, update: Update, context):
        """User sent a photo -> send it to Gemini vision and answer about it.

        The caption (if any) is used as the user's question ("this is my
        homework, help me", "what key is this piece in?"); without a caption
        Gandalf just describes/offers to help. Works with belge (document)
        uploads too, via _on_document.
        """
        if not self._authorized(update):
            await update.message.reply_text("Not authorized.")
            return
        if not runtime_state.is_active():
            await update.message.reply_text(
                "Gandalf is currently in sleep mode. Type /start to wake me up."
            )
            return
        if update.effective_chat is not None:
            self._chat_id = update.effective_chat.id
        if not getattr(gemini_handler, "GEMINI_OK", False):
            await update.message.reply_text(
                "GEMINI_API_KEY is required for photo/document support "
                "(add it to the .env file)."
            )
            return
        caption = (update.message.caption or "").strip()
        try:
            # photo[-1] = the largest size Telegram provides.
            tg_file = await update.message.photo[-1].get_file()
            data = bytes(await tg_file.download_as_bytearray())
        except Exception as e:
            await update.message.reply_text(f"Could not get photo: {e}")
            return
        await update.message.reply_text("Looking at the photo...")

        def _work():
            try:
                reply = gemini_handler.handle_image(data, "image/jpeg", caption)
            except Exception as e:
                reply = f"Could not interpret the photo: {e}"
            reply = reply or ("Sorry, I could not interpret the photo right now "
                              "(Gemini is temporarily busy/unavailable). Please "
                              "try again later.")
            self._dispatch_async(self._send_message, self._chat_id, reply)
        threading.Thread(target=_work, daemon=True).start()

    async def _on_document(self, update: Update, context):
        """User sent a file (belge) -> forward it to Gemini vision/PDF handling
        and answer about it. The caption works like in _on_photo.
        """
        if not self._authorized(update):
            await update.message.reply_text("Not authorized.")
            return
        if not runtime_state.is_active():
            await update.message.reply_text(
                "Gandalf is currently in sleep mode. Type /start to wake me up."
            )
            return
        if update.effective_chat is not None:
            self._chat_id = update.effective_chat.id
        if not getattr(gemini_handler, "GEMINI_OK", False):
            await update.message.reply_text(
                "GEMINI_API_KEY is required for document support (add it to "
                "the .env file)."
            )
            return
        doc = update.message.document
        mime = (doc.mime_type or "").lower()
        # Only raster images and PDFs are understood by the vision model.
        if not (mime.startswith("image/") or mime == "application/pdf"):
            await update.message.reply_text(
                "I cannot view this file type right now (only photos / "
                "images / PDFs are supported)."
            )
            return
        caption = (update.message.caption or "").strip()
        try:
            tg_file = await doc.get_file()
            data = bytes(await tg_file.download_as_bytearray())
        except Exception as e:
            await update.message.reply_text(f"Could not get document: {e}")
            return
        await update.message.reply_text("Looking at the document...")

        def _work():
            try:
                reply = gemini_handler.handle_image(data, mime, caption)
            except Exception as e:
                reply = f"Could not interpret the document: {e}"
            reply = reply or ("Sorry, I could not interpret the document right "
                              "now (Gemini is temporarily busy/unavailable). "
                              "Please try again later.")
            self._dispatch_async(self._send_message, self._chat_id, reply)
        threading.Thread(target=_work, daemon=True).start()

    async def _send_message(self, chat_id, text: str):
        """Send a plain text message to a chat."""
        if self._app and chat_id:
            await self._app.bot.send_message(chat_id=chat_id, text=text)

    async def _send_photo(self, update: Update, path: str):
        """Send a PNG file (e.g. from take_screenshot) as a Telegram photo."""
        if not self._app or not path or not os.path.exists(path):
            return
        chat_id = getattr(self, "_chat_id", None) or update.effective_chat.id
        with open(path, "rb") as f:
            data = f.read()
        await self._app.bot.send_photo(chat_id=chat_id, photo=data)

    def _dispatch_async(self, coro_factory, *args):
        """Run an async coroutine from any context (thread or running loop)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(coro_factory(*args))
        else:
            asyncio.run(coro_factory(*args))

    def _post(self, text: str):
        """Send a Gandalf reply to the chat that last spoke (chat_id cache).

        Safe from any context: plain threads (_forwarder, sync handlers) fall
        back to asyncio.run, and async contexts schedule a task on the running
        loop. Never touches get_event_loop() (broken in py3.12+ for threads).
        """
        chat_id = getattr(self, "_chat_id", None)
        if chat_id is None:
            # No chat has spoken yet: fall back to the configured chat (the
            # TELEGRAM_CHAT_ID owner), so background pushes reach the user even
            # before the first /start.
            chat_id = getattr(self, "allowed_chat", None)
        if chat_id is None or not self._app:
            return
        try:
            self._dispatch_async(self._send_message, chat_id, text)
        except Exception as e:
            print(f"[Telegram] reply error: {e}")

    def _send_via_openrouter(self, user_msg: str):
        """Fallback when Gemini is unavailable: use the classic OpenRouter
        pipeline (llm.get_llm_output) and post the reply to the current chat.
        Runs in a worker thread so a slow remote call never blocks polling."""
        def _work():
            try:
                from llm import get_llm_output
                out = get_llm_output(user_msg)
                text = (out or {}).get("text") or ""
                if text:
                    self._post(text)
            except Exception as e:
                print(f"[Telegram] OpenRouter fallback error: {e}")
        threading.Thread(target=_work, daemon=True).start()


def make_bridge(ui, permitted_ids=None, enabled=True):
    b = TelegramController(ui=ui, permitted_ids=permitted_ids, enabled=enabled)
    return b
