# vitals.py — 1 Hz vitals stream. Phase 1: MOCK feed drives everything.
# Phase 2 flips CORE_MOCK=0 to use real psutil data (same snapshot shape).
import asyncio
import random

from . import config

_MOOD_GUESS = "idle"  # replaced by left brain in Phase 3


async def _mock_snapshot() -> dict:
    """Random-walk synthetic vitals, bounded to sane ranges."""
    return {
        "cpu": round(random.uniform(5, 95), 1),
        "ram": round(random.uniform(35, 75), 1),
        "temp": round(random.uniform(45, 78), 1),  # °C-equivalent scale only
        "battery": round(random.uniform(10, 100), 1),
        "disk": 33.0,
    }


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
    return {
        "cpu": round(psutil.cpu_percent(interval=None), 1),
        "ram": round(psutil.virtual_memory().percent, 1),
        "temp": temp,
        "battery": round(battery.percent, 1) if battery else None,
        "disk": round(psutil.disk_usage("/").percent, 1),
    }


async def stream():
    """Yields {'cpu','ram','temp','battery','disk'} every VITALS_INTERVAL_S."""
    source = _mock_snapshot if config.CORE_MOCK else _real_snapshot
    if not config.CORE_MOCK:  # prime cpu_percent(interval=None): first read = 0
        import psutil
        psutil.cpu_percent(interval=None)
    yield await source()
    while True:
        await asyncio.sleep(config.VITALS_INTERVAL_S)
        yield await source()
