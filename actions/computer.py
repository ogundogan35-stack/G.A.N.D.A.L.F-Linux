"""Computer-control coordinator for Gandalf.

Routes a single ``system_control`` intent (now broadened to "computer
control") to the right specialised module based on the action name. Unknown
actions fall through to the original ``run_system_control`` so nothing already
working breaks.

Categories:
    window_* / processes         -> window_control
    system_status/cpu/ram/disk/battery/network/system_info/uptime/screenshot
                                -> system_diagnose
    type_text/hotkey/press_key/mouse_*/scroll/task_delay
                                -> automation
    clipboard_*/ocr            -> clipboard_screen
    everything else            -> system_control (legacy)
"""

from actions import window_control
from actions import system_diagnose
from actions import automation
from actions import clipboard_screen
from actions.system_control import run_system_control

_WINDOW = ("window_",)
_DIAGNOSE = ("system_status", "cpu", "ram", "disk", "battery", "network",
             "system_info", "uptime", "screenshot", "processes")
_AUTOMATION = ("type_text", "hotkey", "press_key", "mouse_", "scroll", "task_delay")
_CLIPBOARD = ("clipboard_", "ocr")


def run_computer_control(parameters, response=None, player=None, session_memory=None):
    action = str((parameters or {}).get("action", "")).strip().lower()
    if not action:
        action = "window_list"
    mod = None
    if any(action == a or action.startswith(a) for a in _WINDOW):
        mod = window_control.run_window_control
    elif any(action == a or action.startswith(a) for a in _DIAGNOSE):
        mod = system_diagnose.run_system_diagnose
    elif any(action == a or action.startswith(a) for a in _AUTOMATION):
        mod = automation.run_automation
    elif any(action == a or action.startswith(a) for a in _CLIPBOARD):
        mod = clipboard_screen.run_clipboard_screen
    if mod is None:
        mod = run_system_control
    return mod(parameters, response=response, player=player,
               session_memory=session_memory)
