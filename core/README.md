# CORE — a PC-as-avatar with a two-brain architecture

Godot window = **body** (display/input only). Python process = **brain**
(left brain: instant typed decisions; right brain: an $0 LLM writes the words).
See `../CORE_SPEC.md` for the why, `../RESEARCH_NOTES.md` for everything
verified against live docs, and the user's constraints locked in on 2026-11-05:
**free LLM only · Godot visuals authored by the user · TTS non-AI (espeak-ng).**

## Status

| Phase | State |
|---|---|
| 0 Research | ✅ done — see `../RESEARCH_NOTES.md` |
| 1 The body | ✅ built — mock feed on, awaiting your visuals + first run |
| 2 Nervous system (real vitals) | not started (flip `CORE_MOCK=0`) |
| 3 Left brain (RuleBrain + JevBrain) | not started |
| 4 Right brain (LLM words) | not started |
| 5 Push-to-talk (local STT) | not started (Hyprland binds chosen, see notes) |
| 6 Awareness | not started (`hyprctl activewindow -j` verified working) |
| 7 Voice out + polish | not started (espeak-ng via pyttsx3; **not AI voice**) |

## Run it

```bash
# 1) brain (this hosts ws://127.0.0.1:8765 with the Phase 1 mock feed)
core/brain/run.sh
#    -> "ws server on ws://127.0.0.1:8765 (v1)"

# 2) body (Godot 4.7 — install it from godotengine.org/download if missing)
godot --path core/godot          # or open core/godot/project.godot in the editor
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

## Message protocol `v1` (JSON over ws://localhost:8765)

Every message carries `"v":1`. From Python to Godot:

```jsonc
{"v":1,"type":"vitals","cpu":42,"ram":61,"temp":58,"battery":80,"disk":12}
{"v":1,"type":"mood","mood":"stressed","confidence":0.91,"source":"mock|jev|rule"}
{"v":1,"type":"reflex_line","text":"Ow, easy on the CPU."}
{"v":1,"type":"say","text":"...","mood":"happy","final":true}   // final:false = streaming chunk
{"v":1,"type":"event","name":"download_done"}
{"v":1,"type":"speaking_audio","path":"..."}   // Phase 7
{"v":1,"type":"listening","state":"start"}      // Phase 5 mic indicator
{"v":1,"type":"transcript","text":"...","final":true}
```

From Godot to Python:

```jsonc
{"v":1,"type":"user_text","text":"..."}
{"v":1,"type":"user_typing"}                    // instant "thinking" reflex trigger
{"v":1,"type":"ui","action":"mute|quit|toggle_input"}
```

Mood vocabulary (fixed): `idle, happy, stressed, sleepy, thinking, listening, alert, bored`.

## Keys & env

Copy `core/.env.example` → `core/.env` and fill:
- `JEV_API_KEY` — from https://console.typesafe.ai/keys (left brain; optional,
  `RuleBrain` runs without it)
- `CORE_LLM_API_KEY` — **free** key from z.ai (right brain, default
  `glm-4.7-flash`, $0; Groq/Gemini/OpenRouter drop-in via two lines)

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
