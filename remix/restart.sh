#!/usr/bin/env bash
# restart.sh — (re)start Remix: the Python brain AND the Godot face, together.
#   ./restart.sh           stop anything running, start brain, then the face
#   ./restart.sh stop      just stop both
#   ./restart.sh brain     restart only the brain (leave the face open)
#   ./restart.sh face      restart only the face
# Env:  GODOT_BIN=/path/to/godot   (default: godot on PATH)
#       REMIX_X11=1                run Godot under XWayland (always-on-top +
#                                  click-through on Hyprland; see README)
# Logs: brain/brain.log, godot/godot.log      PIDs: .run/
set -uo pipefail
cd "$(dirname "$0")"                       # remix/
ROOT="$PWD"
PY="${REMIX_PY:-./.venv/bin/python}"
GODOT="${GODOT_BIN:-godot}"
HOST=127.0.0.1; PORT=8765
RUN=.run; mkdir -p "$RUN"
MODE="${1:-all}"

port_up() { timeout 1 bash -c "echo > /dev/tcp/${HOST}/${PORT}" 2>/dev/null; }

# stop NAME PIDFILE PATTERN — SIGTERM, wait up to 8 s, then SIGKILL.
# The brain needs the grace period: it writes the journal on shutdown.
stop() {
  local name="$1" pidfile="$2" pattern="$3" pids
  pids="$(cat "$pidfile" 2>/dev/null; pgrep -f -- "$pattern" 2>/dev/null)"
  pids="$(echo "$pids" | tr ' ' '\n' | grep -E '^[0-9]+$' | grep -vx "$$" | sort -u)"
  rm -f "$pidfile"
  [ -z "$pids" ] && return 0
  echo "stopping $name ($(echo $pids | tr '\n' ' '))"
  kill $pids 2>/dev/null
  for _ in $(seq 1 40); do
    sleep 0.2
    kill -0 $pids 2>/dev/null || return 0
  done
  echo "$name ignored SIGTERM, killing"
  kill -9 $pids 2>/dev/null
}

stop_brain() { stop brain "$RUN/brain.pid" '^[^ ]*python[^ ]* (-u )?-m brain\.main'; }
stop_face()  { stop face  "$RUN/godot.pid" "^[^ ]*godot[^ ]* --path $ROOT/godot"; }

start_brain() {
  [ -x "$PY" ] || { echo "no venv at $PY — create it first (see README)"; return 1; }
  nohup "$PY" -u -m brain.main >> brain/brain.log 2>&1 &
  echo $! > "$RUN/brain.pid"
  for _ in $(seq 1 40); do
    sleep 0.5
    port_up && { echo "brain up on ws://${HOST}:${PORT} (pid $(cat "$RUN/brain.pid"))"; return 0; }
    kill -0 "$(cat "$RUN/brain.pid")" 2>/dev/null || break
  done
  echo "brain failed to start — last log lines:"; tail -n 15 brain/brain.log
  return 1
}

start_face() {
  command -v "$GODOT" >/dev/null 2>&1 || { echo "can't find '$GODOT' — set GODOT_BIN=/path/to/godot"; return 1; }
  local pre=()
  [ "${REMIX_X11:-0}" = 1 ] && pre=(env -u WAYLAND_DISPLAY)
  nohup "${pre[@]}" "$GODOT" --path "$ROOT/godot" >> godot/godot.log 2>&1 &
  echo $! > "$RUN/godot.pid"
  sleep 1
  kill -0 "$(cat "$RUN/godot.pid")" 2>/dev/null \
    && echo "face launched (pid $(cat "$RUN/godot.pid"))" \
    || { echo "face exited immediately — see godot/godot.log"; return 1; }
}

case "$MODE" in
  stop)  stop_face; stop_brain ;;
  brain) stop_brain; start_brain ;;
  face)  stop_face; start_face ;;
  all)   stop_face; stop_brain; start_brain && start_face ;;
  *)     echo "usage: $0 [all|stop|brain|face]"; exit 2 ;;
esac
