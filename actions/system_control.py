"""Safe system-control actions for Gandalf (Windows + Linux).

Only a fixed allow-list of operations can be performed (no arbitrary shell
code). No extra third-party audio/brightness libraries are needed:

  Windows -> pyautogui / PowerShell / older NT APIs.
  Linux   -> pactl/amixer (volume), brightnessctl (brightness),
             systemctl/shutdown (power), loginctl (lock),
             nmcli (wifi), gsettings (wallpaper).

Supported action names (parameters.action):
    volume_up / volume_down / volume_mute / volume_set:<0-100>
    brightness:<0-100>
    lock                      (lock the screen)
    sleep / hibernate
    shutdown / restart        (60s delay so it can be cancelled with shutdown_abort)
    shutdown_abort
    wifi_on / wifi_off        (NetworkManager; may need admin for off)
    wallpaper:<path>
    run:<program>             (start an app by name/path)
    kill:<process>            (kill a process by name)
"""

import os
import shutil
import subprocess
import time
import ctypes

import pyautogui
pyautogui.FAILSAFE = False  # corner-bound mouse must not abort automation
from tts import edge_speak

# Apple-pie simplicity: bright/dim via keyboard volume keys (Windows only).
_VOL_KEYS = {
    "volume_up": "volumeup",
    "volume_down": "volumedown",
    "volume_mute": "volumemute",
}

# Friendly app name -> real Windows process (used by "close <app>" / kill).
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

# Same friendly names -> a Linux binary to match with pkill.
LINUX_PROCESS_MAP = {
    "chrome": "chrome",
    "opera gx": "opera",
    "opera": "opera",
    "minecraft": "java",
    "spotify": "spotify",
    "reaper": "reaper",
    "discord": "discord",
    "telegram": "telegram-desktop",
    "notepad": "gedit",
    "youtube": "chrome",
}


def _resolve_process(name: str) -> str:
    """Turn a friendly name or partial name into a process to kill."""
    name = name.strip().lower()
    if not name:
        return ""
    if os.name != "nt":
        app = LINUX_PROCESS_MAP.get(name, name)
        return app.strip().lstrip("/")
    app = APP_EXE_MAP.get(name, name)
    if "." not in app:  # ensure an .exe so taskkill works
        app = app + ".exe"
    return app


def _report(player, msg):
    try:
        player.write_log(f"GANDALF: {msg}")
    except Exception:
        pass
    return msg


def _speak(msg, player):
    try:
        edge_speak(msg, player)
    except Exception:
        pass


def _run_native(cmd, timeout=20, capture=True):
    """Run a native tool; returns (returncode, stdout-or-b''). Best effort."""
    try:
        if capture:
            r = subprocess.run(cmd, shell=False, capture_output=True,
                               timeout=timeout)
            return r.returncode, (r.stdout or b"").decode("utf-8", "ignore")
        subprocess.Popen(cmd, shell=False, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return 0, ""
    except Exception as e:
        print("SYSTEM CONTROL ERROR:", e)
        return -1, ""


def run_system_control(parameters, response=None, player=None, session_memory=None):
    action = str((parameters or {}).get("action", "")).strip().lower()
    msg = ""

    if not action:
        msg = "Sir, I need to know which system setting to change."
        return _report(player, msg)

    try:
        # ---- Windows-only shared keyboard volume keys ------------------
        if os.name == "nt" and action in _VOL_KEYS:
            pyautogui.press(_VOL_KEYS[action])
            msg = f"Done, sir — {action.replace('_', ' ')}."

        # ---- Volume (platform specific) --------------------------------
        elif action.startswith("volume_set"):
            try:
                vol = int(action.split(":")[1])
                if _set_system_volume(vol):
                    msg = f"System volume set to {vol} percent."
                else:
                    msg = "Sir, I couldn't set the volume level."
            except Exception:
                msg = "Sir, I couldn't set the volume level."
        elif os.name != "nt" and action in _VOL_KEYS:
            if action != "volume_mute":
                _linux_volume_relative("+5%" if action == "volume_up" else "-5%")
                msg = f"Done, sir — {action.replace('_', ' ')}."
            else:
                code, _ = _run_native(["pactl", "set-sink-mute",
                                       "@DEFAULT_SINK@", "toggle"])
                msg = ("Muted/unmuted, sir." if code == 0
                       else "Sir, I couldn't toggle the mute.")

        # ---- Brightness ------------------------------------------------
        elif action.startswith("brightness"):
            try:
                val = int(action.split(":")[1])
                val = max(0, min(100, val))
                if _set_brightness(val):
                    msg = f"Display brightness set to {val} percent."
                else:
                    msg = "Sir, I couldn't adjust the brightness."
            except Exception:
                msg = "Sir, I couldn't adjust the brightness."

        # ---- Power states ----------------------------------------------
        elif action == "lock":
            if os.name == "nt":
                ctypes.windll.user32.LockWorkStation()
            else:
                _linux_lock()
            msg = "Screen locked, sir."
        elif action in ("sleep", "hibernate"):
            if os.name == "nt":
                subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState",
                                  "0,1,0"], shell=False)
            else:
                target = "hibernate" if action == "hibernate" else "suspend"
                _run_native(["systemctl", target])
            msg = f"Putting the machine to {action}."
        elif action in ("shutdown", "restart"):
            if os.name == "nt":
                flags = {"shutdown": "/s", "restart": "/r"}[action]
                subprocess.Popen(["shutdown.exe", flags, "/t", "60",
                                  "/c", "Gandalf: user requested."], shell=False)
                msg = f"Machine will {action} in 60 seconds. Say 'cancel shutdown' to stop it."
            else:
                if action == "shutdown":
                    _run_native(["shutdown", "-P", "+1", "Gandalf: user requested."])
                else:
                    _run_native(["shutdown", "-r", "+1", "Gandalf: user requested."])
                msg = f"Machine will {action} in about a minute. Say 'cancel shutdown' to stop it."
        elif action == "shutdown_abort":
            if os.name == "nt":
                subprocess.Popen(["shutdown.exe", "/a"], shell=False)
            else:
                _run_native(["shutdown", "-c"])
            msg = "Shutdown / restart cancelled."

        # ---- Wi-Fi -----------------------------------------------------
        elif action in ("wifi_on", "wifi_off"):
            state = "on" if action == "wifi_on" else "off"
            if os.name == "nt":
                _run_native(["netsh", "interface", "set", "interface",
                             "Wi-Fi", "admin=" + ("enable" if state == "on"
                                                  else "disable")])
            else:
                _run_native(["nmcli", "radio", "wifi", state])
            msg = f"Wi-Fi turned {action.split('_')[1]}. (May need admin.)"

        # ---- Wallpaper ---------------------------------------------
        elif action.startswith("wallpaper:"):
            wall = action.split(":", 1)[1].strip()
            if _set_wallpaper(wall):
                msg = f"Wallpaper changed to {os.path.basename(wall)}."
            else:
                msg = "Sir, I couldn't change the wallpaper (file not found?)."

        # ---- Run / kill --------------------------------------------
        elif action.startswith("run:"):
            prog = action.split(":", 1)[1].strip()
            if prog:
                subprocess.Popen(prog, shell=True)
                msg = f"Started {prog}."
            else:
                msg = "Sir, which program should I run?"
        elif action.startswith("kill:"):
            proc = _resolve_process(action.split(":", 1)[1])
            if proc:
                if os.name == "nt":
                    _run_native(["taskkill", "/im", proc, "/f"])
                    msg = f"Closed {proc.replace('.exe', '')}."
                else:
                    # Match whole process names first (pkill), then argv names.
                    code, _ = _run_native(["pkill", "--", proc])
                    if code != 0:
                        code, _ = _run_native(["pkill", "-f", "--", proc])
                    msg = f"Closed {proc}." if code == 0 else \
                        f"Sir, I couldn't find a process named {proc}."

        else:
            msg = f"Sir, I don't support that system action: {action}"

    except Exception as e:
        msg = f"Sir, the system action failed. ({e})"

    _report(player, msg)
    _speak(msg, player)
    return msg


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

def _linux_volume_relative(delta):
    code, _ = _run_native(["pactl", "set-sink-volume", "@DEFAULT_SINK@", delta])
    if code != 0:
        _run_native(["amixer", "-q", "set", "Master", delta])


def _set_system_volume(percent):
    """Set master volume 0-100. Windows: key-based approximation. Linux: pactl."""
    percent = max(0, min(100, int(percent)))
    if os.name != "nt":
        code, _ = _run_native(["pactl", "set-sink-volume",
                               "@DEFAULT_SINK@", f"{percent}%"])
        if code == 0:
            return True
        return _run_native(["amixer", "-q", "set", "Master", f"{percent}%"])[0] == 0
    # Windows: ~5% per keypress step, from a neutral guess.
    import math
    steps = int(round(percent / 5.0))
    steps = max(0, min(15, steps))
    if percent < 50:
        pyautogui.press("volumedown", presses=steps)
    else:
        pyautogui.press("volumeup", presses=steps)
    return True


def _set_brightness(value):
    """Brightness 0-100. Linux: brightnessctl (or xrandr fallback)."""
    value = max(0, min(100, int(value)))
    if shutil.which("brightnessctl"):
        code, _ = _run_native(["brightnessctl", "-s", "set", f"{value}%"])
        if code == 0:
            return True
    # xrandr fallback (gamma-based approximation, needs X11).
    code, out = _run_native(["xrandr", "--query"])
    if code == 0:
        import re
        disp = re.search(r"([\w-]+) connected", out)
        if disp:
            ratio = max(0.05, value / 100.0)
            _run_native(["xrandr", "--output", disp.group(1),
                         "--brightness", f"{ratio:.2f}"])
            return True
    return False


def _linux_lock():
    for cmd in (["loginctl", "lock-session"],
                ["xdg-screensaver", "lock"],
                ["dm-tool", "lock"],
                ["gnome-screensaver-command", "-l"]):
        if shutil.which(cmd[0]):
            code, _ = _run_native(cmd)
            if code == 0:
                return


def _set_wallpaper(path):
    if not path or not os.path.isfile(path):
        return False
    abs_path = os.path.abspath(path)
    if os.name == "nt":
        SPI_SETDESKWALLPAPER = 20
        SPIF_UPDATEINIFILE = 0x2
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_SETDESKWALLPAPER, 0, abs_path, SPIF_UPDATEINIFILE)
        return True
    xdg = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    # XFCE (Pardus varsayılani): tum monitörlerdeki backdrop tum çalışma
    # alanları için `last-image` özelliklerini güncelle.
    if "xfce" in xdg or shutil.which("xfconf-query"):
        code, out = _run_native(["xfconf-query", "-c", "xfce4-desktop", "-l"])
        if code == 0:
            hits = [l.strip() for l in out.splitlines()
                    if l.strip().endswith("last-image")]
            if hits:
                ok = True
                for prop in hits:
                    c, _ = _run_native(["xfconf-query", "-c", "xfce4-desktop",
                                        "-p", prop, "-s", abs_path])
                    ok = ok and c == 0
                if ok:
                    return True
    # GNOME / Cinnamon
    schema = ("org.cinnamon.desktop.background"
              if "cinnamon" in xdg else "org.gnome.desktop.background")
    code, _ = _run_native(["gsettings", "set", schema, "picture-uri",
                           "file://" + abs_path])
    if code == 0:
        _run_native(["gsettings", "set", schema, "picture-uri-dark",
                     "file://" + abs_path])
        return True
    # KDE
    if shutil.which("plasma-apply-wallpaperimage"):
        return _run_native(["plasma-apply-wallpaperimage", abs_path])[0] == 0
    return False