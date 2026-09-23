import os
import queue
import customtkinter as ctk

# ---------------------------------------------------------------------------
# GANDALF THE WHITE theme (CustomTkinter)
# ---------------------------------------------------------------------------
#  Arkaplan              : koyu fume / siyah  #121212
#  Gandalf mesaj alani   : gece mavisi         #1A2332  + acik metin
#  Kullanici mesaj alani : runik altin / taba  #855A14  + acik metin
#  Buton / vurgu         : parlak altin        #EAB308
#  Font                  : Georgia 11pt
# ---------------------------------------------------------------------------
BG_COLOR = "#121212"
ASA_BG = "#1A2332"
ASA_FG = "#E2E8F0"
KUL_BG = "#855A14"
KUL_FG = "#FFFFFF"
ACCENT = "#EAB308"
CORNER = 12

FONT_FAMILY = "Georgia"
FONT_SIZE = 11


def _pick_font(root):
    return FONT_FAMILY


class GandalfUI:
    def __init__(self, face_path=None, size=(760, 760)):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.root = ctk.CTk()
        self.root.title("G.A.N.D.A.L.F")
        self.root.resizable(False, False)
        self.root.geometry("640x740")
        self.root.configure(fg_color=BG_COLOR)
        self._setup_window_icon()

        self.font_family = _pick_font(self.root)
        self.size = size
        self.speaking = False

        # --- Title -----------------------------------------------------
        self.title_label = ctk.CTkLabel(
            self.root, text="G.A.N.D.A.L.F",
            text_color=ASA_FG, font=(self.font_family, 18, "bold")
        )
        self.title_label.pack(pady=(16, 4))

        # --- Scrollable message area ------------------------------------
        self.chat = ctk.CTkScrollableFrame(
            self.root, fg_color=BG_COLOR, corner_radius=12
        )
        self.chat.pack(fill="both", expand=True, padx=14, pady=10)

        # --- Text input --------------------------------------------------
        self.text_input_queue = queue.Queue()
        self.input_box = ctk.CTkEntry(
            self.root, placeholder_text="Type a message...",
            font=(self.font_family, FONT_SIZE), corner_radius=12,
            fg_color=ASA_BG, text_color=ASA_FG,
            border_color=ACCENT,
        )
        self.input_box.pack(fill="x", padx=14, pady=(0, 14))
        self.input_box.bind("<Return>", self._send_text_input)

        self.root.protocol("WM_DELETE_WINDOW", lambda: os._exit(0))

    # ------------------------------------------------------------------
    # Window visibility (wake/sleep). Must run on the main UI thread, so
    # external threads schedule the call via root.after(0, ...).
    # ------------------------------------------------------------------
    def _setup_window_icon(self):
        """Set the window title-bar icon from gandalf.ico (portable).

        Looks next to the frozen exe (sys._MEIPASS) or the script folder.
        Windows uses iconbitmap(); Linux/macOS converts the .ico to a PNG via
        PIL and applies it with iconphoto() so the taskbar/launcher has an icon.
        """
        import sys
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        for cand in (os.path.join(base, "gandalf.ico"),
                     os.path.join(os.path.dirname(base), "gandalf.ico")):
            if cand and os.path.isfile(cand):
                try:
                    if os.name == "nt":
                        self.root.iconbitmap(cand)
                    else:
                        try:
                            from PIL import Image
                            import io
                            img = Image.open(cand)
                            png = io.BytesIO()
                            img.save(png, format="PNG")
                            png.seek(0)
                            import tkinter as tk
                            tk_img = tk.PhotoImage(data=png.read())
                            self.root.iconphoto(True, tk_img)
                        except Exception:
                            import tkinter as tk
                            try:
                                pm = tk.PhotoImage(file=cand)
                                self.root.iconphoto(True, pm)
                            except Exception:
                                pass
                except Exception:
                    pass
                return

    def hide_window(self):
        """Hide the window (sleep mode): it disappears from view/taskbar but
        the process keeps running in the background."""
        try:
            self.root.after(0, self._do_hide)
        except Exception:
            pass

    def _do_hide(self):
        try:
            self.root.withdraw()
        except Exception:
            pass

    def show_window(self):
        """Bring the window back (wake mode)."""
        try:
            self.root.after(0, self._do_show)
        except Exception:
            pass

    def _do_show(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _add_line(self, text: str, color: str, bg: str = None):
        """Append one plain text line to the chat area."""
        if bg is None:
            bg = BG_COLOR
        ctk.CTkLabel(
            self.chat, text=text, text_color=color, fg_color=bg,
            font=(self.font_family, FONT_SIZE), justify="left", anchor="w",
            wraplength=560, corner_radius=8,
        ).pack(fill="x", padx=6, pady=3, anchor="w")

        # Keep the newest message in view
        try:
            canvas = self.chat._parent_canvas
            canvas.yview_moveto(1.0)
        except Exception:
            pass
        self.chat.update_idletasks()

    def write_log(self, text: str):
        """Called by main loop / actions with a chat line.

        "You: ..." / "Sen: ..." -> user area (runic gold/tan), everything else
        -> Gandalf area (night blue). Shown as plain text lines (no bubbles).

        Thread-safe: tkinter widgets may only be touched from the main UI
        thread, but write_log is called from many threads (telegram, voice,
        action workers, the dashboard). Appends are scheduled on the main
        thread via root.after so order is preserved while calls never block.
        """
        def _work():
            s = text.strip()
            own = s.lower().startswith("you:") or s.lower().startswith("sen:")
            if own:
                s = "You:" + s.split(":", 1)[1]
                self._add_line(s, KUL_FG, KUL_BG)
            else:
                self._add_line(s, ASA_FG, ASA_BG)
        try:
            self.root.after(0, _work)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _send_text_input(self, event=None):
        text = self.input_box.get().strip()
        if text:
            self.text_input_queue.put(text)
            self.input_box.delete(0, "end")
        return "break"

    def get_text_input(self):
        try:
            return self.text_input_queue.get_nowait()
        except queue.Empty:
            return None

    # ------------------------------------------------------------------
    def start_speaking(self):
        self.speaking = True

    def stop_speaking(self):
        self.speaking = False
