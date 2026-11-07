# main.py — asyncio entrypoint. Protocol v2 (see godot/PROTOCOL.md).
# Silent life: vitals -> RuleBrain ($0, no Jev). Typed input (terminal REPL
# or Godot typing) -> reaction pose -> GLM words -> muted perform + subtitle
# notice. Voice + persistent memory land in later phases.
import asyncio
import time

from . import config, jev, rules, speech, vitals, ws_server

_rule: rules.RuleBrain | None = None
_jev: jev.JevBrain | None = None
_seq = 0
_latest_snapshot: dict = {}


async def body_loop(rule: rules.RuleBrain) -> None:
    """The nervous system: vitals stream -> RuleBrain -> v2 messages."""
    global _latest_snapshot
    async for snap in vitals.stream():
        _latest_snapshot = snap
        await ws_server.send({"type": "vitals", **snap})
        for msg in rule.update(snap):
            await ws_server.send(msg)


# ------------------------------------------------------- typed input pipeline

async def _on_user_text(m: dict) -> None:
    global _seq
    text = str(m.get("text", "")).strip()
    if not text:
        return

    # She's visibly alive while upstream thinks (storms can last ~1 min).
    await ws_server.send({"type": "notice", "text": "// one sec //",
                          "seconds": 3.0})

    # Left brain: ONE Jev call -> stance + instant pose + beat arc. Any
    # budget/exhaustion/failure means None and we fall back to the $0 path.
    decision = None
    if _jev is not None:
        decision = await _jev.decide(text, _latest_snapshot)
    if decision:
        await ws_server.send({"type": "pose", "pose": decision.pose,
                              "look": [0.0, -0.15]})
        stance = decision.stance
    else:
        # Instant reaction: eyes up toward the typist, before words exist.
        await ws_server.send({"type": "pose", "pose": "default",
                              "look": [0.0, -0.15]})
        stance = None

    reply = await speech.generate_reply(text, vitals=_latest_snapshot,
                                        stance=stance)
    if reply is None:
        text_line = ("// heart present, words absent: no llm key //"
                 if not config.LLM_API_KEY else
                 "// brain busy (upstream overloaded). nudge me again //")
        await ws_server.send({"type": "notice", "text": text_line,
                              "seconds": 4.0})
        print("remix-brain: no reply available for a typed line")
        return

    # Acting: Jev's per-beat arc if it answered, else the local $0 arc.
    beat_list = speech.beats(reply)
    if decision:
        arc = decision.beats or ["default"]
        rest = decision.resting
    else:
        arc = ["default", "smirk"]
        rest = "default"
    look_pool = [[-0.35, 0.1], [0.35, -0.1], [0.0, 0.2]]
    cues = []
    for i, _beat in enumerate(beat_list):
        cues.append({"t": float(i),
                     "pose": arc[min(i, len(arc) - 1)],
                     "look": look_pool[i % len(look_pool)]})
    _seq += 1
    await ws_server.send({"type": "perform", "id": f"t{_seq}", "cues": cues,
                          "then": {"pose": rest}})
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
    _jev = jev.JevBrain()
    ws_server.on_ready(lambda m: (
        _rule.set_available_poses(m.get("poses") or []),
        _jev.set_available_poses(m.get("poses") or [])))
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
