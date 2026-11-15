# mood.py tests (2026-11-15): her mood is STATE — earned nudges, drift
# weather, decay — picking the temper band. Hermeticity contract: mood
# persists ONLY where the journal is live (memory._active), so a test that
# doesn't boot a journal gets neutral, no disk, no contact with her files.
import json
import os
import tempfile
import unittest
from pathlib import Path

from brain import config, memory, mood, speech  # noqa: E402


def _boot_journal():
    """Mimic MemoryBase: a temp sandbox with the journal live."""
    d = tempfile.mkdtemp()
    saved = (config.SANDBOX_DIR, config.MEMORY_JSON, config.MEMORY_DB)
    config.SANDBOX_DIR = d
    config.MEMORY_JSON = os.path.join(d, "memory.json")
    config.MEMORY_DB = os.path.join(d, "memory.db")
    memory._store = None
    memory._active = False
    memory._db = None
    memory.load()
    mood.reset_for_test()
    return d, saved


def _shut(saved):
    (config.SANDBOX_DIR, config.MEMORY_JSON, config.MEMORY_DB) = saved
    memory._store = None
    memory._active = False
    memory._dirty = False
    memory._db = None
    mood.reset_for_test()


class GateTests(unittest.TestCase):
    """Without a live journal: neutral mood, zero writes, no file contact."""

    def setUp(self):
        mood.reset_for_test()
        self.assertFalse(memory._active)
        self._d = tempfile.mkdtemp()
        self._saved = config.SANDBOX_DIR
        config.SANDBOX_DIR = self._d

    def tearDown(self):
        config.SANDBOX_DIR = self._saved
        mood.reset_for_test()

    def test_no_journal_no_persistence(self):
        mood.nudge(+0.5, "thanks")
        self.assertFalse((Path(self._d) / "mood.json").exists())

    def test_value_still_works_without_a_journal(self):
        mood.nudge(+0.5, "thanks")
        self.assertGreater(mood.value(), 0.0)


class NudgeTests(unittest.TestCase):

    def setUp(self):
        self._tmp, self._saved = _boot_journal()

    def tearDown(self):
        _shut(self._saved)

    def test_nudge_moves_and_persists(self):
        mood.nudge(+0.5, "thanks")
        self.assertGreaterEqual(mood.value(), 0.49)
        raw = json.loads((Path(self._tmp) / "mood.json").read_text())
        self.assertGreater(raw["mood"], 0.0)

    def test_same_kind_rate_limits(self):
        for _ in range(5):
            mood.nudge(+0.5, "thanks")
        self.assertAlmostEqual(mood.value(), 0.5, delta=1e-6)

    def test_decay_toward_neutral(self):
        mood.nudge(+2.0, "event")
        fake = {"mood": 2.0, "weather": 0.0, "at": 0.0}   # written long ago
        (Path(self._tmp) / "mood.json").write_text(json.dumps(fake))
        mood.reset_for_test()          # force a reload from disk
        self.assertAlmostEqual(mood.value(), 0.0, delta=0.1)


class BandTests(unittest.TestCase):

    def setUp(self):
        self._tmp, self._saved = _boot_journal()

    def tearDown(self):
        _shut(self._saved)

    def test_neutral_is_cranky(self):
        self.assertEqual(mood.band(), "cranky")

    def test_warm_weather_lifts_the_band(self):
        mood.weather(+1.0)
        self.assertEqual(mood.band(), "balanced")
        mood.nudge(+1.5, "event")      # clears the 'soft' threshold for sure
        self.assertEqual(mood.band(), "soft")

    def test_foul_weather_stays_cranky(self):
        mood.weather(-1.0)
        mood.nudge(+0.5, "thanks")
        self.assertEqual(mood.band(), "cranky")

    def test_describe_earned_only(self):
        mood.weather(-1.0)
        self.assertEqual(mood.describe(), "")   # weather speaks via the drift
        mood.nudge(+1.0, "event")
        self.assertIn("softer", mood.describe())


class HearTests(unittest.TestCase):

    def setUp(self):
        self._tmp, self._saved = _boot_journal()

    def tearDown(self):
        _shut(self._saved)

    def test_concern_and_thanks_nudge(self):
        mood.hear("how are you doing over there")
        mood.hear("thanks remix")
        self.assertGreater(mood.value(), 0.3)

    def test_plain_text_is_neutral(self):
        mood.hear("run the tests and print the diff")
        self.assertAlmostEqual(mood.value(), 0.0, delta=1e-9)

    def test_vitals_heat_drags(self):
        mood.note_vitals({"temp": 58.0})
        self.assertLess(mood.value(), 0.0)


class TemperLineMoodTests(unittest.TestCase):
    """MOODS on: neutral renders EXACTLY the old static line; earned mood
    swaps the drift line for a RIGHT NOW line; the band follows the scalar."""

    def setUp(self):
        self._tmp, self._saved = _boot_journal()
        self._moods = config.MOODS
        config.MOODS = True
        speech._drift = None   # re-roll deterministically per test

    def tearDown(self):
        config.MOODS = self._moods
        _shut(self._saved)

    def test_neutral_matches_the_static_shape(self):
        line = speech.temper_line()
        self.assertTrue(line.startswith("TODAY'S DRIFT") or line == "")

    def test_stable_until_reroll(self):
        a = speech.temper_line()
        b = speech.temper_line()
        self.assertEqual(a, b)

    def test_earned_mood_replaces_the_drift_line(self):
        mood.nudge(+1.0, "event")
        line = speech.temper_line()
        self.assertIn("RIGHT NOW", line)
        self.assertIn("softer", line)

    def test_earned_mood_picks_a_warmer_base(self):
        mood.nudge(+1.5, "event")      # band 'balanced', not yet 'soft'
        line = speech.temper_line()
        self.assertIn("more cooperative", line)   # the balanced base text

    def test_foul_mood_line(self):
        mood.nudge(-2.5, "event")
        line = speech.temper_line()
        self.assertIn("RIGHT NOW", line)
        self.assertIn("foul", line)


class TemperLineLegacyTests(unittest.TestCase):
    """MOODS off: the old static behavior, pinned so REMIX_MOODS=0 is exact."""

    def setUp(self):
        mood.reset_for_test()
        self._moods = config.MOODS
        self._temper = config.TEMPER
        config.MOODS = False
        config.TEMPER = "cranky"
        speech._drift = None

    def tearDown(self):
        config.MOODS = self._moods
        config.TEMPER = self._temper

    def test_drift_line_shape(self):
        line = speech.temper_line()
        self.assertTrue(line.startswith("TODAY'S DRIFT") or line == "")

    def test_mood_never_leaks_in(self):
        mood.nudge(+2.5, "event")
        line = speech.temper_line()
        self.assertNotIn("RIGHT NOW", line)

    def test_soft_temper_still_reachable_by_config(self):
        config.TEMPER = "soft"
        line = speech.temper_line()
        self.assertIn("noticeably kinder", line)   # the soft base text
