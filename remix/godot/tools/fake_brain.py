#!/usr/bin/env python3
"""
fake_brain.py: a stand-in for the Python brain, to try Remix's face without
Jev/LLM/TTS. Speaks protocol v2 (see PROTOCOL.md).

    pip install websockets
    python3 tools/fake_brain.py            # loops a demo forever (Ctrl-C to stop)
    python3 tools/fake_brain.py --once     # one pass, then exit 0 (used by tests/e2e)

Run it, then start Remix (godot --path . or the exported app).
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from websockets.asyncio.server import serve

WAV = str((Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "beeps.wav"))
result = {"ready": None, "perform_done": []}


async def send(ws, **msg):
    msg.setdefault("v", 2)
    await ws.send(json.dumps(msg))


async def recv_until(ws, kind, timeout):
    async def loop():
        async for raw in ws:
            m = json.loads(raw)
            if m.get("type") == kind:
                return m
    return await asyncio.wait_for(loop(), timeout)


async def demo(ws, once):
    ready = await recv_until(ws, "ready", 5)
    result["ready"] = ready
    poses = ready["poses"]
    result["eyes"] = ready.get("eyes", [])
    result["mouths"] = ready.get("mouths", [])
    print("Remix is ready. poses:", poses, "eyes:", result["eyes"], "mouths:", result["mouths"])
    while True:
        # 1) silent poses with gaze
        for i, p in enumerate(poses):
            await send(ws, type="pose", pose=p, look=[(-1) ** i * 0.8, -0.3])
            await asyncio.sleep(0.6)
        # 1b) eyes and mouth mixed freely (every eye shape against a rotating mouth)
        for i, e in enumerate(result["eyes"]):
            m = result["mouths"][(i * 2) % len(result["mouths"])]
            await send(ws, type="pose", eyes=e, mouth=m, look=[0.0, -0.2])
            await asyncio.sleep(0.5)
        # 2) a spoken line with acting: anger -> tired, then back to default
        await send(ws, type="perform", id="demo1", audio=WAV,
                   cues=[{"t": 0.0, "pose": "anger", "look": [0.0, 0.0]},
                         {"t": 0.5, "pose": "tired", "look": [0.5, 0.4]}],
                   then={"pose": "default"})
        done = await recv_until(ws, "perform_done", 6)
        result["perform_done"].append(done)
        print("perform_done:", done)
        # 3) the little extras
        await send(ws, type="listening", state="start"); await asyncio.sleep(0.5)
        await send(ws, type="listening", state="stop")
        await send(ws, type="twitch")
        await send(ws, type="transcript", text="this is what the mic heard")
        await send(ws, type="notice", text="// you're muted //", seconds=1.5)
        await send(ws, type="mouth", level=0.7); await asyncio.sleep(0.4)
        await send(ws, type="theme", color="#ff5148")
        await asyncio.sleep(2.0)
        if once:
            return


async def handler(ws, once, stop):
    try:
        await demo(ws, once)
    except asyncio.TimeoutError:
        print("TIMEOUT waiting for Remix", file=sys.stderr)
        result["timeout"] = True
    finally:
        if once:
            stop.set()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    stop = asyncio.Event()
    async with serve(lambda ws: handler(ws, a.once, stop), "localhost", a.port):
        print(f"fake brain on ws://localhost:{a.port}")
        if a.once:
            try:
                await asyncio.wait_for(stop.wait(), 60)
            except asyncio.TimeoutError:
                result["timeout"] = True
            ok = (result["ready"] and result["perform_done"]
                  and result["perform_done"][0].get("ok") and not result.get("timeout"))
            print("FAKE_BRAIN_" + ("OK" if ok else "FAILED"))
            sys.exit(0 if ok else 1)
        else:
            await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
