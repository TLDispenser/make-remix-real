# Self-improvement (mechanic) tests: symptom scan offsets, the itch, the
# gates (limits she must respect), the brief (functional, diary excerpt
# capped, redacted), the dispatch prompt's hard lines, and state persistence.
# Hermetic: temp sandbox/logs, no opencode spawned.
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from brain import config, home, mechanic


def _sandbox(self):
    d = tempfile.mkdtemp(prefix="remix-mechanic-")
    saved = config.SANDBOX_DIR
    config.SANDBOX_DIR = Path(d)
    self.addCleanup(setattr, config, "SANDBOX_DIR", saved)
    return Path(d)


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.dir = _sandbox(self)

    def test_patterns_score_and_position_advances(self):
        log = self.dir / "brain.log"
        log.write_text("remix-brain: LLM zai cooling down (600s)\n"
                       "hello\n"
                       "Traceback (most recent call last)\n")
        dec = self.dir / "decisions.log"
        dec.write_text("12:00:00 judge_unseen ... DISAGREE\n")
        st = {}
        score, findings = mechanic.scan(log, dec, st)
        self.assertGreaterEqual(score, 2 + 3 + 2)
        self.assertEqual(len(findings), 3)
        self.assertEqual(st["brain_pos"], log.stat().st_size)
        self.assertEqual(st["decisions_pos"], dec.stat().st_size)
        # nothing new: no new pain counted, no re-read
        self.assertEqual(mechanic.scan(log, dec, st), (0, []))

    def test_a_storm_is_one_symptom_not_four_hundred(self):
        log = self.dir / "brain.log"
        log.write_text("".join("HTTP 429 overloading, cooling down\n"
                               for _ in range(200)))
        score, findings = mechanic.scan(log, self.dir / "decisions.log", {})
        self.assertLessEqual(score, 2 * mechanic._NEEDLE_CAP)
        self.assertLessEqual(len(findings), mechanic._NEEDLE_CAP)

    def test_first_scan_starts_at_the_tail_not_the_whole_log(self):
        log = self.dir / "brain.log"
        old = "Traceback\n" * 50_000        # history a fresh watch must ignore
        log.write_text(old + "fresh Traceback\n")
        score, findings = mechanic.scan(log, self.dir / "decisions.log", {})
        # bounded read: the tail's symptom counted a few times, not 50k
        self.assertEqual(score, 3 * mechanic._NEEDLE_CAP)
        self.assertTrue(findings)

    def test_rotation_shrinks_gracefully(self):
        log = self.dir / "brain.log"
        log.write_text("Traceback old\n")
        st = {}
        dec = self.dir / "decisions.log"
        mechanic.scan(log, dec, st)
        log.write_text("Traceback\n")                 # truncated/rotated: shrunk
        score, findings = mechanic.scan(log, dec, st)
        self.assertGreaterEqual(score, 3)
        self.assertTrue(any("Traceback" in f for f in findings))

    def test_missing_logs_are_quiet(self):
        self.assertEqual(mechanic.scan(self.dir / "nope",
                                       self.dir / "also-nope", {}), (0, []))


class ItchAndGatesTests(unittest.TestCase):
    def test_itch_grows_and_caps(self):
        now = 10_000_000.0
        self.assertEqual(mechanic.itch({}, now), 1)        # standing itch
        st = {"last_call": now - 2 * 86400}
        self.assertEqual(mechanic.itch(st, now), 0)
        st = {"last_call": now - 9 * 86400}
        self.assertEqual(mechanic.itch(st, now), 3)
        st = {"last_call": now - 300 * 86400}
        self.assertEqual(mechanic.itch(st, now), 4)        # cap

    def test_gates(self):
        now = 10_000_000.0
        self.assertIsNone(mechanic.gates({}, now))
        self.assertEqual(mechanic.gates({}, now, already_running=True),
                         "a visit is already running")
        self.assertEqual(mechanic.gates({}, now, busy=True),
                         "a conversation is live (the mechanic shares her pool)")
        self.assertEqual(mechanic.gates({}, now, quiet=True), "quiet hours")
        st = {"last_call": now - config.MECHANIC_REST_S + 100}
        self.assertEqual(mechanic.gates(st, now), "resting after the last call")
        st = {"last_call": now - config.MECHANIC_REST_S - 100}
        self.assertIsNone(mechanic.gates(st, now))


class BriefTests(unittest.TestCase):
    def setUp(self):
        self.dir = _sandbox(self)

    def test_brief_carries_score_findings_and_capped_diary(self):
        diary = self.dir / "diary"
        diary.mkdir()
        (diary / "2026-11-14.md").write_text("# day\ntoday the words kept failing\n")
        with mock.patch.object(config, "MECHANIC_DIARY_CHARS", 20):
            text = mechanic.brief(9, 2, ["Traceback line here"])
        self.assertIn("symptom score 9", text)
        self.assertIn("Traceback line here", text)
        self.assertIn("her own recent note", text)
        self.assertIn("kept failing", text)          # the tail survives the cap
        self.assertNotIn("today the words", text)    # capped from the head

    def test_no_diary_is_fine(self):
        self.assertIn("(none recorded)", mechanic.brief(1, 0, []))

    def test_save_brief_redacts_and_names_itself(self):
        p = home.save_brief("symptoms / more")
        self.assertIsNotNone(p)
        self.assertTrue(p.name.endswith("-mechanic-brief.txt"))
        self.assertIn("symptoms", p.read_text())


class StateTests(unittest.TestCase):
    def test_mark_called_roundtrip(self):
        _sandbox(self)
        st = mechanic.mark_called({"brain_pos": 5}, now=123.0)
        self.assertEqual(st["last_call"], 123.0)
        self.assertEqual(mechanic.load_state()["last_call"], 123.0)
        # a corrupt state file is just "never called"
        (_sandbox(self) / "mechanic.json").write_text("{bogus")
        self.assertEqual(mechanic.load_state(), {})


class DispatchContractTests(unittest.TestCase):
    def test_prompt_hard_lines(self):
        p = mechanic._PROMPT
        self.assertIn("SYS_PROMPT", p)               # persona text untouchable
        self.assertIn("~/.local/share/remix/", p)    # her memory untouchable
        self.assertIn("Never add dependencies", p)
        self.assertIn("unittest discover", p)        # tests must pass
        self.assertIn("Commit", p)                   # branch delivery
        self.assertIn("never push", p)

    def test_dispatch_stays_home_without_opencode(self):
        import asyncio
        _sandbox(self)
        with mock.patch.object(mechanic, "_opencode", return_value=None):
            got = asyncio.run(mechanic.dispatch(
                Path("/tmp/b.txt"), Path("/tmp"), Path("/tmp/out.log")))
        self.assertIsNone(got)

    def test_spawn_wires_the_worktree_and_brief(self):
        _sandbox(self)
        import asyncio
        spawned = {}

        async def fake_exec(*args, **kwargs):
            spawned["args"] = args
            spawned["kwargs"] = kwargs
            return "PROC"

        async def go():
            return await mechanic.dispatch(Path("/tmp/b.txt"), Path("/repo"),
                                           Path("/tmp/o.log"))

        with mock.patch.object(mechanic, "_opencode",
                               return_value="/usr/bin/opencode"), \
                mock.patch.object(mechanic, "asyncio_create_subprocess",
                                  fake_exec):
            out = asyncio.run(go())
        self.assertIsNotNone(out)
        args = spawned["args"]
        self.assertEqual(args[1:3], ("run", "--auto"))
        self.assertIn("--worktree", args)
        self.assertIn("--file", args)
        self.assertEqual(spawned["kwargs"]["cwd"], "/repo")


if __name__ == "__main__":
    unittest.main()
