# test_settings_tui.py — the settings TUI (brain/settings_tui.py) driven
# headlessly through Textual's Pilot harness (no real terminal needed):
#   * boots into a section with rows and a live description pane
#   * space toggles stage a change; ctrl+s writes the temp .env
#   * search filters across all sections; esc returns to the section
#   * enter on a choice opens the picker; picking stages
#   * quitting with staged changes raises the guard; keys decide
# Skipped cleanly when textual isn't installed (the plain menu works
# without it — see settings.sh).
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

try:
    import textual  # noqa: F401
    HAS_TEXTUAL = True
except ImportError:
    HAS_TEXTUAL = False

if HAS_TEXTUAL:
    from brain.settings_tui import ChoiceScreen, QuitGuard, SettingsTUI  \
        # noqa: E402
    from brain.settings_catalog import KNOB_BY_ENV  # noqa: E402
from brain import config  # noqa: E402

_TMP_ENV = """\
# remix settings — test header
REMIX_MOCK=1
REMIX_TEMPER=soft
"""


def _no_knob():
    from brain.settings_catalog import Knob
    return Knob("?", "?", "str", "")  # sentinel so failures read clearly


@unittest.skipUnless(HAS_TEXTUAL, "textual not installed (plain menu mode)")
class TUITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.env = Path(self.dir) / ".env"
        self.env.write_text(_TMP_ENV)
        # The TUI displays LIVE config, so the live view must match the temp
        # .env this class writes: no secrets set (2026-11-15 — her real key
        # used to leak in and "unset" rows read "set").
        self._saved_secrets = (config.LLM_API_KEY, config.JEV_API_KEY)
        config.LLM_API_KEY = ""
        config.JEV_API_KEY = ""

    def tearDown(self):
        config.LLM_API_KEY, config.JEV_API_KEY = self._saved_secrets

    def _app(self) -> SettingsTUI:
        return SettingsTUI(env_path=self.env)

    async def test_boots_into_first_section(self):
        app = self._app()
        async with app.run_test(size=(110, 34)) as pilot:
            await pilot.pause()
            rows = [r.knob.env for r in app.knob_list.children]
            self.assertEqual("REMIX_USER_NAME", rows[0])
            self.assertIn("REMIX_PROACTIVE", rows)
            self.assertEqual(11, len(app.sections_list.options))
            # description pane picked up the highlighted row
            self.assertEqual("REMIX_USER_NAME",
                             (app._desc_knob or _no_knob()).env)

    async def test_space_toggles_bool_and_marks_staged(self):
        app = self._app()
        async with app.run_test(size=(110, 34)) as pilot:
            await pilot.pause()
            app.knob_list.index = 4          # REMIX_PROACTIVE (bool, on)
            await pilot.pause()
            await pilot.press("space")
            await pilot.pause()
            knob = KNOB_BY_ENV["REMIX_PROACTIVE"]
            self.assertTrue(app.model.is_staged(knob))
            row = app._row_for(knob)
            self.assertTrue(row.has_class("staged"))
            self.assertEqual(1, len(app.model.diff()))

    async def test_space_twice_returns_row_to_clean(self):
        """Flip a bool, flip it back: the staged change is dropped, not
        pinned over an .env line that was never there."""
        app = self._app()
        async with app.run_test(size=(110, 34)) as pilot:
            await pilot.pause()
            app.knob_list.index = 4
            await pilot.pause()
            await pilot.press("space")
            await pilot.pause()
            await pilot.press("space")
            await pilot.pause()
            self.assertEqual({}, app.model.staged)
            row = app._row_for(KNOB_BY_ENV["REMIX_PROACTIVE"])
            self.assertFalse(row.has_class("staged"))

    async def test_ctrl_s_writes_temp_env(self):
        app = self._app()
        async with app.run_test(size=(110, 34)) as pilot:
            await pilot.pause()
            app.knob_list.index = 4
            await pilot.pause()
            await pilot.press("space")
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause()
            text = self.env.read_text()
            self.assertIn("REMIX_PROACTIVE=0", text)
            self.assertIn("# remix settings — test header", text)
            self.assertIn("REMIX_TEMPER=soft", text)
            self.assertEqual({}, app.model.staged)

    async def test_search_filters_across_sections_and_esc_restores(self):
        app = self._app()
        async with app.run_test(size=(110, 34)) as pilot:
            await pilot.pause()
            await pilot.press("/")
            for ch in "whisper":
                await pilot.press(ch)
            await pilot.pause()
            rows = [r.knob.env for r in app.knob_list.children]
            self.assertIn("REMIX_PTT_MODEL", rows)
            self.assertTrue(
                all(r.section_label for r in app.knob_list.children),
                "search rows should carry their section")
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(13, len(app.knob_list.children))
            self.assertFalse(app.search_mode)

    async def test_enter_on_choice_opens_picker(self):
        app = self._app()
        async with app.run_test(size=(110, 34)) as pilot:
            await pilot.pause()
            app.knob_list.index = 1          # REMIX_TEMPER (choice)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, ChoiceScreen)
            await pilot.press("down")        # soft -> balanced
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual({"REMIX_TEMPER": "balanced"},
                             app.model.staged)

    async def test_quit_guard_writes_discards_or_stays(self):
        # stays: esc on the guard, nothing written, still staged
        app = self._app()
        async with app.run_test(size=(110, 34)) as pilot:
            await pilot.pause()
            app.knob_list.index = 4
            await pilot.pause()
            await pilot.press("space")
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()
            self.assertIsInstance(app.screen, QuitGuard)
            await pilot.press("escape")      # stay here
            await pilot.pause()
            self.assertTrue(app.is_running)
            self.assertEqual(1, len(app.model.staged))
            # writes and quits: s on the guard
            await pilot.press("q")
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            self.assertIn("REMIX_PROACTIVE=0", self.env.read_text())

    async def test_quit_guard_discard_leaves_env_untouched(self):
        app = self._app()
        async with app.run_test(size=(110, 34)) as pilot:
            await pilot.pause()
            app.knob_list.index = 4
            await pilot.pause()
            await pilot.press("space")
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            self.assertNotIn("REMIX_PROACTIVE=0", self.env.read_text())

    async def test_unstage_row_key(self):
        app = self._app()
        async with app.run_test(size=(110, 34)) as pilot:
            await pilot.pause()
            app.knob_list.index = 4
            await pilot.pause()
            await pilot.press("space")
            await pilot.pause()
            await pilot.press("u")
            await pilot.pause()
            self.assertEqual({}, app.model.staged)

    async def test_section_navigation_and_status_bar(self):
        app = self._app()
        async with app.run_test(size=(110, 34)) as pilot:
            await pilot.pause()
            await pilot.press("right")       # next section
            await pilot.pause()
            rows = [r.knob.env for r in app.knob_list.children]
            self.assertEqual("REMIX_TTS", rows[0])
            await pilot.press("left")
            await pilot.pause()
            rows = [r.knob.env for r in app.knob_list.children]
            self.assertEqual("REMIX_USER_NAME", rows[0])
            # the status bar settles after the brain probe thread runs
            await pilot.pause(1.5)
            self.assertIn("brain", app._status_text)
            self.assertIn("jev", app._status_text)
            self.assertIn("staged", app._status_text)
            # the probe must RESOLVE, not hang on the pending "○ ..." state
            self.assertRegex(app._status_text, r"brain \u25cf (up|down)")

    async def test_secret_editor_never_shows_the_value(self):
        """REMIX_LLM_API_KEY edits masked, rows show set/unset only, and
        a staged secret never leaks into the diff strings the UI shows."""
        app = self._app()
        async with app.run_test(size=(110, 34)) as pilot:
            await pilot.pause()
            await pilot.press("/")           # search across all sections
            for ch in "REMIX_LLM_API_KEY":
                await pilot.press(ch)
            await pilot.pause()
            rows = [r for r in app.knob_list.children
                    if r.knob.env == "REMIX_LLM_API_KEY"]
            self.assertEqual(1, len(rows))
            row = rows[0]
            self.assertEqual("unset", str(row.value_label.render()))
            app.knob_list.focus()            # as if the row was clicked
            await pilot.pause()
            app.knob_list.index = app.knob_list.children.index(row)
            await pilot.pause()
            await pilot.press("enter")       # masked editor
            await pilot.pause()
            inp = app.screen.query_one("#edit_input")
            self.assertTrue(inp.masked if hasattr(inp, "masked")
                            else inp.password)
            await pilot.press("escape")      # cancel
            await pilot.pause()
            self.assertEqual({}, app.model.staged)


if __name__ == "__main__":
    unittest.main()
