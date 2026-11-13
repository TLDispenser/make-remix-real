# Phase 6 tests (plan Part 4 "Senses"): the widened senses (top processes,
# swap, network/disk I/O, body truths), the grounding whitelist that must
# MATCH them, and the shell hook (parse + trigger). Hermetic: no psutil
# probes, no network, no real shell.
import random
import unittest

from brain import grounding, proactive, speech


def snap(**over):
    """A realistic Phase-6 vitals snapshot shape."""
    base = {"cpu": 12.0, "ram": 61.0, "temp": 53.0, "battery": 80.0,
            "disk": 40.0, "swap": 3.0, "net": 45.0, "diskio": 120.0,
            "top": [{"name": "firefox", "cpu": 38.0},
                    {"name": "make", "cpu": 9.0}],
            "body": {"wear": 12.0, "cycles": 34, "smart": "ok",
                     "errors": 7}}
    base.update(over)
    return base


class VitalsStrTests(unittest.TestCase):
    def test_new_senses_reach_her_vitals_line(self):
        s = speech._vitals_str(snap())
        for frag in ("swap 3%", "network 45 KB/s", "disk io 120 KB/s",
                     "busiest right now: firefox", "battery wear 12%",
                     "34 charge cycles", "drive health ok",
                     "7 kernel errors since boot"):
            self.assertIn(frag, s)

    def test_old_shape_still_reads(self):
        self.assertIn("cpu", speech._vitals_str({"cpu": 5.0}))
        self.assertEqual("unavailable right now",
                         speech._vitals_str({"top": [], "body": None}))


class GroundingSensesTests(unittest.TestCase):
    def _check(self, reply, vitals, user="", aware=None):
        return grounding.check(reply, user, "", vitals, aware)

    def test_process_words_pass_when_a_top_list_exists(self):
        self.assertEqual([], self._check("your processes sound loud", snap()))
        self.assertEqual([], self._check("firefox is the loud one", snap()))
        # and the OLD denial is gone when she can actually sense them
        self.assertEqual([], self._check("what's eating the cpu?", snap(),
                                         user="what's eating the cpu"))

    def test_process_words_still_flag_without_a_top_list(self):
        v = snap(top=[], net=None, body=None, swap=None)
        problems = self._check("i can see your processes", v)
        self.assertTrue(any("process" in p for p in problems))

    def test_download_passes_only_with_a_network_rate(self):
        self.assertEqual([], self._check("your downloads are heavy", snap()))
        problems = self._check("your downloads are heavy", snap(net=None))
        self.assertTrue(any("download" in p for p in problems))

    def test_swap_and_wear_claims_validate(self):
        self.assertEqual([], self._check("swap is at 3% and the battery "
                                         "is 12% worn", snap()))
        problems = self._check("swap is at 90%", snap())
        self.assertTrue(any("real reading" in p for p in problems))
        problems = self._check("the battery is 60% worn", snap())
        self.assertTrue(any("real reading" in p for p in problems))

    def test_process_cpu_claim_validates_against_the_top_list(self):
        # firefox's own share (38%) is fair even though the machine total
        # is 12% — the process name in context unlocks its reading.
        self.assertEqual([], self._check("firefox is at 38% cpu", snap()))
        problems = self._check("firefox is at 90% cpu", snap())
        self.assertTrue(any("real reading" in p for p in problems))


class GroundingFactsTextTests(unittest.TestCase):
    def test_facts_state_the_new_senses_and_drop_the_old_denial(self):
        text = grounding.facts("cpu 12%", "a laptop", {}, None, snap())
        self.assertIn("busiest processes right now", text)
        self.assertIn("Body truths", text)
        self.assertIn("KB/s", text)
        # the denial must not claim she can't see processes she CAN see
        self.assertNotIn("processes, files", text)
        self.assertIn("files", text)          # she still can't browse files

    def test_facts_keep_the_denial_when_there_is_no_data(self):
        text = grounding.facts("cpu 12%", "a laptop", {}, None,
                               snap(top=[], net=None, swap=None, body=None))
        self.assertIn("busiest processes right now" in text, [False])
        self.assertIn("processes", text)      # denial intact


class ShellEdgeTests(unittest.TestCase):
    def test_parse_ok_and_failed(self):
        e = main_edge("cargo build all\t0\t42")
        self.assertEqual(e["trigger"], "shell_job")
        self.assertIn("`cargo build all`", e["fact"])
        self.assertIn("42s", e["fact"])
        self.assertIn("finished fine", e["fact"])
        e = main_edge("make\t1\t9")
        self.assertIn("FAILED", e["fact"])

    def test_short_jobs_and_garbage_are_not_news(self):
        self.assertIsNone(main_edge("ls\t0\t1"))
        self.assertIsNone(main_edge("ls"))
        self.assertIsNone(main_edge("ls\tzero\t9"))

    def test_command_is_redacted_and_capped_before_any_cloud(self):
        e = main_edge("curl https://secret.example/api?token=abc\t1\t30")
        self.assertNotIn("secret.example", e["fact"])
        self.assertNotIn("token", e["fact"])
        long = main_edge("x" * 400 + "\t0\t30")
        self.assertLessEqual(len(long["fact"]), 200)


def main_edge(line):
    """The parse lives in brain.main; import lazily so the module's other
    boot-time side effects don't run for every test class."""
    from brain import main
    return main._shell_edge(line)


class ShellTriggerTests(unittest.TestCase):
    def _pro(self):
        p = proactive.Proactive(rng=random.Random(7), enabled=True,
                                awaken_s=None)
        p.greeted = True
        return p

    def test_shell_edge_becomes_a_plan_with_its_own_fact(self):
        p = self._pro()
        plan = p.tick({}, shell_edges=[
            {"trigger": "shell_job", "fact": "the build just FAILED"}],
            now=1000.0, wall_hour=12)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["trigger"], "shell_job")
        self.assertEqual(plan["situation"], "the build just FAILED")

    def test_shell_respects_its_cooldown(self):
        p = self._pro()
        edge = [{"trigger": "shell_job", "fact": "a job finished"}]
        p.tick({}, shell_edges=edge, now=1000.0, wall_hour=12)
        p.last_line_at = -1e12            # pretend MIN_GAP passed
        plan = p.tick({}, shell_edges=edge, now=1150.0, wall_hour=12)
        self.assertTrue(plan is None or plan["trigger"] != "shell_job")

    def test_gates_eat_shell_edges_too(self):
        p = self._pro()
        plan = p.tick({"fullscreen": True}, shell_edges=[
            {"trigger": "shell_job", "fact": "a job finished"}],
            now=1000.0, wall_hour=12)
        self.assertIsNone(plan)


if __name__ == "__main__":
    unittest.main()
