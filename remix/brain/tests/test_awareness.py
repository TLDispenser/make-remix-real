# Offline tests for Phase 6: awareness (senses + redaction) and proactive
# (unprompted lines). Run from remix/:
#   .venv/bin/python -m unittest discover -s brain/tests -t . -v
import asyncio
import os
import random
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("REMIX_TTS_DIR", tempfile.mkdtemp(prefix="remix-test-voice-"))

from brain import awareness, config, grounding, proactive, rules  # noqa: E402
from brain import speech  # noqa: E402
from brain import audio as audio_mod  # noqa: E402

AWAY = {"app": "firefox", "fullscreen": False, "idle_s": 10.0,
        "plugged": True, "muted": False, "muted_s": None,
        "app_since": 0.0, "up_s": 100.0, "at": 0.0}


def run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------- redaction (privacy)

class RedactionTests(unittest.TestCase):
    def test_scrub_strips_url_email_ip_path(self):
        dirty = ("KDE Connect - jane.doe@example.com https://bank.example/"
                 "account?token=1 10.0.0.2 /home/user/secret/tax.pdf")
        clean = awareness.scrub_title(dirty)
        self.assertNotIn("@", clean or "")
        self.assertNotIn("http", clean or "")
        self.assertNotIn("10.0.0.2", clean or "")
        self.assertNotIn("/home", clean or "")
        self.assertNotIn("tax", clean or "")

    def test_scrub_drops_empty_and_oversized(self):
        self.assertIsNone(awareness.scrub_title(""))
        self.assertIsNone(awareness.scrub_title("https://only-a-url.example/x"))
        self.assertIsNone(awareness.scrub_title("x" * 200))

    def test_redact_default_drops_every_title(self):
        # No allowlist -> even an innocent title must not pass. Spec rule.
        self.assertIsNone(awareness.redact("firefox", "GitHub Pull Requests"))
        self.assertIsNone(awareness.redact("kitty", "vim notes.txt"))

    def test_redact_allowlisted_class_passes_scrubbed(self):
        old = config.TITLE_ALLOW
        try:
            config.TITLE_ALLOW = "mpv, Spotify"
            self.assertEqual("big buck bunny",
                             awareness.redact("mpv", "big buck bunny"))
            self.assertIsNone(awareness.redact("firefox", "Inbox (9) - mail"))
            # allowlisted but dirty title -> URL scrubbed away, harmless word left
            leftover = awareness.redact(
                "mpv", "watch https://tracker.example/cursed-link")
            self.assertIsNotNone(leftover)
            self.assertNotIn("http", leftover)
            self.assertNotIn("tracker", leftover)
        finally:
            config.TITLE_ALLOW = old

    def test_snapshot_never_carries_a_title(self):
        awareness.reset_for_test()
        fake = mock.patch.object(
            awareness, "_hyprctl",
            lambda args: '{"class":"Firefox","initialClass":"firefox",'
                         '"title":"my bank statement Q4","fullscreen":1}'
                         if "activewindow" in args else "5, 6")
        with fake:
            awareness._observe_window(1000.0)
        snap = awareness.current()
        self.assertEqual("firefox", snap["app"])
        self.assertTrue(snap["fullscreen"])
        self.assertNotIn("title", snap)
        for v in snap.values():
            self.assertNotIn("bank", str(v))


# ------------------------------------------------ title opt-in (REMIX_TITLES)

class TitleOptInTests(unittest.TestCase):
    """The opt-in-EVERYTHING knob: off (default) / scrub / raw."""

    def setUp(self):
        self.saved = (config.TITLES, config.TITLE_ALLOW)
        awareness.reset_for_test()

    def tearDown(self):
        config.TITLES, config.TITLE_ALLOW = self.saved
        awareness.reset_for_test()

    @staticmethod
    def _observe(js):
        with mock.patch.object(
                awareness, "_hyprctl",
                lambda args: js if "activewindow" in args else "1, 1"):
            awareness._observe_window(1000.0)

    def test_off_mode_never_passes_any_title(self):
        config.TITLES = "off"
        self._observe('{"class":"kitty","title":"secret notes","fullscreen":0}')
        self.assertNotIn("title", awareness.current())
        # allowlist still works per-class in off mode (scrubbed)
        config.TITLE_ALLOW = "kitty"
        self._observe('{"class":"kitty","title":"secret notes","fullscreen":0}')
        self.assertEqual("secret notes", awareness.current()["title"])

    def test_scrub_mode_cleans_every_title(self):
        config.TITLES = "scrub"
        self._observe('{"class":"kitty","title":"notes.txt - vim","fullscreen":0}')
        self.assertEqual("notes.txt - vim", awareness.current()["title"])
        self._observe('{"class":"firefox","title":'
                      '"mail a@b.com https://x.example/t?token=1","fullscreen":0}')
        t = awareness.current()["title"]
        self.assertNotIn("@", t)
        self.assertNotIn("http", t)
        self.assertNotIn("token", t)
        # a title that is ONLY a URL -> nothing survives -> no title at all
        self._observe('{"class":"firefox",'
                      '"title":"https://only.example/x","fullscreen":0}')
        self.assertNotIn("title", awareness.current())

    def test_raw_mode_passes_titles_untouched_but_capped(self):
        config.TITLES = "raw"
        self._observe('{"class":"kitty",'
                      '"title":"a@b.com https://keep.example/?token=xyz",'
                      '"fullscreen":0}')
        self.assertEqual("a@b.com https://keep.example/?token=xyz",
                         awareness.current()["title"])
        long = "x" * 500
        self._observe('{"class":"kitty","title":"' + long + '","fullscreen":0}')
        self.assertEqual(200, len(awareness.current()["title"]))

    def test_no_readable_window_clears_a_stale_title(self):
        config.TITLES = "raw"
        self._observe('{"class":"kitty","title":"hello","fullscreen":0}')
        self.assertIn("title", awareness.current())
        with mock.patch.object(
                awareness, "_hyprctl",
                lambda args: "Invalid" if "activewindow" in args else "1, 1"):
            awareness._observe_window(1001.0)
        self.assertNotIn("title", awareness.current())

    def test_describe_shows_the_title_when_visible(self):
        config.TITLES = "raw"
        self._observe('{"class":"kitty","title":"hello","fullscreen":0}')
        self.assertIn('title="hello"', awareness.describe())


# ------------------------------------------------------------------ parsers

class ParseTests(unittest.TestCase):
    def test_window_json(self):
        p = awareness.parse_window_json(
            '{"class":"kitty","title":"htop","fullscreen":0}')
        self.assertEqual("kitty", p["app"])
        self.assertEqual("htop", p["title"])
        self.assertFalse(p["fullscreen"])
        self.assertIsNone(awareness.parse_window_json("Invalid"))
        self.assertIsNone(awareness.parse_window_json(""))
        self.assertIsNone(awareness.parse_window_json('{"title":"no class"}'))
        p = awareness.parse_window_json(
            '{"initialClass":"foot","title":"x","fullscreen":2}')
        self.assertEqual("foot", p["app"])
        self.assertTrue(p["fullscreen"])     # 2 means fullscreen too

    def test_cursor(self):
        self.assertEqual((12, 34), awareness.parse_cursor("12, 34"))
        self.assertEqual((0, 0), awareness.parse_cursor("0,0"))
        self.assertIsNone(awareness.parse_cursor("nope"))
        self.assertIsNone(awareness.parse_cursor(None))

    def test_plugged_sysfs(self):
        import pathlib
        tmp = pathlib.Path(tempfile.mkdtemp())
        # AC present and online -> True
        (tmp / "ACAD").mkdir()
        (tmp / "ACAD" / "type").write_text("Mains")
        (tmp / "ACAD" / "online").write_text("1")
        self.assertTrue(awareness.read_plugged(str(tmp)))
        # AC offline -> False (AC node is authoritative when present)
        (tmp / "ACAD" / "online").write_text("0")
        bat = tmp / "BAT0"
        bat.mkdir()
        (bat / "type").write_text("Battery")
        (bat / "status").write_text("Discharging")
        self.assertFalse(awareness.read_plugged(str(tmp)))
        # No AC node at all -> battery status decides (some laptops ship one)
        import shutil as _sh
        _sh.rmtree(tmp / "ACAD")
        self.assertFalse(awareness.read_plugged(str(tmp)))
        (bat / "status").write_text("Charging")
        self.assertTrue(awareness.read_plugged(str(tmp)))
        # nothing usable -> None
        empty = pathlib.Path(tempfile.mkdtemp())
        self.assertIsNone(awareness.read_plugged(str(empty)))
        self.assertIsNone(awareness.read_plugged("/nonexistent-sysfs"))


# ------------------------------------------------------- open apps (events)

class OpenAppsTests(unittest.TestCase):
    def test_open_apps_of_lists_classes_and_excludes_herself(self):
        clients = [
            {"app": "kitty", "title": "htop"},
            {"app": "kitty", "title": "second window"},   # dedup by class
            {"app": "Remix", "title": "Remix"},           # she is not an event
            {"app": "", "title": "junk row"},             # no class -> nothing
        ]
        self.assertEqual(["kitty"], awareness.open_apps_of(clients))
        self.assertEqual([], awareness.open_apps_of([]))

    def test_open_apps_lands_in_the_snapshot(self):
        awareness.reset_for_test()
        clients = [{"app": "kitty", "title": "t", "x": 0, "y": 0, "w": 10,
                    "h": 10, "ws": 1, "ws_name": "1", "monitor": 0,
                    "floating": False, "pinned": False, "focused": True,
                    "fullscreen": False}]
        with mock.patch.object(awareness, "_hyprctl", return_value="[]"):
            # no clients text is handled above; go through the real observe
            with mock.patch.object(awareness, "config") as cfg:
                cfg.SURROUNDINGS = False
                cfg.APP_EVENTS = True
                with mock.patch.object(awareness, "parse_clients_json",
                                       return_value=clients):
                    awareness._observe_around(1000.0)
        self.assertEqual(["kitty"], awareness.current()["open_apps"])
        awareness.reset_for_test()


# ------------------------------------------------------------ idle tracking

class IdleTrackingTests(unittest.TestCase):
    def tearDown(self):
        awareness.reset_for_test()

    def test_cursor_motion_and_window_switch_reset_idle(self):
        awareness.reset_for_test()
        js = '{"class":"kitty","title":"t","fullscreen":0}'
        with mock.patch.object(awareness, "_hyprctl",
                               lambda args: js if "activewindow" in args
                               else "1, 1"):
            awareness._observe_window(100.0)     # app change = activity
            awareness._observe_cursor(100.0)
            self.assertEqual(100.0, awareness._active_at)
            awareness._observe_cursor(150.0)     # cursor unmoved: idle grows
            self.assertEqual(100.0, awareness._active_at)
            with mock.patch.object(awareness, "_hyprctl",
                                   lambda args: js if "activewindow" in args
                                   else "2, 2"):
                awareness._observe_cursor(200.0)  # cursor moved: activity
                self.assertEqual(200.0, awareness._active_at)

    def test_same_app_no_activity(self):
        awareness.reset_for_test()
        js = '{"class":"kitty","title":"t","fullscreen":0}'
        with mock.patch.object(awareness, "_hyprctl",
                               lambda args: js if "activewindow" in args
                               else "1, 1"):
            awareness._observe_window(100.0)
            awareness._active_at = 0.0           # pretend activity was long ago
            awareness._observe_window(150.0)     # same app: NOT activity
            self.assertEqual(0.0, awareness._active_at)

    def test_refresh_offline_never_raises(self):
        awareness.reset_for_test()
        with mock.patch("brain.awareness._hyprctl", return_value=None):
            snap = run(awareness.refresh())      # forced offline: no hyprland
        for key in ("app", "fullscreen", "idle_s", "plugged", "muted",
                    "muted_s", "app_since", "up_s", "at"):
            self.assertIn(key, snap)
        self.assertIsNone(snap["app"])

    def test_describe_is_redacted_and_readable(self):
        awareness.seed_for_test(app="firefox", idle_s=600.0, plugged=False,
                                muted=True)
        s = awareness.describe()
        self.assertIn("firefox", s)
        self.assertIn("battery", s)
        self.assertIn("quiet", s)
        awareness.reset_for_test()


# ------------------------------------------------------- proactive triggers

class ProactiveTests(unittest.TestCase):
    def setUp(self):
        self.saved = {k: getattr(config, k) for k in
                      ("PROACTIVE_MIN_GAP_S", "PROACTIVE_MAX_PER_DAY",
                       "BATT_LOW", "IDLE_GENTLE_S", "IDLE_DEEP_S", "AWAY_S",
                       "MUTED_NAG_S", "SESSION_LONG_S", "APP_STUCK_S",
                       "QUIET_HOURS", "PROACTIVE_TICK_S")}
        config.PROACTIVE_MIN_GAP_S = 0.0
        config.QUIET_HOURS = ""
        config.BATT_LOW = 15.0
        config.IDLE_GENTLE_S = 900.0
        config.IDLE_DEEP_S = 2700.0
        config.AWAY_S = 1800.0
        config.MUTED_NAG_S = 600.0
        config.SESSION_LONG_S = 10800.0
        config.APP_STUCK_S = 5400.0

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(config, k, v)

    def eng(self):
        return proactive.Proactive(rng=random.Random(7), enabled=True)

    def test_greeting_fires_a_plan_not_prewritten_words(self):
        e = self.eng()
        plan = e.tick(dict(AWAY), {}, now=1000.0)
        self.assertEqual("greeting", plan["trigger"])
        self.assertNotIn("text", plan)        # words are NEVER pre-written
        self.assertTrue(plan["situation"])
        self.assertIn(plan["stance"], proactive.STANCE_CHOICES["greeting"])
        self.assertIsNotNone(plan["face"])
        self.assertIsNone(e.tick(dict(AWAY), {}, now=2000.0))

    def test_disabled_never_fires(self):
        e = proactive.Proactive(enabled=False)
        self.assertIsNone(e.tick(dict(AWAY), {}, now=1000.0))

    def test_suppression_gates(self):
        # fullscreen DND, mic open, just-exchanged, mid-speech, quiet hours
        e = self.eng()
        self.assertIsNone(e.tick(dict(AWAY, fullscreen=True), {}, now=1000.0))
        self.assertEqual("greeting", e.tick(dict(AWAY), {}, now=1001.0)["trigger"])
        e2 = self.eng()
        self.assertIsNone(e2.tick(dict(AWAY), {}, now=1000.0, mic_open=True))
        self.assertIsNone(e2.tick(dict(AWAY), {}, now=1010.0,
                                  last_exchange_at=1005.0))
        self.assertIsNone(e2.tick(dict(AWAY), {}, now=1020.0, busy_until=1030.0))
        self.assertEqual("greeting", e2.tick(dict(AWAY), {}, now=1040.0)["trigger"])
        old = config.QUIET_HOURS
        try:
            config.QUIET_HOURS = "1-8"
            e3 = self.eng()
            self.assertIsNone(e3.tick(dict(AWAY), {}, now=1000.0, wall_hour=3))
            self.assertEqual("greeting",
                             e3.tick(dict(AWAY), {}, now=1001.0, wall_hour=12)
                             ["trigger"])
        finally:
            config.QUIET_HOURS = old

    def test_idle_gentle_and_deep_edges_fire_once_per_episode(self):
        e = self.eng()
        e.greeted = True                      # skip greeting
        self.assertIsNone(e.tick(dict(AWAY, idle_s=100.0), {}, now=1000.0))
        line = e.tick(dict(AWAY, idle_s=1000.0), {}, now=1010.0)
        self.assertEqual("idle_gentle", line["trigger"])
        self.assertIsNone(e.tick(dict(AWAY, idle_s=1200.0), {}, now=1020.0))
        # crossing BOTH thresholds in one step -> the deep check wins
        e2 = self.eng()
        e2.greeted = True
        e2.tick(dict(AWAY, idle_s=100.0), {}, now=1000.0)
        deep = e2.tick(dict(AWAY, idle_s=3000.0), {}, now=1010.0)
        self.assertEqual("idle_deep", deep["trigger"])
        self.assertEqual("concerned", deep["stance"])   # the only fitting stance

    def test_welcome_back_situation_carries_the_away_time(self):
        e = self.eng()
        e.greeted = True
        e.tick(dict(AWAY, idle_s=2400.0), {}, now=1000.0)
        plan = e.tick(dict(AWAY, idle_s=20.0), {}, now=1010.0)
        self.assertEqual("welcome_back", plan["trigger"])
        self.assertIn("40", plan["situation"])     # 2400 s away = 40 min

    def test_battery_low_edge_needs_discharging(self):
        e = self.eng()
        e.greeted = True
        e.tick(dict(AWAY), {"battery": 20.0}, now=1000.0)
        plan = e.tick(dict(AWAY, plugged=False), {"battery": 12.0}, now=1010.0)
        self.assertEqual("battery_low", plan["trigger"])
        self.assertIn("12", plan["situation"])
        # stays low: no repeat edge
        self.assertIsNone(e.tick(dict(AWAY, plugged=False), {"battery": 10.0},
                                 now=1020.0))
        # charging while low: no alarm
        e2 = self.eng()
        e2.greeted = True
        e2.tick(dict(AWAY, plugged=True), {"battery": 20.0}, now=1000.0)
        self.assertIsNone(e2.tick(dict(AWAY, plugged=True), {"battery": 12.0},
                                  now=1010.0))

    def test_plugged_edge_fires(self):
        e = self.eng()
        e.greeted = True
        e.tick(dict(AWAY, plugged=False), {}, now=1000.0)
        line = e.tick(dict(AWAY, plugged=True), {}, now=1010.0)
        self.assertEqual("plugged", line["trigger"])

    def test_app_opened_and_closed_edges(self):
        e = self.eng()
        e.greeted = True
        base = dict(AWAY, idle_s=10.0)
        # first tick primes the open set: what's already open is not news
        self.assertIsNone(e.tick(dict(base, open_apps=["firefox", "kitty"]),
                                 {}, now=1000.0))
        # a NEW class opens -> app_opened, aimed at it, named in the seed
        plan = e.tick(dict(base, open_apps=["firefox", "kitty", "zathura"]),
                      {}, now=1010.0)
        self.assertEqual("app_opened", plan["trigger"])
        self.assertEqual("zathura", plan["aim"])
        self.assertIn("zathura", plan["situation"])
        self.assertIn(plan["stance"], proactive.STANCE_CHOICES["app_opened"])
        # the per-trigger cooldown swallows another opening right away
        self.assertIsNone(e.tick(dict(
            base, open_apps=["firefox", "kitty", "zathura", "thunar"]),
            {}, now=1020.0))
        # a batch change (session restore) is noise, not news
        self.assertIsNone(e.tick(dict(base, open_apps=[f"a{i}" for i in range(5)]),
                                 {}, now=1030.0))
        # closing fires its own trigger, with no window to glance at
        e2 = self.eng()
        e2.greeted = True
        self.assertIsNone(e2.tick(dict(base, open_apps=["kitty"]), {}, now=1000.0))
        plan = e2.tick(dict(base, open_apps=[]), {}, now=1010.0)
        self.assertEqual("app_closed", plan["trigger"])
        self.assertIn("kitty", plan["situation"])
        self.assertIsNone(plan["aim"])

    def test_app_opened_needs_the_user_at_the_keyboard(self):
        e = self.eng()
        e.greeted = True
        base = dict(AWAY, open_apps=["kitty"])
        e.tick(base, {}, now=1000.0)
        # she noticed the change while they are away: she stays quiet
        self.assertIsNone(e.tick(dict(base, open_apps=["kitty", "zathura"],
                                      idle_s=600.0), {}, now=1010.0))
        # and the event is eaten, not replayed when they return
        self.assertIsNone(e.tick(dict(base, open_apps=["kitty", "zathura"],
                                      idle_s=10.0), {}, now=2000.0))

    def test_muted_long_nag(self):
        e = self.eng()
        e.greeted = True
        self.assertIsNone(e.tick(dict(AWAY, muted=True, muted_s=100.0), {},
                                 now=1000.0))
        line = e.tick(dict(AWAY, muted=True, muted_s=700.0), {}, now=1010.0)
        self.assertEqual("muted_long", line["trigger"])

    def test_long_session_and_app_stuck(self):
        e = self.eng()
        e.greeted = True
        line = e.tick(dict(AWAY, up_s=11000.0), {}, now=1000.0)
        self.assertEqual("long_session", line["trigger"])
        e2 = self.eng()
        e2.greeted = True
        stuck = e2.tick(dict(AWAY, app="firefox", app_since=1000.0 - 6000.0),
                        {}, now=1000.0)
        self.assertEqual("app_stuck", stuck["trigger"])
        self.assertIn("firefox", stuck["situation"])

    def test_daily_cap(self):
        old = config.PROACTIVE_MAX_PER_DAY
        try:
            config.PROACTIVE_MAX_PER_DAY = 2
            e = self.eng()
            self.assertIsNotNone(e.tick(dict(AWAY), {}, now=1000.0))   # greeting
            self.assertIsNotNone(e.force(dict(AWAY), {}, now=1001.0))
            self.assertIsNone(e.tick(dict(AWAY), {}, now=1002.0))
        finally:
            config.PROACTIVE_MAX_PER_DAY = old

    def test_every_trigger_has_stances_that_map_to_faces(self):
        for trigger, choices in proactive.STANCE_CHOICES.items():
            self.assertTrue(choices, trigger)
            for s in choices:
                self.assertIn(s, proactive.STANCE_FACE, f"{trigger}/{s}")

    def test_situations_carry_real_facts(self):
        self.assertIn("12", proactive._situation("battery_low", dict(AWAY), 12.0))
        self.assertIn("firefox",
                      proactive._situation("app_stuck",
                                           dict(AWAY, app="firefox"),
                                           None, stuck_mins=95))
        self.assertIn("95",
                      proactive._situation("app_stuck",
                                           dict(AWAY, app="firefox"),
                                           None, stuck_mins=95))
        self.assertIn("10", proactive._situation("muted_long",
                                                 dict(AWAY, muted_s=600.0),
                                                 None))
        self.assertIn("3", proactive._situation("long_session",
                                                dict(AWAY, up_s=11000.0),
                                                None))
        self.assertIn("poked", proactive._situation("poke", {}, None))

    def test_poke_forces_a_plan(self):
        e = self.eng()
        plan = e.force(dict(AWAY), {})
        self.assertEqual("poke", plan["trigger"])
        self.assertTrue(plan["situation"])
        self.assertNotIn("text", plan)


# ------------------------------------------------ chatter words (generated)

class ChatterGenerationTests(unittest.TestCase):
    """speech.generate_proactive_line: the words, written fresh. The LLM call
    is faked; everything around it (screening, dedup, politeness) is real."""

    def setUp(self):
        speech._recent.clear()
        speech._history.clear()

    @staticmethod
    def _plan(situation="the sound has been off for about 10 minutes",
              stance="pout"):
        return {"trigger": "muted_long", "stance": stance, "face": None,
                "look": None, "situation": situation}

    def test_line_is_generated_and_cleaned(self):
        async def fake_llm(messages, temperature=0.7):
            return '  "sound\'s been off. rude. noted."  '

        with mock.patch.object(speech, "_call_llm", fake_llm):
            line = run(speech.generate_proactive_line(
                self._plan(), vitals={"battery": 50.0},
                aware=dict(AWAY, app="firefox")))
        self.assertEqual("sound's been off. rude. noted.", line)

    def test_grounding_failure_retries_then_passes(self):
        responses = iter(["forty tabs open in there",
                          "the keyboard went quiet on you"])

        async def fake_llm(messages, temperature=0.7):
            return next(responses)

        with mock.patch.object(speech, "_call_llm", fake_llm):
            line = run(speech.generate_proactive_line(
                self._plan(situation="the keyboard has been quiet"),
                vitals=None, aware=dict(AWAY, app="firefox")))
        self.assertEqual("the keyboard went quiet on you", line)

    def test_two_bad_attempts_end_in_silence(self):
        async def fake_llm(messages, temperature=0.7):
            return "forty tabs open in there"

        with mock.patch.object(speech, "_call_llm", fake_llm):
            line = run(speech.generate_proactive_line(self._plan()))
        self.assertIsNone(line)               # silence, never a canned line

    def test_no_repeat_of_something_she_already_said(self):
        speech.add_assistant("still there, huh.")
        responses = iter(["Still there, huh?",
                          "oh. you moved the mouse. thrilling."])

        async def fake_llm(messages, temperature=0.7):
            return next(responses)

        with mock.patch.object(speech, "_call_llm", fake_llm):
            line = run(speech.generate_proactive_line(self._plan()))
        self.assertEqual("oh. you moved the mouse. thrilling.", line)

    def test_rambling_gets_one_shot_to_be_short(self):
        responses = iter(["blah " * 60, "short and flat. like me."])

        async def fake_llm(messages, temperature=0.7):
            return next(responses)

        with mock.patch.object(speech, "_call_llm", fake_llm):
            line = run(speech.generate_proactive_line(self._plan()))
        self.assertEqual("short and flat. like me.", line)

    def test_yields_to_a_real_exchange(self):
        async def scenario():
            async with speech._lock:
                return await speech.generate_proactive_line(self._plan())
        self.assertIsNone(run(scenario()))

    def test_empty_situation_is_silence(self):
        self.assertIsNone(run(speech.generate_proactive_line({"trigger": "x"})))

    def test_add_assistant_feeds_the_dedup_memory(self):
        speech.add_assistant("hi again")
        self.assertIn("hi again", speech._recent)


# --------------------------------------------------- grounding awareness ties

class GroundingAwareTests(unittest.TestCase):
    def test_facts_include_app_name_only(self):
        out = grounding.facts("cpu 10%", "TestLaptop", None,
                              dict(AWAY, app="firefox"))
        self.assertIn('"firefox"', out)
        self.assertIn("NAME only", out)
        self.assertIn("window title", out)
        self.assertNotIn("my bank statement", out)
        plain = grounding.facts("cpu 10%", "TestLaptop", None)
        self.assertNotIn("firefox", plain)

    def test_check_allows_sensed_app_and_word_app(self):
        aware = dict(AWAY, app="firefox")
        self.assertEqual([], grounding.check("you're in firefox. again.", "",
                                             "", None, aware))
        self.assertEqual([], grounding.check("what app even is that", "",
                                             "", None, aware))
        self.assertNotEqual([], grounding.check("you're in firefox. again.",
                                                "", "", None, None))
        # 'tabs' is still never allowed, aware or not
        self.assertNotEqual([], grounding.check("you have forty tabs open",
                                                "", "", None, aware))

    def test_check_blocks_other_apps(self):
        aware = dict(AWAY, app="firefox")
        self.assertNotEqual([], grounding.check("enjoying chrome?", "",
                                                "", None, aware))

    def test_facts_widen_and_check_allows_quoting_a_visible_title(self):
        aware = dict(AWAY, app="firefox", title="listening party tonight")
        out = grounding.facts("cpu 10%", "TestLaptop", None, aware)
        self.assertIn("listening party tonight", out)
        self.assertNotIn("window TITLES", out)
        self.assertEqual([],
                         grounding.check("listening party, huh. cute.",
                                         "", "", None, aware))
        # still cannot invent what a title never said
        self.assertNotEqual([],
                            grounding.check("forty tabs open in there",
                                            "", "", None, aware))

    def test_title_word_unblocks_even_flagged_words(self):
        aware = dict(AWAY, app="chromium", title="12 tabs - chromium")
        self.assertEqual([],
                         grounding.check("twelve tabs. shameless.",
                                         "", "", None, aware))


# ------------------------------------------------------ RuleBrain DND + gate

class RuleBrainDndTests(unittest.TestCase):
    def setUp(self):
        self.saved = {k: getattr(config, k) for k in
                      ("NOTICE_GATE", "NOTICE_MIN_GAP_S")}
        config.NOTICE_GATE = 0            # gate always passes: isolate the DND
        config.NOTICE_MIN_GAP_S = 0.0

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(config, k, v)

    def _rule(self):
        r = rules.RuleBrain()
        r.last_notice_at = 0.0
        r.last_look_at = 0.0
        r.last_twitch_at = 0.0
        return r

    SNAP = {"cpu": 10.0, "ram": 50.0, "temp": None, "battery": 50.0, "disk": 95.0}

    def test_flavor_notice_shows_when_audible_gate_off(self):
        with mock.patch("brain.rules.random.random", return_value=0.0):
            msgs = self._rule().update(dict(self.SNAP), now=1000.0)
        self.assertTrue(any(m.get("type") == "notice" for m in msgs))

    def test_fullscreen_holds_flavor_notices(self):
        with mock.patch("brain.rules.random.random", return_value=0.0):
            msgs = self._rule().update(dict(self.SNAP), now=1000.0,
                                       aware={"fullscreen": True})
        self.assertFalse(any(m.get("type") == "notice" for m in msgs))


if __name__ == "__main__":
    unittest.main()
