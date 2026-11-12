# bits.py — Phase 4 (plan 2.3 #5/#6): the laptop's own life.
#
# She is your laptop, so she notices what you do TO her: lid, sleep, screen
# lock, headphones, HDMI, bluetooth, wifi, brightness, USB, screenshots.
# Every detector is local, cheap and fail-soft (unknown = no edge, never a
# lie). poll() returns EDGES — a bit speaks once per crossing, never in
# bulk — and each carries its own plain-fact clause, which is the SEED the
# proactive words grow from. The POLICY (speak / caption / look / quiet)
# is Jev's `form` decision, not a table here; the only walls are the
# per-bit cooldown floors in config.BIT_COOLDOWN_FLOORS_S.
#
# Suspend is special: the brain is frozen while the machine sleeps, so
# there is no event to catch — instead wall time races monotonic by the
# sleep length, and a big gap IS the event ("she was asleep").
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import config

_state = {
    "wall": time.time(),      # last poll's wall clock (suspend detector)
    "mono": time.monotonic(),
}
_prev: dict = {}              # detector -> last value (None = never seen)


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------- detectors

def _lid(root: str | None = None) -> bool | None:
    """True = lid closed. /proc/acpi/button/lid/*/state -> 'state: closed'."""
    base = Path(root or "/proc/acpi/button/lid")
    try:
        for dev in sorted(base.iterdir()):
            s = (dev / "state")
            if not s.exists():
                continue
            m = re.search(r"closed|open", s.read_text() or "")
            if m:
                return m.group(0) == "closed"
    except OSError:
        pass
    return None


def _brightness(root: str | None = None) -> float | None:
    """The brightest backlight level (a value change is the edge)."""
    base = Path(root or "/sys/class/backlight")
    best = None
    try:
        for dev in sorted(base.iterdir()):
            try:
                v = _f((dev / "brightness").read_text())
            except OSError:
                continue
            if v is not None and (best is None or v > best):
                best = v
    except OSError:
        pass
    return best


def _wifi(root: str | None = None) -> bool | None:
    """True = a wireless interface is up. /sys/class/net/wl*/operstate."""
    try:
        for dev in sorted(Path(root or "/sys/class/net").iterdir()):
            if not dev.name.startswith("wl"):
                continue
            try:
                if (dev / "operstate").read_text().strip() == "up":
                    return True
            except OSError:
                continue
        return False
    except OSError:
        return None


def _bluetooth() -> int | None:
    """How many devices are currently connected (a count change is the edge).
    None when bluetoothctl is missing or answers nothing useful."""
    bt = shutil.which("bluetoothctl")
    if not bt:
        return None
    try:
        r = subprocess.run([bt, "devices", "Connected"], capture_output=True,
                           text=True, timeout=4.0)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return len([ln for ln in (r.stdout or "").splitlines() if ln.strip()])


def _usb(root: str | None = None) -> int | None:
    """Count of real USB devices (directories like 1-2, 1-2.3 — not the
    :1.0 interfaces, not the usbN root hubs). A count RISE is the edge."""
    base = Path(root or "/sys/bus/usb/devices")
    try:
        return len([d.name for d in base.iterdir()
                    if re.fullmatch(r"\d+-[\d.]+", d.name)])
    except OSError:
        return None


def _audio_ports(run=None) -> dict | None:
    """{'headphones': bool|None, 'hdmi': bool|None} from pactl's JSON:
    headphones = the DEFAULT sink's active port, HDMI = any sink named
    like an HDMI output (plugged sinks exist, unplugged ones vanish).
    None when pactl or the JSON mode is unavailable."""
    pactl = shutil.which("pactl")
    if not pactl:
        return None
    runner = run or (lambda cmd: subprocess.run(
        cmd, capture_output=True, text=True, timeout=4.0))

    def _js(cmd):
        try:
            r = runner(cmd)
            if r.returncode != 0:
                return None
            return json.loads(r.stdout or "[]")
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return None

    sinks = _js([pactl, "-f", "json", "list", "sinks"])
    if not isinstance(sinks, list):
        return None
    info = _js([pactl, "-f", "json", "info"]) or {}
    default = info.get("default_sink") if isinstance(info, dict) else None
    head, hdmi = None, False
    for s in sinks:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "")
        if "hdmi" in name.lower():
            hdmi = True
        if default and name == default:
            port = str(s.get("active_port") or "").lower()
            if port:
                head = "headphone" in port
    return {"headphones": head, "hdmi": hdmi or None}


def _locked() -> bool | None:
    """True while the screen is locked (hyprlock running). Cheap pgrep."""
    pgrep = shutil.which("pgrep")
    if not pgrep:
        return None
    try:
        r = subprocess.run([pgrep, "-x", "hyprlock"], capture_output=True,
                           timeout=3.0)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode not in (0, 1):
        return None
    return r.returncode == 0


def _screenshots(dirs=None) -> int | None:
    """Count of files in the screenshot folders (a count RISE is the edge).
    Names are never read past the count — a filename could hold anything."""
    n = 0
    seen_any = False
    for d in (dirs or config.SCREENSHOT_DIRS):
        try:
            n += len([p for p in Path(d).iterdir() if p.is_file()])
            seen_any = True
        except OSError:
            continue
    return n if seen_any else None


# ----------------------------------------------------------------- edges

def poll(now_wall: float | None = None, now_mono: float | None = None) -> list:
    """One slow pass over every bit. Returns edges since the LAST poll:
    [{'trigger': 'lid_closed', 'fact': 'the lid just closed'}, ...] —
    most recent crossing of each bit at most once. Never raises."""
    global _state
    wall = time.time() if now_wall is None else now_wall
    mono = time.monotonic() if now_mono is None else now_mono
    out = []

    # suspend: wall raced monotonic while she was asleep
    wall_gap = wall - _state["wall"]
    mono_gap = mono - _state["mono"]
    _state.update(wall=wall, mono=mono)
    if (wall_gap >= config.RESUME_MIN_S
            and mono_gap < wall_gap * 0.5):
        out.append({"trigger": "suspend",
                    "fact": f"she was asleep for about "
                            f"{max(1, round(wall_gap / 60))} minutes and "
                            f"just woke up"})

    vals = {"lid": _lid(), "brightness": _brightness(), "wifi": _wifi(),
            "bt": _bluetooth(), "usb": _usb(), "audio": _audio_ports(),
            "locked": _locked(), "shots": _screenshots()}

    def edge(trigger: str, fact: str, when_changed: bool) -> None:
        if when_changed:
            out.append({"trigger": trigger, "fact": fact})

    lid = vals["lid"]
    if lid is not None and _prev.get("lid") is not None:
        edge("lid_closed", "the lid just closed", lid and not _prev["lid"])
        edge("lid_opened", "the lid just opened", not lid and _prev["lid"])
    br = vals["brightness"]
    pbr = _prev.get("brightness")
    if br is not None and pbr is not None:
        edge("brightness_changed",
             "the screen brightness just changed", abs(br - pbr) > 0.5)
    wifi = vals["wifi"]
    if wifi is not None and _prev.get("wifi") is not None:
        edge("wifi_up", "the wifi came back up", wifi and not _prev["wifi"])
        edge("wifi_down", "the wifi just dropped", not wifi and _prev["wifi"])
    bt = vals["bt"]
    pbt = _prev.get("bt")
    if bt is not None and pbt is not None:
        edge("bt_connected", "a bluetooth device just connected", bt > pbt)
        edge("bt_gone", "a bluetooth device just disconnected", bt < pbt)
    usb = vals["usb"]
    pus = _prev.get("usb")
    if usb is not None and pus is not None:
        edge("usb_plugged", "something was just plugged into a USB port",
             usb > pus)
    audio = vals["audio"]
    paudio = _prev.get("audio")
    if isinstance(audio, dict) and isinstance(paudio, dict):
        ph, pa = paudio.get("headphones"), paudio.get("hdmi")
        h, a = audio.get("headphones"), audio.get("hdmi")
        if h is not None and ph is not None and h != ph:
            edge("headphones_on" if h else "headphones_off",
                 "the headphones just plugged in" if h
                 else "the headphones just unplugged", True)
        if a is not None and pa is not None and bool(a) != bool(pa):
            edge("hdmi_on" if a else "hdmi_off",
                 "a screen just plugged into the HDMI port" if a
                 else "the HDMI screen just unplugged", True)
    locked = vals["locked"]
    plocked = _prev.get("locked")
    if locked is not None and plocked is not None:
        edge("screen_locked", "the screen just locked", locked and not plocked)
    shots = vals["shots"]
    pshots = _prev.get("shots")
    if shots is not None and pshots is not None and shots > pshots:
        edge("screenshot_taken", "they just took a screenshot", True)

    _prev.update({k: v for k, v in vals.items() if v is not None
                  or k in ("lid", "wifi", "locked")})
    return out


def reset_for_test() -> None:
    _prev.clear()
    _state.update(wall=time.time(), mono=time.monotonic())
