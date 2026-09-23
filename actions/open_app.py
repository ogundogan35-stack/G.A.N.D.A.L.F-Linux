import os
import glob
import shutil
import subprocess
import time
import pyautogui
pyautogui.FAILSAFE = False  # corner-bound mouse must not abort automation
from tts import edge_speak

# Known app-name -> possible Linux binaries / URLs (best effort).
_ALIASES = {
    "chrome": ["google-chrome", "google-chrome-stable", "chromium",
               "chromium-browser", "brave-browser", "microsoft-edge"],
    "microsoft edge": ["microsoft-edge"],
    "firefox": ["firefox"],
    "notepad": ["gedit", "mousepad", "pluma", "kate", "nano"],
    "telegram": ["telegram-desktop"],
    "discord": ["discord"],
    "spotify": ["spotify"],
    "reaper": ["reaper"],
    "file explorer": ["nautilus", "nemo", "dolphin", "thunar", "pcmanfm"],
    "youtube": ["google-chrome", "chromium", "firefox"],
}
_WEBSITES = {
    "youtube": "https://youtube.com",
}


def _desktop_name(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line.lower().startswith("name=") and not line[5:].startswith("["):
                return line[5:].strip()
    return ""


def _open_on_linux(app_name: str) -> bool:
    """Best-effort Linux app launcher (no display server tricks required)."""
    low = app_name.lower()

    # Website-like requests open in the default browser.
    for key, url in _WEBSITES.items():
        if low == key:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return True

    # 1) Real binary on PATH (optionally via alias candidates).
    candidates = [app_name] + _ALIASES.get(low, [])
    for cand in candidates:
        exe = shutil.which(cand)
        if exe:
            subprocess.Popen([exe])
            return True

    # 2) A user may pass a full path.
    if os.path.sep in app_name and os.path.isfile(app_name):
        subprocess.Popen([app_name])
        return True

    # 3) Match an installed .desktop shortcut by Name.
    for base in (os.path.expanduser("~/.local/share/applications"),
                 "/usr/local/share/applications", "/usr/share/applications"):
        for df in glob.glob(os.path.join(base, "*.desktop")):
            try:
                if _desktop_name(df).lower() == low:
                    subprocess.Popen(
                        ["gtk-launch", os.path.basename(df)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True
            except Exception:
                continue
    return False


def open_app(
    parameters: dict,
    response: str | None = None,
    player=None,
    session_memory=None
) -> bool:
    """
    Opens an application using the platform launcher:
      Windows -> Windows start-menu search
      Linux   -> binary on PATH / .desktop entry / xdg-open

    parameters:
        - app_name (str)

    Memory behavior:
        - Uses ONLY session memory
        - No long-term memory writes
    """

    app_name = (parameters or {}).get("app_name", "").strip()

    if not app_name and session_memory:
        app_name = session_memory.get_last_opened_app() or ""

    if not app_name:
        msg = "Sir, I couldn't determine which application to open."
        if player:
            player.write_log(msg)
        edge_speak(msg, player)
        return False

    if response:
        if player:
            player.write_log(response)
        edge_speak(response, player)

    if os.name != "nt":
        try:
            if _open_on_linux(app_name):
                time.sleep(0.6)
                if session_memory:
                    session_memory.set_open_app(app_name)
                return True
        except Exception as e:
            msg = f"Sir, I failed to open {app_name}."
            if player:
                player.write_log(f"{msg} ({e})")
            edge_speak(msg, player)
            return False

    try:
        pyautogui.PAUSE = 0.1


        pyautogui.press("win")
        time.sleep(0.3)

        pyautogui.write(app_name, interval=0.03)
        time.sleep(0.2)

        pyautogui.press("enter")
        time.sleep(0.6)

        if session_memory:
            session_memory.set_open_app(app_name)

        return True

    except Exception as e:
        msg = f"Sir, I failed to open {app_name}."
        if player:
            player.write_log(f"{msg} ({e})")
        edge_speak(msg, player)
        return False