# test_settings_catalog.py — the settings TUI's data layer
# (brain/settings_catalog.py), offline and hermetic:
#   * the catalog matches config.py: every knob's attr exists, every env
#     key is one config actually reads (REMIX_* — or the bare names read
#     via os.environ.get directly). Guards against the bare-name bug the
#     old plain menu had (it wrote TEMPER=, config reads REMIX_TEMPER).
#   * parse/format round-trips write lines config.py will re-read
#   * SettingsModel stages, unstages, diffs and writes .env preserving
#     everything else byte for byte (settings.set_key semantics)
#   * secrets are never present in any display/diff string
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from brain import config, settings_catalog as catalog  # noqa: E402

# env keys config.py reads WITHOUT the REMIX_ prefix (os.environ.get
# directly, see config.py's Jev block) — everything else must be REMIX_*.
_BARE_ATTRS = {"JEV_API_KEY", "JEV_URL", "JEV_MODEL", "JEV_TIMEOUT_S"}
# attrs whose env name is SHORTER than REMIX_<attr> (config._env reads
# REMIX_PTT / REMIX_TTS / REMIX_URGENT; the attr names have _ENABLED/_D).
_SHORT_ENV = {"PTT_ENABLED": "REMIX_PTT", "TTS_ENABLED": "REMIX_TTS",
              "URGENT_ENABLED": "REMIX_URGENT"}
# attrs built dynamically by config._providers() — no static attr exists;
# live values come from catalog._SPECIAL_LIVE instead.
_DYNAMIC_ATTRS = {"LLM_BACKUP1_URL", "LLM_BACKUP1_MODEL",
                  "LLM_BACKUP1_API_KEY"}


class CatalogIntegrity(unittest.TestCase):
    """The catalog can't drift away from config.py silently."""

    def test_every_attr_exists_on_config(self):
        for knob in catalog.ALL_KNOBS:
            if knob.attr in _DYNAMIC_ATTRS:
                continue           # covered by the _SPECIAL_LIVE test below
            self.assertTrue(hasattr(config, knob.attr),
                            f"{knob.env}: config.{knob.attr} missing")

    def test_dynamic_attrs_have_special_live_handlers(self):
        self.assertEqual(_DYNAMIC_ATTRS, set(catalog._SPECIAL_LIVE))

    def test_env_keys_are_readable_names(self):
        for knob in catalog.ALL_KNOBS:
            if knob.attr in _BARE_ATTRS:
                self.assertEqual(knob.attr, knob.env,
                                 f"{knob.attr} is read bare; env must be "
                                 f"'{knob.attr}'")
                continue
            expected = _SHORT_ENV.get(knob.attr, f"REMIX_{knob.attr}")
            self.assertEqual(
                expected, knob.env,
                f"{knob.env} would never be read; config reads "
                f"{expected} (or CORE_...) — this is the bare-name bug "
                f"the old menu had, coming back")

    def test_env_keys_unique(self):
        envs = [knob.env for knob in catalog.ALL_KNOBS]
        self.assertEqual(len(envs), len(set(envs)),
                         "duplicate env keys in the catalog")

    def test_kinds_valid_and_help_present(self):
        for knob in catalog.ALL_KNOBS:
            ok = knob.kind in ("bool", "int", "float", "str", "secret",
                               "pathlist") or (
                isinstance(knob.kind, tuple) and knob.kind
                and knob.kind[0] == "choice" and len(knob.kind) > 1)
            self.assertTrue(ok, f"{knob.env}: bad kind {knob.kind!r}")
            self.assertTrue(knob.help.strip(), f"{knob.env}: no help")
            self.assertTrue(
                knob.hint == "" or knob.hint.startswith("live:"),
                f"{knob.env}: hint must be '' or 'live: ...'")

    def test_secrets_flagged_by_name(self):
        for knob in catalog.ALL_KNOBS:
            should = any(s in knob.env for s in ("KEY", "TOKEN", "SECRET"))
            self.assertEqual(should, knob.secret,
                             f"{knob.env}: secret flag mismatch")
        self.assertTrue(any(k.secret for k in catalog.ALL_KNOBS),
                        "expected some secret knobs")

    def test_every_choice_value_parseable(self):
        """Each choice option must round-trip. The live value check is
        skipped when a real REMIX_*/CORE_* override (or a host .env, which
        the loader folds into os.environ) set something off-menu — the
        TUI then shows the odd value as-is, which is honest."""
        for knob in catalog.ALL_KNOBS:
            if isinstance(knob.kind, tuple) and knob.kind[0] == "choice":
                for option in knob.kind[1:]:
                    self.assertEqual(option, catalog.parse_input(knob,
                                                                 option))
                overridden = os.environ.get(f"REMIX_{knob.attr}") \
                    or os.environ.get(f"CORE_{knob.attr}")
                if overridden:
                    self.skipTest(f"{knob.attr} overridden in environment")
                live = catalog.canonical_value(
                    knob, catalog.live_value(knob))
                self.assertIn(live, knob.kind[1:],
                              f"{knob.env}: live value {live!r} is not an "
                              f"option the TUI offers")


class ParseFormat(unittest.TestCase):
    def _knob(self, env):
        return catalog.KNOB_BY_ENV[env]

    def test_bool_parse_canonical(self):
        k = self._knob("REMIX_PROACTIVE")
        for raw, want in [("on", "1"), ("1", "1"), ("true", "1"),
                          ("yes", "1"), ("off", "0"), ("0", "0"),
                          ("no", "0"), ("false", "0")]:
            self.assertEqual(want, catalog.parse_input(k, raw))
        for bad in ("", "maybe", "2", "on off"):
            with self.assertRaises(ValueError):
                catalog.parse_input(k, bad)

    def test_int_float_parse(self):
        self.assertEqual("160", catalog.parse_input(self._knob(
            "REMIX_TTS_SPEED"), "160"))
        self.assertEqual("12", catalog.parse_input(self._knob(
            "REMIX_TTS_SPEED"), "12.9"))          # menu semantics: int()
        with self.assertRaises(ValueError):
            catalog.parse_input(self._knob("REMIX_TTS_SPEED"), "fast")
        self.assertEqual("0.15", catalog.parse_input(self._knob(
            "REMIX_QUIET_VOLUME"), "0.15"))
        self.assertEqual("600", catalog.parse_input(self._knob(
            "REMIX_BODY_REFRESH_S"), "600.0"))

    def test_choice_rejects_stray_values(self):
        k = self._knob("REMIX_TITLES")
        with self.assertRaises(ValueError):
            catalog.parse_input(k, "everything")

    def test_pathlist_format(self):
        k = self._knob("REMIX_SCREENSHOT_DIRS")
        self.assertEqual("a:b",
                         catalog.format_value(k, ("a", "b")))

    def test_format_float_trims_trailing_zero(self):
        k = self._knob("REMIX_BODY_REFRESH_S")
        self.assertEqual("600", catalog.format_value(k, 600.0))

    def test_display_value_never_shows_secrets(self):
        for knob in catalog.ALL_KNOBS:
            if knob.secret:
                shown = catalog.display_value(knob, {knob.env: "hunter2"})
                self.assertNotIn("hunter2", shown)
                self.assertIn(catalog.display_value(knob, {}),
                              ("set", "unset", "cleared (staged)",
                               "staged"))


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.env = Path(self.dir) / ".env"
        self.env.write_text(
            "# remix settings — hand-written header, kept forever\n"
            "REMIX_MOCK=1\n"
            "REMIX_JEV_MAX_PER_HOUR=10\n\n"
            "# secrets below — never touched by the TUI\n"
            "REMIX_LLM_API_KEY=secret-do-not-print\n"
        )
        self.model = catalog.SettingsModel(env_path=self.env)

    def _write_and_reload_text(self) -> str:
        self.model.apply()
        return self.env.read_text()

    def test_stage_then_apply_updates_existing_line(self):
        knob = catalog.KNOB_BY_ENV["REMIX_MOCK"]
        self.model.stage(knob, "0")
        self.assertTrue(self.model.is_staged(knob))
        text = self._write_and_reload_text()
        self.assertIn("REMIX_MOCK=0", text)
        self.assertNotIn("REMIX_MOCK=1", text)

    def test_apply_appends_new_keys_after_comment(self):
        knob = catalog.KNOB_BY_ENV["REMIX_TEMPER"]
        self.model.stage(knob, "soft")
        text = self._write_and_reload_text()
        self.assertIn("# set by brain.settings\nREMIX_TEMPER=soft", text)

    def test_first_definition_wins_updated_in_place(self):
        """A .env with the same key twice: the FIRST definition is the
        one config honors (config's loader semantics), so the TUI must
        update the first and leave the second untouched."""
        self.env.write_text("REMIX_TEMPER=soft\n"
                            "REMIX_TEMPER=cranky\n")
        knob = catalog.KNOB_BY_ENV["REMIX_TEMPER"]
        self.model.stage(knob, "balanced")
        self.model.apply()
        lines = [ln.strip() for ln in self.model.load_lines()]
        self.assertEqual("REMIX_TEMPER=balanced", lines[0])
        self.assertEqual("REMIX_TEMPER=cranky", lines[1])

    def test_other_lines_preserved_byte_for_byte(self):
        knob = catalog.KNOB_BY_ENV["REMIX_MOCK"]
        self.model.stage(knob, "0")
        self._write_and_reload_text()
        text = self.env.read_text()
        self.assertIn("# remix settings — hand-written header, kept forever",
                      text)
        self.assertIn("REMIX_LLM_API_KEY=secret-do-not-print", text)
        self.assertIn("REMIX_JEV_MAX_PER_HOUR=10", text)

    def test_apply_with_nothing_staged_writes_nothing(self):
        before = self.env.read_text()
        self.assertEqual(0, self.model.apply())
        self.assertEqual(before, self.env.read_text())

    def test_unstage_and_unstage_all(self):
        mock = catalog.KNOB_BY_ENV["REMIX_MOCK"]
        temper = catalog.KNOB_BY_ENV["REMIX_TEMPER"]
        self.model.stage(mock, "0")
        self.model.stage(temper, "soft")
        self.model.unstage(mock)
        self.assertEqual(["REMIX_TEMPER"], list(self.model.staged))
        self.model.unstage_all()
        self.assertEqual({}, self.model.staged)
        self.assertEqual([], self.model.diff())

    def test_stage_same_as_env_is_a_noop(self):
        """Staging what .env already says must not dirty the set."""
        knob = catalog.KNOB_BY_ENV["REMIX_TEMPER"]
        self.env.write_text("REMIX_TEMPER=cranky\n")
        self.model.stage(knob, "cranky")
        self.assertEqual({}, self.model.staged)

    def test_stage_live_value_over_a_stale_env_line_stages(self):
        """.env says soft, the real environment won (live = cranky).
        Staging cranky pins the LIVE value over the stale line — a real
        change the user asked for."""
        knob = catalog.KNOB_BY_ENV["REMIX_TEMPER"]
        self.env.write_text("REMIX_TEMPER=soft\n")
        live = catalog.canonical_value(knob, catalog.live_value(knob))
        self.model.stage(knob, live)
        self.assertEqual({"REMIX_TEMPER": live}, self.model.staged)

    def test_diff_hides_secret_values(self):
        knob = catalog.KNOB_BY_ENV["REMIX_LLM_API_KEY"]
        self.model.stage(knob, "new-secret")
        diff = self.model.diff()
        self.assertEqual(1, len(diff))
        env, old, new = diff[0]
        self.assertEqual("REMIX_LLM_API_KEY", env)
        self.assertNotIn("secret-do-not-print", new)
        self.assertNotIn("new-secret", new)
        self.assertNotIn("secret-do-not-print", str(diff))
        # and the write itself does carry it (the file is where it lives)
        self._write_and_reload_text()
        self.assertIn("REMIX_LLM_API_KEY=new-secret", self.env.read_text())

    def test_env_differs_flags_real_env_override(self):
        """REMIX_TEMPER isn't in .env here but IS in the real environment
        of this process (CI never sets it) — if it were, .env edits of
        REMIX_TEMPER would lose, and the TUI must say so."""
        knob = catalog.KNOB_BY_ENV["REMIX_TEMPER"]
        in_real_env = os.environ.get("REMIX_TEMPER") is not None
        self.assertEqual(in_real_env, self.model.env_differs(knob))


class SearchAndHelpers(unittest.TestCase):
    def test_search_matches_name_attr_and_help(self):
        self.assertIn("REMIX_PTT_MODEL",
                      [k.env for k in catalog.search_knobs("whisper")])
        self.assertIn("REMIX_TEMPER",
                      [k.env for k in catalog.search_knobs("personality")])
        hits = catalog.search_knobs("jev hourly cap")
        self.assertIn("REMIX_JEV_MAX_PER_HOUR", [k.env for k in hits])

    def test_search_requires_all_tokens_and_empty_is_empty(self):
        self.assertEqual([], catalog.search_knobs(""))
        self.assertEqual([], catalog.search_knobs("   "))
        self.assertEqual([], catalog.search_knobs("zzz-no-such-knob"))
        # 'cranky' only appears in TEMPER's choices/help
        self.assertIn("REMIX_TEMPER",
                      [k.env for k in catalog.search_knobs("cranky")])

    def test_search_is_case_insensitive(self):
        self.assertIn("REMIX_MOCK",
                      [k.env for k in catalog.search_knobs("MOCK")])

    def test_load_defaults_returns_clean_config_defaults(self):
        defaults = catalog.load_defaults(timeout_s=30)
        self.assertTrue(defaults, "defaults subprocess returned nothing")
        self.assertEqual("cranky", defaults.get("TEMPER"))
        self.assertEqual("0", defaults.get("MOCK"))
        self.assertEqual("glm-4.7-flash", defaults.get("LLM_MODEL"))
        missing = ({knob.attr for knob in catalog.ALL_KNOBS}
                   - set(defaults))
        self.assertEqual(set(), missing,
                         "defaults probe missed some catalog attrs")

    def test_brain_is_up_false_on_closed_port(self):
        self.assertFalse(catalog.brain_is_up(host="127.0.0.1", port=1,
                                             timeout_s=0.2))

    def test_status_line_honest(self):
        line = catalog.status_line()
        self.assertIn("jev", line)
        self.assertIn("providers", line)
        self.assertIn("vitals", line)


if __name__ == "__main__":
    unittest.main()
