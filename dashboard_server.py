"""Dashboard web server for Gandalf.

A tiny Flask server exposing live system stats + the ongoing chat feed so it
can be watched from any device on the local network (e.g. an iPad).

Endpoints:
    GET /           -> futuristic dark HUD (templates/index.html)
    GET /api/stats  -> JSON { cpu, ram, status, last_user, last_ai, history, ts }

Integration:
    import dashboard_server
    dashboard_server.install(ui)     # wraps ui.write_log + starts the server

The server runs in a daemon thread so it never blocks the main UI loop.
"""

import os
import sys
import time
import threading
from collections import deque

try:
    import psutil
    PSUTIL_OK = True
except Exception:
    PSUTIL_OK = False

try:
    from flask import Flask, jsonify, render_template
    FLASK_OK = True
except Exception:
    FLASK_OK = False

HOST = "0.0.0.0"
PORT = int(os.getenv("DASHBOARD_PORT", "5000"))
MAX_HISTORY = 60

_state = {
    "status": "offline",
    "last_user": "",
    "last_ai": "",
    "history": deque(maxlen=MAX_HISTORY),
    "ts": 0,
    "started": time.time(),
}


def _read_metrics():
    cpu = psutil.cpu_percent(interval=None) if PSUTIL_OK else 0.0
    try:
        ram = psutil.virtual_memory().percent if PSUTIL_OK else 0.0
    except Exception:
        ram = 0.0
    return round(cpu, 1), round(ram, 1)


def record(text: str):
    """Feed one chat line into the dashboard state.

    Called automatically from the ui.write_log hook. "You:" / "Sen:" lines
    become the last_user snapshot, everything else becomes last_ai, and all
    lines are appended to the rolling history.
    """
    if not text:
        return
    s = str(text).strip()
    low = s.lower()
    if low.startswith("you:") or low.startswith("sen:"):
        _state["last_user"] = s.split(":", 1)[1].strip()
    else:
        # Strip "AI:" / "Gandalf:" prefixes that the UI prepends to replies.
        for p in ("ai:", "gandalf:"):
            if low.startswith(p):
                s = s.split(":", 1)[1].strip()
                break
        _state["last_ai"] = s
    _state["history"].append(s)
    _state["ts"] = time.time()
    _state["status"] = "online"


def install(ui=None, start_server=True, host=HOST, port=PORT):
    """Wrap ui.write_log so chat flows into the dashboard, then start Flask.

    Returns the module (for tests). Passing ui=None just starts the server.
    """
    if ui is not None and not getattr(ui, "_dashboard_wrapped", False):
        original = ui.write_log

        def proxy(text, *args, **kwargs):
            original(text, *args, **kwargs)
            record(text)

        ui.write_log = proxy
        ui._dashboard_wrapped = True

    _state["status"] = "ready"

    if start_server and FLASK_OK:
        threading.Thread(target=_serve, kwargs={"host": host, "port": port}, daemon=True).start()
    return _state


def _serve(host=HOST, port=PORT):
    app = Flask(__name__, template_folder=_template_dir())
    _attach_routes(app)
    try:
        app.run(host=host, port=port, threaded=True, use_reloader=False)
    except Exception as e:
        print(f"[Dashboard] server error: {e}")


def _template_dir():
    """Locate the templates folder for both source run and frozen exe."""
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"),
        os.path.join(getattr(sys, "_MEIPASS", ""), "templates"),
        os.path.join(os.getcwd(), "templates"),
    ]
    for cand in candidates:
        if cand and os.path.isdir(cand):
            return cand
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def _attach_routes(app):
    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/stats")
    def stats():
        cpu, ram = _read_metrics()
        return jsonify(
            cpu=cpu,
            ram=ram,
            status=_state["status"],
            last_user=_state["last_user"],
            last_ai=_state["last_ai"],
            history=list(_state["history"]),
            uptime=round(time.time() - _state["started"]),
            ts=_state["ts"],
        )

    @app.route("/api/ping")
    def ping():
        return jsonify(ok=True)


# Convenience entrypoint for quick manual testing:
#   python dashboard_server.py
if __name__ == "__main__":
    install(ui=None)
    print(f"[Dashboard] listening on http://{HOST}:{PORT}")
    threading.Event().wait()
