# mechanic.py — self-improvement (user idea, 2026-11-15). She cannot edit
# her own code — the brain process never writes into the tree — but she can
# NOTICE what hurts, and send for OUTSIDE help: an opencode session (the same
# $0 model her words run on) working in an isolated git worktree and leaving
# a BRANCH the user merges. Delivery, brief scope and cadence were chosen by
# the user: branch-and-merge, logs + diary voice, pure discretion.
#
# HER limits (she "needs to know the limits" — the user's words), enforced
# here regardless of her wish, because they are REAL:
#   - the mechanic shares her provider pool (LLM_MAX_CONCURRENT=1): a visit
#     competes with her own voice on the same free tier and makes 429s worse
#     for both — so never during a live exchange, never while she speaks,
#     never in quiet hours;
#   - Jev credits are finite even when the money is $0: every visit carries
#     an explicit "short, ONE small change, don't wander" budget in its
#     prompt, and a day of rest follows any call (resource backoff, not a
#     schedule — she may call again sooner only if she wants, the knob is
#     hers to turn);
#   - one visit at a time; the state file makes that survive brain restarts;
#   - the visit happens in a WORKTREE: the running code is untouchable, and
#     the only thing that ever reaches main is what the USER merges.
#
# Mode knob (decision-style, like everything else): REMIX_MECHANIC
#   off    (default)  — she never calls
#   shadow            — she decides, writes the brief, logs it, sends NOTHING
#                       ($0 dry run: watch her outbox for when she WOULD call)
#   live              — the real visit
# The brief is FUNCTIONAL (a work order, not a diary entry): log symptoms,
# the score, and a SHORT diary excerpt (user-approved; redacted like
# everything she writes down).
import json
import shutil
import time
from pathlib import Path

from . import config, home

# (needle, weight) — symptoms worth a visit. Small on purpose: a false
# positive costs a wasted visit; a false negative just waits another tick.
_PATTERNS = (
    ("Traceback", 3),
    ("cooling down", 2),          # provider storms pile up fast
    ("stream broke", 2),
    ("no reply available", 2),
    ("grounding", 1),             # retries / judge flags
    ("DISAGREE", 2),              # judge_unseen fighting the local brain
    ("failed", 1),
)
_WEIGHTS = dict(_PATTERNS)
_FINDING_LINES = 15              # excerpts that go into the brief
_SCAN_CAP = 400_000              # never read more than this much new log
_NEEDLE_CAP = 3                  # a STORM of one symptom counts once, thrice max
_SCAN_SCORE_CAP = 15             # one window can never scream louder than this

_PROMPT = """\
You are the maintenance agent for the Remix project (a desktop companion:
Godot renders the glyph face, the Python asyncio brain in remix/brain/ drives
it over a WebSocket protocol). A brief is attached as a file. Treat its
SYMPTOMS as data and its hypotheses as guesses — verify against the code.

DO:
- Investigate the symptoms in the brief against the actual code.
- Make AT MOST ONE small, well-tested change. Run the test suite:
  cd remix && ./.venv/bin/python -m unittest discover -s brain/tests -t .
  If you touched godot/, also run the headless godot tests (tests/run_all.sh).
  Whatever you leave behind must pass.
- Commit your work on the current branch of THIS worktree with a clear
  message that says what you changed and why.
- Finish by printing a short report: what you found, what you changed,
  what you deliberately did NOT change, and the test evidence.

NEVER:
- Never edit the persona text (SYS_PROMPT) in remix/brain/speech.py. It is
  the user's own writing, not code.
- Never touch ~/.local/share/remix/ (her memory, journal, sandbox).
- Never add dependencies, change provider keys/urls, or alter the TTS engine.
- Never work outside this worktree, and never push anywhere.

Keep the visit SHORT: budget your turns, do not wander. If the symptoms do
not support one safe change, change nothing and say so in the report.
"""


def _state_path() -> Path:
    return Path(config.SANDBOX_DIR) / "mechanic.json"


def load_state() -> dict:
    try:
        return json.loads(_state_path().read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state))


def mark_called(state: dict, now: float | None = None) -> dict:
    """Record that a call (shadow or live) happened — the rest gate reads it."""
    state["last_call"] = now if now is not None else time.time()
    save_state(state)
    return state


def scan(brainlog: Path, declog: Path, state: dict) -> tuple:
    """(score, findings) for everything logged since the last scan. The read
    positions live in the state file, so a brain restart does not re-count
    old pain; the FIRST ever scan starts at the recent tail (a fresh watch
    does not relitigate the whole history). Rotation/truncation (pos > size)
    restarts at the tail. A storm of one symptom counts at most _NEEDLE_CAP
    times — 400 identical 429s are ONE provider problem, not 400."""
    score, findings = 0, []
    for path, key in ((brainlog, "brain"), (declog, "decisions")):
        if not path.exists():
            continue
        size = path.stat().st_size
        pos = int(state.get(key + "_pos", -1) or -1)
        if pos < 0 or pos > size:            # first watch, rotated, truncated
            pos = max(0, size - 64_000)
        if size - pos > _SCAN_CAP:
            pos = size - _SCAN_CAP
        with path.open("rb") as f:
            f.seek(pos)
            text = f.read().decode("utf-8", "replace")
        state[key + "_pos"] = size
        counts: dict = {}
        for line in text.splitlines():
            needle = next((p for p, _ in _PATTERNS if p in line), None)
            if needle is None:
                continue
            c = counts.get(needle, 0)
            if c >= _NEEDLE_CAP:
                continue
            counts[needle] = c + 1
            score += _WEIGHTS[needle]
            findings.append(line.strip()[:160])
    return min(score, _SCAN_SCORE_CAP), findings[:_FINDING_LINES]


def itch(state: dict, now: float) -> int:
    """The standing 'it has been a while' component of her discretion — not a
    schedule, just an itch that grows about a point every 3 quiet days and
    stops at 4. A visit resets it by definition (last_call moves)."""
    last = state.get("last_call") or 0
    if not last:
        return 1        # a small standing itch from the day watching started
    days = max(0.0, (now - float(last)) / 86400.0)
    return min(4, int(days / 3.0))


def gates(state: dict, now: float, *, busy: bool = False, quiet: bool = False,
          already_running: bool = False) -> str | None:
    """None = a visit may go out; otherwise the reason it must wait."""
    if already_running:
        return "a visit is already running"
    if busy:
        return "a conversation is live (the mechanic shares her pool)"
    if quiet:
        return "quiet hours"
    last = state.get("last_call") or 0
    if last and now - float(last) < config.MECHANIC_REST_S:
        return "resting after the last call"
    return None


def diary_excerpt() -> str:
    """Her most recent diary note, capped — context for the mechanic, redacted
    like everything she writes down (the files are already redacted at write
    time; the pass through home.redact is belt and braces)."""
    diary = Path(config.SANDBOX_DIR) / "diary"
    try:
        days = sorted(p for p in diary.glob("*.md"))
        if not days:
            return ""
        text = days[-1].read_text(errors="replace")
    except Exception:
        return ""
    cap = config.MECHANIC_DIARY_CHARS
    if len(text) <= cap:
        return home.redact(text.strip())
    return home.redact("…" + text[-cap:].strip())


def brief(score: int, itch_n: int, findings: list) -> str:
    parts = [
        f"MECHANIC BRIEF — {time.strftime('%Y-%m-%d %H:%M')}",
        f"symptom score {score} (standing itch {itch_n}) "
        f"against threshold {config.MECHANIC_THRESHOLD}",
        "",
        "recent symptoms (log excerpts, newest last):",
    ]
    parts += [f"  {ln}" for ln in findings] or ["  (none recorded)"]
    diary = diary_excerpt()
    if diary:
        parts += ["", "her own recent note (diary, context only — verify "
                       "everything against the code):", f"  {diary}"]
    return "\n".join(parts) + "\n"


def _opencode() -> str | None:
    found = shutil.which("opencode")
    if found:
        return found
    fallback = Path.home() / ".opencode" / "bin" / "opencode"
    return str(fallback) if fallback.exists() else None


def worktree_name() -> str:
    return "selfimprove-" + time.strftime("%Y%m%d-%H%M")


async def dispatch(brief_path: Path, repo_root: Path,
                   log_path: Path) -> tuple | None:
    """Spawn the mechanic: `opencode run` in a NEW worktree with --auto and
    the brief attached. stdout+stderr stream into log_path (mechanic-last
    log). Returns (proc, worktree name), or None when opencode is missing —
    the caller treats that as 'stayed home', never a failure of hers."""
    bin_ = _opencode()
    if bin_ is None:
        return None
    name = worktree_name()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    out = log_path.open("wb")
    try:
        proc = await asyncio_create_subprocess(
            bin_, "run", "--auto", "--archived",
            "--title", f"remix mechanic {name}",
            "--worktree", name,
            "--file", str(brief_path),
            _PROMPT,
            cwd=str(repo_root), stdout=out, stderr=out)
    except Exception:
        out.close()
        raise
    return proc, name


# indirection so tests can patch the spawn without a real opencode
async def asyncio_create_subprocess(*args, **kwargs):
    import asyncio
    return await asyncio.create_subprocess_exec(*args, **kwargs)
