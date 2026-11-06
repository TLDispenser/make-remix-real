#!/usr/bin/env bash
# Runs the Python brain. Mock feed is ON until Phase 2 (CORE_MOCK env).
set -euo pipefail
cd "$(dirname "$0")/.."   # remix/ — so that "brain" is importable as a package
exec ./.venv/bin/python -m brain.main "$@"
