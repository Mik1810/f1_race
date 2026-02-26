#!/usr/bin/env python3
"""
F1 Race DALI — Web Dashboard (backend)
Polls tmux panes and serves them as a live web UI.

Usage:  bash ui/run.sh
        Open:  http://localhost:5000
"""

import json
import re
import signal
import subprocess
import argparse
import atexit
import os
import sys
import time
import threading
from flask import Flask, jsonify, request, send_from_directory

# ── Restart logger ────────────────────────────────────────────────────────────
_RLOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "log", "restart.log")
_RLOG_FILE = os.path.normpath(_RLOG_FILE)
os.makedirs(os.path.dirname(_RLOG_FILE), exist_ok=True)
_rlog_t0: float = 0.0

def _rlog(msg: str) -> None:
    """Write a timestamped line to both stdout and log/restart.log."""
    elapsed_ms = int((time.time() - _rlog_t0) * 1000) if _rlog_t0 else 0
    line = f"[PY  ][{elapsed_ms:6d} ms] {msg}"
    print(line, flush=True)
    try:
        with open(_RLOG_FILE, "a", encoding="utf-8") as _f:
            _f.write(line + "\n")
    except Exception:
        pass
# ─────────────────────────────────────────────────────────────────────────────

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


@app.route("/api/results")
def api_results():
    """Parse the pitwall pane for final race results.
    Returns {ready: bool, results: [{pos, id, driver, team, label, color, border, time, points, dnf}]}.
    """
    pane_text = capture_pane("pitwall")
    if "=== FINAL RESULTS ===" not in pane_text:
        return jsonify({"ready": False, "results": []})

    # Official F1 points (P1..P10; 0 beyond P10 or DNF)
    F1_POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]

    # Load car metadata from agents.json
    agents_json = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "agents.json")
    )
    cars_by_id: dict = {}
    try:
        with open(agents_json, encoding="utf-8") as f:
            cfg = json.load(f)
        cars_by_id = {c["id"]: c for c in cfg.get("cars", [])}
    except Exception:
        pass

    # Parse lines after the FINAL RESULTS header
    # Format: [PitWall] P1: ferrari -- 389s   or   [PitWall] P1: ferrari -- DNF
    section = pane_text[pane_text.index("=== FINAL RESULTS ==="):]
    results = []
    for m in re.finditer(r'\[PitWall\] P(\d+): (\w+) -- ([\w]+)', section):
        pos      = int(m.group(1))
        cid      = m.group(2)
        time_raw = m.group(3).rstrip('s')   # strip trailing 's' (e.g. "347s" → "347")
        dnf      = time_raw == "DNF"
        car      = cars_by_id.get(cid, {})

        # Convert seconds → "m:ss" (e.g. 389 → "6:29")
        if dnf:
            time_fmt = "DNF"
        else:
            try:
                total_s = int(time_raw)
                time_fmt = f"{total_s // 60}:{total_s % 60:02d}"
            except ValueError:
                time_fmt = time_raw

        points = 0 if dnf else (F1_POINTS[pos - 1] if pos <= len(F1_POINTS) else 0)

        results.append({
            "pos":    pos,
            "id":     cid,
            "driver": car.get("driver", cid),
            "team":   car.get("team", cid),
            "label":  car.get("label", cid),
            "color":  car.get("color", "#111111"),
            "border": car.get("border", "#888888"),
            "time":   time_fmt,
            "points": points,
            "dnf":    dnf,
        })
    results.sort(key=lambda r: r["pos"])
    return jsonify({"ready": bool(results), "results": results})


@app.route("/api/race-state")
def api_race_state():
    """Parse pitwall + car panes to build a structured race state for the circuit view.

    Returns:
    {
      race_started, race_over, safety_car,
      current_lap, total_laps,
      cars: {
        <id>: { laps_completed, lap_times, pit_stops, dnf,
                total_time, position, in_pit,
                label, driver, team, color, border }
      }
    }
    """
    pitwall_text = capture_pane("pitwall")

    # Load car configs + total_laps from agents.json
    agents_file = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "agents.json")
    )
    try:
        with open(agents_file, encoding="utf-8") as f:
            cfg = json.load(f)
        total_laps = cfg.get("total_laps", 5)
        cars_cfg   = {c["id"]: c for c in cfg.get("cars", [])}
    except Exception:
        total_laps = 5
        cars_cfg   = {}

    state: dict = {
        "race_started": False,
        "race_over":    False,
        "safety_car":   False,
        "current_lap":  0,
        "total_laps":   total_laps,
        "cars":         {},
    }

    # Pane not available yet
    if not pitwall_text or pitwall_text.startswith("[pane"):
        return jsonify(state)

    lines = pitwall_text.split("\n")

    # Race started? (first sign is any car lap time appearing in pitwall)
    if re.search(r'\[PitWall\] \w+ lap: \d+s', pitwall_text, re.IGNORECASE) or \
       re.search(r'\[PitWall\] Lap \d', pitwall_text):
        state["race_started"] = True

    # Race over?
    state["race_over"] = "=== FINAL RESULTS ===" in pitwall_text

    # Safety car — track last occurrence in order
    sc = False
    for line in lines:
        if "SAFETY CAR deployed" in line:
            sc = True
        elif "GREEN FLAG" in line:
            sc = False
    state["safety_car"] = sc

    # Current lap (last "[PitWall] Lap N / M")
    lap_matches = list(re.finditer(r'\[PitWall\] Lap (\d+) / (\d+)', pitwall_text))
    if lap_matches:
        m = lap_matches[-1]
        state["current_lap"] = int(m.group(1))
        state["total_laps"]  = int(m.group(2))

    # Per-car data
    for car_id, car_meta in cars_cfg.items():
        # Use the team name (as written by PitWall) rather than capitalize(id),
        # which breaks for multi-word names like "Red Bull" (id="redbull").
        cap = car_meta.get("team", car_id.capitalize())

        # Lap times list
        lap_times = [
            int(t) for t in re.findall(
                rf'\[PitWall\] {re.escape(cap)} lap: (\d+)s',
                pitwall_text, re.IGNORECASE,
            )
        ]
        # Pit stops
        pit_count = len(re.findall(
            rf'\[PitWall\] {re.escape(cap)} pit stop',
            pitwall_text, re.IGNORECASE,
        ))
        # DNF
        dnf = bool(re.search(
            rf'\[PitWall\] {re.escape(cap)} DNF',
            pitwall_text, re.IGNORECASE,
        ))

        # Last known race position from standings block
        pos = None
        for m in re.finditer(r'\[PitWall\] P(\d+): (\w+) -- ', pitwall_text):
            if m.group(2).lower() == car_id.lower():
                pos = int(m.group(1))

        # Is car currently in pit?
        in_pit = False
        car_pane = capture_pane(car_id)
        if car_pane and not car_pane.startswith("[pane"):
            for line in reversed(car_pane.split("\n")):
                if "BOX BOX BOX" in line:
                    in_pit = True
                    break
                if any(kw in line for kw in
                       ["LIGHTS OUT", "power", "pushing", "flat out",
                        "PUSH LAP", "On the power"]):
                    break

        state["cars"][car_id] = {
            "laps_completed": len(lap_times),
            "lap_times":      lap_times,
            "pit_stops":      pit_count,
            "dnf":            dnf,
            "total_time":     sum(lap_times),
            "position":       pos,
            "in_pit":         in_pit,
            "label":          car_meta.get("label",  car_id),
            "driver":         car_meta.get("driver", car_id),
            "team":           car_meta.get("team",   car_id),
            "color":          car_meta.get("color",  "#111111"),
            "border":         car_meta.get("border", "#888888"),
        }

    # Parse events with their lap context so the frontend can reveal each
    # event only after the corresponding lap animation has completed.
    #
    # Algorithm (forward scan):
    #   - "[PitWall] Lap N / M" marks N laps as officially complete.
    #     After seeing it, the in-progress lap becomes N+1, so any event
    #     that follows must wait for lap N+1 to animate → reveal_lap = N+1.
    #   - Events appearing before the first lap marker belong to lap 1.
    #   - Events after the last lap marker (FINAL RESULTS, CHEQUERED FLAG…)
    #     get reveal_lap = total_laps + 1 so the circuitQueueDrained() gate
    #     in the frontend (animOver) reveals them once every lap is done.
    _EVENT_KEYWORDS = [
        "SAFETY CAR", "GREEN FLAG", "LIGHTS OUT", "FINAL RESULTS",
        "CHEQUERED FLAG", "DNF", "pit stop",
        "RAIN", "engine failure", "fastest lap",
    ]
    _LAP_CTR_RE = re.compile(r'\[PitWall\] Lap (\d+) / \d+')
    current_lap_ctx: int = 1   # events here are revealed when this many laps animated
    events_with_lap: list = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        lap_m = _LAP_CTR_RE.match(stripped)
        if lap_m:
            # Advance context: laps done = N  →  in-progress lap = N+1
            current_lap_ctx = int(lap_m.group(1)) + 1
            continue   # lap-counter lines are bookmarks, not events
        if any(kw.lower() in stripped.lower() for kw in _EVENT_KEYWORDS):
            events_with_lap.append({"text": stripped, "lap": current_lap_ctx})
    # Keep last 20 chronologically
    state["recent_events"] = events_with_lap[-20:]

    return jsonify(state)


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
    Always allowed — if a restart is already running it is superseded.
    """
    f1_race_dir = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    )
    t = threading.Thread(
        target=_do_restart,
        args=(f1_race_dir,),
        daemon=True,
    )
    t.start()
    return jsonify({"ok": True})


def _kill_all():
    """Best-effort kill of every MAS-related process, with death confirmation."""
    _rlog("_kill_all start")
    # Phase 1: send SIGKILL to all MAS-related processes IN PARALLEL.
    cmds = [
        ["pkill", "-9", "-f", "startmas.sh"],
        ["tmux", "kill-session", "-t", SESSION],
        ["pkill", "-9", "-f", "active_server_wi.pl"],
        ["pkill", "-9", "-f", "active_dali_wi.pl"],
        ["pkill", "-9", "-f", "active_user_wi.pl"],
    ]

    def _run(cmd):
        try:
            subprocess.run(cmd, timeout=5, capture_output=True)
        except Exception:
            pass

    threads = [threading.Thread(target=_run, args=(c,), daemon=True) for c in cmds]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=5)
    _rlog("  parallel pkill/tmux done")

    # Phase 2: wait until pgrep confirms every SICStus process is gone.
    _PATTERNS = ["active_server_wi.pl", "active_dali_wi.pl", "active_user_wi.pl"]
    for attempt in range(10):   # up to 1 s
        still_alive = False
        for pat in _PATTERNS:
            r = subprocess.run(["pgrep", "-f", pat], capture_output=True)
            if r.returncode == 0:
                still_alive = True
                break
        if not still_alive:
            _rlog(f"  pgrep confirmed dead (attempt {attempt+1})")
            break
        time.sleep(0.1)
    else:
        _rlog("  WARNING: some SICStus processes did not die in 1 s")
        print("[kill_all] WARNING: some SICStus processes did not die in 1 s", flush=True)

    time.sleep(0.2)   # brief pause for kernel socket cleanup
    _rlog("_kill_all done")


def _in_docker() -> bool:
    """Return True when running inside a Docker container."""
    return os.path.exists("/.dockerenv")


def _launch(f1_race_dir: str):
    """Launch or signal startmas.sh depending on the runtime environment."""
    _rlog("_launch start")
    if _in_docker():
        trigger = "/tmp/tmux-shared/.restart"
        try:
            os.makedirs(os.path.dirname(trigger), exist_ok=True)
            with open(trigger, "w") as fh:
                fh.write("1\n")
            _rlog(f"  Docker trigger written to {trigger}")
            print(f"[restart] Trigger written to {trigger}", flush=True)
        except Exception as exc:
            _rlog(f"  WARNING: could not write restart trigger: {exc}")
            print(f"[restart] WARNING: could not write restart trigger: {exc}", flush=True)
    else:
        subprocess.Popen(
            ["bash", "startmas.sh"],
            cwd=f1_race_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _rlog("  startmas.sh launched as detached process")
        print("[restart] startmas.sh launched as detached process.", flush=True)


def _do_restart(f1_race_dir: str):
    """Kill the MAS and relaunch via startmas.sh.

    Port-free waiting and ADDRINUSE retries are handled entirely by
    startmas.sh — Python's job is just to kill and hand off.
    """
    import datetime
    global _rlog_t0
    _rlog_t0 = time.time()
    th = threading.current_thread()
    header = (
        f"\n=== restart at {datetime.datetime.now().isoformat()} "
        f"thread={th.name} tid={th.ident} ===\n"
    )
    try:
        with open(_RLOG_FILE, "a", encoding="utf-8") as _f:
            _f.write(header)
    except Exception:
        pass
    _rlog(f"_do_restart START thread={th.name} tid={th.ident}")
    _kill_all()
    _launch(f1_race_dir)
    _rlog("_do_restart DONE — startmas.sh running, bash owns the port-wait")




if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="F1 Race DALI — Web Dashboard")
    parser.add_argument("--port",    type=int, default=5000,  help="HTTP port (default: 5000)")
    parser.add_argument("--session", default="f1_race",       help="tmux session name")
    args = parser.parse_args()
    SESSION = args.session

    # ── Shutdown handler: kill SICStus + tmux when the UI is stopped ──────────
    # Covers three cases:
    #   1. CTRL+C        → Python raises KeyboardInterrupt → atexit fires
    #   2. kill <pid>    → SIGTERM  → _shutdown() called  → atexit fires
    #   3. Normal exit() → atexit fires
    def _shutdown(signum=None, frame=None):
        print("\n[ui] Shutting down — stopping MAS...", flush=True)
        _kill_all()
        print("[ui] MAS stopped.", flush=True)
        sys.exit(0)

    atexit.register(_kill_all)                         # CTRL+C / normal exit
    signal.signal(signal.SIGTERM, _shutdown)           # kill <pid>
    # SIGINT is already converted to KeyboardInterrupt by Python;
    # Flask re-raises it after cleanup, which triggers atexit.

    print(f"\n  \U0001f3ce  F1 Race DALI Dashboard")
    print(f"  \u25ba  Open in browser: http://localhost:{args.port}\n")
    app.run(host="0.0.0.0", port=args.port, debug=False)
