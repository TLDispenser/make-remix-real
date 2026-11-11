# Phase 1 (safety & stability) offline tests: provider-failover timing and
# the right-brain queue. Run from remix/:
#   .venv/bin/python -m unittest discover -s brain/tests -t . -v
import asyncio
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("REMIX_TTS_DIR", tempfile.mkdtemp(prefix="remix-test-voice-"))

from brain import config, speech  # noqa: E402


def run(coro):
    return asyncio.run(coro)


FAKE_A = {"name": "fake-a", "api_key": "k", "base_url": "http://127.0.0.1:1",
          "model": "m"}
FAKE_B = {"name": "fake-b", "api_key": "k", "base_url": "http://127.0.0.1:2",
          "model": "m"}


# ------------------------------------------------------------ failover timing

class FailoverTimingTests(unittest.TestCase):
    """_call_llm: backoff sleeps INSIDE a provider's attempt run, but NEVER
    between its last attempt and the next provider (or the return)."""

    def setUp(self):
        self._saved = (config.PROVIDERS, speech._current,
                       dict(speech._cool_until), dict(speech._failures))
        config.PROVIDERS = []
        speech._current = 0
        speech._cool_until.clear()
        speech._failures.clear()

    def tearDown(self):
        (config.PROVIDERS, speech._current, speech._cool_until,
         speech._failures) = self._saved

    def test_no_sleep_after_the_final_attempt(self):
        # one provider, every attempt fails: exactly ATTEMPTS-1 sleeps
        config.PROVIDERS = [FAKE_A]
        sleeps = []
        real_sleep = asyncio.sleep

        async def fake_sleep(t):
            sleeps.append(t)
            await real_sleep(0)

        with mock.patch.object(speech.asyncio, "sleep", fake_sleep), \
                mock.patch.object(speech, "_llm_once_sync",
                                  side_effect=RuntimeError("down")):
            self.assertIsNone(run(speech._call_llm(
                [{"role": "user", "content": "hi"}])))
        self.assertEqual(speech._PER_PROVIDER_ATTEMPTS - 1, len(sleeps))

    def test_no_sleep_crossing_between_providers(self):
        # A fails twice, B answers first try: the only sleep is INSIDE A's
        # run; handing to B must be immediate.
        config.PROVIDERS = [FAKE_A, FAKE_B]
        sleeps = []
        real_sleep = asyncio.sleep

        async def fake_sleep(t):
            sleeps.append(t)
            await real_sleep(0)

        def fake_once(provider, messages, temperature=0.7):
            if provider["name"] == "fake-a":
                raise RuntimeError("down")
            return "hello"

        with mock.patch.object(speech.asyncio, "sleep", fake_sleep), \
                mock.patch.object(speech, "_llm_once_sync", fake_once):
            reply = run(speech._call_llm(
                [{"role": "user", "content": "hi"}]))
        self.assertEqual("hello", reply)
        self.assertEqual(1, len(sleeps))

    def test_failure_marks_then_cooldown_skips(self):
        config.PROVIDERS = [FAKE_A]
        with mock.patch.object(speech, "_llm_once_sync",
                               side_effect=RuntimeError("down")):
            self.assertIsNone(run(speech._call_llm(
                [{"role": "user", "content": "hi"}])))
        self.assertEqual(speech._PER_PROVIDER_ATTEMPTS,
                         speech._failures["fake-a"])
        self.assertGreater(speech._cool_until["fake-a"], 0.0)


# ---------------------------------------------------------------- reply queue

class ReplyQueueTests(unittest.TestCase):
    """generate_reply never drops a question as 'busy': questions queue on
    the net lock and are answered in arrival order. Chatter yields instead
    of queueing."""

    def setUp(self):
        speech._history.clear()
        speech._recent.clear()
        speech._reply_waiters = 0
        speech._cool_until.clear()
        speech._failures.clear()

    def tearDown(self):
        self.setUp()

    def _llm_by_question(self, answers):
        async def fake_llm(messages, temperature=0.7):
            q = messages[-1]["content"]
            return answers[q]
        return fake_llm

    def test_second_question_waits_and_is_answered_in_order(self):
        async def scenario():
            with mock.patch.object(speech, "_call_llm", self._llm_by_question(
                    {"q1": "first answer", "q2": "second answer"})):
                t1 = asyncio.create_task(speech.generate_reply("q1"))
                await asyncio.sleep(0)          # t1 takes the lock
                t2 = asyncio.create_task(speech.generate_reply("q2"))
                r1 = await t1
                r2 = await t2
                return r1, r2
        self.assertEqual(("first answer", "second answer"), run(scenario()))

    def test_busy_lock_no_longer_drops_the_question(self):
        # OLD behavior: lock held -> None at once ("brain busy"). NEW: waits.
        async def scenario():
            with mock.patch.object(speech, "_call_llm",
                                   self._llm_by_question({"q": "late"})):
                async with speech._lock:
                    t = asyncio.create_task(speech.generate_reply("q"))
                    await asyncio.sleep(0)
                    self.assertFalse(t.done())   # waiting, not dropped
                return await t
        self.assertEqual("late", run(scenario()))

    def test_none_only_when_the_pool_is_down(self):
        async def fake_llm(messages, temperature=0.7):
            return None
        with mock.patch.object(speech, "_call_llm", fake_llm):
            self.assertIsNone(run(speech.generate_reply("q")))

    def test_chatter_yields_when_a_reply_is_waiting(self):
        speech._reply_waiters += 1
        try:
            async def fake_llm(messages, temperature=0.7):
                raise AssertionError("chatter must not call the pool")
            with mock.patch.object(speech, "_call_llm", fake_llm):
                line = run(speech.generate_proactive_line(
                    {"trigger": "ambient", "stance": "flat", "face": None,
                     "look": None, "situation": "the keyboard ticked"}))
            self.assertIsNone(line)
        finally:
            speech._reply_waiters -= 1

    def test_chatter_skips_its_retry_when_a_reply_shows_up(self):
        calls = []

        async def fake_llm(messages, temperature=0.85):
            calls.append(1)
            if len(calls) == 1:
                speech._reply_waiters += 1   # a reply arrives while she writes
                return "forty tabs open in there"    # ungrounded -> note set
            return "clean line"

        try:
            with mock.patch.object(speech, "_call_llm", fake_llm):
                line = run(speech.generate_proactive_line(
                    {"trigger": "ambient", "stance": "flat", "face": None,
                     "look": None, "situation": "the keyboard ticked"}))
            self.assertIsNone(line)          # retry skipped: silence, and
            self.assertEqual(1, len(calls))  # the pool was not touched again
        finally:
            speech._reply_waiters -= 1
