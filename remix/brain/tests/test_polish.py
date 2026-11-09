# test_polish.py — the Nov-8 criticism round, offline:
#   * grounding: honest welcome-back (no invented 30 min), awaken line,
#     session-uptime line, PAST gate opened ONLY by a real journal
#   * proactive: awaken trigger, ambient thoughts, no-fake-away-time
#   * jev: variety seeded from the journal + write-through hook
#   * speech: temper drift line + persisted dedup merge
#   * tts: per-beat prosody (still espeak, fixed ranges)
#   * awareness: gaze_toward (from HER window; see test_surroundings.py)
import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from brain import awareness, config, grounding, jev, proactive, speech, tts, faces  # noqa: E402


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


VITALS = {"cpu": 10.0, "ram": 50.0, "temp": 45.0, "battery": 80.0,
          "disk": 40.0}


class WelcomeBackHonestyTests(unittest.TestCase):
    """4: '30 minutes' was FABRICATED when no real sense existed."""

    def test_unknown_away_carries_no_number(self):
        s = proactive._situation("welcome_back", {}, None, away_mins=None)
        self.assertNotIn("30", s)
        self.assertIn("back", s)

    def test_known_away_still_quotes_it(self):
        s = proactive._situation("welcome_back", {}, None, away_mins=40)
        self.assertIn("40", s)


class GroundingTimeTests(unittest.TestCase):
    """4: she finally knows how long she's been awake."""

    def test_facts_mention_session_uptime(self):
        p = grounding.facts("cpu 10%, ram 50%", "x, a laptop", None,
                            {"up_s": 7200.0})
        self.assertIn("awake about 120 minutes", p)

    def test_facts_carry_awaken_fact_while_session_is_young(self):
        with mock.patch("brain.memory.awaken",
                        return_value={"elapsed_s": 7300.0, "since": 0.0}):
            p = grounding.facts("cpu 10%, ram 50%", "x", None,
                                {"up_s": 600.0})
        self.assertIn("woke up", p)
        self.assertIn("hours", p)

    def test_awaken_fact_fades_for_long_sessions(self):
        with mock.patch("brain.memory.awaken",
                        return_value={"elapsed_s": 7300.0, "since": 0.0}):
            p = grounding.facts("cpu 10%, ram 50%", "x", None,
                                {"up_s": 7200.0})
        self.assertNotIn("woke up", p)


class PastGateTests(unittest.TestCase):
    """4: memories exist -> past-talk allowed ONLY as far as they reach."""

    def assert_flags(self, reply, user="", hist=""):
        return bool(grounding.check(reply, user, hist, VITALS))

    def test_without_journal_yesterday_is_still_invention(self):
        with mock.patch("brain.memory.has_journal", return_value=False):
            self.assertTrue(self.assert_flags("you left that on yesterday"))

    def test_journal_reaches_today_says_earlier_today_passes(self):
        with mock.patch("brain.memory.has_journal", return_value=True), \
             mock.patch("brain.memory.lines", return_value=[
                 {"t": grounding.time.time() - 3600,
                  "at": grounding.time.strftime("%Y-%m-%d %H:%M"),
                  "kind": "her", "text": "battery low this morning"}]):
            self.assertFalse(self.assert_flags(
                "battery again. like earlier today."))

    def test_journal_does_not_reach_a_week_ago(self):
        with mock.patch("brain.memory.has_journal", return_value=True), \
             mock.patch("brain.memory.lines", return_value=[
                 {"t": grounding.time.time() - 2 * 86400,
                  "at": "", "kind": "her", "text": "cpu spike"}]):
            self.assertTrue(self.assert_flags(
                "cpu spiked like a week ago too"))


class AwakenTriggerTests(unittest.TestCase):
    def test_real_gap_turns_greeting_into_awaken(self):
        p = proactive.Proactive(rng=__import__("random").Random(0),
                                awaken_s=5000.0)
        plan = p.tick({}, None, now=100.0)
        self.assertEqual("awaken", plan["trigger"])

    def test_first_boot_keeps_greeting(self):
        p = proactive.Proactive(rng=__import__("random").Random(0),
                                awaken_s=None)
        plan = p.tick({}, None, now=100.0)
        self.assertEqual("greeting", plan["trigger"])

    def test_situation_quotes_real_off_time(self):
        p = proactive.Proactive(rng=__import__("random").Random(0),
                                awaken_s=5000.0)
        plan = p.tick({}, None, now=100.0)
        self.assertIn("OFF", plan["situation"])


class AmbientTests(unittest.TestCase):
    def test_ambient_fires_between_events_after_cooldown(self):
        rng = __import__("random").Random(0)
        p = proactive.Proactive(rng=rng)
        p.greeted = True
        p._last_ambient = -1e12
        # force a hit: rng.random() below the AMBIENT_RATE seed needed —
        # instead shrink the rate gate by mocking it high
        with mock.patch.object(proactive.config, "AMBIENT_RATE", 1.0):
            plan = None
            for i in range(5):
                plan = p.tick({"app": "kitty", "up_s": 100.0}, None,
                              now=1000.0 + i)
                if plan:
                    break
        self.assertEqual("ambient", plan["trigger"])

    def test_ambient_respects_its_own_cooldown(self):
        p = proactive.Proactive(rng=__import__("random").Random(0))
        p.greeted = True
        p.last_line_at = -1e12
        p._last_ambient = 5.0
        self.assertFalse(p._ambient_ok(6.0))
        self.assertFalse(p._ambient_ok(5.0 + config.AMBIENT_COOLDOWN_S - 1))
        self.assertTrue(p._ambient_ok(5.0 + config.AMBIENT_COOLDOWN_S + 1))


class JevVarietyTests(unittest.TestCase):
    def test_seed_rebuilds_recent_newest_last(self):
        # seed list is newest-first (journal order)
        j = jev.JevBrain(seed_faces=["smirk/smirk", "anger/anger"])
        self.assertEqual(["anger/anger", "smirk/smirk"], j.recent_faces())

    def test_hook_fires_on_note(self):
        seen = []
        j = jev.JevBrain(variety_hook=seen.append)
        j.note_face(faces.Face("tired", "trauma"))
        self.assertIn("tired/trauma", seen)

    def test_no_seed_no_hook_no_change(self):
        j = jev.JevBrain()
        self.assertEqual([], j.recent_faces())


class TemperTests(unittest.TestCase):
    def setUp(self):
        speech._drift = None   # re-roll deterministically per test

    def test_drift_line_is_short_and_named(self):
        line = speech.temper_line()
        self.assertTrue(line.startswith("TODAY'S DRIFT") or line == "")

    def test_drift_is_stable_until_reroll(self):
        a = speech.temper_line()
        b = speech.temper_line()
        self.assertEqual(a, b)


class DedupMergeTests(unittest.TestCase):
    def test_chatter_dup_consults_the_journal_set(self):
        with mock.patch("brain.memory.said_set",
                        return_value={"you are still weird"}):
            self.assertTrue(speech._chatter_dup("YOU ARE STILL WEIRD"))
        self.assertFalse(speech._chatter_dup("brand new line entirely"))


class ProsodyTests(unittest.TestCase):
    def test_alarm_runs_faster_than_a_plain_beat(self):
        ds, _ = tts._prosody("WAIT. that is on fire.")
        plain, _ = tts._prosody("what is a mouse")
        self.assertGreater(ds, plain)

    def test_ranges_stay_sane(self):
        ds, dp = tts._prosody("NO. FIRE. NOW. NOW. NOW.")
        self.assertGreaterEqual(ds, -25)
        self.assertLessEqual(ds, 30)
        self.assertLessEqual(abs(dp), 8)


class GazeTowardTests(unittest.TestCase):
    """gaze_toward measures from HER window (she floats; she is not the screen)."""
    def setUp(self):
        awareness.reset_for_test()

    def tearDown(self):
        awareness.reset_for_test()

    def _seed(self, **win):
        w = {"app": "firefox", "dir": "to my right", "near": "close",
             "covered": 0.0, "size": "mid-sized", "focused": True,
             "look": [0.85, 0.0]}
        w.update(win)
        awareness.seed_for_test(around={"self": {"x": 0, "y": 0, "w": 480,
                                                 "h": 270}, "windows": [w],
                                        "elsewhere": []})

    def test_focused_window_to_the_right_aims_right(self):
        self._seed()
        look = awareness.gaze_toward()
        self.assertEqual(2, len(look))
        self.assertGreater(look[0], 0.5)

    def test_no_direction_returns_none(self):
        self._seed(look=None)
        self.assertIsNone(awareness.gaze_toward())

    def test_no_surroundings_returns_none(self):
        self.assertIsNone(awareness.gaze_toward())


class ProactiveUrgentFlagTests(unittest.TestCase):
    def test_muted_long_and_idle_deep_flag_urgent(self):
        self.assertIn("muted_long", proactive.URGENT_ON)
        self.assertIn("idle_deep", proactive.URGENT_ON)
        self.assertNotIn("poke", proactive.URGENT_ON)


class WindowPlacementTests(unittest.TestCase):
    """Nov-9 (redo of the patch lost in a4273da): '480x270, about 6% of the
    1920x1080 screen' read like a fixed address when she actually gets dragged
    around. The position Godot now reports becomes plain placement words,
    second screens get named, and when there is no trustworthy position she
    says so instead of guessing."""

    CTX = {"window": [480, 270], "screen": [1920, 1080], "area_fraction": 0.06}

    def test_position_becomes_plain_words(self):
        s = grounding.describe_screen({**self.CTX, "pos": [1340, 100],
                                       "screen_index": 1, "screen_count": 2})
        self.assertIn("480x270", s)
        self.assertIn("top-right", s)
        self.assertIn("screen 2 of 2", s)
        self.assertIn("right now", s)     # current spot, not a fixed address

    def test_middle_park_reads_as_middle(self):
        s = grounding.describe_screen({**self.CTX, "pos": [720, 405]})
        self.assertIn("near the middle", s)
        self.assertNotIn("screen 1 of", s)   # one screen: no numbering noise

    def test_no_position_claims_no_position(self):
        s = grounding.describe_screen(dict(self.CTX))
        self.assertIn("480x270", s)
        self.assertNotIn("top-", s)
        self.assertIn("isn't something you're told", s)

    def test_junk_position_is_not_a_place(self):
        s = grounding.describe_screen({**self.CTX, "pos": ["x", None]})
        self.assertNotIn("top-", s)

    def test_kill_switch_drops_position(self):
        with mock.patch.object(config, "WINDOW_POS", False):
            s = grounding.describe_screen({**self.CTX, "pos": [1340, 100]})
        self.assertNotIn("top-right", s)

    def test_her_own_window_fraction_is_not_a_vital_claim(self):
        # quoting the prompt's own "about 6% of it" line must not read as a
        # made-up cpu/ram/disk number (a real bug in the old prompt's shape)
        self.assertFalse(grounding.check(
            "I'm about 6% of the screen, squished in my window.", "", "", VITALS))
        self.assertTrue(grounding.check(
            "you're at 6% battery, by the way.", "", "", VITALS))


class PersonaRebalanceTests(unittest.TestCase):
    """Nov-9 round: cooperate by default (stubbornness is seasoning, not the
    meal), a complaint is said once and then retired, the glyph face is owned
    as a face, and the voice-direction marks the expressive TTS reads are
    taught explicitly (her old HOW YOU TALK mis-described them)."""

    def setUp(self):
        self.p = speech.build_system_prompt(VITALS, None, None)

    def test_help_is_the_product(self):
        self.assertIn("grumbling is the tax, help is the product", self.p)
        self.assertIn("Flat refusal as a bit", self.p)
        self.assertNotIn("I'm not doing this", speech.SYS_PROMPT)

    def test_a_complaint_is_said_once(self):
        self.assertIn("waved it off", self.p)
        self.assertIn("Nagging is the one thing", self.p)

    def test_the_face_is_a_face(self):
        self.assertIn("and a face, yes", self.p)
        self.assertNotIn("and that's how you talk",
                         self.p.split("YOUR FACE")[0])

    def test_voice_marks_are_taught(self):
        self.assertIn("VOICE DIRECTION", self.p)
        self.assertIn("*sigh*", self.p)
        self.assertIn("(parentheses)", self.p)
        self.assertIn("Never write the word \"sigh\" out", self.p)

    def test_stale_voice_claims_are_gone(self):
        # the expressive TTS made the old claims lies: "..." is a PAUSE now,
        # and a parenthesized line is the whisper aside, not a banned mark
        self.assertNotIn("is a sigh", speech.SYS_PROMPT)
        self.assertNotIn("lists, parentheses", speech.SYS_PROMPT)

    def test_chatter_wont_re_raise_a_waved_off_complaint(self):
        self.assertIn("fresh", speech.CHATTER_ADDENDUM)
        self.assertIn("waved off", speech.CHATTER_ADDENDUM)
