# test_surroundings.py — what's AROUND her: other windows, where they sit
# relative to her window, gaze toward them, grounding, privacy. Offline: the
# hyprctl JSON below is the real shape of `clients -j` / `monitors -j`.
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from brain import awareness, config, grounding, main as brain_main  # noqa: E402

MON = json.dumps([{"id": 0, "name": "eDP-1", "x": 0, "y": 0, "width": 1920,
                   "height": 1080, "scale": 1.0, "transform": 0,
                   "activeWorkspace": {"id": 1, "name": "1"},
                   "focused": True}])


def client(cls, title, x, y, w, h, ws=1, mon=0, focus=5, **kw):
    c = {"address": "0x1", "mapped": True, "hidden": False, "at": [x, y],
         "size": [w, h], "workspace": {"id": ws, "name": str(ws)},
         "floating": False, "monitor": mon, "class": cls, "title": title,
         "pinned": False, "fullscreen": 0, "focusHistoryID": focus}
    c.update(kw)
    return c


# She is the 480x270 window in the bottom-right corner.
ME = client("Remix", "Remix", 1420, 790, 480, 270, floating=True, pinned=True)
FIREFOX = client("firefox", "Secret bank - user@example.com — Mozilla Firefox",
                 0, 0, 1000, 1080, focus=0)
KITTY = client("kitty", "~/work/private", 1020, 20, 880, 400, focus=2)
UNDER = client("spotify", "Spotify", 1450, 800, 400, 250, focus=3)
OTHER_WS = client("discord", "friends", 0, 0, 800, 600, ws=3, focus=7)
BRAVE = client("brave-origin", "New Tab - Brave", 0, 0, 1000, 1080, focus=4)


def around(clients, **kw):
    return awareness.build_around(
        awareness.parse_clients_json(json.dumps(clients)),
        awareness.parse_monitors_json(MON), **kw)


class ParseTests(unittest.TestCase):
    def test_monitor_scale_and_rotation(self):
        m = awareness.parse_monitors_json(json.dumps([
            {"id": 0, "width": 3840, "height": 2160, "scale": 2.0, "x": 0, "y": 0},
            {"id": 1, "width": 1080, "height": 1920, "scale": 1.0, "x": 1920,
             "y": 0, "transform": 1}]))
        self.assertEqual((1920, 1080), (m[0]["w"], m[0]["h"]))   # logical px
        self.assertEqual((1920, 1080), (m[1]["w"], m[1]["h"]))   # rotated

    def test_hidden_unmapped_and_garbage_are_dropped(self):
        txt = json.dumps([FIREFOX, client("x", "t", 0, 0, 1, 1, hidden=True),
                          client("y", "t", 0, 0, 1, 1, mapped=False),
                          {"class": "", "at": [0, 0], "size": [1, 1]}])
        self.assertEqual(["firefox"],
                         [c["app"] for c in awareness.parse_clients_json(txt)])
        self.assertEqual([], awareness.parse_clients_json("not json"))
        self.assertEqual([], awareness.parse_monitors_json(""))


class SpatialTests(unittest.TestCase):
    def test_directions_relative_to_HER_window(self):
        a = around([ME, FIREFOX, KITTY])
        by = {w["app"]: w for w in a["windows"]}
        self.assertEqual("to my left", by["firefox"]["dir"])
        self.assertIn(by["kitty"]["dir"], ("above me", "up and to my left"))
        self.assertLess(by["firefox"]["look"][0], -0.5)     # looks left
        self.assertLess(by["kitty"]["look"][1], 0)          # looks up (y-)

    def test_self_is_excluded_and_found_by_title(self):
        a = around([ME, FIREFOX])
        self.assertNotIn("remix", [w["app"] for w in a["windows"]])
        godot = client("godot", "Remix", 1420, 790, 480, 270)   # class differs
        a2 = around([godot, FIREFOX])
        self.assertIsNotNone(a2["self"])

    def test_no_self_found_means_no_spatial_claims(self):
        a = around([FIREFOX, KITTY])
        self.assertIsNone(a["self"])
        self.assertEqual([], a["windows"])

    def test_window_under_her_is_covered_and_glanced_down(self):
        w = around([ME, UNDER])["windows"][0]
        self.assertGreaterEqual(w["covered"], 0.6)
        self.assertEqual([0.0, 0.45], w["look"])
        self.assertIn("behind me", awareness.describe_around(
            around([ME, UNDER]))[0])

    def test_big_window_behind_her_is_behind_not_beside(self):
        full = client("firefox", "t", 0, 0, 1920, 1080, focus=0)
        w = around([ME, full])["windows"][0]
        self.assertGreaterEqual(w["covered"], 0.6)       # she sits on it
        self.assertEqual([0.0, 0.45], w["look"])
        line = awareness.describe_around(around([ME, full]))[0]
        self.assertIn("behind me", line)
        self.assertNotIn("beside", line)

    def test_other_workspace_and_screen_have_no_position(self):
        other_mon = client("mpv", "video", 1920, 0, 800, 600, mon=1, ws=2)
        a = around([ME, OTHER_WS, other_mon])
        self.assertEqual([], a["windows"])
        where = {e["app"]: e["where"] for e in a["elsewhere"]}
        self.assertEqual("on workspace 3", where["discord"])
        self.assertEqual("on the other screen", where["mpv"])

    def test_focused_first_then_nearest_and_capped(self):
        a = around([ME, KITTY, FIREFOX, UNDER], max_n=2)
        self.assertEqual("firefox", a["windows"][0]["app"])   # focusHistory 0
        self.assertEqual(2, len(a["windows"]))


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.saved = (config.TITLES, config.TITLE_ALLOW)

    def tearDown(self):
        config.TITLES, config.TITLE_ALLOW = self.saved

    def _titles(self):
        a = around([ME, FIREFOX, KITTY], redactor=awareness.redact)
        return {w["app"]: w.get("title") for w in a["windows"]}

    def test_default_off_no_titles_anywhere(self):
        config.TITLES, config.TITLE_ALLOW = "off", ""
        self.assertEqual({"firefox": None, "kitty": None}, self._titles())
        blob = json.dumps(around([ME, FIREFOX, KITTY],
                                 redactor=awareness.redact))
        self.assertNotIn("Secret", blob)
        self.assertNotIn("example.com", blob)
        self.assertNotIn("private", blob)

    def test_scrub_strips_email_and_path(self):
        config.TITLES = "scrub"
        t = self._titles()
        self.assertNotIn("example.com", t["firefox"] or "")
        self.assertNotIn("@", t["firefox"] or "")
        self.assertIsNone(t["kitty"])                # all path -> nothing left


class MentionTests(unittest.TestCase):
    def setUp(self):
        awareness.reset_for_test()
        awareness.seed_for_test(around=around([ME, FIREFOX, KITTY, OTHER_WS]))

    def tearDown(self):
        awareness.reset_for_test()

    def test_name_and_category(self):
        self.assertEqual("firefox", awareness.find_mentioned(
            "ugh, close Firefox already")["app"])
        self.assertEqual("kitty", awareness.find_mentioned(
            "that terminal is on fire")["app"])
        self.assertEqual("firefox", awareness.find_mentioned(
            "your browser is hogging RAM")["app"])

    def test_brave_class_flavors_all_mean_brave(self):
        for cls in ("brave-origin", "brave-bin", "brave"):
            view = awareness.build_around(
                awareness.parse_clients_json(json.dumps([ME, client(cls, "x", 0, 0, 10, 10)])),
                awareness.parse_monitors_json(MON))
            awareness.seed_for_test(around=view)
            self.assertEqual(cls, awareness.find_mentioned("what's on brave?")["app"],
                             f"class {cls!r} should match the word 'brave'")
            self.assertEqual(cls, awareness.find_mentioned("the browser")["app"],
                             f"class {cls!r} should match the word 'browser'")

    def test_first_mention_wins(self):
        self.assertEqual("kitty", awareness.find_mentioned(
            "the terminal, not the browser")["app"])

    def test_cannot_point_at_what_is_not_on_screen(self):
        self.assertIsNone(awareness.find_mentioned("discord is yelling"))
        self.assertIsNone(awareness.find_mentioned("steam is quiet"))
        self.assertIsNone(awareness.look_for_text("nothing relevant here"))

    def test_no_substring_false_positives(self):
        self.assertIsNone(awareness.find_mentioned("fireflies are pretty"))

    def test_beat_looks_follow_the_sentence(self):
        beats = ["fine.", "firefox is the problem.", "whatever."]
        looks = brain_main._beat_looks(beats)
        fx = awareness.look_for_text("firefox")
        self.assertEqual(fx, looks[1])                 # the naming beat aims
        self.assertEqual(brain_main.LOOK_POOL[0], looks[0])   # before: drift
        self.assertEqual(fx, looks[2])                 # after: aim HOLDS

    def test_the_gaze_sticks_once_something_takes_it(self):
        # Random per-beat drift used to snap the aim away mid-sentence,
        # which read as broken gestures. Once a named beat takes pointer-eyes
        # (or the lead does), every later beat keeps them until the line ends.
        beats = ["firefox, again.", "hm.", "and more."]
        looks = brain_main._beat_looks(beats)
        fx = awareness.look_for_text("firefox")
        self.assertEqual([fx, fx, fx], looks)

    def test_lead_holds_for_the_whole_line_too(self):
        looks = brain_main._beat_looks(["hm.", "ok."], lead=[0.1, 0.2])
        self.assertEqual([[0.1, 0.2], [0.1, 0.2]], looks)

    def test_a_later_named_beat_steals_the_gaze_it_names(self):
        beats = ["hm.", "there, kitty's awake."]
        looks = brain_main._beat_looks(beats, lead=[0.4, 0.4])
        kt = awareness.look_for_text("kitty")
        self.assertEqual([0.4, 0.4], looks[0])         # lead starts
        self.assertEqual(kt, looks[1])                 # kitty takes over


class FocusHandlingTests(unittest.TestCase):
    def setUp(self):
        awareness.reset_for_test()

    def tearDown(self):
        awareness.reset_for_test()

    def test_focus_on_her_does_not_replace_the_real_app(self):
        fx = json.dumps({"class": "firefox", "title": "t", "fullscreen": 0,
                         "at": [0, 0], "size": [10, 10]})
        me = json.dumps({"class": "Remix", "title": "Remix", "fullscreen": 0,
                         "at": [0, 0], "size": [10, 10]})
        with mock.patch.object(awareness, "_hyprctl", return_value=fx):
            awareness._observe_window(1.0)
        with mock.patch.object(awareness, "_hyprctl", return_value=me):
            awareness._observe_window(2.0)
        self.assertEqual("firefox", awareness.current()["app"])


class GroundingTests(unittest.TestCase):
    def setUp(self):
        awareness.reset_for_test()
        self.a = around([ME, FIREFOX, KITTY, OTHER_WS])
        self.aware = {"app": "firefox", "around": self.a, "idle_s": 1.0}

    def test_facts_lists_windows_with_directions_and_no_blanket_denial(self):
        t = grounding.facts("cpu 5%", "a laptop", None, self.aware)
        self.assertIn("firefox", t)
        self.assertIn("to my left", t)
        self.assertIn("discord: on workspace 3", t)
        self.assertIn("any window not on your list", t)
        self.assertNotIn("other windows, workspaces", t)

    def test_no_surroundings_keeps_old_denial(self):
        t = grounding.facts("cpu 5%", "a laptop", None, {"app": "firefox"})
        self.assertNotIn("The other windows around yours", t)

    def test_check_allows_windows_she_knows_and_still_flags_inventions(self):
        ok = grounding.check("That kitty window to my right is yelling.", "",
                             "", None, self.aware)
        self.assertEqual([], ok)
        ok2 = grounding.check("Your browser is huge.", "", "", None, self.aware)
        self.assertEqual([], ok2)
        bad = grounding.check("You have forty tabs open in there.", "", "",
                              None, self.aware)
        self.assertTrue(any("tab" in p for p in bad))
        bad2 = grounding.check("Chromium is eating you alive.", "", "", None,
                               self.aware)
        self.assertTrue(any("chromium" in p for p in bad2))

    def test_without_surroundings_window_talk_is_still_flagged(self):
        bad = grounding.check("Another window is open.", "", "", None,
                              {"app": "firefox"})
        self.assertTrue(any("window" in p for p in bad))


if __name__ == "__main__":
    unittest.main()
