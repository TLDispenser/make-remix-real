#!/usr/bin/env python3
"""selftest.py: connects to the real remix brain AS a fake Godot face and
asserts the protocol v2 stream is sane. Run with the brain live:

    remix/brain/run.sh &           # server
    python3 remix/brain/tools/selftest.py [seconds=8]

Real machines sit idle, so organic pose changes are not required here: the
pose-change path is validated by the synthetic-feed unit tests instead.
This checks the stream is alive and well-formed: >=2 vitals, >=1 idle-life
message (look/twitch/notice), valid pose names if any arrive, and no pose
spam (<= 1 change per second on average). Exit 0 on pass, 1 on fail.
"""
import asyncio
import json
import os
import sys
import time

from websockets.asyncio.client import connect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

try:
    from brain import faces  # noqa: E402
    KNOWN = set(faces.POSES)
    EYES = list(faces.EYES_TEXT)
    MOUTHS = list(faces.MOUTHS_TEXT)
except ImportError:
    KNOWN = {
        "default", "anger", "smirk", "pout", "spooked", "tired", "tsun",
        "bashful", "thunk", "scribble", "strange", "smug",
    }
    EYES = sorted(KNOWN)
    MOUTHS = ["default", "anger", "smirk", "pout", "trauma", "tsun", "none",
              "bashful", "strange", "smug"]

_HOST = os.environ.get("REMIX_WS_HOST") or os.environ.get("CORE_WS_HOST") or "127.0.0.1"
_PORT = os.environ.get("REMIX_WS_PORT") or os.environ.get("CORE_WS_PORT") or "8765"
URL = f"ws://{_HOST}:{_PORT}"


async def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
    problems: list[str] = []
    poses: list[str] = []
    looks = twitches = notices = n_vitals = 0
    deadline = time.monotonic() + seconds
    async with connect(URL) as ws:
        await ws.send(json.dumps({
            "v": 2, "type": "ready",
            "poses": sorted(KNOWN), "eyes": EYES, "mouths": MOUTHS,
            "moods": []}))
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except TimeoutError:
                continue
            m = json.loads(raw)
            kind = m.get("type")
            if kind == "vitals":
                n_vitals += 1
            elif kind == "pose":
                poses.append(m.get("pose", "?"))
            elif kind == "look":
                looks += 1
            elif kind == "twitch":
                twitches += 1
            elif kind == "notice":
                notices += 1
    print(f"vitals={n_vitals} poses={len(poses)} names={poses} "
          f"looks={looks} twitches={twitches} notices={notices}")
    if n_vitals < 2:
        problems.append("no vitals stream")
    bad = [p for p in poses if p not in KNOWN]
    if bad:
        problems.append(f"unknown pose names: {bad}")
    if len(poses) > seconds:  # >1 change/sec = the debounce is broken
        problems.append("pose changes too frequent (debounce broken)")
    if not (looks + twitches + notices):
        problems.append("no idle-life messages (look/twitch/notice)")
    for p in problems:
        print("FAIL:", p)
    print("BRAIN_SELFTEST", "OK" if not problems else "FAILED")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
