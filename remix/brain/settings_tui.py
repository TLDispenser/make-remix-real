# settings_tui.py — the settings panel as a real TUI (Phase 7 plan 4.10,
# 2026-11-13 round). Everything config.py can read, in one screen:
# sections on the left, knobs + values in the middle, the selected knob's
# story at the bottom, search over all ~150 knobs on top.
#
# Contract (inherited from brain/settings.py, unchanged):
#   * values shown are LIVE config — what she is actually running on
#   * edits are STAGED (marked amber) until Ctrl+S writes them through
#     settings.set_key: first definition updated in place, everything
#     else in .env preserved byte for byte, secrets never displayed
#   * most knobs need `./restart.sh brain`; the ones with a live slash
#     command say so on the row and in the description pane
#
# Run from remix/:  ./.venv/bin/python -m brain.settings_tui
# (or ./settings.sh, which falls back to the plain menu without textual)
from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, ListItem, ListView, \
    OptionList, Static

from . import config, settings_catalog as catalog
from .settings_catalog import Knob, SECTIONS, SettingsModel

_LONG = 34           # knob name column width
_STAGED = "#fabd2f"  # gruvbox yellow — the "written on save" marker


# --------------------------------------------------------------- screens
class HelpScreen(ModalScreen):
    """? — what every key does, and what the TUI writes where."""

    BINDINGS = [Binding("escape,question_mark", "dismiss_screen",
                        "Close", show=False)]

    def compose(self) -> ComposeResult:
        with Vertical(id="help_box"):
            yield Static(Text.from_markup(
                "[b]remix settings — keys[/b]\n"
                "\n"
                "  arrows / j k   move                    [b]space[/b]   quick "
                "toggle / cycle\n"
                "  [b]enter[/b]          edit the knob           [b]u[/b]       "
                "unstage this row\n"
                "  [b]U[/b]            unstage everything      [b]r[/b]       "
                "stage its default\n"
                "  [b]/[/b]            search all knobs        [b]esc[/b]     "
                "clear search\n"
                "  [b]ctrl+s[/b]        write .env              [b]q[/b]       "
                "quit (guards staged)\n"
                "  [b][ / ][/b]        previous / next section [b]?[/b]       "
                "this help\n"
                "\n"
                "[b]What happens on save[/b]\n"
                "  Values shown are LIVE config. Amber rows are staged and not\n"
                "  written yet; ctrl+s updates the FIRST definition of each key\n"
                "  in remix/.env and preserves every other line byte for byte —\n"
                "  including your keys, which are never displayed here.\n"
                "\n"
                "[b]After saving[/b]\n"
                "  Most knobs need [i]./restart.sh brain[/i]. Rows marked "
                "[i]live[/i]\n"
                "  also have a runtime slash command (/quiet, /aware, "
                "/listen).\n"
                "\n"
                "[b]The \u2260 marker[/b]\n"
                "  .env holds a different raw value than live config — the\n"
                "  real environment or an earlier duplicate line won at load\n"
                "  (first definition wins; the loader names duplicates out "
                "loud)."),
                id="help_text")
            yield Static("esc to close", id="help_foot")

    def action_dismiss_screen(self) -> None:
        self.dismiss()


class EditValueScreen(ModalScreen):
    """Enter — edit one knob's value. Returns the canonical string, or
    None on cancel. Secrets use a masked input and are never echoed."""
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, knob: Knob, current: str):
        super().__init__()
        self._knob = knob
        self._current = current

    def compose(self) -> ComposeResult:
        secret = self._knob.kind == "secret"
        placeholder = ("paste the new value — empty submit CLEARS it"
                       if secret else "new value")
        body = [Static(Text.from_markup(
            f"[b]{self._knob.env}[/b]"
            + ("" if secret else f"   current: {self._current!r}")),
            id="edit_title"),
            Static(self._knob.help, id="edit_help"),
            Input(password=secret, placeholder=placeholder, id="edit_input"),
            Static("", id="edit_err")]
        with Vertical(id="edit_box"):
            yield from body

    def on_mount(self) -> None:
        self.query_one("#edit_input").focus()

    def _submit(self, raw: str) -> None:
        try:
            value = catalog.parse_input(self._knob, raw)
        except ValueError as e:
            self.query_one("#edit_err").update(Text(str(e), style="red"))
            return
        self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit(event.value)


class ChoiceScreen(ModalScreen):
    """Enter on a choice knob — pick one; up/down + enter, or esc."""
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, knob: Knob, current: str):
        super().__init__()
        self._knob = knob
        self._current = current
        self._options = list(knob.kind[1:])

    def compose(self) -> ComposeResult:
        with Vertical(id="choice_box"):
            yield Static(Text.from_markup(
                f"[b]{self._knob.env}[/b]   current: {self._current}"),
                id="edit_title")
            yield Static(self._knob.help, id="edit_help")
            yield OptionList(*self._options, id="choice_list")

    def on_mount(self) -> None:
        self._list = self.query_one("#choice_list")
        if self._current in self._options:
            self._list.highlighted = self._options.index(self._current)
        self._list.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_option_list_option_selected(self, event) -> None:
        self.dismiss(self._options[event.option_index])


class QuitGuard(ModalScreen):
    """q / esc with staged changes — write them, discard them, or stay."""
    BINDINGS = [Binding("escape", "stay", "Stay", show=False),
                Binding("s", "save_quit", "Write + quit", show=False),
                Binding("d", "discard_quit", "Discard + quit", show=False)]

    def __init__(self, diff: list):
        super().__init__()
        self._diff = diff

    def compose(self) -> ComposeResult:
        lines = Text()
        lines.append(f"{len(self._diff)} staged change(s) not written:\n\n",
                     style="bold")
        for env, old, new in self._diff[:8]:
            lines.append(f"  {env}\n")
            lines.append(f"    {old} \u2192 {new}\n", style="dim")
        if len(self._diff) > 8:
            lines.append(f"  ... and {len(self._diff) - 8} more\n",
                         style="dim")
        with Vertical(id="guard_box"):
            yield Static(lines)
            yield Static("s  write .env and quit    d  discard and quit"
                         "\nesc  stay here", id="guard_keys")

    def action_save_quit(self) -> None:
        self.dismiss("save")

    def action_discard_quit(self) -> None:
        self.dismiss("discard")

    def action_stay(self) -> None:
        self.dismiss("cancel")


# ----------------------------------------------------------------- rows
class KnobRow(ListItem):
    """One knob: name, value, marker (staged / env-differs), hint."""
    def __init__(self, knob: Knob, model: SettingsModel,
                 section_label: str = ""):
        self.knob = knob
        self.model = model
        self.section_label = section_label
        super().__init__()
        self.name_label = Static(knob.env, id="kn_name", markup=False)
        self.value_label = Static("", id="kn_val", markup=False)
        self.mark_label = Static("", id="kn_mark", markup=False)
        self.hint_label = Static(self._hint_text(), id="kn_hint",
                                 markup=False)

    def _hint_text(self) -> str:
        if self.section_label:
            return f"in {self.section_label}"
        return f"live: {self.knob.hint.split(':', 1)[1].strip()}" \
            if self.knob.hint else ""

    def refresh_value(self) -> None:
        staged = self.model.staged
        self.value_label.update(catalog.display_value(self.knob, staged))
        if self.model.is_staged(self.knob):
            mark, style = "\u25cf staged", _STAGED
        elif self.model.env_differs(self.knob):
            mark, style = "\u2260", "dim"
        else:
            mark, style = "", ""
        self.mark_label.update(Text(mark, style=style))
        self.set_class(self.model.is_staged(self.knob), "staged")
        self.set_class(mark == "\u2260", "differs")

    def compose(self) -> ComposeResult:
        yield Horizontal(self.name_label, self.value_label,
                         self.mark_label, self.hint_label)


class SectionList(OptionList):
    """The left rail. Subclassed so its keys don't leak while typing."""
    BINDINGS = [
        Binding("slash", "goto_search", "Search", show=False),
        Binding("question_mark", "app.help", "Help", show=False),
        Binding("q", "app.quit_app", "Quit", show=False),
        Binding("left,bracketleft", "app.prev_section", "Prev section",
                show=False),
        Binding("right,bracketright", "app.next_section", "Next section",
                show=False),
    ]

    def action_goto_search(self) -> None:
        self.app.action_focus_search()


class KnobList(ListView):
    """The knob rows, vim keys included (when the list has focus)."""
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("slash", "goto_search", "Search", show=False),
        Binding("question_mark", "app.help", "Help", show=False),
        Binding("q", "app.quit_app", "Quit", show=False),
        Binding("u", "app.unstage_row", "Unstage row", show=False),
        Binding("U", "app.unstage_all", "Unstage all", show=False),
        Binding("r", "app.stage_default", "Stage default", show=False),
        Binding("space", "quick_action", "Toggle / cycle", show=False),
        Binding("left,bracketleft", "app.prev_section", "Prev section",
                show=False),
        Binding("right,bracketright", "app.next_section", "Next section",
                show=False),
    ]

    def action_goto_search(self) -> None:
        self.app.action_focus_search()

    def action_quick_action(self) -> None:
        self.app.quick_action()


class SearchInput(Input):
    BINDINGS = [
        Binding("escape", "clear_search", "Clear", show=False),
    ]

    def action_clear_search(self) -> None:
        self.value = ""
        self.app.exit_search_mode()


# ------------------------------------------------------------------ app
class SettingsTUI(App):
    TITLE = "remix settings"
    SUBTITLE = str(catalog.ENV_PATH)
    CSS = """
    Screen { background: $background; }
    #status { height: 1; background: $panel; color: $foreground-muted;
              padding: 0 1; }
    #status.dim { color: $foreground-darken-1; }
    #body { height: 1fr; }
    #sidebar { width: 26; border-right: solid $panel; padding: 0; }
    #sidebar OptionList { background: $background; border: none; }
    #sidebar OptionList > .option-list--option { padding: 0 1; }
    #sidebar .searching { opacity: 0.45; }
    #main { width: 1fr; }
    #search { border: none; height: 3; background: $background;
              padding: 0 1; }
    #search:focus { border-left: tall $accent; }
    #knobs { height: 1fr; background: $background; border: none; }
    #knobs > ListItem { background: $background; height: 1; }
    #knobs > ListItem:hover { background: $surface; }
    #knobs > ListItem.-highlighted { background: $surface; }
    #knobs > ListItem.-highlighted #kn_name { color: $accent; }
    KnobRow > Horizontal { height: 1; width: 1fr; }
    #kn_name { width: %s; color: $foreground; }
    #kn_val { width: 1fr; color: $foreground-muted; }
    #kn_mark { width: 11; color: $foreground-muted; }
    #kn_hint { width: auto; color: $foreground-darken-1; }
    KnobRow.staged #kn_val { color: %s; text-style: bold; }
    KnobRow.staged #kn_mark { color: %s; }
    KnobRow.differs #kn_val { color: $secondary; }
    #desc { height: 6; border-top: solid $panel; background: $background;
            padding: 0 1; }
    #desc.staged { border-top: solid %s; }
    #edit_box { width: 76; height: auto; max-height: 80%%; margin: 1 4;
                background: $surface; border: round $accent; padding: 1 2; }
    #edit_title { margin-bottom: 1; }
    #edit_help { color: $foreground-muted; margin-bottom: 1; }
    #edit_err { color: $error; height: auto; }
    #choice_box { width: 60; height: auto; margin: 1 4; background: $surface;
                  border: round $accent; padding: 1 2; }
    #choice_list { height: auto; max-height: 10; border: none;
                   background: $surface; }
    #guard_box { width: 70; height: auto; margin: 1 4; background: $surface;
                 border: round $warning; padding: 1 2; }
    #guard_keys { color: $foreground-muted; margin-top: 1; }
    #help_box { width: 80%%; height: 90%%; margin: 1 4; background: $surface;
                border: round $accent; padding: 1 2; }
    #help_text { color: $foreground; }
    #help_foot { dock: bottom; height: 1; color: $foreground-darken-1;
                 padding: 0 1; }
    """ % (_LONG, _STAGED, _STAGED, _STAGED)

    BINDINGS = [
        Binding("ctrl+s", "save", "Write .env"),
        Binding("slash", "focus_search", "Find", key_display="/"),
        Binding("q", "quit_app", "Quit"),
        Binding("question_mark", "help", "Help"),
        Binding("u", "unstage_row", "Unstage row", show=False),
        Binding("U", "unstage_all", "Unstage all", show=False),
        Binding("r", "stage_default", "Stage default", show=False),
        Binding("left,bracketleft", "prev_section", "Prev section",
                key_display="\u2190", show=False),
        Binding("right,bracketright", "next_section", "Next section",
                key_display="\u2192", show=False),
        Binding("ctrl+q", "force_quit", "Quit", show=False),
    ]

    def __init__(self, env_path=catalog.ENV_PATH):
        super().__init__()
        self.model = SettingsModel(env_path)
        self.defaults: dict = {}
        self.section_index = 0
        self.search_mode = False
        self._brain_up: "bool | None" = None   # last probe result
        self._desc_knob: "Knob | None" = None   # last knob described
        self._status_text = ""                  # last status strip

    # ---- compose -------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="status")
        with Horizontal(id="body"):
            yield Vertical(
                OptionList(*self._section_options(), id="sections"),
                id="sidebar")
            yield Vertical(
                SearchInput(placeholder="  /  search all knobs", id="search"),
                KnobList(id="knobs"),
                Static("", id="desc"),
                id="main")
        yield Footer()

    def on_mount(self) -> None:
        self.theme = "gruvbox"
        self._select_section(0)
        self.query_one("#knobs").focus()
        self._refresh_status()
        self._load_defaults_worker()
        self.set_interval(30.0, self._refresh_status)

    # ---- helpers -------------------------------------------------------
    @property
    def sections_list(self) -> OptionList:
        return self.query_one("#sections", OptionList)

    @property
    def knob_list(self) -> KnobList:
        return self.query_one("#knobs", KnobList)

    @property
    def desc(self) -> Static:
        return self.query_one("#desc", Static)

    def _section_options(self) -> list:
        out = []
        for sec in SECTIONS:
            n = len(sec.knobs)
            out.append(f"{sec.title}  ({n})")
        return out

    def _current_knobs(self) -> list:
        if self.search_mode:
            query = self.query_one("#search", SearchInput).value
            return catalog.search_knobs(query)
        return list(SECTIONS[self.section_index].knobs)

    def _row_for(self, knob: Knob) -> "KnobRow | None":
        for row in self.knob_list.children:
            if isinstance(row, KnobRow) and row.knob.env == knob.env:
                return row
        return None

    # ---- list building -------------------------------------------------
    def _rebuild_list(self, keep_env: "str | None" = None) -> None:
        knobs = self._current_knobs()
        was_scrolled = self.knob_list.scroll_offset.y
        self.knob_list.clear()
        rows = []
        for knob in knobs:
            sec_label = ""
            if self.search_mode:
                for sec in SECTIONS:
                    if knob in sec.knobs:
                        sec_label = sec.title
                        break
            rows.append(KnobRow(knob, self.model, sec_label))
        self.knob_list.extend(rows)
        for row in rows:
            row.refresh_value()
        target = 0
        if keep_env:
            for i, row in enumerate(rows):
                if row.knob.env == keep_env:
                    target = i
                    break
        if rows:
            self.knob_list.index = target
        else:
            self.knob_list.index = None
        if not self.search_mode and was_scrolled and not keep_env:
            self.knob_list.scroll_y = was_scrolled
        self._refresh_desc()

    # ---- description pane ----------------------------------------------
    def _refresh_desc(self, knob: "Knob | None" = None) -> None:
        if knob is None:
            rows = [c for c in self.knob_list.children
                    if isinstance(c, KnobRow)]
            lv = self.knob_list
            if lv.index is not None and 0 <= lv.index < len(rows):
                knob = rows[lv.index].knob
            else:
                knob = None
        t = Text()
        if knob is None:
            if self.search_mode:
                query = self.query_one("#search", SearchInput).value
                t.append(f"no knob matches {query!r}", style="dim")
            else:
                sec = SECTIONS[self.section_index]
                t.append(f"{sec.title} — ", style="bold")
                t.append(sec.blurb, style="dim")
            self.desc.update(t)
            self.desc.set_class(False, "staged")
            return
        t.append(f"{knob.env}", style="bold")
        t.append("   ")
        t.append(catalog.display_value(knob, self.model.staged),
                 style=_STAGED if self.model.is_staged(knob) else "")
        t.append("\n")
        t.append(knob.help, style="")
        t.append("\n")
        bits = []
        default = self.defaults.get(knob.attr)
        if default is not None and knob.kind != "secret":
            shown = default if default != "" else "(empty)"
            bits.append(f"default {shown}")
        if knob.kind == "secret":
            bits.append("secret — value never displayed")
        if knob.hint:
            bits.append(f"{knob.hint} — no restart needed")
        else:
            bits.append("needs ./restart.sh brain")
        if self.model.env_differs(knob):
            raw = self.model.env_raw(knob)
            bits.append(f".env has {raw!r} \u2260 live (real environment "
                        f"or an earlier line won)")
        if self.model.is_staged(knob):
            bits.append(f"staged \u2192 {self.model.staged[knob.env]!r} "
                        f"(ctrl+s writes)")
        t.append("   ".join(bits), style="dim")
        self.desc.update(t)
        self.desc.set_class(self.model.is_staged(knob), "staged")
        self._desc_knob = knob

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if isinstance(item, KnobRow):
            self._refresh_desc(item.knob)

    # ---- status bar ------------------------------------------------------
    def _refresh_status(self) -> None:
        self._brain_probe_worker()
        self._render_status()

    def _render_status(self) -> None:
        staged = len(self.model.staged)
        env_note = "" if self.model.env_path.exists() \
            else "   no .env yet — ctrl+s creates it"
        s = Text()
        s.append("  brain ")
        if self._brain_up is None:
            s.append("\u25cb \u2026", style="dim")
        elif self._brain_up:
            s.append("\u25cf up", style="green")
        else:
            s.append("\u25cf down", style="red")
        s.append(f"   jev {'on' if config.JEV_ENABLED else 'off'}",
                 style="dim")
        s.append(f"   llm providers {len(config.PROVIDERS)}", style="dim")
        s.append(f"   vitals {'MOCK' if config.MOCK else 'real'}",
                 style="dim")
        s.append(f"   staged {staged}", style="bold" if staged else "dim")
        if staged:
            s.append("   ctrl+s writes", style=_STAGED)
        s.append(env_note, style="dim")
        self._status_text = s.plain
        self.query_one("#status", Static).update(s)

    @work(thread=True, exclusive=True, group="brain-probe")
    def _brain_probe_worker(self) -> None:
        up = catalog.brain_is_up()
        self._brain_up = up
        self.call_from_thread(self._render_status)

    @work(thread=True, exclusive=True, group="defaults")
    def _load_defaults_worker(self) -> None:
        defaults = catalog.load_defaults()
        self.defaults = defaults
        self.call_from_thread(self._refresh_desc)

    # ---- search ----------------------------------------------------------
    def action_focus_search(self) -> None:
        self.query_one("#search", SearchInput).focus()

    def exit_search_mode(self) -> None:
        self.search_mode = False
        self.sections_list.set_class(False, "searching")
        self.knob_list.focus()
        self._rebuild_list()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "search":
            return
        query = event.value.strip()
        self.search_mode = bool(query)
        self.sections_list.set_class(self.search_mode, "searching")
        self._rebuild_list()

    # ---- sections --------------------------------------------------------
    def _select_section(self, index: int) -> None:
        self.section_index = max(0, min(index, len(SECTIONS) - 1))
        self.sections_list.highlighted = self.section_index
        self._rebuild_list()

    def action_prev_section(self) -> None:
        if not self.search_mode:
            self._select_section(self.section_index - 1)

    def action_next_section(self) -> None:
        if not self.search_mode:
            self._select_section(self.section_index + 1)

    def on_option_list_option_selected(self, event) -> None:
        if event.option_list.id == "sections":
            self.search_mode = False
            self.query_one("#search", SearchInput).value = ""
            self._select_section(event.option_index)

    # ---- editing ---------------------------------------------------------
    def _selected_knob(self) -> "Knob | None":
        lv = self.knob_list
        rows = [c for c in lv.children if isinstance(c, KnobRow)]
        if lv.index is None or not (0 <= lv.index < len(rows)):
            return None
        return rows[lv.index].knob

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, KnobRow):
            self.edit_knob(item.knob)

    def quick_action(self) -> None:
        """Space — flip bools, cycle choices, nothing to type."""
        knob = self._selected_knob()
        if knob is None:
            return
        if knob.kind == "bool":
            current = self._shown_value(knob)
            self._stage_or_drop(knob, "0" if current == "1" else "1")
        elif isinstance(knob.kind, tuple) and knob.kind[0] == "choice":
            options = list(knob.kind[1:])
            current = self._shown_value(knob)
            nxt = options[(options.index(current) + 1) % len(options)] \
                if current in options else options[0]
            self._stage_or_drop(knob, nxt)
        else:
            self.edit_knob(knob)
            return
        self._after_stage(knob)

    def _shown_value(self, knob: Knob) -> str:
        """The staged value if any, else the live one, canonical."""
        staged = self.model.staged.get(knob.env)
        if staged is not None:
            return staged
        return catalog.canonical_value(knob, catalog.live_value(knob))

    def _stage_or_drop(self, knob: Knob, value: str) -> None:
        """Stage the value — unless it just undoes the round trip (back
        to the live value and .env has no line to fix), which unstages
        instead: space twice returns you to a clean row."""
        live = catalog.canonical_value(knob, catalog.live_value(knob))
        if value == live and self.model.env_raw(knob) is None:
            self.model.unstage(knob)
        else:
            self.model.stage(knob, value)

    def edit_knob(self, knob: Knob) -> None:
        current = catalog.display_value(knob, self.model.staged)
        if knob.kind == "secret":
            self.push_screen(EditValueScreen(knob, ""),
                             self._edit_callback(knob))
        elif isinstance(knob.kind, tuple) and knob.kind[0] == "choice":
            self.push_screen(ChoiceScreen(knob, current),
                             self._edit_callback(knob))
        else:
            self.push_screen(EditValueScreen(knob, current),
                             self._edit_callback(knob))

    def _edit_callback(self, knob: Knob):
        def _apply(value):
            if value is None:
                return
            self._stage_or_drop(knob, value)
            self._after_stage(knob)
        return _apply

    def _after_stage(self, knob: Knob) -> None:
        row = self._row_for(knob)
        if row is not None:
            row.refresh_value()
        self._refresh_desc(knob)
        self._render_status()

    # ---- staging keys ----------------------------------------------------
    def action_unstage_row(self) -> None:
        knob = self._selected_knob()
        if knob is None:
            return
        self.model.unstage(knob)
        self._after_stage(knob)
        self.notify(f"{knob.env} — staged change dropped",
                    severity="warning", timeout=2)

    def action_unstage_all(self) -> None:
        if not self.model.staged:
            return
        self.model.unstage_all()
        for row in self.knob_list.children:
            if isinstance(row, KnobRow):
                row.refresh_value()
        self._refresh_desc()
        self._render_status()
        self.notify("all staged changes dropped", severity="warning",
                    timeout=2)

    def action_stage_default(self) -> None:
        knob = self._selected_knob()
        if knob is None:
            return
        if not self.defaults:
            self.notify("defaults not loaded yet — one moment",
                        severity="warning", timeout=3)
            return
        if self.model.stage_default(knob, self.defaults):
            self._after_stage(knob)
        else:
            self.notify(f"no default known for {knob.env}",
                        severity="warning", timeout=3)

    # ---- save & quit -----------------------------------------------------
    def action_save(self) -> None:
        diff = self.model.diff()
        if not diff:
            self.notify("nothing staged — values are already live config",
                        timeout=3)
            return
        n = self.model.apply()
        for row in self.knob_list.children:
            if isinstance(row, KnobRow):
                row.refresh_value()
        self._refresh_desc()
        self._render_status()
        self.notify(
            f"wrote {n} change(s) to {self.model.env_path.name} — "
            f"./restart.sh brain to apply (rows marked live need no restart)",
            title="saved", timeout=6)

    def action_quit_app(self) -> None:
        self._quit(save_if_staged=True)

    def action_force_quit(self) -> None:
        self.exit()

    def _quit(self, save_if_staged: bool) -> None:
        diff = self.model.diff()
        if not diff or not save_if_staged:
            self.exit()
            return

        def _decide(what: str) -> None:
            if what == "save":
                self.model.apply()
                self.exit()
            elif what == "discard":
                self.exit()
        self.push_screen(QuitGuard(diff), _decide)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())


if __name__ == "__main__":
    SettingsTUI().run()
