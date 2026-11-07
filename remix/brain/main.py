# main.py — asyncio entrypoint. Protocol v2 (see godot/PROTOCOL.md):
# vitals -> RuleBrain pose decisions (silent life; $0, no Jev calls).
# Phases 4+ add perform (LLM words + beats + TTS audio) on top of this.
import asyncio
import time

from . import config, rules, vitals, ws_server


async def body_loop() -> None:
    """The nervous system: vitals stream -> RuleBrain -> v2 messages."""
    rule = rules.RuleBrain()
    ws_server.on_ready(lambda m: rule.set_available_poses(m.get("poses") or []))
    ws_server.on_perform_done(lambda m: rule.pause_for_speech(0.6))
    async for snap in vitals.stream():
        await ws_server.send({"type": "vitals", **snap})
        for msg in rule.update(snap):
            await ws_server.send(msg)


async def main() -> None:
    started = time.monotonic()
    body_task = asyncio.create_task(body_loop())
    try:
        await ws_server.serve()  # runs forever
    except KeyboardInterrupt:
        pass
    except OSError as e:
        if getattr(e, "errno", None) == 98:  # EADDRINUSE
            print(f"remix-brain: FATAL — port {config.WS_PORT} already in use "
                  f"(is another brain running?) 'pkill -f brain.main' then retry.")
            return
        print(f"remix-brain: FATAL — {e!r}")
    finally:
        body_task.cancel()
        print(f"remix-brain: stopped after {time.monotonic() - started:.0f}s")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
