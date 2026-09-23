"""File/folder operations for Gandalf.

Gandalf can operate on ANY path on the computer (personal folders, other
drives, user folders). Destructive operations (delete, move, copy over an
existing target) are never executed immediately: Gandalf first reports what it
wants to change and asks for confirmation.

Supported actions (parameters.action) with parameters.path / parameters.target:
    open             -> open a file/folder with its default app
    copy             -> copy path -> target
    move             -> move path -> target
    delete           -> delete a file (or folder with folder:true)
    list             -> list a directory
    read             -> show first N lines of a text file
    create_dir       -> make a directory
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

from tts import edge_speak

_YES_MARKERS = ("yes", "evet", "onay", "onaylıyorum", "do it", "ok", "go",
                "remove", "confirm", "sil", "delete", "continue", "devam")


def _norm(raw):
    """Return an absolute, fully-expanded path, or None if empty."""
    if not raw:
        return None
    p = os.path.abspath(os.path.expanduser(str(raw).strip()))
    return p or None


def _open_path(path):
    """Open a file/folder with its default app (xb-open/gio or os.startfile)."""
    if os.name == "nt":
        os.startfile(path)
        return
    for cmd in (["xdg-open", path], ["gio", "open", path]):
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return
        except Exception:
            continue


def _needs_confirm(action: str, path, target) -> str | None:
    """Return a confirmation prompt string if the action would change files,
    else None."""
    if action in ("delete", "move"):
        what = os.path.basename(path or "") or path or "that item"
        verb = "delete" if action == "delete" else "move"
        return (f"Sir, to {verb} '{what}' I need your confirmation first. "
                "Say 'yes, proceed' to continue, or 'no' to cancel.")
    if action == "copy" and target and os.path.exists(target):
        what = os.path.basename(target) or target
        return (f"Sir, copying here would overwrite '{what}'. "
                "Say 'yes, overwrite' to continue, or 'no' to cancel.")
    return None


def _confirm_check(parameters, session_memory) -> bool:
    """True if the user explicitly confirmed a destructive operation.

    If there is no confirmation yet, stash a pending intent so the next user
    reply is treated as the answer, and return False.
    """
    confirm = str((parameters or {}).get("confirm") or "").strip().lower()
    words = set(re.split(r"[\W_]+", confirm))
    if confirm and (confirm in _YES_MARKERS or words.intersection(_YES_MARKERS)):
        return True
    # No confirmation yet -> remember this request, ask for it.
    if session_memory is not None:
        try:
            session_memory.set_pending_intent("file_operations")
            session_memory.update_parameters(parameters)
            session_memory.set_current_question("confirm")
        except Exception:
            pass
    return False


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


def run_file_ops(parameters, response=None, player=None, session_memory=None):
    action = str((parameters or {}).get("action", "")).strip().lower()
    path = _norm((parameters or {}).get("path"))
    target = _norm((parameters or {}).get("target"))
    msg = ""

    if not action:
        return _report(player, "Sir, I need to know which file operation to perform.")

    if not path or not os.path.exists(path):
        return _report(player, "Sir, that path does not exist on the computer.")

    # Destructive operations always need a fresh, explicit confirmation.
    need_confirm = _needs_confirm(action, path, target)
    if need_confirm and not _confirm_check(parameters, session_memory):
        return _report(player, need_confirm)

    try:
        if action == "open":
            _open_path(path)
            msg = f"Opened {os.path.basename(path)}."

        elif action == "list":
            if not os.path.isdir(path):
                msg = "That's not a folder."
            else:
                entries = sorted(os.listdir(path))[:40]
                msg = "Folder contents: " + (", ".join(entries) if entries else "empty.")

        elif action == "create_dir":
            os.makedirs(path, exist_ok=True)
            msg = f"Created folder {os.path.basename(path)}."

        elif action == "copy":
            if not target:
                msg = "I need a destination for the copy."
            else:
                if os.path.isdir(path):
                    shutil.copytree(path, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(path, target)
                msg = f"Copied to {target}."

        elif action == "move":
            if not target:
                msg = "I need a destination to move it to."
            else:
                shutil.move(path, target)
                msg = f"Moved to {target}."

        elif action == "delete":
            if os.path.isdir(path):
                recurse = bool((parameters or {}).get("folder"))
                if not recurse:
                    msg = "That's a folder. Say yes to delete the whole folder."
                else:
                    shutil.rmtree(path)
                    msg = f"Deleted folder {os.path.basename(path)}."
            else:
                os.remove(path)
                msg = f"Deleted {os.path.basename(path)}."

        elif action == "read":
            if not os.path.isfile(path):
                msg = "That's not a file."
            else:
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        head = "\n".join(f.read().splitlines()[:30])
                    msg = f"First lines of {os.path.basename(path)}:\n{head}"
                except Exception:
                    msg = "I couldn't read that file (maybe not text)."

        else:
            msg = f"Sir, I don't support that file action: {action}"

    except Exception as e:
        msg = f"Sir, the file operation failed. ({e})"

    _report(player, msg)
    _speak(msg, player)
    return msg