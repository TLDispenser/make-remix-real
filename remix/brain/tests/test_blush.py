# Blush tests (offline): the third part of a face. Jev answers a yes/no
# "is she blushing?" beside the eyes and mouth it already picks; the face
# carries it on the wire as `blush` (0..1); BLUSH_BASELINE is the floor.
# Run from remix/:
#   .venv/bin/python -m unittest discover -s brain/tests -t . -v
import asyncio
import os
import random
import tempfile
import unittest

os.environ.setdefault("REMIX_TTS_DIR", tempfile.mkdtemp(prefix="remix-test-voice-"))

from brain import config, decisions, jev  # noqa: E402
from brain.faces import EYES_TEXT, POSES, Face, guess_face  # noqa: E402

VITALS = {"cpu": 40, "ram": 60, "temp": 53, "battery": 80, "disk": 99}


def run(coro):
    return asyncio.run(coro)


def probs(**kw):
    best = max(kw, key=kw.get)
    return {"type": "choice", "choice": best, "confidence": kw[best], "probabilities": kw}


def noul(p):
    return {"type": "noul", "noul": p}


class FaceWireTests(unittest.TestCase):
    def setUp(self):
        self._base = config.BLUSH_BASELINE
        config.BLUSH_BASELINE = 0.0

    def tearDown(self):
        config.BLUSH_BASELINE = self._base

    def test_blush_is_optional_and_defaults_to_none(self):
        self.assertEqual(Face("default", "default"), Face("default", "default", 0.0))
        self.assertEqual(0.0, Face("tsun", "tsun").blush)

    def test_every_wire_form_carries_an_explicit_blush(self):
        # Always explicit (even 0), so a blush never sticks to the next face.
        f = Face("bashful", "bashful", 0.8)
        self.assertEqual(0.8, f.msg()["blush"])
        self.assertEqual(0.8, f.cue(1.0)["blush"])
        self.assertEqual(0.8, f.spec()["blush"])
        plain = Face("default", "default")
        self.assertEqual(0.0, plain.msg()["blush"])
        self.assertEqual(0.0, plain.cue(0.0)["blush"])
        self.assertEqual({"eyes", "mouth", "blush"}, set(plain.spec()))

    def test_baseline_is_a_floor_and_values_are_clamped(self):
        config.BLUSH_BASELINE = 0.3
        self.assertEqual(0.3, Face("default", "default").msg()["blush"])     # floor
        self.assertEqual(0.8, Face("default", "default", 0.8).msg()["blush"])  # above it
        config.BLUSH_BASELINE = 1.0                                            # old always-on look
        self.assertEqual(1.0, Face("default", "default").msg()["blush"])
        config.BLUSH_BASELINE = 0.0
        self.assertEqual(1.0, Face("default", "default", 7.0).msg()["blush"])  # clamp high
        self.assertEqual(0.0, Face("default", "default", -2.0).msg()["blush"])  # clamp low

    def test_look_still_rides_along(self):
        self.assertEqual([0.1, 0.2], Face("default", "default", 0.5).msg(look=[0.1, 0.2])["look"])

    def test_bashful_blushes_for_free(self):
        # the $0 paths (shortcut + flattery words) keep bashful = blushing
        self.assertEqual(1.0, POSES["bashful"].blush)
        self.assertEqual(1.0, guess_face("oh you").blush)
        self.assertEqual(0.0, guess_face("fuck you").blush)

    def test_stale_description_is_gone(self):
        # Jev must not be told the hatching belongs to the eyes any more.
        self.assertNotIn("hatched", EYES_TEXT["bashful"])


class JevBlushTests(unittest.TestCase):
    def setUp(self):
        self._saved = (config.JEV_ENABLED, config.JEV_API_KEY, config.JEV_MAX_PER_HOUR,
                       config.JEV_COOLDOWN_S, jev._post, config.DECISIONS_LOG,
                       dict(decisions._modes), config.JEV_BLUSH,
                       config.JEV_BLUSH_THRESHOLD, config.LISTEN_JEV)
        config.JEV_ENABLED, config.JEV_API_KEY = True, "test"
        config.JEV_MAX_PER_HOUR, config.JEV_COOLDOWN_S = 60, 3.0
        config.DECISIONS_LOG = False
        config.JEV_BLUSH, config.JEV_BLUSH_THRESHOLD = True, 0.5
        decisions._modes.clear()
        for k in decisions.KINDS:
            decisions._modes[k] = "off"
        self.calls = []

    def tearDown(self):
        (config.JEV_ENABLED, config.JEV_API_KEY, config.JEV_MAX_PER_HOUR,
         config.JEV_COOLDOWN_S, jev._post, config.DECISIONS_LOG,
         modes, config.JEV_BLUSH, config.JEV_BLUSH_THRESHOLD,
         config.LISTEN_JEV) = self._saved
        decisions._modes.clear()
        decisions._modes.update(modes)

    def fake(self, answers):
        def _post(url, key, body, timeout):
            self.calls.append(body)
            return {"answers": answers(body), "usage": {"input_tokens": 1, "output_tokens": 1}}
        jev._post = _post

    def _react(self, blush_answer, eyes="tired", mouth="default"):
        base = {"stance": probs(flat=1.0), "eyes": probs(**{eyes: 1.0}),
                "mouth": probs(**{mouth: 1.0})}
        if blush_answer is not None:
            base["blush"] = blush_answer
        self.fake(lambda b: base)
        return run(jev.JevBrain(random.Random(1)).react("you're cute", VITALS))

    def test_the_react_call_asks_blush_in_the_same_request(self):
        d = self._react(noul(0.9))
        self.assertEqual(1, len(self.calls))                 # no extra request
        self.assertEqual("noul", self.calls[0]["questions"]["blush"]["type"])
        self.assertEqual(Face("tired", "default", 0.9), d.face)

    def test_below_threshold_is_no_blush(self):
        self.assertEqual(0.0, self._react(noul(0.3)).face.blush)
        self.assertEqual(0.0, self._react(noul(0.49)).face.blush)
        self.assertEqual(0.5, self._react(noul(0.5)).face.blush)

    def test_garbage_or_missing_answer_is_no_blush(self):
        self.assertEqual(0.0, self._react(None).face.blush)
        self.assertEqual(0.0, self._react(noul("junk")).face.blush)
        self.assertEqual(0.0, self._react({"type": "noul"}).face.blush)
        self.assertEqual(0.0, self._react("nonsense").face.blush)

    def test_probability_above_one_is_clamped(self):
        self.assertEqual(1.0, self._react(noul(1.7)).face.blush)

    def test_bashful_is_blushing_on_the_jev_path_too(self):
        # The pose table defines bashful AS blushing (1.0); the Jev path
        # honors the pose — bashful eyes/mouth floor the level at 1.0, even
        # when the noul says no (a pale bashful is not bashful). This is the
        # gap that showed in the logs: bashful/default, bashful/pout and
        # bashful/bashful with no +blush.
        self.assertEqual(1.0, self._react(noul(0.1), "bashful", "pout").face.blush)
        self.assertEqual(1.0, self._react(noul(0.0), "tired", "bashful").face.blush)
        self.assertEqual(1.0, self._react(None, "bashful", "bashful").face.blush)

    def test_switch_off_asks_nothing_and_blushes_never(self):
        config.JEV_BLUSH = False
        d = self._react(noul(0.99))
        self.assertNotIn("blush", self.calls[0]["questions"])
        self.assertEqual(0.0, d.face.blush)

    def test_peek_bashful_blushes_without_a_blush_question(self):
        # The listening reaction never asks the blush noul (partial); a
        # bashful pick must still blush — the pose, not the question.
        config.LISTEN_JEV = True
        base = {"stance": probs(flat=1.0), "eyes": probs(bashful=1.0),
                "mouth": probs(bashful=1.0)}
        self.fake(lambda b: base)
        d = run(jev.JevBrain(random.Random(3)).peek("um... hi?", VITALS))
        self.assertNotIn("blush", self.calls[0]["questions"])
        self.assertEqual(Face("bashful", "bashful", 1.0), d.face)

    def test_act_plan_gets_a_blush_per_beat(self):
        beats = ["fine.", "...you're not so bad.", "shut up."]

        def answers(body):
            q = body["questions"]
            self.assertEqual({"blush1", "blush2", "blush3"},
                             {k for k in q if k.startswith("blush")})
            a = {}
            for i in (1, 2, 3):
                a[f"eyes{i}"] = probs(default=1.0)
                a[f"mouth{i}"] = probs(default=1.0)
            a["blush1"], a["blush2"], a["blush3"] = noul(0.1), noul(0.85), noul(0.2)
            return a
        self.fake(answers)
        plan = run(jev.JevBrain(random.Random(2)).act("thanks", beats))
        self.assertEqual([0.0, 0.85, 0.0], [f.blush for f in plan.faces])
        # ...and the per-beat cues the brain sends carry exactly that
        self.assertEqual([0.0, 0.85, 0.0],
                         [f.cue(i)["blush"] for i, f in enumerate(plan.faces)])


if __name__ == "__main__":
    unittest.main()
