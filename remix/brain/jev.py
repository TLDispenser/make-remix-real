# jev.py — the JevBrain (left brain, System One, credit-backed).
# ONE Jev call per typed input returns the WHOLE reaction package:
#   * stance        -> feeds speech.py's {stance} sysprompt slot
#   * pose          -> instant face reaction, sent before any words exist
#   * beat1..beat4  -> per-beat acting arc for the upcoming line
#   * resting       -> pose she settles into after the line
# Budget policy (locked 2026-11-06, see RESEARCH_NOTES.md):
#   silent life NEVER calls Jev; cooldown 3 s; hard hourly cap (over cap ->
#   silent RuleBrain fallback); state-signature cache (unchanged state =
#   cached decision, zero credits); every failure path returns None and the
#   caller falls back to its $0 behavior. Request/response shapes verified
#   against https://api.typesafe.ai/openapi.json (fetched 2026-11-06).
import asyncio
import hashlib
import json
import time
import urllib.error
import urllib.request
from typing import NamedTuple

from . import config

# She/her, dry, secretly fond — stance choices the sysprompt understands.
STANCE_OPTION_TEXT = {
    "flat": "flat and tired; no coloring, facts only",
    "annoyed": "irritation on the surface; keep the barbs out",
    "fond": "secretly fond; warmth may leak out ONLY as an insult",
    "concerned": "the bit stops; plainly kind and gentle, still short",
    "alarmed": "something about the machine feels wrong; worry under the sarcasm",
    "playful": "dry mischief; one sly jab is allowed, no gushing",
}

# Pose names match godot scripts/face_config.gd (the face validates too).
POSE_OPTION_TEXT = {
    "default": "baseline: alert, neutral, listening",
    "anger": "working hard / irritated / hot; teeth showing",
    "smirk": "smug, teasing, sly amusement",
    "pout": "sulky, put-upon, patience wearing thin",
    "spooked": "startled; alarm; something wrong with the body",
    "tired": "drained, eyelids heavy, low battery",
    "tsun": "tsundere deflection: hiding that she cares",
}

BEAT_INSTRUCTIONS = {
    "beat1": "Pose for the FIRST beat of her upcoming 1-3 short-beat reply.",
    "beat2": "Pose for a MIDDLE beat of her upcoming 1-3 short-beat reply "
             "(unused if the reply is one beat, that's fine).",
    "beat3": "Pose for the LAST beat of her upcoming 1-3 short-beat reply.",
    "beat4": "Pose for a rare 4th beat; pick whatever flows from the others.",
}


class Decision(NamedTuple):
    stance: str            # stance text for the sysprompt slot, or ""
    pose: str              # instant reaction pose
    beats: list            # per-beat pose arc (may be shorter than the reply)
    resting: str           # pose after the line finishes
    confidence: float      # of the stance answer (representative)
    latency_s: float
    from_cache: bool


class JevBrain:
    def __init__(self) -> None:
        self.poses = list(POSE_OPTION_TEXT)  # refined by ready.poses later
        self._last_call_at = 0.0
        self._call_times: list = []          # for the hourly cap window
        self._cache: dict = {}               # sig -> (expiry, Decision)

    def set_available_poses(self, ready_poses) -> None:
        """ready.poses is Jev's option list (godot/PROTOCOL.md)."""
        valid = [p for p in (ready_poses or []) if p in POSE_OPTION_TEXT]
        self.poses = valid or list(POSE_OPTION_TEXT)

    # ------------------------------------------------------------------ API

    async def decide(self, user_text: str, vitals: dict) -> Decision | None:
        """One Jev reaction package. None when budget/exhausted/broken."""
        now = time.monotonic()
        sig = _signature(user_text, vitals)

        hit = self._cache.get(sig)
        if hit and hit[0] > now:
            d = hit[1]._replace(from_cache=True)
            print(f"remix-brain: jev decision CACHED ({d.pose}, "
                  f"{len(d.beats)}-beat arc) — 0 credits")
            return d

        if not self._budget_ok(now):
            return None
        self._last_call_at = now
        self._call_times.append(now)

        body = {
            "model": config.JEV_MODEL,
            "state": _state(user_text, vitals),
            "questions": _questions(self.poses),
        }
        started = now
        try:
            resp = await asyncio.to_thread(
                _post, config.JEV_URL, config.JEV_API_KEY, body,
                config.JEV_TIMEOUT_S)
        except Exception as e:  # timeout / HTTP / JSON — fall back silently
            print(f"remix-brain: jev call failed ({e!r}) — RuleBrain handles it")
            return None
        latency = time.monotonic() - started
        d = _decision(resp, self.poses, latency)
        self._cache[sig] = (now + config.JEV_CACHE_TTL_S, d)
        return d

    def _budget_ok(self, now: float) -> bool:
        if not config.JEV_ENABLED:
            return False
        if now - self._last_call_at < config.JEV_COOLDOWN_S:
            return False
        hour_window = [t for t in self._call_times if now - t < 3600.0]
        self._call_times = hour_window
        return len(hour_window) < config.JEV_MAX_PER_HOUR


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

def _state(user_text: str, vitals: dict) -> dict:
    bits = []
    for k in ("cpu", "ram", "temp", "battery", "disk"):
        v = _f(vitals.get(k))
        if v is not None:
            bits.append(f"{k} {round(v)}%")
    return {
        "role": ("the human is typing at Remix, a sarcastic AI woman living "
                 "in this laptop; she controls her own face via these poses "
                 "and answers in 1-3 short beats"),
        "user_line": user_text,
        "vitals": ", ".join(bits) or "unknown",
    }


def _questions(poselist: list) -> dict:
    pose_criteria = {n: POSE_OPTION_TEXT.get(n, "") for n in poselist}
    stance_criteria = {n: txt for n, txt in STANCE_OPTION_TEXT.items()}
    q = {
        "stance": {
            "type": "choice",
            "criteria": stance_criteria,
            "instructions": ("Her stance for answering this line. Pick the "
                             "one that colors her reply most naturally."),
        },
        "pose": {
            "type": "choice", "criteria": pose_criteria,
            "instructions": ("Her face NOW, the instant she reads this "
                             "line, before she says anything."),
        },
    }
    for key, instructions in BEAT_INSTRUCTIONS.items():
        q[key] = {"type": "choice", "criteria": pose_criteria,
                  "instructions": instructions}
    q["resting"] = {
        "type": "choice", "criteria": pose_criteria,
        "instructions": "Pose she settles into AFTER the line is spoken.",
    }
    return q


def _decision(resp: dict, poselist: list, latency: float) -> Decision:
    answers = resp.get("answers", {})
    usage = resp.get("usage", {})
    conf = lambda a: float(a.get("confidence", 0.0)) if a else 0.0

    stance_name = str(answers.get("stance", {}).get("choice", "flat"))
    stance_txt = STANCE_OPTION_TEXT.get(stance_name, STANCE_OPTION_TEXT["flat"])
    pose = answers.get("pose", {}).get("choice")
    posture = pose if pose in poselist else "default"
    beats = []
    for key in ("beat1", "beat2", "beat3", "beat4"):
        c = answers.get(key, {}).get("choice")
        if c in poselist:
            beats.append(c)
    resting = answers.get("resting", {}).get("choice")
    resting = resting if resting in poselist else "default"

    print(f"remix-brain: jev decision in {latency:.2f}s "
          f"(tokens {usage.get('input_tokens')}/{usage.get('output_tokens')}): "
          f"stance={stance_name} ({conf(answers.get('stance')):.2f}) "
          f"pose={posture} beats={beats} rest={resting}")
    if not beats:
        beats = ["default"]
    return Decision(stance_txt, posture, beats, resting,
                    conf(answers.get("stance")), latency, False)


def _signature(user_text: str, vitals: dict) -> str:
    compact = {k: _f(vitals.get(k)) for k in
               ("cpu", "ram", "temp", "battery", "disk")}
    return hashlib.sha256(json.dumps(
        [user_text, compact], sort_keys=True).encode()).hexdigest()


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None
