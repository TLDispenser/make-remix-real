# home.py — the sandbox as a home (plan 2.2): the readable files that live
# next to the journal db. She has no file tools (#3 in the plan): the PROGRAM
# writes what she says. Everything that lands in the repo is redacted first
# (emails, URLs, IPs, paths) — the raw truth stays in the local, gitignored
# db; what the GitHub mirror shows is the readable, scrubbed copy.
#   diary/YYYY-MM-DD.md    her own-voice entry for a day — git log reads it
#   journal/YYYY-MM-DD.md  the day's raw journal rows (readable mirror)
#   reflections.md         every reflection, newest first
#   inbox/                 you drop .txt files for her (watcher is opt-in)
#   outbox/                notes she leaves you (the leave_note form)
import re
import time
from pathlib import Path

from . import config

_subject: str | None = None        # first sentence of the newest diary entry


def _base() -> Path:
    return Path(str(config.SANDBOX_DIR))


def ensure_dirs() -> None:
    for d in ("diary", "journal", "inbox", "outbox"):
        (_base() / d).mkdir(parents=True, exist_ok=True)


def _write(path: Path, text: str) -> None:
    """Best-effort atomic write; a file hiccup must never crash the brain."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        print(f"remix-brain: home file write failed ({path.name}: {e!r})")


def _first_sentence(text: str, cap: int = 72) -> str:
    s = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0]
    return s[:cap].strip()


def redact(text: str) -> str:
    """THE gate before text can be committed: emails, URLs, IPs and file
    paths never leave the machine in her readable files."""
    from . import awareness
    return awareness.redact_text(text)


def save_day_entry(day: str, text: str) -> str | None:
    """diary/<day>.md — her entry for a day, in her own voice. Returns the
    first sentence, which becomes the mirror's commit message (plan 2.2 #4:
    her day reads in git log)."""
    text = (text or "").strip()
    if not text:
        return None
    _write(_base() / "diary" / f"{day}.md", f"# {day}\n\n{redact(text)}\n")
    global _subject
    _subject = _first_sentence(text)
    return _subject


def commit_subject() -> str | None:
    return _subject


def clear_subject() -> None:
    global _subject
    _subject = None


def write_journal_days(rows: list) -> None:
    """The readable mirror: group journal rows by local day and rewrite each
    day's file. Rows newer than the store's cap stay readable in the db;
    older days keep the files already on disk."""
    by_day: dict = {}
    for r in rows:
        day = str(r.get("at") or "")[:10]
        if day:
            by_day.setdefault(day, []).append(r)
    for day, day_rows in sorted(by_day.items()):
        parts = [f"# {day}", ""]
        for r in reversed(day_rows):            # oldest first
            stamp = str(r.get("at") or "")[11:16] or "??"
            kind = str(r.get("kind") or "event")
            text = redact(str(r.get("text") or "").strip())
            body = "\n    ".join(text.split("\n")) if "\n" in text else text
            parts.append(f"- `{stamp}` **{kind}** — {body}")
        _write(_base() / "journal" / f"{day}.md", "\n".join(parts) + "\n")


def write_reflections(rows: list) -> None:
    """reflections.md — every reflection, newest first."""
    refl = [r for r in rows if r.get("kind") == "reflection"]
    if not refl:
        return
    parts = ["# Reflections", ""]
    for r in refl:                              # rows arrive newest-first
        parts += [f"## {r.get('at') or '????-??-?? ??:??'}", "",
                  redact(str(r.get("text") or "").strip()), ""]
    _write(_base() / "reflections.md", "\n".join(parts))


def save_outbox(text: str, urgent: bool = False) -> Path | None:
    """outbox/<timestamp>.txt — a note she leaves for you (plan 2.2 #6).
    The `leave_note` form: you are away or she is out of sight, so the line
    is written down instead of said to an empty room."""
    text = (text or "").strip()
    if not text:
        return None
    name = (time.strftime("%Y%m%d-%H%M%S")
            + ("-urgent" if urgent else "") + ".txt")
    path = _base() / "outbox" / name
    _write(path, redact(text) + "\n")
    return path


def poll_inbox(excerpt_chars: int) -> list:
    """New .txt files in inbox/ -> [{'name', 'excerpt'}], each MOVED to
    inbox/seen/ so a file is reacted to exactly once. The excerpt is
    redacted and capped — it goes to the cloud LLM, so it is only ever for
    things you are happy to send. No folder = no watcher yet."""
    inbox = _base() / "inbox"
    if not inbox.is_dir():
        return []
    seen = inbox / "seen"
    out = []
    for p in sorted(inbox.glob("*.txt")):
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"remix-brain: inbox read failed ({p.name}: {e!r})")
            continue
        out.append({"name": p.name,
                    "excerpt": redact(raw.strip())[:max(80, excerpt_chars)]})
        try:
            seen.mkdir(parents=True, exist_ok=True)
            p.replace(seen / (time.strftime("%Y%m%d-%H%M%S-") + p.name))
        except OSError as e:
            print(f"remix-brain: inbox move failed ({p.name}: {e!r})")
    return out
