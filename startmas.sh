#!/bin/bash

# Enable debugging
# set -x  # Start debugging

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
kill_and_wait "active_server_wi.pl"
kill_and_wait "active_dali_wi.pl"
kill_and_wait "active_user_wi.pl"

# 2. Kill by port as belt-and-suspenders (handles edge cases where the
#    process name doesn't match the pattern above).
ss -tlnp "sport = :$LINDA_PORT" 2>/dev/null \
  | grep -oP 'pid=\K[0-9]+' \
  | xargs -r kill -9 2>/dev/null || true

# 3. Destroy any leftover tmux session.
tmux kill-session -t f1_race 2>/dev/null || true

# 4. Wait until the port is confirmed free.
#    We check ALL socket states (LISTEN, TIME_WAIT, CLOSE_WAIT, FIN_WAIT…)
#    because SICStus bind() fails on ANY of them without SO_REUSEADDR.
echo "Waiting for port $LINDA_PORT to be completely free..."
for i in $(seq 1 30); do
    # ss -tan lists every TCP socket in any state; filter for local :LINDA_PORT.
    # If the output (minus the header line) is empty, the port is fully free.
    if [ -z "$(ss -tan "sport = :$LINDA_PORT" 2>/dev/null | tail -n +2)" ]; then
        echo "Port $LINDA_PORT is completely free (after ${i} checks)."
        break
    fi
    # Still occupied — re-kill and wait
    kill_and_wait "active_server_wi.pl"
    ss -tlnp "sport = :$LINDA_PORT" 2>/dev/null \
      | grep -oP 'pid=\K[0-9]+' \
      | xargs -r kill -9 2>/dev/null || true
    sleep 1
    if [ "$i" -eq 30 ]; then
        echo "WARNING: port $LINDA_PORT still in use after 30 s — proceeding anyway." >&2
    fi
done
echo "Pre-cleanup done."
# ────────────────────────────────────────────────────────────────────────────
if [[ -x "$PROLOG" ]]; then
  printf "SICStus Prolog found at %s\n" "$PROLOG"
else
  printf "Error: SICStus Prolog not found at %s or is not executable.\n" "$PROLOG" >&2
  exit -1
fi

# Clean directories
rm -rf build/*
rm -f work/*  # Remove agent history
mkdir -p work/log  # Agents open log/ relative to work/ — must exist before launch
rm -rf conf/mas/*

# Convert text-based files to Unix line endings (fixes Windows CRLF)
# NOTE: only process conf/ and root-level files — mas/ and build/ are written
# by generate_agents.py which already outputs LF, so no need to touch them.
find ./conf -type f \( -name "*.txt" -o -name "*.sh" -o -name "*.con" -o -name "*.pl" \) \
    -exec dos2unix {} \; 2>/dev/null \
  || find ./conf -type f \( -name "*.txt" -o -name "*.sh" -o -name "*.con" -o -name "*.pl" \) \
    | xargs sed -i 's/\r//'
dos2unix ./agents.json ./generate_agents.py ./startmas.sh 2>/dev/null || true

# Generate agent files from agents.json 
echo "════════════════════════════════════════════════"
echo " Generating agents from: $(realpath ./agents.json)"
echo "════════════════════════════════════════════════"
if command -v python3 &>/dev/null; then
    python3 "./generate_agents.py" --config "./agents.json" --force \
        || { echo "ERROR: generate_agents.py failed" >&2; exit 1; }
    echo "Agent generation complete."
    echo "════════════════════════════════════════════════"
else
    echo "WARNING: python3 not found — skipping agent generation (using existing type files)."
fi

# Build agents based on instances
for instance_filename in $INSTANCES_HOME/*.txt; do
    type=$(tr -d '\r' < "$instance_filename")  # Agent type name (strip Windows \r)
    type_filename="$TYPES_HOME/$type.txt"
    echo "Instance: " $instance_filename " of type: " $type_filename
    instance_base="${instance_filename##*/}"  # Extract instance base name
    cat "$type_filename" >> "$BUILD_HOME/$instance_base"
done

ls $BUILD_HOME
cp $BUILD_HOME/*.txt work

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
srvcmd="$PROLOG --noinfo -l $DALI_HOME/active_server_wi.pl --goal go(3010,'server.txt')."
echo "server: " $srvcmd
tmux new-session -d -s f1_race -n "server" $srvcmd

# Wait until the LINDA server is actually listening on port 3010
echo "Waiting for LINDA server on port $LINDA_PORT..."
for i in $(seq 1 30); do
    if nc -z localhost $LINDA_PORT 2>/dev/null; then
        echo "LINDA server is ready (after ${i}s)."
        break
    fi
    sleep 1
    if [ $i -eq 30 ]; then
        echo "ERROR: LINDA server did not start within 30 seconds. Aborting." >&2
        exit 1
    fi
done

# Start user agent in a new window
tmux new-window -t f1_race -n "user" "$PROLOG --noinfo -l $DALI_HOME/active_user_wi.pl --goal utente."
echo "Launching agents instances..."
wait_for_pane f1_race:user  # Let the user agent initialise before launching real agents

# ── Start semaphore FIRST so it is listening before the other agents send ready ──
echo "Starting semaphore agent first..."
$current_dir/conf/makeconf.sh semaphore.txt $DALI_HOME
tmux new-window -t f1_race -n "semaphore" "$current_dir/conf/startagent.sh semaphore.txt $PROLOG $DALI_HOME"
wait_for_pane f1_race:semaphore  # Give it time to fully initialise before the race agents start

# ── Launch the remaining agents (skip semaphore — already started) ────────────
for agent_filename in $BUILD_HOME/*; do
    agent_base="${agent_filename##*/}"
    agent_name="${agent_base%.*}"   # strip .txt for window name
    [ "$agent_name" = "semaphore" ] && continue   # already started above
    echo "Agent: $agent_base"
    # Create the agent configuration
    $current_dir/conf/makeconf.sh $agent_base $DALI_HOME
    # Start the agent in a new window
    tmux new-window -t f1_race -n "$agent_name" "$current_dir/conf/startagent.sh $agent_base $PROLOG $DALI_HOME"
    wait_for_pane "f1_race:$agent_name"  # Wait until the agent has produced output
done

echo "MAS started."

# Attach to the session only when running interactively (not when launched by the UI)
if [ -t 0 ]; then
    tmux attach -t f1_race
    echo "Press Enter to shutdown the MAS"
    read
    # Clean up processes
    killall sicstus
fi
