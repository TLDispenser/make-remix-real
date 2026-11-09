# main.py — asyncio entrypoint. Protocol v2 (see godot/PROTOCOL.md).
# Silent life: vitals -> RuleBrain ($0, no Jev).
# An exchange (typed in the terminal or Godot bar, or spoken via push-to-talk):
#   input -> Jev reaction face (instant) -> GLM words (grounded) ->
#   [Jev acting plan || espeak voice] -> ONE perform (audio + per-beat faces)
#   -> settle back to her baseline face.
import asyncio
import os
import socket as _socket
import threading
import time

from . import audio, awareness, config, faces, jev, memory, proactive, rules, speech, stt, tts, vitals, ws_server
from .faces import NEUTRAL, Face

_rule: rules.RuleBrain | None = None
_jev: jev.JevBrain | None = None
_ptt: stt.PushToTalk | None = None
_pro: proactive.Proactive | None = None
_loop: asyncio.AbstractEventLoop | None = None
_seq = 0
_latest_snapshot: dict = {}
_context: dict = {}              # window/screen facts Godot reports
_settle_task: asyncio.Task | None = None
_silent_task: asyncio.Task | None = None
_gate_task: asyncio.Task | None = None   # audio mute/volume refresher
_aware_task: asyncio.Task | None = None  # awareness poller (Phase 6)
_proactive_task: asyncio.Task | None = None
_typing_at = 0.0
_face_ready = False              # Godot sent `ready`: she is actually visible
_last_exchange_at = -1e12        # last time the user said anything
_urgent: dict | None = None      # 6.4: raised while she's being ignored/muted
_ALL_POSES = list(faces.POSES)

SETTLE_S = 5.0                   # after she stops, how long until her face relaxes
LOOK_POOL = [[-0.35, 0.1], [0.35, -0.1], [0.0, 0.2], [0.2, -0.15]]


async def _set_urgent(on: bool, reason: str = "") -> None:
    """6.4: light the 'urgent' marker on her window when she's been muted or
    ignored too long; clear it when the human shows up or un-mutes. Godot
    builds without this handling simply ignore the message."""
    global _urgent
    if not config.URGENT_ENABLED:
        return
    if on and _urgent is None:
        _urgent = {"reason": reason}
        print(f"remix-brain: urgent ON ({reason})")
        await ws_server.send({"type": "urgent", "state": True, "reason": reason})
    elif not on and _urgent is not None:
        print(f"remix-brain: urgent OFF ({_urgent.get('reason')} resolved)")
        _urgent = None
        await ws_server.send({"type": "urgent", "state": False})


async def memory_loop() -> None:
    """Flush the journal on a cadence; mirror the sandbox to GitHub when
    configured ($0, opt-in: REMIX_MEMORY_REPO + REMIX_MEMORY_TOKEN)."""
    last_sync = -1e12
    last_state = None
    while True:
        await asyncio.sleep(30.0)
        memory.maybe_flush()
        if not memory.sync_enabled():
            continue
        now = time.monotonic()
        if now - last_sync >= config.MEMORY_SYNC_MIN_S:
            try:
                state = await asyncio.to_thread(memory.sync_once)
                last_sync = now
                if state != last_state:
                    print(f"remix-brain: memory sandbox: {state}")
                    last_state = state
            except Exception as e:
                print(f"remix-brain: memory sync failed ({e!r})")


async def body_loop(rule: rules.RuleBrain) -> None:
    """The nervous system: vitals stream -> RuleBrain -> v2 messages. On top
    of vitals posing, she WATCHES the room ($0 reflexes, no words): a glance
    when the focused window changes, a small perk when you come back from a
    long absence. Both are suppressed while she's speaking."""
    global _latest_snapshot
    prev_app = None
    prev_idle = None
    perk_task = None
    async for snap in vitals.stream():
        _latest_snapshot = snap
        await ws_server.send({"type": "vitals", **snap})
        for msg in rule.update(snap, aware=awareness.current()):
            await ws_server.send(msg)

        # ---------------------------------------------------- watch reflexes
        aware = awareness.current()
        if (aware.get("up_s", 0.0) > 15.0      # skip the boot sighting flurry
                and time.monotonic() >= rule.speak_gate_until):
            app = aware.get("app")
            if app and app != prev_app:
                if prev_app is not None:       # the first sight isn't an event
                    aim = awareness.gaze_toward(app)
                    if aim:
                        print(f"remix-brain: watched you move to {app}")
                        perk_task = _perk(rule, aim, perk_task)
                prev_app = app
            idle = aware.get("idle_s")
            if idle is not None:
                # deep idle (>=2 min) broken by the human coming back
                if prev_idle is not None and prev_idle >= 120.0 and idle < 90.0:
                    print("remix-brain: watched you come back")
                    perk_task = _perk(rule, None, perk_task)
                prev_idle = idle


def _perk(rule: rules.RuleBrain, aim: list | None, prior) -> asyncio.Task:
    """A quick 'noticed you' micro-reaction ($0, wordless): pointer-eyes for
    under a second — aimed at the window you moved to when there is one —
    then settle back to the vitals-driven baseline."""
    _cancel(prior)

    async def _perk() -> None:
        msg = {"type": "pose", "eyes": "really"}
        if aim:
            msg["look"] = list(aim)
        await ws_server.send(msg)
        await asyncio.sleep(0.8)
        await ws_server.send(rule.baseline_message())

    return asyncio.create_task(_perk())


# ------------------------------------------------------------- face helpers

def _cancel(task: asyncio.Task | None) -> None:
    if task and not task.done():
        task.cancel()


def _schedule_settle() -> None:
    """Let her face relax to the vitals baseline a few seconds after speaking."""
    global _settle_task
    _cancel(_settle_task)

    async def _later() -> None:
        await asyncio.sleep(SETTLE_S)
        if _rule is not None:
            await ws_server.send(_rule.baseline_message())

    _settle_task = asyncio.create_task(_later())


def _local_arc(first: Face, beat_list: list) -> list:
    """$0 acting plan: each beat's face guessed from its own words."""
    out = []
    for i, b in enumerate(beat_list):
        g = faces.guess_face(b)
        out.append(first if (i == 0 and g == NEUTRAL) else g)
    return out


def _beat_looks(beat_list: list, lead: list | None = None) -> list:
    """One gaze per beat. If a beat NAMES a window she can see ("firefox",
    "your terminal"), her eyes turn toward it for that beat; otherwise the
    ambient drift. (2026-11-08: the gesture STICKS - once a named target or
    the `lead` aim takes hold, every later beat keeps pointer-eyes until the
    line ends. Random per-beat drift kept snapping the aim away mid-sentence,
    which is why gestures read as broken.)"""
    out = []
    held = None
    for i, b in enumerate(beat_list):
        look = awareness.look_for_text(b)
        if look is not None:
            held = list(look)
            out.append(list(held))
        elif held is not None:
            out.append(list(held))        # point the whole line, like a hand
        elif i == 0 and lead:
            held = list(lead)
            out.append(list(held))
        else:
            out.append(LOOK_POOL[i % len(LOOK_POOL)])
    return out


async def _play_silent(face_list: list, beat_list: list,
                       looks: list | None = None) -> None:
    """No audio, so Godot can't pace the beats: step through them on reading time."""
    looks = looks or _beat_looks(beat_list)
    for i, (f, b) in enumerate(zip(face_list, beat_list)):
        await ws_server.send(f.msg(look=looks[i]))
        if i < len(beat_list) - 1:
            await asyncio.sleep(max(0.8, len(b) * 0.06))
    _schedule_settle()


# -------------------------------------------------------------- the exchange

async def _on_user_text(m: dict) -> None:
    global _seq, _silent_task, _last_exchange_at
    text = str(m.get("text", "")).strip()
    if not text:
        return
    _last_exchange_at = time.monotonic()
    await _set_urgent(False, "the human showed up")   # 6.4: attention arrives
    if text.startswith("/"):
        await _on_command(text)
        return
    _cancel(_settle_task)
    _cancel(_silent_task)

    # Left brain: stance + the instant face, before any words exist.
    decision = await _jev.react(text, _latest_snapshot) if _jev else None
    if decision:
        face, stance = decision.face, decision.stance
    else:
        face, stance = faces.guess_face(text), None   # $0 fallback
        if _jev:
            _jev.note_face(face)
    # (5) she looks toward whichever visible window the human just named
    # (any window on her screen, not only the focused one).
    aimed = awareness.look_for_text(text)
    if aimed:
        print(f"remix-brain: gazing toward {awareness.find_mentioned(text)['app']} {aimed}")
    await ws_server.send(face.msg(look=aimed or [0.0, -0.15]))

    # Right brain. Only say "one sec" if she is actually slow.
    task = asyncio.create_task(speech.generate_reply(
        text, vitals=_latest_snapshot, stance=stance, context=_context,
        aware=awareness.current()))
    done, _ = await asyncio.wait({task}, timeout=1.5)
    if not done:
        await ws_server.send({"type": "notice", "text": "// one sec //",
                              "seconds": 3.0})
    reply = await task
    if reply is None:
        line = ("// heart present, words absent: no llm key //"
                if not config.LLM_API_KEY else
                "// brain busy (upstream overloaded). nudge me again //")
        await ws_server.send({"type": "notice", "text": line, "seconds": 4.0})
        print("remix-brain: no reply available")
        return

    # Acting plan (Jev, once her words exist), then her voice. With the
    # expressive voice on, the voice FOLLOWS the faces she is about to make, so the
    # plan comes first (espeak is quick); with it off they run in parallel as before.
    beat_list = speech.beats(reply)
    plan_coro = _jev.act(text, beat_list) if _jev else asyncio.sleep(0, None)
    tag = f"p{_seq + 1}"
    if config.TTS_EXPRESSIVE and tts.available():
        plan = await plan_coro
        face_list = list(plan.faces) if plan else _local_arc(face, beat_list)
        spoken = await tts.synthesize(
            beat_list, tag, faces=face_list,
            strain=tts.strain_from(_latest_snapshot))
    else:
        plan, spoken = await asyncio.gather(
            plan_coro, tts.synthesize(beat_list, tag))
        face_list = list(plan.faces) if plan else _local_arc(face, beat_list)
    if _jev and not plan:
        for f in face_list:
            _jev.note_face(f)

    _seq += 1
    pid = f"t{_seq}"
    looks = _beat_looks(beat_list, lead=aimed)
    await ws_server.send({"type": "say", "text": reply, "final": True})
    if spoken:
        cues = [f.cue(spoken.starts[i], looks[i])
                for i, f in enumerate(face_list)]
        _rule.pause_for_speech(spoken.total + 1.0)
        await ws_server.send({"type": "perform", "id": pid, "audio": spoken.path,
                              "cues": cues, "then": face_list[-1].spec()})
    else:
        _silent_task = asyncio.create_task(_play_silent(face_list, beat_list, looks))
        _rule.pause_for_speech(sum(max(0.8, len(b) * 0.06) for b in beat_list))

    if config.SUBTITLES == "always" or (config.SUBTITLES == "auto" and not spoken):
        secs = min(14.0, max(3.5, len(reply) * 0.045))
        await ws_server.send({"type": "notice", "text": reply, "seconds": secs})
    print(f"remix-brain: reply ready ({len(beat_list)} beats, {len(reply)} chars, "
          f"voice={'espeak' if spoken else 'none'}, "
          f"faces={[str(f) for f in face_list]}) -> {pid}")


# ------------------------------------------------- proactive lines (Phase 6)

def _chatter_uses_voice(audible: bool | None) -> bool:
    """REMIX_CHATTER_VOICE — whether an UNPROMPTED line gets espeak.
    never = captions only; always = talk regardless of the room; auto
    (default) = polite: she stays verbal-silent while the room is audible
    (music, videos — she doesn't talk over them) and captions instead."""
    if config.CHATTER_VOICE == "never":
        return False
    if config.CHATTER_VOICE == "always":
        return True
    return not audible

async def _deliver_proactive(line: dict) -> None:
    """Say an unprompted line through the SAME pipeline as a reply: face,
    espeak beats, one perform, captions per the on-screen text rule."""
    global _seq, _silent_task
    text = str(line.get("text") or "").strip()
    if not text:
        return
    face = line.get("face") or faces.guess_face(text)
    _cancel(_settle_task)
    _cancel(_silent_task)
    look = line.get("look") or [0.0, -0.1]
    lead = None
    if line.get("trigger") == "app_stuck":
        # (5) gesture toward the app she's commenting on
        lead = awareness.gaze_toward()
        if lead:
            look = lead
            print("remix-brain: gazing toward the stuck app")
    await ws_server.send(face.msg(look=look))

    beat_list = speech.beats(text)
    _seq += 1
    pid = f"t{_seq}"
    audible = audio.quiet() is False      # None (can't tell) counts audible
    spoken = (await tts.synthesize(
        beat_list, pid, faces=[face] * len(beat_list),
        strain=tts.strain_from(_latest_snapshot))
        if _chatter_uses_voice(audible) else None)
    await ws_server.send({"type": "say", "text": text, "final": True,
                          "proactive": True})
    face_list = [face] * len(beat_list)          # $0 acting: one mood, gaze varies
    looks = _beat_looks(beat_list, lead=lead)
    if spoken:
        cues = [f.cue(spoken.starts[i], looks[i])
                for i, f in enumerate(face_list)]
        _rule.pause_for_speech(spoken.total + 1.0)
        await ws_server.send({"type": "perform", "id": pid, "audio": spoken.path,
                              "cues": cues, "then": face.spec()})
    else:
        _silent_task = asyncio.create_task(_play_silent(face_list, beat_list, looks))
        _rule.pause_for_speech(sum(max(0.8, len(b) * 0.06) for b in beat_list))

    # On-screen text rule: a caption is flavor, so it shows only when the
    # machine can't hear her (muted/low) — or when she has no voice at all.
    if config.SUBTITLES == "always" or (config.SUBTITLES == "auto"
                                        and (not spoken or audio.quiet())):
        secs = min(14.0, max(3.5, len(text) * 0.045))
        await ws_server.send({"type": "notice", "text": text, "seconds": secs})
    speech.add_assistant(text)   # so the next real exchange knows she said it
    why = ("chosen-quiet" if (spoken is None and audible
                              and config.CHATTER_VOICE == "auto")
           else "muted-room" if (spoken is None and audible)
           else "chatter_voice=never" if (spoken is None
                                          and config.CHATTER_VOICE == "never")
           else "espeak" if spoken else "none")
    print(f"remix-brain: proactive [{line.get('trigger')}] \"{text}\" "
          f"(voice={why}, face={face})")


async def proactive_loop() -> None:
    """The quiet heartbeat: every tick, decide whether she has reason to
    speak. The TRIGGER is local and $0 (never Jev); the WORDS are generated
    fresh through the same $0 pool her replies use. A failed generation is
    silence — never a canned line."""
    while True:
        await asyncio.sleep(config.PROACTIVE_TICK_S)
        if _pro is None or not _pro.enabled or not _face_ready or _rule is None:
            continue
        try:
            plan = _pro.tick(
                awareness.current(), _latest_snapshot,
                mic_open=bool(_ptt and _ptt.listening),
                last_exchange_at=_last_exchange_at,
                busy_until=_rule.speak_gate_until)
        except Exception as e:               # chatter must never crash the brain
            print(f"remix-brain: proactive tick failed ({e!r})")
            continue
        if not plan:
            continue
        # Words, born in the moment: same pipeline family as a reply.
        started = time.monotonic()
        try:
            text = await asyncio.wait_for(
                speech.generate_proactive_line(
                    plan, vitals=_latest_snapshot, context=_context,
                    aware=awareness.current()),
                timeout=config.CHATTER_TIMEOUT_S)
        except Exception as e:
            print(f"remix-brain: chatter generation failed ({e!r})")
            text = None
        if not text:
            print(f"remix-brain: chatter [{plan.get('trigger')}] -> "
                  f"silence (nothing clean came back)")
            continue
        # The room may have changed while the words were being written.
        now = time.monotonic()
        if (_ptt and _ptt.listening) or now < _rule.speak_gate_until \
                or _last_exchange_at > started \
                or awareness.current().get("fullscreen"):
            print(f"remix-brain: chatter [{plan.get('trigger')}] dropped — "
                  f"the human needs the room")
            continue
        await _deliver_proactive({**plan, "text": text})
        if plan.get("urgent"):
            # she said her piece about being ignored; now flag the window
            await _set_urgent(True, plan.get("trigger") or "ignored")
        # muted nag resolved itself → clear the flag (case: yell, then un-mute)
        if _urgent and not (awareness.current().get("muted")
                            and awareness.current().get("idle_s", 0) > 90):
            await _set_urgent(False, "the room changed")


async def _on_command(text: str) -> None:
    """Slash commands typed at her: debug + toggles, never sent to any LLM."""
    cmd = text.split()[0].lower()
    if cmd == "/poke" and _pro:
        plan = _pro.force(awareness.current(), _latest_snapshot)
        text_out = None
        if plan:
            try:
                text_out = await asyncio.wait_for(
                    speech.generate_proactive_line(
                        plan, vitals=_latest_snapshot, context=_context,
                        aware=awareness.current()),
                    timeout=config.CHATTER_TIMEOUT_S)
            except Exception as e:
                print(f"remix-brain: poke generation failed ({e!r})")
        if text_out:
            await _deliver_proactive({**plan, "text": text_out})
        else:
            await ws_server.send({"type": "notice", "seconds": 4.0,
                                  "text": "// nothing came out. poke again //"})
    elif cmd == "/aware":
        lines = awareness.describe().split("\n")
        line = "// " + " | ".join(x.strip(" ·") for x in lines) + " //"
        await ws_server.send({"type": "notice", "text": line, "seconds": 9.0})
        print(f"remix-brain: awareness: {awareness.describe()}")
    elif cmd == "/quiet" and _pro:
        _pro.enabled = not _pro.enabled
        state = "on" if _pro.enabled else "off"
        await ws_server.send({"type": "notice", "seconds": 4.0,
                              "text": f"// unprompted chatter: {state} //"})
        print(f"remix-brain: proactive chatter {state}")
    else:
        await ws_server.send({"type": "notice", "seconds": 3.0,
                              "text": "// unknown command //"})


def _on_perform_done(m: dict) -> None:
    print(f"remix-brain: perform {m.get('id')} done ok={m.get('ok', True)}"
          + (f" ({m.get('error')})" if m.get("error") else ""))
    if _rule:
        _rule.pause_for_speech(0.6)
    if m.get("ok", True):
        _schedule_settle()


# ------------------------------------------------------ push-to-talk (ears)

async def _ptt_start() -> None:
    if _ptt is None or _ptt.listening:
        return
    if _ptt.status == "warming":
        await ws_server.send({"type": "notice", "text": "// ears warming up //",
                              "seconds": 2.5})
    result = await _ptt.start()
    if result == "ok":
        _cancel(_settle_task)
        await ws_server.send({"type": "listening", "state": "start"})
        await ws_server.send(NEUTRAL.msg(look=[0.0, -0.1]))
    elif result == "no_mic":
        await ws_server.send({"type": "notice", "seconds": 5.0, "text":
                              "// can't open the mic. install pipewire-pulse (parec) //"})


async def _ptt_stop() -> None:
    if _ptt is None or not _ptt.listening:
        return
    await ws_server.send({"type": "listening", "state": "stop"})
    await ws_server.send({"type": "mood", "mood": "thinking"})
    if _ptt.status == "warming":
        await ws_server.send({"type": "notice", "text": "// ears warming up //",
                              "seconds": 3.0})
    heard = await _ptt.stop()
    if heard.text:
        await ws_server.send({"type": "transcript", "text": heard.text,
                              "final": True})
        await _on_user_text({"type": "user_text", "text": heard.text})
        return
    notices = {
        "silence": "// heard nothing //",
        "unclear": "// say that again? //",
        "no_mic": "// the mic gave me nothing //",
        "error": "// my ears broke. check the brain log //",
    }
    if heard.reason in notices:
        await ws_server.send({"type": "notice", "text": notices[heard.reason],
                              "seconds": 2.5})
    await ws_server.send(_rule.baseline_message())


def _ptt_command(action: str) -> None:
    """Thread-safe entry for start/stop from the hotkey socket or Godot."""
    if _loop is None:
        return
    if action == "start":
        _loop.call_soon_threadsafe(lambda: asyncio.create_task(_ptt_start()))
    elif action == "stop":
        _loop.call_soon_threadsafe(lambda: asyncio.create_task(_ptt_stop()))


def _start_ptt_server() -> None:
    """Datagram listener: the global hotkey's one-shot client says start/stop."""
    def _thread() -> None:
        sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_DGRAM)
        try:
            os.unlink(config.PTT_SOCK)
        except FileNotFoundError:
            pass
        try:
            sock.bind(config.PTT_SOCK)
            os.chmod(config.PTT_SOCK, 0o600)  # only this user pokes the mic
        except OSError as e:
            print(f"remix-brain: ptt socket unavailable ({e!r}) — hotkey voice "
                  f"input off (the Godot F2 key still works)")
            return
        print(f"remix-brain: ptt listening on {config.PTT_SOCK}")
        while True:
            try:
                action, _addr = sock.recvfrom(32)
            except OSError:
                break
            _ptt_command(action.decode(errors="replace").strip())

    threading.Thread(target=_thread, daemon=True, name="ptt-cmd").start()


# ---------------------------------------------------------- godot -> brain

def _on_ready(m: dict) -> None:
    global _face_ready
    _face_ready = True
    _rule.set_available_poses(m.get("poses") or [])
    _jev.set_available(m.get("eyes"), m.get("mouths"))
    print(f"remix-brain: face ready — eyes={m.get('eyes')} mouths={m.get('mouths')}")


def _on_context(m: dict) -> None:
    global _context
    _context = {k: m.get(k) for k in ("window", "screen", "screen_index",
                                      "screen_count", "area_fraction",
                                      "dpi_scale", "pos")}
    print(f"remix-brain: window context {_context.get('window')} at "
          f"{_context.get('pos')} on screen "
          f"{(_context.get('screen_index') or 0) + 1}/"
          f"{_context.get('screen_count') or '?'} "
          f"({(_context.get('area_fraction') or 0) * 100:.0f}% of area)")


def _on_typing(_m: dict) -> None:
    """Typing in the Godot bar: instant 'thinking' reflex, $0, throttled."""
    global _typing_at
    now = time.monotonic()
    if now - _typing_at < 3.0 or _loop is None:
        return
    _typing_at = now
    _cancel(_settle_task)
    asyncio.create_task(ws_server.send(NEUTRAL.msg(look=[-0.4, -0.7])))


def _on_ptt_msg(m: dict) -> None:
    _ptt_command("start" if m.get("state") == "start" else "stop")


async def main() -> None:
    started = time.monotonic()
    global _rule, _jev, _ptt, _loop, _gate_task, _pro, _aware_task, _proactive_task
    _loop = asyncio.get_running_loop()
    memory.load()                      # journal is live from here on
    _rule = rules.RuleBrain()
    _jev = jev.JevBrain(seed_faces=memory.recent_faces(),
                        variety_hook=memory.note_face)
    _pro = proactive.Proactive(
        awaken_s=((memory.awaken() or {}).get("elapsed_s")))
    ws_server.on("ready", _on_ready)
    ws_server.on("context", _on_context)
    ws_server.on("perform_done", _on_perform_done)
    ws_server.on("user_text", lambda m: asyncio.create_task(_on_user_text(m)))
    ws_server.on("user_typing", _on_typing)
    ws_server.on("ptt", _on_ptt_msg)

    voice = tts.available()
    print(f"remix-brain: voice out {'espeak-ng ' + config.TTS_VOICE if voice else 'OFF (no espeak-ng or REMIX_TTS=0) — captions on'}")
    if config.PTT_ENABLED:
        def _ears(status: str) -> None:
            if status == "error" and _loop:
                _loop.call_soon_threadsafe(lambda: asyncio.create_task(
                    ws_server.send({"type": "notice", "seconds": 6.0, "text":
                                    "// ears offline: whisper model failed to load //"})))
        _ptt = stt.PushToTalk(
            on_status=_ears,
            on_timeout=lambda: asyncio.create_task(_ptt_stop()))
        _start_ptt_server()
        _ptt.warm()  # background thread; the model is loaded once

    body_task = asyncio.create_task(body_loop(_rule))
    memory_task = asyncio.create_task(memory_loop())   # flush + GitHub mirror
    if config.NOTICE_GATE:
        _gate_task = asyncio.create_task(audio.gate_loop())  # wpctl mute/volume
    if config.AWARENESS:
        _aware_task = asyncio.create_task(awareness.watch_loop())
        _proactive_task = asyncio.create_task(proactive_loop())
    print(f"remix-brain: awareness {'on' if config.AWARENESS else 'off'} "
          f"(app names only, titles redacted), proactive "
          f"{'on' if config.PROACTIVE and config.AWARENESS else 'off'} "
          f"(max 1 line / {config.PROACTIVE_MIN_GAP_S / 60:.0f} min)")
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
        _cancel(memory_task)
        _cancel(_gate_task)
        _cancel(_aware_task)
        _cancel(_proactive_task)
        if _ptt:
            await _ptt.cancel()   # never leave the mic open on the way out
        memory.shutdown()             # "I was off" anchors to this timestamp
        print(f"remix-brain: stopped after {time.monotonic() - started:.0f}s")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
