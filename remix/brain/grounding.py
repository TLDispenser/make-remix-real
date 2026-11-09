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

from . import awareness, config, memory

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
    "can't see that — I get app names, not contents. Tell me what you're "
    "stuck on and I'll work with what we've got.",
    "no idea. I don't see screens; word gets to me about which app is "
    "open, that's it. Spell it out.",
    "how would I know, the room's dark from where I sit. What are you "
    "actually trying to do?",
    "ugh. Blind over here, except for the app-name label and my own "
    "vitals. Ask me differently.",
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


def _where_phrase(pos, w, h, scr) -> str:
    """Her window's center as plain region words: 'in the top-right area',
    'toward the left', 'near the middle'. '' when the numbers are junk.
    Rough words on purpose: she gets a sense of placement, not coordinates
    to recite (she'd quote them wrong, and nobody talks in pixel offsets)."""
    try:
        px, py = float(pos[0]), float(pos[1])
        sw, sh = float(scr[0]), float(scr[1])
        fw, fh = float(w), float(h)
    except (TypeError, ValueError, IndexError):
        return ""
    if sw <= 0 or sh <= 0 or fw <= 0 or fh <= 0:
        return ""
    fx = min(1.0, max(0.0, (px + fw / 2.0) / sw))
    fy = min(1.0, max(0.0, (py + fh / 2.0) / sh))
    v = "top" if fy < 1 / 3 else "bottom" if fy >= 2 / 3 else ""
    hz = "left" if fx < 1 / 3 else "right" if fx >= 2 / 3 else ""
    if v and hz:
        return f"in the {v}-{hz} area"
    if v:
        return f"along the {v}"
    if hz:
        return f"toward the {hz}"
    return "near the middle"


def describe_screen(context: dict | None) -> str:
    """Where she is, from the facts Godot actually reports (context msg:
    window size, screen size, screen index/count, pos within her screen).
    That data is LIVE — Godot re-sends it whenever she moves — so the line
    stays honest about being 'right now', not a fixed address. When the
    position isn't trusted (no pos, or REMIX_WINDOW_POS=0), she says so
    instead of guessing: she never invents a location."""
    if not context or not context.get("window"):
        return ("You're shown in a small window on the user's screen. They "
                "can park that window anywhere; you're not told exactly "
                "where right now.")
    w, h = context["window"]
    scr = context.get("screen") or [0, 0]
    frac = context.get("area_fraction") or 0
    screens = ""
    count, idx = context.get("screen_count"), context.get("screen_index")
    if isinstance(count, int) and count > 1 and isinstance(idx, int) \
            and 0 <= idx < count:
        screens = f" (screen {idx + 1} of {count})"
    place = ""
    if scr and scr[0] and scr[1]:
        if config.WINDOW_POS and context.get("pos"):
            phrase = _where_phrase(context["pos"], w, h, scr)
            if phrase:
                place = (f" {phrase} of the {scr[0]}x{scr[1]} screen"
                         f"{screens}")
    if place:
        pct = f", about {round(frac * 100)}% of it" if frac else ""
        return (f"You're a {w}x{h} window, parked{place}{pct}. They move "
                "you around; that's just where you are right now.")
    where = f"You're shown in a {w}x{h} window"
    if scr and scr[0] and scr[1]:
        where += (f" on the {scr[0]}x{scr[1]} screen{screens}, about "
                  f"{round(frac * 100)}% of it")
    return (where + ". They can park that window anywhere; exactly where "
            "right now isn't something you're told.")


def facts(vitals_text: str, machine: str, context: dict | None,
          aware: dict | None = None) -> str:
    now = time.strftime("%A %H:%M")
    extra = ""
    sees_title = bool(aware and aware.get("title"))
    around = (aware or {}).get("around") or {}
    has_around = bool(around.get("self") and (around.get("windows")
                                              or around.get("elsewhere")))
    any_title = sees_title or any(w.get("title") for w in
                                  around.get("windows", []))
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
            lines.append(f'- The focused app right now: "{app}" (the program '
                         'NAME only). You never see its screen, window title, '
                         'or anything being done in it — when it changes, '
                         'the change just arrives, like a label read '
                         'through a wall.')
        if has_around:
            lines.append(
                "- The other windows around yours (the machine reports each "
                "one's app NAME and where it sits relative to your window — "
                "that's all; you can't see inside them):")
            lines.extend("    · " + x for x in awareness.describe_around(around))
            lines.append(
                "  When you name one of these apps your eyes turn toward it "
                "on their own, so you can say \"that one\", \"over there\", "
                "\"to my left\" and the human can follow. Use those "
                "directions exactly as listed; never invent a window that "
                "isn't listed.")
        idle = aware.get("idle_s")
        if idle is not None:
            lines.append("- The user is actively using the machine."
                         if idle < 90 else
                         f"- The user has been away from the keyboard about "
                         f"{max(1, round(idle / 60))} minutes.")
        up = _fl(aware.get("up_s"))
        if up and up > 120:
            lines.append(f"- This session of yours (your brain running) has "
                         f"been awake about {max(1, round(up / 60))} minutes.")
        if aware.get("fullscreen"):
            lines.append("- The focused app is fullscreen: they are likely "
                         "watching or gaming. Keep replies minimal.")
        if aware.get("plugged") is False:
            lines.append("- The machine is on battery power right now.")
        elif aware.get("plugged") is True:
            lines.append("- The machine is plugged into AC power.")
        if aware.get("muted"):
            lines.append("- The system audio is muted or very low.")
        if _fl(aware.get("up_s") or 0) < 1800:
            aw = memory.awaken()
            if aw and aw.get("elapsed_s"):
                lines.append(f"- You were OFF before this session started "
                             f"and woke up {memory.elapsed_text(aw['elapsed_s'])} "
                             "ago. That's normal for you: someone restarted "
                             "the machine. Your journal survived (see WHAT "
                             "YOU REMEMBER if it's present).")
        if lines:
            extra = "\n" + "\n".join(lines) + "\n"
    if has_around:
        denial = ("what is INSIDE any window (the user's screen contents, "
                  "tabs, " + ("" if any_title else "window TITLES, ") +
                  "processes, files, history, camera, or anything they do "
                  "in those apps) or any window not on your list")
        most = ("which apps are open, where their windows sit around you, "
                "your vitals, and how long the keyboard has been quiet")
    else:
        denial = ("the user's screen, other windows, workspaces, processes, "
                  "files, history, camera, or anything they do in the "
                  "focused app beyond its name and title" if sees_title else
                  "the user's screen, tabs, window TITLES, workspaces, "
                  "processes, files, history, camera, or anything they do in "
                  "the focused app")
        most = ("the focused app and its title, your vitals, and how long "
                "the keyboard has been quiet" if sees_title else
                "the app's NAME, your vitals, and how long the keyboard has "
                "been quiet")
    # Her memory rule must MATCH what actually exists, or she'll deny the
    # journal to stay consistent with this text ("it starts with this
    # conversation" while a WHAT YOU REMEMBER block says otherwise).
    mem_line = ("- Your memory: this conversation, "
                "plus WHAT YOU REMEMBER below if that section is present "
                "(a short dated journal). Nothing older than those exists.\n"
                if memory.has_journal() else
                "- The last few lines of THIS conversation, and nothing "
                "from before it.\n")
    mem_rule = ("anything outside your journal and this conversation"
                if memory.has_journal() else
                "anything outside this conversation")
    return (
        "WHAT YOU CAN SEE\n"
        "Honest answer: nothing — you have no eyes. But you're not deaf "
        "either. The machine you live in reports a few things to you the "
        "way sounds carry through a wall: real information, secondhand. "
        "That's a sense, not a guess, and it applies to every line below.\n"
        f"- Your hardware: {machine}.\n"
        f"- Your vitals right now: {vitals_text}.\n"
        f"- {describe_screen(context)}\n"
        f"- The local time: {now}.\n"
        f"{mem_line}"
        f"{extra}"
        f"That is everything Sight-wise. You cannot see {denial}. At most "
        f"you can sense {most}. So when asked whether you can see or "
        "watch something, answer in your voice with the truth: you can't "
        "SEE it, but you often KNOW — name what reaches you (the focused "
        "app's name, how long the room's been quiet, your vitals). Never "
        f"invent {mem_rule}. "
        "Hearing the user happens only while they hold the talk key. If "
        "asked about something not on this list, say you can't get it, in "
        "your voice. Never guess, never invent, never claim to remember "
        f"{mem_rule}. Quote vitals only as given above."
    )


def _memory_ok(phrase: str, reply: str) -> bool:
    """PAST gate widening (proposal 4): a past-tense claim passes ONLY if her
    journal genuinely reaches that far back. Rules:
      'yesterday'            -> a journal row dated the previous local day
      'earlier today' etc    -> a row dated today (or the last ~20 h)
      'N days/weeks... ago'  -> journal age >= about half the claimed span
      last night/week/month  -> same as yesterday
    Sharing a content word with a real row is ALSO required, so a generic
    'yesterday' can't smuggle invented facts."""
    import re
    if not memory.has_journal():
        return False
    pl = (phrase or "").lower()
    rows = memory.lines(limit=100)
    if not rows:
        return False
    words = {w for w in re.findall(r"[a-z0-9']+", reply.lower()) if len(w) > 3}
    day_name = time.strftime("%Y-%m-%d")

    def local_day(ts: float) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(ts))

    yesterday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    for e in rows:
        try:
            ts = float(e.get("t") or 0)
        except (TypeError, ValueError):
            continue
        text = (e.get("text") or "").lower()
        near = bool(words & (set(text.split()) | {e.get("at", "").lower()[:10]}))
        if not near:
            continue
        if "yesterday" in pl or pl.startswith("last "):
            if local_day(ts) in (yesterday, day_name):
                return True
        elif "earlier today" in pl or "today" in pl or "this morning" in pl:
            if local_day(ts) == day_name or time.time() - ts < 20 * 3600:
                return True
        else:
            m2 = re.search(
                r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
                r"eleven|twelve|few|several|couple of)\s*(day|week|month|"
                r"year)s?\s+ago", pl)
            if m2:
                n_txt, unit = m2.group(1), m2.group(2)
                n = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                     "six": 6, "seven": 7, "eight": 8, "nine": 9,
                     "ten": 10, "eleven": 11, "twelve": 12,
                     "few": 3, "several": 4, "couple of": 2}.get(n_txt, 3)
                mult = {"day": 1, "week": 7, "month": 30, "year": 365}[unit]
                if time.time() - ts >= (n * mult * 86400) / 2.0:
                    return True
    return False


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
    ar = (aware or {}).get("around") or {}
    if ar.get("self"):
        # Every window she was told about is fair game, by its own name and
        # by the category words (browser, terminal, ...) that name it.
        for w in list(ar.get("windows", [])) + list(ar.get("elsewhere", [])):
            nm = awareness._name_of(w["app"])
            sensed.update({nm, w["app"].lower(), nm + "s"})
            for word, apps in awareness._CATEGORY.items():
                if nm in apps:
                    sensed.update({word, word + "s"})
        sensed.update({"app", "apps", "window", "windows"})
        if ar.get("elsewhere"):
            sensed.update({"workspace", "workspaces"})
        for w in ar.get("windows", []):
            for tw in re.findall(r"[a-z0-9']+", str(w.get("title") or "").lower()):
                sensed.add(tw)
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
    if m and not PAST.search(known) and not _memory_ok(m.group(0), reply):
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
        elif not named and re.search(r"\b(?:screen|window)\b", ctx):
            continue    # her own window fraction ("6% of the screen"), not a vital
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
