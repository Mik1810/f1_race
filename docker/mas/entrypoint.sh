#!/bin/bash
# ── MAS container entrypoint ───────────────────────────────────────────────────
# Runs startmas.sh initially, then watches the shared tmux-socket volume for a
# restart trigger written by the UI container (dashboard.py → /api/restart).
#
# Trigger protocol:
#   UI writes   /tmp/tmux-shared/.restart   (any content, just the presence matters)
#   This script detects it, deletes it, and re-runs startmas.sh.
#
# Why not just re-run bash startmas.sh from the UI container?
#   The UI container does not have SICStus Prolog.  All agent processes must be
#   spawned from THIS container where SICStus is mounted at /usr/local/sicstus4.6.0.
# ──────────────────────────────────────────────────────────────────────────────

TRIGGER=/tmp/tmux-shared/.restart
WORKDIR=/dali/Examples/f1_race

kill_mas() {
    echo "[entrypoint] Killing old MAS processes..."
    # Kill the tmux session (works via shared socket from either container)
    tmux kill-session -t f1_race 2>/dev/null || true
    # Kill SICStus processes that may still be running in this container
    pkill -9 -f active_server_wi.pl 2>/dev/null || true
    pkill -9 -f active_dali_wi.pl   2>/dev/null || true
    pkill -9 -f active_user_wi.pl   2>/dev/null || true
    # No need to wait for a specific port here: startmas.sh already handles
    # dynamic port selection (3010..3019) and waits for server.txt to confirm
    # the chosen port before proceeding.
}

run_mas() {
    rm -f "$TRIGGER"          # clear trigger before (re)starting
    cd "$WORKDIR"
    # Redirect stdin from /dev/null so startmas.sh never blocks on the
    # interactive "Press Enter to shutdown" read at the end of the script.
    # Without this, docker-compose tty:true would make [ -t 0 ] true and
    # startmas.sh would hang in tmux attach, preventing the restart loop.
    bash startmas.sh < /dev/null || true
}

echo "[entrypoint] Starting MAS for the first time..."
run_mas

# ── Restart-watch loop ────────────────────────────────────────────────────────
echo "[entrypoint] Entering restart-watch loop (trigger: $TRIGGER)"
while true; do
    if [ -f "$TRIGGER" ]; then
        echo "[entrypoint] Restart trigger detected — restarting MAS..."
        kill_mas
        run_mas
        echo "[entrypoint] MAS restarted. Watching for next trigger..."
    fi
    sleep 1
done
