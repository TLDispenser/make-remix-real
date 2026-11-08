# config.py — thresholds, ports, model names. Env vars win over defaults.
# Keys NEVER enter code (spec ground rule); .env is loaded if present.
#
# Naming (2026-11-08): settings read as REMIX_* first; the old CORE_* names
# are still honored so an existing .env keeps working until it's updated.
import itertools
import os
from pathlib import Path

BRAIN_DIR = Path(__file__).resolve().parent


def _load_dotenv() -> None:
    """Tiny .env loader (no third-party dependency).
    Duplicate keys: the FIRST definition wins (os.environ is never
    overwritten) and later ones get named out loud — a twice-set knob that
    silently loses is exactly the kind of thing that wastes an evening."""
    env_file = BRAIN_DIR.parent / ".env"
    if not env_file.exists():
        return
    seen: set = set()
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not key:
            continue
        if key in seen:
            print(f"remix-brain: .env: '{key}' defined more than once — the "
                  f"first definition won")
            continue
        seen.add(key)
        os.environ.setdefault(key, value)


_load_dotenv()


def _env(name: str, default: str | None = None) -> str | None:
    """REMIX_<name> first, legacy CORE_<name> second, then the default."""
    v = os.environ.get(f"REMIX_{name}")
    if v is None:
        v = os.environ.get(f"CORE_{name}")
    return v if v is not None else default


# ---- WebSocket (Python -> Godot) ----
WS_HOST = _env("WS_HOST", "127.0.0.1")
WS_PORT = int(_env("WS_PORT", "8765"))

# The person at the keyboard, for the system prompt ("Say \"you\""). Set
# REMIX_USER_NAME in .env to what she should call you in instruction context.
USER_NAME = _env("USER_NAME", "")

# ---- Body / vitals ----
VITALS_INTERVAL_S = float(_env("VITALS_INTERVAL_S", "1.0"))
MOCK = _env("MOCK", "0") == "1"  # real vitals verified 2026-11-06; set REMIX_MOCK=1 for demo feeds

# ---- RuleBrain thresholds (safe defaults; tune per host via env) ----
TEMP_ALARM_C = float(_env("TEMP_ALARM_C", "85"))
CPU_SPIKE = float(_env("CPU_SPIKE", "92"))    # instant spooked
CPU_HIGH = float(_env("CPU_HIGH", "75"))      # sustained anger
CPU_FLAT = float(_env("CPU_FLAT", "15"))      # idle-machine rolls
BATT_LOW = float(_env("BATT_LOW", "15"))      # tired
BATT_DROOP = float(_env("BATT_DROOP", "35"))  # pout-ish
DISK_FULL = float(_env("DISK_FULL", "90"))    # notice only
# Pose-change gating: N consecutive agreeing snapshots + min hold seconds.
POSE_CANDIDATE_N = int(_env("POSE_CANDIDATE_N", "3"))
POSE_MIN_HOLD_S = float(_env("POSE_MIN_HOLD_S", "4.0"))
NOTICE_MIN_GAP_S = float(_env("NOTICE_MIN_GAP_S", "240.0"))

# ---- On-screen text rule (2026-11-08) ----
# Flavor notices only when the machine is quiet (muted or low volume) — see
# audio.py. NOTICE_GATE=0 restores the old always-show behavior.
NOTICE_GATE = _env("NOTICE_GATE", "1") == "1"
QUIET_VOLUME = float(_env("QUIET_VOLUME", "0.15"))   # at or below = "low"
AUDIO_GATE_TTL_S = float(_env("AUDIO_GATE_TTL_S", "5.0"))

# ---- Awareness (Phase 6): senses + privacy + unprompted chatter ----
# Senses (awareness.py): Hyprland active window (app CLASS only), user idle
# via cursor/app-change polling, AC plug state, audio quiet state.
AWARENESS = _env("AWARENESS", "1") == "1"
AWARENESS_INTERVAL_S = float(_env("AWARENESS_INTERVAL_S", "5.0"))
# PRIVACY (spec): window titles NEVER reach Jev/LLM by default — only the app
# class name does. List app classes here to let their titles through, still
# scrubbed of URLs/emails/IPs/paths, e.g. "mpv,spotify,strawberry".
TITLE_ALLOW = _env("TITLE_ALLOW", "")
# The opt-in-EVERYTHING switch (2026-11-08). REMIX_TITLES:
#   off   (default) titles never pass; TITLE_ALLOW above still works per-class
#   scrub every focused-window title passes, still scrubbed of
#         emails/URLs/IPs/paths (recommended if opting in — raw titles often
#         carry tokens, and she SPEAKS)
#   raw   every title passes untouched (length-capped at 200 chars — safety
#         rail, not redaction). They ride inside the cloud LLM prompts and she
#         may read them aloud or caption them. That is the point; you asked.
TITLES = (_env("TITLES", "off") or "off").strip().lower()

# Unprompted lines (proactive.py). The TRIGGER is local and $0 (never Jev);
# the WORDS are written fresh by the same $0 provider pool her replies use
# (speech.generate_proactive_line), grounded + screened + deduped. No
# pre-written pools: if generation fails she stays quiet. /quiet toggles at
# runtime; CHATTER_TIMEOUT_S bounds how long the words may take.
PROACTIVE = _env("PROACTIVE", "1") == "1"
PROACTIVE_TICK_S = float(_env("PROACTIVE_TICK_S", "20.0"))
PROACTIVE_MIN_GAP_S = float(_env("PROACTIVE_MIN_GAP_S", "1200.0"))    # 20 min
PROACTIVE_MAX_PER_DAY = int(_env("PROACTIVE_MAX_PER_DAY", "8"))
CHATTER_TIMEOUT_S = float(_env("CHATTER_TIMEOUT_S", "45.0"))  # 25 starved: z.ai free tier routinely took longer than 25 s for the long proactive prompt (2026-11-08 log: every attempt died in TimeoutError)
IDLE_GENTLE_S = float(_env("IDLE_GENTLE_S", "900.0"))     # 15 min -> "still there?"
IDLE_DEEP_S = float(_env("IDLE_DEEP_S", "2700.0"))        # 45 min -> soft check-in
AWAY_S = float(_env("AWAY_S", "1800.0"))                  # 30 min = "was away"
MUTED_NAG_S = float(_env("MUTED_NAG_S", "600.0"))         # quiet 10 min -> comment
SESSION_LONG_S = float(_env("SESSION_LONG_S", "10800.0")) # 3 h -> stretch nudge
APP_STUCK_S = float(_env("APP_STUCK_S", "5400.0"))        # 90 min same app
QUIET_HOURS = _env("QUIET_HOURS", "")                     # "1-8" = no chatter 01:00-08:59
# Awaken (memory.py): if she was off at least this long, the boot greeting
# becomes "I just woke up / we were apart for X" instead of a plain greeting.
AWAKEN_MIN_S = float(_env("AWAKEN_MIN_S", "120.0"))
# Ambient thoughts (2b): between real events, a small-chance idle musing so
# her chatter feels random inside the same caps (MIN_GAP, day cap, quiet hours).
AMBIENT_RATE = float(_env("AMBIENT_RATE", "0.03"))        # per eligible 20 s tick
AMBIENT_COOLDOWN_S = float(_env("AMBIENT_COOLDOWN_S", "2400.0"))  # >=40 min
# Urgent marker (6.4): muted_long / idle_deep fire -> she flags her window as
# "urgent" until the human shows up or the room un-mutes. Godot may ignore it.
URGENT_ENABLED = _env("URGENT", "1") == "1"

# ---- Left brain (Jev, System 1) — BUDGET-GATED (user: limited credits) ----
# Policy (exercised by JevBrain, Phase 3; RuleBrain never calls Jev):
#   1. Jev fires ONLY on user input or notable events, never on a timer.
#   2. Cooldown between any two calls (rate cap)
#   3. Hard hourly cap -> silence falls back to RuleBrain, never an error
#   4. Deduped: same state signature returns the cached decision (TTL)
JEV_API_KEY = os.environ.get("JEV_API_KEY", "")
JEV_ENABLED = bool(JEV_API_KEY)
JEV_URL = os.environ.get("JEV_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = os.environ.get("JEV_MODEL", "jev-latest")
JEV_TIMEOUT_S = float(os.environ.get("JEV_TIMEOUT_S", "3.0"))
JEV_COOLDOWN_S = float(_env("JEV_COOLDOWN_S", "3.0"))
JEV_MAX_PER_HOUR = int(_env("JEV_MAX_PER_HOUR", "60"))
JEV_CACHE_TTL_S = float(_env("JEV_CACHE_TTL_S", "300.0"))

# Face variety (jev.py): sample from Jev's own probabilities, and discount
# whatever the face showed recently, instead of always taking the top pick.
JEV_FACE_TEMPERATURE = float(_env("JEV_FACE_TEMPERATURE", "1.0"))
JEV_REPEAT_PENALTY = float(_env("JEV_REPEAT_PENALTY", "0.55"))

# ---- Phase 5: push-to-talk STT (all local; mic open only while held) ----
PTT_ENABLED = _env("PTT", "1") == "1"
# Recorder: "auto" tries parec, pw-record, arecord (first one installed). Or give
# a full command that writes raw s16le / 16 kHz / mono PCM to stdout.
PTT_REC_CMD = _env("PTT_REC_CMD", "auto")
PTT_MODEL = _env("PTT_MODEL", "tiny.en")       # or a local dir; int8 on CPU
PTT_LANGUAGE = _env("PTT_LANGUAGE") or None    # None = autodetect ("en" for *.en)
PTT_SOCK = _env(
    "PTT_SOCK",
    (os.environ.get("XDG_RUNTIME_DIR", "/tmp") + "/remix-ptt.sock"))
PTT_MIN_PRESS_S = float(_env("PTT_MIN_PRESS_S", "0.3"))
# Hard cap: the mic is CLOSED by the brain after this long even if the key-up
# never arrives (lost release event), so the mic can never stay open.
PTT_MAX_PRESS_S = float(_env("PTT_MAX_PRESS_S", "15.0"))
PTT_MIN_PEAK = float(_env("PTT_MIN_PEAK", "0.015"))  # below = silence, skip whisper

# ---- Voice out (Phase 7): espeak-ng, NOT an AI voice (user constraint) ----
TTS_ENABLED = _env("TTS", "1") == "1"
TTS_BIN = _env("TTS_BIN", "espeak-ng")
TTS_VOICE = _env("TTS_VOICE", "en-us+f3")
TTS_SPEED = int(_env("TTS_SPEED", "160"))       # words/min
TTS_PITCH = int(_env("TTS_PITCH", "42"))        # 0-99; lower = flatter
TTS_AMP = int(_env("TTS_AMP", "120"))           # 0-200
TTS_BEAT_GAP_S = float(_env("TTS_BEAT_GAP_S", "0.2"))
# Prosody (6.3): per-beat speed/pitch leaning from the beat's own text —
# still espeak, NOT an AI voice; 0 disables and the fixed constants return.
TTS_PROSODY = _env("TTS_PROSODY", "1") == "1"
TTS_DIR = _env(
    "TTS_DIR",
    (os.environ.get("XDG_RUNTIME_DIR", "/tmp") + "/remix-voice"))
# Captions on her face (the notice line): auto = only when she has no voice.
SUBTITLES = (_env("SUBTITLES", "auto") or "auto").lower()   # auto|always|never

# ---- Right brain (System 2) — OpenAI-compatible chat, $0 default ----
# Primary: Z.ai GLM-4.7-Flash (permanent free tier). Its free tier sheds load
# intermittently (HTTP 429 / code 1305, verified 2026-11-06), so speech.py
# rotates to BACKUP providers when the primary proves stuck. All providers in
# the pool must be $0 (user ground rule).
LLM_API_KEY = _env("LLM_API_KEY", "")
LLM_BASE_URL = _env("LLM_BASE_URL", "https://api.z.ai/api/paas/v4")
LLM_MODEL = _env("LLM_MODEL", "glm-4.7-flash")
LLM_TIMEOUT_S = float(_env("LLM_TIMEOUT_S", "45.0"))


def _providers() -> list:
    """Ordered provider pool: primary first, then REMIX_LLM_BACKUPn_* (the
    legacy CORE_LLM_BACKUPn_* names still work)."""
    pool = [{"name": "zai", "url": LLM_BASE_URL, "model": LLM_MODEL,
             "key": LLM_API_KEY} if LLM_API_KEY else None]
    for i in itertools.count(1):
        url = _env(f"LLM_BACKUP{i}_URL", "")
        if not url:
            break
        model = _env(f"LLM_BACKUP{i}_MODEL", "")
        key = _env(f"LLM_BACKUP{i}_API_KEY", "")
        if model and key:
            pool.append({"name": f"backup{i}", "url": url, "model": model,
                         "key": key})
    return [p for p in pool if p]


PROVIDERS = _providers()
LLM_PROVIDER_COOLDOWN_S = float(_env("LLM_PROVIDER_COOLDOWN_S", "600.0"))
LLM_MAX_CONCURRENT = 1  # z.ai free Flash models allow ONE concurrent request

# ---- Memory (Phase 7 polish): her sandbox + persistent journal ----
# A folder that is HER repo: sandbox/memory.json (the journal) plus anything
# else she is wired to write later. Optionally mirrored to a GitHub repo so
# "space" is never a worry and she survives a disk move. The token is read at
# push time only and never embedded in the stored remote URL.
SANDBOX_DIR = Path(_env("SANDBOX_DIR") or (BRAIN_DIR / "sandbox"))
MEMORY_JSON = SANDBOX_DIR / "memory.json"
MEMORY_REPO = _env("MEMORY_REPO", "")            # e.g. https://github.com/me/remix-sandbox.git (PRIVATE)
MEMORY_TOKEN = _env("MEMORY_TOKEN", "")          # fine-grained PAT; contents:read/write on that repo
MEMORY_SYNC_MIN_S = float(_env("MEMORY_SYNC_MIN_S", "1800.0"))   # push this often if dirty
MEMORY_MAX_ENTRIES = int(_env("MEMORY_MAX_ENTRIES", "400"))      # journal rows kept
# Temper (1a): a slight per-boot personality drift, and REMIX_TEMPER sets the
# baseline: cranky (default, the character you have now), balanced, soft.
TEMPER = (_env("TEMPER", "cranky") or "cranky").strip().lower()
TEMPER_REHOUR = float(_env("TEMPER_REHOUR", "6.0"))   # re-roll the drift every N h
