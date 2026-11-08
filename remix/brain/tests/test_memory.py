# test_memory.py — Phase 7 polish: the journal, awaken, dedup/variety
# persistence, GitHub-sync enablement. All offline; the sandbox points at a
# temp dir so the real journal is never touched.
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from brain import config, grounding, memory, proactive, speech  # noqa: E402


class MemoryBase(unittest.TestCase):
    """Journaling IS active for these tests: a temp sandbox replaces the real
    one, and load() runs like a real boot would."""

    def setUp(self):
        d = tempfile.mkdtemp()
        self._saved = (config.SANDBOX_DIR, config.MEMORY_JSON,
                       config.AWAKEN_MIN_S)
        config.SANDBOX_DIR = d
        config.MEMORY_JSON = os.path.join(d, "memory.json")
        memory._store = None
        memory._active = False
        memory.load()   # journal on (against the temp dir)

    def tearDown(self):
        (config.SANDBOX_DIR, config.MEMORY_JSON, config.AWAKEN_MIN_S) = (
            self._saved)
        memory._store = None
        memory._active = False
        memory._dirty = False


class JournalTests(MemoryBase):
    def test_record_is_dated_and_capped(self):
        for i in range(config.MEMORY_MAX_ENTRIES + 10):
            memory.record("thought", f"line {i}")
        rows = memory.lines()
        self.assertLessEqual(len(rows), config.MEMORY_MAX_ENTRIES)
        self.assertEqual("line %d" % i, rows[0]["text"])   # newest first
        self.assertIn("at", rows[0])                       # dated

    def test_said_set_survives_a_reload(self):
        memory.note_said("get to the point ALREADY!!")
        memory.flush()
        memory._store = None
        memory._active = False
        memory.load()
        said = memory.said_set()
        self.assertIn("get to the point already", said)

    def test_shutdown_sets_last_shutdown_and_reload_sees_it(self):
        memory.shutdown()
        with open(config.MEMORY_JSON, encoding="utf-8") as f:
            data = json.load(f)
        self.assertTrue(data["last_shutdown"])
        memory._store = None
        memory._active = False
        memory.load()
        self.assertAlmostEqual(memory._store["last_shutdown"],
                               data["last_shutdown"], places=3)

    def test_corrupt_file_starts_fresh(self):
        memory.record("thought", "survive me")
        memory.flush()
        with open(config.MEMORY_JSON, "w", encoding="utf-8") as f:
            f.write("{not json at all")
        memory._store = None
        memory._active = False
        memory.load()
        self.assertEqual([], memory.lines())

    def test_variety_memory_roundtrip(self):
        memory.note_face("anger/anger")
        memory.note_face("smirk/smirk")
        self.assertEqual(["smirk/smirk", "anger/anger"],
                         memory.recent_faces())   # newest first


class AwakenTests(MemoryBase):
    def test_first_boot_has_no_awaken(self):
        self.assertIsNone(memory.awaken())

    def test_long_off_time_becomes_awaken(self):
        memory._store["last_shutdown"] = time.time() - 7300
        aw = memory.awaken()
        self.assertIsInstance(aw, dict)
        self.assertIn("hours", memory.elapsed_text(aw["elapsed_s"]))

    def test_short_gap_is_not_an_awaken(self):
        memory._store["last_shutdown"] = time.time() - 30
        self.assertIsNone(memory.awaken())

    def test_hours_text_buckets(self):
        self.assertIn("minutes", memory.elapsed_text(300))
        self.assertIn("hours", memory.elapsed_text(3 * 3600))
        self.assertIn("days", memory.elapsed_text(50 * 3600))


class UnbootedJournalIsInert(unittest.TestCase):
    """Without load() (offline test suites, stray imports) nothing writes."""

    def setUp(self):
        self._saved = (config.SANDBOX_DIR, config.MEMORY_JSON)
        d = tempfile.mkdtemp()
        config.SANDBOX_DIR = d
        config.MEMORY_JSON = os.path.join(d, "memory.json")
        memory._store = None
        memory._active = False

    def tearDown(self):
        (config.SANDBOX_DIR, config.MEMORY_JSON) = self._saved
        memory._store = None
        memory._active = False

    def test_record_and_faces_noop_without_load(self):
        memory.record("thought", "should not happen")
        memory.note_face("anger/anger")
        memory.note_said("silent")
        memory.flush()
        self.assertFalse(os.path.exists(config.MEMORY_JSON))
        self.assertFalse(memory.has_journal())


class SandboxSyncTests(MemoryBase):
    def test_disabled_without_repo_config(self):
        self.assertFalse(memory.sync_enabled())

    def test_sync_once_noop_when_disabled(self):
        self.assertIsNone(memory.sync_once())
