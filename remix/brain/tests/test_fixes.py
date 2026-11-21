# test_fixes.py — regression tests for cleaned-up bugs and edge cases.
import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from brain import (  # noqa: E402
    awareness,
    config,
    decisions,
    faces,
    main as brain_main,
    memory,
    mood,
    proactive,
    rules,
    settings,
    stt,
    tts,
    vocal_fx,
)


def _run(coro):
    return asyncio.run(coro)


class MoodFixTests(unittest.TestCase):
    def setUp(self):
        mood.reset_for_test()

    def tearDown(self):
        mood.reset_for_test()

    def test_note_vitals_works_at_low_monotonic_uptime(self):
        with mock.patch.object(mood, "_gate", return_value=False), \
             mock.patch.object(mood.time, "monotonic", return_value=5.0):
            mood.note_vitals({"temp": 70.0})
        self.assertLess(mood._state["mood"], 0.0)


class SettingsAndConfigFixTests(unittest.TestCase):
    def test_env_val_formats_bools_as_one_or_zero(self):
        self.assertEqual("1", settings._env_val(True))
        self.assertEqual("0", settings._env_val(False))
        self.assertEqual("soft", settings._env_val("soft"))

    def test_load_dotenv_strips_export_quotes_and_inline_comments(self):
        keys = ("_TEST_EXPORT_A", "_TEST_QUOTED_B", "_TEST_COMMENT_C", "_TEST_HASH_IN_QUOTE")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "brain").mkdir()
            (root / ".env").write_text(
                'export _TEST_EXPORT_A="hello world"\n'
                "_TEST_QUOTED_B='single # kept'\n"
                "_TEST_COMMENT_C=unquoted   # inline comment\n"
                '_TEST_HASH_IN_QUOTE="http://example.com/#frag" # trailing\n',
                encoding="utf-8",
            )
            saved = {k: os.environ.pop(k, None) for k in keys}
            try:
                with mock.patch.object(config, "BRAIN_DIR", root / "brain"):
                    config._load_dotenv()
                self.assertEqual("hello world", os.environ.get("_TEST_EXPORT_A"))
                self.assertEqual("single # kept", os.environ.get("_TEST_QUOTED_B"))
                self.assertEqual("unquoted", os.environ.get("_TEST_COMMENT_C"))
                self.assertEqual("http://example.com/#frag",
                                 os.environ.get("_TEST_HASH_IN_QUOTE"))
            finally:
                for k, v in saved.items():
                    os.environ.pop(k, None)
                    if v is not None:
                        os.environ[k] = v

    def test_empty_xdg_runtime_dir_falls_back_cleanly(self):
        env = dict(os.environ)
        env["XDG_RUNTIME_DIR"] = ""
        for k in ("REMIX_PTT_SOCK", "CORE_PTT_SOCK", "REMIX_TTS_DIR", "CORE_TTS_DIR"):
            env.pop(k, None)
        remix_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        env["PYTHONPATH"] = remix_root
        out = subprocess.check_output(
            [sys.executable, "-c",
             "from brain import config; print(config.PTT_SOCK); print(config.TTS_DIR)"],
            env=env, text=True,
        ).strip().splitlines()
        self.assertEqual(["/tmp/remix-ptt.sock", "/tmp/remix-voice"], out)


class MemoryFixTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._dir = Path(self._tmp.name)
        self._saved = (
            config.SANDBOX_DIR, config.MEMORY_JSON, config.MEMORY_DB,
            memory._store, memory._dirty, memory._active, memory._db,
        )
        config.SANDBOX_DIR = self._dir
        config.MEMORY_JSON = self._dir / "memory.json"
        config.MEMORY_DB = self._dir / "memory.db"
        memory._store = None
        memory._dirty = False
        memory._active = False
        memory._db = None
        memory.load()

    def tearDown(self):
        if memory._db is not None:
            memory._db.close()
        (config.SANDBOX_DIR, config.MEMORY_JSON, config.MEMORY_DB,
         memory._store, memory._dirty, memory._active, memory._db) = self._saved
        self._tmp.cleanup()

    def test_record_caps_and_forget_wildcard_escaping(self):
        long_summary = "x" * 1500
        long_her = "y" * 1500
        memory.record("summary", long_summary)
        memory.record("her", long_her)
        rows = memory.lines(limit=10)
        self.assertEqual(1500, len(rows[0]["text"]) if rows[0]["kind"] == "summary" else len(rows[1]["text"]))
        her_row = next(r for r in rows if r["kind"] == "her")
        self.assertEqual(400, len(her_row["text"]))

        memory.record("user", "battery at 100% now")
        memory.record("user", "100 apples on shelf")
        memory.record("user", "alpha_beta config")
        memory.record("user", "alphaxbeta config")
        memory.add_commitment("finish foo_bar task", "later_today")
        memory.add_commitment("finish fooxbar task", "later_today")

        removed = memory.forget("100%")
        self.assertEqual(1, removed)
        texts = [r["text"] for r in memory.lines(limit=20)]
        self.assertIn("100 apples on shelf", texts)
        self.assertNotIn("battery at 100% now", texts)

        removed2 = memory.forget("foo_bar")
        self.assertEqual(1, removed2)
        con = memory._db_conn()
        rem_commits = [r[0] for r in con.execute("SELECT text FROM commitments").fetchall()]
        self.assertEqual(["finish fooxbar task"], rem_commits)


class VoiceAndSttFixTests(unittest.TestCase):
    def test_vocal_fx_imports_without_numpy(self):
        self.assertIsInstance(vocal_fx.HAS_NUMPY, bool)

    def test_markup_handles_uppercase_contractions(self):
        m = tts._markup("DON'T touch that!")
        self.assertIn('<emphasis level="strong">DON\'T</emphasis>', m)

    def test_ptt_watchdog_sets_last_before_on_timeout_and_dedups_stop(self):
        calls = []
        seen_last = []

        async def _scenario():
            ptt = stt.PushToTalk(
                on_timeout=lambda: (calls.append("timeout"), seen_last.append(ptt._last))
            )
            ptt._started_at = 0.0

            async def _fake_finalize():
                await asyncio.sleep(0.02)
                return stt.HoldResult("hello", "ok")

            with mock.patch.object(config, "PTT_MAX_PRESS_S", 0.01), \
                 mock.patch.object(ptt, "_finalize_locked", side_effect=_fake_finalize) as fl:
                wd = asyncio.create_task(ptt._watchdog())
                await asyncio.sleep(0.05)
                await wd
                self.assertEqual(1, fl.call_count)
                self.assertEqual(["timeout"], calls)
                self.assertEqual([stt.HoldResult("hello", "ok")], seen_last)
                # Calling stop after timeout must not re-run _finalize_locked
                res = await ptt.stop()
                self.assertEqual(stt.HoldResult("hello", "ok"), res)
                self.assertEqual(1, fl.call_count)

        _run(_scenario())


class ProactiveAndMainFixTests(unittest.TestCase):
    def test_proactive_dynamic_cooldown_and_force_daily_rollover(self):
        pro = proactive.Proactive()
        pro.day_key = "1999-01-01"
        pro.day_count = 7
        plan = pro.force()
        self.assertEqual("poke", plan["trigger"])
        self.assertEqual(1, pro.day_count)

        pro._last["on_my_mind"] = 100.0
        with mock.patch.object(config, "ON_MIND_COOLDOWN_S", 10.0), \
             mock.patch.object(config, "PROACTIVE_JITTER", 0.0):
            self.assertFalse(pro._due("on_my_mind", 105.0))
            self.assertTrue(pro._due("on_my_mind", 111.0))

    def test_maybe_escalate_logs_used_jev_when_live_vetoes(self):
        fake_jev = mock.AsyncMock()
        fake_jev.decide_proactive.return_value = {"speak_p": 0.1}
        logged = []

        async def _scenario():
            with mock.patch.object(brain_main, "_jev", fake_jev), \
                 mock.patch.object(config, "JEV_ENABLED", True), \
                 mock.patch.object(config, "JEV_SPEAK_P", 0.6), \
                 mock.patch.object(awareness, "out_of_sight", return_value=True), \
                 mock.patch.object(decisions, "mode", return_value="live"), \
                 mock.patch.object(decisions, "log", side_effect=lambda *a, **kw: logged.append(kw)):
                await brain_main._maybe_escalate("reply", "hi")

        _run(_scenario())
        self.assertEqual(1, len(logged))
        self.assertEqual("jev", logged[0]["used"])

    def test_on_user_text_dodge_retry_ships_second_reply(self):
        sent = []
        rule = rules.RuleBrain()
        fake_jev = mock.AsyncMock()
        fake_jev.react.return_value = SimpleNamespace(
            face=faces.Face("default", "default"),
            stance="flat",
            stance_name="flat",
            confidence=0.9,
            from_cache=False,
            intent="question",
            commitment_p=None,
            commitment_when=None,
        )
        fake_jev.act.return_value = SimpleNamespace(
            faces=[faces.Face("really", "default")],
            dodged=0.95,
            unseen=None,
        )
        fake_jev.note_face = mock.MagicMock()

        async def _fake_send(msg):
            sent.append(msg)

        async def _fake_undodged(*args, **kwargs):
            return "Actual straight answer."

        async def _scenario():
            with mock.patch.object(brain_main, "_rule", rule), \
                 mock.patch.object(brain_main, "_jev", fake_jev), \
                 mock.patch.object(brain_main.ws_server, "send", side_effect=_fake_send), \
                 mock.patch.object(brain_main.speech, "generate_reply_stream",
                                   return_value={"text": "Dodged fluff.", "first": None}), \
                 mock.patch.object(brain_main.speech, "generate_undodged",
                                   side_effect=_fake_undodged), \
                 mock.patch.object(tts, "available", return_value=False), \
                 mock.patch.object(tts, "synthesize", return_value=None), \
                 mock.patch.object(decisions, "mode", side_effect=lambda k: "live" if k == "dodge_judge" else "off"):
                await brain_main._on_user_text({"text": "what is my cpu?"}, echo=False)

        _run(_scenario())
        says = [m["text"] for m in sent if m.get("type") == "say"]
        self.assertEqual(["Actual straight answer."], says)
        if brain_main._silent_task and not brain_main._silent_task.done():
            brain_main._silent_task.cancel()

    def test_single_sentence_streamed_reply_final_say_and_no_spurious_caption(self):
        sent = []
        rule = rules.RuleBrain()
        fake_jev = mock.AsyncMock()
        fake_jev.react.return_value = None
        fake_jev.act.return_value = None
        fake_jev.note_face = mock.MagicMock()

        async def _fake_stream(*args, on_first=None, **kwargs):
            if on_first:
                await on_first("One sentence only.")
            return {"text": "One sentence only.", "first": "One sentence only."}

        async def _fake_send(msg):
            sent.append(msg)

        async def _scenario():
            with mock.patch.object(brain_main, "_rule", rule), \
                 mock.patch.object(brain_main, "_jev", fake_jev), \
                 mock.patch.object(brain_main.ws_server, "send", side_effect=_fake_send), \
                 mock.patch.object(brain_main.speech, "generate_reply_stream", side_effect=_fake_stream), \
                 mock.patch.object(tts, "available", return_value=True), \
                 mock.patch.object(tts, "synthesize",
                                   return_value=tts.Speech("/tmp/x.wav", [0.0], [0.5], 0.5)), \
                 mock.patch.object(config, "LLM_STREAM", True), \
                 mock.patch.object(config, "SUBTITLES", "auto"):
                await brain_main._on_user_text({"text": "hello"}, echo=False)

        _run(_scenario())
        says = [m for m in sent if m.get("type") == "say"]
        notices = [m for m in sent if m.get("type") == "notice"]
        self.assertEqual([{"type": "say", "text": "One sentence only.", "final": False},
                          {"type": "say", "text": "One sentence only.", "final": True}], says)
        self.assertEqual([], notices)


if __name__ == "__main__":
    unittest.main()
