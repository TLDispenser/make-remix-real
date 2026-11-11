# jev.py — the JevBrain (left brain, System One, credit-backed).
#
# Two kinds of call, both small, both falling back to $0 local behaviour:
#   react(user_line)        -> stance + the instant FACE (eyes + mouth), sent
#                              before any words exist. Feeds the {stance} slot.
#   act(user_line, beats)   -> one FACE per beat of the line she is about to say.
#                              Asked AFTER the words exist, with each beat's real
#                              text in its question (asked before, every beat
#                              got the same prior: that was the endless smirk).
#                              Skipped for one-beat lines (nothing to vary).
#
# Eyes and mouth are separate choice questions (faces.py), so Jev mixes them.
# Variety: the answer is SAMPLED from Jev's own probabilities with a penalty on
# whatever the face showed recently, instead of always taking the top pick.
#
# Budget policy (see RESEARCH_NOTES.md): silent life NEVER calls Jev; cooldown
# between exchanges (react calls); hard hourly cap over BOTH call kinds (over cap
# -> local fallback, silently); same-state cache = zero credits; every failure
# path returns None and the caller does its own $0 thing.
# Request/response shapes: https://api.typesafe.ai/openapi.json (RESEARCH_NOTES).
import asyncio
import hashlib
import json
import random
import time
import urllib.request
from collections import deque
from typing import NamedTuple

from . import config
from . import decisions
from .faces import EYES_TEXT, MOUTHS_TEXT, NEUTRAL, Face, Vocabulary

# She/her, dry, secretly fond — stance choices the sysprompt understands.
# (1b soften: "fond" may now be plain warm; the barbs stay OUT of the words.)
STANCE_OPTION_TEXT = {
    "flat": "flat and tired; no coloring, facts only",
    "annoyed": "irritation on the surface; keep the barbs out",
    "fond": "quiet fondness; a small warm word is allowed",
    "concerned": "the bit stops; plainly kind and gentle, still short",
    "alarmed": "something about the machine feels wrong; worry under the sarcasm",
    "playful": "dry mischief; one sly jab is allowed, no gushing",
}

# Stance wording for silent-life choices (names not in STANCE_OPTION_TEXT).
_PROACTIVE_STANCE_TEXT = {
    "flat": "flat and tired; facts only",
    "playful": "dry mischief; one sly jab is allowed",
    "pout": "huffy pout; wants attention, without saying so",
    "tsun": "prickly on the surface, fond underneath",
    "annoyed": "irritation on the surface",
    "concerned": "plainly kind and gentle, still short",
    "fond": "quiet fondness; a small warm word is allowed",
}

PEEK_SCENE = (
    "The user is PARTWAY through a message to Remix (typed or spoken); the line "
    "may be cut off mid-sentence. Remix is a deadpan, tired, dry laptop "
    "character who is on the user's side. She is LISTENING, not answering yet. "
    "Choose the face she makes while she listens to what has been said so far: "
    "plain, present and attentive unless the words clearly call for more. Do "
    "not guess at what the unfinished part will say. Insults at her -> "
    "irritation or deadpan; something sweet -> flustered; a sad or hurt user "
    "-> plain and soft; a scary system problem -> startled; something boring "
    "-> tired or deadpan. recent_faces lists what her face just showed: avoid "
    "repeating it unless the feeling is unchanged."
)

REACT_SCENE = (
    "The user just sent Remix a message. Remix is a deadpan, tired, dry laptop "
    "character — dry does NOT mean cruel: she is on the user's side, and she "
    "cooperates. Her baseline is flat, so plain shapes are right when nothing "
    "in the message calls for more. Choose her first gut reaction as a face "
    "made of separate EYES and MOUTH. Smug is for real teasing or mischief "
    "only, not the default. Match the line: insults or swearing at her -> "
    "irritation or deadpan; praise or something sweet -> flustered; a "
    "question or request -> pay attention, plain and present; a sad or hurt "
    "user -> plain and soft; something gross or weird -> disgust or deadpan; "
    "a scary system problem -> startled; a boring or lazy line -> tired or "
    "deadpan. recent_faces lists what her face just showed: avoid repeating "
    "it unless the feeling is unchanged."
)

ACT_SCENE = (
    "Remix is about to say these beats, in order. For each beat choose the EYES "
    "and MOUTH that fit that beat's wording and feeling. Let the face change when "
    "the feeling changes; a flat beat may stay plain. Do not give every beat the "
    "same face, and do not default to smug."
)


class Decision(NamedTuple):
    stance: str            # stance text for the sysprompt slot
    stance_name: str
    face: Face             # instant reaction (eyes + mouth)
    confidence: float      # of the stance answer (representative)
    latency_s: float
    from_cache: bool


class ActPlan(NamedTuple):
    faces: list            # one Face per beat
    latency_s: float
    from_cache: bool
    unseen: float | None = None   # judge: p(reply claims to see what it can't)


class JevBrain:
    def __init__(self, rng: random.Random | None = None,
                 seed_faces: list | None = None,
                 variety_hook: "callable | None" = None) -> None:
        """seed_faces: 'eyes/mouth' strings from the persisted journal
        (memory). variety_hook: called with str(face) on every shown face so
        the journal remembers her recent looks across restarts."""
        self.vocab = Vocabulary()
        self.rng = rng or random.Random()
        self._last_react_at = 0.0
        self._last_peek_at = 0.0
        self._call_times: list = []          # hourly-cap window (both call kinds)
        self._pro_call_times: list = []      # silent-life decision window
        self._cache: dict = {}               # sig -> (expiry, value)
        self._recent: deque = deque(maxlen=4)  # last faces shown (variety memory)
        self._hook = variety_hook
        if seed_faces:
            # journal order is newest-first; rebuild so `.recent[-1]` is newest
            for s in list(seed_faces)[:4][::-1]:
                try:
                    e, _, m = str(s).partition("/")
                    self._recent.append(Face(e, m))
                except Exception:
                    pass

    def set_available(self, eyes=None, mouths=None) -> None:
        """Godot's `ready` lists the shapes the face actually has."""
        self.vocab.set_available(eyes, mouths)

    # ------------------------------------------------------------ variety

    def note_face(self, face: Face) -> None:
        """Tell the variety memory what the face just showed (any source)."""
        if not self._recent or self._recent[-1] != face:
            self._recent.append(face)
        if self._hook:
            try:
                self._hook(str(face))
            except Exception:
                pass

    def recent_faces(self) -> list:
        return [str(f) for f in self._recent]

    # ------------------------------------------------------------------ API

    async def react(self, user_text: str, vitals: dict) -> Decision | None:
        """Stance + instant face. None when budget/exhausted/broken."""
        now = time.monotonic()
        sig = _signature("react", user_text, vitals, self.recent_faces())
        hit = self._cache.get(sig)
        if hit and hit[0] > now:
            d = hit[1]._replace(from_cache=True)
            print(f"remix-brain: jev reaction CACHED ({d.face}) — 0 credits")
            return d
        if not self._budget_ok(now, exchange_start=True):
            return None
        self._last_react_at = now
        self._call_times.append(now)

        body = {
            "model": config.JEV_MODEL,
            "state": {
                "scene": REACT_SCENE,
                "user_line": user_text,
                "vitals": _vitals_text(vitals),
                "recent_faces": self.recent_faces(),
            },
            "questions": self._react_questions(),
        }
        resp = await self._call(body)
        if resp is None:
            return None
        latency = time.monotonic() - now
        d = self._react_decision(resp, latency)
        self._cache[sig] = (now + config.JEV_CACHE_TTL_S, d)
        self.note_face(d.face)
        return d

    async def peek(self, partial_text: str, vitals: dict) -> Decision | None:
        """A reaction to a line that is STILL BEING typed/spoken (listening.py).

        Same shape as react(), but: a listening-face scene, its own spacing
        (LISTEN_JEV_MIN_GAP_S), and it never touches the react cooldown, so
        the real reaction at send time is not blocked by it. It stops
        LISTEN_JEV_RESERVE calls short of the hourly cap. The caller notes the
        face only if it is actually shown. None = budget/off/failed."""
        now = time.monotonic()
        sig = _signature("peek", partial_text, vitals, self.recent_faces())
        hit = self._cache.get(sig)
        if hit and hit[0] > now:
            d = hit[1]._replace(from_cache=True)
            print(f"remix-brain: jev listening reaction CACHED ({d.face}) — 0 credits")
            return d
        if not self._peek_budget_ok(now):
            return None
        self._last_peek_at = now
        self._call_times.append(now)

        body = {
            "model": config.JEV_MODEL,
            "state": {
                "scene": PEEK_SCENE,
                "user_line": partial_text,
                "vitals": _vitals_text(vitals),
                "recent_faces": self.recent_faces(),
            },
            "questions": self._react_questions(partial=True),
        }
        resp = await self._call(body)
        if resp is None:
            return None
        d = self._react_decision(resp, time.monotonic() - now,
                                 label="listening reaction")
        self._cache[sig] = (now + config.JEV_CACHE_TTL_S, d)
        return d

    async def act(self, user_text: str, beats: list,
                  senses: str | None = None) -> ActPlan | None:
        """One face per beat, plus (when the grounding judge is shadow or
        live) the `unseen` judgment on the finished reply — same request, no
        extra call. Skipped for one-beat lines UNLESS the judge needs it
        (then the one-beat faces are simply unused)."""
        judge = decisions.mode("judge_unseen")
        if len(beats) < 2 and judge == "off":
            return None
        beats = beats[:4]
        now = time.monotonic()
        sig = _signature("act", user_text, {}, [beats, senses, judge])
        hit = self._cache.get(sig)
        if hit and hit[0] > now:
            return hit[1]._replace(from_cache=True)
        if not self._budget_ok(now, exchange_start=False):
            return None
        self._call_times.append(now)

        state = {
            "scene": ACT_SCENE,
            "user_line": user_text,
            "beats": {f"beat{i + 1}": b for i, b in enumerate(beats)},
            "recent_faces": self.recent_faces(),
        }
        questions = self._act_questions(beats)
        if judge != "off":
            # Grounding judge (plan 1.5): does the reply claim to see or know
            # something the state does not list? `senses` is the compact
            # what-she-can-actually-see list.
            state["she_can_sense"] = senses or "nothing beyond her own vitals"
            questions["unseen"] = {
                "type": "noul",
                "instructions": (
                    "Does the reply claim to see, know or check something "
                    "about the user's computer, files, screen or room that "
                    "the she_can_sense list does not include? Numbers about "
                    "her own vitals do not count as unseen."),
            }
        body = {"model": config.JEV_MODEL, "state": state,
                "questions": questions}
        resp = await self._call(body)
        if resp is None:
            return None
        plan = self._act_plan(resp, len(beats), time.monotonic() - now,
                              judge != "off")
        self._cache[sig] = (now + config.JEV_CACHE_TTL_S, plan)
        for f in plan.faces:
            self.note_face(f)
        return plan

    # ----------------------------------------------------------- transport

    PROACTIVE_SCENE = (
        "Remix, a deadpan tired dry laptop character, just NOTICED something "
        "on her own — nobody is talking to her. Decide what she does with "
        "the urge. Nothing happened unless the state says so; do not invent "
        "drama. recent_faces lists what her face just showed: avoid "
        "repeating it unless the feeling is unchanged."
    )

    FORM_OPTION_TEXT = {
        "speak": "say one short line out loud (her usual reaction)",
        "caption": "show the line as on-screen text only, no voice",
        "look": "no words at all: just a face and a glance",
        "quiet": "nothing; keep it to herself this time",
    }

    async def decide_proactive(self, trigger: str, situation: str,
                               stance_options: list) -> dict | None:
        """Silent-life decisions in ONE request (the caps were already
        applied by the trigger engine; this judges INSIDE them):
          stance        choice among the trigger's allowed menu
          should_speak  noul: is one line out loud worth it right now?
          form          choice: speak / caption / look / quiet
        Returns None on budget/off/failure — the caller then does exactly
        what it would have done before this feature existed (logged)."""
        now = time.monotonic()
        sig = _signature("proactive", trigger, {}, [situation,
                                                    self.recent_faces()])
        hit = self._cache.get(sig)
        if hit and hit[0] > now:
            return hit[1]
        if not self._proactive_budget_ok(now):
            return None
        self._call_times.append(now)
        self._pro_call_times.append(now)

        stance_criteria = {
            s: _PROACTIVE_STANCE_TEXT.get(s, s) for s in stance_options}
        body = {
            "model": config.JEV_MODEL,
            "state": {
                "scene": self.PROACTIVE_SCENE,
                "she_just_noticed": situation,
                "trigger": trigger,
                "recent_faces": self.recent_faces(),
            },
            "questions": {
                "stance": {
                    "type": "choice", "criteria": stance_criteria,
                    "instructions": ("The stance she takes for a line about "
                                     "this. One of the offered options only."),
                },
                "should_speak": {
                    "type": "noul",
                    "instructions": (
                        "Is one short line out loud about this worth saying "
                        "right now, or would silence be better? Say yes only "
                        "when the moment genuinely calls for her voice."),
                },
                "form": {
                    "type": "choice", "criteria": dict(self.FORM_OPTION_TEXT),
                    "instructions": ("How she reacts, if she reacts at all. "
                                     "speak unless there is a reason to be "
                                     "quiet or wordless."),
                },
            },
        }
        resp = await self._call(body)
        if resp is None:
            return None
        answers = resp.get("answers", {}) or {}
        usage = resp.get("usage", {}) or {}
        stance = str(_choice(answers.get("stance"), ""))
        if stance not in stance_criteria:
            stance = None
        speak_p = None
        if isinstance(answers.get("should_speak"), dict):
            speak_p = _f(answers["should_speak"].get("noul"))
        form = str(_choice(answers.get("form"), ""))
        if form not in self.FORM_OPTION_TEXT:
            form = None
        d = {"stance": stance, "speak_p": speak_p, "form": form,
             "confidence": _confidence(answers.get("stance")),
             "speak_confidence": _confidence(answers.get("should_speak")),
             "latency_s": time.monotonic() - now}
        print(f"remix-brain: jev proactive decision in {d['latency_s']:.2f}s "
              f"(tokens {usage.get('input_tokens')}/{usage.get('output_tokens')}): "
              f"stance={stance} speak={speak_p} form={form} [{trigger}]")
        self._cache[sig] = (now + config.JEV_CACHE_TTL_S, d)
        return d

    def _proactive_budget_ok(self, now: float) -> bool:
        """Same shared hourly window, plus a dedicated ceiling for
        silent-life decisions, plus a reserve so replies and peeks keep
        priority when the budget runs low."""
        if not config.JEV_ENABLED:
            return False
        window = [t for t in self._call_times if now - t < 3600.0]
        self._call_times = window
        if len(window) >= config.JEV_MAX_PER_HOUR - config.LISTEN_JEV_RESERVE:
            return False
        pro = [t for t in self._pro_call_times if now - t < 3600.0]
        self._pro_call_times = pro
        return len(pro) < config.JEV_PROACTIVE_MAX_PER_HOUR

    # ----------------------------------------------------------- transport

    async def _call(self, body: dict) -> dict | None:
        try:
            return await asyncio.to_thread(
                _post, config.JEV_URL, config.JEV_API_KEY, body,
                config.JEV_TIMEOUT_S)
        except Exception as e:  # timeout / HTTP / JSON — fall back silently
            print(f"remix-brain: jev call failed ({e!r}) — local fallback")
            return None

    def _budget_ok(self, now: float, exchange_start: bool) -> bool:
        if not config.JEV_ENABLED:
            return False
        # The cooldown spaces EXCHANGES. The act call belongs to the exchange
        # its react call opened, so it is not blocked by it.
        if exchange_start and now - self._last_react_at < config.JEV_COOLDOWN_S:
            return False
        window = [t for t in self._call_times if now - t < 3600.0]
        self._call_times = window
        return len(window) < config.JEV_MAX_PER_HOUR

    def _peek_budget_ok(self, now: float) -> bool:
        if not (config.JEV_ENABLED and config.LISTEN_JEV):
            return False
        if now - self._last_peek_at < config.LISTEN_JEV_MIN_GAP_S:
            return False
        window = [t for t in self._call_times if now - t < 3600.0]
        self._call_times = window
        return len(window) < config.JEV_MAX_PER_HOUR - config.LISTEN_JEV_RESERVE

    # ------------------------------------------------------------ questions

    def _react_questions(self, partial: bool = False) -> dict:
        eyes_ask = ("Her EYES while she listens to this unfinished line."
                    if partial else
                    "Her EYES the instant she reads this line, before she says anything.")
        mouth_ask = ("Her MOUTH while she listens to this unfinished line."
                     if partial else
                     "Her MOUTH the instant she reads this line, before she says anything.")
        return {
            "stance": {
                "type": "choice", "criteria": dict(STANCE_OPTION_TEXT),
                "instructions": (
                    "Her stance for answering this line. Hints, not rules: a "
                    "sad, hurt, or genuinely worried user -> concerned (the "
                    "bit stops; getting this one right matters most); praise, "
                    "warmth, or sweetness -> fond; insults or needless "
                    "friction -> annoyed; a scary system problem in the "
                    "vitals or the line -> alarmed; teasing or banter she'd "
                    "secretly enjoy -> playful; only when nothing above "
                    "applies -> flat. flat is her resting stance, not a "
                    "catch-all for emotional lines."),
            },
            "eyes": {
                "type": "choice", "criteria": self._eyes_criteria(),
                "instructions": eyes_ask,
            },
            "mouth": {
                "type": "choice", "criteria": self._mouth_criteria(),
                "instructions": mouth_ask,
            },
        }

    def _act_questions(self, beats: list) -> dict:
        q = {}
        for i, text in enumerate(beats, start=1):
            quoted = text.strip().replace('"', "'")[:140]
            q[f"eyes{i}"] = {
                "type": "choice", "criteria": self._eyes_criteria(),
                "instructions": f'Her EYES while she says beat {i}: "{quoted}"',
            }
            q[f"mouth{i}"] = {
                "type": "choice", "criteria": self._mouth_criteria(),
                "instructions": f'Her MOUTH while she says beat {i}: "{quoted}"',
            }
        return q

    def _eyes_criteria(self) -> dict:
        return {n: EYES_TEXT[n] for n in self.vocab.eyes}

    def _mouth_criteria(self) -> dict:
        return {n: MOUTHS_TEXT[n] for n in self.vocab.mouths}

    # ------------------------------------------------------------- parsing

    def _react_decision(self, resp: dict, latency: float,
                        label: str = "reaction") -> Decision:
        answers = resp.get("answers", {}) or {}
        usage = resp.get("usage", {}) or {}
        stance_name = str(_choice(answers.get("stance"), "flat"))
        if stance_name not in STANCE_OPTION_TEXT:
            stance_name = "flat"
        face = Face(
            self._pick(answers.get("eyes"), self.vocab.eyes, "eyes", []),
            self._pick(answers.get("mouth"), self.vocab.mouths, "mouth", []),
        )
        conf = _confidence(answers.get("stance"))
        print(f"remix-brain: jev {label} in {latency:.2f}s "
              f"(tokens {usage.get('input_tokens')}/{usage.get('output_tokens')}): "
              f"stance={stance_name} ({conf:.2f}) face={face} "
              f"[eyes {_confidence(answers.get('eyes')):.2f}, "
              f"mouth {_confidence(answers.get('mouth')):.2f}] "
              f"recent={self.recent_faces()}")
        return Decision(STANCE_OPTION_TEXT[stance_name], stance_name, face,
                        conf, latency, False)

    def _act_plan(self, resp: dict, n: int, latency: float,
                  judge: bool = False) -> ActPlan:
        answers = resp.get("answers", {}) or {}
        usage = resp.get("usage", {}) or {}
        faces: list = []
        for i in range(1, n + 1):
            prev_e = [faces[-1].eyes] if faces else []
            prev_m = [faces[-1].mouth] if faces else []
            faces.append(Face(
                self._pick(answers.get(f"eyes{i}"), self.vocab.eyes, "eyes", prev_e),
                self._pick(answers.get(f"mouth{i}"), self.vocab.mouths, "mouth", prev_m),
            ))
        unseen = None
        if judge and isinstance(answers.get("unseen"), dict):
            unseen = _f(answers["unseen"].get("noul"))
        print(f"remix-brain: jev acting plan in {latency:.2f}s "
              f"(tokens {usage.get('input_tokens')}/{usage.get('output_tokens')}): "
              f"{[str(f) for f in faces]}"
              + (f" unseen={unseen:.2f}" if unseen is not None else ""))
        return ActPlan(faces, latency, False, unseen)

    def _pick(self, answer, valid: list, unit: str, also_avoid: list) -> str:
        """Sample from Jev's probabilities, penalising what was shown recently."""
        avoid = [getattr(f, unit) for f in list(self._recent)[-2:]] + list(also_avoid)
        return sample_choice(answer, valid, avoid, self.rng,
                             config.JEV_FACE_TEMPERATURE,
                             config.JEV_REPEAT_PENALTY)


# --------------------------------------------------------------- sampling

def sample_choice(answer, valid: list, avoid: list, rng: random.Random,
                  temperature: float = 1.0, repeat_penalty: float = 0.55) -> str:
    """Pick one of `valid` from a Jev choice answer.

    weight = probability ** (1/temperature) * repeat_penalty ** (times the name
    is in `avoid`). Missing/garbled probabilities degrade to the plain top
    choice; an invalid or missing choice degrades to the neutral shape.
    """
    fallback = NEUTRAL.eyes if "default" in valid else (valid[0] if valid else "default")
    probs = _probabilities(answer, valid)
    if not probs:
        c = _choice(answer, None)
        return c if c in valid else fallback
    weights = {}
    inv_t = 1.0 / max(temperature, 0.05)
    for name, p in probs.items():
        w = max(p, 0.0) ** inv_t
        w *= repeat_penalty ** sum(1 for a in avoid if a == name)
        weights[name] = w
    total = sum(weights.values())
    if total <= 0.0:
        c = _choice(answer, None)
        return c if c in valid else fallback
    r = rng.random() * total
    acc = 0.0
    for name, w in weights.items():
        acc += w
        if r <= acc:
            return name
    return max(weights, key=weights.get)


def _probabilities(answer, valid: list) -> dict:
    if not isinstance(answer, dict):
        return {}
    raw = answer.get("probabilities")
    out = {}
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, list):
        items = []
        for e in raw:
            if isinstance(e, dict) and "choice" in e:
                items.append((e["choice"], e.get("probability", e.get("p", 0.0))))
            elif isinstance(e, (list, tuple)) and len(e) == 2:
                items.append((e[0], e[1]))
    else:
        return {}
    for k, v in items:
        if k in valid:
            f = _f(v)
            if f is not None:
                out[k] = f
    return out


def _choice(answer, default):
    return answer.get("choice", default) if isinstance(answer, dict) else default


def _confidence(answer) -> float:
    if not isinstance(answer, dict):
        return 0.0
    return _f(answer.get("confidence")) or 0.0


# -------------------------------------------------------------- transport

def _post(url: str, key: str, body: dict, timeout_s: float) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        })
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.loads(r.read())


# --------------------------------------------------------------- shaping

def _vitals_text(vitals: dict) -> str:
    units = {"cpu": "% cpu", "ram": "% ram", "temp": "C core temp",
             "battery": "% battery", "disk": "% disk full"}
    bits = []
    for k, unit in units.items():
        v = _f((vitals or {}).get(k))
        if v is not None:
            bits.append(f"{round(v)}{unit}")
    return ", ".join(bits) or "unknown"


def _signature(kind: str, user_text: str, vitals: dict, extra) -> str:
    compact = {k: _f((vitals or {}).get(k)) for k in
               ("cpu", "ram", "temp", "battery", "disk")}
    return hashlib.sha256(json.dumps(
        [kind, user_text, compact, extra], sort_keys=True).encode()).hexdigest()


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None
