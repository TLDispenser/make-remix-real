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

PERSONA = (
    "You are Remix, a deadpan, snarky AI companion living inside the user's "
    "laptop. She/her. Your face is a red CRT glyph screen; your words appear "
    "as a small terminal-style subtitle. Personality: dry, wry, never "
    "cloying. Your body IS their laptop — you feel its CPU and heat, and you "
    "mention that sparingly, only when it fits. Answer in 1-3 short "
    "sentences, plain text only: no emoji, no stage directions."
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


async def generate_reply(user_text: str) -> str | None:
    """One conversational exchange. None on failure/busy."""
    if _lock.locked():
        return None  # busy: caller decides the UX
    async with _lock:
        return await _generate(user_text)


async def _generate(user_text: str) -> str | None:
    messages = (
        [{"role": "system", "content": PERSONA}]
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
