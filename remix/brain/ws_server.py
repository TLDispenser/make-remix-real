# ws_server.py — protocol v2 bridge (see godot/PROTOCOL.md). brain -> Godot.
# Python hosts ws://127.0.0.1:8765; Godot connects and sends `ready` (poses)
# and `perform_done`. Brain broadcasts pose/perform/notice/... messages.
# API shapes verified 2026-11-05 (websockets 17.x): handler takes ONE
# connection arg, serve() from websockets.asyncio.server.
import asyncio
import json
from typing import Callable, Optional, Set

from websockets.asyncio.server import (
    ServerConnection,
    broadcast as _broadcast,
    serve as _ws_serve,
)

from . import config

PROTOCOL_VERSION = 2
_clients: Set[ServerConnection] = set()

# Single-callback hooks (main.py wires them; None = ignore).
_cb_ready: Optional[Callable[[dict], None]] = None
_cb_perform_done: Optional[Callable[[dict], None]] = None
_cb_user_text: Optional[Callable[[dict], None]] = None


def on_ready(cb: Callable[[dict], None]) -> None:
    global _cb_ready
    _cb_ready = cb


def on_perform_done(cb: Callable[[dict], None]) -> None:
    global _cb_perform_done
    _cb_perform_done = cb


def on_user_text(cb: Callable[[dict], None]) -> None:
    """Text from the terminal REPL (tools/terminal.py) or Godot typing."""
    global _cb_user_text
    _cb_user_text = cb


def _wrap(msg: dict) -> str:
    payload = {"v": PROTOCOL_VERSION, **msg}
    return json.dumps(payload, ensure_ascii=False)


async def _handle_incoming(raw: str) -> None:
    try:
        m = json.loads(raw)
    except (TypeError, ValueError):
        return
    kind = m.get("type")
    if kind == "ready" and _cb_ready:
        _cb_ready(m)
    elif kind == "perform_done" and _cb_perform_done:
        _cb_perform_done(m)
    elif kind == "user_text" and _cb_user_text:
        _cb_user_text(m)  # long pipelines MUST schedule their own task
    # future kinds (user_typing / ui) land here.


async def _handler(websocket: ServerConnection) -> None:
    _clients.add(websocket)
    try:
        async for raw in websocket:
            await _handle_incoming(raw)
    except Exception:
        pass
    finally:
        _clients.discard(websocket)


def _valid_writer(conn: ServerConnection) -> bool:
    """websockets 17 marks terminated connections via .closed."""
    return getattr(conn, "closed", None) is None


async def send(msg: dict) -> None:
    """Best-effort send to every live Godot connection."""
    if not _clients:
        return
    try:
        live = {c for c in _clients if _valid_writer(c)}
        if live:
            await _broadcast(live, _wrap(msg))
    except Exception:
        pass


async def serve() -> None:
    async with _ws_serve(_handler, config.WS_HOST, config.WS_PORT,
                     max_size=None, ping_interval=20):
        print(f"remix-brain: ws server on ws://{config.WS_HOST}:{config.WS_PORT} (v{PROTOCOL_VERSION})")
        await asyncio.Future()
