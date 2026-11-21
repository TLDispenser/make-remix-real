# shell_hook.py — Phase 6 zsh preexec/precmd unix-socket receiver.
# Opt-in (REMIX_SHELL_HOOK=1): reports long or failed jobs to Proactive.
from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

from . import awareness, config

_shell_edges: list = []
_shell_lock = threading.Lock()


def drain_edges() -> list:
    with _shell_lock:
        out, _shell_edges[:] = _shell_edges[:], []
        return out


def parse_edge(line: str) -> dict | None:
    """Parse one hook line (`cmd\\trc\\tduration`) into a plain-fact edge."""
    cmd, _, rest = (line or "").partition("\t")
    rc_s, _, dur_s = rest.partition("\t")
    try:
        rc = int((rc_s or "0").strip())
        dur = float((dur_s or "0").strip())
    except ValueError:
        return None
    if dur < config.SHELL_MIN_S:
        return None
    cmd = awareness.redact_text(cmd)[:config.SHELL_MAX_CMD].strip()
    if not cmd:
        cmd = "a command whose name died in the scrub"
    return {
        "trigger": "shell_job",
        "fact": (
            f"they just ran `{cmd}` in the shell; it ran "
            f"{max(1, round(dur))}s and "
            + ("FAILED" if rc != 0 else "finished fine")
        ),
    }


async def handle_conn(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    try:
        raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
        edge = parse_edge(raw.decode("utf-8", "replace").strip())
    except Exception:
        edge = None
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
    if edge:
        with _shell_lock:
            _shell_edges.append(edge)


async def serve_forever() -> None:
    """Listen on a 0600 unix socket in the runtime dir for zsh hook reports."""
    if not config.SHELL_HOOK:
        print("remix-brain: shell hook off (REMIX_SHELL_HOOK=1 to enable)")
        return
    sock = Path(str(config.SHELL_SOCK))
    try:
        sock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if sock.exists():
            sock.unlink()
        server = await asyncio.start_unix_server(handle_conn, path=str(sock))
        os.chmod(sock, 0o600)
    except OSError as e:
        print(f"remix-brain: shell socket failed ({e!r})")
        return
    print(f"remix-brain: shell hook listening on {sock}")
    try:
        async with server:
            await server.serve_forever()
    finally:
        try:
            sock.unlink(missing_ok=True)
        except OSError:
            pass
