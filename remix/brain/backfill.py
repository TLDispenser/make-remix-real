# backfill.py — boot-time journal backfill, diary voice migration, and
# graceful-shutdown diary authoring.
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Callable

from . import awareness, config, home, memory, speech

# Timestamp cutoff for the one-shot diary voice migration. Pages authored
# before this instant get re-written under the settled diary standard.
DIARY_MIGRATE_BEFORE = "2026-11-15 11:00"


def spread_rows(rows: list, cap: int) -> list:
    """Even sample of a day's rows (input oldest-first): first and last
    always in, the middle evenly spaced."""
    if len(rows) <= cap:
        return rows
    step = (len(rows) - 1) / (cap - 1)
    return [rows[round(k * step)] for k in range(cap)]


def diary_migrate_marker() -> Path:
    return Path(str(config.SANDBOX_DIR)) / "diary" / ".voice-migrated"


async def migrate_diary_voice(
    before: str = DIARY_MIGRATE_BEFORE,
    get_vitals: Callable[[], dict] | None = None,
) -> None:
    """Re-author pre-cutoff diary pages oldest-first, chained via previous
    entry context. Resumable if interrupted or if the provider pool fails."""
    if diary_migrate_marker().exists():
        return
    rows = memory.lines(limit=2000)
    by_day: dict = {}
    for r in rows:
        at = str(r.get("at") or "")
        if at and at < before:
            by_day.setdefault(at[:10], []).append(r)
    base = Path(str(config.SANDBOX_DIR)) / "diary"
    reauthored = {
        r.get("about") for r in rows
        if r.get("kind") == "summary" and r.get("about")
        and str(r.get("at") or "") >= before
    }
    days = sorted(
        d for d in by_day
        if d not in reauthored and (base / f"{d}.md").exists()
    )
    snap = get_vitals() if get_vitals else None
    for day in days:
        try:
            text = await asyncio.wait_for(
                speech.generate_day_summary(
                    spread_rows(list(reversed(by_day[day])), 40),
                    vitals=snap,
                    aware=awareness.current(),
                    previous=home.last_diary(day),
                ),
                timeout=30.0,
            )
        except Exception:
            text = None
        if not text:
            print(f"remix-brain: diary re-author skipped for {day} (pool down?)")
            return
        memory.record("summary", text, about=day)
        home.save_day_entry(day, text)
        print(f"remix-brain: diary page re-authored for {day}")
    try:
        diary_migrate_marker().write_text(
            f"diary voice migration done {time.strftime('%Y-%m-%d')}\n"
        )
    except OSError:
        pass


async def run_backfill(
    before: str = DIARY_MIGRATE_BEFORE,
    get_vitals: Callable[[], dict] | None = None,
) -> None:
    """Boot backfill: write missing day summaries (max 2/boot), catch up any
    missed reflection, then run the one-shot diary migration."""
    await asyncio.sleep(20.0)
    if not memory.has_journal():
        return
    try:
        rows = memory.lines(limit=400)
        if not rows:
            return
        have = {
            r.get("about") for r in rows
            if r.get("kind") == "summary" and r.get("about")
        }
        today = time.strftime("%Y-%m-%d")
        for r in rows:
            if r.get("kind") != "summary" or not r.get("about"):
                continue
            day_file = Path(str(config.SANDBOX_DIR)) / "diary" / f"{r['about']}.md"
            if not day_file.exists():
                home.save_day_entry(r["about"], str(r.get("text") or ""))
        by_day: dict = {}
        for r in rows:
            day = str(r.get("at") or "")[:10]
            if day and day < today:
                by_day.setdefault(day, []).append(r)
        done = 0
        snap = get_vitals() if get_vitals else None
        for day in sorted(by_day):
            if done >= 2:
                break
            if day in have:
                continue
            text = await speech.generate_day_summary(
                list(reversed(by_day[day])),
                vitals=snap,
                aware=awareness.current(),
                previous=home.last_diary(day),
            )
            if text:
                memory.record("summary", text, about=day)
                home.save_day_entry(day, text)
                done += 1
                print(f"remix-brain: day summary written for {day}")
        last_refl = next(
            (i for i, r in enumerate(rows) if r.get("kind") == "reflection"),
            None,
        )
        newer = rows[:last_refl] if last_refl is not None else rows
        if newer:
            text = await speech.generate_reflection(
                vitals=snap, aware=awareness.current()
            )
            if text:
                memory.record("reflection", text)
                home.write_reflections(memory.lines(limit=400))
                print("remix-brain: missed reflection written at boot")
        await migrate_diary_voice(before=before, get_vitals=get_vitals)
    except Exception as e:
        print(f"remix-brain: memory backfill failed ({e!r})")


async def write_diary_entry(vitals: dict | None = None) -> None:
    """Today's diary entry at graceful shutdown when new rows arrived."""
    if not memory.has_journal():
        return
    today = time.strftime("%Y-%m-%d")
    rows = memory.lines(limit=400)
    last = next(
        (i for i, r in enumerate(rows)
         if r.get("kind") == "summary" and r.get("about") == today),
        None,
    )
    newer_today = [
        r for r in (rows[:last] if last is not None else rows)
        if str(r.get("at") or "").startswith(today)
    ]
    if not newer_today:
        return
    text = await asyncio.wait_for(
        speech.generate_day_summary(
            list(reversed(newer_today)),
            vitals=vitals,
            aware=awareness.current(),
            previous=home.last_diary(today),
        ),
        timeout=30.0,
    )
    if text:
        memory.record("summary", text, about=today)
        home.save_day_entry(today, text)
        print("remix-brain: diary entry written")
