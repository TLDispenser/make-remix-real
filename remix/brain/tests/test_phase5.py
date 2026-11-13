# Phase 5 tests (plan 2.2): the sandbox as a home — readable diary files,
# the outbox (leave_note), the opt-in inbox watcher, and the push safety
# pass (redaction + refuse-public-repo). Hermetic: temp sandbox, no network.
import os
import tempfile
import unittest
from pathlib import Path

from brain import config, home, memory

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


if __name__ == "__main__":
    unittest.main()
