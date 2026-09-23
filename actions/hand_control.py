# -*- coding: utf-8 -*-
"""
Birleşik "the hands" kontrolünü başlatıp durduran local modül.

Kamera iki farklı proses tarafından PAYLAŞILAMAZ (yalnız tek proses görüntü
alır). Bu yüzden "the hands" TEK bir birleşik programdır:
window_gesture.py --hands  (pencere yönetimi + parlaklık/ses pinç).

Davranış:
  - start_hand_control():  window_gesture.py'yi "pythonw" ile, "--hands"
    argümanıyla, pencere AÇMADAN (arka planda) başlatır.
  - stop_hand_control():   "pythonw" sürecini sonlandırır.
  - is_hand_control_running(): şu an çalışıyor mu?

Çağıranlar (sesli komut, local AI ve Telegram):
  - llm.py            -> bunu "hand_control" intent'ine bağlar
  - gemini_handler.py -> bunu Gemini FunctionCall'a bağlar
  - telegram_bot.py   -> /pencere vb. komutlar da bunu kullanır
"""
import os
import subprocess
import sys
import threading

_proc = None
_log_f = None
_lock = threading.Lock()
_hand_thread = None

# This project's own bundled WindowGesture folder (portable - travels with the
# project), overridable via HAND_GESTURE_CONTROL_SCRIPT.
_BUNDLED = os.path.normpath(
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "WindowGesture",
                 "window_gesture.py")
)

HAND_SCRIPT = os.path.abspath(_BUNDLED)
if not os.path.exists(HAND_SCRIPT):
    # Fall back to an env-override, then to a sibling parent WindowGesture.
    HAND_SCRIPT = os.environ.get(
        "HAND_GESTURE_CONTROL_SCRIPT",
        _BUNDLED,
    )
# If default path doesn't exist, try parent directory search and current directory
if not os.path.exists(HAND_SCRIPT):
    # Try searching in parent directories
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for search_dir in [project_root, os.path.dirname(project_root), os.path.dirname(os.path.dirname(__file__))]:
        candidate = os.path.join(search_dir, "WindowGesture", "window_gesture.py")
        if os.path.exists(candidate):
            HAND_SCRIPT = candidate
            break
# Also try current directory (for standalone APP folder)
if not os.path.exists(HAND_SCRIPT):
    candidate = os.path.join(os.path.dirname(os.path.dirname(__file__)), "WindowGesture", "window_gesture.py")
    if os.path.exists(candidate):
        HAND_SCRIPT = candidate

# For PyInstaller bundled app, try to find it in the data directory
if not os.path.exists(HAND_SCRIPT):
    try:
        import sysconfig
        # In PyInstaller, data files are extracted to _MEIPASS
        if hasattr(sys, '_MEIPASS'):
            candidate = os.path.join(sys._MEIPASS, "WindowGesture", "window_gesture.py")
            if os.path.exists(candidate):
                HAND_SCRIPT = candidate
    except Exception:
        pass

HANDS_ARG = "--hands"


def _pythonw():
    """python.exe de varsa pythonw.exe aynı klasördedir.
    PyInstaller EXE içinde ise sys.executable kullanılır.
    Linux'ta pythonw yoktur; doğrudan sys.executable (python3) döner."""
    if os.name != "nt":
        return sys.executable
    exe = sys.executable
    # In PyInstaller bundled app, sys.executable points to the EXE itself
    # We need to use python.exe from the Python installation if available
    if hasattr(sys, '_MEIPASS'):
        # Running in PyInstaller bundle
        # Try to find python.exe in the system
        python_exe = sys.executable
        if os.path.exists(python_exe):
            return python_exe
        # Fallback to PATH
        return "python"
    else:
        # Normal Python environment
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(w):
            return w
        return exe  # yoksa python.exe (konsol kısa an görünür)


def _is_alive(proc):
    if proc is None:
        return False
    return proc.poll() is None


def _log_file():
    """window_gesture.log'yu açık tutan TEK handle (her start'ta yeni handle
    biriktirip dosyayı kilitlemeyelim)."""
    global _log_f
    if _log_f is not None:
        return _log_f
    log_path = os.path.join(os.path.dirname(HAND_SCRIPT), "window_gesture.log")
    try:
        _log_f = open(log_path, "ab", buffering=0)
    except Exception:
        _log_f = None
    return _log_f


def _close_log():
    global _log_f
    if _log_f is not None:
        try:
            _log_f.close()
        except Exception:
            pass
        _log_f = None


def is_hand_control_running():
    with _lock:
        return _is_alive(_proc)


def start_hand_control():
    global _proc
    with _lock:
        if _is_alive(_proc):
            return "already_running"
        try:
            # Check if hand control script exists
            if not os.path.exists(HAND_SCRIPT):
                return "script_not_found"
            # Çıktı, teşhis için window_gesture.log'a yazılır (konsol yok).
            log_f = _log_file()
            if log_f is None:
                return "failed"
            
            # In PyInstaller bundle, we need to use the bundled Python
            python_exe = _pythonw()
            
            # If running in PyInstaller bundle, use sys.executable directly
            if hasattr(sys, '_MEIPASS'):
                python_exe = sys.executable
            
            _kwargs = dict(
                cwd=os.path.dirname(HAND_SCRIPT),
                stdout=log_f,
                stderr=log_f,
            )
            # CREATE_NO_WINDOW is Windows-only; omit it on Linux.
            if os.name == "nt":
                _kwargs["creationflags"] = getattr(
                    subprocess, "CREATE_NO_WINDOW", 0)
            _proc = subprocess.Popen(
                [python_exe, HAND_SCRIPT, HANDS_ARG],
                **_kwargs,
            )
            return "started"
        except Exception as e:
            print(f"Hand control start error: {e}")
            _proc = None
            return "failed"


def stop_hand_control():
    global _proc
    with _lock:
        if not _is_alive(_proc):
            _proc = None
            _close_log()
            return "not_running"
        try:
            _proc.terminate()
            try:
                _proc.wait(timeout=3)
            except Exception:
                _proc.kill()
        except Exception:
            pass
        _proc = None
        _close_log()
        return "stopped"
