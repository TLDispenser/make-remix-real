#!/usr/bin/env bash
# settings.sh — open Remix's settings. Uses the TUI (brain/settings_tui.py)
# when textual is in the venv; otherwise falls back to the plain no-dep
# menu (brain/settings.py), which works over SSH without any install.
#   ./settings.sh            pick the best UI available
#   ./settings.sh plain      force the no-dep menu
#   ./settings.sh tui        force the TUI (errors if textual is missing)
set -euo pipefail
cd "$(dirname "$0")"                      # remix/
PY="${REMIX_PY:-./.venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
MODE="${1:-auto}"

if [ "$MODE" = "plain" ]; then
  exec "$PY" -m brain.settings
fi

if "$PY" -c "import textual" 2>/dev/null; then
  exec "$PY" -m brain.settings_tui
fi

if [ "$MODE" = "tui" ]; then
  echo "the TUI needs textual in the venv: $PY -m pip install 'textual==8.2.8'"
  exit 1
fi

echo "(textual not installed — plain menu. For the full TUI:"
echo " $PY -m pip install 'textual==8.2.8')"
exec "$PY" -m brain.settings
