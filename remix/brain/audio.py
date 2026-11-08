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
import asyncio
import re
import shutil
import subprocess
import time

from . import config

_state = {"quiet": True, "checked_at": 0.0, "warned": False}
_VOL_RE = re.compile(r"(\d+(?:\.\d+)?)")


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


async def refresh() -> None:
    """One wpctl query, off the event loop, result cached for gate_loop's TTL."""
    answer = await asyncio.to_thread(_query)
    if answer is None:
        if not _state["warned"]:
            print("remix-brain: audio gate: wpctl unavailable — flavor text "
                  "always allowed (rule can't be enforced)")
            _state["warned"] = True
        _state["quiet"] = True
    else:
        _state["quiet"] = answer
    _state["checked_at"] = time.monotonic()


async def gate_loop() -> None:
    """Background refresher so notice emission never waits on a subprocess."""
    while True:
        await refresh()
        await asyncio.sleep(config.AUDIO_GATE_TTL_S)


# ---------------------------------------------------------------- test hooks

def set_for_test(is_quiet: bool) -> None:
    _state["quiet"] = is_quiet


def reset_for_test() -> None:
    _state.update(quiet=True, checked_at=0.0, warned=False)
