# speech.py — the right brain ($0 GLM via Z.ai) + the typed-input pipeline.
# Rules: the key must never be logged; errors degrade to None (caller falls
# back to a notice, never a crash). Z.ai's free flash tier periodically
# sheds load with 429/code 1305 — that is THEIR overload, so we retry with
# LONG waits and give up gracefully (remeasured 2026-11-06).
import asyncio
import collections
import json
import random
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
    """One conversational exchange with the persona. None on failure."""
    if not _lock.locked():
        async with _lock:
            return await _generate(user_text)
    return None  # busy: caller decides the UX


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


def _llm_once_sync(messages: list) -> str:
    url = config.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": config.LLM_MODEL,
        "temperature": 0.9,
        # NOTE: glm-4.7's reasoning_content is written BEFORE content on the
        # same budget; 500 was starved during tonight's load storms
        # (2026-11-06), 2048 survived. If content comes back empty, we retry.
        "max_tokens": 2048,
        "messages": messages,
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": "Bearer " + config.LLM_API_KEY,
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


async def _call_llm(messages: list, attempts: int = 4) -> str | None:
    if not config.LLM_API_KEY:
        print("remix-brain: no CORE_LLM_API_KEY — wordless mode")
        return None
    # Empty-content (short) vs 1305 overload (long): keep the first retry
    # snappy so a shed request still returns while you're at the keyboard.
    delay = 8.0
    for i in range(attempts):
        try:
            reply = await asyncio.to_thread(_llm_once_sync, messages)
            if reply:
                return reply
        except Exception as e:
            print(f"remix-brain: LLM attempt {i + 1}/{attempts} failed: {e}")
            # jittered backoff; Z.ai 1305 needs patience, not hammering
            await asyncio.sleep(delay + random.uniform(0, delay * 0.3))
            delay = min(delay * 2.2, 60.0)
    return None
