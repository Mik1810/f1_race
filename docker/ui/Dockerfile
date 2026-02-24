# ── UI container ──────────────────────────────────────────────────────────────
# Serves the Flask dashboard that reads tmux panes via the shared socket volume.
#
# Build context: f1_race/ (set in docker-compose.yml)
# ──────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# tmux client needed to run capture-pane against the shared socket
# curl needed for the healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends tmux curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies at build time (requirements.txt is small/stable)
COPY ui/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Project files are bind-mounted at runtime; working dir inside the same tree.
WORKDIR /dali/Examples/f1_race/ui

# Same TMUX_TMPDIR as the MAS container → tmux uses the shared socket volume
ENV TMUX_TMPDIR=/tmp/tmux-shared

EXPOSE 5000

CMD ["python3", "dashboard.py"]
