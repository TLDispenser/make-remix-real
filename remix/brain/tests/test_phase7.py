# Phase 7 tests (plan 3.9/4.10): streamed replies (first-sentence hook,
# dirty-first screen, mid-stream partial, one-shot fallback), the
# temperature tint bands, and the settings TUI's .env merge semantics.
# Hermetic: no network, no real providers, temp .env.
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from brain import settings, speech


def run(c):
    return asyncio.run(c)


class StreamReplyTests(unittest.TestCase):
    def setUp(self):
        speech._history.clear()
        async def never_once(messages, temperature=0.7):
            raise AssertionError("one-shot pool call leaked into a "
                                 "streaming test")
        self._patches = [
            mock.patch.object(speech.memory, "record"),
            mock.patch.object(speech.memory, "note_said"),
            mock.patch.object(speech, "_call_llm", never_once),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        speech._history.clear()

    def test_clean_first_sentence_fires_on_first(self):
        got = []

        async def on_first(s):
            got.append(s)

        async def gen(messages, temperature=0.7):
            for c in ("You woke me. ", "Now go away, kindly."):
                yield c

        with mock.patch.object(speech, "_call_llm_stream", gen), \
                mock.patch.object(speech.grounding, "check",
                                  return_value=[]):
            r = run(speech.generate_reply_stream("hi", on_first=on_first))
        self.assertEqual(got, ["You woke me."])
        self.assertTrue(r["streamed"])
        self.assertEqual(r["first"], "You woke me.")
        self.assertEqual(r["text"], "You woke me. Now go away, kindly.")
        # the journal got the FINAL text, both sides
        speech.memory.record.assert_any_call("her", r["text"])
        self.assertEqual(speech._history[-1]["content"], r["text"])

    def test_dirty_first_sentence_never_speaks_early(self):
        got = []

        async def on_first(s):
            got.append(s)

        async def gen(messages, temperature=0.7):
            yield "I can see 47 processes. "
            yield "Anyway, enough of that."

        def fake_check(text, *a, **k):
            # the FIRST SENTENCE alone is dirty; the full text is clean
            return (["flagged"]
                    if text.strip() == "I can see 47 processes." else [])

        with mock.patch.object(speech, "_call_llm_stream", gen), \
                mock.patch.object(speech.grounding, "check",
                                  side_effect=fake_check):
            r = run(speech.generate_reply_stream("hi", on_first=on_first))
        self.assertEqual(got, [])                  # the hook never fired
        self.assertFalse(r["first"])
        self.assertTrue(r["streamed"])

    def test_mid_stream_failure_keeps_the_partial(self):
        got = []

        async def on_first(s):
            got.append(s)

        async def gen(messages, temperature=0.7):
            yield "Say this first. "
            raise RuntimeError("connection reset")

        with mock.patch.object(speech, "_call_llm_stream", gen), \
                mock.patch.object(speech.grounding, "check",
                                  return_value=[]):
            r = run(speech.generate_reply_stream("hi", on_first=on_first))
        self.assertEqual(r["text"], "Say this first.")
        self.assertEqual(got, ["Say this first."])

    def test_stream_disabled_falls_back_to_one_shot(self):
        got = []

        async def on_first(s):
            got.append(s)

        async def once(messages, temperature=0.7):
            return "One whole reply. The end."

        with mock.patch.object(speech.config, "LLM_STREAM", False), \
                mock.patch.object(speech, "_call_llm", once):
            r = run(speech.generate_reply_stream("hi", on_first=on_first))
        self.assertEqual(got, [])
        self.assertFalse(r["streamed"])
        self.assertIsNone(r["first"])
        self.assertEqual(r["text"], "One whole reply. The end.")

    def test_dead_pool_yields_none(self):
        async def gen(messages, temperature=0.7):
            return
            yield  # pragma: no cover — makes this an async generator

        async def once(messages, temperature=0.7):
            return None

        with mock.patch.object(speech, "_call_llm_stream", gen), \
                mock.patch.object(speech, "_call_llm", once):
            self.assertIsNone(run(speech.generate_reply_stream("hi")))

    def test_first_sentence_boundary_rules(self):
        self.assertIsNone(speech._first_sentence("still typing"))
        self.assertIsNone(speech._first_sentence("no."))
        self.assertEqual(speech._first_sentence("Yes. More"),
                         "Yes.")
        self.assertEqual(speech._first_sentence("One! Two?"),
                         "One!")


class TempTintTests(unittest.TestCase):
    def test_disabled_or_unknown_temp_never_sends(self):
        from brain import main as m
        with mock.patch.object(m.config, "TEMP_TINT", False):
            self.assertEqual((None, None), m._temp_tint(60, None))

    def test_bands_and_first_reading_sends_its_colour(self):
        from brain import main as m
        band, color = m._temp_tint(58, None)
        self.assertEqual(band, 1)
        self.assertEqual(color, "#ff5148")
        # no change inside the band
        self.assertEqual((1, None), m._temp_tint(70, 1))
        # cold
        self.assertEqual(0, m._temp_tint(40, None)[0])
        self.assertEqual("#5ab0ff", m._temp_tint(40, None)[1])
        # hot
        self.assertEqual("#ff3814", m._temp_tint(90, 2)[1])

    def test_hysteresis_at_the_edge(self):
        from brain import main as m
        # hovering just under 72 in the normal band: no flicker
        self.assertEqual((1, None), m._temp_tint(71.9, 1))
        # one full degree past the edge moves her
        band, color = m._temp_tint(73.5, 1)
        self.assertEqual(band, 2)
        self.assertEqual(color, "#ff7a29")
        # and back down needs a full degree the other way
        self.assertEqual((2, None), m._temp_tint(71.9, 2))
        band, color = m._temp_tint(70.5, 2)
        self.assertEqual(band, 1)


class SettingsMergeTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp(prefix="remix-settings-")
        self._path = Path(d) / ".env"
        self._saved = settings._ENV_PATH
        settings._ENV_PATH = self._path

    def tearDown(self):
        settings._ENV_PATH = self._saved

    def test_set_key_updates_the_first_definition_only(self):
        lines = ["# header", "USER_NAME=old", "PROACTIVE=1",
                 "USER_NAME=stale-duplicate"]
        out = settings.set_key(lines, "USER_NAME", "you")
        self.assertEqual(out[1], "USER_NAME=you")
        self.assertEqual(out[3], "USER_NAME=stale-duplicate")  # untouched
        self.assertEqual(settings.get_key(out, "USER_NAME"), "you")

    def test_set_key_appends_when_missing_and_preserves_the_rest(self):
        lines = ["PROACTIVE=1", "", "# tail comment"]
        out = settings.set_key(lines, "QUIET_HOURS", "1-8")
        self.assertEqual(out[0], "PROACTIVE=1")
        self.assertEqual(out[-1], "QUIET_HOURS=1-8")   # the value lands last
        self.assertIn("# tail comment", out)

    def test_roundtrip_through_a_real_file(self):
        self._path.write_text("PROACTIVE=1\n")
        settings.write_lines(settings.set_key(settings.load_lines(),
                                              "PROACTIVE", "0"))
        self.assertEqual(settings.get_key(settings.load_lines(),
                                          "PROACTIVE"), "0")

    def test_parse_kinds(self):
        self.assertTrue(settings._parse("bool", "ON"))
        self.assertFalse(settings._parse("bool", "0"))
        self.assertEqual(settings._parse(("choice", "a", "b"), "B"), "b")
        with self.assertRaises(ValueError):
            settings._parse(("choice", "a", "b"), "c")
        self.assertEqual(settings._parse("float", "2"), 2.0)


if __name__ == "__main__":
    unittest.main()
