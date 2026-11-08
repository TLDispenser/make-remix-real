# memory.py — the fix for "she doesn't know she was off": a persistent
# journal in her sandbox, plus an optional GitHub mirror (user request 3: a
# repo she can keep whatever she wants in, so space is never a worry).
#
# What lives here:
#   memory.json  — the journal: dated lines (what was said, her thoughts),
#                  last_shutdown (so an awaken knows how long she was off),
#                  the said-set (chatter dedup survives a restart) and the
#                  face-variety memory.
#   <anything>   — the sandbox is her repo; later wiring can let her write
#                  files there too. Sync pushes the whole folder.
#
# Ground rules kept: audio NEVER touches disk anyway (that's the mic rule, and
# no audio exists to store); the journal is text. The GitHub mirror is opt-in
# via REMIX_MEMORY_REPO (+ REMIX_MEMORY_TOKEN) and the journal contains chat
# text — the user was told to use a PRIVATE repo.
#
# Store shape (v1, tolerant of missing fields):
#   {"v": 1, "last_shutdown": <unix>, "lines": [...], "said": [...],
#    "faces": [...]}
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import config

_store: dict | None = None
_dirty = False
_last_flush = 0.0
_awaken: dict | None = None          # set once at boot
_active = False                      # booted with load(): journaling is live.
# (Offline tests never call load(), so all writes no-op there and the real
# journal stays clean.)
_FLUSH_EVERY_S = 30.0


# ------------------------------------------------------------------ store IO

def _now() -> float:
    return time.time()


def path() -> str:
    return str(config.MEMORY_JSON)


def load() -> dict:
    """Read memory.json once at boot; anything unreadable starts fresh."""
    global _store, _dirty, _last_flush, _awaken, _active
    _read()
    if _store is None:                 # unreadable/missing -> empty journal
        _store = dict(_BLANK)
    _dirty = False
    _last_flush = _now()
    _awaken = _compute_awaken()
    _active = True
    return dict(_store)


def _read() -> None:
    """Fill _store from disk WITHOUT equipping journaling (store()/read paths
    in tests use this; only the brain's real boot runs load())."""
    global _store, _awaken
    _store = None
    _awaken = None
    try:
        with open(path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _store = {
                "v": 1,
                "last_shutdown": _num(data.get("last_shutdown")),
                "lines": [x for x in (data.get("lines") or []) if isinstance(x, dict)],
                "said": [str(x) for x in (data.get("said") or [])],
                "faces": [str(x) for x in (data.get("faces") or [])],
            }
    except (OSError, ValueError):
        _store = None


_BLANK = {"v": 1, "last_shutdown": None, "lines": [], "said": [], "faces": []}


def _num(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def store() -> dict:
    """The live store (lazy read for tool/test callers; never a full load,
    so journaling stays off outside a real boot)."""
    if _store is None:
        _read()
    return _store if _store is not None else dict(_BLANK)


def _save() -> None:
    """Best-effort atomic write; the journal failing must never crash her."""
    try:
        d = Path(config.SANDBOX_DIR)
        d.mkdir(parents=True, exist_ok=True)
        tmp = str(path()) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_store or dict(_BLANK), f, ensure_ascii=False, indent=1)
        os.replace(tmp, path())
    except OSError as e:
        print(f"remix-brain: memory save failed ({e!r})")


def flush() -> None:
    """Write if dirty (called on a timer and at exit)."""
    global _dirty, _last_flush
    if _dirty:
        _save()
        _dirty = False
        _last_flush = _now()


def maybe_flush(now: float | None = None) -> None:
    now = _now() if now is None else now
    if _dirty and now - _last_flush >= _FLUSH_EVERY_S:
        flush()


def shutdown() -> None:
    """Record clean shutdown time. main's finally calls this."""
    global _store, _dirty
    s = store()
    s["last_shutdown"] = _now()
    _store = s
    _dirty = True
    flush()
    print("remix-brain: memory: shutdown recorded")


# ------------------------------------------------------------- awaken (4)

def _compute_awaken() -> dict | None:
    """How long was she off before THIS session? None = no known past."""
    prev = (store() or {}).get("last_shutdown")
    prev = _num(prev)
    if not prev:
        return None
    elapsed = _now() - prev
    if elapsed <= 0 or elapsed < config.AWAKEN_MIN_S:
        return None
    return {"elapsed_s": elapsed, "since": prev}


def awaken() -> dict | None:
    "The boot-time 'she was off' fact (or None on first boot / short gap)."
    prev = _num((store() or {}).get("last_shutdown"))
    if not prev:
        return None
    elapsed = _now() - prev
    if elapsed <= 0 or elapsed < config.AWAKEN_MIN_S:
        return None
    return {"elapsed_s": elapsed, "since": prev}


def elapsed_text(elapsed_s: float | None) -> str | None:
    if not elapsed_s or elapsed_s <= 0:
        return None
    mins = round(elapsed_s / 60)
    if mins < 60:
        return f"{max(1, mins)} minutes"
    hours = round(elapsed_s / 3600)
    if hours < 48:
        return "about an hour" if hours == 1 else f"about {hours} hours"
    return f"about {round(elapsed_s / 86400)} days"


# ---------------------------------------------------------------- journal (3)

def record(kind: str, text: str, when: float | None = None) -> None:
    """One dated journal row. kind: 'user' | 'her' | 'thought' | 'event'.
    No-op until load() has run (tests never journal; the brain does)."""
    if not _active:
        return
    text = (text or "").strip()
    if not text:
        return
    s = store()
    entry = {"t": round(_now() if when is None else when),
             "at": time.strftime("%Y-%m-%d %H:%M", time.localtime(
                 _now() if when is None else when)),
             "kind": kind, "text": text[:400]}
    s["lines"] = ([entry] + s["lines"])[:config.MEMORY_MAX_ENTRIES]
    _mark()


def lines(kind: str | None = None, limit: int = 20) -> list:
    out = [x for x in store()["lines"] if kind is None or x.get("kind") == kind]
    return out[:limit]


# ----------------------------------------------------- dedup + variety memory

def note_said(text: str) -> None:
    """Remember a line she has already said (chatter dedup across restarts).
    Normalization is byte-for-byte the same rule as speech._norm_line, so the
    persisted set matches the in-memory one. No-op until load()."""
    if not _active:
        return
    import re
    n = " ".join(re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower()).split())
    if not n:
        return
    s = store()
    said = s["said"]
    if n not in said:
        said.insert(0, n)
    del said[400:]
    _mark()


def said_set() -> set:
    return set(store()["said"])


def has_journal() -> bool:
    """True once she has any dated memory at all (and journaling is live)."""
    return _active and bool(store()["lines"])


def note_face(face: str) -> None:
    """Variety memory write-through so faces keep changing across restarts.
    No-op until load()."""
    if not _active:
        return
    s = store()
    if face and (not s["faces"] or s["faces"][0] != face):
        s["faces"].insert(0, face)
    del s["faces"][8:]
    _mark()


def recent_faces() -> list:
    return list(store()["faces"])


def _mark() -> None:
    global _dirty
    _dirty = True


# ----------------------------------------------------------- GitHub mirror (3)

def sync_enabled() -> bool:
    return bool(config.MEMORY_REPO and config.MEMORY_TOKEN
                and shutil.which("git"))


def _git(*args: str, cwd: str = path() and str(config.SANDBOX_DIR)) -> tuple:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"        # never hang waiting for a password
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                       text=True, timeout=60, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def sync_once() -> str | None:
    """Commit + push the sandbox. Non-fatal; returns a short status string."""
    if not sync_enabled():
        return None
    flush()
    d = str(config.SANDBOX_DIR)
    rc, out = _git("rev-parse", "--is-inside-work-tree")
    if rc != 0:
        rc, out = _git("init", "-b", "main")
        if rc != 0:
            return f"init failed: {out.strip()[:120]}"
        _git("config", "user.name", "remix")
        _git("config", "user.email", "remix@sandbox.local")
    _git("add", "-A")
    if _git("diff", "--cached", "--quiet")[0] == 0:
        rc, out = _git("remote", "get-url", "origin")
        return "clean" if rc == 0 else f"init ok ({out.strip()[:80]})"
    _git("commit", "-q", "-m",
         "journal " + time.strftime("%Y-%m-%d %H:%M"))
    # Token goes in the push URL only — never stored in .git/config.
    url = config.MEMORY_REPO
    head = url.rsplit("/", 1)
    authed = f"https://remix-token:{config.MEMORY_TOKEN}@" + (
        head[0].split("://", 1)[-1] + "/" + head[1]).removeprefix("/")
    rc, out = _git("push", "--quiet", authed, "HEAD:main")
    _git("remote", "remove", "origin") if _git(
        "remote", "get-url", "origin")[0] == 0 else None
    if rc != 0:
        return f"push failed: {out.strip()[:120]}"
    return "pushed"
