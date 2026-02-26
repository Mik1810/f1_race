#!/bin/bash

# Enable debugging
# set -x  # Start debugging

# ── Timing helpers ─────────────────────────────────────────────────────────
_T0=$(date +%s%3N)   # script start time in milliseconds
_LOG_FILE="./log/restart.log"   # same file as dashboard.py
mkdir -p ./log
# Append a separator (dashboard.py writes the header and truncates the file first)
_tick() {
    local now; now=$(date +%s%3N)
    local elapsed=$(( now - _T0 ))
    local msg; msg=$(printf "[BASH][%6d ms] %s" "$elapsed" "$*")
    echo "$msg"                        # stdout (visible in tmux pane)
    echo "$msg" >> "$_LOG_FILE"        # shared log file
}
# ────────────────────────────────────────────────────────────────────────────

clear  # Clear the terminal

# Save the current directory to a variable
current_dir=$(pwd)

# Print the current directory
echo "The current directory is: $current_dir"

# Test if tmux is installed
if command -v tmux &> /dev/null; then
    echo "tmux is installed."
    tmux -V  # Display tmux version
else
    echo "TMUX is a requirement in Unix-like OS to run DALI"
    echo "tmux is not installed."
    echo "Check installation instructions at https://github.com/tmux/tmux/wiki/Installing"
    exit -1
fi

# Define paths and variables
SICSTUS_HOME=/usr/local/sicstus4.6.0
MAIN_HOME=../..
DALI_HOME=../../src
CONF_DIR=conf
PROLOG="$SICSTUS_HOME/bin/sicstus"
LINDA_PORT=3010
INSTANCES_HOME=mas/instances
TYPES_HOME=mas/types
BUILD_HOME=build

# ── Pre-cleanup: ensure no stale DALI instance is running ──────────────────
# Uses a lock file so concurrent startmas.sh invocations never overlap.
LOCKFILE="/tmp/f1_race_startmas.lock"
exec 200>"$LOCKFILE"
flock -x 200   # blocks until any other running instance releases the lock

_tick "START cleanup"
echo "Cleaning up any previous DALI instance on port $LINDA_PORT..."

# Helper: kill by pattern and WAIT until pgrep confirms the process is gone.
kill_and_wait() {
    local pattern="$1"
    pkill -9 -f "$pattern" 2>/dev/null || true
    # pgrep loop: pkill delivers the signal but the process may still be
    # alive for a few hundred ms while the kernel cleans up.
    for _i in $(seq 1 20); do
        pgrep -f "$pattern" &>/dev/null || return 0
        sleep 0.2
    done
    echo "WARNING: process matching '$pattern' did not die in 4 s" >&2
}

# 1. Kill all SICStus processes and wait for them to be truly gone.
_tick "  kill_and_wait active_server_wi.pl"
kill_and_wait "active_server_wi.pl"
_tick "  kill_and_wait active_dali_wi.pl"
kill_and_wait "active_dali_wi.pl"
_tick "  kill_and_wait active_user_wi.pl"
kill_and_wait "active_user_wi.pl"
_tick "  kill_and_wait done"

# 2. Kill by port as belt-and-suspenders and destroy leftover tmux session.
for _p in 3010 3011 3012 3013 3014 3015 3016 3017 3018 3019; do
    ss -tlnp "sport = :$_p" 2>/dev/null \
      | grep -oP 'pid=\K[0-9]+' \
      | xargs -r kill -9 2>/dev/null || true
done
tmux kill-session -t f1_race 2>/dev/null || true

echo "Pre-cleanup done."
_tick "END cleanup"
# ────────────────────────────────────────────────────────────────────────────
if [[ -x "$PROLOG" ]]; then
  printf "SICStus Prolog found at %s\n" "$PROLOG"
else
  printf "Error: SICStus Prolog not found at %s or is not executable.\n" "$PROLOG" >&2
  exit -1
fi

# Clean directories
_tick "START rm work/build"
rm -rf build/*
rm -f work/*  # Remove agent history
mkdir -p work/log  # Agents open log/ relative to work/ — must exist before launch
rm -rf conf/mas/*
_tick "END rm work/build"

# Convert text-based files to Unix line endings (fixes Windows CRLF).
# Only run if any file actually contains \r — skipped on clean restarts.
_tick "START dos2unix check"
if grep -rlP '\r' ./conf/ ./agents.json ./generate_agents.py ./startmas.sh 2>/dev/null | grep -q .; then
    echo "CRLF detected — running dos2unix..."
    find ./conf -type f \( -name "*.txt" -o -name "*.sh" -o -name "*.con" -o -name "*.pl" \) \
        -exec dos2unix {} \; 2>/dev/null \
      || find ./conf -type f \( -name "*.txt" -o -name "*.sh" -o -name "*.con" -o -name "*.pl" \) \
        | xargs sed -i 's/\r//'
    dos2unix ./agents.json ./generate_agents.py ./startmas.sh 2>/dev/null || true
else
    echo "No CRLF found — skipping dos2unix."
fi
_tick "END dos2unix check"

# Generate agent files from agents.json.
# Skip if agents.json has NOT changed since types were last generated.
echo "════════════════════════════════════════════════"
echo " Generating agents from: $(realpath ./agents.json)"
echo "════════════════════════════════════════════════"
_tick "START generate_agents.py"
if command -v python3 &>/dev/null; then
    python3 "./generate_agents.py" --config "./agents.json" \
        || { echo "ERROR: generate_agents.py failed" >&2; exit 1; }
    echo "Agent generation complete."
    echo "════════════════════════════════════════════════════════"
else
    echo "WARNING: python3 not found — skipping agent generation (using existing type files)."
fi
_tick "END generate_agents.py"

# Build agents based on instances
_tick "START build instances loop"
for instance_filename in $INSTANCES_HOME/*.txt; do
    type=$(tr -d '\r' < "$instance_filename")  # Agent type name (strip Windows \r)
    type_filename="$TYPES_HOME/$type.txt"
    echo "Instance: " $instance_filename " of type: " $type_filename
    instance_base="${instance_filename##*/}"  # Extract instance base name
    cat "$type_filename" >> "$BUILD_HOME/$instance_base"
done

_tick "END build instances loop"
ls $BUILD_HOME
cp $BUILD_HOME/*.txt work
_tick "END cp to work"

# Wait until a tmux pane has produced at least one line of output.
# Usage: wait_for_pane <session:window> [timeout_seconds]
# Falls back gracefully after timeout (default 10s).
wait_for_pane() {
    local target="$1"
    local timeout="${2:-10}"
    local elapsed=0
    while [ "$elapsed" -lt "$timeout" ]; do
        local content
        content=$(tmux capture-pane -pt "$target" 2>/dev/null | tr -d '[:space:]')
        [ -n "$content" ] && return 0
        sleep 0.3
        elapsed=$((elapsed + 1))
    done
    echo "WARNING: pane '$target' had no output after ${timeout}s — continuing anyway." >&2
}

# Start the LINDA server in a new console
_tick "START tmux server (port $LINDA_PORT)"
srvcmd="$PROLOG --noinfo -l $DALI_HOME/active_server_wi.pl --goal go($LINDA_PORT,'server.txt')."
echo "server: " $srvcmd
tmux new-session -d -s f1_race -n "server" $srvcmd
_tick "END tmux server launched"

# Wait until the LINDA server is actually listening on port 3010.
# Use ss -tnlp (LISTEN state only) instead of nc -z:
#   - no TCP connection is established → no extra TIME_WAIT sockets
#   - purely reads the kernel socket table (no special perms needed)
echo "Waiting for LINDA server on port $LINDA_PORT..."
_tick "START ss LINDA wait"
for i in $(seq 1 150); do
    if ss -tnlp "sport = :$LINDA_PORT" 2>/dev/null | grep -q "LISTEN"; then
        _tick "END ss LINDA ready"
        echo "LINDA server is ready (after $((i * 2 / 10)).$((i * 2 % 10))s)."
        break
    fi
    sleep 0.2
    if [ $i -eq 150 ]; then
        _tick "ERROR: ss LINDA timeout — dumping server pane"
        tmux capture-pane -pt f1_race:server -S -50 2>/dev/null | tail -20 | while IFS= read -r l; do _tick "  [server] $l"; done
        _tick "ABORT: LINDA did not start within 30 s"
        echo "ERROR: LINDA server did not start within 30 seconds. Aborting." >&2
        exit 1
    fi
done

# Start user agent in a new window
_tick "START user agent"
tmux new-window -t f1_race -n "user" "$PROLOG --noinfo -l $DALI_HOME/active_user_wi.pl --goal utente."
echo "Launching agents instances..."
wait_for_pane f1_race:user  # Let the user agent initialise before launching real agents
_tick "END wait_for_pane user"

# ── Start semaphore FIRST so it is listening before the other agents send ready ──
echo "Starting semaphore agent first..."
_tick "START semaphore"
$current_dir/conf/makeconf.sh semaphore.txt $DALI_HOME
tmux new-window -t f1_race -n "semaphore" "$current_dir/conf/startagent.sh semaphore.txt $PROLOG $DALI_HOME"
wait_for_pane f1_race:semaphore  # Give it time to fully initialise before the race agents start
_tick "END wait_for_pane semaphore"

# ── Launch the remaining agents (skip semaphore — already started) ────────────
for agent_filename in $BUILD_HOME/*; do
    agent_base="${agent_filename##*/}"
    agent_name="${agent_base%.*}"   # strip .txt for window name
    [ "$agent_name" = "semaphore" ] && continue   # already started above
    echo "Agent: $agent_base"
    _tick "  START agent $agent_name"
    $current_dir/conf/makeconf.sh $agent_base $DALI_HOME
    tmux new-window -t f1_race -n "$agent_name" "$current_dir/conf/startagent.sh $agent_base $PROLOG $DALI_HOME"
    wait_for_pane "f1_race:$agent_name"
    _tick "  END wait_for_pane $agent_name"
done

_tick "DONE — MAS started"
echo "MAS started."

# Attach to the session only when running interactively (not when launched by the UI)
if [ -t 0 ]; then
    tmux attach -t f1_race
    echo "Press Enter to shutdown the MAS"
    read
    # Clean up processes
    killall sicstus
fi
