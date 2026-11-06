# config.py — thresholds, ports, model names. Env vars win over defaults.
# Keys NEVER enter code (spec ground rule); .env is loaded if present.
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

# ---- Body / vitals ----
VITALS_INTERVAL_S = float(os.environ.get("CORE_VITALS_INTERVAL_S", "1.0"))
CORE_MOCK = os.environ.get("CORE_MOCK", "1") == "1"  # Phase 1: mock feed first

# ---- Left brain (System 1) ----
JEV_ENABLED = bool(os.environ.get("JEV_API_KEY"))
JEV_URL = os.environ.get("JEV_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = os.environ.get("JEV_MODEL", "jev-latest")
JEV_TIMEOUT_S = float(os.environ.get("JEV_TIMEOUT_S", "3.0"))
# Mood re-eval is rate-limited: only when vitals move this much, or at most
# every N seconds (keeps cost/limits sane; see RESEARCH_NOTES.md).
MOOD_MIN_DELTA = float(os.environ.get("CORE_MOOD_MIN_DELTA", "5.0"))
MOOD_MAX_GAP_S = float(os.environ.get("CORE_MOOD_MAX_GAP_S", "20.0"))
CONFIDENCE_FLOORS = {  # below these -> safe defaults (doc rule from spec)
    "mood": 0.55,
    "should_speak": 0.7,
}

# ---- Right brain (System 2) — OpenAI-compatible chat, $0 default ----
# Default: Z.ai GLM-4.7-Flash, permanent free tier, best personality per user
# pick. Swap Provider by editing base model URL only. See RESEARCH_NOTES.md.
LLM_API_KEY = os.environ.get("CORE_LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("CORE_LLM_BASE_URL", "https://api.z.ai/api/paas/v4")
LLM_MODEL = os.environ.get("CORE_LLM_MODEL", "glm-4.7-flash")
LLM_TIMEOUT_S = float(os.environ.get("CORE_LLM_TIMEOUT_S", "30.0"))
LLM_MAX_CONCURRENT = 1  # z.ai free Flash models allow ONE concurrent request
