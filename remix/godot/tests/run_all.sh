#!/usr/bin/env bash
# Run every Remix Godot test headless. Usage:  tests/run_all.sh [path/to/godot]
# Needs: godot 4.7, python3 + `pip install websockets` (for the e2e test).
set -u
GODOT="${1:-godot}"
cd "$(dirname "$0")/.."
fail=0
run() {  # name, script
        out=$("$GODOT" --headless --path . --script "res://tests/$2" 2>&1)
        echo "$out" | grep -E "^(FAIL|[A-Z0-9_]+_(OK|FAILED))" || true
        if echo "$out" | grep -qE "FAILED|^FAIL |SCRIPT ERROR|^ERROR:"; then echo "$out" | grep -E "SCRIPT ERROR|^ERROR:|^FAIL " | head -5; fail=1; fi
}
"$GODOT" --headless --path . --import >/dev/null 2>&1
run shapes shapes_audit.gd
run engine engine_test.gd
run talk talk_test.gd
run protocol protocol_test.gd
run perform perform_test.gd
# end to end: fake brain + real Godot
python3 tools/fake_brain.py --once > /tmp/remix_fake_brain.log 2>&1 &
BRAIN=$!
sleep 1
run e2e e2e_test.gd
wait $BRAIN || { echo "fake brain reported failure:"; tail -5 /tmp/remix_fake_brain.log; fail=1; }
tail -1 /tmp/remix_fake_brain.log
[ $fail -eq 0 ] && echo "ALL GREEN" || { echo "SOMETHING FAILED"; exit 1; }
