# main.py — asyncio entrypoint. Phase 1 scope: body driven by mock feed.
# Phases 3/4 plug the real left/right brains into the same broadcast points.
import asyncio
import random
import time

from . import vitals, ws_server

# Placeholder mood logic so Phase 1 has something to show. Phase 3 replaces
# this with the left brain (RuleBrain first, JevBrain behind the interface).
MOOD_POOLS = {
    "low": ["sleepy", "bored", "idle"],
    "mid": ["idle", "happy", "thinking"],
    "high": ["stressed", "alert"],
}
MOCK_LINES = [
    "Ow, easy on the CPU.",
    "All quiet on the driver front.",
    "These transistors are my favorite weather.",
    "I can feel the fans spinning up.",
]


def _mock_mood(snap: dict) -> str:
    cpu = snap.get("cpu", 0)
    if cpu < 25:
        return random.choice(MOOD_POOLS["low"])
    if cpu > 75:
        return random.choice(MOOD_POOLS["high"])
    return random.choice(MOOD_POOLS["mid"])


async def body_loop() -> None:
    """The nervous system: get vitals, broadcast them + a mood placeholder."""
    async for snap in vitals.stream():
        await ws_server.send({"type": "vitals", **snap})
        mood = _mock_mood(snap) if snap.get("cpu") is not None else "idle"
        await ws_server.send({"type": "mood", "mood": mood,
                              "confidence": 1.0, "source": "mock"})
        if random.random() < 0.05:  # occasional reflex line, keeps bubble alive
            await ws_server.send({"type": "reflex_line",
                                  "text": random.choice(MOCK_LINES)})


async def main() -> None:
    started = time.monotonic()
    body_task = asyncio.create_task(body_loop())
    try:
        await ws_server.serve()  # runs forever
    except KeyboardInterrupt:
        pass
    finally:
        body_task.cancel()
        print(f"core-brain: stopped after {time.monotonic() - started:.0f}s")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
