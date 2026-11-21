#!/usr/bin/env bash
# talk.sh — one command to talk to Remix. Ensures the brain is running
# (starts it if not; log: remix/brain/brain.log), then opens the chat REPL.
# Her replies show on her face (she must be displayed) AND echo here.
# Ctrl-C / "exit" closes the chat; the brain keeps running.
set -euo pipefail
cd "$(dirname "$0")"                      # remix/
PY="./.venv/bin/python"
HOST="127.0.0.1"; PORT=8765

port_up() { timeout 1 bash -c "echo > /dev/tcp/${HOST}/${PORT}" 2>/dev/null; }

if ! port_up; then
  echo "brain isn't running — starting it (log: remix/brain/brain.log)..."
  nohup "$PY" -u -m brain.main >> brain/brain.log 2>&1 &
  for _ in $(seq 1 20); do
    sleep 0.5
    port_up && break
  done
  port_up || { echo "brain failed to start — see remix/brain/brain.log"; exit 1; }
fi

exec "$PY" -u brain/tools/terminal.py "$@"
