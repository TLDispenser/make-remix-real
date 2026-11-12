# main.py — asyncio entrypoint. Protocol v2 (see godot/PROTOCOL.md).
# Listening (listening.py): while a line is being typed/spoken she already
# reacts to the PARTIAL text; a reaction that still fits the final line is
# reused instead of asked for again.
# Silent life: vitals -> RuleBrain ($0, no Jev).
# An exchange (typed in the terminal or Godot bar, or spoken via push-to-talk):
#   input -> Jev reaction face (instant) -> GLM words (grounded) ->
#   [Jev acting plan || espeak voice] -> ONE perform (audio + per-beat faces)
#   -> settle back to her baseline face.
import asyncio
import os
import shutil
import socket as _socket
import subprocess
import threading
import time

from . import audio, awareness, bits, config, decisions, faces, jev, listening, memory, proactive, rules, speech, stt, tts, vitals, ws_server
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
_listener = listening.Listener()     # partial-line reflexes + peek/commit bookkeeping
_listen_task: asyncio.Task | None = None
_peek_task: asyncio.Task | None = None       # at most one Jev peek in flight
_peek_req: "listening.PeekRequest | None" = None
_face_ready = False              # Godot sent `ready`: she is actually visible
_last_exchange_at = -1e12        # last time the user said anything
_urgent: dict | None = None      # 6.4: raised while she's being ignored/muted
_ALL_POSES = list(faces.POSES)
_spoken_now: dict = {}           # the perform in flight: {pid, text} (mute reflex)
_mute_pending = False            # a cut happened; one dry beat on unmute
_mute_last_react_at = -1e12      # rate limit for the dry beat
_escal_rungs: dict = {}          # ladder rung -> monotonic of its last fire
_audio_task: asyncio.Task | None = None   # audio.py event source (Phase 4)
_hypr_task: asyncio.Task | None = None    # Hyprland socket2 listener (Phase 4)

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
        _escal_rungs.clear()
        await ws_server.send({"type": "urgent", "state": False})


async def _on_audio_edge(quiet: bool) -> None:
    """Phase 4 (plan 2.3 #1): the mute flip, seen live (pactl subscribe).
    Muting her MID-LINE cuts the voice at once — stop + the line as a
    caption + a flat look — the reflex is local and instant. The DRY BEAT
    (one word about being muted) is Jev's `mute_react` call and the LLM
    writes it: at most one per mute episode, rate-limited. No canned
    '...rude.' — silence ships when nothing clean comes back."""
    global _mute_pending, _mute_last_react_at
    if _rule is None:
        return
    now = time.monotonic()
    speaking = bool(_spoken_now) and now < _rule.speak_gate_until
    if quiet and speaking:
        line = _spoken_now.get("text") or ""
        _spoken_now.clear()
        await ws_server.send({"type": "stop"})   # voice AND face stop; `then` settles
        if line:
            await ws_server.send({"type": "notice", "text": line,
                                  "seconds": min(14.0,
                                                 max(3.5, len(line) * 0.045))})
        await ws_server.send(Face("default", "default").msg(look=[0.0, 0.1]))
        _mute_pending = True
        decisions.log("mute_react", decisions.mode("mute_react"), jev=None,
                      used="local",
                      local="reflex: stop + caption + flat look")
        print("remix-brain: muted mid-line — voice stopped, line captioned")
        if decisions.mode("mute_react") == "live":
            await _dry_beat("they just muted the sound while you were "
                            "mid-sentence and the voice got cut off")
    elif not quiet and not speaking and _mute_pending:
        _mute_pending = False        # one dry beat per mute episode
        if now - _mute_last_react_at < config.MUTE_REACT_COOLDOWN_S:
            decisions.log("mute_react", decisions.mode("mute_react"), jev=None,
                          used="local", local="cooldown",
                          extra="unmute beat skipped")
            return
        if decisions.mode("mute_react") == "live":
            await _dry_beat("the sound just came back after being muted "
                            "mid-sentence")


async def _dry_beat(situation: str) -> None:
    """The mute/unmute dry beat: Jev says whether it's worth a line
    (`mute_react` live), the LLM writes it, delivered captioned while the
    room is quiet and spoken once they can hear her again. Never canned."""
    global _mute_last_react_at
    mode = decisions.mode("mute_react")
    speak_p = None
    if _jev is not None and config.JEV_ENABLED:
        try:
            d = await _jev.decide_proactive("mute_react", situation,
                                            ["flat", "pout"])
            speak_p = (d or {}).get("speak_p")
        except Exception as e:
            print(f"remix-brain: mute beat jev failed ({e!r})")
    wants = (speak_p is not None and speak_p >= config.JEV_SPEAK_P)
    decisions.log("mute_react", mode,
                  jev=f"{speak_p:.2f}" if speak_p is not None else None,
                  used=("jev" if (mode == "live" and wants) else "local"),
                  local="one dry line", extra=situation[:60])
    if not (mode == "live" and wants):
        return
    try:
        text = await asyncio.wait_for(
            speech.generate_proactive_line(
                {"trigger": "mute_react", "situation": situation,
                 "stance": "flat"},
                vitals=_latest_snapshot, aware=awareness.current()),
            timeout=config.CHATTER_TIMEOUT_S)
    except Exception as e:
        print(f"remix-brain: mute beat generation failed ({e!r})")
        return
    if not text:
        return                      # silence beats filler (her own rule)
    _mute_last_react_at = time.monotonic()
    # Captioned while the room is quiet, spoken once they can hear her:
    # _deliver_proactive's own voice rule (chatter auto) already knows that.
    await _deliver_proactive({"trigger": "mute_react", "text": text,
                              "face": Face("default", "default")})


def _hypr_focus_cmd(match: str) -> list | None:
    """The rung-3 command: bring her forward with a Hyprland dispatch.
    `focuswindow` takes the class matcher as a separate argument (the `^`
    caret is batch syntax and makes the dispatch silently do nothing)."""
    cls = next((x.strip().lower() for x in (match or "").split(",")
                if x.strip()), None)
    return (["hyprctl", "dispatch", "focuswindow", f"class:{cls}"]
            if cls else None)


async def _maybe_escalate(source: str, text: str = "") -> None:
    """Phase 4 (plan 2.3 #3): the attention ladder, started by decision.
    Jev's `escalate` kind decides whether what she has to say deserves
    pulling your attention. Rung 1 = the Godot urgent marker + attention
    request (the bar glows). Rung 2 = notify-send with urgency set.
    Rung 3 = a Hyprland dispatch to bring her forward — explicit opt-in
    (REMIX_ESCALATE_DISPATCH), it can steal focus. The local fallback
    (shadow/off) is rung 1 only, for the urgent triggers and for anything
    she says while out of sight. Each rung has its own cooldown, and
    everything clears the moment you focus her or reply (_set_urgent)."""
    oos = awareness.out_of_sight()
    urgent_source = source in ("muted_long", "idle_deep")
    if not config.OOS_URGENT and not urgent_source:
        return
    local_yes = urgent_source or (oos is True and config.OOS_URGENT)
    mode = decisions.mode("escalate")
    if not local_yes:
        return          # she is right there and nothing urgent fired: no ladder
    situation = (f"she just said something ({source}) while she is out of "
                 f"sight (not focused, not pinned to your workspace, or "
                 f"covered)" if oos is True else
                 f"she was muted or ignored long enough to say so ({source})")
    jev_p = None
    if mode != "off" and _jev is not None and config.JEV_ENABLED:
        try:
            d = await _jev.decide_proactive("escalate", situation, ["flat"])
            jev_p = (d or {}).get("speak_p")
        except Exception as e:
            print(f"remix-brain: escalate decision failed ({e!r})")
        decisions.log("escalate", mode,
                      jev=f"{jev_p:.2f}" if jev_p is not None else None,
                      used="local",
                      local="rung 1" if local_yes else "stay put",
                      extra=f"[{source}] oos={oos}")
    ship = ((mode == "live" and jev_p is not None
             and jev_p >= config.JEV_SPEAK_P)
            or (mode != "live" and local_yes))
    if not ship:
        return
    await _set_urgent(True, source)     # rung 1 (idempotent — may already burn)
    if mode != "live" or not _urgent:
        return
    now = time.monotonic()
    if (config.ESCALATE_NOTIFY and shutil.which("notify-send")
            and now - _escal_rungs.get(2, -1e12)
            >= config.ESCALATE_NOTIFY_COOLDOWN_S):
        _escal_rungs[2] = now
        body = (text or "she has something for you").strip()[:140]
        try:
            await asyncio.to_thread(
                subprocess.run,
                ["notify-send", "-u", "critical", "-a", "Remix", "-t",
                 str(int(config.NOTIFY_TIMEOUT_S)), "Remix", body],
                capture_output=True, timeout=5.0)
            print("remix-brain: escalation rung 2 (notify-send)")
        except Exception as e:
            print(f"remix-brain: notify-send failed ({e!r})")
    cmd = _hypr_focus_cmd(config.SELF_MATCH)
    if (config.ESCALATE_DISPATCH and cmd and shutil.which("hyprctl")
            and now - _escal_rungs.get(3, -1e12)
            >= config.ESCALATE_DISPATCH_COOLDOWN_S):
        _escal_rungs[3] = now
        try:
            await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, timeout=5.0)
            print("remix-brain: escalation rung 3 (hyprctl focuswindow)")
        except Exception as e:
            print(f"remix-brain: hyprctl dispatch failed ({e!r})")


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


async def _memory_backfill() -> None:
    """Phase 3 boot backfill (plan 2.1.4 / 2.1.7), one-shot: day summaries
    for past days that lack one (cap 2 per boot), and a reflection when the
    last session ended too abruptly to write one. $0 pool; any failure just
    skips the note — the journal never depends on the words existing."""
    await asyncio.sleep(20.0)          # let boot settle (greeting, senses)
    if not memory.has_journal():
        return
    try:
        rows = memory.lines(limit=400)
        if not rows:
            return
        # --- day summaries: group by local day; each day BEFORE today that
        # has rows but no summary ABOUT it gets one paragraph, cap 2 per
        # boot. A summary row carries `about` = the day it describes (its
        # own `at` is the moment it was WRITTEN, which is useless here).
        have = {r.get("about") for r in rows
                if r.get("kind") == "summary" and r.get("about")}
        today = time.strftime("%Y-%m-%d")
        by_day: dict = {}
        for r in rows:
            day = str(r.get("at") or "")[:10]
            if day and day < today:
                by_day.setdefault(day, []).append(r)
        done = 0
        for day in sorted(by_day):
            if done >= 2:
                break
            if day in have:
                continue
            text = await speech.generate_day_summary(
                list(reversed(by_day[day])),        # oldest first
                vitals=_latest_snapshot, aware=awareness.current())
            if text:
                memory.record("summary", text, about=day)
                done += 1
                print(f"remix-brain: day summary written for {day}")
        # --- missed reflection: rows exist after the newest reflection row
        # (the previous shutdown was too abrupt to write one). lines() is
        # newest-first, so everything ABOVE the newest reflection is newer.
        last_refl = next((i for i, r in enumerate(rows)
                          if r.get("kind") == "reflection"), None)
        newer = rows[:last_refl] if last_refl is not None else rows
        if newer:
            text = await speech.generate_reflection(
                vitals=_latest_snapshot, aware=awareness.current())
            if text:
                memory.record("reflection", text)
                print("remix-brain: missed reflection written at boot")
    except Exception as e:
        print(f"remix-brain: memory backfill failed ({e!r})")


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
        # Phase 4 (plan 2.3 done-when): the bar clears the moment you look
        # at her — the vitals tick is fast enough to feel instant.
        if _urgent and aware.get("self_focused"):
            await _set_urgent(False, "you looked")
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
        look = awareness.look_for_text(b) or awareness.gaze_for_dir(b)
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


# --------------------------------------------------- listening (the spike)

def _speaking() -> bool:
    return bool(_rule and _rule.speaking())


async def _send_reflexes(reflexes: list) -> None:
    for r in reflexes:
        await ws_server.send(r.msg)
        if r.msg.get("type") in ("pose", "twitch"):   # log faces, not every glance
            print(f"remix-brain: listening: {r.why}")


def _listen_feed(text: str, source: str) -> None:
    """A partial line: typed in the bar ("typing") or a Whisper partial while
    push-to-talk is held ("voice"). Reflexes go out now; pauses and peeks are
    handled by _listen_loop."""
    global _listen_task
    if _loop is None:
        return
    text = (text or "")[:400]
    if not text.strip():                      # cleared / Esc
        if _listener.cancel():
            asyncio.create_task(_listen_release("cleared"))
        return
    reflexes = _listener.feed(text, source, quiet=_speaking())
    if _rule:
        _rule.hold_listening(2.0, self_load=(source == "voice"))
    _cancel(_settle_task)
    if reflexes:
        asyncio.create_task(_send_reflexes(reflexes))
    if _listen_task is None or _listen_task.done():
        _listen_task = asyncio.create_task(_listen_loop())


async def _listen_loop() -> None:
    """While a line is in progress: stalls, cue fades, and peek asks."""
    while _listener.active:
        await asyncio.sleep(config.LISTEN_TICK_S)
        busy = _peek_task is not None and not _peek_task.done()
        t = _listener.tick(quiet=_speaking(), peek_busy=busy)
        if _rule and _listener.active:
            _rule.hold_listening(2.0, self_load=(_listener.source == "voice"))
        await _send_reflexes(t.reflexes)
        if t.peek and _jev:
            _start_peek(t.peek)
        if t.release:
            await _listen_release("waited too long")
            return


async def _listen_release(why: str) -> None:
    """She stops listening without a line to answer: back to her baseline."""
    _listener.release()
    if _rule:
        _rule.release_listening()
        await ws_server.send(_rule.baseline_message())
    print(f"remix-brain: listening: released ({why})")


def _start_peek(req: "listening.PeekRequest") -> None:
    global _peek_task, _peek_req
    _peek_req = req
    _peek_task = asyncio.create_task(_peek(req))


async def _peek(req: "listening.PeekRequest"):
    """Ask Jev how she'd feel about the line so far, and show it if the line
    hasn't moved on. Returns the Decision either way (an in-flight peek can
    still be reused by the commit)."""
    d = await _jev.peek(req.text, _latest_snapshot)
    if d is None:
        return None
    if _listener.accept_peek(req.seq, d):
        _jev.note_face(d.face)
        await ws_server.send(d.face.msg(look=_listener.last_look))
        print(f"remix-brain: listening: jev peek ({len(listening.words(req.text))} "
              f"words) -> {d.face} stance={d.stance_name}")
    return d


# -------------------------------------------------------------- the exchange

async def _on_user_text(m: dict, echo: bool = True) -> None:
    global _seq, _silent_task, _last_exchange_at
    text = str(m.get("text", "")).strip()
    if not text:
        return
    # Typed at her (Godot bar, terminal REPL): mirror it exactly the way the
    # mic path already does, so the HUMAN's words show up in every place her
    # replies do — her HUD ("you: ..."), any connected terminal, brain.log.
    # The PTT path passes echo=False: it has its own transcript broadcast.
    if echo:
        await ws_server.send({"type": "transcript", "text": text,
                              "final": True})
        print(f"remix-brain: user typed: \"{text}\"")
    _last_exchange_at = time.monotonic()
    await _set_urgent(False, "the human showed up")   # 6.4: attention arrives
    if text.startswith("/"):
        if _listener.active:
            await _listen_release("command")
        await _on_command(text)
        return
    _cancel(_settle_task)
    _cancel(_silent_task)

    # Listening: did her reaction to the partial line already happen? If it
    # still fits what was actually sent, it STANDS (no react call, no wait).
    commit = _listener.commit(text)            # also ends the listening state
    if _rule:
        _rule.release_listening()
    decision = commit.reuse
    if decision is not None:
        print(f"remix-brain: listening: reused the reaction shown "
              f"{commit.lead_s:.1f}s before send - react call skipped")
    elif commit.peeked:
        print("remix-brain: listening: the line changed - peek discarded")
    elif (_jev and _peek_task is not None and not _peek_task.done()
          and _peek_req is not None and listening.similar(text, _peek_req.text)):
        # a peek is still in flight for (nearly) this line: wait for it
        # instead of paying for a second call
        try:
            decision = await asyncio.wait_for(asyncio.shield(_peek_task), 1.2)
        except Exception:
            decision = None
        if decision is not None:
            _listener.stats.reused_inflight += 1
            _jev.note_face(decision.face)
            print("remix-brain: listening: awaited the in-flight peek - "
                  "react call skipped")

    # Left brain: stance + the instant face, before any words exist.
    if decision is None and _jev:
        decision = await _jev.react(text, _latest_snapshot)
    if decision:
        face, stance = decision.face, decision.stance
        decisions.log("react", "-", jev=decision.stance_name,
                      confidence=decision.confidence, used="jev",
                      local="rules.guess_face",
                      extra=f"face={decision.face}"
                            + (" (cached)" if decision.from_cache else ""))
    else:
        face, stance = faces.guess_face(text), None   # $0 fallback
        if _jev:
            _jev.note_face(face)
        decisions.log("react", "-", jev=None, used="local",
                      local=f"rules.guess_face -> {face}")
    # Phase 3 riders (plan 2.4.2 / 2.1.5): the react call already carried
    # them, so this is bookkeeping only — no extra request, no extra spend.
    # user_intent: shadow = logged only (it later steers the dodge judge and
    # cooperation scoring). commitment: shadow = logged only; live turns a
    # spotted promise into a dated reminder row.
    intent = decision.intent if decision else None
    if decision is not None:
        if intent:
            decisions.log("user_intent", decisions.mode("user_intent"),
                          jev=intent, used="jev", local="none",
                          extra="react rider")
        cp = decision.commitment_p
        if cp is not None:
            cmode = decisions.mode("commitment")
            note = f"p={cp:.2f} when={decision.commitment_when or '?'}"
            if cmode == "live" and cp >= config.JEV_COMMIT_P:
                try:
                    memory.add_commitment(text, decision.commitment_when
                                          or "no_time_given")
                    note += " -> recorded"
                except Exception as e:
                    note += f" (record failed: {e!r})"
            decisions.log("commitment", cmode, jev=f"{cp:.2f}",
                          used="jev", local="none", extra=note)
    # (5) she looks toward whichever visible window the human just named
    # (any window on her screen, not only the focused one) — or toward the
    # side they ask about ("which one is to your right?").
    aimed = awareness.look_for_text(text) or awareness.gaze_for_dir(text)
    if aimed:
        named = awareness.find_mentioned(text)
        print(f"remix-brain: gazing toward "
              f"{named['app'] if named else 'the window you pointed at'} {aimed}")
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
                "// can't reach the words right now. nudge me again //")
        await ws_server.send({"type": "notice", "text": line, "seconds": 4.0})
        print("remix-brain: no reply available")
        return

    # Acting plan (Jev, once her words exist), then her voice. With the
    # expressive voice on, the voice FOLLOWS the faces she is about to make, so the
    # plan comes first (espeak is quick); with it off they run in parallel as before.
    beat_list = speech.beats(reply)
    judge_on = decisions.mode("judge_unseen") != "off"
    dodge_on = decisions.mode("dodge_judge") != "off"
    senses = grounding_senses(awareness.current(), _latest_snapshot)
    plan_coro = (_jev.act(text, beat_list, senses=senses, intent=intent)
                 if _jev else asyncio.sleep(0, None))
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
    if _jev:
        decisions.log("act", "-", jev="faces",
                      used="jev" if plan else "local",
                      local="_local_arc",
                      extra=f"{[str(f) for f in face_list]}"
                            + (f" unseen={plan.unseen:.2f}"
                               if plan is not None
                               and plan.unseen is not None else ""))
    # Grounding judge (shadow): log the disagreement space, never act on it.
    # The local word list still decided everything about this reply.
    if judge_on and plan is not None and plan.unseen is not None:
        local_problems = speech.grounding.check(
            reply, text, "", _latest_snapshot, awareness.current())
        verdict = ("clean" if not local_problems
                   else "flagged: " + "; ".join(local_problems[:3]))
        agree = (plan.unseen >= config.JEV_SPEAK_P) == bool(local_problems)
        decisions.log("judge_unseen", decisions.mode("judge_unseen"),
                      jev=f"{plan.unseen:.2f}", used="local", local=verdict,
                      extra="agree" if agree else "DISAGREE")

    # Dodge judge (plan 2.4.3): shadow logs the score, live acts on it — ONE
    # plain-prose retry when she dodged a question/command, and the second
    # try ships either way (her words, her responsibility). Nothing has been
    # sent yet, so re-voicing the corrected line is safe.
    if (dodge_on and plan is not None and plan.dodged is not None
            and intent in ("question", "command")):
        dmode = decisions.mode("dodge_judge")
        if dmode == "live" and plan.dodged >= config.JEV_DODGE_THRESHOLD:
            print(f"remix-brain: dodge judge flagged the reply "
                  f"({plan.dodged:.2f}) — one plain retry")
            decisions.log("dodge_judge", "live", jev=f"{plan.dodged:.2f}",
                          used="jev", local="none", extra="retry")
            second = await speech.generate_undodged(
                text, reply, vitals=_latest_snapshot, stance=stance,
                context=_context, aware=awareness.current())
            if second:
                reply = second
                beat_list = speech.beats(reply)
                spoken = await tts.synthesize(beat_list, tag)
                face_list = list(plan.faces) if plan else _local_arc(face, beat_list)
        else:
            decisions.log("dodge_judge", dmode, jev=f"{plan.dodged:.2f}",
                          used="jev", local="none", extra=f"intent={intent}")


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
        _spoken_now.update(pid=pid, text=reply)   # the mute reflex watches this
    else:
        _silent_task = asyncio.create_task(_play_silent(face_list, beat_list, looks))
        _rule.pause_for_speech(sum(max(0.8, len(b) * 0.06) for b in beat_list))

    if config.SUBTITLES == "always" or (config.SUBTITLES == "auto" and not spoken):
        secs = min(14.0, max(3.5, len(reply) * 0.045))
        await ws_server.send({"type": "notice", "text": reply, "seconds": secs})
    print(f"remix-brain: reply ready ({len(beat_list)} beats, {len(reply)} chars, "
          f"voice={'espeak' if spoken else 'none'}, "
          f"faces={[str(f) for f in face_list]}) -> {pid}")
    # Phase 4 (plan 2.3 done-when): she answered while out of sight — the
    # bar glows within a second, and clears the moment you look at her.
    await _maybe_escalate("reply", reply)


# ------------------------------------------------- proactive lines (Phase 6)

async def _decide_proactive(plan: dict) -> dict | None:
    """The seam (Phase 2, plan 1.2 rule 3): silent life asks the left brain
    what it would do — stance, whether to speak, in what form — strictly
    INSIDE the caps the trigger engine already applied (no new schedule).
    Modes per kind:
      off    plan unchanged: today's local behavior (no call)
      shadow Jev answers, the decision LOG reads it, the local pick ships
      live   Jev ships; any failure/garbled answer falls back to local
    Returns the (possibly amended) plan, or None when LIVE decides she
    stays quiet this time."""
    trigger = plan.get("trigger") or ""
    active = {k: decisions.mode(k) for k in
              ("proactive_stance", "should_speak", "form")
              if decisions.mode(k) != "off"}
    if not active or _jev is None or not config.JEV_ENABLED:
        return plan
    d = await _jev.decide_proactive(
        trigger, plan.get("situation") or "",
        list(proactive.STANCE_CHOICES.get(trigger, ["flat"])))
    if d is None:                       # budget spent / call failed
        for k, m in active.items():
            decisions.log(k, m, jev=None, used="local",
                          local=f"fallback: local behavior [{trigger}]")
        return plan
    m = active.get("proactive_stance")
    if m:
        local_stance = plan["stance"]
        # re-validate against the local menu: garbage ships nothing
        stance_ok = (d["stance"] in proactive.STANCE_CHOICES.get(trigger, []))
        if m == "live" and stance_ok and d["stance"] != plan["stance"]:
            plan["stance"] = d["stance"]
            plan["face"] = proactive.STANCE_FACE.get(
                d["stance"], plan["face"])
        decisions.log("proactive_stance", m, jev=d["stance"],
                      confidence=d["confidence"],
                      used=("jev" if m == "live" and stance_ok else "local"),
                      local=local_stance, extra=f"[{trigger}]")
    m = active.get("should_speak")
    if m and d["speak_p"] is not None:
        wants = d["speak_p"] >= config.JEV_SPEAK_P
        if m == "live" and not wants:
            decisions.log("should_speak", m, jev=f"{d['speak_p']:.2f}",
                          confidence=d["speak_confidence"], used="jev",
                          local="would have spoken",
                          extra=f"stayed quiet [{trigger}]")
            return None
        decisions.log("should_speak", m, jev=f"{d['speak_p']:.2f}",
                      confidence=d["speak_confidence"],
                      used=("jev" if m == "live" else "local"),
                      local="speak", extra=f"[{trigger}]")
    m = active.get("form")
    if m and d["form"] in ("speak", "caption", "look", "quiet", "leave_note"):
        if m == "live":
            # leave_note needs the outbox (Phase 5); degrade to caption.
            plan["form"] = ("caption" if d["form"] == "leave_note"
                            else d["form"])
        decisions.log("form", m, jev=d["form"],
                      used=("jev" if m == "live" else "local"),
                      local="speak", extra=f"[{trigger}]")
    return plan


async def _deliver_proactive_look(plan: dict) -> None:
    """The `look` form: a face and a glance at what she noticed — no words,
    no caption. Wordless presence, $0."""
    face = plan.get("face")
    if face is None:
        return
    look = plan.get("look")
    if plan.get("trigger") == "app_stuck":
        lead = awareness.gaze_toward()
        look = lead or look
    elif plan.get("aim"):
        lead = awareness.gaze_toward(plan["aim"])
        look = lead or look
    await ws_server.send(face.msg(look=look or [0.0, -0.1]))
    print(f"remix-brain: wordless reaction ({plan.get('trigger')})")

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

def grounding_senses(aware: dict | None, vitals: dict | None) -> str:
    """A compact plain-words list of what she can actually sense right now —
    the state the grounding judge compares her reply against (plan 1.5)."""
    aware, vitals = aware or {}, vitals or {}
    bits = []
    if aware.get("app"):
        bits.append(f"the focused app is {aware['app']}")
    around = aware.get("around") or {}
    names = [w.get("app") for w in (around.get("windows") or [])
             if w.get("app")]
    if names:
        bits.append("windows she can see around her: "
                    + ", ".join(names[:6]))
    for k, unit in (("cpu", "% cpu"), ("ram", "% ram"), ("temp", "°C core"),
                    ("battery", "% battery"), ("disk", "% disk")):
        v = vitals.get(k)
        if v is not None:
            try:
                bits.append(f"{round(float(v))}{unit}")
            except (TypeError, ValueError):
                pass
    return "; ".join(bits) or "nothing beyond her own vitals"


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
    elif line.get("aim"):
        # a window she just watched open: her eyes flick to it first
        lead = awareness.gaze_toward(line["aim"])
        if lead:
            look = lead
            print(f"remix-brain: gazing toward the new window ({line['aim']})")
    await ws_server.send(face.msg(look=look))

    beat_list = speech.beats(text)
    _seq += 1
    pid = f"t{_seq}"
    audible = audio.quiet() is False      # None (can't tell) counts audible
    spoken = (await tts.synthesize(
        beat_list, pid, faces=[face] * len(beat_list),
        strain=tts.strain_from(_latest_snapshot))
        if (_chatter_uses_voice(audible) and not line.get("mute_voice"))
        else None)
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
        _spoken_now.update(pid=pid, text=text)   # the mute reflex watches this
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
        # Phase 4 (plan 2.3 #3): the ladder clears the moment you look at her.
        if _urgent and awareness.current().get("self_focused"):
            await _set_urgent(False, "you looked")
        bit_edges = None
        if config.BITS:
            try:
                bit_edges = await asyncio.to_thread(bits.poll)
            except Exception as e:       # a broken bit never stops the heart
                print(f"remix-brain: bits poll failed ({e!r})")
        try:
            plan = _pro.tick(
                awareness.current(), _latest_snapshot,
                mic_open=bool(_ptt and _ptt.listening),
                last_exchange_at=_last_exchange_at,
                busy_until=_rule.speak_gate_until,
                commitments=memory.due_commitments(),
                bit_edges=bit_edges)
        except Exception as e:               # chatter must never crash the brain
            print(f"remix-brain: proactive tick failed ({e!r})")
            continue
        if not plan:
            continue
        if plan.get("trigger") == "commitment_due" and plan.get("commitment"):
            # fires once: the slot is spent whether or not words materialize
            try:
                memory.mark_commitment_done(plan["commitment"].get("id"))
            except Exception as e:
                print(f"remix-brain: commitment done-mark failed ({e!r})")
        # The seam: what would the left brain do with this urge? (modes)
        decided = await _decide_proactive(plan)
        if decided is None:              # live mode chose silence
            continue
        plan = decided
        form = plan.get("form") or "speak"
        if form == "look":               # wordless: face + glance only
            await _deliver_proactive_look(plan)
            decisions.log("outcome", "-", jev=None, used="look",
                          local="speak", extra=f"[{plan.get('trigger')}]")
            continue
        if form == "quiet":
            decisions.log("outcome", "-", jev=None, used="quiet",
                          local="speak", extra=f"[{plan.get('trigger')}]")
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
        # caption form: text on screen, no voice (plan 1.4 form choice)
        await _deliver_proactive({**plan, "text": text,
                                  "mute_voice": form == "caption"})
        decisions.log("outcome", "-", jev=None,
                      used=("caption" if form == "caption" else "spoke"),
                      local="speak", extra=f"[{plan.get('trigger')}]")
        # Phase 4: she said her piece while out of sight — the ladder starts.
        if awareness.out_of_sight() is True:
            await _maybe_escalate(str(plan.get("trigger") or "noticed"), text)
        if plan.get("urgent"):
            # she said her piece about being ignored; now flag the window
            await _set_urgent(True, plan.get("trigger") or "ignored")
        # muted nag resolved itself → clear the flag (case: yell, then un-mute).
        # Phase 4: this auto-clear is ONLY for the legacy ignore triggers —
        # an out-of-sight escalation stays lit until you look or reply.
        if (_urgent and _urgent.get("reason") in ("muted_long", "idle_deep")
                and not (awareness.current().get("muted")
                         and awareness.current().get("idle_s", 0) > 90)):
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
    elif cmd == "/listen":
        line = "// listening: " + _listener.stats.summary() + " //"
        await ws_server.send({"type": "notice", "text": line, "seconds": 9.0})
        print(f"remix-brain: listening: {_listener.stats.summary()}")
    elif cmd == "/quiet" and _pro:
        _pro.enabled = not _pro.enabled
        state = "on" if _pro.enabled else "off"
        await ws_server.send({"type": "notice", "seconds": 4.0,
                              "text": f"// unprompted chatter: {state} //"})
        print(f"remix-brain: proactive chatter {state}")
    elif cmd == "/why":
        # the decision log, in plain words (Phase 2, plan 1.6)
        entries = decisions.recent(6)
        print("remix-brain: recent decisions:\n  "
              + "\n  ".join(entries or ["(none logged yet)"]))
        show = entries[-3:]
        line = ("// nothing decided yet //" if not show else
                "// " + " | ".join(
                    e.split(" ", 1)[1] for e in show) + " //")
        await ws_server.send({"type": "notice", "text": line, "seconds": 9.0})
    elif cmd == "/decisions":
        # view or flip the off/shadow/live switches at runtime
        parts = text.split()
        if len(parts) == 1:
            line = "// decisions: " + decisions.summary() + " //"
            await ws_server.send({"type": "notice", "text": line,
                                  "seconds": 9.0})
        elif len(parts) == 3 and decisions.set_mode(parts[1], parts[2]):
            line = (f"// {parts[1]}: {parts[2]} //")
            await ws_server.send({"type": "notice", "text": line,
                                  "seconds": 5.0})
            print(f"remix-brain: decision mode {parts[1]} -> {parts[2]}")
        else:
            await ws_server.send({"type": "notice", "seconds": 6.0,
                                  "text": "// usage: /decisions <kind> "
                                          "<off|shadow|live> //"})
    elif cmd == "/memory":
        # Phase 3: show exactly what she would inject RIGHT NOW for a real
        # line — the recency window plus whatever this text would retrieve.
        topic = text[len("/memory"):].strip()
        rows = memory.lines(limit=8, spread=5)
        extra = memory.retrieve(topic, limit=config.MEMORY_SEARCH_LIMIT) \
            if topic else []
        seen = {(r.get("t"), r.get("text")) for r in rows}
        picks = [r for r in extra if (r.get("t"), r.get("text")) not in seen]
        parts = ([f"{r.get('at')} {str(r.get('text'))[:70]}"
                  for r in rows[:4]] +
                 [f"+ {r.get('at')} {str(r.get('text'))[:70]}" for r in picks])
        line = ("// journal empty //" if not parts else
                "// remembers: " + " | ".join(parts[:5]) + " //")
        await ws_server.send({"type": "notice", "text": line, "seconds": 12.0})
        print(f"remix-brain: /memory ({len(rows)} recent, "
              f"{len(picks)} retrieved for '{topic or '-'}'):\n  "
              + "\n  ".join(parts) if parts else "remix-brain: /memory: empty")
    elif cmd == "/forget":
        topic = text[len("/forget"):].strip()
        if not topic:
            await ws_server.send({"type": "notice", "seconds": 5.0,
                                  "text": "// usage: /forget <topic> //"})
        else:
            n = memory.forget(topic)
            await ws_server.send({"type": "notice", "seconds": 6.0,
                                  "text": f"// {n} notes about '{topic}' "
                                          f"gone. it's done //"})
            print(f"remix-brain: /forget '{topic}': {n} rows removed")
    elif cmd == "/remember":
        on = not memory.is_remember_enabled()
        memory.set_remember(on)
        state = "on" if on else "paused — this stays between us"
        await ws_server.send({"type": "notice", "seconds": 5.0,
                              "text": f"// remembering: {state} //"})
        print(f"remix-brain: remembering {'on' if on else 'off'}")
    else:
        await ws_server.send({"type": "notice", "seconds": 3.0,
                              "text": "// unknown command //"})


def _on_perform_done(m: dict) -> None:
    print(f"remix-brain: perform {m.get('id')} done ok={m.get('ok', True)}"
          + (f" ({m.get('error')})" if m.get("error") else ""))
    if _spoken_now.get("pid") == m.get("id"):
        _spoken_now.clear()
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
        _listener.reset()
        if _rule:
            _rule.hold_listening(2.5, self_load=True)
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
        await _on_user_text({"type": "user_text", "text": heard.text},
                            echo=False)
        return
    _listener.release()                    # nothing usable was said
    if _rule:
        _rule.release_listening()
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


def _on_typing(m: dict) -> None:
    """The Godot bar sends the PARTIAL line with every change (user_typing.text)
    so she can react while you type. A bare ping (older face build, or
    REMIX_LISTEN=0) keeps the old $0 'thinking' look, throttled."""
    global _typing_at
    text = m.get("text")
    if config.LISTEN and isinstance(text, str):
        _listen_feed(text, "typing")
        return
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
    global _audio_task, _hypr_task
    _loop = asyncio.get_running_loop()
    memory.load()                      # journal is live from here on
    _rule = rules.RuleBrain()
    _jev = jev.JevBrain(seed_faces=memory.recent_faces(),
                        variety_hook=memory.note_face)
    _listener.peek_enabled = bool(config.LISTEN and config.LISTEN_JEV
                                  and config.JEV_ENABLED and _jev)
    _pro = proactive.Proactive(
        awaken_s=((memory.awaken() or {}).get("elapsed_s")))
    _backfill_task: asyncio.Task | None = None
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
            on_timeout=lambda: asyncio.create_task(_ptt_stop()),
            on_partial=((lambda t: _listen_feed(t, "voice"))
                        if config.LISTEN else None))
        _start_ptt_server()
        _ptt.warm()  # background thread; the model is loaded once

    print(f"remix-brain: listening {'on' if config.LISTEN else 'off'} "
          f"(local reflexes; jev peeks "
          f"{'ON — credits, partial lines leave the machine' if _listener.peek_enabled else 'off'}; "
          f"voice partials "
          f"{'on' if config.LISTEN and config.PTT_ENABLED and config.LISTEN_STT_PARTIALS else 'off'})")
    audio.on_change(_on_audio_edge)   # Phase 4: the mute flip reflex
    body_task = asyncio.create_task(body_loop(_rule))
    memory_task = asyncio.create_task(memory_loop())   # flush + GitHub mirror
    if config.NOTICE_GATE:
        _gate_task = asyncio.create_task(audio.gate_loop())  # wpctl mute/volume
        if config.AUDIO_EVENTS:
            _audio_task = asyncio.create_task(audio.event_loop())
    if config.AWARENESS:
        _aware_task = asyncio.create_task(awareness.watch_loop())
        _hypr_task = asyncio.create_task(awareness.events_loop())
        _proactive_task = asyncio.create_task(proactive_loop())
    _backfill_task = asyncio.create_task(_memory_backfill())
    print(f"remix-brain: awareness {'on' if config.AWARENESS else 'off'} "
          f"(app names only, titles redacted), proactive "
          f"{'on' if config.PROACTIVE and config.AWARENESS else 'off'} "
          f"(max 1 line / {config.PROACTIVE_MIN_GAP_S / 60:.0f} min)")
    if config.BITS:
        print(f"remix-brain: bits on "
              f"({len(config.BIT_COOLDOWN_FLOORS_S)} wall-cooldowns)")
    print(f"remix-brain: decision modes: {decisions.summary()}"
          f"{' (log off)' if not config.DECISIONS_LOG else ''}")
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
        _cancel(_audio_task)
        _cancel(_aware_task)
        _cancel(_hypr_task)
        _cancel(_proactive_task)
        _cancel(_backfill_task)
        if _ptt:
            await _ptt.cancel()   # never leave the mic open on the way out
        # Parting note (plan 2.1.4): one reflection over this session, if
        # there was one. A $0 call; failure just skips the note.
        if speech.had_exchanges():
            try:
                refl = await asyncio.wait_for(
                    speech.generate_reflection(vitals=_latest_snapshot,
                                               aware=awareness.current()),
                    timeout=30.0)
                if refl:
                    memory.record("reflection", refl)
                    print("remix-brain: parting reflection written")
            except Exception as e:
                print(f"remix-brain: parting reflection failed ({e!r})")
        memory.shutdown()             # "I was off" anchors to this timestamp
        print(f"remix-brain: stopped after {time.monotonic() - started:.0f}s")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
