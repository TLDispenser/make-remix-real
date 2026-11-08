# awareness.py — Phase 6: the world outside her own vitals.
#
# Senses (all fail-soft; anything unknown is just absent from the snapshot):
#   active window  `hyprctl activewindow -j` -> the app CLASS + fullscreen flag
#   user idle      `hyprctl cursorpos` polling; cursor or app change = activity.
#                  Limit: a keyboard-only stretch with no window change reads as
#                  idle — accepted, documented, tune via REMIX_AWARENESS_INTERVAL_S.
#   AC plug state  /sys/class/power_supply (Mains `online`, else battery status)
#   audio          reuses audio.py's wpctl gate (muted / very low)
#   session age    brain process uptime
#
# PRIVACY (spec, non-negotiable by default): window TITLES never leave this
# module raw. Modes (REMIX_TITLES):
#   off    only the app CLASS name passes (e.g. "firefox" — a program name,
#          not content). A title passes ONLY for classes listed in
#          REMIX_TITLE_ALLOW, and even then through scrub_title() first.
#   scrub  every focused-window title passes through scrub_title() — emails,
#          URLs, IPs and file paths stripped.
#   raw    the user's explicit opt-in-EVERYTHING: titles pass untouched
#          (length-capped). The user owns that risk; the code's only jobs are
#          to make it a conscious choice and to widen grounding to match so
#          she isn't punished for quoting what she legitimately saw.
# Jev and the LLM can never see a raw title in off/scrub mode, a URL, a path,
# or anything on screen.
#
# The snapshot is a plain dict of primitives: app, fullscreen, idle_s, plugged,
# muted, muted_s (how long audio has been quiet), app_since, up_s, at — plus
# "title" ONLY when the title policy let one through (absent otherwise).
import asyncio
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import audio, config

_started = time.monotonic()
_state = {
    "app": None,          # focused app class, e.g. "firefox" (name only)
    "fullscreen": False,  # focused window is fullscreen (DND signal)
    "idle_s": None,       # seconds since cursor or window last changed
    "plugged": None,      # True on AC, False on battery, None = unknown
    "muted": None,        # True while system audio is muted or very low
    "muted_s": None,      # seconds the audio has been quiet
    "app_since": None,    # monotonic time the current app took focus
    "up_s": 0.0,          # brain session age
    "at": 0.0,            # monotonic time of this snapshot
}
_last_cursor = None
_last_app = None
_active_at = _started      # last observed user activity (cursor or window)
_quiet_since = None        # when audio turned quiet (for muted_s)
_screen_wh = None          # [w, h] of the primary output (cached)


def gaze_toward() -> list | None:
    """A look [x, y] aimed at the focused window's center (the 5 gesture:
    she can gesture toward the thing on screen she's commenting on). Public
    geometry only — never the screen contents. None when unknown/offline."""
    at = _state.get("win_at")
    size = _state.get("win_size")
    if not at or not size or len(at) != 2 or len(size) != 2:
        return None
    if _screen_wh is None:
        _read_screen()
    if not _screen_wh:
        return None
    sw, sh = _screen_wh
    fx = at[0] + size[0] / 2.0
    fy = at[1] + size[1] / 2.0
    # She fills nearly the whole screen (her window is her world), so screen
    # center is "straight ahead": offset = direction to look at.
    dx = (fx - sw / 2.0) / (sw / 2.0)
    dy = (fy - sh / 2.0) / (sh / 2.0)   # +y = down = compositor +y = look down
    out = [max(-0.85, min(0.85, round(dx, 2))),
           max(-0.85, min(0.85, round(dy, 2)))]
    return out if abs(out[0]) + abs(out[1]) > 0.06 else None


def _read_screen() -> None:
    """One `hyprctl monitors -j`; take the first monitor's width/height."""
    global _screen_wh
    import json as _json
    raw = _hyprctl(["monitors", "-j"]) or ""
    if not raw.startswith("[") and not raw.startswith("{"):
        return
    try:
        data = _json.loads(raw)
        mons = data if isinstance(data, list) else [data]
        for m in mons:
            w, h = m.get("width"), m.get("height")
            if w and h:
                _screen_wh = [int(w), int(h)]
                return
    except ValueError:
        pass


# ------------------------------------------------------------- redaction

_URL = re.compile(r"(?:https?://|www\.)\S+|[a-z0-9.-]+\.(?:com|org|net|io|dev|edu|gov|co|uk)\S*", re.I)
_EMAIL = re.compile(r"\S+@\S+")
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_PATH = re.compile(r"(?:/[\w.-]+){2,}|~/?\w*(?:/[\w.-]+)+|[A-Za-z]:\\(?:[\w.-]+\\?)+")
_WS = re.compile(r"\s+")


def scrub_title(title: str) -> str | None:
    """Last-resort scrub for titles that ARE allowed through. Strips emails,
    URLs, IPs and file paths (emails FIRST: the URL/domain rule would eat
    'example.com' out of an address and leave the local part behind); drops
    anything too long to be safe."""
    s = _EMAIL.sub(" ", title or "")
    s = _URL.sub(" ", s)
    s = _IP.sub(" ", s)
    s = _PATH.sub(" ", s)
    s = _WS.sub(" ", s).strip(" -–—|:;,")
    if not s or len(s) > 80:
        return None
    return s


def _allowlist() -> set:
    """App classes whose window titles may pass (still scrubbed). Re-read each
    call so REMIX_TITLE_ALLOW can be changed without restarting the brain."""
    raw = config.TITLE_ALLOW or ""
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


def redact(app_class: str | None, title: str | None):
    """THE privacy gate. Returns a safe title or None. REMIX_TITLES modes:
    off (default) = nothing passes unless the class is in TITLE_ALLOW (still
    scrubbed); scrub = everything passes scrubbed; raw = everything passes
    untouched (200-char cap — a safety rail, not redaction)."""
    if not title:
        return None
    mode = (config.TITLES or "off").strip().lower()
    if mode == "raw":
        return title.strip()[:200] or None
    if mode == "scrub":
        return scrub_title(title)
    if (app_class or "").strip().lower() in _allowlist():
        return scrub_title(title)
    return None


def parse_window_json(text: str) -> dict | None:
    """`hyprctl activewindow -j` output -> {app, title(raw), fullscreen,
    at, size}. None when there is no window / bad output (empty desktop
    prints `Invalid`). at/size are the window's geometry — public compositor
    data, used only for gaze-toward gestures (never spoken)."""
    t = (text or "").strip()
    if not t or not t.startswith("{"):
        return None
    try:
        data = json.loads(t)
    except ValueError:
        return None
    app = data.get("class") or data.get("initialClass") or ""
    title = data.get("title") or ""
    if not app:
        return None
    try:
        full = int(data.get("fullscreen") or 0) > 0
    except (TypeError, ValueError):
        full = bool(data.get("fullscreen"))
    return {"app": app.strip().lower(), "title": title, "fullscreen": full,
            "at": [int(v) for v in (data.get("at") or [])][:2] or None,
            "size": [int(v) for v in (data.get("size") or [])][:2] or None}


def parse_cursor(text: str) -> tuple | None:
    """`hyprctl cursorpos` output ('123, 456') -> (123, 456) or None."""
    try:
        x, _, y = (text or "").strip().partition(",")
        return (int(float(x.strip())), int(float(y.strip())))
    except ValueError:
        return None


def read_plugged(root: str | None = None) -> bool | None:
    """AC plug state from sysfs. True on AC, False on battery, None unknown.
    `root` overrides /sys/class/power_supply for tests."""
    base = Path(root or "/sys/class/power_supply")
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return None
    bat_status = None
    for dev in entries:
        try:
            kind = (dev / "type").read_text().strip()
        except OSError:
            continue
        if kind in ("Mains", "USB", "UPS") or dev.name.startswith(("AC", "ADP")):
            online = dev / "online"
            if online.exists():
                try:
                    return online.read_text().strip() == "1"
                except OSError:
                    pass
        elif kind == "Battery" and bat_status is None:
            status = dev / "status"
            try:
                bat_status = status.read_text().strip().lower()
            except OSError:
                bat_status = None
    if bat_status in ("charging", "full"):
        return True
    if bat_status == "discharging":
        return False
    return None


# --------------------------------------------------------------- polling

def _hyprctl(args: list) -> str | None:
    if not shutil.which("hyprctl"):
        return None
    try:
        r = subprocess.run(["hyprctl"] + args, capture_output=True,
                           text=True, timeout=2.0)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return (r.stdout or "").strip() or None


def _observe_window(now: float) -> None:
    """Poll the focused window; fold it into the activity/idle tracking."""
    global _last_app, _active_at, _state, _screen_wh
    parsed = parse_window_json(_hyprctl(["activewindow", "-j"]) or "")
    if parsed is None:
        # No readable window (desktop, locked, non-Hyprland): keep last app,
        # but never keep a stale title or stale geometry.
        _state.pop("title", None)
        _state.pop("win_at", None)
        _state.pop("win_size", None)
        return
    app = parsed["app"]
    if app != _last_app:
        _last_app = app
        _active_at = now               # a window switch is activity
        _state["app_since"] = now
    _state["app"] = app
    _state["fullscreen"] = parsed["fullscreen"]
    if parsed.get("at"):
        _state["win_at"] = parsed["at"]
    if parsed.get("size"):
        _state["win_size"] = parsed["size"]
    title = redact(app, parsed["title"])   # THE gate; off mode -> always None
    if title:
        _state["title"] = title
    else:
        _state.pop("title", None)


def _observe_cursor(now: float) -> None:
    global _last_cursor, _active_at
    cur = parse_cursor(_hyprctl(["cursorpos"]) or "")
    if cur is None:
        return
    if cur != _last_cursor:
        _active_at = now               # the mouse moved: user is there
    _last_cursor = cur


async def refresh() -> dict:
    """One poll of every sense. Never raises; missing senses stay as they were."""
    global _quiet_since, _state
    now = time.monotonic()
    await asyncio.to_thread(_observe_window, now)
    await asyncio.to_thread(_observe_cursor, now)
    plugged = await asyncio.to_thread(read_plugged)
    if plugged is not None:
        _state["plugged"] = plugged

    await audio.refresh()          # cheap, cached; shared with the notice gate
    quiet = audio.quiet()
    _state["muted"] = quiet
    if quiet and _quiet_since is None:
        _quiet_since = now
    elif not quiet:
        _quiet_since = None

    _state["muted_s"] = ((now - _quiet_since)
                          if (quiet and _quiet_since is not None) else None)
    _state["idle_s"] = max(0.0, now - _active_at)
    _state["up_s"] = now - _started
    _state["at"] = now
    return current()


def current() -> dict:
    """The latest snapshot. A copy: callers may hold it across polls."""
    return dict(_state)


async def watch_loop() -> None:
    """Background poller. Keeps the snapshot warm so request-time code never
    waits on a subprocess."""
    while True:
        try:
            await refresh()
        except Exception as e:     # a sense must never take the brain down
            print(f"remix-brain: awareness poll failed ({e!r})")
        await asyncio.sleep(config.AWARENESS_INTERVAL_S)


def describe(aware: dict | None = None) -> str:
    """One human line for the /aware debug command (already-redacted data)."""
    s = aware if aware is not None else current()
    bits = [f"app={s.get('app') or '?'}"]
    if s.get("title"):
        bits.append(f'title="{s["title"]}"')   # only present if opted in
    bits.append("fullscreen" if s.get("fullscreen") else "windowed")
    idle = s.get("idle_s")
    bits.append(f"idle {round(idle / 60)}m" if idle is not None and idle > 90
                else "active")
    bits.append({True: "AC", False: "battery", None: "power?"}[s.get("plugged")])
    bits.append("quiet" if s.get("muted") else "audible")
    return " ".join(bits)


# --------------------------------------------------------------- test hooks

def seed_for_test(**kw) -> None:
    """Poke the snapshot directly in tests (already-redacted values only)."""
    _state.update(kw)


def reset_for_test() -> None:
    global _last_cursor, _last_app, _active_at, _quiet_since
    _state.update(app=None, fullscreen=False, idle_s=None, plugged=None,
                  muted=None, muted_s=None, app_since=None,
                  up_s=0.0, at=0.0)
    _state.pop("title", None)
    _state.pop("win_at", None)
    _state.pop("win_size", None)
    _last_cursor = None
    _last_app = None
    _active_at = time.monotonic()
    _quiet_since = None
