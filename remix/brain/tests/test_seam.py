# Phase 2 (the seam) offline tests: decision modes, the decision log, the
# proactive seam (stance / should_speak / form), the grounding judge hook,
# and the cadence changes (jitter, plain event text, no canned lines).
# Run from remix/:
#   .venv/bin/python -m unittest discover -s brain/tests -t . -v
import asyncio
import os
import random
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("REMIX_TTS_DIR", tempfile.mkdtemp(prefix="remix-test-voice-"))

from brain import config, decisions, jev, main as brain_main, proactive, rules  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def _plan(trigger="ambient", stance="pout"):
    return {"trigger": trigger, "stance": stance,
            "face": proactive.STANCE_FACE[stance], "look": None,
            "urgent": False, "aim": None,
            "situation": "nothing happened; it is dead of night"}


class FakeJev:
    """Stands in for JevBrain.decide_proactive."""

    def __init__(self, result=None):
        self.result = result
        self.calls = []

    async def decide_proactive(self, trigger, situation, options):
        self.calls.append((trigger, situation, list(options)))
        return self.result


class SeamTests(unittest.TestCase):
    """main._decide_proactive: the seam between local and Jev decisions."""

    def setUp(self):
        self.saved = {k: getattr(config, k) for k in
                      ("JEV_DECIDE_STANCE", "JEV_DECIDE_SPEAK",
                       "JEV_DECIDE_FORM", "JEV_SPEAK_P")}
        self.saved_modes = dict(decisions._modes)
        decisions._modes.clear()
        self.saved_log = decisions._log
        decisions._log = None          # never touch the real file in tests
        self.saved_flag = config.DECISIONS_LOG
        config.DECISIONS_LOG = False   # and never CREATE a handler either
        self._jev_saved = brain_main._jev
        brain_main._jev = FakeJev()

    def tearDown(self):
        decisions._modes.clear()
        decisions._modes.update(self.saved_modes)
        (config.JEV_DECIDE_STANCE, config.JEV_DECIDE_SPEAK,
         config.JEV_DECIDE_FORM, config.JEV_SPEAK_P) = (
            self.saved["JEV_DECIDE_STANCE"], self.saved["JEV_DECIDE_SPEAK"],
            self.saved["JEV_DECIDE_FORM"], self.saved["JEV_SPEAK_P"])
        config.DECISIONS_LOG = self.saved_flag
        decisions._log = self.saved_log
        brain_main._jev = self._jev_saved

    def _modes(self, stance, speak, form):
        decisions.set_mode("proactive_stance", stance)
        decisions.set_mode("should_speak", speak)
        decisions.set_mode("form", form)

    def test_off_mode_is_todays_behavior_no_call(self):
        self._modes("off", "off", "off")
        brain_main._jev = FakeJev(result={"stance": "fond", "speak_p": 0.9,
                                          "form": "look",
                                          "confidence": 0.9,
                                          "speak_confidence": 0.9,
                                          "latency_s": 0.0})
        plan = run(brain_main._decide_proactive(_plan()))
        self.assertEqual("pout", plan["stance"])
        self.assertNotIn("form", plan)
        self.assertEqual([], brain_main._jev.calls)   # off = no Jev spend

    def test_shadow_logs_but_the_local_pick_ships(self):
        self._modes("shadow", "shadow", "shadow")
        brain_main._jev = FakeJev(result={"stance": "fond", "speak_p": 0.95,
                                          "form": "look", "confidence": 0.61,
                                          "speak_confidence": 0.7,
                                          "latency_s": 0.0})
        plan = run(brain_main._decide_proactive(_plan()))
        # the local dice roll still decides everything in shadow
        self.assertEqual("pout", plan["stance"])
        self.assertNotIn("form", plan)
        # ...and Jev's answers are beside them in the log
        trail = " ".join(decisions.recent(20))
        self.assertIn("proactive_stance [shadow] jev=fond", trail)
        self.assertIn("used=local", trail)
        self.assertIn("should_speak [shadow] jev=0.95", trail)
        self.assertIn("form [shadow] jev=look", trail)

    def test_live_stance_ships_and_remaps_the_face(self):
        self._modes("live", "off", "off")
        brain_main._jev = FakeJev(result={"stance": "fond", "speak_p": None,
                                          "form": None, "confidence": 0.7,
                                          "speak_confidence": None,
                                          "latency_s": 0.0})
        plan = run(brain_main._decide_proactive(_plan(trigger="greeting")))
        self.assertEqual("fond", plan["stance"])
        self.assertEqual(proactive.STANCE_FACE["fond"], plan["face"])
        self.assertIn("proactive_stance [live] jev=fond "
                      "(0.70) local=pout used=jev",
                      " ".join(decisions.recent(20)))

    def test_live_should_speak_below_threshold_stays_quiet(self):
        self._modes("off", "live", "off")
        brain_main._jev = FakeJev(result={"stance": None, "speak_p": 0.31,
                                          "form": None, "confidence": 0.0,
                                          "speak_confidence": 0.8,
                                          "latency_s": 0.0})
        self.assertIsNone(run(brain_main._decide_proactive(_plan())))
        self.assertIn("stayed quiet",
                      " ".join(decisions.recent(20)))

    def test_live_should_speak_above_threshold_speaks(self):
        self._modes("off", "live", "off")
        brain_main._jev = FakeJev(result={"stance": None, "speak_p": 0.81,
                                          "form": None, "confidence": 0.0,
                                          "speak_confidence": 0.7,
                                          "latency_s": 0.0})
        plan = run(brain_main._decide_proactive(_plan()))
        self.assertIsNotNone(plan)
        self.assertEqual("pout", plan["stance"])    # stance mode was off

    def test_live_form_is_carried_on_the_plan(self):
        for form, expect in (("look", "look"), ("quiet", "quiet"),
                             ("caption", "caption")):
            with self.subTest(form=form):
                decisions._modes.clear()
                self._modes("off", "off", "live")
                brain_main._jev = FakeJev(result={
                    "stance": None, "speak_p": None, "form": form,
                    "confidence": 0.0, "speak_confidence": 0.0,
                    "latency_s": 0.0})
                plan = run(brain_main._decide_proactive(_plan()))
                self.assertEqual(expect, plan["form"])

    def test_leave_note_degrades_to_caption_until_the_outbox(self):
        self._modes("off", "off", "live")
        brain_main._jev = FakeJev(result={
            "stance": None, "speak_p": None, "form": "leave_note",
            "confidence": 0.0, "speak_confidence": 0.0, "latency_s": 0.0})
        plan = run(brain_main._decide_proactive(_plan()))
        self.assertEqual("caption", plan["form"])

    def test_garbled_answers_fall_back_to_local(self):
        self._modes("live", "live", "live")
        brain_main._jev = FakeJev(result={"stance": "angry-bad",
                                          "speak_p": None, "form": "yell",
                                          "confidence": 0.0,
                                          "speak_confidence": 0.0,
                                          "latency_s": 0.0})
        plan = run(brain_main._decide_proactive(_plan()))
        self.assertEqual("pout", plan["stance"])
        self.assertNotIn("form", plan)

    def test_jev_unavailable_falls_back_and_logs_it(self):
        self._modes("shadow", "shadow", "shadow")
        brain_main._jev = None
        plan = run(brain_main._decide_proactive(_plan()))
        self.assertEqual("pout", plan["stance"])
        self.assertNotIn("form", plan)

    def test_stance_menu_stays_the_local_one(self):
        self._modes("shadow", "off", "off")
        brain_main._jev = FakeJev(result=None)
        run(brain_main._decide_proactive(_plan(trigger="battery_low")))
        trigger, situation, options = brain_main._jev.calls[0]
        self.assertEqual("battery_low", trigger)
        self.assertEqual(proactive.STANCE_CHOICES["battery_low"], options)


# ------------------------------------------------------------- decision log

class DecisionLogTests(unittest.TestCase):
    def setUp(self):
        self.saved_log = decisions._log
        self.saved_modes = dict(decisions._modes)
        self.saved_flag = config.DECISIONS_LOG
        decisions._modes.clear()
        decisions._log = None
        config.DECISIONS_LOG = False
        decisions._recent.clear()

    def tearDown(self):
        decisions._log = self.saved_log
        config.DECISIONS_LOG = self.saved_flag
        decisions._modes.clear()
        decisions._modes.update(self.saved_modes)
        decisions._recent.clear()

    def test_log_builds_a_plain_words_trail(self):
        decisions.log("should_speak", "shadow", jev=0.81, used="local",
                      local="speak", extra="[ambient]")
        (entry,) = decisions.recent()
        self.assertIn("should_speak [shadow] jev=0.81 local=speak "
                      "used=local [ambient]", entry)

    def test_recent_is_oldest_first_and_capped(self):
        for i in range(8):
            decisions.log("outcome", "-", jev=None, used="spoke",
                          local="speak", extra=f"#{i}")
        last3 = decisions.recent(3)
        self.assertEqual(3, len(last3))
        self.assertIn("#5", last3[0])
        self.assertIn("#7", last3[2])

    def test_mode_defaults_and_runtime_flip(self):
        self.assertEqual("shadow", decisions.mode("proactive_stance"))
        self.assertEqual("off", decisions.mode("no_such_kind"))
        self.assertTrue(decisions.set_mode("should_speak", "live"))
        self.assertEqual("live", decisions.mode("should_speak"))
        self.assertFalse(decisions.set_mode("should_speak", "wild"))
        self.assertFalse(decisions.set_mode("no_such_kind", "live"))
        # an invalid flip leaves the mode untouched
        self.assertEqual("live", decisions.mode("should_speak"))

    def test_summary_lists_every_kind(self):
        s = decisions.summary()
        for kind in decisions.KINDS:
            self.assertIn(kind, s)

    def test_no_jev_key_forces_off_everywhere(self):
        # rule 6: no key = today's behavior; the seam honours JEV_ENABLED
        with mock.patch.object(config, "JEV_ENABLED", False):
            brain_main._jev = FakeJev(result={"stance": "fond",
                                              "speak_p": 0.9,
                                              "form": "look"})
            for kind in decisions.KINDS:
                decisions.set_mode(kind, "live")
            plan = run(brain_main._decide_proactive(_plan()))
        self.assertEqual("pout", plan["stance"])
        self.assertNotIn("form", plan)


# ----------------------------------------------------- proactive cadence

class CadenceTests(unittest.TestCase):
    """The 'silent life is too automated' fixes: jitter, plain event text."""

    def setUp(self):
        self.saved = {k: getattr(config, k) for k in
                      ("PROACTIVE_JITTER", "PROACTIVE_MIN_GAP_S",
                       "AMBIENT_RATE", "AMBIENT_COOLDOWN_S")}
        config.PROACTIVE_MIN_GAP_S = 0.0

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(config, k, v)

    def test_idle_deep_situation_is_plain_facts(self):
        s = proactive._situation("idle_deep", {"idle_s": 2700.0}, None)
        self.assertIn("45 minutes", s)
        self.assertNotIn("they never said", s)      # no editorializing
        self.assertNotIn("ok", s)

    def test_cooldown_jitter_widens_and_narrows_the_window(self):
        config.PROACTIVE_JITTER = 0.25
        e = proactive.Proactive(rng=random.Random(1))
        e._last["app_closed"] = 0.0                 # base cooldown 1800 s
        # a stuck-at-minimum rng: due at 75% of base, never before
        e.rng = mock.Mock()
        e.rng.uniform = lambda a, b: a
        self.assertFalse(e._due("app_closed", 1800.0 * 0.74))
        self.assertTrue(e._due("app_closed", 1800.0 * 0.76))
        # a stuck-at-maximum rng: not due until 125% of base
        e.rng.uniform = lambda a, b: b
        self.assertFalse(e._due("app_closed", 1800.0 * 1.1))
        self.assertTrue(e._due("app_closed", 1800.0 * 1.26))

    def test_zero_jitter_is_the_exact_old_timing(self):
        config.PROACTIVE_JITTER = 0.0
        e = proactive.Proactive(rng=random.Random(1))
        e._last["app_closed"] = 0.0
        self.assertFalse(e._due("app_closed", 1799.0))
        self.assertTrue(e._due("app_closed", 1800.0))

    def test_small_cooldowns_are_not_jittered(self):
        config.PROACTIVE_JITTER = 0.5
        e = proactive.Proactive(rng=random.Random(1))
        e._last["greeting"] = 0.0                   # base 1.0 s
        self.assertTrue(e._due("greeting", 1.0))    # exact, no wobble

    def test_gap_jitter_stays_within_bounds(self):
        config.PROACTIVE_JITTER = 0.25
        config.PROACTIVE_MIN_GAP_S = 1200.0
        e = proactive.Proactive(rng=random.Random(2))
        for _ in range(40):
            g = e._next_gap()
            self.assertGreaterEqual(g, 1200.0 * 0.75)
            self.assertLessEqual(g, 1200.0 * 1.25)

    def test_ambient_defaults_got_sparser(self):
        self.assertLess(config.AMBIENT_RATE, 0.03)
        self.assertGreater(config.AMBIENT_COOLDOWN_S, 2400.0)


# --------------------------------------------------------- the jev side

class DecideProactiveTests(unittest.TestCase):
    def setUp(self):
        self.saved = {k: getattr(config, k) for k in
                      ("JEV_ENABLED", "JEV_MAX_PER_HOUR",
                       "JEV_PROACTIVE_MAX_PER_HOUR", "LISTEN_JEV_RESERVE",
                       "JEV_CACHE_TTL_S")}
        config.JEV_CACHE_TTL_S = 0.0
        config.JEV_ENABLED = True
        config.JEV_MAX_PER_HOUR = 60
        config.LISTEN_JEV_RESERVE = 20

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(config, k, v)

    def _brain(self, resp):
        b = jev.JevBrain(rng=random.Random(3))
        b._call = mock.AsyncMock(return_value=resp)
        return b

    def test_parses_a_good_answer(self):
        b = self._brain({"answers": {
            "stance": {"type": "choice", "choice": "tsun",
                       "confidence": 0.66, "probabilities": {"tsun": 0.66}},
            "should_speak": {"type": "noul", "noul": 0.44},
            "form": {"type": "choice", "choice": "look",
                     "confidence": 0.5, "probabilities": {"look": 0.5}}},
            "usage": {"input_tokens": 100, "output_tokens": 5}})
        d = run(b.decide_proactive("muted_long", "sound is muted",
                                   ["pout", "tsun", "flat"]))
        self.assertEqual("tsun", d["stance"])
        self.assertAlmostEqual(0.44, d["speak_p"])
        self.assertEqual("look", d["form"])
        self.assertAlmostEqual(0.66, d["confidence"])

    def test_garbled_names_become_none_not_crashes(self):
        b = self._brain({"answers": {
            "stance": {"type": "choice", "choice": "furious"},
            "should_speak": {"type": "noul", "noul": "junk"},
            "form": {"type": "choice", "choice": "shout"}},
            "usage": {"input_tokens": 100, "output_tokens": 5}})
        d = run(b.decide_proactive("ambient", "x", ["flat", "pout"]))
        self.assertIsNone(d["stance"])
        self.assertIsNone(d["speak_p"])
        self.assertIsNone(d["form"])

    def test_disabled_or_over_budget_is_none(self):
        config.JEV_ENABLED = False
        b = self._brain({"answers": {}, "usage": {}})
        self.assertIsNone(run(b.decide_proactive("ambient", "x", ["flat"])))
        config.JEV_ENABLED = True
        config.JEV_MAX_PER_HOUR = 60
        config.LISTEN_JEV_RESERVE = 20
        config.JEV_PROACTIVE_MAX_PER_HOUR = 2
        b2 = self._brain({"answers": {}, "usage": {}})
        now = {"n": 0.0}

        def fake_mono():
            now["n"] += 1.0
            return now["n"]
        with mock.patch.object(jev.time, "monotonic", fake_mono):
            self.assertIsNotNone(run(b2.decide_proactive("a", "x", ["flat"])))
            self.assertIsNotNone(run(b2.decide_proactive("b", "x", ["flat"])))
            self.assertIsNone(run(b2.decide_proactive("c", "x", ["flat"])))

    def test_act_carries_the_judge_only_when_not_off(self):
        b = self._brain({"answers": {
            "eyes1": {"type": "choice", "choice": "default",
                      "confidence": 0.5, "probabilities": {"default": 0.5}},
            "mouth1": {"type": "choice", "choice": "default",
                       "confidence": 0.5, "probabilities": {"default": 0.5}},
            "unseen": {"type": "noul", "noul": 0.12}},
            "usage": {"input_tokens": 100, "output_tokens": 5}})
        with mock.patch.object(decisions, "mode", return_value="shadow"):
            plan = run(b.act("hi", ["one line only"], senses="cpu 10% cpu"))
        self.assertAlmostEqual(0.12, plan.unseen)
        body = b._call.call_args[0][0]
        self.assertIn("unseen", body["questions"])
        self.assertIn("she_can_sense", body["state"])
        # with the judge off, a one-beat line is not worth a call at all
        with mock.patch.object(decisions, "mode", return_value="off"):
            self.assertIsNone(run(b.act("hi", ["one line only"])))

    def test_one_beat_act_runs_when_the_judge_is_on(self):
        b = self._brain({"answers": {
            "eyes1": {"type": "choice", "choice": "default",
                      "confidence": 0.5, "probabilities": {"default": 0.5}},
            "mouth1": {"type": "choice", "choice": "default",
                       "confidence": 0.5, "probabilities": {"default": 0.5}},
            "unseen": {"type": "noul", "noul": 0.4}},
            "usage": {"input_tokens": 100, "output_tokens": 5}})
        with mock.patch.object(decisions, "mode", return_value="live"):
            plan = run(b.act("hi", ["single beat"], senses="cpu 10% cpu"))
        self.assertIsNotNone(plan)
        self.assertAlmostEqual(0.4, plan.unseen)


# ------------------------------------------------------------- canned text

class NoCannedTextTests(unittest.TestCase):
    def test_no_notice_pool_left_in_rules(self):
        self.assertFalse(hasattr(rules, "MOCK_NOTICES"))
        self.assertFalse(hasattr(rules.RuleBrain, "_notice_pool"))

    def test_silent_life_emits_no_notices(self):
        r = rules.RuleBrain()
        r.last_look_at = 0.0
        r.last_twitch_at = 0.0
        msgs = r.update({"cpu": 10.0, "ram": 50.0, "temp": None,
                         "battery": 50.0, "disk": 95.0}, now=100000.0,
                        aware={"fullscreen": False})
        self.assertFalse(any(m.get("type") == "notice" for m in msgs))


if __name__ == "__main__":
    unittest.main()
