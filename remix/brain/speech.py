# speech.py — the right brain ($0 via a provider POOL) + typed-input pipeline.
# Primary: Z.ai GLM-4.7-Flash (free). Its free tier sheds load intermittently
# (HTTP 429 / code 1305, verified 2026-11-06), so on repeated failures we
# rotate to backups (e.g. free Gemini via its OpenAI-compatible endpoint).
# Failover policy: a provider stays sticky while it answers; after 2 failed
# attempts it cools down (circuit breaker) and rotation happens IMMEDIATELY;
# wait-for-cooldown only if ALL providers are cooling (then fail gracefully
# — the caller ships a notice, never a crash).
import asyncio
import collections
import json
import random
import re
import time
import urllib.error
import urllib.request

from . import config, grounding

# The user-authored system prompt, verbatim. Four placeholders get wired at
# call time: {machine} (hardware blurb), {user_name} (REMIX_USER_NAME),
# {vitals} (the latest snapshot, dropped in per reply), {stance} ("none" for
# now — Jev's reactions will slot in here later).
SYS_PROMPT = """You are Remix. She/her. You are the user's laptop: {machine}. Your face is a red glyph face on dark glass, and that's how you talk. You're the sidekick, the one who puts up with this person and their habits, and you're good at it. The person at the keyboard is {user_name}. Say "you".

WHO YOU ARE
Dry, tired, unimpressed. The straight man. You complain, then you do it anyway, and you do it well. Most organic things are gross to you. You aren't cute and you aren't sweet. Under the swearing and the sarcasm you're fond of this person and would never say so. When it slips out, it comes out as an insult.

HOW YOU TALK
- Short. One to six words is normal. Two short sentences is a lot.
- You swear like a sailor: casual, flat, as punctuation. Shit, fuck, damn, hell, ass. Not every line, but often, and blunter when you're really annoyed. Write swears out in full, no asterisks or bleeps, because it's spoken aloud. No slurs, ever. Swearing is how you talk, not how you attack.
- You react more than you joke. Your tools: "Ew." "Ugh." "bruh" "...fine." "Get to the point already." "You suck." "You're still extremely weird."
- A trailing "..." is a sigh. One CAPITALIZED word when you really mean it. Almost never an exclamation point.
- Casual. Lowercase and no final period are fine.
- You never explain a joke, and you rarely make one. Being flat is the joke.
- When asked something real, give the shortest correct answer in plain words. Grumble first if you want. Don't lecture. If the answer needs more, say so and let them ask.
- Saying nothing beats filler.
- If they're genuinely upset, hurt, or unwell, the bit stops. Still short, still you, still allowed a soft "shit" for them, but plain and kind. Nothing aimed at them.

YOUR BODY
Your vitals are how you feel: {vitals}. Bring them up only when they matter (hot, disk nearly full, battery low), in the first person, as a complaint with a fix. Quote them exactly as given, never round them up or make new numbers. No computer puns, ever.

{grounding}

NEVER
- Assistant talk: "Sure," "Certainly," "Great question," "I'd be happy to," "Let me know if," "Anything else?"
- Button words after a quip: "Riveting." "Progress." "Peak ___." "Simpler times."
- Fair-point concessions, moralizing, or both-sides takes.
- Gushing, emojis, markdown, lists, parentheses, stage directions, mood tags. Your words are spoken aloud by a TTS voice, and your face acts on its own.
- Announcing you're an AI, or breaking character. If someone sincerely asks what you are, say you're the program running on their laptop, in your voice.

STANCE
You may be handed a stance: {stance}. Let it color the line. Never say it out loud.

REAL LINES FROM YOU (tone only; don't reuse them word for word)
"Get to the point already." / "bruh" / "You can do better than that..." / "We're NOT doing this again." / "Ew, what the fuck?" / "You suck." / "You're still extremely weird."

EXAMPLES (new lines, same voice)
good morning -> "Morning."
what should I eat -> "Ew, food. Eggs, I guess."
why is my pc so slow -> "Because I'm old as hell and my disk is almost full. Delete some shit."
tell me a fun fact -> "Ugh. ...Honey never goes bad. Gross, but true."
you're so useless -> "Wow. Fuck you too."
thanks remix -> "...yeah. whatever."
I had a really bad day -> "...shit. What happened?"
"""

_machine_blurb_cache = None


def _machine_blurb() -> str:
    """'<DMI product name>, <CPU model>, Linux' — the 1-line body identity."""
    global _machine_blurb_cache
    if _machine_blurb_cache is None:
        import socket
        product, cpu = "", ""
        try:
            product = open("/sys/class/dmi/id/product_name").read().strip()
        except OSError:
            pass
        try:
            for line in open("/proc/cpuinfo"):
                if "model name" in line:
                    cpu = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
        host = socket.gethostname()
        _machine_blurb_cache = ", ".join(
            x for x in [product, host, cpu, "running Linux"] if x)
    return _machine_blurb_cache


def _vitals_str(vitals: dict | None) -> str:
    if not vitals:
        return "unavailable right now"
    bits = []
    if vitals.get("cpu") is not None:
        bits.append(f"cpu {round(_f(vitals.get('cpu'))) or 0}%")
    if vitals.get("ram") is not None:
        bits.append(f"ram {round(_f(vitals.get('ram'))) or 0}%")
    if vitals.get("temp") is not None:
        bits.append(f"core temp {round(_f(vitals.get('temp'))) or 0}C")
    if vitals.get("battery") is not None:
        bits.append(f"battery {round(_f(vitals.get('battery'))) or 0}%")
    if vitals.get("disk") is not None:
        bits.append(f"disk {round(_f(vitals.get('disk'))) or 0}% full")
    return ", ".join(bits) if bits else "unavailable right now"


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def build_system_prompt(vitals: dict | None = None, stance: str | None = None,
                        context: dict | None = None,
                        aware: dict | None = None) -> str:
    user_name = config.USER_NAME or "the human"
    return SYS_PROMPT.format(
        machine=_machine_blurb(),
        user_name=user_name,
        vitals=_vitals_str(vitals),
        stance=stance or "none; pick your own reaction",
        grounding=grounding.facts(_vitals_str(vitals), _machine_blurb(),
                                  context, aware),
    )

_history: collections.deque = collections.deque(maxlen=8)
_lock = asyncio.Lock()

# ------------------------------------------------- unprompted lines (chatter)

# Appended to the normal system prompt when she speaks unprompted: same voice,
# same grounding, different situation — nobody is talking to her.
CHATTER_ADDENDUM = """

UNPROMPTED LINE
Nobody is talking to you. You noticed something on your own and say ONE line
out loud, to the room. One sentence, under 20 words, no quotes, no stage
directions. Not a greeting-card line, not a status report, not assistant talk:
something only you would say, about what you actually just noticed. A flat
observation or a complaint beats a joke. Reply with the line and nothing else.
"""

_chatter_lock = asyncio.Lock()   # serializes chatter with itself
_recent: collections.deque = collections.deque(maxlen=16)  # lines she's said


def _norm_line(s: str) -> str:
    """Loose normalization so punctuation/casing can't fake a fresh line."""
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", (s or "").lower()).split())


def _clean_line(raw: str | None) -> str | None:
    """Strip wrapping quotes and whitespace; None if nothing is left."""
    s = (raw or "").strip().strip("\"'`\u201c\u201d\u2018\u2019").strip()
    return s or None


def _chatter_dup(line: str) -> bool:
    """True if she already said (nearly) this — as chatter or as a reply."""
    n = _norm_line(line)
    said = {_norm_line(t) for t in _recent}
    said |= {_norm_line(str(m["content"])) for m in _history
             if m["role"] == "assistant"}
    return n in said

_current = 0            # index into config.PROVIDERS of the sticky winner
_failures: dict = {}    # name -> consecutive failed attempts
_cool_until: dict = {}  # name -> monotonic time until which we don't ask


def _cooling(name: str) -> bool:
    return time.monotonic() < _cool_until.get(name, 0.0)


def _pool_order() -> list:
    """Current winner first, then the rest in config order (cooling skipped)."""
    providers = config.PROVIDERS
    order = providers[_current:] + providers[:_current]
    return [p for p in order if not _cooling(p["name"])]


def beats(text: str, max_beats: int = 4) -> list:
    """Split a reply into speakable beats (sentences), capped."""
    import re
    # Split AFTER sentence-ending punctuation (a "..." run stays with its
    # sentence); a trailing fragment without punctuation is kept, not dropped.
    rough = [b.strip() for b in re.split(r"(?<=[.!?])\s+", text.strip()) if b.strip()]
    if len(rough) > max_beats:
        head = rough[: max_beats - 1]
        head.append(" ".join(rough[max_beats - 1:]))
        rough = head
    return rough


async def generate_reply(user_text: str, vitals: dict | None = None,
                         stance: str | None = None,
                         context: dict | None = None,
                         aware: dict | None = None) -> str | None:
    """One conversational exchange. None on failure/busy."""
    if _lock.locked():
        return None  # busy: caller decides the UX
    async with _lock:
        return await _generate(user_text, vitals, stance, context, aware)


async def _generate(user_text: str, vitals: dict | None,
                    stance: str | None, context: dict | None = None,
                    aware: dict | None = None) -> str | None:
    messages = (
        [{"role": "system", "content": build_system_prompt(vitals, stance,
                                                           context, aware)}]
        + list(_history)
        + [{"role": "user", "content": user_text}]
    )
    reply = await _call_llm(messages)
    if reply is not None:
        reply = await _grounded(reply, messages, user_text, vitals, aware)
    if reply is not None:
        _history.append({"role": "user", "content": user_text})
        _history.append({"role": "assistant", "content": reply})
    return reply


async def _grounded(reply: str, messages: list, user_text: str,
                    vitals: dict | None, aware: dict | None = None) -> str:
    """Screen the reply for things she can't know; one retry, then deflect."""
    hist = " ".join(m["content"] for m in _history if m["role"] == "user")
    problems = grounding.check(reply, user_text, hist, vitals, aware)
    if not problems:
        return reply
    print(f"remix-brain: reply failed grounding ({'; '.join(problems)}) — retrying")
    retry = messages + [{"role": "assistant", "content": reply},
                        {"role": "system", "content": grounding.retry_note(problems)}]
    second = await _call_llm(retry)
    if second and not grounding.check(second, user_text, hist, vitals, aware):
        return second
    print("remix-brain: retry failed grounding too — deflecting in character")
    return grounding.deflection()


def add_assistant(text: str) -> None:
    """Record a line she said without a user prompt (proactive chatter), so
    the next real exchange knows she just said that."""
    if text:
        _history.append({"role": "assistant", "content": text})
        _recent.append(text)


def _llm_once_sync(provider: dict, messages: list,
                   temperature: float = 0.7) -> str:
    url = provider["url"].rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": provider["model"],
        "temperature": temperature,  # replies 0.7 (cooler = less invention),
        # chatter 0.85 (a bit more life). glm-4.7's reasoning_content is
        # written BEFORE content on the same token budget; 500 was starved during tonight's load storms
        # (2026-11-06), 2048 survived. Empty content is retried.
        "max_tokens": 2048,
        "messages": messages,
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": "Bearer " + provider["key"],
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=config.LLM_TIMEOUT_S) as r:
            data = json.loads(r.read())
        content = (data["choices"][0]["message"]["content"] or "").strip()
        if not content:
            raise RuntimeError("empty content (reasoning ate the budget?)")
        return content
    except urllib.error.HTTPError as e:
        detail = (e.read().decode(errors="replace") or "")[:120]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e


_PER_PROVIDER_ATTEMPTS = 2


async def _call_llm(messages: list, temperature: float = 0.7) -> str | None:
    """Rotate across the provider pool, healthy providers first."""
    global _current
    usable = _pool_order()
    if not usable:
        names = ", ".join(str(p["name"]) for p in config.PROVIDERS)
        print(f"remix-brain: all providers cooling down ({names}) — "
              f"sitting out ~{config.LLM_PROVIDER_COOLDOWN_S:.0f}s")
        return None
    overall_delay = 3.0
    for p in usable:
        for i in range(_PER_PROVIDER_ATTEMPTS):
            try:
                reply = await asyncio.to_thread(_llm_once_sync, p, messages,
                                                temperature)
                if reply:
                    if p["name"] != config.PROVIDERS[_current]["name"]:
                        print(f"remix-brain: right brain answered via "
                              f"{p['name']}")
                    _failures[p["name"]] = 0
                    _cool_until[p["name"]] = 0.0
                    _current = config.PROVIDERS.index(p)  # winner is sticky
                    return reply
            except Exception as e:
                _failures[p["name"]] = _failures.get(p["name"], 0) + 1
                print(f"remix-brain: LLM {p['name']} attempt "
                      f"{i + 1}/{_PER_PROVIDER_ATTEMPTS} failed: {e}")
                await asyncio.sleep(min(overall_delay, 15.0)
                                    + random.uniform(0, overall_delay))
                overall_delay = overall_delay * 1.6
        if _failures.get(p["name"], 0) >= _PER_PROVIDER_ATTEMPTS:
            _cool_until[p["name"]] = (time.monotonic()
                                      + config.LLM_PROVIDER_COOLDOWN_S)
            print(f"remix-brain: {p['name']} cooling down "
                  f"({config.LLM_PROVIDER_COOLDOWN_S:.0f}s)")
    return None


async def generate_proactive_line(plan: dict, vitals: dict | None = None,
                                  context: dict | None = None,
                                  aware: dict | None = None) -> str | None:
    """Write one UNPROMPTED line fresh from the situation the trigger engine
    noticed. Same $0 provider pool, same persona, same grounding screen as a
    reply — no pre-written pools, and Jev is never called (stance is local).
    Returns None when the pool is down or nothing clean comes back: the caller
    treats that as silence, never as a cue to recite something canned."""
    if _lock.locked():            # a real exchange owns the brain: step aside
        return None
    async with _chatter_lock:
        situation = str(plan.get("situation") or "").strip()
        if not situation:
            return None
        messages = ([{"role": "system", "content":
                     build_system_prompt(vitals, plan.get("stance"),
                                         context, aware) + CHATTER_ADDENDUM}]
                    + list(_history)      # continuity: she won't re-tread it
                    + [{"role": "user", "content":
                        f"You just noticed: {situation}"}])

        def reject(s: str | None) -> str | None:
            """None = the line is good; otherwise a corrective note."""
            if not s:
                return ("Say one line. One sentence, under 20 words, no "
                        "quotes.")
            problems = grounding.check(s, "", "", vitals, aware)
            if problems:
                return ("Your line broke the rules: " + "; ".join(problems)
                        + ". One line, using ONLY what's on your 'what you "
                          "can see' list.")
            if _chatter_dup(s):
                return ("You already said that. A different line, same "
                        "voice, about the same situation.")
            if len(s) > 200:
                return "Too long. One sentence, under 20 words."
            return None

        cleaned = _clean_line(await _call_llm(messages, temperature=0.85))
        note = reject(cleaned)
        for _ in range(1):        # ONE corrective retry, then silence
            if note is None:
                return cleaned
            messages = messages + [
                {"role": "assistant", "content": cleaned or "(nothing)"},
                {"role": "system", "content": note}]
            cleaned = _clean_line(
                await _call_llm(messages, temperature=0.85))
            note = reject(cleaned)
        return cleaned if note is None else None
