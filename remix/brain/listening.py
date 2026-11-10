# listening.py — she listens WHILE you type or talk (the "negative latency" spike).
#
# Pure logic: no network, no LLM, no asyncio. main.py feeds it the PARTIAL line
# (typed in the bar, or rolling Whisper partials while push-to-talk is held)
# and sends whatever it returns. It does three jobs:
#
#   1. REFLEXES ($0)  her gaze follows your text; eyes/mouth react to strong
#                     cues in the last few words; a few rhythm tells (you
#                     deleted a lot, you wrote a wall, you stalled).
#   2. PEEK ASKS      at a pause it says "worth asking Jev what she'd feel so
#                     far" (main.py makes the call; the credit budget lives in
#                     jev.py and is opt-in: REMIX_LISTEN_JEV=1).
#   3. COMMIT         when the final line lands, decide whether the partial
#                     reaction can STAND FOR it (skip the react call: no wait,
#                     no credit) and record the lead time.
#
# Lead time = how long her reaction was already on screen before you hit
# send / released the key. Positive lead on a reuse is the success case; a
# peek that gets thrown away is logged too, because that is the price.
import re
import time
from collections import deque
from typing import NamedTuple

from . import config
from .faces import NEUTRAL, Face

# ---------------------------------------------------------------- tuning
# (module constants, not env knobs: these are feel, not policy)
CUE_TAIL_WORDS = 10        # strong cues are looked for in the last N words only
FACE_MIN_GAP_S = 0.8       # min time between her listening-face changes
LOOK_MIN_GAP_S = 0.35      # min time between gaze updates while you type
LOOK_MIN_STEP = 0.08       # ignore gaze moves smaller than this
CUE_FADE_S = 2.0           # a cue face relaxes this long after its words left the tail
REWRITE_DROP = 8           # chars deleted (vs the last 2.5 s) = "you're rewriting that"
REWRITE_WINDOW_S = 2.5
REWRITE_COOLDOWN_S = 10.0
WALL_CHARS = 160           # a wall of text
STALL_LOOK_S = 2.5         # you stopped: she looks at you
STALL_TWITCH_S = 6.0       # still stopped: one impatient twitch
RELEASE_S = 14.0           # gave up waiting: she goes back to her baseline
PEEK_MAX_STALE_S = 8.0     # don't ask about a line that has been sitting this long
PEEK_NEW_CHARS = 12        # how much new text justifies a second peek

# Where her eyes sit while she reads along the bar (x sweeps with the text).
READ_X0, READ_X1, READ_Y = -0.55, -0.15, -0.7
READ_FULL_CHARS = 60

_WORD = re.compile(r"[a-z0-9']+")

# Strong cues only. These run on a PARTIAL line, so single words that mean
# different things in context ("help", "die", "dead") are deliberately absent:
# "can you help me with" must not make her flinch.
_CUES = [
    ("insult", re.compile(
        r"\b(fuck (you|off)|screw you|shut up|piss off|asshole|you suck|"
        r"useless|stupid|idiot|hate you|trash)\b", re.I), Face("anger", "pout")),
    ("warmth", re.compile(
        r"\b(thanks|thank you|love you|you're the best|good job|so cute|"
        r"proud of you)\b", re.I), Face("tsun", "pout")),
    ("low", re.compile(
        r"\b(sorry|my bad|bad day|so tired|can't sleep|feel sick|crying|"
        r"i'm sad|it hurts)\b", re.I), Face("tired", "default")),
    ("alarm", re.compile(
        r"\b(crash(ed|ing)?|overheat(ed|ing)?|on fire|panic(king)?|data loss|"
        r"deleted everything|won't boot)\b", re.I), Face("spooked", "none")),
    ("joke", re.compile(
        r"\b(lol|lmao|haha+|just kidding|joking)\b", re.I),
     Face("bottomlid", "smirk")),
    ("meh", re.compile(r"\b(ugh+|bruh|whatever|meh)\b", re.I),
     Face("really", "default")),
]


# ---------------------------------------------------------------- types

class Reflex(NamedTuple):
    msg: dict      # a protocol message (pose / look / twitch), ready to send
    why: str       # for the log


class PeekRequest(NamedTuple):
    text: str
    seq: int


class Tick(NamedTuple):
    reflexes: list
    peek: "PeekRequest | None"
    release: bool          # she waited long enough: go back to baseline


class Commit(NamedTuple):
    reuse: object          # a jev.Decision (duck-typed) or None
    lead_s: "float | None" # how long that reaction was on screen before commit
    peeked: bool           # a peek had been applied (reused or discarded)
    local_lead_s: "float | None"   # time since her first local reflex


class Stats:
    """Counters so 'does this work' is a number, not a feeling."""

    def __init__(self) -> None:
        self.exchanges = 0
        self.local_reflexes = 0
        self.peeks_asked = 0
        self.peeks_applied = 0
        self.peeks_stale = 0         # came back after the line moved on
        self.reused = 0              # partial reaction stood for the final line
        self.reused_inflight = 0     # ... still in flight; awaited instead of re-asking
        self.discarded = 0           # peek applied, final line differed
        self.lead_sum = 0.0
        self.local_lead_sum = 0.0
        self.local_lead_n = 0

    def summary(self) -> str:
        ask = self.peeks_asked
        used = self.reused + self.reused_inflight
        lead = (self.lead_sum / self.reused) if self.reused else 0.0
        local = (self.local_lead_sum / self.local_lead_n) if self.local_lead_n else 0.0
        hit = f"{100 * used / ask:.0f}%" if ask else "n/a"
        return (f"{self.exchanges} lines, {self.local_reflexes} local reflexes "
                f"(avg {local:.1f}s ahead); jev peeks {ask} asked, "
                f"{self.peeks_applied} shown, {used} reused ({hit}), "
                f"{self.discarded} discarded, {self.peeks_stale} stale; "
                f"avg reused lead {lead:.1f}s")


# ---------------------------------------------------------------- helpers

def words(text: str) -> list:
    return _WORD.findall((text or "").lower())


def similar(final: str, partial: str, slack_words: "int | None" = None) -> bool:
    """Could a reaction to `partial` stand for `final`?

    Yes when the final line is the partial plus at most `slack_words` more
    words. The partial's LAST word may be cut off ("can you help me wi"), so
    it is allowed to differ. Anything that rewrites earlier words is a
    different line.
    """
    if slack_words is None:
        slack_words = config.LISTEN_REUSE_SLACK_WORDS
    p, f = words(partial), words(final)
    if not p or not f:
        return False
    if p == f:
        return True
    stem = p[:-1] if len(p) > 3 else p
    if f[:len(stem)] != stem:
        return False
    return (len(f) - len(p)) <= slack_words


def tail(text: str, n: int = CUE_TAIL_WORDS) -> str:
    return " ".join((text or "").split()[-n:])


def cue_for(text: str):
    """(name, Face) of the strong cue in the last few words, or None."""
    t = tail(text)
    for name, pat, face in _CUES:
        if pat.search(t):
            return name, face
    return None


def read_look(n_chars: int) -> list:
    """Gaze that sweeps along the bar as the line grows."""
    frac = max(0.0, min(1.0, n_chars / READ_FULL_CHARS))
    return [round(READ_X0 + (READ_X1 - READ_X0) * frac, 3), READ_Y]


# ---------------------------------------------------------------- listener

class _Peek(NamedTuple):
    text: str
    decision: object
    applied_at: float


class Listener:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self.stats = Stats()
        self.peek_enabled = False     # main.py: Jev available AND REMIX_LISTEN_JEV=1
        self.last_look: list = [0.0, -0.15]
        self.reset()

    # --------------------------------------------------------------- state

    def reset(self) -> None:
        """The exchange is over (committed, cancelled, or she gave up)."""
        self.active = False
        self.text = ""
        self.source = "typing"
        self._events: deque = deque(maxlen=96)     # (t, len)
        self._first_reflex_at = None
        self._last_change_at = 0.0
        self._max_len = 0
        self._cur_face = NEUTRAL
        self._cue_name = None
        self._cue_gone_at = None
        self._pending_cue = None      # a cue seen while the face-change throttle was closed
        self._last_face_at = -1e9
        self._last_look_at = -1e9
        self._last_look_x = None
        self._last_rewrite_at = -1e9
        self._once: set = set()
        self._peek_seq = 0
        self._peek_count = 0
        self._peek_text = ""          # text of the last peek REQUESTED
        self._peek: "_Peek | None" = None

    def _forget_peek(self) -> None:
        """The thought changed: an old reaction no longer applies."""
        self._peek = None
        self._peek_text = ""
        self._peek_count = 0

    def cancel(self) -> bool:
        """Empty line (Esc / cleared). True when she was actually listening."""
        was = self.active
        self.reset()
        return was

    release = cancel

    # --------------------------------------------------------------- input

    def feed(self, text: str, source: str = "typing", now: "float | None" = None,
             quiet: bool = False) -> list:
        """One partial line. Returns Reflexes to send ([] when `quiet`: she is
        mid-speech, so track the line but don't stomp her performance)."""
        now = self._clock() if now is None else now
        text = (text or "").strip()
        if not text:
            self.cancel()
            return []
        out: list = []
        if not self.active:
            self.active = True
            self.source = source
            self._last_change_at = now
            self._events.clear()
            self._max_len = 0
            if not quiet:
                start = (read_look(len(text)) if source == "typing"
                         else [0.0, -0.1])
                # (onset does not start the face-change throttle: a cue in
                # the very same partial must still be able to show)
                out.append(self._face(NEUTRAL, now, "onset", look=start,
                                      force=True, stamp=False))
                self._last_look_x, self._last_look_at = start[0], now  # the onset already looked
        prev = self.text
        self.text = text
        if text != prev:
            self._last_change_at = now
        self._events.append((now, len(text)))
        self._max_len = max(self._max_len, len(text))

        # A much shorter line = a new thought; the old reaction is stale.
        if self._max_len >= 12 and len(text) < 0.4 * self._max_len:
            self._forget_peek()
            self._max_len = len(text)

        if quiet:
            return []

        typing = (self.source == "typing")
        # --- strong content cue in the last few words
        cue = cue_for(text)
        if cue:
            name, face = cue
            self._cue_gone_at = None
            if name != self._cue_name or face != self._cur_face:
                if self._gap_ok(now):
                    self._cue_name = name
                    self._pending_cue = None
                    out.append(self._face(face, now, f"cue:{name}", force=True))
                else:
                    self._pending_cue = (name, face)   # tick() shows it
        else:
            self._pending_cue = None
            if self._cue_name and self._cue_gone_at is None:
                self._cue_gone_at = now

        # --- rhythm tells (typing only: a transcript that shrinks is just
        # Whisper revising itself, not you deleting)
        if typing:
            recent_max = max((l for t, l in self._events
                              if now - t <= REWRITE_WINDOW_S), default=0)
            if (recent_max - len(text) >= REWRITE_DROP
                    and now - self._last_rewrite_at >= REWRITE_COOLDOWN_S):
                self._last_rewrite_at = now
                r = self._face(Face("really", "default"), now, "rewrite",
                               force=True)
                if r:
                    out.append(r)
            if len(text) >= WALL_CHARS and "wall" not in self._once:
                self._once.add("wall")
                r = self._face(Face("tired", "default"), now, "wall", force=True)
                if r:
                    out.append(r)
            # her eyes read along the bar as the line grows
            if self._cue_name is None:
                lk = self._sweep(len(text), now)
                if lk:
                    out.append(lk)
        if out and self._first_reflex_at is None:
            self._first_reflex_at = now
        self.stats.local_reflexes += sum(
            1 for r in out if r.msg.get("type") == "pose")
        return out

    def tick(self, now: "float | None" = None, quiet: bool = False,
             peek_busy: bool = False) -> Tick:
        """Called every ~0.25 s while active: pauses, stalls, peek asks."""
        now = self._clock() if now is None else now
        if not self.active:
            return Tick([], None, False)
        idle = now - self._last_change_at
        out: list = []
        release = False

        if not quiet:
            if self._pending_cue and self._gap_ok(now):
                name, face = self._pending_cue
                self._pending_cue = None
                self._cue_gone_at = None
                self._cue_name = name
                out.append(self._face(face, now, f"cue:{name}", force=True))
            # a cue face relaxes once its words have left the tail
            if (self._cue_name and self._cue_gone_at is not None
                    and now - self._cue_gone_at >= CUE_FADE_S):
                self._cue_name = None
                self._cue_gone_at = None
                r = self._face(NEUTRAL, now, "cue-fade", force=True)
                if r:
                    out.append(r)
            if self.source == "typing":
                if idle >= STALL_LOOK_S and "stall-look" not in self._once:
                    self._once.add("stall-look")
                    self.last_look = [0.0, -0.05]
                    out.append(Reflex({"type": "look", "look": [0.0, -0.05]},
                                      "stalled: looks at you"))
                if idle >= STALL_TWITCH_S and "stall-twitch" not in self._once:
                    self._once.add("stall-twitch")
                    out.append(Reflex({"type": "twitch"}, "stalled: twitch"))
        if idle >= RELEASE_S:
            release = True

        peek = None
        if (not quiet and not peek_busy and not release
                and self._peek_ok(now, idle)):
            self._peek_seq += 1
            self._peek_count += 1
            self._peek_text = self.text
            self.stats.peeks_asked += 1
            peek = PeekRequest(self.text, self._peek_seq)
        if out and self._first_reflex_at is None:
            self._first_reflex_at = now
        return Tick(out, peek, release)

    # ------------------------------------------------------------- peeking

    def _peek_ok(self, now: float, idle: float) -> bool:
        if not self.peek_enabled:
            return False
        if self._peek_count >= config.LISTEN_JEV_PER_EXCHANGE:
            return False
        if idle < config.LISTEN_PAUSE_S or idle > PEEK_MAX_STALE_S:
            return False
        need = config.LISTEN_MIN_WORDS if self.source == "typing" else max(
            2, config.LISTEN_MIN_WORDS - 1)
        if len(words(self.text)) < need:
            return False
        if self._peek_text:
            grew = len(self.text) - len(self._peek_text)
            if similar(self.text, self._peek_text, slack_words=2) and grew < PEEK_NEW_CHARS:
                return False
        return True

    def accept_peek(self, seq: int, decision, now: "float | None" = None) -> bool:
        """A peek came back. True = still about the live line, show it."""
        now = self._clock() if now is None else now
        if (not self.active or seq != self._peek_seq
                or not similar(self.text, self._peek_text, slack_words=3)):
            self.stats.peeks_stale += 1
            return False
        self._peek = _Peek(self._peek_text, decision, now)
        self.stats.peeks_applied += 1
        return True

    # -------------------------------------------------------------- commit

    def commit(self, final_text: str, now: "float | None" = None) -> Commit:
        """The line is final. Reuse the partial reaction if it still fits."""
        now = self._clock() if now is None else now
        peek, first = self._peek, self._first_reflex_at
        reuse = lead = local = None
        if peek is not None:
            if similar(final_text, peek.text):
                reuse = peek.decision
                lead = max(0.0, now - peek.applied_at)
                self.stats.reused += 1
                self.stats.lead_sum += lead
            else:
                self.stats.discarded += 1
        if first is not None:
            local = max(0.0, now - first)
            self.stats.local_lead_sum += local
            self.stats.local_lead_n += 1
        if self.active:
            self.stats.exchanges += 1
        self.reset()
        return Commit(reuse, lead, peek is not None, local)

    # ------------------------------------------------------------- internal

    def _gap_ok(self, now: float) -> bool:
        return now - self._last_face_at >= FACE_MIN_GAP_S

    def _face(self, face: Face, now: float, why: str, look=None,
              force: bool = False, stamp: bool = True) -> "Reflex | None":
        if face == self._cur_face and not force:
            return None
        if not force and not self._gap_ok(now):
            return None
        self._cur_face = face
        if stamp:
            self._last_face_at = now
        lk = look if look is not None else self.last_look
        self.last_look = list(lk)
        return Reflex(face.msg(look=lk), f"{why} -> {face}")

    def _sweep(self, n_chars: int, now: float) -> "Reflex | None":
        if now - self._last_look_at < LOOK_MIN_GAP_S:
            return None
        lk = read_look(n_chars)
        if (self._last_look_x is not None
                and abs(lk[0] - self._last_look_x) < LOOK_MIN_STEP):
            return None
        self._last_look_at = now
        self._last_look_x = lk[0]
        self.last_look = lk
        return Reflex({"type": "look", "look": lk}, "reads along")
