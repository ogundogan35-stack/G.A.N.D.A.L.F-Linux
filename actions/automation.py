"""Automation & shortcuts for Gandalf (keyboard/mouse/tasks).

Uses pyautogui (already a dependency) for input injection and a worker thread
for simple one-shot delayed tasks. Only does what the user asks for.

Supported actions (parameters.action with parameters.value / parameters.delay):
    type_text:<text>          type text into the active field
    hotkey:<combo>            send a hotkey combo e.g. "ctrl+alt+delete" or "win+d"
    press_key:<key>           press a single key
    mouse_click:[left|right|middle]
    mouse_move_to:<x>,<y>
    scroll:<amt>              scroll wheel by amount
    task_delay:<seconds>:<command>   run a shell "command" after N seconds
"""

import os
import threading
import subprocess

import pyautogui
pyautogui.FAILSAFE = False  # corner-bound mouse must not abort automation


def _norm_key(key: str) -> str:
    """Map Window-specific key names to their Linux equivalents (best effort)."""
    k = key.strip().lower()
    if os.name != "nt":
        if k in ("win", "windows", "meta"):
            return "super"
    return k


def _report(player, msg):
    try:
        player.write_log(f"GANDALF: {msg}")
    except Exception:
        pass
    return msg


def _type_combo(combo: str):
    """Parse 'ctrl+alt+t' style combos and send them."""
    keys = [_norm_key(k) for k in combo.replace(" ", "").split("+") if k.strip()]
    if not keys:
        return False
    pyautogui.hotkey(*keys)
    return True


def run_automation(parameters, response=None, player=None, session_memory=None):
    action = str((parameters or {}).get("action", "")).strip()
    msg = ""
    try:
        if action.startswith("type_text"):
            txt = action.split(":", 1)[1] if ":" in action else str((parameters or {}).get("value") or "")
            if txt:
                pyautogui.write(txt, interval=0.02)
                msg = "I typed that text, sir."
            else:
                msg = "Sir, what should I type?"
        elif action.startswith("hotkey"):
            combo = action.split(":", 1)[1] if ":" in action else ""
            if combo and _type_combo(combo):
                msg = f"Sent the {combo} shortcut."
            else:
                msg = "Sir, I need a hotkey combo like 'ctrl+alt+delete' or 'win+d'."
        elif action.startswith("press_key"):
            key = _norm_key(action.split(":", 1)[1] if ":" in action else "")
            if key:
                pyautogui.press(key)
                msg = f"Pressed {key}."
            else:
                msg = "Sir, which key should I press?"
        elif action.startswith("mouse_click"):
            btn = action.split(":", 1)[1] if ":" in action else "left"
            pyautogui.click(button=btn.strip().lower() or "left")
            msg = f"Clicked {btn}."
        elif action.startswith("mouse_move_to"):
            try:
                x, y = (int(v) for v in action.split(":", 1)[1].replace(" ", "").split(","))
                pyautogui.moveTo(x, y)
                msg = f"Moved pointer to ({x},{y})."
            except Exception:
                msg = "Sir, I need coordinates like mouse_move_to:500,300."
        elif action.startswith("scroll"):
            try:
                amt = int(action.split(":", 1)[1])
                pyautogui.scroll(amt)
                msg = f"Scrolled by {amt}."
            except Exception:
                msg = "Sir, I need an amount like scroll:5."
        elif action.startswith("task_delay"):
            spec = action.split(":", 1)[1] if ":" in action else ""
            try:
                secs, cmd = spec.split(":", 1)
                secs = max(1, int(secs.strip()))
                cmd = cmd.strip()
            except Exception:
                cmd, secs = "", 0
            if not cmd:
                msg = "Sir, I need: task_delay:<seconds>:<command>"
            else:
                threading.Timer(secs, lambda: subprocess.Popen(cmd, shell=True)).start()
                msg = f"I'll run '{cmd}' in {secs} seconds, sir."
        else:
            msg = ("Sir, I can type text, send hotkeys, press keys, click/move the "
                   "mouse, scroll, or schedule a one-shot task. What would you like?")
    except Exception as e:
        msg = f"Sir, the automation failed. ({e})"
    return _report(player, msg)
