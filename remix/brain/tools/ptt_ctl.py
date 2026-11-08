#!/usr/bin/env python3
# ptt_ctl.py — one-shot push-to-talk control client (Phase 5).
# Your global hotkey runs this twice per press:
#   keydown -> `ptt_ctl.py start`   keyup -> `ptt_ctl.py stop`
# Sends a tiny UDP datagram to the running brain's control socket. No
# daemon here by design: 0.02s spawn, mic is opened by the brain only.
import socket
import sys

SOCK = __import__("os").environ.get(
    "XDG_RUNTIME_DIR", "/tmp") + "/remix-ptt.sock"


def main() -> int:
    action = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    if action not in ("start", "stop"):
        print("usage: ptt_ctl.py start|stop", file=sys.stderr)
        return 2
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(action.encode(), SOCK)
    except OSError as e:
        print(f"brain not reachable ({e}); is the remix brain running?",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
