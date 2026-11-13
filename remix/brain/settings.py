# settings.py — Phase 7 (plan 4.10): the settings panel as a plain terminal
# menu (no deps, works over SSH). Edits the REMIX_* knobs in remix/.env in
# place: the first definition of a key wins (config's loader semantics), so
# an existing line is UPDATED, a new one is APPENDED, and everything else in
# the file — including your keys — is preserved byte for byte. Secrets are
# never displayed. Most knobs need `./restart.sh brain` to take effect; the
# menu says which ones have a live slash command instead.
#
# Run from the remix/ directory:
#   ./.venv/bin/python -m brain.settings
import sys
from pathlib import Path

from . import config

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

# (env key, config attr, kind, one-line explanation)
# kind: 'bool' | 'int' | 'float' | 'str' | choice-tuples like ('choice', a, b)
KNOBS = [
    ("USER_NAME", "USER_NAME", "str",
     "what she should call you in instruction context"),
    ("TEMPER", "TEMPER", ("choice", "cranky", "balanced", "soft"),
     "baseline personality drift per boot"),
    ("QUIET_HOURS", "QUIET_HOURS", "str",
     'no unprompted lines in these hours, "1-8" format; empty = off'),
    ("PROACTIVE", "PROACTIVE", "bool",
     "her own unprompted lines at all (off = she only answers)"),
    ("SUBTITLES", "SUBTITLES", ("choice", "auto", "always", "never"),
     "when her words show as on-screen text"),
    ("TTS_EXPRESSIVE", "TTS_EXPRESSIVE", "bool",
     "her voice follows the faces she makes (0 = the old fixed voice)"),
    ("TTS_EXPRESSIVENESS", "TTS_EXPRESSIVENESS", "float",
     "0.5 subtle .. 2.0 theatrical (TTS_EXPRESSIVE only)"),
    ("LISTEN", "LISTEN", "bool",
     "she reacts to what you TYPE while she is not addressed (push-to-talk "
     "mic is separate and always local)"),
    ("TITLES", "TITLES", ("choice", "off", "scrub", "raw"),
     "window titles reaching the LLM: off / scrubbed / raw (allowlist still "
     "applies; raw sends more to the cloud)"),
    ("BITS", "BITS", "bool",
     "the laptop's own life: lid, wifi, usb, screenshots..."),
    ("TEMP_TINT", "TEMP_TINT", "bool",
     "her colour follows the core temperature (OFF by default; 1 = the tint)"),
    ("BODY_REFRESH_S", "BODY_REFRESH_S", "float",
     "how often body truths (battery wear, SMART, kernel errors) re-probe"),
    ("LLM_STREAM", "LLM_STREAM", "bool",
     "stream replies so sentence one speaks early (0 = one-shot calls)"),
    ("MOCK", "MOCK", "bool",
     "synthetic vitals feed (demo mode; 0 = the real machine)"),
    ("INBOX", "INBOX", "bool",
     "watch inbox/ for files you drop for her (excerpt goes to the cloud)"),
    ("SHELL_HOOK", "SHELL_HOOK", "bool",
     "unix socket for the zsh hook (long shell jobs; command text goes to "
     "the cloud, redacted first)"),
]
_SECRET_HINTS = ("KEY", "TOKEN", "SECRET")


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


def _parse(kind, raw: str):
    raw = raw.strip()
    if kind == "bool":
        return raw.lower() in ("1", "on", "true", "yes", "y")
    if kind == "int":
        return int(float(raw))
    if kind == "float":
        return float(raw)
    if isinstance(kind, tuple) and kind[0] == "choice":
        v = raw.lower()
        if v not in kind[1:]:
            raise ValueError(f"choose one of {', '.join(kind[1:])}")
        return v
    return raw


def load_lines() -> list:
    """The .env file as a list of lines (missing file = empty)."""
    if not _ENV_PATH.exists():
        return []
    return _ENV_PATH.read_text().splitlines()


def set_key(lines: list, key: str, value: str) -> list:
    """Update the FIRST `KEY=` line (the one the loader honors) or append at
    the end. Returns the new list; does not write."""
    target = f"{key}="
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith(target) and not s.startswith("#"):
            lines[i] = f"{key}={value}"
            return lines
    if lines and lines[-1].strip():
        lines.append("")
    lines.append(f"# set by brain.settings")
    lines.append(f"{key}={value}")
    return lines


def get_key(lines: list, key: str) -> str | None:
    target = f"{key}="
    for ln in lines:
        s = ln.strip()
        if s.startswith(target) and not s.startswith("#"):
            return s[len(target):].strip()
    return None


def write_lines(lines: list) -> None:
    _ENV_PATH.write_text("\n".join(lines).rstrip("\n") + "\n")


def menu() -> None:
    print(f"remix settings — {_ENV_PATH}\n"
          "(values below are LIVE config; changes are written to .env and\n"
          " need `./restart.sh brain` unless a live slash command exists)\n")
    while True:
        for i, (key, attr, kind, help_text) in enumerate(KNOBS, 1):
            cur = _fmt(getattr(config, attr))
            print(f"{i:2}. {key:22} = {cur}")
        print("  0. quit")
        raw = input("\nnumber to change (or just Enter to quit): ").strip()
        if not raw or raw == "0":
            return
        try:
            idx = int(raw) - 1
            key, attr, kind, help_text = KNOBS[idx]
        except (ValueError, IndexError):
            print("  ? not a number on the list\n")
            continue
        print(f"\n{key} — {help_text}")
        if any(s in key for s in _SECRET_HINTS):
            print("  (secret values are never displayed here)")
            continue
        cur = _fmt(getattr(config, attr))
        if isinstance(kind, tuple) and kind[0] == "choice":
            print(f"  choices: {', '.join(kind[1:])}")
        print(f"  current: {cur}")
        val = input("  new value (Enter cancels): ").strip()
        if not val:
            continue
        try:
            parsed = _parse(kind, val)
        except (ValueError, TypeError) as e:
            print(f"  ? {e}\n")
            continue
        lines = set_key(load_lines(), key, _fmt(parsed))
        write_lines(lines)
        setattr(config, attr, parsed)      # the running menu sees it now
        print(f"  {key} = {_fmt(parsed)} written\n")


if __name__ == "__main__":
    try:
        menu()
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(0)
