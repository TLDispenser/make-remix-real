# Phase 5 tests (plan 2.2): the sandbox as a home — readable diary files,
# the outbox (leave_note), the opt-in inbox watcher, and the push safety
# pass (redaction + refuse-public-repo). Hermetic: temp sandbox, no network.
import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from brain import config, home, main, memory, speech

VITALS = {"cpu": 40, "ram": 60, "temp": 53, "battery": 80, "disk": 96}


def fresh_sandbox(t):
    d = tempfile.mkdtemp(prefix="remix-home-")
    saved = (config.SANDBOX_DIR, config.MEMORY_JSON, config.MEMORY_DB)
    config.SANDBOX_DIR = Path(d)
    config.MEMORY_JSON = Path(d) / "memory.json"
    config.MEMORY_DB = Path(d) / "memory.db"
    return saved


class HomeTests(unittest.TestCase):
    def setUp(self):
        self._saved = fresh_sandbox(self)
        memory._store = None            # lazy read -> the temp sandbox

    def tearDown(self):
        (config.SANDBOX_DIR, config.MEMORY_JSON,
         config.MEMORY_DB) = self._saved
        memory._store = None
        home.clear_subject()

    def test_ensure_dirs_makes_her_home(self):
        home.ensure_dirs()
        base = Path(str(config.SANDBOX_DIR))
        for d in ("diary", "journal", "inbox", "outbox"):
            self.assertTrue((base / d).is_dir(), d)

    def test_day_entry_is_redacted_and_yields_a_subject(self):
        subject = home.save_day_entry(
            "2026-11-12",
            "Spent the day at mail me@evil.com and https://secret.example/x, "
            "as usual. Then some peace.")
        text = (Path(str(config.SANDBOX_DIR)) / "diary" / "2026-11-12.md"
                ).read_text()
        self.assertNotIn("me@evil.com", text)
        self.assertNotIn("https://", text)
        self.assertIn("as usual", text)
        self.assertEqual(subject, home.commit_subject())
        self.assertTrue(subject.startswith("Spent the day at"))
        self.assertLessEqual(len(subject), 72)

    def test_first_sentence_becomes_the_commit_subject(self):
        home.save_day_entry("2026-11-12", "I watched. They typed. The end.")
        self.assertEqual("I watched.", home.commit_subject())

    def test_journal_days_group_rows_and_redact(self):
        home.write_journal_days([
            {"at": "2026-11-11 23:00", "kind": "user", "text": "hi there"},
            {"at": "2026-11-11 23:01", "kind": "her", "text": "hi@evil.com says hi /etc/passwd"},
            {"at": "2026-11-12 00:30", "kind": "thought", "text": "new day"},
        ])
        base = Path(str(config.SANDBOX_DIR)) / "journal"
        d11 = (base / "2026-11-11.md").read_text()
        d12 = (base / "2026-11-12.md").read_text()
        self.assertIn("`23:00` **user** — hi there", d11)
        self.assertNotIn("hi@evil.com", d11)
        self.assertNotIn("/etc/passwd", d11)
        self.assertIn("new day", d12)

    def test_reflections_file_is_newest_first(self):
        home.write_reflections([
            {"at": "2026-11-12 00:34", "kind": "reflection", "text": "later one"},
            {"at": "2026-11-11 18:00", "kind": "reflection", "text": "earlier one"},
        ])
        text = (Path(str(config.SANDBOX_DIR)) / "reflections.md").read_text()
        self.assertLess(text.index("later one"), text.index("earlier one"))

    def test_outbox_saves_a_redacted_note(self):
        p = home.save_outbox("call me at 555-0100, mail me@evil.com", urgent=True)
        self.assertIsNotNone(p)
        self.assertTrue(p.name.endswith("-urgent.txt"))
        text = p.read_text()
        self.assertNotIn("me@evil.com", text)
        # no note -> nothing
        self.assertIsNone(home.save_outbox("   "))

    def test_inbox_poll_moves_and_caps_and_redacts(self):
        inbox = Path(str(config.SANDBOX_DIR)) / "inbox"
        inbox.mkdir(parents=True)
        (inbox / "todo.txt").write_text(
            "remember to check http://tracker.example/bug/1 — also me@evil.com "
            "knows about /home/you/secret\n" + "filler " * 200)
        notes = home.poll_inbox(120)
        self.assertEqual(1, len(notes))
        self.assertEqual("todo.txt", notes[0]["name"])
        exc = notes[0]["excerpt"]
        self.assertLessEqual(len(exc), 120)
        self.assertNotIn("me@evil.com", exc)
        self.assertNotIn("http://", exc)
        self.assertNotIn("/home/you", exc)
        self.assertEqual([], home.poll_inbox(120))       # moved -> once only
        self.assertTrue((inbox / "seen").is_dir())
        self.assertEqual(1, len(list((inbox / "seen").glob("*.txt"))))

    def test_inbox_poll_without_a_folder_is_empty(self):
        self.assertEqual([], home.poll_inbox(120))


class SyncSafetyTests(unittest.TestCase):
    def setUp(self):
        self._saved = fresh_sandbox(self)
        self._creds = (config.MEMORY_REPO, config.MEMORY_TOKEN)
        memory._store = None
        memory._repo_private = None
        memory.load()

    def tearDown(self):
        (config.MEMORY_REPO, config.MEMORY_TOKEN) = self._creds
        (config.SANDBOX_DIR, config.MEMORY_JSON,
         config.MEMORY_DB) = self._saved
        memory._store = None
        memory._active = False         # journal OFF before the live dir returns
        memory._dirty = False
        memory._db = None
        memory._repo_private = None
        home.clear_subject()

    def test_visibility_check_refuses_a_public_repo(self):
        import json as _json
        import urllib.request
        config.MEMORY_REPO = "https://github.com/me/sandbox.git"
        config.MEMORY_TOKEN = "tok"
        memory.record("user", "hello diary")      # something to commit

        # public answer, no network: patch the urlopen used by memory
        class R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return _json.dumps({"private": False}).encode()

        saved = memory.urllib.request.urlopen
        memory.urllib.request.urlopen = lambda req, timeout: R()
        try:
            self.assertEqual("public", memory._repo_visibility())
            self.assertEqual("refused: the mirror repo is PUBLIC — journal "
                             "stays local", memory.sync_once())
        finally:
            memory.urllib.request.urlopen = saved
        # and the commit still happened locally (only the PUSH was refused)
        rc, log = memory._git("log", "--oneline", "-1")
        self.assertEqual(0, rc)
        self.assertTrue(log.strip())

    def test_unknown_visibility_fails_closed(self):
        config.MEMORY_REPO = "https://github.com/me/sandbox.git"
        config.MEMORY_TOKEN = "tok"
        memory.record("user", "hello diary")
        memory._repo_private = None
        saved = memory.urllib.request.urlopen

        def boom(req, timeout):
            raise OSError("no network in tests")

        memory.urllib.request.urlopen = boom
        try:
            self.assertEqual("unknown", memory._repo_visibility())
            self.assertIn("not pushed", memory.sync_once())
        finally:
            memory.urllib.request.urlopen = saved

    def test_commit_message_prefers_her_first_sentence(self):
        home.save_day_entry("2026-11-12", "I watched. They typed.")
        self.assertEqual("I watched.", memory.commit_message())
        home.clear_subject()
        self.assertTrue(memory.commit_message().startswith("journal "))


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class DiaryMigrationTests(unittest.TestCase):
    """The one-shot diary voice migration (2026-11-15): old day-summary
    pages get re-authored in the diary voice, oldest first, each seeing the
    page before it, grounded in that day's rows. Resumable; runs once."""

    def setUp(self):
        self._saved = fresh_sandbox(self)
        memory._store = None
        memory._active = False
        memory._db = None
        memory._remember_enabled = True
        memory.load()
        self._cutoff = main.DIARY_MIGRATE_BEFORE
        main.DIARY_MIGRATE_BEFORE = "2026-11-04 00:00"   # clock-independent
        self._gen = speech.generate_day_summary
        self.calls = []

        async def fake(day_rows, vitals=None, aware=None, previous=None):
            day = str(day_rows[0].get("at"))[:10]
            self.calls.append((day, len(day_rows), previous))
            return f"diary page for {day}"

        speech.generate_day_summary = fake
        # three past days, each with rows and an old-voice page already on disk
        for day, n in (("2026-11-01", 5), ("2026-11-02", 7), ("2026-11-03", 3)):
            for i in range(n):
                memory.record("user", f"line {i} of {day}",
                              when=dt(day, i))
            home.save_day_entry(day, f"old summary voice for {day}")
        main._diary_migrate_marker().unlink(missing_ok=True)

    def tearDown(self):
        speech.generate_day_summary = self._gen
        main.DIARY_MIGRATE_BEFORE = self._cutoff
        memory._store = None
        memory._active = False
        memory._dirty = False
        memory._db = None
        memory._remember_enabled = True
        (config.SANDBOX_DIR, config.MEMORY_JSON, config.MEMORY_DB) = self._saved

    def test_rewrites_oldest_first_with_chaining_and_runs_once(self):
        _run(main._migrate_diary_voice())
        self.assertEqual(["2026-11-01", "2026-11-02", "2026-11-03"],
                         [c[0] for c in self.calls])
        # each page sees the page before it (the freshly rewritten one)
        self.assertIsNone(self.calls[0][2])
        self.assertIn("diary page for 2026-11-01", self.calls[1][2])
        self.assertIn("diary page for 2026-11-02", self.calls[2][2])
        # rows were spread, not dumped whole (7-row day arrives intact)
        self.assertEqual(7, self.calls[1][1])
        # files rewritten, summary rows recorded about each day
        self.assertIn("diary page for 2026-11-02",
                      (Path(str(config.SANDBOX_DIR)) / "diary" / "2026-11-02.md")
                      .read_text())
        days = {r.get("about") for r in memory.lines(limit=50)
                if r.get("kind") == "summary"}
        self.assertTrue({"2026-11-01", "2026-11-02", "2026-11-03"} <= days)
        # marker written: a second run is a no-op
        self.assertTrue(main._diary_migrate_marker().exists())
        self.calls.clear()
        _run(main._migrate_diary_voice())
        self.assertEqual([], self.calls)

    def test_pool_failure_resumes_without_redoing_finished_pages(self):
        real = speech.generate_day_summary
        tried = set()

        async def flaky(day_rows, vitals=None, aware=None, previous=None):
            day = str(day_rows[0].get("at"))[:10]
            if day == "2026-11-02" and day not in tried:
                tried.add(day)                    # pool down, once
                return None
            return await real(day_rows, vitals=vitals, aware=aware,
                              previous=previous)

        speech.generate_day_summary = flaky
        _run(main._migrate_diary_voice())
        self.assertEqual(1, len(self.calls))       # only 11-01 finished
        _run(main._migrate_diary_voice())          # resume: 11-01 not redone
        self.assertEqual(["2026-11-01", "2026-11-02", "2026-11-03"],
                         [c[0] for c in self.calls])


def dt(day, i):
    """A stable epoch inside `day`, spaced so rows order deterministically."""
    import calendar
    import datetime
    d = datetime.date.fromisoformat(day)
    return calendar.timegm((d.year, d.month, d.day, 10 + i % 12, 0, 0))


if __name__ == "__main__":
    unittest.main()
