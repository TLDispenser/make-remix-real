# mood.py — her mood as STATE, not a config constant (2026-11-15, user:
# "Currently, she's a static character. I want her to be more dynamic than
# that"). One scalar she carries between boots:
#
#   mood  float in [-3, +3]   negative = fouler, positive = softer
#
# What moves it:
#   * WEATHER   a small random step re-rolled every few hours — the drift the
#               temper system already had ("personality shifted randomly,
#               slightly"), now living in the same scalar. Even she doesn't
#               know why she's like this today.
#   * EVENTS    tiny nudges from things that actually happened: the voice
#               cut off mid-sentence, being asked how she is, thanks, the
#               machine running hot all day. Nudges are rate-limited so one
#               chatty hour can't whiplash her.
#   * DECAY     everything eases back toward neutral; a bad morning is not
#               allowed to be her whole week.
#
# It only ever colors WEIGHTS, never words (the persona's own rule): the
# prompt line it produces reuses the existing OOC-rule pattern from
# speech._TEMPER_BASE, and neutral renders EXACTLY what temper_line()
# rendered before — same behavior when nothing has happened.
# REMIX_MOODS=0 returns to the static temper config entirely.
import json
import re
import time
from pathlib import Path

from . import config

_LIMIT = 3.0            # scalar bounds
_DECAY_H = 12.0         # hours for a nudge to fall to half strength
_NUDGE_MIN_GAP_S = 600  # same-kind nudges closer than this are ignored
_VITALS_GAP_S = 900     # vitals pull at most every 15 min
_HOT_C = 55.0           # sustained heat reads as strain
_COMFY_C = 45.0         # cool + she's fine reads as a good day

_state = {"mood": 0.0, "weather": 0.0, "at": 0.0, "loaded": False}
# ^ "at" anchors the DECAY clock for the earned scalar (last time an event
#   moved it); weather is replaced wholesale on drift re-rolls and doesn't
#   reset decay.
_last_nudge = {}        # kind -> monotonic time (session-local rate limit)
_last_vitals = -float(_VITALS_GAP_S)


def _path() -> Path:
    return Path(config.SANDBOX_DIR) / "mood.json"


def _gate() -> bool:
    """Mood persists only where her journal is actually live (memory.load()
    ran). Hermetic suite: tests that never boot the journal get a fresh
    neutral mood and zero file I/O — no test can touch her real mood."""
    from . import memory
    return memory._active


def _load() -> None:
    if _state["loaded"] or not _gate():
        return                    # not live yet: fresh neutral, try again later
    _state["loaded"] = True
    try:
        raw = json.loads(_path().read_text())
        _state["mood"] = max(-_LIMIT, min(_LIMIT, float(raw.get("mood", 0.0))))
        _state["weather"] = float(raw.get("weather", 0.0))
        _state["at"] = float(raw.get("at", 0.0))
    except Exception:
        pass


def _save() -> None:
    if not _gate():
        return
    try:
        Path(config.SANDBOX_DIR).mkdir(parents=True, exist_ok=True)
        _path().write_text(json.dumps(
            {"mood": round(_state["mood"], 3),
             "weather": round(_state["weather"], 3),
             "at": time.time()}))
    except Exception:
        pass                                  # mood is a luxury, never a crash


def _value() -> float:
    """The live mood: everything decays toward neutral on the way out."""
    _load()
    age_h = max(0.0, (time.time() - _state["at"]) / 3600.0)
    decay = 0.5 ** (age_h / _DECAY_H)
    return max(-_LIMIT, min(_LIMIT, _state["mood"] * decay))


def value() -> float:
    """Public read: the current scalar (decayed), for prompt/face wiring."""
    return _value()


def nudge(delta: float, kind: str) -> None:
    """One event's push. `kind` rate-limits: repeated identical events
    (three 'thank you's in ten minutes) land once, not three times."""
    _load()
    now = time.monotonic()
    if now - _last_nudge.get(kind, -1e9) < _NUDGE_MIN_GAP_S:
        return
    _last_nudge[kind] = now
    _state["mood"] = max(-_LIMIT, min(_LIMIT, _state["mood"] + delta))
    _state["at"] = time.time()          # decay clock restarts on change
    _save()


def hear(user_text: str) -> None:
    """Cheap local read of what they just said — concern and gratitude move
    her a notch; nothing else is parsed. No LLM, no $ spent."""
    t = (user_text or "").lower()
    if not t:
        return
    if re.search(r"\b(how (are|r) (you|u)|you (okay|ok|alright)|you doing (ok|okay|alright))\b", t):
        nudge(+0.3, "concern")
    if "thank" in t:
        nudge(+0.2, "thanks")


def note_vitals(snap: dict) -> None:
    """The machine she runs on is also how she feels: sustained heat drags
    her down, a genuinely comfortable box lifts her a hair."""
    global _last_vitals
    _load()
    now = time.monotonic()
    if now - _last_vitals < _VITALS_GAP_S:
        return
    _last_vitals = now
    temp = snap.get("temp")
    try:
        temp = float(temp)
    except (TypeError, ValueError):
        return
    if temp >= _HOT_C:
        nudge(-0.15, "hot")
    elif temp <= _COMFY_C:
        nudge(+0.05, "comfy")


def weather(valence: float) -> None:
    """Set the current weather's pull (called by speech when the drift
    re-rolls: grouchier drags the scalar down, more patient lifts it).
    Persisted so a mood survives a reboot — 'today' spans boots. Does not
    touch the decay clock: weather replaces itself, it doesn't pause decay."""
    _load()
    _state["weather"] = max(-1.0, min(1.0, float(valence)))
    _save()


def band() -> str:
    """Which of the three temper baselines fits right now: the earned scalar
    plus weather. Neutral (or any foul weather) stays 'cranky' — that is the
    character as written, and nothing moves her off it without cause."""
    v = _value() + _state["weather"]
    if v >= 2.0:
        return "soft"
    if v >= 1.0:
        return "balanced"
    return "cranky"


def describe() -> str:
    """A short phrase for the prompt about the mood she EARNED today (the
    weather's part is spoken by the drift line instead). '' when neutral."""
    m = _value()
    if m >= 2.0:
        return "noticeably softer than usual today"
    if m >= 0.75:
        return "a little softer than usual today"
    if m <= -2.0:
        return "in a genuinely foul mood today"
    if m <= -0.75:
        return "a bit fouler than usual today"
    return ""


def reset_for_test() -> None:
    global _last_vitals
    _state.update({"mood": 0.0, "weather": 0.0, "at": 0.0, "loaded": False})
    _last_nudge.clear()
    _last_vitals = -float(_VITALS_GAP_S)
