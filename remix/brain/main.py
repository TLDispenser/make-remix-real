# main.py — asyncio entrypoint. Protocol v2 (see godot/PROTOCOL.md).
# Silent life: vitals -> RuleBrain ($0, no Jev). Typed input (terminal REPL
# or Godot typing) -> reaction pose -> GLM words -> muted perform + subtitle
# notice. Voice + persistent memory land in later phases.
import asyncio
import time

from . import config, rules, speech, vitals, ws_server

_rule: rules.RuleBrain | None = None
_seq = 0


async def body_loop(rule: rules.RuleBrain) -> None:
    """The nervous system: vitals stream -> RuleBrain -> v2 messages."""
    async for snap in vitals.stream():
        await ws_server.send({"type": "vitals", **snap})
        for msg in rule.update(snap):
            await ws_server.send(msg)


# ------------------------------------------------------- typed input pipeline

async def _on_user_text(m: dict) -> None:
    global _seq
    text = str(m.get("text", "")).strip()
    if not text:
        return

    # Instant reaction: eyes up toward the typist, before any words exist.
    await ws_server.send({"type": "pose", "pose": "default",
                          "look": [0.0, -0.15]})

    reply = await speech.generate_reply(text)
    if reply is None:
        text_line = ("// heart present, words absent: no llm key //"
                 if not config.LLM_API_KEY else
                 "// brain busy (upstream overloaded). nudge me again //")
        await ws_server.send({"type": "notice", "text": text_line,
                              "seconds": 4.0})
        print("remix-brain: no reply available for a typed line")
        return

    # Beat acting (local, $0 — Jev takes over this role once keyed).
    beat_list = speech.beats(reply)
    cues = [{"t": 0.0, "pose": "default", "look": [0.0, 0.0]}]
    look_pool = [[-0.35, 0.1], [0.35, -0.1], [0.0, 0.2]]
    for i, _beat in enumerate(beat_list):
        cues.append({"t": 0.0 if i == 0 else float(i),
                     "pose": "smirk" if i % 2 else "default",
                     "look": look_pool[i % len(look_pool)]})
    _seq += 1
    await ws_server.send({"type": "perform", "id": f"t{_seq}", "cues": cues,
                          "then": {"pose": "default"}})
    # Subtitle: notice is the ONLY text channel (godot contract). No espeak
    # yet (Phase 7), so words ride subtitles while cues act them out.
    secs = min(14.0, max(3.5, len(reply) * 0.045))
    await ws_server.send({"type": "notice", "text": reply, "seconds": secs})
    print(f"remix-brain: reply ready ({len(beat_list)} beats, "
          f"{len(reply)} chars) -> perform t{_seq}")


async def main() -> None:
    started = time.monotonic()
    global _rule
    _rule = rules.RuleBrain()
    ws_server.on_ready(lambda m: _rule.set_available_poses(m.get("poses") or []))
    ws_server.on_perform_done(lambda m: _rule.pause_for_speech(0.6))
    ws_server.on_user_text(lambda m: asyncio.create_task(_on_user_text(m)))
    body_task = asyncio.create_task(body_loop(_rule))
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
