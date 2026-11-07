#!/usr/bin/env python3
"""terminal.py: type to Remix's brain from a terminal (for the sake of it).

    python3 remix/brain/tools/terminal.py            # REPL
    python3 remix/brain/tools/terminal.py "hey there"   # one-shot

Connects to the brain's ws://127.0.0.1:8765 as a second client and sends
{"type":"user_text","text":...}. The brain's reply surfaces ON HER FACE as a
subtitle notice + acting cues (Protocol v2); this REPL prints notices and
perform_done acks it receives back.
"""
import asyncio
import json
import sys

from websockets.asyncio.client import connect

URL = "ws://127.0.0.1:8765"


async def run() -> int:
    argv_in = [a for a in sys.argv[1:] if not a.startswith("-")]
    one_shot = bool(argv_in)
    async with connect(URL) as ws:

        async def reader() -> None:
            async for raw in ws:
                try:
                    m = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                kind = m.get("type")
                if kind == "notice":
                    print(f"remix says: {m.get('text', '')}")
                elif kind == "perform_done":
                    print(f"[perform {m.get('id')} ok={m.get('ok', True)}]")
                # everything else (pose/look/...) is face business; stay quiet

        task = asyncio.create_task(reader())
        if one_shot:
            for text in argv_in:
                await ws.send(json.dumps({"v": 2, "type": "user_text",
                                          "text": text}))
                print(f"you: {text}")
            await asyncio.sleep(90)  # let reply + perform land (429 backoff)
            task.cancel()
            return 0
        while True:
            line = await asyncio.to_thread(input, "you> ")
            line = line.strip()
            if not line:
                continue
            if line.lower() in {"exit", "quit", ":q"}:
                task.cancel()
                return 0
            await ws.send(json.dumps({"v": 2, "type": "user_text",
                                      "text": line}))
            print("(sent — watch her face)")


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run()))
    except KeyboardInterrupt:
        sys.exit(0)
