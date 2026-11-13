# vitals.py — 1 Hz vitals stream. Phase 1: MOCK feed drives everything.
# Phase 2 flips REMIX_MOCK=0 to use real psutil data (same snapshot shape).
# Phase 6 (plan Part 4 "Senses"): real senses — the busiest processes, swap,
# network and disk I/O rates, plus slow-changing body truths (battery wear,
# drive health, kernel errors). Everything here is local: psutil, /sys,
# smartctl -H (never sudo), journalctl. Snapshot grows the same dict shape,
# so speech/grounding learn to read the new keys — nothing else changes.
import asyncio
import glob
import os
import random
import re
import shutil
import subprocess
import time

from . import config

_MOOD_GUESS = "idle"  # replaced by left brain in Phase 3

TOP_N = 3   # how many busy processes she senses (name + cpu share only)

# --- delta state (module level: rates are differences BETWEEN snapshots) ----
_procs: dict = {}               # pid -> psutil.Process (cpu% needs a 2nd read)
_net_last: tuple | None = None  # (monotonic, bytes total)
_disk_last: tuple | None = None # (monotonic, bytes total)
_body_cache: tuple | None = None  # (monotonic, body dict or None)


async def _mock_snapshot() -> dict:
    """Random-walk synthetic vitals, bounded to sane ranges. The Phase-6
    keys are present here too, same shape as the real feed, so demos and
    tests exercise the full grounding path."""
    return {
        "cpu": round(random.uniform(5, 95), 1),
        "ram": round(random.uniform(35, 75), 1),
        "temp": round(random.uniform(45, 78), 1),  # °C-equivalent scale only
        "battery": round(random.uniform(10, 100), 1),
        "disk": 33.0,
        "swap": round(random.uniform(0, 8), 1),
        "net": round(random.uniform(0, 300), 1),       # KB/s
        "diskio": round(random.uniform(0, 2000), 1),   # KB/s
        "top": [{"name": "mock-proc", "cpu": round(random.uniform(1, 40), 1)}],
        "body": {"wear": 12.0, "cycles": 34, "smart": "ok", "errors": 2},
    }


def _top_snapshot(psutil) -> list:
    """Top-N busiest processes by CPU share, delta since the LAST snapshot.
    The Process objects are kept across snapshots (module state) so each
    cpu_percent(interval=None) read is a real delta over the previous one.
    Name + appetite only — nothing inside them."""
    out = []
    seen = set()
    for p in psutil.process_iter(["name"]):
        seen.add(p.pid)
        try:
            cpu = p.cpu_percent(interval=None)   # 0.0 on first sight (primes)
        except Exception:                        # NoSuchProcess/AccessDenied…
            continue
        out.append({"name": str(p.info.get("name") or "")[:40],
                    "cpu": round(cpu, 1)})
        _procs[p.pid] = p
    for pid in [k for k in _procs if k not in seen]:
        _procs.pop(pid, None)                    # prune dead pids
    out.sort(key=lambda x: x["cpu"], reverse=True)
    return out[:TOP_N]


def _rate_snapshot(psutil, kind: str) -> float | None:
    """KB/s since the previous snapshot for net ('net') or disk ('diskio').
    None on the first snapshot — no previous point to differ from."""
    global _net_last, _disk_last
    try:
        if kind == "net":
            io = psutil.net_io_counters()
            cur = io.bytes_sent + io.bytes_recv
            last = _net_last
        else:
            io = psutil.disk_io_counters()
            if io is None:
                return None
            cur = io.read_bytes + io.write_bytes
            last = _disk_last
    except OSError:
        return None
    now = time.monotonic()
    if last is None or now <= last[0]:
        if kind == "net":
            _net_last = (now, cur)
        else:
            _disk_last = (now, cur)
        return None
    rate = max(0.0, (cur - last[1]) / (now - last[0]) / 1024.0)
    if kind == "net":
        _net_last = (now, cur)
    else:
        _disk_last = (now, cur)
    return round(rate, 1)


def _read_sys_num(path: str) -> float | None:
    try:
        with open(path, encoding="utf-8") as f:
            return float(f.read().strip())
    except (OSError, ValueError):
        return None


def _root_device() -> str | None:
    """The disk '/' lives on, as smartctl wants it (/dev/sda, /dev/nvme0n1)."""
    try:
        import psutil
        for p in psutil.disk_partitions(all=False):
            if p.mountpoint == "/" and p.device:
                base = os.path.basename(p.device)
                if re.search(r"p\d+$", base):        # nvme0n1p2, mmcblk0p3
                    base = re.sub(r"p\d+$", "", base)
                elif re.search(r"\d+$", base):       # sda1, vdb2 -> whole disk
                    base = re.sub(r"\d+$", "", base)
                return "/dev/" + base
    except Exception:
        return None
    return None


def _body_snapshot() -> dict | None:
    """Slow-changing body truths: battery wear, drive SMART health, kernel
    errors this boot. Cached ~10 min (config.BODY_REFRESH_S) so the 1 Hz
    stream never re-runs the probes; never needs sudo — smartctl without
    privileges just reports nothing and she says nothing about it."""
    global _body_cache
    now = time.monotonic()
    if _body_cache is not None and now - _body_cache[0] < config.BODY_REFRESH_S:
        return _body_cache[1]
    body: dict = {}
    # Battery wear: what /sys says the battery HOLDS vs what it was BORN
    # holding. psutil exposes neither design capacity nor cycle count.
    for base in sorted(glob.glob("/sys/class/power_supply/BAT*")):
        full = design = None
        for stem in ("energy_full", "charge_full"):
            v = _read_sys_num(os.path.join(base, stem))
            if v:
                full = v
                break
        for stem in ("energy_full_design", "charge_full_design"):
            v = _read_sys_num(os.path.join(base, stem))
            if v:
                design = v
                break
        if full and design and full > 0 and design > 0:
            body["wear"] = round(max(0.0, (1.0 - full / design) * 100.0), 1)
            cc = _read_sys_num(os.path.join(base, "cycle_count"))
            if cc:
                body["cycles"] = int(cc)
            break
    # Drive health: SMART overall-status only (smartctl -H). No sudo — if
    # it can't run, the key simply never appears.
    sm = shutil.which("smartctl")
    dev = _root_device()
    if sm and dev:
        try:
            r = subprocess.run([sm, "-H", dev], capture_output=True,
                               text=True, timeout=20)
            text = (r.stdout or "") + (r.stderr or "")
            if "PASSED" in text or "OK:" in text:
                body["smart"] = "ok"
            elif "FAILED" in text:
                body["smart"] = "FAILED"
        except (OSError, subprocess.TimeoutExpired):
            pass
    # Kernel errors since this boot: a COUNT — the errors themselves never
    # reach her (each line is a timestamp + binary + complaint).
    try:
        r = subprocess.run(
            ["journalctl", "-p", "err", "-b", "--no-pager", "-q"],
            capture_output=True, text=True, timeout=20)
        if r.returncode == 0:
            body["errors"] = sum(
                1 for ln in (r.stdout or "").splitlines() if ln.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    body = body or None
    _body_cache = (now, body)
    return body


async def _real_snapshot() -> dict:
    import psutil  # pinned 7.x: see RESEARCH_NOTES.md (8.0 will break APIs)

    temp = None
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            chips = temps.get("coretemp") or next(iter(temps.values()), None)
            if chips:
                temp = round(chips[0].current, 1)
    except (AttributeError, IndexError, OSError):
        temp = None
    battery = psutil.sensors_battery()  # None on desktops
    try:
        swap = round(psutil.swap_memory().percent, 1)
    except Exception:
        swap = None
    # The body probes can block (smartctl/journalctl) — off the event loop.
    body = await asyncio.to_thread(_body_snapshot)
    return {
        "cpu": round(psutil.cpu_percent(interval=None), 1),
        "ram": round(psutil.virtual_memory().percent, 1),
        "temp": temp,
        "battery": round(battery.percent, 1) if battery else None,
        "disk": round(psutil.disk_usage("/").percent, 1),
        "swap": swap,
        "net": _rate_snapshot(psutil, "net"),
        "diskio": _rate_snapshot(psutil, "diskio"),
        "top": _top_snapshot(psutil),
        "body": body,
    }


async def stream():
    """Yields a vitals snapshot every VITALS_INTERVAL_S: the five core keys,
    plus the Phase-6 senses (swap, net, diskio, top, body)."""
    source = _mock_snapshot if config.MOCK else _real_snapshot
    if not config.MOCK:  # prime cpu_percent(interval=None): first read = 0
        import psutil
        psutil.cpu_percent(interval=None)
    yield await source()
    while True:
        await asyncio.sleep(config.VITALS_INTERVAL_S)
        yield await source()
