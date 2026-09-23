"""Window & application management for Gandalf (Windows + Linux).

Windows -> pure ctypes (EnumWindows / SetWindowPos / ShowWindow /
           SetForegroundWindow / PostMessage).
Linux   -> wmctrl + xdotool (install via setup.sh). If they are missing the
           action reports a friendly "install wmctrl/xdotool" message and
           continues gracefully.

All operations are read-only unless the user explicitly asks to move/resize/close.

Supported actions (parameters.action with parameters spec):
    window_list                 list open top-level windows + their titles
    window_focus:<title>        bring a window to the foreground
    window_minimize:<title>     minimise a window
    window_maximize:<title>     maximise a window
    window_restore:<title>      restore a minimised/maximised window
    window_close:<title>        close a window (graceful WM_CLOSE)
    window_move:<title>:<x>,<y>        move to screen x,y
    window_resize:<title>:<w>,<h>      resize to w,h
Taken titles are matched case-insensitively by substring.
"""

import os
import shutil
import subprocess

_NEEDED_TOOLS = ("wmctrl", "xdotool")


def _is_protected_window(title: str) -> bool:
    """Never touch our own shell / critical windows."""
    title_l = (title or "").lower()
    if "gandalf" in title_l:
        return True
    return False


def _run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, shell=False, capture_output=True, timeout=timeout)
        return r.returncode, (r.stdout or b"").decode("utf-8", "ignore")
    except Exception as e:
        print("WINDOW CONTROL ERROR:", e)
        return -1, ""


# ---------------------------------------------------------------------------
# Linux backend (wmctrl + xdotool)
# ---------------------------------------------------------------------------

def _linux_tools_missing():
    return [t for t in _NEEDED_TOOLS if not shutil.which(t)]


def _linux_windows():
    """Return [{wid, title}] from `wmctrl -l`."""
    wins = []
    code, out = _run(["wmctrl", "-l"])
    if code != 0:
        return wins
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) >= 4:
            wins.append({"wid": parts[0], "title": parts[3]})
    return wins


def _linux_find(title):
    if not title:
        return None
    title_l = title.lower()
    for w in _linux_windows():
        if title_l in w["title"].lower():
            return w
    return None


def _linux_window_control(verb, title, geom):
    """Handle one Linux action. Returns a message string."""
    missing = _linux_tools_missing()
    if missing:
        return ("Sir, the window tools are not installed. Run setup.sh to "
                f"install: {', '.join(missing)}")

    win = _linux_find(title)
    if win is None:
        return f"Sir, I couldn't find a window matching '{title}'."

    wid = win["wid"]

    if verb == "window_focus":
        _run(["wmctrl", "-ia", wid])
        return f"Focused '{title}'."
    if verb == "window_minimize":
        _run(["xdotool", "windowminimize", wid])
        return f"Minimised '{title}'."
    if verb == "window_maximize":
        _run(["wmctrl", "-i", "-r", wid, "-b", "add,maximized_vert,maximized_horz"])
        return f"Maximised '{title}'."
    if verb == "window_restore":
        _run(["wmctrl", "-i", "-r", wid, "-b", "remove,maximized_vert,maximized_horz"])
        _run(["xdotool", "windowactivate", wid])
        return f"Restored '{title}'."
    if verb == "window_close":
        if _is_protected_window(title):
            return f"Sir, I can't close '{title}' — it's a protected window."
        _run(["wmctrl", "-ic", wid])
        return f"Sent close to '{title}'."
    if verb in ("window_move", "window_resize"):
        if _is_protected_window(title):
            return f"Sir, I can't {verb.split('_')[1]} '{title}' — it's a protected window."
        try:
            a, b = (int(v) for v in geom.replace(" ", "").split(","))
        except Exception:
            form = ("move:<title>:<x>,<y>" if verb == "window_move"
                    else "resize:<title>:<w>,<h>")
            return f"Sir, I need the form: window_{form}"
        code, out = _run(["xdotool", "getwindowgeometry", "--shell", wid])
        if code != 0:
            return "Sir, I couldn't read the window geometry."
        cur = {}
        for kv in out.splitlines():
            if "=" in kv:
                k, v = kv.split("=", 1)
                cur[k.strip()] = int(v.strip())
        if verb == "window_move":
            w = cur.get("WIDTH", 800)
            h = cur.get("HEIGHT", 600)
        else:
            w, h = a, b
            a = cur.get("X", 0)
            b = cur.get("Y", 0)
        _run(["wmctrl", "-i", "-r", wid, "-e", f"0,{a},{b},{w},{h}"])
        if verb == "window_move":
            return f"Moved '{title}' to ({a},{b})."
        return f"Resized '{title}' to {w}x{h}."
    return f"Sir, I don't support that window action: {verb}"


# ---------------------------------------------------------------------------
# Windows backend (ctypes)
# ---------------------------------------------------------------------------

def _win_collect_windows():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    result = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if title.strip():
            result.append({"hwnd": hwnd, "title": title})
        return True

    user32.EnumWindows(_cb, 0)
    return result


def _win_find(title):
    if not title:
        return None
    title_l = title.lower()
    for w in _win_collect_windows():
        if title_l in w["title"].lower():
            return w["hwnd"]
    return None


def _win_window_control(verb, title, geom):
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32

    hwnd = _win_find(title)
    if hwnd is None:
        return f"Sir, I couldn't find a window matching '{title}'."

    SW_MINIMIZE, SW_MAXIMIZE, SW_RESTORE = 6, 3, 9
    if verb == "window_focus":
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        return f"Focused '{title}'."
    if verb == "window_minimize":
        user32.ShowWindow(hwnd, SW_MINIMIZE)
        return f"Minimised '{title}'."
    if verb == "window_maximize":
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
        return f"Maximised '{title}'."
    if verb == "window_restore":
        user32.ShowWindow(hwnd, SW_RESTORE)
        return f"Restored '{title}'."
    if verb == "window_close":
        if _is_protected_window(title):
            return f"Sir, I can't close '{title}' — it's a protected window."
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        return f"Sent close to '{title}'."
    if verb in ("window_move", "window_resize"):
        if _is_protected_window(title):
            return f"Sir, I can't {verb.split('_')[1]} '{title}' — it's a protected window."
        try:
            a, b = (int(v) for v in geom.replace(" ", "").split(","))
        except Exception:
            form = ("move:<title>:<x>,<y>" if verb == "window_move"
                    else "resize:<title>:<w>,<h>")
            return f"Sir, I need the form: window_{form}"
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if verb == "window_move":
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            user32.MoveWindow(hwnd, a, b, w, h, True)
            return f"Moved '{title}' to ({a},{b})."
        user32.MoveWindow(hwnd, rect.left, rect.top, a, b, True)
        return f"Resized '{title}' to {a}x{b}."


# ---------------------------------------------------------------------------
# Shared dispatcher
# ---------------------------------------------------------------------------

def _report(player, msg):
    try:
        player.write_log(f"GANDALF: {msg}")
    except Exception:
        pass
    return msg


def run_window_control(parameters, response=None, player=None, session_memory=None):
    action = str((parameters or {}).get("action", "")).strip()
    msg = ""

    try:
        if action == "window_list":
            if os.name == "nt":
                wins = _win_collect_windows()
            else:
                wins = _linux_windows()
            lines = ([f"Open windows ({len(wins)}):"] if wins
                     else ["No open windows."])
            for i, w in enumerate(wins[:30], 1):
                lines.append(f"  {i}. {w['title']}")
            if len(wins) > 30:
                lines.append(f"  ... and {len(wins) - 30} more.")
            msg = "\n".join(lines)

        elif action.startswith("window_"):
            parts = action.split(":", 1)
            verb = parts[0]
            spec = parts[1] if len(parts) > 1 else ""
            if not spec:
                return _report(player, "Sir, which window should I manage?")
            # For move/resize the spec is "title:x,y" / "title:w,h".
            title = spec
            if verb in ("window_move", "window_resize") and ":" in spec:
                title, geom = spec.split(":", 1)
            else:
                geom = ""

            if os.name == "nt":
                msg = _win_window_control(verb, title, geom)
            else:
                msg = _linux_window_control(verb, title, geom)
        else:
            msg = ("Sir, I can list open windows, focus/minimise/maximise/restore/"
                   "close a window by title, or move/resize one. What would you like?")
    except Exception as e:
        msg = f"Sir, the window action failed. ({e})"

    return _report(player, msg)