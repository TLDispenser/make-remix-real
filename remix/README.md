# REMIX — a PC-as-avatar with a two-brain architecture

Godot window = **body** (display/input only). Python process = **brain**
(left brain: instant typed decisions; right brain: an $0 LLM writes the words).
See `../CORE_SPEC.md` for the why, `../RESEARCH_NOTES.md` for everything
verified against live docs, and the user's constraints locked in on 2026-11-05:
**free LLM only · Godot visuals authored by the user · TTS non-AI (espeak-ng).**

## Status

| Phase | State |
|---|---|
| 0 Research | ✅ done — see `../RESEARCH_NOTES.md` |
| 1 The body | ✅ **user-built**: SVG-driven ribbon face, CRT shader, protocol v2 (see `godot/README.md`, `godot/PROTOCOL.md`) |
| 2 Nervous system (real vitals) | ✅ done — psutil feed verified on her host (`CORE_MOCK=0` is now the default) |
| 3 Left brain | ✅ RuleBrain (vitals → poses, $0) live • JevBrain = key-gated, budget policy below |
| 4 Right brain (LLM words) | pending `CORE_LLM_API_KEY` (free, z.ai — how-to below) |
| 5 Push-to-talk (local STT) | not started (Hyprland binds chosen, see notes) |
| 6 Awareness | not started (`hyprctl activewindow -j` verified working) |
| 7 Voice out + polish | not started (espeak-ng via pyttsx3; **not AI voice**) |

## The face (user's Godot work)

The Godot half was rebuilt by the user 2026-11-06: faces are authored as SVGs
(`godot/art/faces/*.svg -> scripts/face_shapes.gd` via `godot/tools/svg_to_shapes.py`),
every feature is a 10-point ribbon, poses carry per-pose motion limits, and the
playback contract is audio-clock-synced `perform` cues. **Read** `godot/README.md`
and `godot/PROTOCOL.md` — the protocol lives THERE (single source of truth).

## Run it

```bash
# Easiest — chat with her right in a terminal (starts the brain if needed):
./talk.sh
#    Type; Enter to send; "exit" or Ctrl-C to stop (brain keeps running).
#    Her words show here AND on her face if she's displayed.
# One-liner instead of a REPL:
./talk.sh "hey there"

# Manual two-terminal run (if you'd rather start pieces yourself):
#   remix/brain/run.sh      -> "ws server on ws://127.0.0.1:8765 (v2)"
#   godot --path remix/godot   # or open remix/godot/project.godot in the editor
```

First-run checklist (Hyprland):
- `hyprctl clients | grep -i class` → find the avatar's window **class**
- pin it above everything: `windowrulev2 = pin on, float on, class:^(<that class>)$`
  (Godot's `always_on_top` is X11-only on Wayland — verified in docs.)
- If per-pixel transparency misbehaves, `DisplayServer.is_window_transparency_available()`
  already falls back to an opaque dark window.
- Click-through: NOT available under Wayland (Godot implements
  mouse-passthrough for X11 only). Options: XWayland run
  (`env -u WAYLAND_DISPLAY godot ...` gets passthrough + always-on-top), or
  keep the window tight around the avatar. Tell me which you prefer.

## Message protocol `v2` (see `godot/PROTOCOL.md` for the full contract)

The user's Godot half defines the protocol; the brain speaks it. What the brain
currently sends (silent life):

```jsonc
{"v":2,"type":"vitals","cpu":42,"ram":61,"temp":58,"battery":80,"disk":12}  // pass-through
{"v":2,"type":"pose","pose":"anger","look":[0.2,-0.3]}   // RuleBrain decisions (debounced)
{"v":2,"type":"look","look":[0.7,0.2]}                   // gaze drift, no pose change
{"v":2,"type":"twitch"}                                   // allowed in anger/pout
{"v":2,"type":"notice","text":"// ... //","seconds":3.0}  // rare flavor (>=4 min gap)
```

Godot sends `ready` (poses list) and `perform_done`; the brain holds the poses
list for name validation and pauses silent posing across performances.

## Jev credit budget (user: limited credits — enforced from Phase 3 on)

**Silent life NEVER calls Jev.** The RuleBrain does all vitals->pose work for
free. Jev fires only on (a) user input arriving (typed/voice, Phase 5) and
(b) notable events the rules flag. Enforced knobs (env, see `config.py`):

- `CORE_JEV_COOLDOWN_S` (3) — minimum seconds between any two Jev calls
- `CORE_JEV_MAX_PER_HOUR` (60) — hard hourly cap; over cap -> RuleBrain answer, silently
- `CORE_JEV_CACHE_TTL_S` (300) — identical state signature = cached decision, no call
- One Jev call carries BOTH the reaction stance AND the per-beat acting plan
  (pose list per beat) — no per-beat calls.

## Keys & env

Copy `remix/.env.example` -> `remix/.env` and fill:
- `JEV_API_KEY` — from https://console.typesafe.ai/keys (left brain; optional,
  `RuleBrain` runs without it)
- `CORE_LLM_API_KEY` — **free** key from z.ai (right brain, default
  `glm-4.7-flash`, $0; Groq/Gemini/OpenRouter drop-in via two lines)

Getting the GLM key (free, no card): 1) make an account at z.ai; 2) profile
menu -> **API Keys** -> Create a new API key; 3) paste it in `remix/.env`
above. The free flash model allows one concurrent request (config already
respects that).

**Provider failover:** `speech.py` rotates a pool of providers if the
primary proves stuck (z.ai's free tier intermittently sheds load). Add
backups with `CORE_LLM_BACKUP1_URL/_MODEL/_API_KEY` in `.env` — AI Studio's
free `gemini-3.5-flash-lite` via its OpenAI-compatible endpoint is the
natural second (2.5-flash is dead for new users; 3.5-flash-lite / 3.8-flash
/ 3.6-flash are the free-tier models as of 2026-11-06). Losers cool down 10
minutes; the winner stays until it fails.

Smoke tests (only after adding a key):

```bash
# Jev smoke test (1 call: auth + request shape):
curl -s https://api.typesafe.ai/v1/systemone \
  -H "Authorization: Bearer $JEV_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"jev-latest","state":"test","questions":{"is_awake":{"type":"noul","instructions":"Is the test message about sleep?"}}}'

# LLM smoke test (1 call, default provider):
curl -s https://api.z.ai/api/paas/v4/chat/completions \
  -H "Authorization: Bearer $CORE_LLM_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"glm-4.7-flash","messages":[{"role":"user","content":"Say hello in one sentence."}]}'
```

## Privacy rules baked in (non-negotiable, spec)

- Push-to-talk only; mic closed otherwise; transcription fully local
  (whisper.cpp/faster-whisper, base/tiny model on CPU — no GPU); audio
  never written or uploaded; always-visible mic indicator.
- Window titles run through user-configurable allowlist/redaction before
  leaving the machine (Jev / LLM keys go to $0 handling: mock-"safe" only).
- Keys in `.env` (gitignored) only — never code, logs, or version control.
