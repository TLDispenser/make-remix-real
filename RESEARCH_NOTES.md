# RESEARCH_NOTES

All sources read **2026-11-05** by the build agent. Rule: docs beat the spec; discrepancies are noted inline.

## Environment (verified on the target machine)

| Item | Value | Consequence |
|---|---|---|
| OS | Linux, Arch-family | sensors via `/sys/class/hwmon`; community docs assume X11 less and less |
| Display server | **Wayland (Hyprland 0.56.2)** | see Wayland section |
| GPU | none (Intel i7-4310M-class iGPU, i5-4310M CPU, 8 GB RAM) | whisper on CPU only; keep Godot visuals light |
| Python | 3.14.7 system; project venv at `core/brain/.venv` | pin deps `<8.0` where noted |
| Godot | **not installed yet** | user installs Godot 4.7; see install note in README |

User constraints locked in (Nov 5 2026):
- LLM (right brain): must be **$0, no cost of any kind** → no Anthropic, no OpenAI. Default is a permanent free tier (see "Right brain").
- Godot: ship **base project only**; user authors all visuals and reports scene structure afterwards.
- TTS: **no AI/neural voices.** espeak-ng/pyttsx3 only (formant synthesis, zero neural models — verified fit for this constraint).

---

## TypeSafe / Jev (left brain) — VERIFIED, spec's guess mostly right

**URLs read:** https://typesafe.ai (home), /blog/introducing-system-one-models-and-jev (Sep 15 2026, Diogo Almeida), /manifesto, official docs https://docs.typesafe.ai, API reference https://docs.typesafe.ai/api/system-one, OpenAPI 3.1 spec `https://api.typesafe.ai/openapi.json`, console https://console.typesafe.ai (keys at `/keys`, playground). Third-party: OpenRouter `~typesafe/jev-latest`, Cloudflare `typesafe/jev`, LiteLLM gateway. (An HF-blog pointer to `thejevai.com` looked unofficial; disregarded.)

**Endpoint & auth (from OpenAPI spec):**
- `POST https://api.typesafe.ai/v1/systemone`, header `Authorization: Bearer $JEV_API_KEY`
- Request core (all top-level): `model`, `state`, `questions`
- `state`: string | object | array (images need `assets` + a `TypeSafe-Danger-Zone-Images: true` header — not needed here)
- `model`: `jev-latest` alias confirmed (concrete version in responses, e.g. `jev-1.13.0`); a `jev-preview` also exists

**Question primitives: 4 documented, not 3 (SPEC CORRECTION).**
| Primitive | Returns | Use for this project |
|---|---|---|
| `noul` | probability of "yes" 0–1 | `should_speak`, `needs_deep_reply` |
| `choice` | `{choice, confidence, probabilities}` | `mood`, `event_importance`, `user_intent` |
| `score` | rubric score + legend + confidence | optional quality scoring (not v1) |
| `box` | visual localization (newer, gated) | not needed |

- Example request body verified from docs:
```json
{"model":"jev-latest",
 "state":"I was charged twice. Please refund the extra charge.",
 "questions":{"billing":{"type":"noul","instructions":"Is this message about billing?"}}}
```
- Response shape: `{"model":..., "answers":{"<key>":{"type":...,...typed value, confidence, probabilities}}, "usage":{"input_tokens":..., "output_tokens":...}}`
- Pricing page claims output tokens free, ~$42/M input tokens (re-verify at integration time; we already rate-limit calls ourselves).
- Status: **early access** — the user holds early access and supplies `JEV_API_KEY`; smoke test command ships in `.env.example` / README until the key exists.
- "OpenAI-style dispatch" is right in spirit (Bearer, `model` field, `/v1/`, JSON) but there is **no messages array, no chat facade, no streaming** — a purpose-built decisions endpoint.

---

## Godot (body)

**Version: stable is 4.7** (all fetched docs pages say "Godot Engine 4.7 documentation"). Target 4.7.

**Window setup — exact setting names (from class_projectsettings + class_window):**
- `display/window/size/borderless = true`
- `display/window/size/transparent = true` (hint only; needs the two below to actually be see-through)
- `display/window/per_pixel_transparency/allowed = true`
- `rendering/viewport/transparent_background = true` (sets `Viewport.transparent_bg` on root)
- Runtime: set flags on the root `Window` (`borderless`, `transparent`); check support with `DisplayServer.is_window_transparency_available()` and degrade to a solid borderless window if false.

**Wayland implementation matrix (per 4.7 class Window docs, quoted impl lists):**
| Capability | Wayland |
|---|---|
| `borderless` | ✅ implemented |
| per-pixel transparency | ✅ implemented ("availability might vary by GPU driver, display manager, compositor") |
| `always_on_top` | ❌ **not implemented** (X11 only) |
| `DisplayServer.window_set_mouse_passthrough(PackedVector2Array)` | ❌ **not implemented** (Linux impl list says X11 only) — NOTE: polygon semantics = region that *accepts* input; `[]` resets to intercept-everything |
| drag | ✅ `Window.start_drag()` / `DisplayServer.window_start_drag()` implemented on X11+Wayland; docs recommend calling on press (Under Wayland the compositor must own repositioning — manual move-to is unreliable) |
| `WebSocketPeer` | platform-neutral |

**Hyprland recoveries for the ❌ items (our compositor, 0.56.2 present on machine):**
- Always-on-top: pin the window — window rule `windowrulev2 = pin on, float on, class:^(godot)$` (or runtime `hyprctl dispatch pin float`). **Untested yet — verify Godot's actual window class under Hyprland (`hyprctl clients | grep class`) the first time it runs.**
- Click-through: no Wayland equivalent in Godot. Mitigation: keep the window small and hug the avatar's bounding box so "empty areas" barely exist; drop the click-through requirement out-of-the-box, or run under XWayland (`env -u WAYLAND_DISPLAY godot ...`) where X11-based always-on-top AND mouse passthrough both work. Document both modes in README; ask user to compare.
- Window title: set `Window.title = "Core"` at runtime so hyprland window rules can match `title:^(Core)` reliably.

**Client WebSocket (`WebSocketPeer` in 4.7):** `connect_to_url(url)` (non-blocking) → per-frame `poll()` → `get_ready_state()` (`STATE_CONNECTING/OPEN/CLOSING/CLOSED`) → text via `send_text(msg)`, receive via `get_available_packet_count()` + `get_packet()` (PacketPeer). Use `ws://localhost:8765` plain — TLS checks only apply to `wss://`.

**Speech bubble basics (verified):** `PanelContainer` (or backgroundless `Control`) + `Label` with `autowrap_mode > TextServer.AUTOWRAP_OFF` and a width via `custom_maximum_size`.

---

## Python (brain)

**psutil 7.2.2 current** (2026-01-28); **psutil 8.0 is unreleased & breaking — pin `psutil>=7.2,<8`.**
- CPU%: `psutil.cpu_percent(interval=1)` blocking (1 Hz loop OK) — first call meaningless; discard.
- RAM%: `psutil.virtual_memory().percent`.
- Disk: `psutil.disk_usage('/').percent`.
- Battery: `psutil.sensors_battery()` → `.percent`, `.power_plugged` (None on desktops — handle).
- Temp (Linux): `psutil.sensors_temperatures()['coretemp'][0].current`; wrap in try/except (hwmon may be absent).

**websockets 17.x current (17.4, 2026-10-30 — includes a security fix; require `>=17.4`).**
Modern asyncio API — the shape we use (two-arg `(websocket, path)` handler is long REMOVED):
```python
from websockets.asyncio.server import serve
async def handler(websocket):          # single connection arg
    ...
async with serve(handler, "127.0.0.1", 8765):
    await asyncio.Future()             # run forever
```
Broadcast: `from websockets.asyncio.server import broadcast`.
- **Client `connect()` gives up fast (no retry loop) if the server socket isn't
  bound yet** — hit during smoke testing; Godot-side must treat connect
  failures as retryable with its own delay (main.gd already does: 2 s interval).
- `serve()` alias-imported as `_ws_serve` inside `ws_server.py` — classic
  shadowing pitfall found in practice: a project function named `serve()`
  silently called itself.

**Active window on Hyprland (verified live on the machine):**
- `hyprctl activewindow -j` returns JSON incl. `class`, `title`, `at`, `size`, `floating`. Poll via `asyncio.create_subprocess_exec` at low Hz.
- Content titles (browser origins like `brave-origin` are FMSharer-disguised at IPC level still show class) — redaction allowlist still applies per spec.

**Idle time on Wayland:** compositor protocol `ext-idle-notify-v1` gives only idled/resumed *edge events* (no numeric duration) — accumulate user-idle seconds by counting state transitions; Hyprland implements it (Hyprland ships `hypridle`, same protocol). Deferred to Phase 6 — X11 fallback: `xprintidle`.

**Global push-to-talk hotkey (Wayland):** `pynput`/`keyboard` are confirmed **X11-only in practice** (maintainer statement: no Wayland backend). Compositors own global keys. Two viable patterns, both planned:
1. **Hyprland binds → exec a one-liner → Unix socket to the brain.** Hyprland supports release-triggers (`bindr`), so true hold-to-talk = press bind + release bind. Permission-light, zero extra daemons.
2. **evdev passive listener** (`python-evdev`, reading `/dev/input/event*`) — compositor-agnostic, gives press AND release, needs `input` group only.
Cap: mic open only while held (spec's privacy rule is non-negotiable).

**STT (Phase 5, CPU-only box):** whisper.cpp release line ~1.9.5 or faster-whisper 1.2.1. Benchmark on a near-identical 2010-era 2c/4t CPU (whisper.cpp discussion #3752): `tiny` q4_0 ≈ 1.58× realtime, `base` q4_0 ≈ 0.86× realtime, q4_0 fastest quant by far (f16 ~4× slower). Haswell adds AVX → expect ~1–3× realtime either way for short clips. Decide then: fastest + lightest = **whisper.cpp as subprocess with `base.en`-class ggml q4_0 model**; fallback = tiny. Both strictly local; first-run model download noted.

**Mic capture:** sounddevice 0.5.6 (2026-08-17). Pattern for hold-to-talk: `sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=...)`, append `indata` copies in the callback, `np.concatenate` on release (repo's `rec_unlimited.py` pattern). Whispers wants 16 kHz mono int16 — no resample needed. Never run blocking calls on the asyncio loop — stream callback + `run_in_executor` for transcribe.

**TTS (Phase 7):** pyttsx3 v2.99 (2025-07-08; maintenance weak-but-alive) → **espeak-ng backend on Linux** (`pyttsx3.init("espeak")`, needs `espeak-ng` + libespeak package — **not installed on the machine yet; install then**). Formant synthesis = classic robotic voice, zero neural models → satisfies "no AI voice." Fully offline. Property keys verified: `rate`, `volume`, `voice`.

---

## Right brain (LLM) — user constraint: $0 total

Spec named Anthropic as example, but the user cannot pay anything. Verified free tiers (pricing pages checked 2026-10/11 via web):

| Provider | Free reality | Note |
|---|---|---|
| **Z.ai — GLM-4.7-Flash** | **$0 permanently** on input/output/cached-input; no card; no published request-count ceiling (reported ~1 req/s); docs page says "Completely Free" | **DEFAULT — best free personality** (GLM Flash models are the most free-tier character/lively; user's stated priority) |
| Google Gemini Flash free tier | $0 | outer limits ~15 RPM / 1500 RPD; **free-tier content trains Google products** |
| Groq free plan | $0, 30 RPM / 1K RPD / 200K token-day (gpt-oss-120b) | fast, but personality-neutral |
| OpenRouter `:free` | $0 but capped at 50 req/day | too tight for chat |
| Anthropic | **pay-per-token only — LOCKED OUT** by user constraint | removed from default path |

**Config-made swappable:** `right_brain.py` speaks generic **OpenAI-compatible chat completions** (base URL + model from config), because Z.ai (`https://api.z.ai/api/paas/v4`, model `glm-4.7-flash`), Groq (`https://api.groq.com/openai/v1`), Gemini's compat endpoint, and OpenRouter all expose that shape. Switching = one config edit. No SDK hardcoding; auth: `Authorization: Bearer $LLM_API_KEY`.
- Z.ai free-model caveat (verified from error docs): **1 concurrent request per model** on free Flash → serialize LLM calls (one in flight at a time), 429 backoff on codes 1302/1305, and send an explicit `User-Agent`.
- Free tiers can rotate models: model id is config, never hardcoded.

---

## Protocol / layout deltas vs spec

- Spec's protocol block, folder layout, phases, `v1` message versioning, privacy rules — adopted as-is, except:
- Sonnet-referencing line replaced by provider-agnostic config (above).
- `Godot -> Python` `ui` action and `user_typing` unchanged; push-to-talk handled Python-side via Hyprland binds (see above).
- Godot 4.7 not older; project targets 4.7 stable so doc-verified names hold exactly.

## Open items to confirm with the user (as they come up)
1. Jev smoke test once `JEV_API_KEY` is supplied (curl in README).
2. Hyprland window-class string for the built avatar (`hyprctl clients`) → finalize `pin` window rule test.
3. Whether transparency renders on Hyprland as expected (Godot side) and whether XWayland fallback is preferred for click-through — user advices after first run.
4. espeak-ng install (Phase 7): `pacman -S espeak-ng` (or distro equiv).

---

# NEW_SPEC.md addendum (read 2026-11-05)

NEW_SPEC renames the project to **Remix** (she/her) and adds a character spec.
Deltas applied:
- Folder `core/` → `remix/` (git mv); window title, project name, docs, and
  log prefixes all say **Remix**. No "Core" anywhere.
- Character reference material lives in **`references/`** (the spec writes
  `reference/` singular — using the folder the user actually created:
  Faces.png, Anger.png present; **character scripts NOT yet provided** — ask
  the user, since the spec says to read them before designing the character).
- Mood vocabulary, protocol `v1`, thresholds — unchanged from verification.

**Right-brain conflict (spec vs user constraint, user wins):** NEW_SPEC still
lists Anthropic/Claude as the right brain and mentions Piper (neural TTS).
The user's standing constraints from the Phase-0 clarification (2026-11-05)
override both: right brain must be **$0 of any kind** (→ GLM-4.7-Flash via
Z.ai, OpenAI-compatible, config-swappable), and TTS must be **non-AI**
(→ espeak-ng/pyttsx3; Piper is neural — excluded). Logged here as a
deliberate doc-vs-user resolution, per the "user's asks win" precondition
they set in this repo's history.

**Face rendering system (NEW work, spec'd):**
- Face = glyph vocabulary adapted from `references/Faces.png`, NOT Mech's
  green — Remix uses **red**, one theme-variable color (shader uniform,
  settings-exposed) since her laptop palette may clash.
- Eyes: polygons with a FIXED point count; mouth: polyline with a FIXED point
  count. An expression = target points + per-feature scale/rotation; springs
  glide every point toward targets so any expression morphs into any other and
  interrupted morphs stay smooth. Unmorphable shapes (hollow outlines, the
  scribble/static face) transition via short glitchy crossfade / static burst.
- CRT shader: phosphor glow/bloom, faint scanlines, subtle flicker, slight
  edge bleed — each effect adjustable and cheap (weak iGPU machine).
- Additive idle life: blinking (eyes collapse to a line), breathing glow, gaze
  drift, occasional flicker. Body posing is a later polish phase; face first.

Build order respected: Phase 0-1 already done (research + body). The face
system slots into Phase 1/2 Godot work before Phase 3 brain work.

## Face-system engine findings (2026-11-05, from headless validation)

- **Padded/degenerate point runs + springs = broken polygons.** Repeating the
  final vertex to fill a fixed point budget is NOT spring-safe: each
  duplicate carries its own velocity, so mid-morph they diverge by fractions
  of a pixel and the rendered polygon self-intersects (bowtie) → Godot's
  earcut fails ("Invalid polygon data, triangulation failed").
- **Two disjoint sub-shapes concatenated into ONE polygon path** (the doubled
  bar) also fail triangulation regardless of morphs.
- Solution implemented: glyph vocabulary = compositions of ≤4 REAL quads
  (unused budget slots get hidden duplicates of real geometry, not zeros);
  face.gd draws each quad as TWO triangles with an |area|-maximizing diagonal
  and skips ~zero-area triangles. Springs now can never produce an invalid
  draw. Atlas contract: 16 pts = 4 quads per feature (`tests/atlas_audit.gd`).
- Websockets client gotcha: `connect()` gives up fast (no retry) if the
  server socket isn't bound yet — Godot side retries on its own 2 s timer.
- venv viability after folder renames: venv scripts embed absolute paths —
  recreated `remix/.venv`; removed the stale duplicate.

## Protocol v2 score (2026-11-06, user rebuilt the Godot half)

The user authored her face: SVG poses -> 10-pt ribbons (`svg_to_shapes.py`),
per-pose motion limits (`face_config.gd`), CRT shader rewrite, and a
performance contract: brain sends ONE `perform` with audio path + cue times;
Godot fires cues off the audio's own playback clock (face/voice can't drift).
Full contract: `godot/PROTOCOL.md` (single source of truth). Brain side was
rebuilt to v2 this session: pose decisions live in `brain/rules.py`
(RuleBrain), stream verified by `brain/tools/selftest.py` (invariants only —
organic pose changes on a live host are nondeterministic; the pose-CHANGE
path is validated by synthetic-feed unit tests instead, which passed:
alarm/spike -> immediate spooked, sustained -> streak+hold gating, noisy
single samples -> no flip).

Real-host sensor readings (2026-11-06): coretemp works (57-72 °C), CPU idles
10-25% with compile bursts to 100, RAM ~84%, battery present, **disk 99%
full** (added a notices branch; user should know). Threshold dangers on a
live host: idle CPU sits right around 12-18% so ANY threshold testing must
come from synthetic feeds, not live runs.

## Provider failover (added 2026-11-06, user request)

speech.py's right brain is a POOL: primary (z.ai) + `CORE_LLM_BACKUPn_*`.
Verified semantics with fakes: sticky winner, immediate rotation after 2
failed attempts, 10-min circuit breaker (cooling providers skipped),
all-cooling -> graceful None. All providers must be $0 (ground rule).
Gotcha found + fixed in the same session: `_current` needed a `global`
declaration in `_call_llm` (assigned and read in the same scope).

Gemini as backup: OpenAI-compatible endpoint is
`https://generativelanguage.googleapis.com/v1beta/openai` + an AI Studio key
(aistudio.google.com, free). Verified 2026-11-06 from the Gemini pricing page:
**gemini-3.5-flash-lite** (recommended — cost-efficient, high-volume; what
deprecated 2.5-flash routes into), **gemini-3.8-flash** (smartest Flash) and
**gemini-3.6-flash** are all "Free of charge" on the Free tier for input AND
output. gemini-2.5-flash is dead for new users (API limited to existing
users since ~2026-10). Grounding/search is paid-only on free tier — we don't
use it. Smoke-tested 2026-11-06: raw curl to `v1beta/openai/chat/completions`
with `gemini-3.5-flash-lite` -> HTTP 200, clean content, finish_reason stop.
Full code path verified too (throwaway process, z.ai simulated-cooling):
rotation picked `backup1` through the real sysprompt and answered in voice.
Gemini leg is LIVE.

## GLM (right brain) key — how the user gets one (verified 2026-11-06)

1. Account at **z.ai** (chat signup works; no card required).
2. Profile menu -> **API Keys** -> Create a new API key.
3. Put it in `remix/.env` as `CORE_LLM_API_KEY`.
4. Endpoint/model already defaulted: `https://api.z.ai/api/paas/v4` +
   `glm-4.7-flash` (free variant; supports ONE concurrent request — config
   already caps concurrency at 1).
OpenAI-compatible chat/completions shape (curl smoke test in remix/README.md).

## Jev credit budget policy (locked 2026-11-06; enforced by JevBrain)

User's constraint: Jev credits are FINITE. Policy encoded in config.py:
1. **Silent life NEVER calls Jev** — RuleBrain ($0) owns all vitals->pose.
2. Jev fires on user input arrival or events the rules flag — never a timer.
3. Cooldown `CORE_JEV_COOLDOWN_S` between any two calls (default 3 s).
4. Hard hourly cap `CORE_JEV_MAX_PER_HOUR` (default 60) — over the cap the
   RuleBrain answer ships silently instead (no errors, no retry spam).
5. State-signature cache `CORE_JEV_CACHE_TTL_S` (default 300 s): unchanged
   state = cached decision, zero calls.
6. ONE Jev call per reaction covers stance AND the full per-beat acting plan
   (pose list for each beat) — never per-beat calls.
7. Every failure path (timeout/error/over-budget) falls back to RuleBrain.

**JevBrain live (2026-11-06).** Re-verified the OpenAPI schema before any
call: `choice` criteria = {name: "when it applies"}; answers carry
confidence + probabilities; one request = many named questions. ONE real
call burned (user-authorized): "pc has been running slow... thirty tabs
open" → 0.18 s round trip, 1619/485 tokens, stance=playful (0.51), pose
smirk, smirk beat arc, rest smirk. Offline (faked transport) tests pass:
parsing + bogus-pose drop, cooldown block, cache hit = 0 calls, hourly cap,
transport failure → None, disabled → None. In production, during a real
z.ai 1305 storm the pool rotated to backup1 (failover machinery verified
live) but the user's `.env` still carried the dead `gemini-2.5-flash`
string -> 404 -> no reply available. Lesson: rotation code was right, the
config was stale; `.env` now says `gemini-3.5-flash-lite`. Brain restarts
need `setsid` — nohup'd children die when the harness reaps the process
group.

## Voice, faces, grounding (2026-11-07)
- **TTS:** `espeak-ng --stdin --stdout` (non-AI, per the locked constraint) -> per-beat WAV, joined with a
  0.2 s gap into one 22.05 kHz mono s16 file; beat starts become `perform` cue times. Verified here with
  real espeak-ng 1.51 and a real Godot 4.7 playing the file (`perform_done ok=true`).
- **STT:** faster-whisper on a float32 array (no WAV encoding), VAD on, `condition_on_previous_text=False`,
  silence/phantom filtering. Recorder auto-detect: `parec` -> `pw-record` -> `arecord`. NOT verified here:
  a real whisper model download (huggingface.co is blocked in the build sandbox) and a real PipeWire mic;
  both were tested through fakes (fake recorder process, fake model).
- **Hyprland release binds are `bindr`**, not an `up` argument (the earlier draft was wrong).
- **Jev smirk diagnosis (from brain.log):** 30/30 reactions were `pose=smirk`, stance confidence 0.3-0.8, per-beat
  poses asked BEFORE her words existed (so identical), scene text said "sarcastic", argmax every time. Fixed by
  separate eyes/mouth questions, neutral scene text, sampling from `probabilities` with a repeat penalty, and a
  post-words acting call. NOT verified against live Jev (no key in the build sandbox): the `probabilities`
  shape is handled as dict or list and degrades to the top `choice` if it differs.
- **Hallucination diagnosis:** the sysprompt's own example asserted "forty fucking tabs open"; nothing told her
  what she can't see. Fixed with an explicit "what you can see" block + a reply screen (grounding.py).

## Loose-ends pass (2026-11-08): naming, text rule, Hyprland syntax, stance, tests

- **`CORE_*` -> `REMIX_*` (45 settings).** config.py reads `REMIX_<name>` first,
  legacy `CORE_<name>` second, so the existing `.env` (still CORE_-named, keys
  seen but never printed) keeps working until it is rewritten. `.env.example`,
  README, run.sh, comments updated. `JEV_*` was never prefixed and stays.
- **`.gitignore` fixed:** it ignored `remix/brain/.venv/` but the venv lives at
  `remix/.venv` (~477 MB was one `git add` away from a commit). `.env` was and
  remains ignored. Reminder: the shared zip contained `.env` with two live keys
  — rotate both (Jev + z.ai) on the user side.
- **On-screen text rule enforced (was violated by RuleBrain flavor notices).**
  New `brain/audio.py`: `wpctl get-volume @DEFAULT_AUDIO_SINK@` (WirePlumber
  0.5.x manual verified: output `Volume: 0.68`, gains `[MUTED]` when muted);
  muted or volume <= `REMIX_QUIET_VOLUME` (0.15) = "quiet" -> flavor text
  allowed; audible -> suppressed. Refreshed off-loop every 5 s (cache), fails
  OPEN to text when wpctl is missing/broken (pre-gate behavior), and
  `REMIX_NOTICE_GATE=0` disables. A suppressed attempt still consumes the >=4
  min notice gap (at most one try per gap). Cutesy notice lines rewritten dry
  ("choo-choo motif", "heart: warmer" gone). DECISION: the mic-transcript line,
  "one sec", and ear-problem notices are exempt — functional/privacy, not
  flavor (she must always be checkable about what was heard).
- **Hyprland window rules (verified against the wiki + 0.53-era forum configs):
  `windowrulev2` is deprecated since 0.53**; current form is
  `windowrule = match:title ^Remix$, float on, pin on, no_initial_focus on`
  (matchers carry a `match:` prefix; a Lua `hl.window_rule({...})` form also
  exists for 0.55+ configs). README checklist + hyprland-setup.md updated.
  The click-through question (XWayland vs tight window) is still the user's call.
- **Jev stance lopsidedness (live log: playful 40 / flat 26 / annoyed 2 /
  alarmed 1 / fond 0 / concerned 0):** the stance question gave Jev no trigger
  guidance. The react question now maps triggers explicitly (sad/hurt/worried ->
  concerned; praise/warmth -> fond; insults -> annoyed; scary system -> alarmed;
  teasing -> playful; flat only as the resting stance). Measuring it needs real
  credits: `brain/tools/stance_probe.py` runs a 20-line battery (one call per
  line, `--yes` required) and prints the spread + never-fired stances. NOT run
  here (no key in the build sandbox) — first thing to run on her machine.
- **Godot tests rewritten for the ribbon mouth** (Godot 4.7-stable headless,
  run in the build sandbox): engine_test's old "mouth widens" check referenced
  the removed `widen` key (would crash: "Invalid get index"); replaced with the
  shipped contract — mouth OPENS (thickness, capped per shape's `open`), never
  gets longer, rests when quiet. perform_test now tracks `mouth_open_scale`
  (opens while loud, rests in the quiet gap) instead of mouth width. NEW
  `tests/talk_test.gd` covers the TALK_* handoff deterministically: sub-threshold
  level keeps the expression mouth, sound flips to the straight bar re-seated at
  the expression's height with the shape's `open` cap, mid-speech mouth swaps
  re-seat the bar, TALK_RELEASE returns the expression. `run_all.sh` runs it.
  Full suite: SHAPES/ENGINE/TALK/PROTOCOL/PERFORM/E2E **ALL GREEN**.
- **Specs de-staled:** CORE_SPEC.md carries a SUPERSEDED banner (history only);
  NEW_SPEC.md got a reality pass (right brain = $0 pool, TTS = espeak-ng,
  protocol v2 pointer, actual folder layout, mood-vocabulary annotation, sysprompt
  pointer to speech.py). README now points to NEW_SPEC.md.
