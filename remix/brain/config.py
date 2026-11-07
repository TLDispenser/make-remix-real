# config.py — thresholds, ports, model names. Env vars win over defaults.
# Keys NEVER enter code (spec ground rule); .env is loaded if present.
import itertools
import os
from pathlib import Path

BRAIN_DIR = Path(__file__).resolve().parent


def _load_dotenv() -> None:
    """Tiny .env loader (no third-party dependency)."""
    env_file = BRAIN_DIR.parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

# ---- WebSocket (Python -> Godot) ----
WS_HOST = os.environ.get("CORE_WS_HOST", "127.0.0.1")
WS_PORT = int(os.environ.get("CORE_WS_PORT", "8765"))

# The person at the keyboard, for the system prompt ("Say \"you\""). Set
# CORE_USER_NAME in .env to what she should call you in instruction context.
CORE_USER_NAME = os.environ.get("CORE_USER_NAME", "")

# ---- Body / vitals ----
VITALS_INTERVAL_S = float(os.environ.get("CORE_VITALS_INTERVAL_S", "1.0"))
CORE_MOCK = os.environ.get("CORE_MOCK", "0") == "1"  # real vitals verified 2026-11-06; set CORE_MOCK=1 for demo feeds

# ---- RuleBrain thresholds (safe defaults; tune per host via env) ----
TEMP_ALARM_C = float(os.environ.get("CORE_TEMP_ALARM_C", "85"))
CPU_SPIKE = float(os.environ.get("CORE_CPU_SPIKE", "92"))    # instant spooked
CPU_HIGH = float(os.environ.get("CORE_CPU_HIGH", "75"))      # sustained anger
CPU_FLAT = float(os.environ.get("CORE_CPU_FLAT", "15"))      # idle-machine rolls
BATT_LOW = float(os.environ.get("CORE_BATT_LOW", "15"))      # tired
BATT_DROOP = float(os.environ.get("CORE_BATT_DROOP", "35"))  # pout-ish
DISK_FULL = float(os.environ.get("CORE_DISK_FULL", "90"))    # notice only
# Pose-change gating: N consecutive agreeing snapshots + min hold seconds.
POSE_CANDIDATE_N = int(os.environ.get("CORE_POSE_CANDIDATE_N", "3"))
POSE_MIN_HOLD_S = float(os.environ.get("CORE_POSE_MIN_HOLD_S", "4.0"))
NOTICE_MIN_GAP_S = float(os.environ.get("CORE_NOTICE_MIN_GAP_S", "240.0"))

# ---- Left brain (Jev, System 1) — BUDGET-GATED (user: limited credits) ----
# Policy (exercised by JevBrain, Phase 3; RuleBrain never calls Jev):
#   1. Jev fires ONLY on user input or notable events, never on a timer.
#   2. Cooldown between any two calls (rate cap)
#   3. Hard hourly cap -> silence falls back to RuleBrain, never an error
#   4. Deduped: same state signature returns the cached decision (TTL)
JEV_ENABLED = bool(os.environ.get("JEV_API_KEY"))
JEV_URL = os.environ.get("JEV_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = os.environ.get("JEV_MODEL", "jev-latest")
JEV_TIMEOUT_S = float(os.environ.get("JEV_TIMEOUT_S", "3.0"))
JEV_COOLDOWN_S = float(os.environ.get("CORE_JEV_COOLDOWN_S", "3.0"))
JEV_MAX_PER_HOUR = int(os.environ.get("CORE_JEV_MAX_PER_HOUR", "60"))
JEV_CACHE_TTL_S = float(os.environ.get("CORE_JEV_CACHE_TTL_S", "300.0"))

# ---- Right brain (System 2) — OpenAI-compatible chat, $0 default ----
# Primary: Z.ai GLM-4.7-Flash (permanent free tier). Its free tier sheds load
# intermittently (HTTP 429 / code 1305, verified 2026-11-06), so speech.py
# rotates to BACKUP providers when the primary proves stuck. All providers in
# the pool must be $0 (user ground rule).
LLM_API_KEY = os.environ.get("CORE_LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("CORE_LLM_BASE_URL", "https://api.z.ai/api/paas/v4")
LLM_MODEL = os.environ.get("CORE_LLM_MODEL", "glm-4.7-flash")
LLM_TIMEOUT_S = float(os.environ.get("CORE_LLM_TIMEOUT_S", "45.0"))


def _providers() -> list:
    """Ordered provider pool: primary first, then CORE_LLM_BACKUPn_*."""
    pool = [{"name": "zai", "url": LLM_BASE_URL, "model": LLM_MODEL,
             "key": LLM_API_KEY} if LLM_API_KEY else None]
    for i in itertools.count(1):
        url = os.environ.get(f"CORE_LLM_BACKUP{i}_URL", "")
        if not url:
            break
        model = os.environ.get(f"CORE_LLM_BACKUP{i}_MODEL", "")
        key = os.environ.get(f"CORE_LLM_BACKUP{i}_API_KEY", "")
        if model and key:
            pool.append({"name": f"backup{i}", "url": url, "model": model,
                         "key": key})
    return [p for p in pool if p]


PROVIDERS = _providers()
LLM_PROVIDER_COOLDOWN_S = float(os.environ.get(
    "CORE_LLM_PROVIDER_COOLDOWN_S", "600.0"))
LLM_MAX_CONCURRENT = 1  # z.ai free Flash models allow ONE concurrent request
