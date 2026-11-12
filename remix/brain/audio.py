# audio.py — is the machine's audio muted or turned down?
#
# The on-screen text rule (2026-11-08): her face shows FLAVOR text only when
# the system is quiet (muted or low volume) — if her voice would be audible,
# a caption is the wrong channel. Functional lines (mic transcript, "one sec",
# ear problems) are exempt: they exist even when she has no voice.
#
# Detector: `wpctl get-volume @DEFAULT_AUDIO_SINK@` (WirePlumber/PipeWire).
#   Output is `Volume: 0.68` and gains a `[MUTED]` marker when muted.
#   Verified 2026-11-08 against the wpctl(1) manual (WirePlumber 0.5.x).
# wpctl missing or failing -> "quiet" (fails open to text: that was the
# pre-gate behavior, so a broken desktop layer must not silence her).
#
# Phase 4 (plan 2.3): polling became the FALLBACK. The primary source is
# `pactl subscribe` (works over pipewire-pulse): PipeWire pushes an event
# line on every sink/server change, so a mute is seen in well under a
# second instead of on the next TTL tick. Events just invalidate the cache
# and refresh — wpctl stays the single source of truth. When pactl is
# missing the TTL loop keeps everything working exactly as before.
# Consumers can register an edge callback (`on_change`): fired when the
# quiet answer FLIPS, e.g. main's live mute reflex (stop + caption).
import asyncio
import re
import shutil
import subprocess
import time

from . import config

_state = {"quiet": True, "checked_at": 0.0, "warned": False}
_VOL_RE = re.compile(r"(\d+(?:\.\d+)?)")
_change_cbs: list = []       # coroutine fns fired on quiet flips


def _query() -> bool | None:
    """True = quiet (muted or low), False = audible, None = can't tell."""
    wpctl = shutil.which("wpctl")
    if not wpctl:
        return None
    try:
        r = subprocess.run(
            [wpctl, "get-volume", "@DEFAULT_AUDIO_SINK@"],
            capture_output=True, text=True, timeout=2.0)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out:
        return None
    if "MUTED" in out.upper():
        return True
    m = _VOL_RE.search(out)
    if not m:
        return None
    try:
        vol = float(m.group(1))
    except ValueError:
        return None
    return vol <= config.QUIET_VOLUME


def quiet() -> bool:
    """Cached 'may flavor text show' answer. Never blocks, never spawns —
    reads whatever refresh() last learned (start-up default: quiet/text ok)."""
    return bool(_state["quiet"])


def on_change(cb) -> None:
    """Register a coroutine fn fired when the quiet answer flips
    (it receives the NEW quiet state). Called by the event loop and by
    the TTL fallback alike, so edges are edges either way."""
    _change_cbs.append(cb)


async def refresh() -> None:
    """One wpctl query, off the event loop; fires edge callbacks on flips."""
    answer = await asyncio.to_thread(_query)
    await _apply(answer)


async def _apply(answer: bool | None) -> None:
    """Fold one detector answer in; fire callbacks when quiet flipped."""
    was = bool(_state["quiet"])
    if answer is None:
        if not _state["warned"]:
            print("remix-brain: audio gate: wpctl unavailable — flavor text "
                  "always allowed (rule can't be enforced)")
            _state["warned"] = True
        _state["quiet"] = True
    else:
        _state["quiet"] = answer
    _state["checked_at"] = time.monotonic()
    if _change_cbs and bool(_state["quiet"]) != was:
        for cb in list(_change_cbs):
            try:
                asyncio.get_running_loop().create_task(cb(_state["quiet"]))
            except Exception:
                pass          # a broken listener never breaks the gate


async def gate_loop() -> None:
    """Fallback refresher (the TTL walk) so notice emission never waits on
    a subprocess even without the event source."""
    while True:
        await refresh()
        await asyncio.sleep(config.AUDIO_GATE_TTL_S)


def _event_relevant(line: str) -> bool:
    """pactl subscribe lines look like:  Event 'change' on sink #71
    Only sink/server events can change the DEFAULT sink's mute or volume."""
    m = re.match(r"Event '([a-z]+)' on ([a-z]+)", (line or "").strip())
    if not m:
        return False
    return m.group(1) in ("change", "remove", "new") and \
        m.group(2) in ("sink", "server")


async def event_loop() -> None:
    """The primary source (Phase 4): read pactl subscribe until the pipe
    ends, refresh on every relevant event. Ends quietly when pactl is
    missing (the TTL loop keeps polling)."""
    pactl = shutil.which("pactl")
    if not pactl or not config.AUDIO_EVENTS:
        return
    while True:
        try:
            proc = await asyncio.create_subprocess_exec(
                pactl, "subscribe", stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
        except OSError:
            return
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break            # pactl died (PipeWire restart?) — respawn
                if _event_relevant(line.decode("utf-8", "replace")):
                    await refresh()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"remix-brain: audio event loop hiccup ({e!r})")
            await asyncio.sleep(2.0)
        finally:
            try:
                proc.kill()
            except Exception:
                pass
            await asyncio.sleep(1.0)   # don't spin on a dying desktop layer


# ---------------------------------------------------------------- test hooks

def set_for_test(is_quiet: bool) -> None:
    _state["quiet"] = is_quiet


def reset_for_test() -> None:
    _state.update(quiet=True, checked_at=0.0, warned=False)
    _change_cbs.clear()
