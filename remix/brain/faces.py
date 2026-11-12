# faces.py — the brain's vocabulary for Remix's face. Mirrors
# godot/scripts/face_config.gd (EYES / MOUTHS / POSES); Godot's `ready` message
# carries the live lists and the brain narrows to them (set_available()).
#
# Eyes and mouth are two independent units, so a Face is a PAIR — plus an
# optional blush level (0..1): the global cheek hatching, its own overlay in
# Godot, worn in every pose. Twelve drawings give the shapes; the
# descriptions below say what each READS as, because that is what Jev picks
# from.
from typing import NamedTuple

from . import config


class Face(NamedTuple):
    eyes: str
    mouth: str
    blush: float = 0.0

    def _blush(self) -> float:
        """Clamped to 0..1 and floored by BLUSH_BASELINE, so the wire value
        is ALWAYS explicit (even 0) and never sticks to the next face."""
        b = min(1.0, max(0.0, float(self.blush)))
        return max(float(config.BLUSH_BASELINE), b)

    def msg(self, look=None) -> dict:
        m = {"type": "pose", "eyes": self.eyes, "mouth": self.mouth,
             "blush": self._blush()}
        if look is not None:
            m["look"] = list(look)
        return m

    def cue(self, t: float, look=None) -> dict:
        c = {"t": round(float(t), 3), "eyes": self.eyes, "mouth": self.mouth,
             "blush": self._blush()}
        if look is not None:
            c["look"] = list(look)
        return c

    def spec(self) -> dict:
        """For a perform `then`."""
        return {"eyes": self.eyes, "mouth": self.mouth,
                "blush": self._blush()}

    def __str__(self) -> str:
        return (f"{self.eyes}/{self.mouth}"
                + (f"+blush" if self._blush() > 0.01 else ""))


# What each EYE shape reads as. (Written for Jev: feelings, not geometry.)
EYES_TEXT = {
    "default": "plain open eyes: attentive, neutral, just listening, nothing going on",
    "anger": "sharp angled brows: irritated, furious, hot, straining, fed up",
    "bottomlid": "narrowed with raised lower lids: smug, amused, scheming, pleased with herself",
    "really": "heavy flat lids: unimpressed, deadpan, dry disbelief, \"really?\", sulky",
    "spooked": "hollow trembling outline eyes: startled, scared, something is wrong",
    "tired": "plain eyes with bags under them: exhausted, drained, numb, over it",
    "tsun": "squeezed > < chevron eyes: flustered, embarrassed, cringing, denying she cares",
    # ---- from the reference folder ----
    "bashful": "default-tall eyes sitting LOW on the face: bashful, caught out, pleased and trying very hard to look normal about it (the cheek hatching is its own overlay — set the blush level, don't rely on the eyes)",
    "thunk": "eyes as two thin closed lids, asleep, with a little loading wheel spinning in the corner of the face: powered down, thunking out, resting, politely not here",
    "scribble": "THE ENTIRE FACE IS ONE WILD SCRIBBLE whose knot stutters and teleports around itself, pulsing: short-circuited, overloaded, error, kernel panic; words no longer available",
    "strange": "one eye dropped short and low, the other stretched tall with a slanted top: glitching, deranged, something is wrong and she can feel it",
    "smug": "bars squeezed narrow with flared caps, I-beam slits: smug, sly, teasing, needling, squidward-tier self-satisfaction",
}

# What each MOUTH shape reads as.
MOUTHS_TEXT = {
    "default": "flat line: neutral, deadpan, nothing to say",
    "anger": "tight tilted slash: clenched, seething, annoyed",
    "smirk": "line curling up at one end: smug, teasing, sly",
    "pout": "line with a flared sulky end: sulking, put-out, grumbling",
    "trauma": "drops low and flat: resigned, haunted, long-suffering, dead inside",
    "tsun": "peaked tent shape: flustered, awkward, embarrassed",
    "none": "no mouth at all: stunned, speechless, alarmed, or mouthless-and-running-on-power-save",
    # ---- from the reference folder ----
    "bashful": "flat line sitting noticeably lower: quiet, holding still while flustered",
    "strange": "long thin line rising off-axis to one side: a slow, wrong grin",
    "smug": "wide deep smile riding high at both corners: open, delighted, insufferable",
}

# Named shortcuts (the original seven drawings). RuleBrain thinks in these.
POSES = {
    "default": Face("default", "default"),
    "anger": Face("anger", "anger"),
    "smirk": Face("bottomlid", "smirk"),
    "pout": Face("really", "pout"),
    "spooked": Face("spooked", "none"),
    "tired": Face("tired", "trauma"),
    "tsun": Face("tsun", "tsun"),
    # ---- reference-folder shortcuts (mirror face_config.gd POSES) ----
    "bashful": Face("bashful", "bashful", 1.0),
    "thunk": Face("thunk", "none"),
    "scribble": Face("scribble", "none"),
    "strange": Face("strange", "strange"),
    "smug": Face("smug", "smug"),
}

NEUTRAL = Face("default", "default")


class Vocabulary:
    """Which eye/mouth names the face actually has (from Godot's `ready`)."""

    def __init__(self) -> None:
        self.eyes = list(EYES_TEXT)
        self.mouths = list(MOUTHS_TEXT)

    def set_available(self, eyes=None, mouths=None) -> None:
        e = [x for x in (eyes or []) if x in EYES_TEXT]
        m = [x for x in (mouths or []) if x in MOUTHS_TEXT]
        self.eyes = e or list(EYES_TEXT)
        self.mouths = m or list(MOUTHS_TEXT)

    def face(self, eyes, mouth) -> Face:
        """Validate a pair; anything unknown falls back to the neutral unit."""
        return Face(eyes if eyes in self.eyes else NEUTRAL.eyes,
                    mouth if mouth in self.mouths else NEUTRAL.mouth)


# ---- $0 fallback: a face from the words alone (no Jev, over budget, offline) --
# Ordered; first match wins. Deliberately mixed pairs, not just the 7 shortcuts.
_GUESS = [
    (r"\b(fuck (you|off)|bite me|shut up|screw you|piss off|asshole)\b", Face("anger", "anger")),
    (r"\b(you suck|useless|stupid|idiot|dumbass|hate you|trash)\b", Face("anger", "pout")),
    (r"\b(ew+|gross|disgusting|nasty|what the (fuck|hell))\b", Face("really", "pout")),
    (r"\b(shit|damn|hell|ugh+|bruh)\b.*\.\.\.|\.\.\.\s*$", Face("really", "default")),
    (r"\b(thanks|thank you|love|nice|good job|you're the best|cute)\b", Face("tsun", "pout")),
    (r"\b(sorry|my bad|hurt|sad|bad day|tired|sick|cry)\b", Face("tired", "default")),
    (r"\b(die|dead|kill|crash|overheat|on fire|panic|help)\b", Face("spooked", "none")),
    (r"\b(lol|haha|funny|joke|sure,? buddy|keep telling yourself|wow)\b", Face("bottomlid", "smirk")),
    (r"\b(whatever|fine|if you say so|i guess|meh|boring)\b", Face("really", "default")),
    (r"\b(scrambled|overloaded|short.?circuit|too many (tabs|windows))\b", Face("scribble", "none")),
    (r"\b(shut ?down|reboot(ing)?|restarting|goodnight|good night)\b", Face("thunk", "none")),
    (r"\b(something'?s wrong|not right|unhinged|freaky)\b", Face("strange", "strange")),
    (r"\b(told you|i knew it|called it|of course|obviously)\b", Face("smug", "smug")),
    (r"\b(flatterer|charmer|oh you|stop it,? you)\b", Face("bashful", "bashful", 1.0)),
    (r"\b(morning|hey|hi|yo|hello|sup)\b", Face("default", "default")),
    (r"\?\s*$", Face("default", "default")),
]
import re as _re
_GUESS = [(_re.compile(p, _re.I), f) for p, f in _GUESS]


def guess_face(text: str) -> Face:
    """Cheap, local face for a line of words. Neutral when nothing matches."""
    t = (text or "").strip()
    for pat, face in _GUESS:
        if pat.search(t):
            return face
    return NEUTRAL
