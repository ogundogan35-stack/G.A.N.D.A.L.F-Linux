"""Clipboard & screen analysis for Gandalf.

Clipboard read/write is platform aware:
  Windows -> ctypes clipboard API (no deps)
  Linux   -> wl-paste/wl-copy (Wayland) or xclip/xsel (X11), installed by setup.sh
Screen capture reuses system_diagnose. OCR uses whatever is available
(pytesseract if tesseract is installed); otherwise it gracefully explains.

Supported actions (parameters.action with parameters.value for clipboard_set):
    clipboard_get              read the current clipboard text
    clipboard_set:<text>       replace the clipboard with text
    screenshot[:path]          capture the screen (returns the PNG path)
    ocr[:path]                 read text from the screenshot / image via OCR
"""

import os
import shutil
import subprocess

from actions import system_diagnose


def _report(player, msg):
    try:
        player.write_log(f"GANDALF: {msg}")
    except Exception:
        pass
    return msg


def _clipboard_get() -> str:
    if os.name != "nt":
        # Wayland first, X11 tools as fallback.
        for cmd in (["wl-paste", "--no-newline"], ["xclip", "-selection",
                                                   "clipboard", "-o"],
                    ["xsel", "--clipboard", "--output"]):
            if shutil.which(cmd[0]):
                try:
                    r = subprocess.run(cmd, shell=False, capture_output=True,
                                       timeout=10)
                    if r.returncode == 0:
                        return (r.stdout or b"").decode("utf-8", "replace").strip()
                except Exception:
                    continue
        return ""
    return _win_clipboard_get()


def _win_clipboard_get() -> str:
    import ctypes
    from ctypes import wintypes
    CF_TEXTLOCAL = 1
    CF_UNICODETEXT = 13
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    if not user32.OpenClipboard(0):
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            # Older apps may store ANSI text; try CF_TEXT as a fallback.
            handle2 = user32.GetClipboardData(CF_TEXTLOCAL)
            if handle2:
                ptr = kernel32.GlobalLock(handle2)
                if ptr:
                    try:
                        return ctypes.string_at(ptr).decode("cp1252", "replace").rstrip("\x00")
                    finally:
                        kernel32.GlobalUnlock(handle2)
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr).rstrip("\x00")
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _clipboard_set(text: str) -> bool:
    if os.name != "nt":
        for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"],
                    ["xsel", "--clipboard", "--input"]):
            if shutil.which(cmd[0]):
                try:
                    r = subprocess.run(cmd, shell=False, input=(text or ""),
                                       capture_output=True, timeout=10)
                    return r.returncode == 0
                except Exception:
                    continue
        return False
    return _win_clipboard_set(text)


def _win_clipboard_set(text: str) -> bool:
    import ctypes
    from ctypes import wintypes
    CF_UNICODETEXT = 13
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
    kernel32.GlobalFree.restype = wintypes.HANDLE
    if not user32.OpenClipboard(0):
        return False
    try:
        user32.EmptyClipboard()
        data = (text or "").encode("utf-16-le") + b"\x00\x00"
        size = len(data)
        h = kernel32.GlobalAlloc(0x0042, size)  # GMEM_MOVEABLE | GMEM_ZEROINIT
        if not h:
            return False
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            kernel32.GlobalFree(h)
            return False
        try:
            ctypes.memmove(ptr, data, size)
        finally:
            kernel32.GlobalUnlock(h)
        # Note: after SetClipboardData the system owns h; do NOT GlobalFree(h).
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            return False
        return True
    finally:
        user32.CloseClipboard()


def _ocr(path: str) -> str:
    try:
        from PIL import Image
        import pytesseract
        try:
            return pytesseract.image_to_string(Image.open(path)).strip() or "(no text found)"
        except Exception:
            # tesseract binary missing/not on PATH -> fall through to vision OCR
            pass
    except ImportError:
        pass
    # Vision-based OCR: reuse the Gemini screen-reading pipeline already used
    # by /ekranoku and WhatsApp reading, so no local install is required.
    import re
    txt = _vision_ocr(path)
    if txt:
        return txt
    return ("OCR isn't available — I'd need tesseract installed "
            "(pip install pytesseract + the tesseract binary).")


def _vision_ocr(path: str) -> str:
    """Read the text in an image via Gemini vision (no local installs)."""
    try:
        from actions.screen_vision import _ask_gemini
        from PIL import Image
        image = Image.open(path)
        buf = _image_to_png_bytes(image)
        if not buf:
            return ""
        return _ask_gemini(
            "Write out all the text you can see in this image verbatim "
            "(OCR). Transcribe text even inside images/logos; if there is no "
            "text, reply '(no text)'.", buf)
    except Exception:
        return ""


def _image_to_png_bytes(image):
    import io
    buf = io.BytesIO()
    try:
        image.save(buf, format="PNG")
    except Exception:
        return None
    buf.seek(0)
    return buf.read()


def run_clipboard_screen(parameters, response=None, player=None, session_memory=None):
    action = str((parameters or {}).get("action", "")).strip()
    msg = ""
    try:
        if action == "clipboard_get":
            val = _clipboard_get()
            msg = f"Clipboard: {val}" if val else "The clipboard is empty."
        elif action.startswith("clipboard_set"):
            txt = action.split(":", 1)[1] if ":" in action else str((parameters or {}).get("value") or "")
            if _clipboard_set(txt):
                msg = "Clipboard updated, sir."
            else:
                msg = "Sir, I couldn't write to the clipboard."
        elif action.startswith("screenshot"):
            target = action.split(":", 1)[1] if ":" in action else None
            if target:
                target = os.path.abspath(target)
            out = system_diagnose._take_screenshot(target)
            msg = f"Screenshot saved to {out}" if isinstance(out, str) and out else \
                  "Sir, I couldn't take a screenshot."
        elif action.startswith("ocr"):
            path = action.split(":", 1)[1] if ":" in action else str((parameters or {}).get("path") or "")
            if not path:
                path = system_diagnose._take_screenshot()
                if not isinstance(path, str) or path.startswith("Screenshot failed"):
                    return _report(player, "Sir, I couldn't capture the screen for OCR.")
            msg = _ocr(path)
        else:
            msg = ("Sir, I can read or set the clipboard, take a screenshot, or read "
                   "text from the screen/images with OCR. What would you like?")
    except Exception as e:
        msg = f"Sir, that clipboard/screen action failed. ({e})"
    return _report(player, msg)
