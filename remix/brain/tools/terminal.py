#!/usr/bin/env python3
"""terminal.py: type to Remix's brain from a terminal (for the sake of it).

    python3 remix/brain/tools/terminal.py            # REPL
    python3 remix/brain/tools/terminal.py "hey there"   # one-shot

Connects to the brain's ws://127.0.0.1:8765 as a second client and sends
{"type":"user_text","text":...}. Her reply shows on her face (voice + acting,
or captions when she has no voice) AND prints here as `remix: ...`.

    /talk    open the mic (same as holding push-to-talk); press Enter to stop.
             Needs the brain's ears (faster-whisper + a recorder, see README).
"""
import asyncio
import json
import os
import sys

from websockets.asyncio.client import connect

_HOST = os.environ.get("REMIX_WS_HOST") or os.environ.get("CORE_WS_HOST") or "127.0.0.1"
_PORT = os.environ.get("REMIX_WS_PORT") or os.environ.get("CORE_WS_PORT") or "8765"
URL = f"ws://{_HOST}:{_PORT}"


async def run() -> int:
    argv_in = [a for a in sys.argv[1:] if not a.startswith("-")]
    one_shot = bool(argv_in)
    done_ev = asyncio.Event()
    async with connect(URL) as ws:

        async def reader() -> None:
            async for raw in ws:
                try:
                    m = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                kind = m.get("type")
                if kind == "say":
                    if not m.get("final", True):
                        continue
                    if one_shot:
                        print(f"remix: {m.get('text', '')}", flush=True)
                        done_ev.set()
                    else:
                        print(f"\rremix: {m.get('text', '')}\nyou> ", end="", flush=True)
                elif kind == "transcript":
                    if not one_shot:
                        print(f"\rheard: {m.get('text', '')}\nyou> ", end="", flush=True)
                elif kind == "notice":
                    t = str(m.get("text", ""))
                    if t.startswith("//") and "one sec" not in t:   # system notices only
                        if one_shot:
                            print(t, flush=True)
                            done_ev.set()
                        else:
                            print(f"\r{t}\nyou> ", end="", flush=True)
                # everything else (pose/look/perform/...) is face business; stay quiet

        task = asyncio.create_task(reader())
        if one_shot:
            text = " ".join(argv_in)
            await ws.send(json.dumps({"v": 2, "type": "user_text", "text": text}))
            print(f"you: {text}", flush=True)
            try:
                await asyncio.wait_for(done_ev.wait(), timeout=90.0)
                await asyncio.sleep(0.25)
            except TimeoutError:
                pass
            task.cancel()
            return 0
        while True:
            try:
                line = await asyncio.to_thread(input, "you> ")
            except EOFError:  # Ctrl-D
                task.cancel()
                return 0
            line = line.strip()
            if not line:
                continue
            if line.lower() in {"exit", "quit", ":q"}:
                task.cancel()
                return 0
            if line.lower() == "/talk":
                await ws.send(json.dumps({"v": 2, "type": "ptt", "state": "start"}))
                print("(mic open - speak, then press Enter to stop)")
                await asyncio.to_thread(input, "")
                await ws.send(json.dumps({"v": 2, "type": "ptt", "state": "stop"}))
                continue
            await ws.send(json.dumps({"v": 2, "type": "user_text",
                                      "text": line}))
            print(f"\ryou: {line}\nyou> ", end="", flush=True)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run()))
    except KeyboardInterrupt:
        sys.exit(0)
