# decisions.py — the seam (Upgrade Plan V2, Part 1.6).
#
# Every decision that moves from "the program decides" to "Jev decides"
# carries four things (plan rule 3):
#   * a local fallback (the code's existing pattern),
#   * a runtime switch: mode(kind) in off / shadow / live,
#   * a shadow period that runs Jev beside the old logic and ships NOTHING
#     but the log (the local pick still decides),
#   * a log entry here: timestamp, kind, mode, Jev's answer + confidence,
#     whether it was used or fell back, and what the local logic would
#     have done.
#
# The log is a rotating file next to brain.log, and /why reads it back in
# plain words. No JEV_API_KEY means everything degrades to off — the modes
# are irrelevant when the left brain isn't there.
import logging
import logging.handlers
import time
from collections import deque

from . import config

# kind -> (what it decides, default mode). Thresholds live in config.py.
KINDS = {
    "proactive_stance": ("which stance an unprompted line takes",
                         config.JEV_DECIDE_STANCE),
    "should_speak": ("whether an unprompted line is worth saying",
                     config.JEV_DECIDE_SPEAK),
    "form": ("the form of an unprompted reaction: speak/caption/look/quiet",
             config.JEV_DECIDE_FORM),
    "judge_unseen": ("whether her reply claims to see what it cannot",
                     config.JEV_JUDGE_GROUNDING),
}

# runtime overrides (e.g. /decisions); boot defaults come from KINDS
_modes: dict = {}
_recent: deque = deque(maxlen=40)     # last entries for /why
_log: logging.Logger | None = None


def _logger() -> logging.Logger | None:
    """Rotating file log; None when logging is off (tests / user switch)."""
    global _log
    if _log is not None:
        return _log
    if not config.DECISIONS_LOG:
        return None
    log = logging.getLogger("remix.decisions")
    if not log.handlers:              # never pile handlers onto the singleton
        h = logging.handlers.RotatingFileHandler(
            config.BRAIN_DIR / "decisions.log",
            maxBytes=config.DECISIONS_LOG_MAX_BYTES, backupCount=2,
            encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s",
                                         datefmt="%H:%M:%S"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
    _log = log
    return _log


def mode(kind: str) -> str:
    """off / shadow / live for a decision kind. Unknown kind = off."""
    if kind not in KINDS:
        return "off"
    return _modes.get(kind, KINDS[kind][1])


def set_mode(kind: str, m: str) -> bool:
    """Runtime override (/decisions). Returns True when applied."""
    if kind not in KINDS or m not in ("off", "shadow", "live"):
        return False
    was = mode(kind)
    _modes[kind] = m
    log(kind, m, jev=None, used="mode change", local=f"{was} -> {m}")
    return True


def summary() -> str:
    return " | ".join(f"{k}={mode(k)}" for k in KINDS)


def any_active(kinds: list) -> bool:
    """True when any listed kind is shadow or live (i.e. Jev may be asked)."""
    return any(mode(k) != "off" for k in kinds)


def log(kind: str, m: str, *, jev, used: str, local,
        confidence: float | None = None, extra: str | None = None) -> None:
    """One entry: plain-words recent trail + structured rotating file.

    jev: the Jev answer (str/float) or None (not asked / failed / off).
    used: "jev" (live shipped it) | "local" (fell back or shadow) | free text.
    local: what the local logic did or would have done.
    """
    bits = [kind, f"[{m}]"]
    if jev is not None:
        conf = f" ({confidence:.2f})" if confidence is not None else ""
        bits.append(f"jev={jev}{conf}")
    else:
        bits.append("jev=-")
    bits.append(f"local={local}")
    bits.append(f"used={used}")
    if extra:
        bits.append(extra)
    line = " ".join(bits)
    _recent.append(f"{time.strftime('%H:%M:%S')} {line}")
    lg = _logger()
    if lg:
        try:
            lg.info(line)
        except Exception:
            pass


def recent(n: int = 6) -> list:
    """The last n entries, oldest first, for /why."""
    if n <= 0:
        return []
    return list(_recent)[-n:]
