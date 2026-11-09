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
    "self_focused": False,  # the human is looking at HER window right now
    "idle_s": None,       # seconds since cursor or window last changed
    "plugged": None,      # True on AC, False on battery, None = unknown
    "muted": None,        # True while system audio is muted or very low
    "muted_s": None,      # seconds the audio has been quiet
    "app_since": None,    # monotonic time the current app took focus
    "up_s": 0.0,          # brain session age
    "at": 0.0,            # monotonic time of this snapshot
    "around": None,       # surroundings: {self, windows, elsewhere}
}
_last_cursor = None
_last_app = None
_active_at = _started      # last observed user activity (cursor or window)
_quiet_since = None        # when audio turned quiet (for muted_s)
_monitors: list = []        # cached `hyprctl monitors -j`
_monitors_at = 0.0         # monotonic time of that cache


# ------------------------------------------------------------ surroundings
#
# She is a window on a desktop full of other windows. `hyprctl clients -j`
# tells us each window's app CLASS and geometry (public compositor data), so
# she can know "firefox is to my left, partly behind me" and her eyes can turn
# toward it. Contents are never read: class names + rectangles, and a title
# only when redact() lets that title through.

import math

_ODD_TRANSFORMS = {1, 3, 5, 7}     # rotated outputs swap width/height
_CATEGORY = {                      # friendly words -> app classes she may mean
    "browser": {"firefox", "chromium", "chrome", "google-chrome", "brave",
                "brave-browser", "brave-origin", "brave-bin", "zen",
                "librewolf", "vivaldi", "floorp",
                "qutebrowser", "epiphany", "microsoft-edge"},
    "terminal": {"kitty", "alacritty", "foot", "footclient", "wezterm",
                 "konsole", "ghostty", "gnome-terminal", "xterm", "st",
                 "terminator", "tilix"},
    "editor": {"code", "code-oss", "codium", "vscodium", "zed", "kate",
               "gedit", "emacs", "sublime_text", "neovide", "obsidian"},
    "music": {"spotify", "strawberry", "rhythmbox", "amberol", "lollypop",
              "cider", "deadbeef"},
    "chat": {"discord", "vesktop", "webcord", "telegram", "telegramdesktop",
             "signal", "element", "slack", "thunderbird"},
    "game": {"steam", "lutris", "heroic", "prismlauncher", "minecraft"},
}


def _name_of(app_class: str) -> str:
    """'org.mozilla.firefox' -> 'firefox'. The short name she'd say aloud."""
    c = (app_class or "").strip().lower()
    return c.rsplit(".", 1)[-1] if "." in c else c


def _self_names() -> set:
    return {x.strip().lower() for x in (config.SELF_MATCH or "").split(",")
            if x.strip()}


def is_self(app_class: str | None, title: str | None = None) -> bool:
    """Is this hyprctl client HER OWN window? (class or title, exact)."""
    names = _self_names()
    return ((app_class or "").strip().lower() in names
            or (title or "").strip().lower() in names)


def parse_monitors_json(text: str) -> list:
    """`hyprctl monitors -j` -> [{id, name, x, y, w, h, ws, focused}] in LOGICAL
    (layout) pixels: width/height are physical, so divide by scale, and a
    rotated output swaps them. Window `at`/`size` are already logical."""
    try:
        data = json.loads((text or "").strip() or "[]")
    except ValueError:
        return []
    out = []
    for m in (data if isinstance(data, list) else [data]):
        try:
            w, h = float(m["width"]), float(m["height"])
            sc = float(m.get("scale") or 1.0) or 1.0
            if int(m.get("transform") or 0) in _ODD_TRANSFORMS:
                w, h = h, w
            out.append({
                "id": m.get("id"), "name": m.get("name"),
                "x": float(m.get("x") or 0), "y": float(m.get("y") or 0),
                "w": w / sc, "h": h / sc,
                "ws": (m.get("activeWorkspace") or {}).get("id"),
                "focused": bool(m.get("focused")),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def parse_clients_json(text: str) -> list:
    """`hyprctl clients -j` -> [{app, title(raw), x, y, w, h, ws, ws_name,
    monitor, floating, pinned, focused, fullscreen}] for mapped, visible-ish
    windows. Titles stay RAW here; build_around() runs them through redact()."""
    try:
        data = json.loads((text or "").strip() or "[]")
    except ValueError:
        return []
    out = []
    for c in (data if isinstance(data, list) else []):
        try:
            if not c.get("mapped", True) or c.get("hidden"):
                continue
            app = (c.get("class") or c.get("initialClass") or "").strip().lower()
            at, size = c.get("at") or [], c.get("size") or []
            if not app or len(at) != 2 or len(size) != 2:
                continue
            ws = c.get("workspace") or {}
            out.append({
                "app": app, "title": c.get("title") or "",
                "x": float(at[0]), "y": float(at[1]),
                "w": float(size[0]), "h": float(size[1]),
                "ws": ws.get("id"), "ws_name": ws.get("name"),
                "monitor": c.get("monitor"),
                "floating": bool(c.get("floating")),
                "pinned": bool(c.get("pinned")),
                "focused": c.get("focusHistoryID") == 0,
                "fullscreen": int(c.get("fullscreen") or 0) > 0,
            })
        except (TypeError, ValueError):
            continue
    return out


def _overlap(a: dict, b: dict) -> float:
    """Fraction of rect b that rect a covers (0..1)."""
    ix = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    iy = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    area = max(1.0, b["w"] * b["h"])
    return min(1.0, (ix * iy) / area)


def _gap(a: dict, b: dict) -> float:
    """Pixels of empty space between two rects (0 when touching/overlapping)."""
    dx = max(0.0, max(a["x"], b["x"]) - min(a["x"] + a["w"], b["x"] + b["w"]))
    dy = max(0.0, max(a["y"], b["y"]) - min(a["y"] + a["h"], b["y"] + b["h"]))
    return math.hypot(dx, dy)


_SECTORS = [  # angle (deg, 0 = right, 90 = DOWN since screen y grows down)
    "to my right", "down and to my right", "below me", "down and to my left",
    "to my left", "up and to my left", "above me", "up and to my right"]


def _direction(dx: float, dy: float) -> str:
    ang = math.degrees(math.atan2(dy, dx)) % 360.0
    return _SECTORS[int(((ang + 22.5) % 360.0) // 45.0)]


def look_vector(dx: float, dy: float, covered: float = 0.0) -> list | None:
    """Gaze [x, y] (-1..1, +y = down) for a target at offset (dx, dy) from her
    center. Direction only — her eyes can't reach, they point. A window mostly
    under her is a downward glance. None when there's no direction to give."""
    if covered >= 0.6 or math.hypot(dx, dy) < 8.0:
        return [0.0, 0.45] if covered >= 0.6 else None
    n = math.hypot(dx, dy)
    return [round(0.85 * dx / n, 2), round(0.85 * dy / n, 2)]


def build_around(clients: list, monitors: list, redactor=None,
                 max_n: int = 8) -> dict:
    """Pure: raw clients + monitors -> {self, windows, elsewhere}. `redactor`
    is redact() — the SAME privacy gate the focused title goes through.
    windows   = other windows on HER workspace+monitor (position known), nearest
                first; each {app, dir, near, covered, size, focused, look,
                [title]}
    elsewhere = [{app, where}] — other workspaces / screens (no position)."""
    me = next((c for c in clients if is_self(c["app"], c["title"])), None)
    if me is None:
        return {"self": None, "windows": [], "elsewhere": []}
    mon = next((m for m in monitors if m["id"] == me["monitor"]), None)
    area = (mon["w"] * mon["h"]) if mon else max(1.0, me["w"] * me["h"] * 4)
    cx, cy = me["x"] + me["w"] / 2.0, me["y"] + me["h"] / 2.0
    here, away = [], []
    for c in clients:
        if c is me or is_self(c["app"], c["title"]):
            continue
        if c["monitor"] == me["monitor"] and c["ws"] == me["ws"]:
            dx, dy = c["x"] + c["w"] / 2.0 - cx, c["y"] + c["h"] / 2.0 - cy
            # covered = how much of IT she hides; under = how much of HER sits
            # on top of it (a big window behind her). Either way: "behind me".
            cov = max(_overlap(me, c), _overlap(c, me))
            gap = _gap(me, c)
            ref = max(me["w"], me["h"])
            w = {
                "app": c["app"], "dir": _direction(dx, dy),
                "near": ("right beside me" if gap < ref * 0.15 else
                         "close" if gap < ref * 0.9 else "across the screen"),
                "covered": round(cov, 2),
                "size": ("huge" if c["w"] * c["h"] > area * 0.5 else
                         "small" if c["w"] * c["h"] < area * 0.1 else "mid-sized"),
                "focused": c["focused"],
                "look": look_vector(dx, dy, cov),
                "_dist": math.hypot(dx, dy),
            }
            t = redactor(c["app"], c["title"]) if redactor else None
            if t:
                w["title"] = t
            here.append(w)
        else:
            if c["monitor"] != me["monitor"]:
                where = "on the other screen"
            elif (c["ws"] or 0) < 0:
                where = "in the scratchpad"
            else:
                where = f"on workspace {c['ws_name'] or c['ws']}"
            away.append({"app": c["app"], "where": where})
    here.sort(key=lambda w: (not w["focused"], w["_dist"]))
    for w in here:
        w.pop("_dist", None)
    seen, uniq = set(), []
    for a in away:                       # one line per app+place
        k = (a["app"], a["where"])
        if k not in seen:
            seen.add(k)
            uniq.append(a)
    return {"self": {"x": me["x"], "y": me["y"], "w": me["w"], "h": me["h"],
                     "focused": me["focused"], "monitors": len(monitors)},
            "windows": here[:max(0, max_n)], "elsewhere": uniq[:max(0, max_n)]}


def around() -> dict:
    """Her latest surroundings ({self, windows, elsewhere}); empty when unknown."""
    return _state.get("around") or {"self": None, "windows": [], "elsewhere": []}


def find_mentioned(text: str) -> dict | None:
    """The visible window (on her screen) that `text` is talking about: a
    window's app name, or a category word (browser/terminal/editor/music/
    chat/game) that matches an app that's actually there. First mention in
    the text wins; among duplicates, the focused/nearest. None if nothing."""
    low = (text or "").lower()
    if not low:
        return None
    best = None                          # (position in text, window)
    for w in around().get("windows", []):
        app = _name_of(w["app"])
        names = {app}
        stem = app.split("-", 1)[0]     # 'brave-origin' -> whom they say: brave
        if stem != app and len(stem) >= 4:
            names.add(stem)
        for word, apps in _CATEGORY.items():
            if app in apps:
                names.add(word)
        for n in names:
            if len(n) < 3:
                continue
            m = re.search(r"(?<![a-z0-9])" + re.escape(n) + r"s?(?![a-z0-9])", low)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), w)
    return best[1] if best else None


def look_for_text(text: str) -> list | None:
    """Gaze toward whatever window `text` names — per beat, so her eyes follow
    her sentence. None when it names nothing she can point at."""
    w = find_mentioned(text)
    return list(w["look"]) if w and w.get("look") else None


_DIR_WORDS = ("my right", "my left", "above me", "below me", "beside me",
              "behind me", "over me", "under me")


def gaze_for_dir(text: str) -> list | None:
    """Aim her eyes at the window a direction phrase points at ("to my
    right", "above me", "the one beside me"...). Pairs with look_for_text:
    those need an app NAME, this catches the plain geometry words she (or
    the human) actually use. None when nothing on her screen matches."""
    low = (text or "").lower()
    if not any(k in low for k in _DIR_WORDS):
        return None
    for w in around().get("windows", []):
        d = (w.get("dir") or "").lower()
        if any(k in low and k in d for k in _DIR_WORDS):
            return list(w["look"]) if w.get("look") else None
    return None


def gaze_toward(app: str | None = None) -> list | None:
    """Gaze at the focused window (or the one named `app`), measured from HER
    window — not from the screen center (she is a floating window, not the
    whole screen). None when it can't be known (not Hyprland, she isn't found,
    she's on top of it, no such window)."""
    ws = around().get("windows", [])
    want = (app or "").lower()
    for w in ws:
        if (want and _name_of(w["app"]) == _name_of(want)) or \
                (not want and w["focused"]):
            return list(w["look"]) if w.get("look") else None
    return None


def describe_around(a: dict | None = None) -> list:
    """Plain-English lines for the system prompt and /aware (already redacted)."""
    a = a if a is not None else around()
    if not a.get("self"):
        return []
    lines = []
    for w in a.get("windows", []):
        bits = [w["dir"], w["near"]]
        if w["covered"] >= 0.6:
            bits = ["right behind me (I'm sitting on top of it)"]
        elif w["covered"] >= 0.1:
            bits.append("partly behind me")
        bits.append(w["size"])
        s = f'{w["app"]}{" (focused)" if w["focused"] else ""}: ' + ", ".join(bits)
        if w.get("title"):
            s += f' — title "{w["title"]}"'
        lines.append(s)
    for e in a.get("elsewhere", []):
        lines.append(f'{e["app"]}: {e["where"]} (out of sight)')
    return lines


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
    global _last_app, _active_at, _state
    parsed = parse_window_json(_hyprctl(["activewindow", "-j"]) or "")
    if parsed is None:
        # No readable window (bare desktop, locked, non-Hyprland): the machine
        # told her nothing this tick — so nothing is CURRENT truth. Keep the
        # last app only as history; app goes None so she never claims a
        # focus label she can't see (the 2026-11-08 "stale brave" bug).
        _state.pop("title", None)
        _state["app"] = None
        _state["self_focused"] = False
        return
    if is_self(parsed["app"], parsed["title"]):
        # The human is typing AT her. That's activity — and an honest fact:
        # SHE is what's in focus right now, not the last app they had open.
        # Keep the last real app (she can still talk about it), but say so.
        _active_at = now
        _state["self_focused"] = True
        return
    _state["self_focused"] = False
    app = parsed["app"]
    if app != _last_app:
        _last_app = app
        _active_at = now               # a window switch is activity
        _state["app_since"] = now
    _state["app"] = app
    _state["fullscreen"] = parsed["fullscreen"]
    title = redact(app, parsed["title"])   # THE gate; off mode -> always None
    if title:
        _state["title"] = title
    else:
        _state.pop("title", None)


def _observe_around(now: float) -> None:
    """Poll every window + (cached) monitors -> her surroundings."""
    global _monitors, _monitors_at
    if not config.SURROUNDINGS:
        _state["around"] = None
        return
    if not _monitors or now - _monitors_at > 30.0:
        mons = parse_monitors_json(_hyprctl(["monitors", "-j"]) or "")
        if mons:
            _monitors, _monitors_at = mons, now
    raw = _hyprctl(["clients", "-j"])
    if raw is None:
        return                         # not Hyprland / hiccup: keep last view
    _state["around"] = build_around(parse_clients_json(raw), _monitors,
                                    redactor=redact,
                                    max_n=config.SURROUNDINGS_MAX)


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
    await asyncio.to_thread(_observe_around, now)
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
    head = " ".join(bits)
    extra = describe_around(s.get("around"))
    return head + ("".join(f"\n  · {x}" for x in extra) if extra else "")


# --------------------------------------------------------------- test hooks

def seed_for_test(**kw) -> None:
    """Poke the snapshot directly in tests (already-redacted values only)."""
    _state.update(kw)


def reset_for_test() -> None:
    global _last_cursor, _last_app, _active_at, _quiet_since, _monitors, _monitors_at
    _monitors, _monitors_at = [], 0.0
    _state.update(around=None, app=None, fullscreen=False, self_focused=False,
                  idle_s=None, plugged=None,
                  muted=None, muted_s=None, app_since=None,
                  up_s=0.0, at=0.0)
    _state.pop("title", None)
    _last_cursor = None
    _last_app = None
    _active_at = time.monotonic()
    _quiet_since = None
