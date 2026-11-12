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
#    "faces": [...], "commitments": [...]}
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

from . import config

_store: dict | None = None
_dirty = False
_last_flush = 0.0
_awaken: dict | None = None          # set once at boot
_active = False                      # booted with load(): journaling is live.
_remember_enabled = True             # /remember toggle: False pauses recording
_db: sqlite3.Connection | None = None
# (Offline tests never call load(), so all writes no-op there and the real
# journal stays clean.)
_FLUSH_EVERY_S = 30.0


# ------------------------------------------------------------------ store IO

def _now() -> float:
    return time.time()


def path() -> str:
    return str(config.MEMORY_JSON)


_BLANK = {"v": 1, "last_shutdown": None, "lines": [], "said": [], "faces": [],
          "commitments": []}


def _db_conn() -> sqlite3.Connection | None:
    """SQLite FTS5 database connection for fast retrieval and indexing."""
    global _db
    if _db is not None:
        return _db
    try:
        p = Path(config.MEMORY_DB)
        p.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(p), check_same_thread=False)
        con.execute("PRAGMA journal_mode=WAL;")
        con.executescript("""
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            at TEXT NOT NULL,
            kind TEXT NOT NULL,
            text TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
            text,
            content='entries',
            content_rowid='id'
        );
        CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
            INSERT INTO entries_fts(rowid, text) VALUES (new.id, new.text);
        END;
        CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
            INSERT INTO entries_fts(entries_fts, rowid, text) VALUES('delete', old.id, old.text);
        END;
        CREATE TABLE IF NOT EXISTS commitments (
            id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            when_tag TEXT NOT NULL,
            created_at REAL NOT NULL,
            due_at REAL NOT NULL,
            done INTEGER DEFAULT 0
        );
        """)
        _db = con
        return _db
    except Exception as e:
        print(f"remix-brain: memory db init failed ({e!r})")
        return None


def _backfill_fts_if_needed() -> None:
    """If the SQLite index is empty but lines exist in store, backfill."""
    con = _db_conn()
    if con is None or _store is None:
        return
    try:
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM entries;")
        count = cur.fetchone()[0]
        if count == 0 and _store.get("lines"):
            print(f"remix-brain: backfilling {len(_store['lines'])} memories into FTS5 index")
            for entry in reversed(_store["lines"]):
                cur.execute(
                    "INSERT INTO entries (ts, at, kind, text) VALUES (?, ?, ?, ?)",
                    (float(entry.get("t", _now())), str(entry.get("at", "")),
                     str(entry.get("kind", "event")), str(entry.get("text", ""))),
                )
            con.commit()
    except Exception as e:
        print(f"remix-brain: memory backfill failed ({e!r})")


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
    _db_conn()
    _backfill_fts_if_needed()
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
                "commitments": [x for x in (data.get("commitments") or []) if isinstance(x, dict)],
            }
    except (OSError, ValueError):
        _store = None


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

def set_remember(enabled: bool) -> None:
    """Per-session toggle: /remember off pauses saving conversation lines."""
    global _remember_enabled
    _remember_enabled = bool(enabled)
    print(f"remix-brain: memory recording {'enabled' if _remember_enabled else 'paused'}")


def is_remember_enabled() -> bool:
    return _remember_enabled


def record(kind: str, text: str, when: float | None = None) -> None:
    """One dated journal row. kind: 'user' | 'her' | 'thought' | 'event' |
    'reflection' | 'summary'.
    No-op until load() has run (tests never journal; the brain does)."""
    if not _active or not _remember_enabled:
        return
    text = (text or "").strip()
    if not text:
        return
    ts = round(_now() if when is None else when)
    at = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    entry = {"t": ts, "at": at, "kind": kind, "text": text[:400]}
    s = store()
    s["lines"] = ([entry] + s["lines"])[:config.MEMORY_MAX_ENTRIES]
    _mark()

    # Also persist to SQLite FTS5 table
    con = _db_conn()
    if con is not None:
        try:
            con.execute(
                "INSERT INTO entries (ts, at, kind, text) VALUES (?, ?, ?, ?)",
                (float(ts), at, kind, text[:400]),
            )
            con.commit()
        except Exception as e:
            print(f"remix-brain: memory db insert failed ({e!r})")


def lines(kind: str | None = None, limit: int = 20, spread: int = 0) -> list:
    """Newest-first journal rows. `limit` takes the most recent; `spread`
    additionally samples rows from the OLDER remainder — evenly spaced,
    always including the journal's very first row — so 'the morning' stays
    reachable even when the recent lines have been chatty. No duplicates."""
    rows = [x for x in store()["lines"] if kind is None or x.get("kind") == kind]
    out = rows[:limit]
    if spread and len(rows) > limit:
        rest = rows[limit:]
        n = len(rest)
        picks = {int(k * (n - 1) / spread) for k in range(spread)} | {n - 1}
        for i in sorted(picks):
            if rest[i] not in out:
                out.append(rest[i])
    return out


# ------------------------------------------------------------- FTS5 retrieval

_STOPWORDS = {
    "a", "about", "after", "all", "also", "an", "and", "any", "are", "as",
    "at", "be", "because", "been", "but", "by", "can", "could", "did", "do",
    "does", "for", "from", "get", "got", "had", "has", "have", "he", "her",
    "him", "his", "how", "i", "if", "in", "into", "is", "it", "its", "just",
    "like", "me", "my", "no", "not", "now", "of", "on", "one", "only", "or",
    "our", "out", "say", "said", "she", "so", "some", "that", "the", "their",
    "them", "then", "there", "these", "they", "this", "to", "up", "us", "was",
    "we", "were", "what", "when", "where", "which", "who", "will", "with",
    "would", "you", "your", "tell", "remember",
}


def search_fts(query: str, limit: int = 5) -> list:
    """Search journal entries using SQLite FTS5 BM25 ranking."""
    con = _db_conn()
    if con is None:
        # Fallback to in-memory search if DB is unavailable
        return _search_fallback(query, limit)
    tokens = [t.lower() for t in re.findall(r"[a-zA-Z0-9]+", query)
              if len(t) > 2 and t.lower() not in _STOPWORDS]
    if not tokens:
        return []
    fts_expr = " OR ".join(f'"{t}"' for t in tokens)
    try:
        cur = con.cursor()
        cur.execute(
            """
            SELECT e.ts, e.at, e.kind, e.text, bm25(entries_fts) AS rank
            FROM entries_fts f
            JOIN entries e ON e.id = f.rowid
            WHERE entries_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_expr, limit),
        )
        return [{"t": row[0], "at": row[1], "kind": row[2], "text": row[3],
                 "score": row[4]} for row in cur.fetchall()]
    except Exception as e:
        print(f"remix-brain: fts search error ({e!r})")
        return _search_fallback(query, limit)


def _search_fallback(query: str, limit: int = 5) -> list:
    """Simple in-memory substring/token match fallback."""
    tokens = {t.lower() for t in re.findall(r"[a-zA-Z0-9]+", query)
              if len(t) > 2 and t.lower() not in _STOPWORDS}
    if not tokens:
        return []
    matches = []
    for entry in store().get("lines", []):
        text = str(entry.get("text", "")).lower()
        score = sum(1 for t in tokens if t in text)
        if score > 0:
            matches.append({**entry, "score": -float(score)})
    matches.sort(key=lambda x: x.get("score", 0))
    return matches[:limit]


def _temporal_window(text: str) -> tuple[float, float] | None:
    """Extract an epoch timestamp window (start, end) from temporal phrases."""
    t = text.lower()
    now = _now()
    lt = time.localtime(now)
    midnight_today = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))

    if "yesterday" in t:
        return (midnight_today - 86400, midnight_today)
    if "this morning" in t:
        return (midnight_today + 5 * 3600, midnight_today + 12 * 3600)
    if "last night" in t:
        return (midnight_today - 6 * 3600, midnight_today + 6 * 3600)
    if "earlier today" in t or "earlier" in t:
        return (midnight_today, now)

    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for day_idx, day_name in enumerate(weekdays):
        if re.search(r"\b" + day_name + r"\b", t):
            current_wday = lt.tm_wday
            days_ago = (current_wday - day_idx) % 7
            if days_ago == 0:
                days_ago = 7
            target_midnight = midnight_today - days_ago * 86400
            return (target_midnight, target_midnight + 86400)

    m = re.search(r"\b(\d+|one|two|three|four|five|six|seven)\s+days?\s+ago\b", t)
    if m:
        num_map = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
        n = num_map.get(m.group(1)) or int(m.group(1))
        target_midnight = midnight_today - n * 86400
        return (target_midnight, target_midnight + 86400)

    return None


def search_temporal(start_ts: float, end_ts: float, limit: int = 5) -> list:
    """Retrieve memories falling within the given timestamp window."""
    con = _db_conn()
    if con is not None:
        try:
            cur = con.cursor()
            cur.execute(
                """
                SELECT ts, at, kind, text FROM entries
                WHERE ts >= ? AND ts <= ?
                ORDER BY ts DESC
                LIMIT ?
                """,
                (start_ts, end_ts, limit),
            )
            return [{"t": row[0], "at": row[1], "kind": row[2], "text": row[3]}
                    for row in cur.fetchall()]
        except Exception as e:
            print(f"remix-brain: temporal search db error ({e!r})")
    # Fallback to store
    matches = [e for e in store().get("lines", [])
               if start_ts <= float(e.get("t", 0)) <= end_ts]
    return matches[:limit]


def retrieve(user_text: str, limit: int = 5) -> list:
    """Retrieve top relevant memories matching either content (FTS5) or
    temporal hints (e.g. 'yesterday', 'Tuesday'). Deduplicated."""
    results = []
    seen = set()

    # 1. Temporal match if user asked about a specific time
    temp_win = _temporal_window(user_text)
    if temp_win:
        for row in search_temporal(temp_win[0], temp_win[1], limit=limit):
            key = (row.get("at"), row.get("text"))
            if key not in seen:
                seen.add(key)
                results.append(row)

    # 2. Content match via FTS5
    for row in search_fts(user_text, limit=limit):
        key = (row.get("at"), row.get("text"))
        if key not in seen:
            seen.add(key)
            results.append(row)
        if len(results) >= limit:
            break

    return results[:limit]


def forget(topic: str) -> int:
    """Remove matching rows from store, SQLite database, and said set.
    Returns the number of rows forgotten."""
    topic = (topic or "").strip().lower()
    if not topic:
        return 0
    s = store()
    before = len(s.get("lines", []))
    s["lines"] = [e for e in s.get("lines", [])
                  if topic not in str(e.get("text", "")).lower()]
    forgotten = before - len(s["lines"])

    # Remove from said set
    s["said"] = [t for t in s.get("said", []) if topic not in t.lower()]

    # Remove from SQLite database
    con = _db_conn()
    if con is not None:
        try:
            cur = con.cursor()
            cur.execute("DELETE FROM entries WHERE LOWER(text) LIKE ?",
                        (f"%{topic}%",))
            con.commit()
        except Exception as e:
            print(f"remix-brain: memory forget db error ({e!r})")

    _mark()
    flush()
    if forgotten > 0 and sync_enabled():
        _git("commit", "-q", "-m", f"forget {topic[:50]}")
    return forgotten


# ------------------------------------------------------------- commitments

def add_commitment(text: str, when_tag: str, due_time: float | None = None) -> str:
    """Record a commitment/reminder. due_time defaults based on when_tag."""
    # time_ns: two commitments made in the same millisecond must not share
    # an id (SQLite PRIMARY KEY would silently REPLACE the first one).
    cid = f"c{time.time_ns()}"
    now = _now()
    if due_time is None:
        if when_tag == "later_today":
            due_time = now + 10800.0   # +3 hours
        elif when_tag == "tomorrow":
            lt = time.localtime(now)
            midnight_today = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
            due_time = midnight_today + 86400.0 + 10 * 3600.0  # tomorrow 10am
        else:
            due_time = now + 86400.0   # default +24h

    item = {
        "id": cid,
        "text": text[:300],
        "when": when_tag,
        "created_at": now,
        "due_at": due_time,
        "done": False,
    }
    s = store()
    s.setdefault("commitments", []).append(item)
    _mark()

    con = _db_conn()
    if con is not None:
        try:
            con.execute(
                """
                INSERT OR REPLACE INTO commitments (id, text, when_tag, created_at, due_at, done)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (cid, text[:300], when_tag, float(now), float(due_time)),
            )
            con.commit()
        except Exception as e:
            print(f"remix-brain: commitment db insert failed ({e!r})")

    record("commitment", f"they said: {text} ({when_tag})")
    print(f"remix-brain: commitment recorded ({cid}): '{text[:40]}' due at {time.strftime('%H:%M', time.localtime(due_time))}")
    return cid


def due_commitments(now: float | None = None) -> list:
    """Return active commitments that have passed their due time."""
    now = _now() if now is None else now
    due = []
    con = _db_conn()
    if con is not None:
        try:
            cur = con.cursor()
            cur.execute(
                "SELECT id, text, when_tag, created_at, due_at FROM commitments WHERE done = 0 AND due_at <= ?",
                (now,),
            )
            for row in cur.fetchall():
                due.append({"id": row[0], "text": row[1], "when": row[2],
                            "created_at": row[3], "due_at": row[4], "done": False})
            return due
        except Exception as e:
            print(f"remix-brain: due commitments db query failed ({e!r})")
    # In-memory fallback
    for c in store().get("commitments", []):
        if not c.get("done") and float(c.get("due_at", 0)) <= now:
            due.append(dict(c))
    return due


def mark_commitment_done(cid: str) -> None:
    """Mark a commitment as handled so it does not trigger again."""
    con = _db_conn()
    if con is not None:
        try:
            con.execute("UPDATE commitments SET done = 1 WHERE id = ?", (cid,))
            con.commit()
        except Exception as e:
            print(f"remix-brain: commitment done db update failed ({e!r})")
    s = store()
    for c in s.get("commitments", []):
        if c.get("id") == cid:
            c["done"] = True
            _mark()
            break


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


def _git(*args: str, cwd: str | None = None) -> tuple:
    # cwd is resolved at CALL time. (An earlier version froze it into the
    # default argument at import time — with a test/alt sandbox that made
    # every git call run in the import-time sandbox, which lives inside the
    # PROJECT work tree; with real creds that would have committed and pushed
    # the whole project repo to her sandbox remote. Never again.)
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"        # never hang waiting for a password
    r = subprocess.run(["git", *args], cwd=cwd or str(config.SANDBOX_DIR),
                       capture_output=True, text=True, timeout=60, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def sync_once() -> str | None:
    """Commit + push the sandbox. Non-fatal; returns a short status string."""
    if not sync_enabled():
        return None
    flush()
    d = Path(str(config.SANDBOX_DIR)).resolve()
    # The sandbox must be its OWN repo. It lives INSIDE the project work
    # tree, so "inside a work tree" is always true here — the question is
    # whether the toplevel is the sandbox itself.
    rc, top = _git("rev-parse", "--show-toplevel")
    if not (rc == 0 and Path(top.strip()).resolve() == d):
        rc, out = _git("init", "-b", "main")
        if rc != 0:
            return f"init failed: {out.strip()[:120]}"
        _git("config", "user.name", "remix")
        _git("config", "user.email", "remix@sandbox.local")
    _git("add", "-A")
    if _git("diff", "--cached", "--quiet")[0] == 0:
        return "clean (nothing new to commit; pushes on the next journal change)"
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
    # Git echoes the authed URL in network-failure messages; never let the
    # token reach the return value (it would land in the log).
    out = out.replace(config.MEMORY_TOKEN, "***")
    if rc != 0:
        return f"push failed: {out.strip()[:120]}"
    return "pushed"
