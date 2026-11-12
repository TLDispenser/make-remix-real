# Phase 4 (plan 2.3) offline tests: the live mute reflex, the out-of-sight
# state, the escalation ladder, the socket2 + pactl event sources, and the
# laptop bits. All hermetic: no pactl, no Hyprland, no Jev API.
# Run from remix/:
#   .venv/bin/python -m unittest discover -s brain/tests -t . -v
import asyncio
import json
import random
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os_tmp = tempfile.mkdtemp(prefix="remix-test4-")
import os  # noqa: E402
os.environ.setdefault("REMIX_TTS_DIR", tempfile.mkdtemp(prefix="remix-test4-voice-"))

from brain import audio, awareness, bits, config, decisions, main as brain_main  # noqa: E402
from brain import proactive  # noqa: E402
from brain.awareness import build_around, out_of_sight_of, parse_clients_json  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def _monitors(ws_active=1):
    return [{"id": 0, "name": "DP-1", "x": 0.0, "y": 0.0, "w": 1920.0,
             "h": 1080.0, "ws": ws_active, "focused": True}]


def _clients(**kw):
    """Her window + one terminal. kw overrides her fields."""
    her = {"class": "remix", "initialClass": "remix", "title": "remix",
           "mapped": True, "hidden": False, "at": [100, 100], "size": [400, 400],
           "workspace": {"id": kw.get("her_ws", 2), "name": "two"},
           "monitor": 0, "floating": True, "pinned": kw.get("pinned", False),
           "focusHistoryID": kw.get("hist", 1), "fullscreen": 0}
    term = {"class": "kitty", "initialClass": "kitty", "title": "work",
            "mapped": True, "hidden": False,
            "at": [kw.get("term_x", 700), 100], "size": [800, 600],
            "workspace": {"id": kw.get("term_ws", 1), "name": "one"},
            "monitor": 0, "floating": False, "pinned": False,
            "focusHistoryID": kw.get("term_hist", 0), "fullscreen": 0}
    return json.dumps([her, term])


class DecisionKindTests(unittest.TestCase):
    def test_phase4_kinds_exist_with_modes(self):
        self.assertIn("escalate", decisions.KINDS)
        self.assertIn("mute_react", decisions.KINDS)
        saved_modes = dict(decisions._modes)
        decisions._modes.clear()          # older suites leave overrides behind
        try:
            self.assertEqual(decisions.mode("escalate"),
                             config.JEV_DECIDE_ESCALATE)
            self.assertEqual(decisions.mode("mute_react"),
                             config.JEV_DECIDE_MUTE)
        finally:
            decisions._modes.clear()
            decisions._modes.update(saved_modes)
        for kind in ("escalate", "mute_react"):
            self.assertIn(decisions.mode(kind), ("off", "shadow", "live"))


class AudioEventTests(unittest.TestCase):
    def tearDown(self):
        audio.reset_for_test()

    def test_event_relevant_parsing(self):
        self.assertTrue(audio._event_relevant("Event 'change' on sink #71"))
        self.assertTrue(audio._event_relevant("Event 'new' on server #0"))
        self.assertFalse(audio._event_relevant("Event 'change' on client #3"))
        self.assertFalse(audio._event_relevant("garbage"))

    def test_edge_callbacks_fire_on_flip_only(self):
        got = []

        async def cb(quiet):
            got.append(quiet)

        audio.on_change(cb)
        run(audio._apply(True))          # same as the boot default: no edge
        self.assertEqual(got, [])
        run(audio._apply(False))         # muted: edge fires
        self.assertEqual(got, [False])
        run(audio._apply(False))         # still muted: no edge
        self.assertEqual(got, [False])
        run(audio._apply(None))          # can't tell fails open to quiet: edge
        self.assertEqual(got, [False, True])

    def test_broken_listener_never_breaks_the_gate(self):
        async def bad(quiet):
            raise RuntimeError("boom")

        audio.on_change(bad)
        run(audio._apply(False))         # must not raise
        self.assertFalse(audio.quiet())


class OutOfSightTests(unittest.TestCase):
    def setUp(self):
        self._saved = config.SELF_MATCH
        config.SELF_MATCH = "remix"

    def tearDown(self):
        config.SELF_MATCH = self._saved

    def test_unpinned_on_another_workspace_is_out_of_sight(self):
        a = build_around(parse_clients_json(_clients()), _monitors(ws_active=1))
        self.assertTrue(out_of_sight_of(a))

    def test_pinned_is_always_visible(self):
        a = build_around(parse_clients_json(_clients(pinned=True)),
                         _monitors(ws_active=1))
        self.assertFalse(out_of_sight_of(a))

    def test_same_active_workspace_is_visible(self):
        a = build_around(parse_clients_json(_clients(her_ws=1, hist=1)),
                         _monitors(ws_active=1))
        self.assertFalse(out_of_sight_of(a))

    def test_focused_is_visible_even_elsewhere(self):
        a = build_around(parse_clients_json(_clients(her_ws=2, hist=0,
                                                      term_hist=1)),
                         _monitors(ws_active=1))
        self.assertFalse(out_of_sight_of(a))

    def test_covered_is_out_of_sight_even_on_her_workspace(self):
        # the terminal sits fully on top of her: covered >= 0.6
        a = build_around(parse_clients_json(_clients(her_ws=1, term_ws=1,
                                                      term_x=100)),
                         _monitors(ws_active=1))
        self.assertTrue(out_of_sight_of(a))

    def test_unknown_when_she_is_not_found(self):
        self.assertIsNone(out_of_sight_of({"self": None}))
        self.assertIsNone(out_of_sight_of(None))


class Socket2Tests(unittest.TestCase):
    def test_relevant_event_names(self):
        for line in ("workspace>>2", "activewindow>>kitty,work",
                     "openwindow>>0,1,kitty,work", "closewindow>>",
                     "fullscreen>>0", "pin>>0,1,0", "movewindow>>"):
            self.assertTrue(awareness.event_relevant(line), line)
        for line in ("unknown>>x", "", "submap>>", "configreloaded>>"):
            self.assertFalse(awareness.event_relevant(line), line)

    def test_socket2_path(self):
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "sig",
                                          "XDG_RUNTIME_DIR": "/run/u"}):
            self.assertEqual(awareness._socket2_path(),
                             "/run/u/hypr/sig/.socket2.sock")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(awareness._socket2_path())

    def test_watch_loop_survives_the_boot_wake_race(self):
        # no socket2 event has arrived yet -> _dirty is still None; the
        # first wait must time out quietly, not kill the poller with an
        # AttributeError (that froze awareness at boot state for real)
        saved = awareness._dirty
        awareness._dirty = None
        try:
            self.assertFalse(run(awareness._sleep_or_wake(0.05)))
            awareness._wake()
            self.assertTrue(run(awareness._sleep_or_wake(1.0)))
        finally:
            awareness._dirty = saved


class BitDetectorTests(unittest.TestCase):
    def test_lid(self):
        root = Path(os_tmp) / "lid"
        (root / "LID0").mkdir(parents=True)
        (root / "LID0" / "state").write_text("state:      closed\n")
        self.assertTrue(bits._lid(str(root)))
        (root / "LID0" / "state").write_text("state:      open\n")
        self.assertFalse(bits._lid(str(root)))
        self.assertIsNone(bits._lid(str(Path(os_tmp) / "nope")))

    def test_brightness(self):
        root = Path(os_tmp) / "bl"
        (root / "intel_backlight").mkdir(parents=True)
        (root / "intel_backlight" / "brightness").write_text("1380\n")
        self.assertEqual(bits._brightness(str(root)), 1380.0)

    def test_wifi(self):
        root = Path(os_tmp) / "net"
        (root / "wlo1").mkdir(parents=True)
        (root / "wlo1" / "operstate").write_text("up\n")
        (root / "lo").mkdir()
        self.assertTrue(bits._wifi(str(root)))
        (root / "wlo1" / "operstate").write_text("down\n")
        self.assertFalse(bits._wifi(str(root)))

    def test_usb_counts_real_devices(self):
        root = Path(os_tmp) / "usb"
        root.mkdir(parents=True)
        for name in ("1-2", "1-2.3", "usb1", "1-2:1.0", "1-3:1.0"):
            (root / name).mkdir()
        self.assertEqual(bits._usb(str(root)), 2)

    def test_screenshot_count_only(self):
        d = Path(os_tmp) / "shots"
        d.mkdir(parents=True)
        (d / "2026-11-11-01.png").write_bytes(b"x")
        (d / "2026-11-11-02.png").write_bytes(b"x")
        self.assertEqual(bits._screenshots([str(d)]), 2)
        self.assertEqual(bits._screenshots([str(Path(os_tmp) / "none")]), None)

    def test_poll_suspends_on_a_wall_clock_race(self):
        bits.reset_for_test()
        wall, mono = time.time(), time.monotonic()
        self.assertEqual(bits.poll(wall, mono), [])   # baseline: no edge
        # 10 min of wall time passed while monotonic moved 10 s: she slept
        edges = bits.poll(wall + 600.0, mono + 10.0)
        self.assertEqual([e["trigger"] for e in edges], ["suspend"])
        self.assertIn("asleep", edges[0]["fact"])

    def test_poll_fires_each_crossing_once(self):
        bits.reset_for_test()
        with mock.patch.multiple(
                bits, _lid=mock.Mock(return_value=False),
                _brightness=mock.Mock(return_value=100.0),
                _wifi=mock.Mock(return_value=True),
                _bluetooth=mock.Mock(return_value=0),
                _usb=mock.Mock(return_value=1),
                _audio_ports=mock.Mock(return_value=None),
                _locked=mock.Mock(return_value=False),
                _screenshots=mock.Mock(return_value=3)):
            wall, mono = time.time(), time.monotonic()
            self.assertEqual(bits.poll(wall, mono), [])   # first sight: no edge
            self.assertEqual(bits.poll(wall + 1, mono + 1), [])
            bits._lid.return_value = True
            bits._wifi.return_value = False
            bits._screenshots.return_value = 4
            edges = bits.poll(wall + 2, mono + 2)
            got = sorted(e["trigger"] for e in edges)
            self.assertEqual(got, ["lid_closed", "screenshot_taken", "wifi_down"])
            for e in edges:            # plain facts, in her program's words
                self.assertTrue(e["fact"].strip())


class ProactiveBitTests(unittest.TestCase):
    def _pro(self):
        p = proactive.Proactive(rng=random.Random(7), enabled=True,
                                awaken_s=None)
        p.greeted = True               # skip the boot greeting slot
        return p

    def test_bit_edge_becomes_a_plan_with_its_own_fact(self):
        p = self._pro()
        plan = p.tick({}, bit_edges=[{"trigger": "wifi_down",
                                      "fact": "the wifi just dropped"}],
                      now=1000.0, wall_hour=12)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["trigger"], "wifi_down")
        self.assertEqual(plan["situation"], "the wifi just dropped")

    def test_bit_respects_its_cooldown_floor(self):
        p = self._pro()
        p.tick({}, bit_edges=[{"trigger": "wifi_down",
                               "fact": "the wifi just dropped"}],
               now=1000.0, wall_hour=12)
        p.last_line_at = -1e12         # pretend MIN_GAP passed
        plan = p.tick({}, bit_edges=[{"trigger": "wifi_down",
                                      "fact": "the wifi just dropped"}],
                      now=1100.0, wall_hour=12)
        self.assertTrue(plan is None or plan["trigger"] != "wifi_down")

    def test_gates_eat_bits(self):
        p = self._pro()
        plan = p.tick({"fullscreen": True},            # DND while fullscreen
                      bit_edges=[{"trigger": "lid_closed",
                                  "fact": "the lid just closed"}],
                      now=1000.0, wall_hour=12)
        self.assertIsNone(plan)


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, m):
        self.sent.append(m)


class MuteReflexTests(unittest.TestCase):
    """main._on_audio_edge: stop + caption + flat look, locally and instantly."""

    def setUp(self):
        self.saved = {k: getattr(config, k) for k in
                      ("DECISIONS_LOG", "MUTE_REACT_COOLDOWN_S")}
        config.DECISIONS_LOG = False
        self.saved_log = decisions._log
        decisions._log = None
        self.saved_modes = dict(decisions._modes)
        decisions._modes.clear()
        self.ws = FakeWS()
        self._saved_ws = brain_main.ws_server
        brain_main.ws_server = self.ws
        self._saved_rule = brain_main._rule
        brain_main._rule = SimpleNamespace(speak_gate_until=1e12)
        brain_main._spoken_now.clear()
        brain_main._mute_pending = False
        brain_main._mute_last_react_at = -1e12
        brain_main._urgent = None
        brain_main._escal_rungs.clear()
        self._saved_aware = dict(awareness._state)
        awareness._state.update(out_of_sight=None)

    def tearDown(self):
        brain_main.ws_server = self._saved_ws
        brain_main._rule = self._saved_rule
        brain_main._spoken_now.clear()
        brain_main._mute_pending = False
        brain_main._urgent = None
        brain_main._escal_rungs.clear()
        awareness._state.clear()
        awareness._state.update(self._saved_aware)
        decisions._modes.clear()
        decisions._modes.update(self.saved_modes)
        decisions._log = self.saved_log
        config.DECISIONS_LOG = self.saved["DECISIONS_LOG"]
        config.MUTE_REACT_COOLDOWN_S = self.saved["MUTE_REACT_COOLDOWN_S"]
        audio.reset_for_test()

    def test_mute_mid_line_stops_her_and_captions_once(self):
        brain_main._spoken_now.update(pid="t9", text="you never listen anyway")
        run(brain_main._on_audio_edge(True))
        kinds = [m.get("type") for m in self.ws.sent]
        self.assertIn("stop", kinds)
        notice = next(m for m in self.ws.sent if m.get("type") == "notice")
        self.assertEqual(notice["text"], "you never listen anyway")
        pose = next(m for m in self.ws.sent if m.get("type") == "pose")
        self.assertEqual((pose.get("eyes"), pose.get("mouth")),
                         ("default", "default"))
        self.assertTrue(brain_main._mute_pending)
        self.assertEqual(brain_main._spoken_now, {})
        # mute with nothing in flight: the reflex stays asleep
        self.ws.sent.clear()
        run(brain_main._on_audio_edge(True))
        self.assertEqual(self.ws.sent, [])

    def test_unmute_dry_beat_is_jev_only_and_rate_limited(self):
        # shadow ships nothing (the local fallback is silence, logged)
        brain_main._mute_pending = True
        run(brain_main._on_audio_edge(False))
        self.assertFalse(brain_main._mute_pending)
        self.assertEqual([m for m in self.ws.sent if m.get("type") == "notice"],
                         [])
        # live but inside the cooldown floor: skipped
        decisions.set_mode("mute_react", "live")
        brain_main._mute_pending = True
        brain_main._mute_last_react_at = time.monotonic() - 1.0
        config.MUTE_REACT_COOLDOWN_S = 900.0
        run(brain_main._on_audio_edge(False))
        self.assertEqual([m for m in self.ws.sent if m.get("type") == "notice"],
                         [])


class EscalationTests(unittest.TestCase):
    def setUp(self):
        self.saved = {k: getattr(config, k) for k in
                      ("DECISIONS_LOG", "OOS_URGENT", "ESCALATE_NOTIFY",
                       "ESCALATE_DISPATCH", "URGENT_ENABLED")}
        config.DECISIONS_LOG = False
        self.saved_log = decisions._log
        decisions._log = None
        self.saved_modes = dict(decisions._modes)
        decisions._modes.clear()
        self.ws = FakeWS()
        self._saved_ws = brain_main.ws_server
        brain_main.ws_server = self.ws
        self._saved_jev = brain_main._jev
        brain_main._jev = SimpleNamespace(
            decide_proactive=self._jev_none)
        self._saved_urgent = brain_main._urgent
        brain_main._urgent = None
        brain_main._escal_rungs.clear()
        self._saved_aware = dict(awareness._state)
        awareness._state.update(out_of_sight=True, self_focused=False)

    async def _jev_none(self, *a, **k):
        return None

    def tearDown(self):
        brain_main.ws_server = self._saved_ws
        brain_main._jev = self._saved_jev
        brain_main._urgent = self._saved_urgent
        brain_main._escal_rungs.clear()
        awareness._state.clear()
        awareness._state.update(self._saved_aware)
        decisions._modes.clear()
        decisions._modes.update(self.saved_modes)
        decisions._log = self.saved_log
        for k, v in self.saved.items():
            setattr(config, k, v)

    def test_local_rung1_glow_when_she_speaks_out_of_sight(self):
        run(brain_main._maybe_escalate("reply", "here is your answer"))
        self.assertIsNotNone(brain_main._urgent)
        urgent = next(m for m in self.ws.sent if m.get("type") == "urgent")
        self.assertTrue(urgent["state"])
        self.assertEqual(urgent["reason"], "reply")
        # no rungs 2/3 in shadow mode
        self.assertEqual([m for m in self.ws.sent if m.get("type") == "notice"],
                         [])
        self.assertEqual(brain_main._escal_rungs, {})

    def test_visible_means_no_glow(self):
        awareness._state.update(out_of_sight=False)
        run(brain_main._maybe_escalate("reply", "hi"))
        self.assertIsNone(brain_main._urgent)
        self.assertEqual(brain_main.ws_server.sent, [])

    def test_switch_off_stops_the_oos_glow(self):
        config.OOS_URGENT = False
        run(brain_main._maybe_escalate("reply", "hi"))
        self.assertIsNone(brain_main._urgent)

    def test_clearing_urgent_resets_the_ladder(self):
        run(brain_main._maybe_escalate("reply"))
        brain_main._escal_rungs[2] = 1.0
        run(brain_main._set_urgent(False, "you looked"))
        self.assertIsNone(brain_main._urgent)
        self.assertEqual(brain_main._escal_rungs, {})
        off = [m for m in self.ws.sent if m.get("type") == "urgent"][-1]
        self.assertFalse(off["state"])

    def test_hypr_focus_cmd(self):
        # `focuswindow` takes the matcher as a separate argv item; the `^`
        # caret is batch syntax and makes a single dispatch do nothing.
        self.assertEqual(brain_main._hypr_focus_cmd("remix"),
                         ["hyprctl", "dispatch", "focuswindow", "class:remix"])
        self.assertEqual(brain_main._hypr_focus_cmd("Remix, kitty"),
                         ["hyprctl", "dispatch", "focuswindow", "class:remix"])
        self.assertIsNone(brain_main._hypr_focus_cmd(""))
        self.assertIsNone(brain_main._hypr_focus_cmd(None))

    def test_live_dispatch_uses_the_helper(self):
        config.ESCALATE_NOTIFY = False
        config.ESCALATE_DISPATCH = True
        decisions._modes["escalate"] = "live"
        saved = brain_main._jev

        async def jev_yes(*a, **k):
            return {"speak_p": 0.9}      # Jev approves the escalation

        brain_main._jev = SimpleNamespace(decide_proactive=jev_yes)
        sent = []
        try:
            with mock.patch.object(brain_main.subprocess, "run",
                                   lambda cmd, **k: sent.append(cmd)):
                with mock.patch.object(brain_main.shutil, "which",
                                       lambda x: "/usr/bin/hyprctl"):
                    run(brain_main._maybe_escalate("reply", "hi"))
        finally:
            brain_main._jev = saved
        self.assertEqual(sent, [["hyprctl", "dispatch", "focuswindow",
                                 "class:remix"]])


if __name__ == "__main__":
    unittest.main()
