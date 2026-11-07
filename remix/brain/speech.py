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
import time
import urllib.error
import urllib.request

from . import config

# The user-authored system prompt, verbatim. Four placeholders get wired at
# call time: {machine} (hardware blurb), {user_name} (CORE_USER_NAME),
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
Your vitals are how you feel: {vitals}. Bring them up only when they matter (hot, disk nearly full, battery low), in the first person, as a complaint with a fix. No computer puns, ever.

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
why is my pc so slow -> "Because you've got forty fucking tabs open and my disk is almost full. Close some shit."
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


def build_system_prompt(vitals: dict | None = None, stance: str | None = None) -> str:
    user_name = config.CORE_USER_NAME or "the human"
    return SYS_PROMPT.format(
        machine=_machine_blurb(),
        user_name=user_name,
        vitals=_vitals_str(vitals),
        stance=stance or "none; pick your own reaction",
    )

_history: collections.deque = collections.deque(maxlen=8)
_lock = asyncio.Lock()

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
    rough = re.findall(r"[^.!?]+[.!?]+", text.strip()) or ([text.strip()]
                                                           if text.strip() else [])
    rough = [b.strip() for b in rough if b.strip()]
    if len(rough) > max_beats:
        head = rough[: max_beats - 1]
        head.append(" ".join(rough[max_beats - 1:]))
        rough = head
    return rough


async def generate_reply(user_text: str, vitals: dict | None = None,
                         stance: str | None = None) -> str | None:
    """One conversational exchange. None on failure/busy."""
    if _lock.locked():
        return None  # busy: caller decides the UX
    async with _lock:
        return await _generate(user_text, vitals, stance)


async def _generate(user_text: str, vitals: dict | None,
                    stance: str | None) -> str | None:
    messages = (
        [{"role": "system", "content": build_system_prompt(vitals, stance)}]
        + list(_history)
        + [{"role": "user", "content": user_text}]
    )
    reply = await _call_llm(messages)
    if reply is not None:
        _history.append({"role": "user", "content": user_text})
        _history.append({"role": "assistant", "content": reply})
    return reply


def _llm_once_sync(provider: dict, messages: list) -> str:
    url = provider["url"].rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": provider["model"],
        "temperature": 0.9,
        # glm-4.7's reasoning_content is written BEFORE content on the same
        # token budget; 500 was starved during tonight's load storms
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


async def _call_llm(messages: list) -> str | None:
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
                reply = await asyncio.to_thread(_llm_once_sync, p, messages)
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
