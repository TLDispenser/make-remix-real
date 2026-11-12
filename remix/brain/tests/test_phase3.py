# Phase 3 (cooperation and memory core) offline tests: FTS5 retrieval and the
# temporal window, the honesty guard, commitments (record -> due -> fire
# once), the react riders (intent / commitment / dodge), and the longer
# history window. All offline: a temp sandbox + temp memory.db replace the
# real ones, so neither the journal nor the FTS index is ever touched.
# Run from remix/:
#   .venv/bin/python -m unittest discover -s brain/tests -t . -v
import asyncio
import json
import os
import random
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("REMIX_TTS_DIR", tempfile.mkdtemp(prefix="remix-test-voice-"))

from brain import config, decisions, grounding, jev, memory, proactive, \
    speech  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class Phase3Base(unittest.TestCase):
    """Journaling IS active for these tests: a temp sandbox + temp SQLite
    file replace the real ones, and load() runs like a real boot would."""

    def setUp(self):
        d = tempfile.mkdtemp()
        self._saved = (config.SANDBOX_DIR, config.MEMORY_JSON, config.MEMORY_DB)
        config.SANDBOX_DIR = d
        config.MEMORY_JSON = os.path.join(d, "memory.json")
        config.MEMORY_DB = os.path.join(d, "memory.db")
        memory._store = None
        memory._active = False
        memory._db = None
        memory._remember_enabled = True
        memory.load()

    def tearDown(self):
        (config.SANDBOX_DIR, config.MEMORY_JSON, config.MEMORY_DB) = self._saved
        memory._store = None
        memory._active = False
        memory._dirty = False
        memory._db = None
        memory._remember_enabled = True
        grounding.set_retrieved([])


# ---------------------------------------------------------------- retrieval

class RetrievalTests(Phase3Base):
    def test_fts_finds_a_row_by_content(self):
        memory.record("user", "we picked the rust-orange color for the site")
        memory.record("her", "fine. orange it is. hope you like rust.")
        hits = memory.search_fts("rust orange site", limit=5)
        self.assertTrue(hits)
        self.assertIn("rust-orange", hits[0]["text"])

    def test_fts_garbage_query_is_empty_not_an_error(self):
        memory.record("thought", "plain words only")
        self.assertEqual(memory.search_fts("xyzzyqq blorptastic", limit=5), [])

    def test_retrieve_combines_temporal_and_content_without_dupes(self):
        when = time.time() - 3600.0
        memory.record("user", "the renderer crashed twice today", when=when)
        memory.record("thought", "renderer keeps them humble", when=when)
        hits = memory.retrieve("what happened with the renderer earlier?")
        texts = [h["text"] for h in hits]
        self.assertEqual(len(texts), len(set(texts)))      # dedup
        self.assertTrue(any("renderer" in t for t in texts))

    def test_temporal_window_parses_the_common_phrases(self):
        lt = time.localtime()
        midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0,
                                0, 0, -1))
        w = memory._temporal_window("what did we do yesterday?")
        self.assertAlmostEqual(w[1], midnight)
        self.assertAlmostEqual(w[0], midnight - 86400)
        w = memory._temporal_window("remember three days ago?")
        self.assertAlmostEqual(w[0], midnight - 3 * 86400)
        w = memory._temporal_window("this morning was chaos")
        self.assertAlmostEqual(w[0], midnight + 5 * 3600)
        self.assertIsNone(memory._temporal_window("what is a renderer?"))

    def test_search_temporal_finds_rows_in_the_window(self):
        lt = time.localtime()
        midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0,
                                0, 0, -1))
        yesterday_noon = midnight - 86400 + 12 * 3600
        memory.record("user", "a row that lives in yesterday",
                      when=yesterday_noon)
        hits = memory.search_temporal(midnight - 86400, midnight)
        self.assertTrue(any("yesterday" in h["text"] for h in hits))

    def test_forget_removes_from_store_db_and_said(self):
        memory.record("user", "the passphrase is crimson ferret")
        memory.record("her", "crimson ferret, noted.")
        memory.note_said("crimson ferret, noted.")
        memory.flush()
        n = memory.forget("crimson ferret")
        self.assertGreaterEqual(n, 2)
        texts = [r["text"] for r in memory.lines(limit=100)]
        for t in texts:
            self.assertNotIn("crimson", t.lower())
        self.assertNotIn("crimson ferret, noted.", memory.said_set())
        self.assertEqual(memory.search_fts("crimson ferret", limit=5), [])

    def test_record_can_tag_the_day_a_summary_describes(self):
        # a summary row's own date is when it was WRITTEN; `about` carries
        # the day it DESCRIBES, so boot backfill can tell done from not-yet
        memory.record("summary", "that day was chaos", about="2026-11-08")
        row = memory.lines(limit=1)[0]
        self.assertEqual("2026-11-08", row.get("about"))
        self.assertEqual(time.strftime("%Y-%m-%d"), str(row.get("at"))[:10])

    def test_remember_toggle_pauses_recording(self):
        memory.set_remember(False)
        memory.record("user", "off the record")
        memory.set_remember(True)
        self.assertEqual(memory.lines(limit=100), [])

    def test_backfill_populates_fts_from_memory_json(self):
        # simulate an old journal that predates the index: reload from json
        memory.record("user", "we picked the rust-orange color for the site")
        memory.flush()
        memory._db = None
        os.unlink(config.MEMORY_DB)          # as if the index never existed
        memory._store = None
        memory._active = False
        memory.load()                        # backfill runs on load
        hits = memory.search_fts("rust orange site", limit=5)
        self.assertTrue(hits)


# -------------------------------------------------------------- commitments

class CommitmentTests(Phase3Base):
    def test_add_then_due_then_done(self):
        memory.add_commitment("water the fern", "no_time_given",
                              due_time=time.time() - 60.0)
        due = memory.due_commitments()
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["text"], "water the fern")
        memory.mark_commitment_done(due[0]["id"])
        self.assertEqual(memory.due_commitments(), [])

    def test_not_yet_due_is_invisible(self):
        memory.add_commitment("later thing", "later_today")   # +3 h
        self.assertEqual(memory.due_commitments(), [])

    def test_when_tags_pick_sane_due_times(self):
        now = time.time()
        cid = memory.add_commitment("soon thing", "later_today")
        s = memory.store()
        item = next(c for c in s["commitments"] if c["id"] == cid)
        self.assertAlmostEqual(item["due_at"], now + 10800.0, delta=5.0)
        cid2 = memory.add_commitment("tomorrow thing", "tomorrow")
        item2 = next(c for c in s["commitments"] if c["id"] == cid2)
        lt = time.localtime(now)
        midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0,
                                0, 0, -1))
        self.assertAlmostEqual(item2["due_at"], midnight + 86400 + 36000,
                               delta=5.0)


class CommitmentTriggerTests(unittest.TestCase):
    """The due commitment rides Proactive.tick like any other trigger —
    behind the suppression gates, and it fires ONCE."""

    def _pro(self):
        p = proactive.Proactive(rng=random.Random(1))
        p.greeted = True          # skip the boot greeting for these tests
        return p

    def test_due_commitment_becomes_a_proactive_event(self):
        p = self._pro()
        c = {"id": "c1", "text": "water the fern", "when": "later_today",
             "due_at": time.time() - 10, "done": False}
        plan = p.tick({}, None, now=1000.0, commitments=[c])
        self.assertIsNotNone(plan)
        self.assertEqual(plan["trigger"], "commitment_due")
        self.assertEqual(plan["commitment"]["id"], "c1")
        self.assertIn("water the fern", plan["situation"])

    def test_fires_once_then_stays_quiet(self):
        p = self._pro()
        c = {"id": "c1", "text": "water the fern", "when": "later_today",
             "due_at": time.time() - 10, "done": False}
        self.assertIsNotNone(p.tick({}, None, now=1000.0, commitments=[c]))
        # slot spent: immediately after, nothing fires (min gap + cooldown)
        self.assertIsNone(p.tick({}, None, now=1001.0, commitments=[]))

    def test_suppression_gates_still_apply(self):
        p = self._pro()
        c = {"id": "c1", "text": "water the fern", "when": "later_today",
             "due_at": time.time() - 10, "done": False}
        self.assertIsNone(p.tick({}, None, now=1000.0, commitments=[c],
                                 mic_open=True))
        self.assertIsNone(p.tick({}, None, now=1000.0, commitments=[c],
                                 busy_until=2000.0))
        saved = config.QUIET_HOURS
        config.QUIET_HOURS = "1-8"
        try:
            self.assertIsNone(p.tick({}, None, now=1000.0, commitments=[c],
                                     wall_hour=4))        # quiet hours
        finally:
            config.QUIET_HOURS = saved
        self.assertIsNone(p.tick({}, None, now=1000.0, commitments=[c],
                                 last_exchange_at=995.0))  # just talked

    def test_battery_low_still_wins_over_commitment(self):
        p = self._pro()
        p.prev_batt = 30.0
        c = {"id": "c1", "text": "water the fern", "when": "later_today",
             "due_at": time.time() - 10, "done": False}
        plan = p.tick({"plugged": False}, {"battery": 12.0}, now=1000.0,
                      commitments=[c])
        self.assertEqual(plan["trigger"], "battery_low")


# ------------------------------------------------------------- react riders

class ReactRiderTests(unittest.TestCase):
    def _brain(self, resp):
        b = jev.JevBrain(rng=random.Random(3))
        b._call = mock.AsyncMock(return_value=resp)
        return b

    _GOOD = {"answers": {
        "stance": {"type": "choice", "choice": "flat",
                   "confidence": 0.5, "probabilities": {"flat": 0.5}},
        "eyes": {"type": "choice", "choice": "default",
                 "confidence": 0.5, "probabilities": {"default": 0.5}},
        "mouth": {"type": "choice", "choice": "default",
                  "confidence": 0.5, "probabilities": {"default": 0.5}},
        "user_intent": {"type": "choice", "choice": "question",
                        "confidence": 0.7, "probabilities": {"question": 0.7}},
        "commitment": {"type": "noul", "noul": 0.82},
        "commit_when": {"type": "choice", "choice": "tomorrow",
                        "confidence": 0.6, "probabilities": {"tomorrow": 0.6}},
    }, "usage": {"input_tokens": 100, "output_tokens": 5}}

    def test_react_parses_the_riders(self):
        b = self._brain(self._GOOD)
        with mock.patch.object(config, "JEV_ENABLED", True), \
             mock.patch.object(config, "JEV_MAX_PER_HOUR", 60), \
             mock.patch.object(config, "LISTEN_JEV_RESERVE", 20):
            d = run(b.react("remind me to water the fern tomorrow", {}))
        self.assertEqual(d.intent, "question")
        self.assertAlmostEqual(d.commitment_p, 0.82)
        self.assertEqual(d.commitment_when, "tomorrow")

    def test_garbled_riders_become_none(self):
        resp = json.loads(json.dumps(self._GOOD))
        resp["answers"]["user_intent"] = {"type": "choice", "choice": "yell"}
        resp["answers"]["commitment"] = {"type": "noul", "noul": "junk"}
        resp["answers"]["commit_when"] = {"type": "choice", "choice": "tuesday"}
        b = self._brain(resp)
        with mock.patch.object(config, "JEV_ENABLED", True), \
             mock.patch.object(config, "JEV_MAX_PER_HOUR", 60), \
             mock.patch.object(config, "LISTEN_JEV_RESERVE", 20):
            d = run(b.react("anything", {}))
        self.assertIsNone(d.intent)
        self.assertIsNone(d.commitment_p)
        self.assertIsNone(d.commitment_when)

    def test_peek_is_partial_no_riders(self):
        b = self._brain(self._GOOD)
        with mock.patch.object(config, "JEV_ENABLED", True), \
             mock.patch.object(config, "LISTEN_JEV", True), \
             mock.patch.object(config, "LISTEN_JEV_RESERVE", 20), \
             mock.patch.object(config, "LISTEN_JEV_MIN_GAP_S", 0.0), \
             mock.patch.object(config, "JEV_MAX_PER_HOUR", 60):
            d = run(b.peek("still typing this", {}))
        self.assertIsNone(d.intent)
        self.assertIsNone(d.commitment_p)

    def test_act_dodge_judge_lives_on_intent(self):
        resp = {"answers": {
            "eyes1": {"type": "choice", "choice": "default",
                      "confidence": 0.5, "probabilities": {"default": 0.5}},
            "mouth1": {"type": "choice", "choice": "default",
                       "confidence": 0.5, "probabilities": {"default": 0.5}},
            "dodged": {"type": "noul", "noul": 0.9},
        }, "usage": {"input_tokens": 100, "output_tokens": 5}}
        b = self._brain(resp)
        saved = {k: getattr(config, k) for k in
                 ("JEV_ENABLED", "JEV_MAX_PER_HOUR", "LISTEN_JEV_RESERVE")}
        config.JEV_ENABLED = True
        config.JEV_MAX_PER_HOUR = 60
        config.LISTEN_JEV_RESERVE = 20
        try:
            # one-beat reply with the dodge judge live on question intent:
            # the dodge noul rides along. With a chitchat intent the judge is
            # not asked, and a one-beat line with ALL judges off gets no
            # acting call at all.
            def mode_of(kind, _modes={"dodge_judge": "shadow",
                                      "judge_unseen": "off"}):
                return _modes.get(kind, "off")
            with mock.patch.object(decisions, "mode", side_effect=mode_of):
                plan = run(b.act("how do i fix it", ["one line only"],
                                 senses="nothing", intent="question"))
                self.assertIsNotNone(plan)
                self.assertAlmostEqual(plan.dodged, 0.9)
                b._call.reset_mock()
                plan2 = run(b.act("lol same", ["one line only"],
                                  senses="nothing", intent="chitchat"))
                self.assertIsNone(plan2)   # no judge, one beat -> no call
        finally:
            for k, v in saved.items():
                setattr(config, k, v)


# ------------------------------------------------------------ honesty guard

class HonestyGuardTests(Phase3Base):
    VITALS = {"cpu": 12, "ram": 40, "temp": 50, "battery": 80}

    def test_retrieved_rows_license_past_tense_claims(self):
        when = time.time() - 3 * 86400
        memory.record("user", "we renamed the project folder to vibes",
                      when=when)
        rows = memory.retrieve("what was the project folder called days ago?")
        self.assertTrue(rows)
        grounding.set_retrieved(rows)
        self.assertTrue(grounding._memory_ok(
            "three days ago", "the folder was named vibes"))
        grounding.set_retrieved([])
        # without the retrieval, the same claim has no license (journal row
        # predates the standard 'N days ago' half-span rule? three days, one
        # row -> half of 3 days = 1.5 days of age — the row IS that old, so
        # it passes on rows alone too; use a far-future claim to see the deny)
        self.assertFalse(grounding._memory_ok(
            "weeks ago", "the folder was named vibes"))

    def test_honesty_guard_still_needs_word_overlap(self):
        when = time.time() - 7200.0
        memory.record("thought", "the fern needs watering", when=when)
        rows = memory.retrieve("the fern?")
        grounding.set_retrieved(rows)
        self.assertFalse(grounding._memory_ok(
            "earlier", "the passphrase is crimson ferret"))


# ------------------------------------------------------------- speech layer

class SpeechLayerTests(Phase3Base):
    def test_history_window_matches_config(self):
        self.assertEqual(speech._history.maxlen,
                         max(8, config.HISTORY_MAX_MESSAGES))
        self.assertGreaterEqual(speech._history.maxlen, 20)

    def test_memory_block_merges_retrieved_rows(self):
        memory.record("user", "the project codename is glass lantern")
        prompt = speech.build_system_prompt({}, None, None, None,
                                            user_text="what's the codename?")
        self.assertIn("glass lantern", prompt)
        self.assertIn("they said", prompt)
        rows = grounding._retrieved_rows
        self.assertTrue(any("glass lantern" in r["text"] for r in rows))

    def test_memory_block_without_user_text_clears_retrieved(self):
        memory.record("user", "some line")
        speech.build_system_prompt({}, None, None, None, user_text="codename?")
        grounding.set_retrieved([])   # proactive chatter never sets them
        prompt = speech.build_system_prompt({}, None, None, None)
        self.assertIn("some line", prompt)
        self.assertEqual(grounding._retrieved_rows, [])

    def test_generate_undodged_swaps_history_and_journals(self):
        speech._history.clear()
        speech._history.append({"role": "user", "content": "how do i fix it"})
        speech._history.append({"role": "assistant",
                                "content": "not my problem"})
        calls = []

        async def fake_llm(messages):
            calls.append(messages)
            return "the build fails because you deleted the lockfile."

        orig = speech._call_llm
        speech._call_llm = fake_llm
        try:
            second = run(speech.generate_undodged(
                "how do i fix it", "not my problem"))
        finally:
            speech._call_llm = orig
        self.assertIn("lockfile", second)
        self.assertEqual(speech._history[-1]["content"], second)
        texts = [r["text"] for r in memory.lines(limit=10)]
        self.assertTrue(any("lockfile" in t for t in texts))
        self.assertEqual(len(calls), 1)
        speech._history.clear()

    def test_generate_undodged_pool_down_keeps_first_reply(self):
        speech._history.clear()
        speech._history.append({"role": "user", "content": "hi"})
        speech._history.append({"role": "assistant", "content": "nope"})

        async def fake_llm(messages):
            return None

        orig = speech._call_llm
        speech._call_llm = fake_llm
        try:
            out = run(speech.generate_undodged("hi", "nope"))
        finally:
            speech._call_llm = orig
        self.assertIsNone(out)
        self.assertEqual(speech._history[-1]["content"], "nope")
        speech._history.clear()
