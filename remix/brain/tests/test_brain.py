# Offline tests for the brain. Run from remix/:
#   .venv/bin/python -m unittest discover -s brain/tests -t . -v
import asyncio
import os
import random
import sys
import tempfile
import textwrap
import time
import unittest
import wave
from collections import Counter
from unittest import mock

os.environ.setdefault("REMIX_TTS_DIR", tempfile.mkdtemp(prefix="remix-test-voice-"))

from brain import config, decisions, faces, grounding, jev, main as brain_main, rules, speech, stt, tts  # noqa: E402
from brain.faces import Face  # noqa: E402

VITALS = {"cpu": 40, "ram": 60, "temp": 53, "battery": 80, "disk": 99}


def run(coro):
    return asyncio.run(coro)


def probs(**kw):
    best = max(kw, key=kw.get)
    return {"type": "choice", "choice": best, "confidence": kw[best], "probabilities": kw}


# ------------------------------------------------------------------ Jev

class JevTests(unittest.TestCase):
    def setUp(self):
        self._saved = (config.JEV_ENABLED, config.JEV_API_KEY, config.JEV_MAX_PER_HOUR,
                       config.JEV_COOLDOWN_S, jev._post, config.DECISIONS_LOG,
                       decisions._modes)
        config.JEV_ENABLED, config.JEV_API_KEY = True, "test"
        config.JEV_MAX_PER_HOUR, config.JEV_COOLDOWN_S = 60, 3.0
        config.DECISIONS_LOG = False          # keep the suite out of the log file
        decisions._modes.clear()
        for _k in decisions.KINDS:
            decisions._modes[_k] = "off"      # this suite predates the seam
        self.calls = []

    def tearDown(self):
        (config.JEV_ENABLED, config.JEV_API_KEY, config.JEV_MAX_PER_HOUR,
         config.JEV_COOLDOWN_S, jev._post, config.DECISIONS_LOG,
         decisions._modes) = self._saved

    def fake(self, answers):
        def _post(url, key, body, timeout):
            self.calls.append(body)
            return {"answers": answers(body), "usage": {"input_tokens": 1, "output_tokens": 1}}
        jev._post = _post

    def test_eyes_and_mouth_are_separate_questions(self):
        self.fake(lambda b: {"stance": probs(flat=1.0), "eyes": probs(tired=1.0),
                             "mouth": probs(smirk=1.0)})
        d = run(jev.JevBrain(random.Random(1)).react("hey", VITALS))
        self.assertEqual(d.face, Face("tired", "smirk"))          # a mix no single drawing has
        q = self.calls[0]["questions"]
        # Phase 3 riders + the blush noul ride the same request
        self.assertEqual(set(q), {"stance", "eyes", "mouth", "blush",
                                  "user_intent", "commitment", "commit_when"})
        self.assertEqual(set(q["eyes"]["criteria"]), set(faces.EYES_TEXT))
        self.assertEqual(set(q["mouth"]["criteria"]), set(faces.MOUTHS_TEXT))
        self.assertNotIn("sarcastic", str(self.calls[0]["state"]))  # old scene primed smirk

    def test_sampling_follows_probabilities_not_just_the_top(self):
        rng = random.Random(7)
        ans = probs(default=0.5, really=0.3, anger=0.2)
        c = Counter(jev.sample_choice(ans, list(faces.EYES_TEXT), [], rng) for _ in range(4000))
        self.assertTrue(all(c[k] > 400 for k in ("default", "really", "anger")), c)
        self.assertEqual(c["tired"], 0)                           # never invents zero-prob picks

    def test_repeat_penalty_reduces_repeats(self):
        rng = random.Random(3)
        ans = probs(bottomlid=0.6, default=0.4)
        base = Counter(jev.sample_choice(ans, list(faces.EYES_TEXT), [], rng) for _ in range(4000))
        pen = Counter(jev.sample_choice(ans, list(faces.EYES_TEXT), ["bottomlid", "bottomlid"], rng)
                      for _ in range(4000))
        self.assertLess(pen["bottomlid"], base["bottomlid"] * 0.7)

    def test_garbled_answers_degrade_to_neutral(self):
        rng = random.Random(0)
        valid = list(faces.EYES_TEXT)
        self.assertEqual(jev.sample_choice(None, valid, [], rng), "default")
        self.assertEqual(jev.sample_choice({"choice": "bogus"}, valid, [], rng), "default")
        self.assertEqual(jev.sample_choice({"choice": "tired"}, valid, [], rng), "tired")
        self.assertEqual(jev.sample_choice({"probabilities": {"x": "NaN?"}}, valid, [], rng), "default")

    def test_a_week_of_the_same_input_no_longer_means_one_face(self):
        # The log: 30 inputs -> smirk 30 times. Even with a smirk-heavy Jev the faces now vary.
        self.fake(lambda b: {"stance": probs(playful=0.5, annoyed=0.5),
                             "eyes": probs(bottomlid=0.5, really=0.3, default=0.2),
                             "mouth": probs(smirk=0.5, default=0.3, pout=0.2)})
        j = jev.JevBrain(random.Random(11))
        config.JEV_COOLDOWN_S = 0
        seen = Counter()
        for i in range(30):
            seen[str(run(j.react(f"line {i}", VITALS)).face)] += 1
        self.assertGreaterEqual(len(seen), 5, seen)
        self.assertLess(max(seen.values()), 12, seen)

    def test_act_puts_each_beats_text_in_its_question(self):
        self.fake(lambda b: {f"{u}{i}": probs(default=1.0) for i in (1, 2, 3) for u in ("eyes", "mouth")})
        beats = ["Ew.", "Gross.", "Close it."]
        plan = run(jev.JevBrain(random.Random(0)).act("tabs", beats))
        self.assertEqual(len(plan.faces), 3)
        q = self.calls[0]["questions"]
        self.assertIn("Gross.", q["eyes2"]["instructions"])
        self.assertIn("Close it.", q["mouth3"]["instructions"])

    def test_act_skipped_for_one_beat_and_not_blocked_by_cooldown(self):
        self.fake(lambda b: {"stance": probs(flat=1.0), "eyes": probs(default=1.0), "mouth": probs(default=1.0),
                             "eyes1": probs(default=1.0), "mouth1": probs(default=1.0),
                             "eyes2": probs(tired=1.0), "mouth2": probs(default=1.0)})
        saved = dict(decisions._modes)
        decisions._modes["judge_unseen"] = "off"   # no judge: one beat = no call
        try:
            j = jev.JevBrain(random.Random(0))
            self.assertIsNotNone(run(j.react("x", VITALS)))
            self.assertIsNone(run(j.act("x", ["only one."])))
            self.assertEqual(len(self.calls), 1)
            plan = run(j.act("x", ["a.", "b."]))                      # immediately after react
            self.assertIsNotNone(plan)
            self.assertEqual(len(self.calls), 2)
        finally:
            decisions._modes.clear()
            decisions._modes.update(saved)

    def test_hourly_cap_counts_both_call_kinds_and_fails_soft(self):
        self.fake(lambda b: {"stance": probs(flat=1.0), "eyes": probs(default=1.0), "mouth": probs(default=1.0),
                             "eyes1": probs(default=1.0), "mouth1": probs(default=1.0),
                             "eyes2": probs(default=1.0), "mouth2": probs(default=1.0)})
        config.JEV_MAX_PER_HOUR = 2
        j = jev.JevBrain(random.Random(0))
        self.assertIsNotNone(run(j.react("a", VITALS)))
        self.assertIsNotNone(run(j.act("a", ["x.", "y."])))
        config.JEV_COOLDOWN_S = 0
        self.assertIsNone(run(j.react("b", VITALS)))              # capped -> None -> local path

    def test_failures_and_disabled_return_none(self):
        def boom(*a, **k): raise OSError("net down")
        jev._post = boom
        self.assertIsNone(run(jev.JevBrain().react("a", VITALS)))
        config.JEV_ENABLED = False
        self.assertIsNone(run(jev.JevBrain().react("a", VITALS)))


# ------------------------------------------------------------ grounding

class GroundingTests(unittest.TestCase):
    def test_the_actual_hallucinations_from_the_log_are_caught(self):
        bad = [
            ("Examples?", "you still have the tab open from three days ago where you tried to diagnose your weird rash."),
            ("wait wtf which browser?", "chromium. third tab. you were crying about it looking like a spider bite."),
            ("WHICH CHROMIUM", "window two, workspace four. I see everything."),
            ("What tab?", "bruh. I *am* your active processes."),
            ("I JUST BOOTED.", "then you left a zombie process hanging in workspace four, you dumbass. kill it."),
        ]
        for user, reply in bad:
            self.assertTrue(grounding.check(reply, user, "", VITALS), reply)

    def test_made_up_vitals_are_caught_real_ones_pass(self):
        self.assertTrue(grounding.check("it's 91C in here", "hot?", "", VITALS))
        self.assertTrue(grounding.check("battery's at twelve percent", "bat?", "", VITALS))
        self.assertFalse(grounding.check("53C. fine.", "temp?", "", VITALS))
        self.assertFalse(grounding.check("ninety-nine percent full. delete shit.", "disk?", "", VITALS))
        self.assertTrue(grounding.check("it's 50C", "temp?", "", {"temp": None}))   # no sensor = no number

    def test_user_can_raise_a_topic_and_her_own_window_is_allowed(self):
        self.assertFalse(grounding.check("can't see your tabs, dumbass", "do you see my tabs", "", VITALS))
        self.assertFalse(grounding.check("I'm squished in this tiny window.", "where are you", "", VITALS))
        self.assertTrue(grounding.check("you left that on yesterday", "hm", "", VITALS))

    def test_prompt_lists_everything_she_sees_and_the_bad_example_is_gone(self):
        p = speech.build_system_prompt(VITALS, None, {"window": [640, 360], "screen": [1280, 720], "area_fraction": 0.25})
        self.assertIn("WHAT YOU CAN SEE", p)
        self.assertIn("640x360", p)
        self.assertNotIn("forty fucking tabs", p)

    def test_retry_then_deliver_second_try(self):
        # plan 1.5.3: no canned deflection. One retry; when the second try
        # is still flagged it ships as-is (her words, her responsibility).
        calls = []
        async def fake_llm(messages):
            calls.append(messages)
            return "you've got three tabs open in chromium"
        orig = speech._call_llm
        speech._call_llm = fake_llm
        try:
            out = run(speech._grounded("you've got three tabs open in chromium", [{"role": "user", "content": "hi"}], "hi", VITALS))
        finally:
            speech._call_llm = orig
        self.assertEqual(len(calls), 1)                            # exactly one retry
        self.assertEqual(out, "you've got three tabs open in chromium")

    def test_clean_reply_needs_no_retry(self):
        calls = []
        async def fake_llm(messages):
            calls.append(messages)
            return "fine."
        orig = speech._call_llm
        speech._call_llm = fake_llm
        try:
            out = run(speech._grounded("fine.", [{"role": "user", "content": "hi"}], "hi", VITALS))
        finally:
            speech._call_llm = orig
        self.assertEqual(len(calls), 0)   # clean reply: no extra call
        self.assertEqual(out, "fine.")

    def test_retry_pool_down_stays_silent(self):
        async def fake_llm(messages):
            return None
        orig = speech._call_llm
        speech._call_llm = fake_llm
        try:
            out = run(speech._grounded("you've got three tabs open in chromium", [{"role": "user", "content": "hi"}], "hi", VITALS))
        finally:
            speech._call_llm = orig
        self.assertIsNone(out)   # provider down -> silence, never a canned line

    def test_no_canned_deflections_remain(self):
        self.assertFalse(hasattr(grounding, "DEFLECTIONS")
                         and grounding.DEFLECTIONS,
                         "canned deflection pool must stay retired")
        self.assertFalse(hasattr(grounding, "deflection"))

    def test_beats_keep_trailing_fragment(self):
        self.assertEqual(speech.beats("ugh. ...fine. whatever"), ["ugh.", "...fine.", "whatever"])
        self.assertEqual(speech.beats("Bite me"), ["Bite me"])
        self.assertEqual(len(speech.beats("a. b. c. d. e. f.")), 4)


# ------------------------------------------------------------------ TTS

@unittest.skipUnless(tts.available(), "espeak-ng not installed")
class TtsTests(unittest.TestCase):
    def test_beats_become_one_wav_with_increasing_cue_times(self):
        s = run(tts.synthesize(["Ugh.", "...fine.", "Close some shit, it's 53C."], "unit"))
        self.assertEqual(len(s.starts), 3)
        self.assertEqual(s.starts[0], 0.0)
        self.assertTrue(s.starts[0] < s.starts[1] < s.starts[2])
        with wave.open(s.path) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth()), (1, 2))   # Godot's loader contract
            self.assertAlmostEqual(w.getnframes() / w.getframerate(), s.total, places=2)
        # each beat starts where the previous one ended + the gap
        self.assertAlmostEqual(s.starts[1], s.durations[0] + config.TTS_BEAT_GAP_S, places=2)

    def test_speakable_cleans_text(self):
        self.assertEqual(tts.speakable("53C and 40%"), "53 degrees and 40 percent")
        self.assertNotIn("*", tts.speakable("*shit*"))
        self.assertEqual(tts.speakable("cpu"), "C P U")
        self.assertEqual(tts.speakable("😀"), "")

    def test_disabled_means_no_voice(self):
        config.TTS_ENABLED = False
        try:
            self.assertIsNone(run(tts.synthesize(["hi."], "x")))
        finally:
            config.TTS_ENABLED = True


# ------------------------------------------------------------------ STT

def _fake_recorder(seconds_of_audio: float, loud=True, endless=False) -> str:
    """A script standing in for parec: writes known PCM to stdout."""
    path = os.path.join(tempfile.mkdtemp(), "rec.py")
    amp = 8000 if loud else 3
    with open(path, "w") as fh:
      fh.write(textwrap.dedent(f"""
        import sys, struct, time
        n = int({seconds_of_audio} * 16000)
        buf = struct.pack('<%dh' % n, *([{amp}, -{amp}] * (n // 2)))
        sys.stdout.buffer.write(buf); sys.stdout.buffer.flush()
        {"time.sleep(3600)" if endless else "time.sleep(0.4)"}
    """))
    return f"{sys.executable} {path}"


class FakeSeg:
    def __init__(self, text, nsp=0.0, lp=-0.2):
        self.text, self.no_speech_prob, self.avg_logprob = text, nsp, lp


class FakeModel:
    def __init__(self, segs): self.segs, self.seen = segs, []
    def transcribe(self, audio, **kw):
        self.seen.append((len(audio), kw))
        return iter(self.segs), None


class SttTests(unittest.TestCase):
    def setUp(self):
        self._saved = (config.PTT_REC_CMD, config.PTT_MAX_PRESS_S, config.PTT_MIN_PRESS_S)
        config.PTT_MIN_PRESS_S = 0.1

    def tearDown(self):
        config.PTT_REC_CMD, config.PTT_MAX_PRESS_S, config.PTT_MIN_PRESS_S = self._saved

    async def _press(self, ptt, hold):
        self.assertEqual(await ptt.start(), "ok")
        await asyncio.sleep(hold)
        return await ptt.stop()

    def test_full_capture_reaches_model_and_text_comes_back(self):
        config.PTT_REC_CMD = _fake_recorder(2.0)
        ptt = stt.PushToTalk()
        ptt._model = FakeModel([FakeSeg(" what time is it ")])
        h = run(self._press(ptt, 0.8))
        self.assertEqual((h.text, h.reason), ("what time is it", "ok"))
        n, kw = ptt._model.seen[0]
        self.assertEqual(n, 32000)               # all 2.0 s arrived: the tail was NOT clipped
        self.assertTrue(kw["vad_filter"] and kw["language"] == "en" and not kw["condition_on_previous_text"])
        self.assertEqual(len(ptt._pcm), 0)       # audio dropped after use
        self.assertFalse(ptt.listening)

    def test_quiet_audio_never_reaches_the_model(self):
        config.PTT_REC_CMD = _fake_recorder(1.0, loud=False)
        ptt = stt.PushToTalk()
        ptt._model = FakeModel([FakeSeg("hello")])
        self.assertEqual(run(self._press(ptt, 0.6)).reason, "silence")
        self.assertEqual(ptt._model.seen, [])

    def test_short_press_is_ignored(self):
        config.PTT_REC_CMD = _fake_recorder(1.0)
        config.PTT_MIN_PRESS_S = 0.5
        ptt = stt.PushToTalk()
        ptt._model = FakeModel([FakeSeg("hello")])
        self.assertEqual(run(self._press(ptt, 0.15)).reason, "short")

    def test_missed_key_up_still_closes_the_mic(self):
        config.PTT_REC_CMD = _fake_recorder(1.0, endless=True)   # recorder that never exits
        config.PTT_MAX_PRESS_S = 0.5
        fired = []
        async def go():
            ptt = stt.PushToTalk(on_timeout=lambda: fired.append(1))
            ptt._model = FakeModel([FakeSeg("x")])
            await ptt.start()
            await asyncio.sleep(1.0)
            self.assertTrue(fired, "watchdog never fired")
            await ptt.stop()
            self.assertFalse(ptt.listening)
        run(go())

    def test_missing_recorder_is_reported_not_raised(self):
        config.PTT_REC_CMD = "/definitely/not/a/recorder"
        self.assertEqual(run(stt.PushToTalk().start()), "no_mic")

    def test_whisper_phantoms_and_low_confidence_are_dropped(self):
        self.assertEqual(stt.clean_segments([FakeSeg("Thank you.")]), "")
        self.assertEqual(stt.clean_segments([FakeSeg("you", 0.9, -1.2)]), "")
        self.assertEqual(stt.clean_segments([FakeSeg("garbled", 0.1, -2.0)]), "")
        self.assertEqual(stt.clean_segments([FakeSeg("open the pod bay doors", 0.1, -0.3)]), "open the pod bay doors")
        self.assertEqual(stt.clean_segments([]), "")

    def test_unclear_when_model_returns_only_junk(self):
        config.PTT_REC_CMD = _fake_recorder(1.0)
        ptt = stt.PushToTalk()
        ptt._model = FakeModel([FakeSeg("Thank you.")])
        self.assertEqual(run(self._press(ptt, 0.6)).reason, "unclear")

    def test_recorder_autodetect_prefers_parec(self):
        config.PTT_REC_CMD = "auto"
        cmd = stt.recorder_cmd()
        if cmd:
            self.assertIn(os.path.basename(cmd[0]), ("parec", "pw-record", "arecord"))
            self.assertIn("16000", " ".join(cmd))


# ------------------------------------------------------- the whole exchange

class ExchangeTests(unittest.TestCase):
    """main._on_user_text end to end with a fake LLM, fake Godot socket, real espeak."""

    def setUp(self):
        self.sent = []
        async def send(m): self.sent.append(m)
        self._saved = (brain_main.ws_server.send, speech.generate_reply, config.JEV_ENABLED, config.SUBTITLES)
        brain_main.ws_server.send = send
        config.JEV_ENABLED = False
        config.SUBTITLES = "auto"
        brain_main._rule = rules.RuleBrain()
        brain_main._jev = jev.JevBrain(random.Random(0))
        brain_main._latest_snapshot = dict(VITALS)

    def tearDown(self):
        brain_main.ws_server.send, speech.generate_reply, config.JEV_ENABLED, config.SUBTITLES = self._saved

    def go(self, reply, text="hi"):
        async def fake(*a, **k): return reply
        speech.generate_reply = fake
        async def flow():
            await brain_main._on_user_text({"type": "user_text", "text": text})
            await asyncio.sleep(0.05)
            for t in (brain_main._settle_task, brain_main._silent_task):
                if t: t.cancel()
        run(flow())
        return {t: [m for m in self.sent if m["type"] == t] for t in {m["type"] for m in self.sent}}

    @unittest.skipUnless(tts.available(), "espeak-ng not installed")
    def test_voiced_reply_is_one_perform_with_per_beat_mixed_faces(self):
        by = self.go("Ew. Gross. ...fine, whatever.", "look at this")
        self.assertEqual(by["say"][0]["text"], "Ew. Gross. ...fine, whatever.")
        p = by["perform"][0]
        self.assertTrue(os.path.exists(p["audio"]))
        ts = [c["t"] for c in p["cues"]]
        self.assertEqual(len(ts), 3)
        self.assertTrue(ts[0] == 0.0 and ts == sorted(ts) and ts[1] > 0.3)
        for c in p["cues"]:
            self.assertIn(c["eyes"], faces.EYES_TEXT)
            self.assertIn(c["mouth"], faces.MOUTHS_TEXT)
            self.assertNotIn("pose", c)
        # every wire form carries the explicit blush level (0 on plain faces)
        self.assertEqual(set(p["then"]), {"eyes", "mouth", "blush"})
        self.assertNotIn("notice", by, "no captions when she has a voice (and no 'one sec' when fast)")
        self.assertIn("pose", by)                                    # the instant reaction came first
        # typed text echoes FIRST (for the human's own HUD/terminal), then
        # her instant reaction pose
        self.assertEqual(self.sent[0]["type"], "transcript")
        self.assertEqual(self.sent[1]["type"], "pose")

    def test_no_voice_shows_captions_and_steps_faces_on_the_brain_clock(self):
        saved = config.TTS_ENABLED
        config.TTS_ENABLED = False
        try:
            by = self.go("Bite me. Ew.", "you suck")
        finally:
            config.TTS_ENABLED = saved
        self.assertNotIn("perform", by)
        self.assertEqual(by["notice"][0]["text"], "Bite me. Ew.")
        poses = [m for m in self.sent if m["type"] == "pose"]
        self.assertGreaterEqual(len(poses), 2)                       # reaction + first beat at least
        self.assertEqual(poses[0]["eyes"], "anger")

    def test_llm_down_gives_a_notice_not_a_crash(self):
        by = self.go(None)
        self.assertIn("notice", by)
        self.assertNotIn("perform", by)

    def test_settle_message_restates_the_baseline(self):
        self.assertEqual(brain_main._rule.baseline_message(), {"type": "pose", "pose": "default"})


class ChatterVoiceTests(unittest.TestCase):
    """REMIX_CHATTER_VOICE: the room policy for unprompted lines."""

    def _modes(self, mode):
        with mock.patch.object(config, "CHATTER_VOICE", mode):
            return [brain_main._chatter_uses_voice(audible)
                    for audible in (True, False, None)]

    def test_auto_is_polite_talks_only_into_a_quiet_room(self):
        self.assertEqual(self._modes("auto"), [False, True, True])

    def test_always_talks_over_anything(self):
        self.assertEqual(self._modes("always"), [True, True, True])

    def test_never_is_captions_only(self):
        self.assertEqual(self._modes("never"), [False, False, False])


class WatchTests(unittest.IsolatedAsyncioTestCase):
    """She reacts to what the human does: a wordless perk when they switch
    windows or come back from deep idle. $0, no speech, self-restoring."""

    def setUp(self):
        self.sent = []
        self._send = brain_main.ws_server.send

        async def send(msg: dict) -> None:
            self.sent.append(msg)

        brain_main.ws_server.send = send

    def tearDown(self):
        brain_main.ws_server.send = self._send

    async def test_perk_aims_then_reassumes_the_baseline(self):
        rule = rules.RuleBrain()
        real_sleep = asyncio.sleep

        async def fast(_s):
            await real_sleep(0)

        with mock.patch.object(brain_main.asyncio, "sleep", fast):
            task = brain_main._perk(rule, [-0.6, 0.2], None)
            await task
        self.assertEqual(self.sent[0],
                         {"type": "pose", "eyes": "really", "look": [-0.6, 0.2]})
        self.assertEqual(self.sent[1], rule.baseline_message())

    async def test_perk_without_aim_is_bare_pointer_eyes(self):
        rule = rules.RuleBrain()
        real_sleep = asyncio.sleep

        async def fast(_s):
            await real_sleep(0)

        with mock.patch.object(brain_main.asyncio, "sleep", fast):
            await brain_main._perk(rule, None, None)
        self.assertEqual(self.sent[0], {"type": "pose", "eyes": "really"})
        # eyes-only pose: Godot keeps/recenters gaze itself, no explicit look
        self.assertNotIn("look", self.sent[0])


if __name__ == "__main__":
    unittest.main()
