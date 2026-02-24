# ── MAS container ─────────────────────────────────────────────────────────────
# Provides the runtime for DALI/SICStus agents managed via tmux.
# SICStus Prolog is NOT bundled here — it is mounted read-only at runtime
# via the SICSTUS_PATH env variable (see docker-compose.yml / .env).
#
# Build context: DALI/ root (set in docker-compose.yml)
# ──────────────────────────────────────────────────────────────────────────────

FROM ubuntu:22.04

# Avoid interactive prompts during apt
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        tmux            \
        python3         \
        python3-pip     \
        dos2unix        \
        netcat-openbsd  \
    && rm -rf /var/lib/apt/lists/*

# All project files are bind-mounted at runtime (see docker-compose.yml).
# Working dir mirrors the path inside the mounted DALI/ tree so that relative
# paths in startmas.sh (../../src, etc.) resolve correctly.
WORKDIR /dali/Examples/f1_race

# tmux will store its socket here; the UI container mounts the same volume
# so capture-pane works across containers with no code changes.
ENV TMUX_TMPDIR=/tmp/tmux-shared

CMD ["bash", "startmas.sh"]
