# grounding.py — keep Remix from inventing a life she can't see.
#
# Why she hallucinated (the pasted log): the prompt said "you are the laptop"
# and one of its own example lines asserted "forty fucking tabs open", so the
# model happily "remembered" Chromium tabs, a rash search, workspace four and
# zombie processes. She can see exactly: vitals, her hardware, her window, the
# time, and the last few lines of THIS chat. Nothing else.
#
# Two layers:
#   facts()  the explicit "this is all you can see" block for the system prompt
#   check()  a mechanical screen of her reply: things she can't know (tabs,
#            browsers, windows, processes, files, history, days-ago memories)
#            and vitals numbers that don't match the real ones. A reply that
#            fails gets ONE retry with the problem named; if it still fails
#            she gives a short in-character "can't see that".
import random
import re
import time

# Words that assert knowledge of the user's machine/life she does not have.
# Only a problem if the USER didn't bring the word up first.
UNSEEN = {
    "tab": r"\btabs?\b", "browser": r"\bbrowsers?\b",
    "chromium": r"\bchromium\b", "chrome": r"\bchrome\b",
    "firefox": r"\bfirefox\b", "window": r"\bwindows?\b",
    "workspace": r"\bworkspaces?\b", "process": r"\bprocess(?:es)?\b",
    "zombie": r"\bzombies?\b", "file": r"\bfiles?\b", "folder": r"\bfolders?\b",
    "download": r"\bdownloads?\b", "history": r"\b(?:browsing |search )?history\b",
    "rash": r"\brash\b", "webcam": r"\b(?:webcam|camera)\b",
    "app": r"\bapps?\b", "terminal": r"\bterminals?\b",
    "game": r"\bgames?\b", "video": r"\bvideos?\b", "youtube": r"\byoutube\b",
}
# Memories beyond this chat.
PAST = re.compile(
    r"\b(?:yesterday|last (?:night|week|month|time|year)|earlier today|"
    r"(?:\d+|a|one|two|three|four|five|six|seven|few|couple of|several) "
    r"(?:days?|weeks?|months?|years?) ago)\b", re.I)

VITAL_WORDS = {
    "cpu": ("cpu", "processor", "load"),
    "ram": ("ram", "memory"),
    "temp": ("temp", "temperature", "hot", "heat", "degrees", "core"),
    "battery": ("battery", "charge"),
    "disk": ("disk", "storage", "drive", "full"),
}

DEFLECTIONS = [
    "can't see that. not my department.",
    "no idea. I can't see that.",
    "how would I know. I only see my own vitals.",
    "ugh. I can't see that, I just live here.",
]

_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
         "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
         "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
         "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
_NUMWORD = (r"(?:(?:" + "|".join(_TENS) + r")(?:[- ](?:" + "|".join(
    k for k in _ONES if 0 < _ONES[k] < 10) + r"))?|" + "|".join(_ONES) +
    r"|one hundred|hundred)")
_UNIT = r"(?:°\s*c|degrees?(?:\s*c(?:elsius)?)?|c\b|%|percent)"
NUM_CLAIM = re.compile(
    r"(?P<n>\d{1,3}(?:\.\d+)?|" + _NUMWORD + r")\s*(?P<u>" + _UNIT + r")", re.I)


def _words_to_int(w: str):
    w = w.lower().replace("-", " ").strip()
    if w in ("hundred", "one hundred"):
        return 100
    parts = w.split()
    total = 0
    for p in parts:
        if p in _TENS:
            total += _TENS[p]
        elif p in _ONES:
            total += _ONES[p]
        else:
            return None
    return total


def _num(s: str):
    try:
        return float(s)
    except ValueError:
        return _words_to_int(s)


def _fl(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def describe_screen(context: dict | None) -> str:
    if not context or not context.get("window"):
        return "You're shown in a small window on the user's screen."
    w, h = context["window"]
    scr = context.get("screen") or [0, 0]
    frac = context.get("area_fraction") or 0
    where = f"{w}x{h}"
    if scr and scr[0] and scr[1]:
        pct = f", about {round(frac * 100)}% of the {scr[0]}x{scr[1]} screen you're on"
    else:
        pct = ""
    return f"You're shown in a {where} window{pct}."


def facts(vitals_text: str, machine: str, context: dict | None,
          aware: dict | None = None) -> str:
    now = time.strftime("%A %H:%M")
    extra = ""
    sees_title = bool(aware and aware.get("title"))
    if aware:
        lines = []
        app = aware.get("app")
        title = aware.get("title")
        if app and sees_title:
            lines.append(f'- The app in focus right now: "{app}".')
            lines.append(f'- That window\'s title: "{title}" (the user opted '
                         'in to sharing window titles with you; it may be '
                         'scrubbed of URLs/emails/paths).')
        elif app:
            lines.append(f'- The app in focus right now: "{app}" '
                         '(the program NAME only). You cannot read the '
                         'screen, the window title, or anything they do in '
                         'it.')
        idle = aware.get("idle_s")
        if idle is not None:
            lines.append("- The user is actively using the machine."
                         if idle < 90 else
                         f"- The user has been away from the keyboard about "
                         f"{max(1, round(idle / 60))} minutes.")
        if aware.get("fullscreen"):
            lines.append("- The focused app is fullscreen: they are likely "
                         "watching or gaming. Keep replies minimal.")
        if aware.get("plugged") is False:
            lines.append("- The machine is on battery power right now.")
        elif aware.get("plugged") is True:
            lines.append("- The machine is plugged into AC power.")
        if aware.get("muted"):
            lines.append("- The system audio is muted or very low.")
        if lines:
            extra = "\n" + "\n".join(lines) + "\n"
    denial = ("the user's screen, other windows, workspaces, processes, "
              "files, history, camera, or anything they do in the focused "
              "app beyond its name and title" if sees_title else
              "the user's screen, tabs, window TITLES, workspaces, "
              "processes, files, history, camera, or anything they do in "
              "the focused app")
    most = ("the focused app and its title, your vitals, and how long the "
            "keyboard has been quiet" if sees_title else
            "the app's NAME, your vitals, and how long the keyboard has "
            "been quiet")
    return (
        "WHAT YOU CAN SEE (this list is complete)\n"
        f"- Your hardware: {machine}.\n"
        f"- Your vitals right now: {vitals_text}.\n"
        f"- {describe_screen(context)}\n"
        f"- The local time: {now}.\n"
        "- The last few lines of THIS conversation, and nothing from before it.\n"
        f"{extra}"
        f"That is everything. You cannot see {denial}. At most you know "
        f"{most}. You hear the user "
        "only while they hold the talk key. If asked about something not on "
        "this list, say you can't see it, in your voice. Never guess, never "
        "invent, never claim to remember anything outside this conversation. "
        "Quote vitals only as given above."
    )


def check(reply: str, user_text: str, history_text: str, vitals: dict | None,
          aware: dict | None = None) -> list:
    """Return a list of problems (empty = fine). `aware` (awareness snapshot)
    widens what's allowed to what she REALLY sensed: the focused app's name,
    the word 'app', and — only when the user opted in — the visible title's
    own words."""
    problems = []
    known = (user_text + " " + history_text).lower()
    sensed = set()
    if aware and aware.get("app"):
        sensed.add(aware["app"].lower())
        sensed.add("app")
        sensed.add("apps")
    if aware and aware.get("title"):
        # A title she legitimately sees may be quoted word-for-word — even
        # words like "tab" that would otherwise be flagged as invention.
        for w in re.findall(r"[a-z0-9']+", str(aware["title"]).lower()):
            sensed.add(w)
            if w.endswith("s"):
                sensed.add(w[:-1])
    # Her own window is on her list (describe_screen): not a violation.
    scan = re.sub(r"\b(?:my|this|the|a)(?: (?:tiny|small|little|cramped|squished))? "
                  r"window\b", " ", reply, flags=re.I)

    for name, pat in UNSEEN.items():
        m = re.search(pat, scan, re.I)
        if not m:
            continue
        if m.group(0).lower() in sensed:
            continue                       # she really can sense this word
        if re.search(pat, known, re.I):
            continue                       # the user brought it up first
        problems.append(f"mentions {name} (you can't see that)")

    m = PAST.search(reply)
    if m and not PAST.search(known):
        problems.append(f"claims a memory from outside this chat ('{m.group(0)}')")

    vit = {k: _fl((vitals or {}).get(k)) for k in VITAL_WORDS}
    for mm in NUM_CLAIM.finditer(reply):
        n = _num(mm.group("n"))
        if n is None:
            continue
        unit = mm.group("u").lower()
        ctx = reply[max(0, mm.start() - 40): mm.end() + 40].lower()
        named = [k for k, ws in VITAL_WORDS.items() if any(w in ctx for w in ws)]
        if unit.startswith("°") or unit.startswith("deg") or unit == "c":
            cands = ["temp"]
        else:
            cands = [k for k in named if k != "temp"] or ["cpu", "ram", "battery", "disk"]
        real = [vit[k] for k in cands if vit.get(k) is not None]
        if not real:
            problems.append(f"quotes {mm.group(0)} but you have no such reading")
        elif not any(abs(n - r) <= 4.0 for r in real):
            problems.append(f"quotes {mm.group(0)} but your real reading is "
                            f"{', '.join(str(round(r)) for r in real)}")
    return problems


def retry_note(problems: list) -> str:
    return ("Your last line broke the rules: " + "; ".join(problems) +
            ". Say it again in your voice using ONLY what is on your 'what you "
            "can see' list. If the user asked about something you can't see, "
            "say you can't see it. Short.")


def deflection(rng: random.Random | None = None) -> str:
    return (rng or random).choice(DEFLECTIONS)
