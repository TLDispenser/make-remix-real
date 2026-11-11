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

# One callback per incoming message type (main.py wires them; none = ignore).
# Types: ready, perform_done, context, user_text, user_typing, ptt.
_handlers: dict = {}


def on(kind: str, cb: Callable[[dict], None]) -> None:
    _handlers[kind] = cb


def on_ready(cb): on("ready", cb)
def on_perform_done(cb): on("perform_done", cb)
def on_user_text(cb):
    """Text from the terminal REPL (tools/terminal.py) or the Godot input bar."""
    on("user_text", cb)


def _wrap(msg: dict) -> str:
    payload = {"v": PROTOCOL_VERSION, **msg}
    return json.dumps(payload, ensure_ascii=False)


async def _handle_incoming(raw: str) -> None:
    try:
        m = json.loads(raw)
    except (TypeError, ValueError):
        return
    if not isinstance(m, dict):
        return
    cb = _handlers.get(m.get("type"))
    if cb:
        try:
            cb(m)  # long pipelines MUST schedule their own task
        except Exception as e:
            print(f"remix-brain: handler for {m.get('type')!r} raised {e!r}")


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
    # origins=[None]: ONLY clients that send no Origin header (Godot's
    # WebSocketPeer never does). Any web page in any browser DOES send one,
    # so a random site the user visits cannot open a socket to 127.0.0.1:8765
    # and drive her — and through `user_text` reach the mic pipeline. This is
    # a DNS-rebinding/cross-site-WebSocket wall, not auth; everything still
    # binds to localhost.
    # max_size: 1 MiB per inbound frame is ~100x our largest legal message
    # (a push-to-talk partial transcript); without it a hostile/buggy peer
    # could balloon the brain's memory with one frame.
    async with _ws_serve(_handler, config.WS_HOST, config.WS_PORT,
                     origins=[None], max_size=1_048_576,
                     ping_interval=20):
        print(f"remix-brain: ws server on ws://{config.WS_HOST}:{config.WS_PORT} (v{PROTOCOL_VERSION})")
        await asyncio.Future()
