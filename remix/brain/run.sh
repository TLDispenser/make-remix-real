#!/usr/bin/env bash
# Runs the Python brain. Mock feed is ON until Phase 2 (REMIX_MOCK env).
set -euo pipefail
cd "$(dirname "$0")/.."   # remix/ — so that "brain" is importable as a package
# Rotate brain.log (keep one old copy) before the append redirect pins it.
log="brain/brain.log"
if [ -f "$log" ] && [ "$(wc -c < "$log")" -gt 2097152 ]; then
  mv "$log" "$log.1"
fi
exec ./.venv/bin/python -m brain.main "$@"
