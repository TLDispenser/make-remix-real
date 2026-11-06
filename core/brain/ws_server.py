# ws_server.py — protocol v1 bridge. Python"brain" -> Godot"display".
# Shapes verified 2026-11-05 (websockets 17.x modern asyncio API):
#   handler takes exactly ONE connection argument, serve() from websockets.asyncio.server
import asyncio
import json
from typing import Set

from websockets.asyncio.server import (  # noqa: E501 -- `serve` is alias-imported so our own serve() can't shadow it
    ServerConnection,
    broadcast as _broadcast,
    serve as _ws_serve,
)

from . import config

PROTOCOL_VERSION = 1
_clients: Set[ServerConnection] = set()


def _wrap(msg: dict) -> str:
    payload = {"v": PROTOCOL_VERSION, **msg}
    return json.dumps(payload, ensure_ascii=False)


async def _handler(websocket: ServerConnection) -> None:
    _clients.add(websocket)
    try:
        # v1: Godot -> Python (user_text / user_typing / ui) is Phase 4+;
        # consume so the socket stays healthy, log only.
        async for raw in websocket:
            print(f"[ws] godot -> brain: {raw}")
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
        _broadcast(_clients, _wrap(msg))
    except Exception:
        pass


async def serve() -> None:
    async with _ws_serve(_handler, config.WS_HOST, config.WS_PORT,
                     max_size=None, ping_interval=20):
        print(f"core-brain: ws server on ws://{config.WS_HOST}:{config.WS_PORT} (v{PROTOCOL_VERSION})")
        await asyncio.Future()
