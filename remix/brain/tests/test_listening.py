# test_listening.py — "she reacts while you type/talk", offline:
#   * listening.py: reflexes, cue throttle, rhythm tells, stalls, peek asks,
#     accept/commit/reuse, lead stats
#   * jev.peek: its own scene + budget + reserve, never blocks the react call
#   * rules: the listening gate (alarms still pass; her own Whisper CPU spike doesn't)
#   * stt: rolling partials while the key is held
#   * main: partial -> peek shown -> final line REUSES it (react call skipped)
import asyncio
import os
import random
import sys
import tempfile
import time
import unittest

os.environ.setdefault("REMIX_TTS_DIR", tempfile.mkdtemp(prefix="remix-test-voice-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from brain import config, faces, jev, listening, rules, speech, stt  # noqa: E402
from brain import main as brain_main  # noqa: E402
from brain.faces import Face  # noqa: E402

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

VITALS = {"cpu": 10.0, "ram": 40.0, "temp": 45.0, "battery": 80.0, "disk": 40.0}


def run(coro):
    return asyncio.run(coro)


def probs(**kw):
    best = max(kw, key=kw.get)
    return {"type": "choice", "choice": best, "confidence": kw[best],
            "probabilities": kw}


def L(peek=False):
    lst = listening.Listener(clock=lambda: 0.0)
    lst.peek_enabled = peek
    return lst


def poses(reflexes):
    return [r for r in reflexes if r.msg.get("type") == "pose"]


# ------------------------------------------------------------------ similar

class SimilarTests(unittest.TestCase):
    def test_equal_ignoring_case_and_punctuation(self):
        self.assertTrue(listening.similar("Can you help me?", "can you help me"))

    def test_final_may_add_a_few_words(self):
        self.assertTrue(listening.similar("can you help me with this", "can you help me"))

    def test_final_adding_many_words_is_a_different_line(self):
        self.assertFalse(listening.similar(
            "can you help me with this because it keeps crashing every time", "can you help me"))

    def test_partial_last_word_may_be_cut_off(self):
        self.assertTrue(listening.similar("can you help me with", "can you help me wi"))

    def test_rewriting_earlier_words_is_different(self):
        self.assertFalse(listening.similar("can you stop helping me", "can you help me"))

    def test_empty_never_matches(self):
        self.assertFalse(listening.similar("", "hello there"))
        self.assertFalse(listening.similar("hello there", ""))

    def test_slack_is_configurable(self):
        self.assertFalse(listening.similar("a b c d e f", "a b c", slack_words=2))
        self.assertTrue(listening.similar("a b c d e f", "a b c", slack_words=3))


# ----------------------------------------------------------------- reflexes

class ReflexTests(unittest.TestCase):
    def test_first_text_is_an_onset_look_at_the_bar(self):
        r = L().feed("hey", now=1.0)
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0].msg["eyes"], r[0].msg["mouth"]), ("default", "default"))
        self.assertIn("look", r[0].msg)

    def test_empty_text_cancels(self):
        lst = L()
        lst.feed("hey", now=1.0)
        self.assertTrue(lst.active)
        self.assertEqual(lst.feed("   ", now=2.0), [])
        self.assertFalse(lst.active)

    def test_her_gaze_reads_along_as_the_line_grows(self):
        lst = L()
        lst.feed("hey", now=1.0)
        r = lst.feed("hey there my friend how are you doing today", now=1.5)
        looks = [x.msg["look"] for x in r if x.msg["type"] == "look"]
        self.assertEqual(len(looks), 1)
        self.assertGreater(looks[0][0], listening.read_look(3)[0])

    def test_gaze_updates_are_throttled(self):
        lst = L()
        lst.feed("hey", now=1.0)
        lst.feed("hey there my friend how", now=1.5)
        r = lst.feed("hey there my friend how are you doing today ok", now=1.6)
        self.assertEqual([x for x in r if x.msg["type"] == "look"], [])

    def test_strong_cue_changes_her_face(self):
        lst = L()
        lst.feed("hey", now=1.0)
        r = lst.feed("hey you suck", now=2.0)
        self.assertEqual([(x.msg["eyes"], x.msg["mouth"]) for x in poses(r)],
                         [("anger", "pout")])

    def test_cue_in_the_very_first_partial_still_shows(self):
        # voice: the first partial can already hold the insult
        r = L().feed("you suck", source="voice", now=1.0)
        shown = [(x.msg["eyes"], x.msg["mouth"]) for x in poses(r)]
        self.assertEqual(shown[-1], ("anger", "pout"))

    def test_help_alone_does_not_make_her_flinch(self):
        lst = L()
        for t, n in (("can you help me with", 1.0), ("can you help me with my help", 3.0)):
            self.assertEqual([x for x in poses(lst.feed(t, now=n))
                              if x.msg["eyes"] == "spooked"], [])

    def test_real_alarm_words_do_flinch(self):
        lst = L()
        lst.feed("so my laptop", now=1.0)
        r = lst.feed("so my laptop is overheating", now=2.0)
        self.assertEqual([x.msg["eyes"] for x in poses(r)], ["spooked"])

    def test_cue_blocked_by_the_throttle_shows_on_the_next_tick(self):
        lst = L()
        lst.feed("hey", now=1.0)
        lst.feed("hey lol", now=1.1)             # joke face shows (gap open: onset didn't stamp)
        r = lst.feed("hey lol you suck", now=1.2)  # inside 0.8 s: held back
        self.assertEqual(poses(r), [])
        t = lst.tick(now=2.0)
        self.assertEqual([(x.msg["eyes"], x.msg["mouth"]) for x in poses(t.reflexes)],
                         [("anger", "pout")])

    def test_cue_face_relaxes_after_its_words_leave_the_tail(self):
        lst = L()
        lst.feed("you suck", now=1.0)
        # eleven plain words push the cue out of the last-10-words tail
        lst.feed("you suck and then a lot of other plain words follow it now", now=2.0)
        t = lst.tick(now=4.5)
        self.assertEqual([x.msg["eyes"] for x in poses(t.reflexes)], ["default"])

    def test_big_deletion_gets_a_deadpan_once(self):
        lst = L()
        lst.feed("this is a rather long sentence i wrote", now=1.0)
        r = lst.feed("this is", now=2.0)
        self.assertEqual([(x.msg["eyes"], x.msg["mouth"]) for x in poses(r)],
                         [("really", "default")])
        lst.feed("this is a rather long sentence i wrote again", now=3.0)
        r2 = lst.feed("this", now=4.0)             # inside the cooldown
        self.assertEqual([x for x in poses(r2) if x.msg["eyes"] == "really"], [])

    def test_voice_partials_shrinking_is_not_a_rewrite(self):
        lst = L()
        lst.feed("this is a rather long sentence i said", source="voice", now=1.0)
        r = lst.feed("this is", source="voice", now=2.0)
        self.assertEqual([x for x in poses(r) if x.msg["eyes"] == "really"], [])

    def test_wall_of_text_once(self):
        lst = L()
        lst.feed("word " * 40, now=1.0)
        r = lst.feed("word " * 41, now=2.0)
        self.assertEqual([x for x in poses(r) if x.msg["eyes"] == "tired"], [])  # already fired on first feed
        lst2 = L()
        first = lst2.feed("hey", now=1.0)
        r = lst2.feed("word " * 40, now=2.0)
        self.assertEqual([x.msg["eyes"] for x in poses(r)].count("tired"), 1)

    def test_quiet_tracks_the_line_but_sends_nothing(self):
        lst = L()
        self.assertEqual(lst.feed("you suck", now=1.0, quiet=True), [])
        self.assertTrue(lst.active)
        self.assertEqual(lst.text, "you suck")
        self.assertEqual(lst.tick(now=9.0, quiet=True).reflexes, [])


# -------------------------------------------------------------------- tick

class StallTests(unittest.TestCase):
    def test_stall_look_then_twitch_then_release(self):
        lst = L()
        lst.feed("so i was thinking", now=1.0)
        self.assertEqual(lst.tick(now=2.0).reflexes, [])
        look = lst.tick(now=3.6).reflexes
        self.assertEqual([x.msg["type"] for x in look], ["look"])
        self.assertEqual(lst.tick(now=4.0).reflexes, [])            # once
        tw = lst.tick(now=7.2).reflexes
        self.assertEqual([x.msg["type"] for x in tw], ["twitch"])
        self.assertFalse(lst.tick(now=10.0).release)
        self.assertTrue(lst.tick(now=15.5).release)

    def test_voice_does_not_twitch_while_you_think(self):
        lst = L()
        lst.feed("so i was thinking", source="voice", now=1.0)
        self.assertEqual(lst.tick(now=8.0).reflexes, [])

    def test_inactive_tick_is_a_noop(self):
        t = L().tick(now=5.0)
        self.assertEqual((t.reflexes, t.peek, t.release), ([], None, False))


# -------------------------------------------------------------------- peek

class PeekTests(unittest.TestCase):
    def setUp(self):
        self._saved = (config.LISTEN_PAUSE_S, config.LISTEN_MIN_WORDS,
                       config.LISTEN_JEV_PER_EXCHANGE)
        config.LISTEN_PAUSE_S, config.LISTEN_MIN_WORDS = 0.9, 4
        config.LISTEN_JEV_PER_EXCHANGE = 2

    def tearDown(self):
        (config.LISTEN_PAUSE_S, config.LISTEN_MIN_WORDS,
         config.LISTEN_JEV_PER_EXCHANGE) = self._saved

    def test_off_by_default(self):
        lst = L(peek=False)
        lst.feed("can you help me with this", now=1.0)
        self.assertIsNone(lst.tick(now=3.0).peek)

    def test_asks_only_at_a_pause_on_a_real_line(self):
        lst = L(peek=True)
        lst.feed("can you help me with this", now=1.0)
        self.assertIsNone(lst.tick(now=1.3).peek)                    # still typing
        req = lst.tick(now=2.2).peek
        self.assertIsNotNone(req)
        self.assertEqual(req.text, "can you help me with this")
        self.assertEqual(lst.stats.peeks_asked, 1)

    def test_short_lines_are_not_worth_a_call(self):
        lst = L(peek=True)
        lst.feed("hey you", now=1.0)
        self.assertIsNone(lst.tick(now=3.0).peek)

    def test_voice_needs_one_word_fewer(self):
        lst = L(peek=True)
        lst.feed("can you help", source="voice", now=1.0)
        self.assertIsNotNone(lst.tick(now=2.2).peek)

    def test_busy_suppresses_a_second_ask(self):
        lst = L(peek=True)
        lst.feed("can you help me with this", now=1.0)
        self.assertIsNone(lst.tick(now=2.2, peek_busy=True).peek)

    def test_second_peek_needs_new_text_and_the_cap_holds(self):
        lst = L(peek=True)
        lst.feed("can you help me with this", now=1.0)
        first = lst.tick(now=2.2).peek
        self.assertIsNotNone(first)
        self.assertIsNone(lst.tick(now=3.5).peek)                    # nothing new
        lst.feed("can you help me with this because the build keeps failing", now=4.0)
        second = lst.tick(now=5.2).peek
        self.assertIsNotNone(second)
        self.assertGreater(second.seq, first.seq)
        lst.feed("can you help me with this because the build keeps failing on ci "
                 "after every single push i make", now=6.0)
        self.assertIsNone(lst.tick(now=7.2).peek)                    # cap of 2

    def test_no_peek_on_a_line_that_has_gone_stale(self):
        lst = L(peek=True)
        lst.feed("can you help me with this", now=1.0)
        self.assertIsNone(lst.tick(now=12.0).peek)

    def test_accept_only_the_live_line(self):
        lst = L(peek=True)
        lst.feed("can you help me with this", now=1.0)
        req = lst.tick(now=2.2).peek
        self.assertTrue(lst.accept_peek(req.seq, "D", now=2.4))
        lst2 = L(peek=True)
        lst2.feed("can you help me with this", now=1.0)
        r2 = lst2.tick(now=2.2).peek
        lst2.feed("actually forget all of that, new topic entirely please", now=2.3)
        self.assertFalse(lst2.accept_peek(r2.seq, "D", now=2.5))
        self.assertEqual(lst2.stats.peeks_stale, 1)

    def test_accept_rejects_a_superseded_seq(self):
        lst = L(peek=True)
        lst.feed("can you help me with this", now=1.0)
        req = lst.tick(now=2.2).peek
        self.assertFalse(lst.accept_peek(req.seq + 5, "D", now=2.4))


# ------------------------------------------------------------------ commit

class CommitTests(unittest.TestCase):
    def _shown(self, text="can you help me with this", peek_at=2.4):
        lst = L(peek=True)
        lst.feed(text, now=1.0)
        req = lst.tick(now=2.2).peek
        self.assertTrue(lst.accept_peek(req.seq, "DECISION", now=peek_at))
        return lst

    def test_same_line_reuses_the_reaction_and_reports_the_lead(self):
        lst = self._shown()
        c = lst.commit("can you help me with this", now=5.4)
        self.assertEqual(c.reuse, "DECISION")
        self.assertAlmostEqual(c.lead_s, 3.0, places=3)
        self.assertTrue(c.peeked)
        self.assertEqual(lst.stats.reused, 1)
        self.assertFalse(lst.active)                                 # reset

    def test_a_few_more_words_still_reuses(self):
        c = self._shown().commit("can you help me with this please", now=5.0)
        self.assertEqual(c.reuse, "DECISION")

    def test_a_different_line_discards_it(self):
        lst = self._shown()
        c = lst.commit("never mind, tell me a joke about toasters instead", now=5.0)
        self.assertIsNone(c.reuse)
        self.assertTrue(c.peeked)
        self.assertEqual((lst.stats.discarded, lst.stats.reused), (1, 0))

    def test_no_peek_means_no_reuse_but_local_lead_is_counted(self):
        lst = L()
        lst.feed("hey you suck", now=1.0)
        c = lst.commit("hey you suck", now=4.0)
        self.assertIsNone(c.reuse)
        self.assertFalse(c.peeked)
        self.assertAlmostEqual(c.local_lead_s, 3.0, places=3)

    def test_much_shorter_line_forgets_the_old_peek(self):
        lst = self._shown("can you help me with this long thing here now")
        lst.feed("ok", now=3.0)                                      # deleted nearly everything
        c = lst.commit("ok", now=3.5)
        self.assertIsNone(c.reuse)
        self.assertFalse(c.peeked)

    def test_stats_summary_is_readable(self):
        lst = self._shown()
        lst.commit("can you help me with this", now=5.0)
        s = lst.stats.summary()
        self.assertIn("1 asked", s)
        self.assertIn("100%", s)


# ----------------------------------------------------------------- jev.peek

class JevPeekTests(unittest.TestCase):
    def setUp(self):
        self._saved = (config.JEV_ENABLED, config.JEV_API_KEY, config.JEV_MAX_PER_HOUR,
                       config.JEV_COOLDOWN_S, config.LISTEN_JEV, config.LISTEN_JEV_RESERVE,
                       config.LISTEN_JEV_MIN_GAP_S, jev._post)
        config.JEV_ENABLED, config.JEV_API_KEY = True, "test"
        config.JEV_MAX_PER_HOUR, config.JEV_COOLDOWN_S = 60, 3.0
        config.LISTEN_JEV, config.LISTEN_JEV_RESERVE = True, 20
        config.LISTEN_JEV_MIN_GAP_S = 0.0
        self.calls = []

        def _post(url, key, body, timeout):
            self.calls.append(body)
            return {"answers": {"stance": probs(flat=1.0), "eyes": probs(default=1.0),
                                "mouth": probs(default=1.0)},
                    "usage": {"input_tokens": 1, "output_tokens": 1}}
        jev._post = _post

    def tearDown(self):
        (config.JEV_ENABLED, config.JEV_API_KEY, config.JEV_MAX_PER_HOUR,
         config.JEV_COOLDOWN_S, config.LISTEN_JEV, config.LISTEN_JEV_RESERVE,
         config.LISTEN_JEV_MIN_GAP_S, jev._post) = self._saved

    def test_opt_in_off_makes_no_call(self):
        config.LISTEN_JEV = False
        self.assertIsNone(run(jev.JevBrain(random.Random(1)).peek("can you help me", VITALS)))
        self.assertEqual(self.calls, [])

    def test_uses_the_listening_scene_and_wording(self):
        d = run(jev.JevBrain(random.Random(1)).peek("can you help me", VITALS))
        self.assertIsNotNone(d)
        body = self.calls[0]
        self.assertEqual(body["state"]["scene"], jev.PEEK_SCENE)
        self.assertEqual(body["state"]["user_line"], "can you help me")
        self.assertIn("unfinished", body["questions"]["eyes"]["instructions"])
        self.assertIn("unfinished", body["questions"]["mouth"]["instructions"])

    def test_react_wording_is_untouched(self):
        run(jev.JevBrain(random.Random(1)).react("hey", VITALS))
        q = self.calls[0]["questions"]
        self.assertEqual(self.calls[0]["state"]["scene"], jev.REACT_SCENE)
        self.assertIn("the instant she reads this line", q["eyes"]["instructions"])

    def test_a_peek_never_blocks_the_real_reaction(self):
        j = jev.JevBrain(random.Random(1))
        run(j.peek("can you help me with this", VITALS))
        d = run(j.react("can you help me with this", VITALS))   # right away: no cooldown from the peek
        self.assertIsNotNone(d)
        self.assertEqual(len(self.calls), 2)

    def test_stops_short_of_the_cap_so_real_calls_are_never_starved(self):
        j = jev.JevBrain(random.Random(1))
        now = time.monotonic()
        j._call_times = [now] * (config.JEV_MAX_PER_HOUR - config.LISTEN_JEV_RESERVE)
        self.assertIsNone(run(j.peek("can you help me with this", VITALS)))
        self.assertEqual(self.calls, [])
        self.assertIsNotNone(run(j.react("hello there friend", VITALS)))  # real call still allowed

    def test_peeks_are_spaced(self):
        config.LISTEN_JEV_MIN_GAP_S = 5.0
        j = jev.JevBrain(random.Random(1))
        self.assertIsNotNone(run(j.peek("can you help me with this", VITALS)))
        self.assertIsNone(run(j.peek("can you help me with this build", VITALS)))
        self.assertEqual(len(self.calls), 1)

    def test_same_line_twice_is_one_credit(self):
        j = jev.JevBrain(random.Random(1))
        run(j.peek("can you help me with this", VITALS))
        d = run(j.peek("can you help me with this", VITALS))
        self.assertTrue(d.from_cache)
        self.assertEqual(len(self.calls), 1)

    def test_peek_counts_toward_the_hourly_total(self):
        j = jev.JevBrain(random.Random(1))
        run(j.peek("can you help me with this", VITALS))
        self.assertEqual(len(j._call_times), 1)


# --------------------------------------------------------------- rule gate

class RuleListenGateTests(unittest.TestCase):
    def snap(self, **kw):
        s = dict(VITALS)
        s.update(kw)
        return s

    def test_silent_life_is_held_while_listening(self):
        r = rules.RuleBrain()
        now = time.monotonic()
        r.last_look_at = now - 100.0
        free = rules.RuleBrain()
        free.last_look_at = now - 100.0
        self.assertTrue(any(m["type"] == "look" for m in free.update(self.snap(), now=now)))
        r.hold_listening(5.0)
        self.assertEqual(r.update(self.snap(), now=now), [])

    def test_release_restores_it(self):
        r = rules.RuleBrain()
        now = time.monotonic()
        r.last_look_at = now - 100.0
        r.hold_listening(5.0)
        r.release_listening()
        self.assertTrue(any(m["type"] == "look" for m in r.update(self.snap(), now=now)))

    def test_the_hold_lapses_on_its_own(self):
        r = rules.RuleBrain()
        r.hold_listening(0.0)
        r.last_look_at = time.monotonic() - 100.0
        time.sleep(0.01)
        self.assertTrue(any(m["type"] == "look"
                            for m in r.update(self.snap(), now=time.monotonic())))

    def test_real_alarms_still_get_through(self):
        r = rules.RuleBrain()
        r.hold_listening(5.0)
        out = r.update(self.snap(temp=config.TEMP_ALARM_C + 5), now=time.monotonic())
        self.assertEqual([m.get("pose") for m in out if m["type"] == "pose"], ["spooked"])

    def test_her_own_whisper_cpu_spike_is_not_an_alarm(self):
        r = rules.RuleBrain()
        r.hold_listening(5.0, self_load=True)
        out = r.update(self.snap(cpu=config.CPU_SPIKE + 1), now=time.monotonic())
        self.assertEqual([m for m in out if m["type"] == "pose"], [])

    def test_but_a_real_temperature_alarm_still_is_while_whisper_runs(self):
        r = rules.RuleBrain()
        r.hold_listening(5.0, self_load=True)
        out = r.update(self.snap(cpu=config.CPU_SPIKE + 1, temp=config.TEMP_ALARM_C + 5),
                       now=time.monotonic())
        self.assertEqual([m.get("pose") for m in out if m["type"] == "pose"], ["spooked"])

    def test_speaking_reflects_the_speech_gate(self):
        r = rules.RuleBrain()
        self.assertFalse(r.speaking())
        r.pause_for_speech(5.0)
        self.assertTrue(r.speaking())


# ------------------------------------------------------------- stt partials

class _Seg:
    def __init__(self, text, nsp=0.0, lp=-0.2):
        self.text, self.no_speech_prob, self.avg_logprob = text, nsp, lp


class _SeqModel:
    """Returns the next text each call (repeats the last)."""
    def __init__(self, texts):
        self.texts, self.calls = list(texts), 0

    def transcribe(self, audio, **kw):
        i = min(self.calls, len(self.texts) - 1)
        self.calls += 1
        self.kw = kw
        return iter([_Seg(self.texts[i])]), None


def _tone(seconds, amp=0.3):
    t = np.arange(int(16000 * seconds)) / 16000.0
    return (np.sin(2 * np.pi * 220 * t) * amp * 32767).astype(np.int16).tobytes()


@unittest.skipUnless(np is not None, "numpy not installed")
class SttPartialTests(unittest.TestCase):
    def setUp(self):
        self._saved = (config.LISTEN_STT_INTERVAL_S, config.LISTEN_STT_MIN_S,
                       config.PTT_MIN_PEAK, config.LISTEN_STT_PARTIALS)
        config.LISTEN_STT_INTERVAL_S, config.LISTEN_STT_MIN_S = 0.2, 1.0
        config.PTT_MIN_PEAK, config.LISTEN_STT_PARTIALS = 0.015, True

    def tearDown(self):
        (config.LISTEN_STT_INTERVAL_S, config.LISTEN_STT_MIN_S,
         config.PTT_MIN_PEAK, config.LISTEN_STT_PARTIALS) = self._saved

    def _held(self, texts, audio):
        got = []
        ptt = stt.PushToTalk(on_partial=got.append)
        ptt._model = _SeqModel(texts)
        ptt._pcm.extend(audio)
        ptt._proc = object()          # "the mic is open"
        ptt._closed.clear()
        return ptt, got

    def test_changed_partials_stream_while_the_key_is_held_then_stop(self):
        async def flow():
            ptt, got = self._held(["hello", "hello", "hello there"], _tone(1.5))
            task = asyncio.create_task(ptt._partial_loop())
            await asyncio.sleep(1.0)
            ptt._closed.set()                       # key up
            await asyncio.sleep(0.5)
            calls = ptt._model.calls
            await asyncio.sleep(0.5)
            task.cancel()
            return got, calls, ptt._model.calls
        got, calls_at_release, calls_later = run(flow())
        self.assertEqual(got, ["hello", "hello there"])          # duplicates are not re-sent
        self.assertLessEqual(calls_later - calls_at_release, 1)  # at most the in-flight one

    def test_too_little_audio_is_skipped(self):
        async def flow():
            ptt, _ = self._held(["hello"], _tone(0.4))
            return await ptt._partial_once(), ptt._model.calls
        text, calls = run(flow())
        self.assertEqual((text, calls), ("", 0))

    def test_silence_never_reaches_whisper(self):
        async def flow():
            ptt, _ = self._held(["hello"], _tone(1.5, amp=0.0001))
            return await ptt._partial_once(), ptt._model.calls
        text, calls = run(flow())
        self.assertEqual((text, calls), ("", 0))

    def test_a_read_ending_mid_sample_does_not_crash(self):
        async def flow():
            ptt, _ = self._held(["hello there"], _tone(1.5) + b"\x01")
            return await ptt._partial_once()
        self.assertEqual(run(flow()), "hello there")

    def test_partials_use_the_cheap_decode(self):
        async def flow():
            ptt, _ = self._held(["hi"], _tone(1.5))
            await ptt._partial_once()
            return ptt._model.kw
        self.assertEqual(run(flow())["beam_size"], 1)

    def test_phantoms_are_filtered_like_the_final_pass(self):
        async def flow():
            ptt, _ = self._held(["Thank you."], _tone(1.5))
            return await ptt._partial_once()
        self.assertEqual(run(flow()), "")

    def test_no_callback_no_task(self):
        ptt = stt.PushToTalk()
        self.assertIsNone(ptt._partial_task)


# --------------------------------------------------------------- main flow

class ListeningFlowTests(unittest.TestCase):
    """Partial line -> peek shown -> the final line reuses it. Fake Godot
    socket, fake Jev transport, fake LLM."""

    def setUp(self):
        self.sent, self.posts = [], []
        async def send(m): self.sent.append(m)
        self._saved = (brain_main.ws_server.send, speech.generate_reply,
                       config.JEV_ENABLED, config.JEV_API_KEY, config.LISTEN,
                       config.LISTEN_JEV, config.LISTEN_TICK_S, config.LISTEN_PAUSE_S,
                       config.LISTEN_JEV_MIN_GAP_S, config.LISTEN_JEV_RESERVE,
                       config.TTS_ENABLED, config.SUBTITLES, jev._post)
        brain_main.ws_server.send = send
        config.JEV_ENABLED, config.JEV_API_KEY = True, "test"
        config.LISTEN, config.LISTEN_JEV = True, True
        config.LISTEN_TICK_S, config.LISTEN_PAUSE_S = 0.02, 0.05
        config.LISTEN_JEV_MIN_GAP_S, config.LISTEN_JEV_RESERVE = 0.0, 0
        config.TTS_ENABLED, config.SUBTITLES = False, "never"
        self.delay = 0.0

        def _post(url, key, body, timeout):
            self.posts.append(body)
            if self.delay:
                time.sleep(self.delay)
            return {"answers": {"stance": probs(playful=1.0), "eyes": probs(bottomlid=1.0),
                                "mouth": probs(smirk=1.0)},
                    "usage": {"input_tokens": 1, "output_tokens": 1}}
        jev._post = _post

        async def fake_reply(*a, **k): return "Sure."
        speech.generate_reply = fake_reply
        brain_main._rule = rules.RuleBrain()
        brain_main._jev = jev.JevBrain(random.Random(0))
        brain_main._latest_snapshot = dict(VITALS)
        brain_main._listener = listening.Listener()
        brain_main._listener.peek_enabled = True
        brain_main._listen_task = brain_main._peek_task = brain_main._peek_req = None
        brain_main._typing_at = 0.0

    def tearDown(self):
        (brain_main.ws_server.send, speech.generate_reply, config.JEV_ENABLED,
         config.JEV_API_KEY, config.LISTEN, config.LISTEN_JEV, config.LISTEN_TICK_S,
         config.LISTEN_PAUSE_S, config.LISTEN_JEV_MIN_GAP_S, config.LISTEN_JEV_RESERVE,
         config.TTS_ENABLED, config.SUBTITLES, jev._post) = self._saved

    def _drain(self):
        for t in (brain_main._settle_task, brain_main._silent_task,
                  brain_main._listen_task, brain_main._peek_task):
            if t:
                t.cancel()

    def scenes(self):
        return [p["state"]["scene"] for p in self.posts]

    def test_the_final_line_reuses_the_peek_and_skips_the_react_call(self):
        async def flow():
            brain_main._loop = asyncio.get_running_loop()
            brain_main._listen_feed("hey can you look at this", "typing")
            await asyncio.sleep(0.4)                    # a pause: the peek is asked and shown
            shown_before_send = [m for m in self.sent if m["type"] == "pose"
                                 and m.get("eyes") == "bottomlid"]
            await brain_main._on_user_text({"type": "user_text",
                                            "text": "hey can you look at this"})
            await asyncio.sleep(0.05)
            self._drain()
            return shown_before_send
        shown = run(flow())
        self.assertEqual(self.scenes(), [jev.PEEK_SCENE])          # ONE call, and not a react
        self.assertTrue(shown, "her listening reaction was on screen before the send")
        self.assertEqual(brain_main._listener.stats.reused, 1)     # stats outlive the per-line reset
        self.assertFalse(brain_main._listener.active)
        self.assertIn("say", [m["type"] for m in self.sent])

    def test_a_different_final_line_is_asked_fresh(self):
        async def flow():
            brain_main._loop = asyncio.get_running_loop()
            brain_main._listen_feed("hey can you look at this", "typing")
            await asyncio.sleep(0.4)
            await brain_main._on_user_text({"type": "user_text",
                                            "text": "never mind, tell me about toasters instead"})
            await asyncio.sleep(0.05)
            self._drain()
        run(flow())
        self.assertEqual(self.scenes(), [jev.PEEK_SCENE, jev.REACT_SCENE])

    def test_an_in_flight_peek_is_awaited_not_paid_for_twice(self):
        self.delay = 0.3
        async def flow():
            brain_main._loop = asyncio.get_running_loop()
            brain_main._listen_feed("hey can you look at this", "typing")
            await asyncio.sleep(0.15)                   # peek asked, still in flight
            self.assertFalse(brain_main._peek_task.done())
            await brain_main._on_user_text({"type": "user_text",
                                            "text": "hey can you look at this"})
            stats = brain_main._listener.stats
            await asyncio.sleep(0.05)
            self._drain()
            return stats.reused_inflight
        self.assertEqual(run(flow()), 1)
        self.assertEqual(self.scenes(), [jev.PEEK_SCENE])

    def test_local_reflexes_need_no_jev_and_no_credits(self):
        brain_main._listener.peek_enabled = False
        async def flow():
            brain_main._loop = asyncio.get_running_loop()
            brain_main._listen_feed("you suck", "typing")
            await asyncio.sleep(0.1)
            self._drain()
        run(flow())
        self.assertEqual(self.posts, [])
        eyes = [m.get("eyes") for m in self.sent if m["type"] == "pose"]
        self.assertEqual(eyes[0], "default")                        # onset
        self.assertIn("anger", eyes)                                # the cue

    def test_typing_text_routes_to_the_listener(self):
        async def flow():
            brain_main._loop = asyncio.get_running_loop()
            brain_main._on_typing({"type": "user_typing", "text": "you suck"})
            await asyncio.sleep(0.05)
            self._drain()
        run(flow())
        self.assertTrue(brain_main._listener.active)
        self.assertEqual(brain_main._listener.text, "you suck")

    def test_a_bare_ping_keeps_the_old_thinking_look(self):
        async def flow():
            brain_main._loop = asyncio.get_running_loop()
            brain_main._on_typing({"type": "user_typing"})
            await asyncio.sleep(0.05)
        run(flow())
        self.assertFalse(brain_main._listener.active)
        self.assertEqual(self.sent[0]["look"], [-0.4, -0.7])

    def test_listen_off_falls_back_to_the_ping_even_with_text(self):
        config.LISTEN = False
        async def flow():
            brain_main._loop = asyncio.get_running_loop()
            brain_main._on_typing({"type": "user_typing", "text": "you suck"})
            await asyncio.sleep(0.05)
        run(flow())
        self.assertFalse(brain_main._listener.active)
        self.assertEqual(self.sent[0]["look"], [-0.4, -0.7])

    def test_clearing_the_bar_stops_her_listening_and_settles(self):
        async def flow():
            brain_main._loop = asyncio.get_running_loop()
            brain_main._listen_feed("hey can you look", "typing")
            await asyncio.sleep(0.05)
            brain_main._listen_feed("", "typing")
            await asyncio.sleep(0.05)
            self._drain()
        run(flow())
        self.assertFalse(brain_main._listener.active)
        self.assertEqual(self.sent[-1], brain_main._rule.baseline_message())

    def test_she_does_not_stomp_her_own_speech(self):
        async def flow():
            brain_main._loop = asyncio.get_running_loop()
            brain_main._rule.pause_for_speech(5.0)
            brain_main._listen_feed("you suck", "typing")
            await asyncio.sleep(0.1)
            self._drain()
        run(flow())
        self.assertEqual([m for m in self.sent if m["type"] == "pose"], [])
        self.assertEqual(self.posts, [])

    def test_the_loop_keeps_her_silent_life_held_through_a_long_pause(self):
        async def flow():
            brain_main._loop = asyncio.get_running_loop()
            brain_main._listen_feed("hey can you look", "typing")
            brain_main._rule.listen_gate_until = 0.0     # as if the 2 s hold had lapsed
            await asyncio.sleep(0.15)                    # the loop ticks every 0.02 s
            held = brain_main._rule.listen_gate_until > time.monotonic()
            self._drain()
            return held
        self.assertTrue(run(flow()))

    def test_a_slash_command_ends_listening_cleanly(self):
        async def flow():
            brain_main._loop = asyncio.get_running_loop()
            brain_main._listen_feed("/listen", "typing")
            await asyncio.sleep(0.05)
            await brain_main._on_user_text({"type": "user_text", "text": "/listen"})
            self._drain()
        run(flow())
        self.assertFalse(brain_main._listener.active)
        self.assertTrue(any(m["type"] == "notice" and "listening" in m["text"]
                            for m in self.sent))


if __name__ == "__main__":
    unittest.main()
