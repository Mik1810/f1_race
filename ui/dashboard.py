#!/usr/bin/env python3
"""
F1 Race DALI — Web Dashboard (backend)
Polls tmux panes and serves them as a live web UI.

Usage:  bash ui/run.sh
        Open:  http://localhost:5000
"""

import json
import subprocess
import argparse
import os
import time
import threading
from flask import Flask, jsonify, request, send_from_directory

# ── Config ────────────────────────────────────────────────────────────────────

SESSION = "f1_race"

# Fixed panes: infrastructure agents that never change
_FIXED_PANES_BEFORE = [
    {"id": "server",     "label": "Server (LINDA)", "color": "#121212", "border": "#555555"},
    {"id": "user",       "label": "User Console",   "color": "#0b180b", "border": "#4caf50"},
    {"id": "semaphore",  "label": "Semaphore",      "color": "#0a0a18", "border": "#aa88ff"},
]
_FIXED_PANES_AFTER = [
    {"id": "pitwall",    "label": "Pit Wall",       "color": "#05051a", "border": "#2277ff"},
    {"id": "safety_car", "label": "Safety Car",     "color": "#181600", "border": "#ffd700"},
]


def _load_car_panes() -> list:
    """Read car agent definitions from agents.json (one level above ui/)."""
    agents_json = os.path.join(os.path.dirname(__file__), "..", "agents.json")
    agents_json = os.path.normpath(agents_json)
    try:
        with open(agents_json, encoding="utf-8") as f:
            cfg = json.load(f)
        return [
            {
                "id":     car["id"],
                "label":  car["label"],
                "color":  car["color"],
                "border": car["border"],
            }
            for car in cfg.get("cars", [])
        ]
    except FileNotFoundError:
        print(f"[dashboard] WARNING: {agents_json} not found — no car panes loaded.")
        return []
    except (KeyError, json.JSONDecodeError) as exc:
        print(f"[dashboard] WARNING: could not parse agents.json: {exc}")
        return []


# Build the full panes list: fixed infrastructure + cars (from config) + fixed tail
PANES = _FIXED_PANES_BEFORE + _load_car_panes() + _FIXED_PANES_AFTER

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# ── tmux helpers ──────────────────────────────────────────────────────────────

def capture_pane(window_id: str) -> str:
    """Read the last ~400 lines from a tmux pane (plain text, no ANSI codes)."""
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-pt", f"{SESSION}:{window_id}", "-S", "-400"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode != 0:
            return f"[pane '{window_id}' not available — is the MAS running?]"
        return r.stdout
    except FileNotFoundError:
        return "[tmux not found — run this inside WSL or a Linux terminal]"
    except subprocess.TimeoutExpired:
        return "[timeout reading pane]"
    except Exception as e:
        return f"[error: {e}]"


def send_keys(window_id: str, cmd: str) -> None:
    """Type a command into a tmux pane as if the user pressed Enter."""
    subprocess.run(
        ["tmux", "send-keys", "-t", f"{SESSION}:{window_id}", cmd, "Enter"],
        timeout=3,
    )


# ── Flask app ─────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


def current_panes() -> list:
    """Return up-to-date panes list, re-reading agents.json each call."""
    return _FIXED_PANES_BEFORE + _load_car_panes() + _FIXED_PANES_AFTER


@app.route("/api/config")
def api_config():
    return jsonify({"session": SESSION, "panes": current_panes()})


@app.route("/api/reload-config")
def api_reload_config():
    """Re-read agents.json and return the updated panes list (no restart needed)."""
    global PANES
    PANES = current_panes()
    return jsonify({"ok": True, "panes": PANES})


@app.route("/api/panes")
def api_panes():
    return jsonify({p["id"]: capture_pane(p["id"]) for p in current_panes()})


@app.route("/api/send", methods=["POST"])
def api_send():
    data = request.get_json()
    if not data or "window" not in data or "cmd" not in data:
        return jsonify({"error": "missing fields"}), 400
    send_keys(data["window"], data["cmd"])
    return jsonify({"ok": True})


@app.route("/api/restart", methods=["POST"])
def api_restart():
    """Kill current MAS and relaunch via startmas.sh.
    A background watcher thread monitors the server pane for ADDRINUSE and
    automatically retries the full restart cycle if needed.
    """
    f1_race_dir = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    )
    _do_restart(f1_race_dir, max_attempts=5)
    return jsonify({"ok": True})


def _kill_all():
    """Best-effort kill of every MAS-related process."""
    for cmd in [
        ["pkill", "-9", "-f", "startmas.sh"],
        ["tmux", "kill-session", "-t", SESSION],
        ["pkill", "-9", "-f", "active_server_wi.pl"],
        ["pkill", "-9", "-f", "active_dali_wi.pl"],
        ["pkill", "-9", "-f", "active_user_wi.pl"],
    ]:
        try:
            subprocess.run(cmd, timeout=5)
        except Exception:
            pass
    time.sleep(1)


def _launch(f1_race_dir: str):
    """Launch startmas.sh detached."""
    subprocess.Popen(
        ["bash", "startmas.sh"],
        cwd=f1_race_dir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _do_restart(f1_race_dir: str, max_attempts: int = 5):
    """Kill, launch, then start the watcher thread."""
    _kill_all()
    _launch(f1_race_dir)
    t = threading.Thread(
        target=_watch_for_addrinuse,
        args=(f1_race_dir, max_attempts),
        daemon=True,
    )
    t.start()


def _watch_for_addrinuse(f1_race_dir: str, attempts_left: int):
    """Watch the server pane; if ADDRINUSE appears, restart automatically."""
    # Wait long enough for SICStus to either succeed or print the error.
    # The server usually starts in 3-8 s; 12 s gives ample margin.
    time.sleep(12)
    pane = capture_pane("server")
    if "ADDRINUSE" in pane:
        print(f"[auto-retry] ADDRINUSE detected in server pane "
              f"(attempts left: {attempts_left})", flush=True)
        if attempts_left > 0:
            _do_restart(f1_race_dir, max_attempts=attempts_left - 1)
        else:
            print("[auto-retry] max attempts reached, giving up.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="F1 Race DALI — Web Dashboard")
    parser.add_argument("--port",    type=int, default=5000,  help="HTTP port (default: 5000)")
    parser.add_argument("--session", default="f1_race",       help="tmux session name")
    args = parser.parse_args()
    SESSION = args.session
    print(f"\n  \U0001f3ce  F1 Race DALI Dashboard")
    print(f"  \u25ba  Open in browser: http://localhost:{args.port}\n")
    app.run(host="0.0.0.0", port=args.port, debug=False)
