# proactive.py — unprompted lines: awareness events -> the URGE to speak.
# (the "random speech triggers")
#
# Split of labor (the spirit fix, 2026-11-08 — no pre-written pools):
#   THIS module is the reflex, and it never writes words. It is purely local,
#   $0, no network: edges, cooldowns, caps, suppression. What it returns is a
#   PLAN: which trigger fired, a locally-picked stance, the face that stance
#   maps to, and a one-clause description of what she just noticed.
#   speech.generate_proactive_line() then writes the actual line FRESH, through
#   the same $0 provider pool that writes her replies — her voice, grounded,
#   screened, deduped. If generation fails or finds nothing worth saying, she
#   stays quiet: "saying nothing beats filler" is her own rule, so there is no
#   canned fallback.
#   Jev is NEVER called by silent life (credit rule stands): the stance is
#   chosen locally per trigger and mapped to a face without any LLM.
#
# A plan only fires when ALL of these hold:
#   * PROACTIVE enabled (REMIX_PROACTIVE=1, or /quiet toggles at runtime)
#   * she is not mid-speech, the mic is closed, and the last exchange was
#     not just now (never interrupt the human or herself)
#   * not quiet hours (REMIX_QUIET_HOURS="1-8" = nothing between 01:00-08:59)
#   * NOT fullscreen — DND while fullscreen is spec (she doesn't interrupt
#     a movie; the pose/notice system still shows her mood silently)
#   * MIN_GAP since her last unprompted line, per-trigger cooldowns, edges
#     fire once per crossing, and a daily cap on top of all that.
import random
import time

from . import config
from .faces import Face, POSES

# Exchange hush: right after she answered you, she shuts up for a bit.
EXCHANGE_HUSH_S = 120.0

# Local stance -> face mapping (no Jev call for proactive lines; the trigger
# names the stance options, this turns the pick into shapes she has).
# "concerned" reads as plain + soft per the sysprompt, so it stays
# neutral-eyed with a low look.
STANCE_FACE = {
    "flat": Face("default", "default"),
    "playful": POSES["smirk"],
    "pout": POSES["pout"],
    "tsun": POSES["tsun"],
    "annoyed": Face("anger", "pout"),
    "concerned": Face("default", "default"),
    "fond": Face("tsun", "pout"),
}
CONCERNED_LOOK = [0.0, 0.15]     # gaze slightly down: soft, not staring

# Local stance options per trigger (rng picks one). Deliberately narrow: the
# stance must FIT what she noticed, variety comes from the generated words.
STANCE_CHOICES = {
    "greeting": ["playful", "tsun", "fond"],
    "awaken": ["flat", "playful", "pout"],
    "battery_low": ["concerned", "annoyed"],
    "plugged": ["tsun", "playful"],
    "welcome_back": ["playful", "flat", "fond"],
    "idle_gentle": ["playful", "pout", "flat"],
    "idle_deep": ["concerned"],
    "muted_long": ["pout", "tsun", "flat"],
    "long_session": ["concerned", "flat"],
    "app_stuck": ["flat", "playful", "pout"],
    "ambient": ["flat", "playful", "pout"],
    "poke": ["playful", "tsun", "flat"],
}

# Per-trigger minimum spacing (seconds), on top of the global MIN_GAP.
TRIGGER_COOLDOWN_S = {
    "greeting": 1.0,          # once per boot anyway
    "awaken": 1.0,            # once per boot anyway
    "battery_low": 2400.0,    # 40 min
    "plugged": 10800.0,       # 3 h
    "welcome_back": 3600.0,   # 1 h
    "idle_gentle": 1.0,       # once per idle episode (edge-gated anyway)
    "idle_deep": 1.0,
    "muted_long": 7200.0,     # 2 h
    "long_session": 7200.0,   # 2 h
    "app_stuck": 10800.0,     # 3 h
    "ambient": config.AMBIENT_COOLDOWN_S,
}

# Triggers whose room-notice doubles as the "urgent" flag (6.4: she's being
# ignored/muted and got pissed about it; main raises the marker).
URGENT_ON = ("muted_long", "idle_deep")


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _quiet_hour(wall_hour: int | None) -> bool:
    """REMIX_QUIET_HOURS='1-8' -> no chatter 01:00 <= h < 08:00. '' = off."""
    raw = (config.QUIET_HOURS or "").strip()
    if not raw or wall_hour is None:
        return False
    try:
        a, _, b = raw.partition("-")
        a, b = int(a), int(b)
    except ValueError:
        return False
    if a == b:
        return False
    if a < b:
        return a <= wall_hour < b
    return wall_hour >= a or wall_hour < b   # wraps midnight, e.g. 22-6


def _situation(trigger: str, aware: dict, batt: float | None,
               away_mins: int | None = None,
               stuck_mins: int | None = None) -> str:
    """What she just noticed, as one plain clause. This is the SEED her words
    grow from — speech.generate_proactive_line() reacts to it. Every number
    here comes straight from a real sense; nothing is invented."""
    aware = aware or {}
    if trigger == "greeting":
        return ("a new session just started and they just showed up at the "
                "keyboard")
    if trigger == "awaken":
        from . import memory
        gap = memory.elapsed_text((memory.awaken() or {}).get("elapsed_s")) \
            or "some time"
        return (f"you were OFF (the whole machine was shut down) and just "
                f"woke up after about {gap}; they are at the keyboard now")
    if trigger == "ambient":
        app = aware.get("app")
        now = aware.get("up_s")
        parts = []
        if app:
            parts.append(f"they have {app} in focus")
        hour = time.localtime().tm_hour
        when = ("morning" if hour < 11 else "midday" if hour < 14 else
                "afternoon" if hour < 18 else "evening" if hour < 23 else
                "dead of night")
        parts.append(f"it's {when}")
        if (aware.get("muted")):
            parts.append("the sound is muted")
        return ("nothing happened — you just felt like saying something "
                "about " + (", ".join(parts[:2]) or "where you are"))
    if trigger == "battery_low":
        return (f"the battery just crossed {round(config.BATT_LOW)}% and is "
                f"now at {round(batt)}%, and the machine is running on "
                f"battery power")
    if trigger == "plugged":
        return "the power cable was just plugged back in"
    if trigger == "welcome_back":
        if away_mins:
            return (f"they were away from the keyboard for about "
                    f"{away_mins} minutes and just came back")
        return "they just came back to the keyboard"   # honest when unknown
    if trigger == "idle_gentle":
        idle = _f(aware.get("idle_s")) or 0.0
        return f"the keyboard has been quiet for about {max(1, round(idle / 60))} minutes"
    if trigger == "idle_deep":
        idle = _f(aware.get("idle_s")) or 0.0
        return (f"it has been about {max(1, round(idle / 60))} minutes of "
                f"complete silence and they never said they're ok")
    if trigger == "muted_long":
        muted = _f(aware.get("muted_s")) or 0.0
        return (f"the sound has been muted or nearly silent for about "
                f"{max(1, round(muted / 60))} minutes")
    if trigger == "long_session":
        up = _f(aware.get("up_s")) or 0.0
        return f"this session has been running about {max(1, round(up / 3600))} hours straight"
    if trigger == "app_stuck":
        mins = stuck_mins or 90
        title = (aware or {}).get("title")
        where = (aware.get("app") or "the same app")
        if title:
            where += f" (window title: {title})"
        return (f"they have had {where} in "
                f"focus for about {mins} minutes straight")
    if trigger == "poke":
        return "they poked you and want to hear one line right now"
    return "something on the machine changed"


class Proactive:
    """Stateful trigger engine. tick() is pure-in/plan-out: feed it the latest
    awareness snapshot + vitals, get a plan dict (no words) or None. The words
    are written afterwards by speech.generate_proactive_line()."""

    def __init__(self, rng: random.Random | None = None,
                 enabled: bool | None = None,
                 awaken_s: float | None = None) -> None:
        self.rng = rng or random.Random()
        self.enabled = config.PROACTIVE if enabled is None else enabled
        # awaken_s: how long she was OFF before this boot (memory at boot).
        # A real gap turns the boot greeting into an awaken line (proposal 4).
        self.awaken_s = awaken_s
        self.last_line_at = -1e12
        self.day_key = None          # (for the daily cap)
        self.day_count = 0
        self._last = {}              # trigger -> monotonic of last fire
        self._last_ambient = -1e12
        # edge memory (previous observations; None = never seen)
        self.prev_batt = None
        self.prev_plugged = None
        self.prev_idle = None
        self.greeted = False
        self.episode = {"gentle": False, "deep": False}

    # ---------------------------------------------------------------- API

    def tick(self, aware: dict, vitals: dict | None = None, now: float | None = None,
             mic_open: bool = False, last_exchange_at: float | None = None,
             busy_until: float = 0.0, wall_hour: int | None = None) -> dict | None:
        """One engine step. Returns {'trigger','stance','face','look',
        'situation'} — the urge, never the words."""
        now = time.monotonic() if now is None else now
        aware = aware or {}
        vitals = vitals or {}

        # --- edges (computed BEFORE suppression, updated ALWAYS, so a DND
        # window eats the event instead of replaying it later) ---
        batt = _f(vitals.get("battery"))
        plugged = aware.get("plugged")
        idle = _f(aware.get("idle_s"))
        prev_idle = self.prev_idle          # the value the EDGES were computed from

        low_edge = (self.prev_batt is not None and batt is not None
                    and plugged is False
                    and self.prev_batt > config.BATT_LOW >= batt)
        plug_edge = (self.prev_plugged is False and plugged is True)
        if batt is not None:
            self.prev_batt = batt
        if plugged is not None:
            self.prev_plugged = plugged

        away = (prev_idle is not None and idle is not None
                and prev_idle >= config.AWAY_S > idle)
        gentle_edge = (prev_idle is not None and idle is not None
                       and prev_idle < config.IDLE_GENTLE_S <= idle)
        deep_edge = (prev_idle is not None and idle is not None
                     and prev_idle < config.IDLE_DEEP_S <= idle)
        if idle is not None:
            self.prev_idle = idle
        if idle is not None and idle < 60.0:   # fresh idle episode
            self.episode = {"gentle": False, "deep": False}

        # --- suppression gates ---
        if not self.enabled:
            return None
        if now < busy_until:                       # she is mid-speech
            return None
        if now - self.last_line_at < config.PROACTIVE_MIN_GAP_S:
            return None
        if mic_open:                               # never talk over the mic
            return None
        if (last_exchange_at is not None
                and now - last_exchange_at < EXCHANGE_HUSH_S):
            return None                            # you just talked; shh
        if _quiet_hour(wall_hour if wall_hour is not None
                       else time.localtime().tm_hour):
            return None
        if aware.get("fullscreen"):                # spec: DND while fullscreen
            return None
        if self._daily_cap(now) is False:
            return None

        # --- candidates, most urgent first ---
        plan = None
        if not self.greeted:
            if self.awaken_s and self.awaken_s >= config.AWAKEN_MIN_S:
                plan = "awaken"        # the greeting slot becomes a wake-up
            else:
                plan = "greeting"
            self.greeted = True        # one boot line either way
        elif low_edge:
            plan = "battery_low"
        elif plug_edge:
            plan = "plugged"
        elif away and self._due("welcome_back", now):
            plan = "welcome_back"
        elif deep_edge and not self.episode["deep"]:
            plan = "idle_deep"
        elif gentle_edge and not self.episode["gentle"]:
            plan = "idle_gentle"
        elif (aware.get("muted_s") or 0) >= config.MUTED_NAG_S \
                and self._due("muted_long", now):
            plan = "muted_long"
        elif (aware.get("up_s") or 0) >= config.SESSION_LONG_S \
                and idle is not None and idle < 300.0 \
                and self._due("long_session", now):
            plan = "long_session"
        elif (aware.get("app_since") is not None and idle is not None
                and idle < 300.0
                and now - aware["app_since"] >= config.APP_STUCK_S
                and self._due("app_stuck", now)):
            plan = "app_stuck"
        elif self._ambient_ok(now) and \
                self.rng.random() < config.AMBIENT_RATE:
            plan = "ambient"

        if plan is None:
            return None

        stance = self.rng.choice(STANCE_CHOICES.get(plan, ["flat"]))

        # --- bookkeeping: the slot is spent whether or not the words
        # materialize (a failed generation is silence for this slot — never a
        # canned fallback — and MIN_GAP keeps retries from hammering the pool)
        self.last_line_at = now
        self._last[plan] = now
        self.day_count += 1
        if plan == "greeting":
            self.greeted = True
        if plan == "ambient":
            self._last_ambient = now
        if plan in ("idle_gentle", "idle_deep"):
            self.episode[plan[5:]] = True

        return {"trigger": plan, "stance": stance,
                "face": STANCE_FACE.get(stance, POSES["default"]),
                "look": CONCERNED_LOOK if stance == "concerned" else None,
                "urgent": plan in URGENT_ON,
                "situation": _situation(
                    plan, aware, batt,
                    away_mins=round((_f(prev_idle) or 0) / 60)
                    if plan == "welcome_back" else None,
                    stuck_mins=round((now - aware["app_since"]) / 60)
                    if plan == "app_stuck" and aware.get("app_since") is not None
                    else None)}

    def force(self, aware: dict | None = None, vitals: dict | None = None,
              now: float | None = None) -> dict | None:
        """/poke: the urge to say something right now, ignoring cooldowns.
        The words still come from the generation step, not from a pool."""
        now = time.monotonic() if now is None else now
        self.last_line_at = now
        self.day_count += 1
        stance = self.rng.choice(STANCE_CHOICES["poke"])
        return {"trigger": "poke", "stance": stance,
                "face": STANCE_FACE.get(stance, POSES["default"]),
                "look": None,
                "situation": _situation("poke", aware or {}, None)}

    # ----------------------------------------------------------- internals

    def _daily_cap(self, now: float) -> bool:
        """True = a line is still allowed today."""
        key = time.strftime("%Y-%m-%d")
        if key != self.day_key:
            self.day_key = key
            self.day_count = 0
        return self.day_count < config.PROACTIVE_MAX_PER_DAY

    def _due(self, trigger: str, now: float) -> bool:
        return now - self._last.get(trigger, -1e12) >= \
            TRIGGER_COOLDOWN_S.get(trigger, 3600.0)

    def _ambient_ok(self, now: float) -> bool:
        """Ambient musings (2b): own cooldown, on top of MIN_GAP/day-cap."""
        return now - self._last_ambient >= config.AMBIENT_COOLDOWN_S
