# faces.py — the brain's vocabulary for Remix's face. Mirrors
# godot/scripts/face_config.gd (EYES / MOUTHS / POSES); Godot's `ready` message
# carries the live lists and the brain narrows to them (set_available()).
#
# Eyes and mouth are two independent units, so a Face is a PAIR. Seven drawings
# give 7 eye shapes x 7 mouth shapes = 49 faces; the descriptions below say what
# each shape READS as, because that is what Jev picks from.
from typing import NamedTuple


class Face(NamedTuple):
    eyes: str
    mouth: str

    def msg(self, look=None) -> dict:
        m = {"type": "pose", "eyes": self.eyes, "mouth": self.mouth}
        if look is not None:
            m["look"] = list(look)
        return m

    def cue(self, t: float, look=None) -> dict:
        c = {"t": round(float(t), 3), "eyes": self.eyes, "mouth": self.mouth}
        if look is not None:
            c["look"] = list(look)
        return c

    def spec(self) -> dict:
        """For a perform `then`."""
        return {"eyes": self.eyes, "mouth": self.mouth}

    def __str__(self) -> str:
        return f"{self.eyes}/{self.mouth}"


# What each EYE shape reads as. (Written for Jev: feelings, not geometry.)
EYES_TEXT = {
    "default": "plain open eyes: attentive, neutral, just listening, nothing going on",
    "anger": "sharp angled brows: irritated, furious, hot, straining, fed up",
    "bottomlid": "narrowed with raised lower lids: smug, amused, scheming, pleased with herself",
    "really": "heavy flat lids: unimpressed, deadpan, dry disbelief, \"really?\", sulky",
    "spooked": "hollow trembling outline eyes: startled, scared, something is wrong",
    "tired": "plain eyes with bags under them: exhausted, drained, numb, over it",
    "tsun": "squeezed > < chevron eyes: flustered, embarrassed, cringing, denying she cares",
}

# What each MOUTH shape reads as.
MOUTHS_TEXT = {
    "default": "flat line: neutral, deadpan, nothing to say",
    "anger": "tight tilted slash: clenched, seething, annoyed",
    "smirk": "line curling up at one end: smug, teasing, sly",
    "pout": "line with a flared sulky end: sulking, put-out, grumbling",
    "trauma": "drops low and flat: resigned, haunted, long-suffering, dead inside",
    "tsun": "peaked tent shape: flustered, awkward, embarrassed",
    "none": "no mouth at all: stunned, speechless, alarmed",
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
