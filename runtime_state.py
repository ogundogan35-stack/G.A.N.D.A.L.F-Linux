"""Shared runtime state for Gandalf.

A tiny, dependency-free wake/sleep switch used by every input source
(voice listener, Telegram bot, local UI) so the heavy Vosk speech engine and
AI calls only run while Gandalf is ACTIVE.

    runtime_state.set_active(True)   # /start, /wake
    runtime_state.set_active(False)  # /stop, /sleep
    runtime_state.is_active()        # checked by listeners before processing

The default is ``True`` (active mode) for standalone version: Gandalf starts
awake and ready to process commands.
"""

_active = True
_listeners = []


def on_change(callback) -> None:
    """Register a callback invoked (with the new bool) whenever the state
    changes. Callbacks are called synchronously from whoever flips the flag,
    so UI code should marshal to its own thread (e.g. via root.after)."""
    if callable(callback) and callback not in _listeners:
        _listeners.append(callback)


def set_active(value: bool) -> None:
    """Turn Gandalf's listening/AI on (True) or off (False)."""
    global _active
    new = bool(value)
    if new == _active:
        return
    _active = new
    for cb in list(_listeners):
        try:
            cb(new)
        except Exception:
            pass


def is_active() -> bool:
    """True when Gandalf is awake and processing commands."""
    return _active


def wake() -> None:
    set_active(True)


def sleep() -> None:
    set_active(False)
