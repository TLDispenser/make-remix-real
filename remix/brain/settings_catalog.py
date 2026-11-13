# settings_catalog.py — the data layer behind the settings TUI
# (brain/settings_tui.py): every REMIX_* knob config.py reads, organized
# into sections, plus the pure machinery the TUI needs — formatting,
# parsing, staged changes, search, defaults. NO third-party imports: the
# catalog is testable offline exactly like listening.py, and the plain
# menu (brain/settings.py) can keep running without the TUI dependency.
#
# The .env contract is brain/settings.py's (load_lines/set_key/get_key):
#   * the FIRST definition of a key wins (config's loader semantics)
#   * an existing line is UPDATED, a new one is APPENDED, everything
#     else — including your keys — is preserved byte for byte
#   * secrets are never displayed by any UI layer
#
# Values shown in the TUI are LIVE config (what she is actually running
# on); staged changes are written to .env on Ctrl+S and need
# `./restart.sh brain` to take effect.
import json
import re
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

from . import config, settings as _settings

# The .env file this TUI edits (same file the plain menu edits).
ENV_PATH = _settings._ENV_PATH

# (env key, config attr, kind, one-line help) — env key is the name
# WRITTEN to .env, so it must be one config actually reads: REMIX_<attr>
# for the _env() knobs, the bare name for the few read via os.environ.get
# directly (JEV_URL, JEV_MODEL, JEV_TIMEOUT_S, JEV_API_KEY).
# kind: 'bool' | 'int' | 'float' | 'str' | 'secret' | 'pathlist'
#       | ('choice', a, b, ...)
# hint: what it takes to take effect — '' = restart the brain (default),
# or a live slash command that touches the same knob at runtime.
Knob = namedtuple("Knob", "env attr kind help hint secret")
Knob.__new__.__defaults__ = ("", False)

_SECTION = namedtuple("_Section", "title blurb knobs")

_C = "choice"  # brevity below


def _k(env, attr, kind, help_text, hint=""):
    return Knob(env, attr, kind, help_text, hint,
                secret=("KEY" in env or "TOKEN" in env or "SECRET" in env))


# ---------------------------------------------------------------- sections
# Section blurbs carry the mode/behavior docs so per-knob help can stay
# one line. Every knob here MUST exist on config (test_settings_catalog
# enforces it, so the catalog and config.py can't drift apart silently).
SECTIONS = [
    _SECTION("You & her", "identity, temper, unprompted chatter", [
        _k("REMIX_USER_NAME", "USER_NAME", "str",
           "what she should call you in instruction context (empty = \"you\")"),
        _k("REMIX_TEMPER", "TEMPER", (_C, "cranky", "balanced", "soft"),
           "baseline personality drift per boot"),
        _k("REMIX_TEMPER_REHOUR", "TEMPER_REHOUR", "float",
           "re-roll her per-boot drift this often (hours)"),
        _k("REMIX_QUIET_HOURS", "QUIET_HOURS", "str",
           "no unprompted lines in these hours, \"1-8\" format; empty = off"),
        _k("REMIX_PROACTIVE", "PROACTIVE", "bool",
           "her own unprompted lines at all (off = she only answers)",
           "live: /quiet"),
        _k("REMIX_PROACTIVE_TICK_S", "PROACTIVE_TICK_S", "float",
           "how often the chatter trigger loop wakes (s)"),
        _k("REMIX_PROACTIVE_MIN_GAP_S", "PROACTIVE_MIN_GAP_S", "float",
           "minimum spacing between her unprompted lines (s)"),
        _k("REMIX_PROACTIVE_MAX_PER_DAY", "PROACTIVE_MAX_PER_DAY", "int",
           "hard daily cap on unprompted lines"),
        _k("REMIX_PROACTIVE_JITTER", "PROACTIVE_JITTER", "float",
           "how much cooldowns may wobble so she never lands on a schedule "
           "(0 = exact timings)"),
        _k("REMIX_AMBIENT_RATE", "AMBIENT_RATE", "float",
           "per-tick chance of an idle ambient thought (0.02 = rare)"),
        _k("REMIX_AMBIENT_COOLDOWN_S", "AMBIENT_COOLDOWN_S", "float",
           "her own cooldown between ambient thoughts (s)"),
        _k("REMIX_CHATTER_TIMEOUT_S", "CHATTER_TIMEOUT_S", "float",
           "how long her unprompted words may take to write before the "
           "line is dropped (s)"),
        _k("REMIX_AWAKEN_MIN_S", "AWAKEN_MIN_S", "float",
           "min shut-down gap for an awaken greeting (\"we were apart for "
           "X\") (s)"),
    ]),
    _SECTION("Voice out", "espeak-ng, NOT an AI voice (spec)", [
        _k("REMIX_TTS", "TTS_ENABLED", "bool",
           "she speaks at all (espeak-ng; without it she falls back to "
           "captions)"),
        _k("REMIX_TTS_BIN", "TTS_BIN", "str",
           "the synthesizer binary (default espeak-ng)"),
        _k("REMIX_TTS_VOICE", "TTS_VOICE", "str",
           "espeak voice code (en-us+f3)"),
        _k("REMIX_TTS_SPEED", "TTS_SPEED", "int",
           "words per minute (160)"),
        _k("REMIX_TTS_PITCH", "TTS_PITCH", "int",
           "pitch 0-99 (42); lower = flatter"),
        _k("REMIX_TTS_AMP", "TTS_AMP", "int",
           "amplitude 0-200 (120)"),
        _k("REMIX_TTS_BEAT_GAP_S", "TTS_BEAT_GAP_S", "float",
           "silence between beats of one reply (s)"),
        _k("REMIX_TTS_PROSODY", "TTS_PROSODY", "bool",
           "per-beat speed/pitch lean from the beat's own text"),
        _k("REMIX_TTS_EXPRESSIVE", "TTS_EXPRESSIVE", "bool",
           "her voice follows the face she makes (0 = the old fixed voice)"),
        _k("REMIX_TTS_EXPRESSIVENESS", "TTS_EXPRESSIVENESS", "float",
           "0.5 subtle .. 2.0 theatrical (TTS_EXPRESSIVE only)"),
        _k("REMIX_TTS_DSP", "TTS_DSP", "bool",
           "the numpy DSP layer: tremble / muffle / glitch / breath "
           "(0 = espeak only)"),
        _k("REMIX_TTS_STRAIN", "TTS_STRAIN", "bool",
           "her voice gets glitchy when the machine is genuinely strained"),
        _k("REMIX_TTS_DIR", "TTS_DIR", "str",
           "runtime dir for the synthesized WAVs"),
        _k("REMIX_SUBTITLES", "SUBTITLES", (_C, "auto", "always", "never"),
           "when her words show as on-screen text"),
        _k("REMIX_CHATTER_VOICE", "CHATTER_VOICE",
           (_C, "auto", "always", "never"),
           "whether unprompted lines get a voice (auto = only when the "
           "room is quiet)"),
    ]),
    _SECTION("Ears", "push-to-talk + reacting while you type", [
        _k("REMIX_PTT", "PTT_ENABLED", "bool",
           "push-to-talk at all (mic open ONLY while held; always local)"),
        _k("REMIX_PTT_REC_CMD", "PTT_REC_CMD", "str",
           "recorder: \"auto\" (parec/pw-record/arecord) or a full command "
           "writing s16le/16 kHz/mono PCM to stdout"),
        _k("REMIX_PTT_MODEL", "PTT_MODEL", "str",
           "local whisper model (tiny.en / base.en / a local dir)"),
        _k("REMIX_PTT_LANGUAGE", "PTT_LANGUAGE", "str",
           "language code; empty = autodetect"),
        _k("REMIX_PTT_SOCK", "PTT_SOCK", "str",
           "unix socket for the global hotkey daemon (ptt.sh)"),
        _k("REMIX_PTT_MIN_PRESS_S", "PTT_MIN_PRESS_S", "float",
           "shorter presses are discarded as accidental (s)"),
        _k("REMIX_PTT_MAX_PRESS_S", "PTT_MAX_PRESS_S", "float",
           "hard cap: the brain closes the mic itself after this long, "
           "key-up or not (s)"),
        _k("REMIX_PTT_MIN_PEAK", "PTT_MIN_PEAK", "float",
           "below this audio peak = silence, whisper is skipped"),
        _k("REMIX_LISTEN", "LISTEN", "bool",
           "she reacts to what you TYPE while unaddressed (the push-to-talk "
           "mic is separate and always local)"),
        _k("REMIX_LISTEN_TICK_S", "LISTEN_TICK_S", "float",
           "listening reflex tick (s)"),
        _k("REMIX_LISTEN_PAUSE_S", "LISTEN_PAUSE_S", "float",
           "a typing pause this long is worth a peek (s)"),
        _k("REMIX_LISTEN_MIN_WORDS", "LISTEN_MIN_WORDS", "int",
           "no peek on a line shorter than this"),
        _k("REMIX_LISTEN_REUSE_SLACK_WORDS", "LISTEN_REUSE_SLACK_WORDS",
           "int", "the final line may add this many words and still reuse "
           "the peeked reaction"),
        _k("REMIX_LISTEN_STT_PARTIALS", "LISTEN_STT_PARTIALS", "bool",
           "while the key is held, whisper transcribes rolling partials "
           "(costs CPU while held)"),
        _k("REMIX_LISTEN_STT_INTERVAL_S", "LISTEN_STT_INTERVAL_S", "float",
           "seconds between voice partials"),
        _k("REMIX_LISTEN_STT_MIN_S", "LISTEN_STT_MIN_S", "float",
           "audio needed before the first partial (s)"),
        _k("REMIX_LISTEN_JEV", "LISTEN_JEV", "bool",
           "Jev peeks at pauses on 4+ word lines — SPENDS CREDITS and "
           "sends a line you might still delete to the cloud"),
        _k("REMIX_LISTEN_JEV_PER_EXCHANGE", "LISTEN_JEV_PER_EXCHANGE",
           "int", "max peeks per line"),
        _k("REMIX_LISTEN_JEV_MIN_GAP_S", "LISTEN_JEV_MIN_GAP_S", "float",
           "min gap between peeks (s)"),
        _k("REMIX_LISTEN_JEV_RESERVE", "LISTEN_JEV_RESERVE", "int",
           "peeks stop this many calls BEFORE the hourly cap so they can "
           "never starve the real reaction"),
    ]),
    _SECTION("Senses & privacy", "what she can see — privacy gates live here",
             [
        _k("REMIX_AWARENESS", "AWARENESS", "bool",
           "senses beyond her own vitals: focused app, idle, power, audio"),
        _k("REMIX_AWARENESS_INTERVAL_S", "AWARENESS_INTERVAL_S", "float",
           "sense poll interval (s)"),
        _k("REMIX_TITLES", "TITLES", (_C, "off", "scrub", "raw"),
           "window titles reaching the LLM: off / scrubbed / raw — raw "
           "sends more to the cloud", "live: /aware"),
        _k("REMIX_TITLE_ALLOW", "TITLE_ALLOW", "str",
           "app classes whose titles may pass (comma list, e.g. "
           "mpv,spotify); still scrubbed of emails/URLs/IPs/paths"),
        _k("REMIX_SURROUNDINGS", "SURROUNDINGS", "bool",
           "she knows what windows are AROUND her (class names + geometry "
           "only)"),
        _k("REMIX_SURROUNDINGS_MAX", "SURROUNDINGS_MAX", "int",
           "nearest N windows she names"),
        _k("REMIX_SELF_MATCH", "SELF_MATCH", "str",
           "how her own window is recognised in hyprctl (comma list, "
           "class or title)"),
        _k("REMIX_WINDOW_POS", "WINDOW_POS", "bool",
           "trust her reported on-screen position (0 = kill switch for "
           "compositors that report junk)"),
        _k("REMIX_APP_EVENTS", "APP_EVENTS", "bool",
           "she notices apps opening and closing (class names only)"),
        _k("REMIX_BITS", "BITS", "bool",
           "the laptop's own life: lid, wifi, usb, screenshots... (per-bit "
           "cooldown floors are BIT_COOLDOWN_FLOORS_S in config.py)"),
        _k("REMIX_AUDIO_EVENTS", "AUDIO_EVENTS", "bool",
           "pactl subscribe as an event source"),
        _k("REMIX_SCREENSHOT_DIRS", "SCREENSHOT_DIRS", "pathlist",
           "where screenshots typically land (colon-separated list)"),
        _k("REMIX_SHELL_HOOK", "SHELL_HOOK", "bool",
           "unix socket for the zsh hook (long shell jobs; command text "
           "goes to the cloud, redacted first)"),
        _k("REMIX_SHELL_SOCK", "SHELL_SOCK", "str",
           "socket path for the shell hook"),
        _k("REMIX_SHELL_MIN_S", "SHELL_MIN_S", "float",
           "shorter shell jobs aren't news (s)"),
        _k("REMIX_SHELL_MAX_CMD", "SHELL_MAX_CMD", "int",
           "chars of a command she ever sees"),
        _k("REMIX_SHELL_COOLDOWN_S", "SHELL_COOLDOWN_S", "float",
           "min between shell-job reactions (s)"),
        _k("REMIX_INBOX", "INBOX", "bool",
           "watch inbox/ for files you drop for her (the file excerpt goes "
           "to the cloud — opt-in on purpose)"),
        _k("REMIX_INBOX_POLL_S", "INBOX_POLL_S", "float",
           "how often the inbox watcher looks (s)"),
        _k("REMIX_INBOX_EXCERPT_CHARS", "INBOX_EXCERPT_CHARS", "int",
           "file excerpt cap BEFORE the LLM"),
    ]),
    _SECTION("Body & vitals", "the machine she lives in", [
        _k("REMIX_MOCK", "MOCK", "bool",
           "synthetic vitals feed (demo mode; 0 = the real machine)"),
        _k("REMIX_VITALS_INTERVAL_S", "VITALS_INTERVAL_S", "float",
           "vitals poll interval (s)"),
        _k("REMIX_BODY_REFRESH_S", "BODY_REFRESH_S", "float",
           "how often body truths (battery wear, SMART, kernel errors) "
           "re-probe (s)"),
        _k("REMIX_TEMP_TINT", "TEMP_TINT", "bool",
           "her colour follows the core temperature (OFF by default; "
           "you asked for that)"),
        _k("REMIX_TEMP_ALARM_C", "TEMP_ALARM_C", "float",
           "core temp that reads as alarm (deg C)"),
        _k("REMIX_CPU_SPIKE", "CPU_SPIKE", "float",
           "instant spooked above this CPU %"),
        _k("REMIX_CPU_HIGH", "CPU_HIGH", "float",
           "sustained anger above this CPU %"),
        _k("REMIX_CPU_FLAT", "CPU_FLAT", "float",
           "below this CPU % the machine reads as idle"),
        _k("REMIX_BATT_LOW", "BATT_LOW", "float",
           "battery % that reads as tired"),
        _k("REMIX_BATT_DROOP", "BATT_DROOP", "float",
           "battery % that reads as pout-ish"),
        _k("REMIX_DISK_FULL", "DISK_FULL", "float",
           "disk % for a \"disk's full\" notice"),
        _k("REMIX_POSE_CANDIDATE_N", "POSE_CANDIDATE_N", "int",
           "consecutive agreeing snapshots before a pose change"),
        _k("REMIX_POSE_MIN_HOLD_S", "POSE_MIN_HOLD_S", "float",
           "min seconds a pose holds before reconsidering"),
        _k("REMIX_NOTICE_MIN_GAP_S", "NOTICE_MIN_GAP_S", "float",
           "min gap between flavor notices (s)"),
        _k("REMIX_NOTICE_GATE", "NOTICE_GATE", "bool",
           "flavor notices only show while the machine is muted/quiet "
           "(0 = the old always-show)"),
        _k("REMIX_QUIET_VOLUME", "QUIET_VOLUME", "float",
           "sink volume at or below this counts as \"low\""),
        _k("REMIX_AUDIO_GATE_TTL_S", "AUDIO_GATE_TTL_S", "float",
           "how long a wpctl reading is trusted (s)"),
        _k("REMIX_RESUME_MIN_S", "RESUME_MIN_S", "float",
           "wall-time gap this big after resume = \"she was asleep\" (s)"),
    ]),
    _SECTION("Attention", "urgent marker + the escalation ladder + nudges", [
        _k("REMIX_URGENT", "URGENT_ENABLED", "bool",
           "the urgent marker at all (taskbar attention request when she "
           "needs you)"),
        _k("REMIX_OOS_URGENT", "OOS_URGENT", "bool",
           "rung 1: she said something while out of sight"),
        _k("REMIX_ESCALATE_NOTIFY", "ESCALATE_NOTIFY", "bool",
           "rung 2: notify-send with urgency set"),
        _k("REMIX_ESCALATE_DISPATCH", "ESCALATE_DISPATCH", "bool",
           "rung 3: a Hyprland dispatch brings her forward (STEALS FOCUS "
           "— opt-in on purpose)"),
        _k("REMIX_ESCALATE_NOTIFY_COOLDOWN_S", "ESCALATE_NOTIFY_COOLDOWN_S",
           "float", "min between notify rungs (s)"),
        _k("REMIX_ESCALATE_DISPATCH_COOLDOWN_S",
           "ESCALATE_DISPATCH_COOLDOWN_S", "float",
           "min between dispatch rungs (s)"),
        _k("REMIX_NOTIFY_TIMEOUT_S", "NOTIFY_TIMEOUT_S", "int",
           "notify-send -t (ms)"),
        _k("REMIX_MUTE_REACT_COOLDOWN_S", "MUTE_REACT_COOLDOWN_S", "float",
           "min between dry beats about being muted (s)"),
        _k("REMIX_SESSION_LONG_S", "SESSION_LONG_S", "float",
           "session this long with no real break earns a stretch nudge (s)"),
        _k("REMIX_APP_STUCK_S", "APP_STUCK_S", "float",
           "same app this long earns a nudge (s)"),
        _k("REMIX_IDLE_GENTLE_S", "IDLE_GENTLE_S", "float",
           "idle past this = \"still there?\" (s)"),
        _k("REMIX_IDLE_DEEP_S", "IDLE_DEEP_S", "float",
           "idle past this = soft check-in (s)"),
        _k("REMIX_AWAY_S", "AWAY_S", "float",
           "away past this = welcome-back line (s)"),
        _k("REMIX_MUTED_NAG_S", "MUTED_NAG_S", "float",
           "room quiet this long earns a comment (s)"),
    ]),
    _SECTION("Jev & budget", "left brain — limited credits, enforced here", [
        _k("JEV_API_KEY", "JEV_API_KEY", "secret",
           "Jev key from console.typesafe.ai/keys; absent = RuleBrain only. "
           "Never displayed"),
        _k("JEV_URL", "JEV_URL", "str",
           "the System One endpoint"),
        _k("JEV_MODEL", "JEV_MODEL", "str",
           "Jev model name"),
        _k("JEV_TIMEOUT_S", "JEV_TIMEOUT_S", "float",
           "per-call timeout (s)"),
        _k("REMIX_JEV_COOLDOWN_S", "JEV_COOLDOWN_S", "float",
           "min seconds between ANY two Jev calls"),
        _k("REMIX_JEV_MAX_PER_HOUR", "JEV_MAX_PER_HOUR", "int",
           "hard hourly cap; over cap = RuleBrain answers, silently"),
        _k("REMIX_JEV_CACHE_TTL_S", "JEV_CACHE_TTL_S", "float",
           "identical state signature = cached decision, no call (s)"),
        _k("REMIX_JEV_PROACTIVE_MAX_PER_HOUR", "JEV_PROACTIVE_MAX_PER_HOUR",
           "int", "own conservative ceiling for silent-life calls, on top "
           "of the shared hourly cap"),
        _k("REMIX_JEV_FACE_TEMPERATURE", "JEV_FACE_TEMPERATURE", "float",
           "face sampling: lower = more predictable, higher = more varied"),
        _k("REMIX_JEV_REPEAT_PENALTY", "JEV_REPEAT_PENALTY", "float",
           "weight multiplier per recent repeat (1 = off)"),
        _k("REMIX_BLUSH_BASELINE", "BLUSH_BASELINE", "float",
           "floor the wire blush can't go under (1.0 = the old always-on "
           "hatching)"),
        _k("REMIX_JEV_BLUSH", "JEV_BLUSH", "bool",
           "Jev answers a blush noul beside the eyes/mouth it already "
           "picks (same request, $0)"),
        _k("REMIX_JEV_BLUSH_THRESHOLD", "JEV_BLUSH_THRESHOLD", "float",
           "blush noul above this = bashful hatching"),
        _k("REMIX_JEV_SPEAK_P", "JEV_SPEAK_P", "float",
           "live should-speak threshold"),
        _k("REMIX_JEV_DODGE_THRESHOLD", "JEV_DODGE_THRESHOLD", "float",
           "dodge judge threshold for questions/commands"),
        _k("REMIX_JEV_COMMIT_P", "JEV_COMMIT_P", "float",
           "live commitment threshold"),
        _k("REMIX_DECISIONS_LOG", "DECISIONS_LOG", "bool",
           "the rotating decision log: answer, confidence, "
           "used-or-fell-back, and what local would have done"),
        _k("REMIX_DECISIONS_LOG_MAX_BYTES", "DECISIONS_LOG_MAX_BYTES", "int",
           "decision log size cap before rotation"),
    ]),
    _SECTION("The seam", "off = local logic ships - shadow = Jev answers "
             "beside it, log only - live = Jev's answer ships", [
        _k("REMIX_JEV_DECIDE_STANCE", "JEV_DECIDE_STANCE",
           (_C, "off", "shadow", "live"),
           "proactive stance (which mood a noticed thing gets)"),
        _k("REMIX_JEV_DECIDE_SPEAK", "JEV_DECIDE_SPEAK",
           (_C, "off", "shadow", "live"),
           "the should-speak noul (whether she has reason to speak)"),
        _k("REMIX_JEV_DECIDE_FORM", "JEV_DECIDE_FORM",
           (_C, "off", "shadow", "live"),
           "form: speak / caption / look / quiet"),
        _k("REMIX_JEV_JUDGE_GROUNDING", "JEV_JUDGE_GROUNDING",
           (_C, "off", "shadow", "live"),
           "the unseen-thing judge: her words vs what she can actually see"),
        _k("REMIX_JEV_DECIDE_INTENT", "JEV_DECIDE_INTENT",
           (_C, "off", "shadow", "live"),
           "user intent (question / command / ...)"),
        _k("REMIX_JEV_JUDGE_DODGE", "JEV_JUDGE_DODGE",
           (_C, "off", "shadow", "live"),
           "the dodge judge for questions/commands"),
        _k("REMIX_JEV_DECIDE_COMMITMENT", "JEV_DECIDE_COMMITMENT",
           (_C, "off", "shadow", "live"),
           "commitment & reminder detection"),
        _k("REMIX_JEV_RANK_MEMORY", "JEV_RANK_MEMORY",
           (_C, "off", "shadow", "live"),
           "ranking memory candidates with Jev"),
        _k("REMIX_JEV_DECIDE_ESCALATE", "JEV_DECIDE_ESCALATE",
           (_C, "off", "shadow", "live"),
           "start the attention ladder?"),
        _k("REMIX_JEV_DECIDE_MUTE", "JEV_DECIDE_MUTE",
           (_C, "off", "shadow", "live"),
           "a dry beat on mute/unmute?"),
    ]),
    _SECTION("LLM (right brain)", "the $0 provider pool that writes her words",
             [
        _k("REMIX_LLM_API_KEY", "LLM_API_KEY", "secret",
           "free key from z.ai (glm-4.7-flash, $0). Never displayed"),
        _k("REMIX_LLM_BASE_URL", "LLM_BASE_URL", "str",
           "primary provider, OpenAI-compatible base URL"),
        _k("REMIX_LLM_MODEL", "LLM_MODEL", "str",
           "primary model (must be $0 — ground rule)"),
        _k("REMIX_LLM_TIMEOUT_S", "LLM_TIMEOUT_S", "float",
           "per-call timeout (s)"),
        _k("REMIX_LLM_STREAM", "LLM_STREAM", "bool",
           "stream replies so sentence one can speak early (0 = one-shot "
           "calls)"),
        _k("REMIX_LLM_PROVIDER_COOLDOWN_S", "LLM_PROVIDER_COOLDOWN_S",
           "float", "a provider that proves stuck rests this long (s)"),
        _k("REMIX_LLM_BACKUP1_URL", "LLM_BACKUP1_URL", "str",
           "fallback pool, entry 1 URL (increment the number for more "
           "backups)"),
        _k("REMIX_LLM_BACKUP1_MODEL", "LLM_BACKUP1_MODEL", "str",
           "fallback model, entry 1 (e.g. gemini-3.5-flash-lite)"),
        _k("REMIX_LLM_BACKUP1_API_KEY", "LLM_BACKUP1_API_KEY", "secret",
           "fallback key, entry 1. Never displayed"),
        _k("REMIX_HISTORY_MAX_MESSAGES", "HISTORY_MAX_MESSAGES", "int",
           "conversation context window (20 = 10 exchanges)"),
    ]),
    _SECTION("Memory & home", "her sandbox, journal and optional mirror", [
        _k("REMIX_SANDBOX_DIR", "SANDBOX_DIR", "str",
           "her sandbox: journal, notes, outbox (XDG data dir by default, "
           "OUTSIDE the project tree)"),
        _k("REMIX_MEMORY_REPO", "MEMORY_REPO", "str",
           "optional PRIVATE git mirror of her sandbox"),
        _k("REMIX_MEMORY_TOKEN", "MEMORY_TOKEN", "secret",
           "fine-grained PAT (contents read/write), read at push time "
           "only. Never displayed"),
        _k("REMIX_MEMORY_SYNC_MIN_S", "MEMORY_SYNC_MIN_S", "float",
           "push cadence when dirty (s)"),
        _k("REMIX_MEMORY_MAX_ENTRIES", "MEMORY_MAX_ENTRIES", "int",
           "journal rows kept in memory.json"),
        _k("REMIX_MEMORY_SEARCH_LIMIT", "MEMORY_SEARCH_LIMIT", "int",
           "FTS candidates retrieved"),
        _k("REMIX_MEMORY_INJECT_LIMIT", "MEMORY_INJECT_LIMIT", "int",
           "retrieved memories injected into her prompt"),
    ]),
    _SECTION("Face link", "the brain's WebSocket (protocol v2, v2 is the "
             "contract in godot/PROTOCOL.md)", [
        _k("REMIX_WS_HOST", "WS_HOST", "str",
           "WebSocket host (the brain is the server)"),
        _k("REMIX_WS_PORT", "WS_PORT", "int",
           "WebSocket port (talk.sh and restart.sh expect 8765)"),
    ]),
]

ALL_KNOBS = [knob for _s in SECTIONS for knob in _s.knobs]
KNOB_BY_ENV = {knob.env: knob for knob in ALL_KNOBS}
KNOB_BY_ATTR = {knob.attr: knob for knob in ALL_KNOBS}


# ------------------------------------------------------------ formatting
def format_value(knob, value) -> str:
    """A config value as a human-readable display string."""
    if knob.kind == "secret":
        return ""                     # callers show set/unset instead
    if isinstance(knob.kind, tuple) and knob.kind[0] == _C:
        return str(value)
    if isinstance(value, bool):
        return "on" if value else "off"
    if value is None:
        return ""
    if knob.kind == "pathlist" and isinstance(value, (tuple, list)):
        return ":".join(value)
    if knob.kind == "float" and isinstance(value, float):
        return f"{value:g}"
    return str(value)


def canonical_value(knob, value) -> str:
    """A config value as the canonical .env string."""
    if isinstance(knob.kind, tuple) and knob.kind[0] == _C:
        return str(value)
    if isinstance(value, bool):
        return "1" if value else "0"
    if value is None:
        return ""
    if knob.kind == "pathlist" and isinstance(value, (tuple, list)):
        return ":".join(value)
    if knob.kind == "float" and isinstance(value, float):
        return f"{value:g}"
    return str(value)


def parse_input(knob, raw: str) -> str:
    """User input -> the canonical .env string for this knob.

    Raises ValueError on anything that would write a line config.py
    would misread. Secrets pass through untouched (masked in the UI)."""
    raw = raw.strip()
    if isinstance(knob.kind, tuple) and knob.kind[0] == _C:
        v = raw.lower()
        if v not in knob.kind[1:]:
            raise ValueError(f"choose one of: {', '.join(knob.kind[1:])}")
        return v
    if knob.kind == "bool":
        v = raw.lower()
        if v in ("1", "on", "true", "yes", "y"):
            return "1"
        if v in ("0", "off", "false", "no", "n"):
            return "0"
        raise ValueError("on/off (1/0, true/false, yes/no all work)")
    if knob.kind == "int":
        try:
            return str(int(float(raw)))
        except ValueError:
            raise ValueError(f"not a number: {raw!r}") from None
    if knob.kind == "float":
        try:
            return f"{float(raw):g}"
        except ValueError:
            raise ValueError(f"not a number: {raw!r}") from None
    return raw                    # str / pathlist / secret: as typed


def live_value(knob):
    """What she is actually running on (the config module's read)."""
    special = _SPECIAL_LIVE.get(knob.attr)
    if special is not None:
        return special()
    return getattr(config, knob.attr)


def _backup1_entry() -> dict:
    """The backup-1 provider as config.py built the pool: primary first
    (when a key exists), then REMIX_LLM_BACKUPn_* entries with url+model
    +key complete."""
    idx = 1 if config.LLM_API_KEY else 0
    return config.PROVIDERS[idx] if len(config.PROVIDERS) > idx else {}


# config.py has no static attrs for the BACKUP1 trio — _providers() reads
# REMIX_LLM_BACKUP1_* dynamically and folds them into PROVIDERS. Live
# values come from there; the defaults probe handles them the same way.
_SPECIAL_LIVE = {
    "LLM_BACKUP1_URL": lambda: _backup1_entry().get("url", ""),
    "LLM_BACKUP1_MODEL": lambda: _backup1_entry().get("model", ""),
    "LLM_BACKUP1_API_KEY": lambda: _backup1_entry().get("key", ""),
}


def secret_is_set(knob) -> bool:
    return bool(live_value(knob))


def display_value(knob, staged: "dict | None" = None) -> str:
    """The row text for a knob: staged value wins, then live."""
    if knob.kind == "secret":
        if staged is not None and knob.env in staged:
            return "staged" if staged[knob.env] else "cleared (staged)"
        return "set" if secret_is_set(knob) else "unset"
    if staged is not None and knob.env in staged:
        return staged[knob.env]
    return format_value(knob, live_value(knob))


# ---------------------------------------------------------------- search
def search_knobs(query: str, knobs=None) -> list:
    """Every knob whose name, config attr, help or CHOICES match ALL
    tokens (case-insensitive substring). Catalog order preserved."""
    knobs = ALL_KNOBS if knobs is None else knobs
    tokens = [t for t in re.split(r"\s+", query.strip().lower()) if t]
    if not tokens:
        return []
    out = []
    for knob in knobs:
        choices = "" if not isinstance(knob.kind, tuple) \
            else " ".join(knob.kind[1:])
        hay = f"{knob.env} {knob.attr} {knob.help} {choices}".lower()
        if all(t in hay for t in tokens):
            out.append(knob)
    return out


# ----------------------------------------------------------------- model
class SettingsModel:
    """The staged-changes state for one .env file.

    live() reads the config module (import-time truth); staged changes
    sit in memory until apply() writes them through settings.set_key —
    first definition updated in place, everything else preserved."""
    def __init__(self, env_path: Path = ENV_PATH):
        self.env_path = Path(env_path)
        self.staged: dict = {}          # env key -> canonical .env string

    # -- .env inspection -------------------------------------------------
    def load_lines(self) -> list:
        if not self.env_path.exists():
            return []
        return self.env_path.read_text().splitlines()

    def env_raw(self, knob) -> "str | None":
        """The raw value .env holds for this knob (or None = not there)."""
        return _settings.get_key(self.load_lines(), knob.env)

    def env_differs(self, knob) -> bool:
        """True when .env's raw line disagrees with live config — meaning
        the real environment or an earlier duplicate line won at load."""
        raw = self.env_raw(knob)
        if raw is None or knob.kind == "secret":
            return False
        return raw != canonical_value(knob, live_value(knob))

    # -- staging ---------------------------------------------------------
    def stage(self, knob, value_str: str) -> None:
        """No-op only when .env's line would end up byte-identical."""
        if self.env_raw(knob) == value_str:
            return
        self.staged[knob.env] = value_str

    def stage_default(self, knob, defaults: dict) -> bool:
        """Reset-to-default: stage the knob's config.py default."""
        default = defaults.get(knob.attr)
        if default is None:
            return False
        self.stage(knob, default)
        return True

    def unstage(self, knob) -> None:
        self.staged.pop(knob.env, None)

    def unstage_all(self) -> None:
        self.staged.clear()

    def is_staged(self, knob) -> bool:
        return knob.env in self.staged

    def diff(self) -> list:
        """(env key, live display, new value) for every staged change."""
        out = []
        for knob in ALL_KNOBS:
            if knob.env in self.staged:
                old = display_value(knob, None)
                new = self.staged[knob.env]
                if knob.kind == "secret":
                    old = "set" if secret_is_set(knob) else "unset"
                    new = "(hidden)"
                out.append((knob.env, old, new))
        return out

    # -- writing ---------------------------------------------------------
    def apply(self) -> int:
        """Write every staged change to .env (fresh load first, so lines
        edited elsewhere meanwhile are preserved). Returns the count."""
        if not self.staged:
            return 0
        lines = self.load_lines()
        for env_key, value in self.staged.items():
            lines = _settings.set_key(lines, env_key, value)
        self.env_path.parent.mkdir(parents=True, exist_ok=True)
        self.env_path.write_text("\n".join(lines).rstrip("\n") + "\n")
        n = len(self.staged)
        self.staged.clear()
        return n


# -------------------------------------------------------------- defaults
def load_defaults(timeout_s: float = 20.0) -> dict:
    """config.py's built-in defaults, as canonical .env strings keyed by
    config attr. Read from a CLEAN subprocess: a throwaway copy of the
    brain package in a temp dir (so the repo's real .env — which config
    loads unconditionally — can't masquerade as the defaults) and a
    stripped environment (so REMIX_*/CORE_*/JEV_* from the real
    environment can't either). Same isolation trick test_env.py uses.

    Returns {} on any failure; the UI just shows nothing then."""
    attrs = sorted({knob.attr for knob in ALL_KNOBS})
    code = "\n".join([
        "import contextlib, io, json, sys",
        "sys.path.insert(0, '')",
        "buf = io.StringIO()",
        "with contextlib.redirect_stdout(buf):",
        "    from brain import config",
        "def _backup1():",
        "    idx = 1 if config.LLM_API_KEY else 0",
        "    n = len(config.PROVIDERS)",
        "    return config.PROVIDERS[idx] if n > idx else {}",
        "special = {",
        "    'LLM_BACKUP1_URL': lambda: _backup1().get('url', ''),",
        "    'LLM_BACKUP1_MODEL': lambda: _backup1().get('model', ''),",
        "    'LLM_BACKUP1_API_KEY': lambda: _backup1().get('key', ''),",
        "}",
        "out = {}",
        "for a in attrs:",
        "    v = special[a]() if a in special else getattr(config, a)",
        "    if isinstance(v, bool):",
        "        out[a] = '1' if v else '0'",
        "    elif v is None:",
        "        out[a] = ''",
        "    elif isinstance(v, float):",
        "        out[a] = f'{v:g}'",
        "    elif isinstance(v, (tuple, list)):",
        "        out[a] = ':'.join(str(x) for x in v)",
        "    else:",
        "        out[a] = str(v)",
        "print(json.dumps(out))",
    ])
    import shutil
    import tempfile
    try:
        with tempfile.TemporaryDirectory(prefix="remix-defaults-") as tmp:
            shutil.copytree(
                Path(__file__).resolve().parent, Path(tmp) / "brain",
                ignore=shutil.ignore_patterns("__pycache__", "sandbox",
                                              "tests"))
            proc = subprocess.run(
                [sys.executable, "-c", f"attrs = {attrs!r}\n{code}"],
                cwd=tmp, capture_output=True, text=True, timeout=timeout_s,
                env={"HOME": str(Path.home()),
                     "PATH": "/usr/bin:/bin:/usr/local/bin",
                     "PYTHONHASHSEED": "0"})
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, KeyError, IndexError):
        return {}


# -------------------------------------------------------------- status
def brain_is_up(host: "str | None" = None, port: "int | None" = None,
                timeout_s: float = 0.4) -> bool:
    """Can we open the brain's WebSocket port right now? (Cheap TCP
    connect — no protocol bytes, no side effects.)"""
    import socket
    host = config.WS_HOST if host is None else host
    port = config.WS_PORT if port is None else port
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def status_line() -> str:
    """One honest strip for the header: what's actually configured."""
    jev = "on" if config.JEV_ENABLED else "off"
    providers = len(config.PROVIDERS)
    mock = "MOCK" if config.MOCK else "real"
    return f"jev {jev} · llm providers {providers} · vitals {mock}"
