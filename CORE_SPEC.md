# CORE: a PC-as-avatar with a two-brain architecture

Build a desktop companion whose "body" is the user's PC itself. A Godot 4 app renders a living avatar. Real system vitals drive its mood. A fast "left brain" (TypeSafe's Jev, a System One model) makes instant decisions. A slow "right brain" (an LLM) writes the actual words.

---

## STEP 0: RESEARCH FIRST (MANDATORY, DO NOT SKIP)

Many things below are new or fast-moving (Jev launched September 2026; Godot, Claude model names, and SDKs change often). **Do not rely on memory or on this document for API shapes, version numbers, or model names.** Before writing any code:

1. Fetch and read the current docs listed in "Resources" below.
2. Create `RESEARCH_NOTES.md` in the project root recording, for each resource: the URL, the date you read it, the version in use, and the exact API shape you verified (endpoint, request body, response body, auth header, rate limits).
3. If a doc contradicts this spec, **the doc wins**. Note the discrepancy in `RESEARCH_NOTES.md` and adapt.
4. If you cannot find or verify something (especially Jev's endpoint, question types, or auth), **stop and ask the user**. Never guess an API schema.
5. Any time you hit an unexpected error from a library or API, re-check the docs for version changes before debugging blindly.

### Resources

**TypeSafe / Jev (left brain), highest priority, newest, least likely in your training data**
- https://typesafe.ai (start here, find the docs and API reference from the site and the early-access dashboard)
- https://typesafe.ai/blog/introducing-system-one-models-and-jev (concept, architecture, what System One models are)
- https://typesafe.ai/manifesto
- The user has early access and will supply `JEV_API_KEY`. Ask them for the docs link or dashboard if the public site lacks API reference.
- Known from public coverage (VERIFY, do not trust): Jev returns typed decisions with calibrated confidence instead of text; the HTTP request has top-level `state`, `model`, and `questions` fields; model name looked like `jev-latest`; there are three question primitives (one is a yes/no type). Confirm all of this and learn the other two primitives from the official docs.

**Godot (body)**
- https://docs.godotengine.org/en/stable/ (confirm the current stable 4.x version, and use the matching docs)
- Look up specifically: transparent window / per-pixel transparency, borderless window, always-on-top flag, `WebSocketPeer`, mouse passthrough (`DisplayServer.window_set_mouse_passthrough`), `AudioEffectSpectrumAnalyzer` or capture bus for lip-sync, `Tween`, `AnimationPlayer`.
- Platform quirks: transparency and passthrough behave differently on Windows, macOS, and Linux/Wayland. Check the current docs and known issues for the user's OS and **ask the user which OS they run**.

**Python (brain)**
- psutil: https://psutil.readthedocs.io (note: temperature and fan sensors are Linux/FreeBSD only; on Windows plan an alternative such as LibreHardwareMonitor or `wmi`, so research this for the user's OS)
- websockets: https://websockets.readthedocs.io (use the current asyncio API, which has changed across versions)
- Active-window detection: research the current best library for the user's OS (e.g. `pygetwindow`, `pywin32`, `pyobjc`, `xdotool`/`ewmh`). Verify it's maintained.

**Right brain (LLM)**
- Anthropic API docs: https://docs.claude.com and the models overview page there. **Look up the current model names and pick a fast, cheap one for chat. Do not hardcode from memory; make the model name a config value.**
- Anthropic Python SDK: check PyPI for the current version and the streaming interface.
- Optional local alternative: https://ollama.com and its API docs.

**Voice (Phases 5 and 7)**
- TTS: Piper (find the current maintained repo and voices via search), or pyttsx3 as a zero-setup fallback.
- STT (push-to-talk, local only): faster-whisper or whisper.cpp (check current repos, install steps, and which model sizes run well on the user's hardware; ask whether they have a GPU).
- Mic capture: `sounddevice` (check current docs) or an equivalent maintained library.
- Global push-to-talk hotkey: research the current best option for the user's OS (e.g. `pynput`, `keyboard`, or native APIs). **Godot only sees keys while its window is focused, and Core's window is usually unfocused and click-through, so the hotkey must be captured globally in Python.** Check known limits: macOS needs Accessibility/Input Monitoring permissions, Wayland restricts global key capture, and some libraries need admin rights on Linux. Ask the user which OS and (on Linux) which display server they use.

---

## Architecture

```
            +------------------------+
 vitals --> |  LEFT BRAIN (System 1) |--> reflex decisions (<500ms)
 events --> |  Jev API, typed output |       mood, speak?, react
 typing --> +-----------+------------+
                         | "speak? yes" / context
                         v
            +------------------------+
            | RIGHT BRAIN (System 2) |--> words (streamed)
            | Claude / Ollama        |
            +-----------+------------+
                        |
         Python "brain" process (asyncio)
                        | WebSocket ws://localhost:8765 (JSON)
                        v
            +------------------------+
            | GODOT: display only    |--> avatar, bubble, audio
            +------------------------+
```

**Principles**
- Godot is display and input only. All logic lives in Python.
- The left brain is **swappable** behind an interface (`decide(state) -> Decision`). Implement `JevBrain`, plus a `RuleBrain` fallback (plain if/else on thresholds) used when Jev is unreachable, rate-limited, or the key is missing. The app must still work with `RuleBrain` alone.
- The left brain never writes prose. The right brain never gates reflexes.
- Reflexes fire immediately; words arrive later and layer on top. The avatar must never freeze waiting on the LLM.

## What the left brain decides

Define decisions as typed questions about a compact `state` snapshot. Adapt to Jev's real question primitives after reading the docs. Target decisions:

| Decision | Type | Used for |
|---|---|---|
| `mood` | one of: idle, happy, stressed, sleepy, thinking, listening, alert, bored | avatar expression, updated on vitals change |
| `should_speak` | yes/no + confidence | idle chatter, event reactions |
| `event_importance` | one of: ignore, glance, react, interrupt | download done, low battery, app launched |
| `user_intent` | one of: question, command, chitchat, venting | route input, pick reply style |
| `needs_deep_reply` | yes/no | if no, send a short canned reflex line and skip the LLM |

Use the confidence values: below a threshold, fall back to a safe default (e.g. `idle`, don't speak). Make thresholds config values.

`state` snapshot (compact JSON, keep it small to stay fast): vitals, active window title, idle seconds, session length, time of day, last user message (if any), seconds since last spoken line.

Rate-limit left-brain calls (e.g. mood re-eval only when vitals change meaningfully or every N seconds) to control cost and respect API limits found in the docs.

## Message protocol (JSON over WebSocket)

Python -> Godot
- `{"type":"vitals","cpu":42,"ram":61,"temp":58,"battery":80,"disk":12}`
- `{"type":"mood","mood":"stressed","confidence":0.91}` (reflex, immediate)
- `{"type":"reflex_line","text":"Ow, easy on the CPU."}` (instant canned line)
- `{"type":"say","text":"...","mood":"happy","final":true}` (right brain; support streaming chunks with `final:false`)
- `{"type":"event","name":"download_done"}`
- `{"type":"speak_audio","path":"..."}` (Phase 7)
- `{"type":"listening","state":"start|stop"}` (mic indicator, see Phase 5)
- `{"type":"transcript","text":"...","final":true}` (optional: show what was heard)

Godot -> Python
- `{"type":"user_text","text":"..."}`
- `{"type":"user_typing"}` (triggers instant `thinking` reflex)
- `{"type":"ui","action":"mute|quit|toggle_input"}`
(Push-to-talk is handled entirely in Python via the global hotkey; Godot only displays state.)

Version the protocol (`"v":1`) and keep it documented in `README.md`.

## Folder layout

```
core/
  godot/            project.godot, scenes/, scripts/, assets/
  brain/
    main.py         asyncio entrypoint
    vitals.py       psutil + OS-specific sensors
    awareness.py    active window, idle time, session tracking
    left_brain.py   LeftBrain interface, JevBrain, RuleBrain
    right_brain.py  LLM client (streaming), system prompt, memory
    ws_server.py    WebSocket server + protocol
    stt.py          push-to-talk capture + local transcription
    config.py       thresholds, model names, ports (env + config file)
    memory.json     persistent notes about the user (gitignored)
  RESEARCH_NOTES.md
  README.md
  .env.example      JEV_API_KEY=, ANTHROPIC_API_KEY=
```

---

## Phases (finish and run each before starting the next)

**Phase 0: Research.** Complete Step 0. Produce `RESEARCH_NOTES.md`. Confirm user's OS. Send a one-call smoke test to Jev and the LLM to prove auth and request shape work.

**Phase 1: The body.** Borderless, transparent, always-on-top Godot window with click-through on empty areas and draggable avatar. Simple 2D avatar (glowing orb or face) with mood states and smooth transitions. Driven by a mock feed first.

**Phase 2: The nervous system.** `vitals.py` streams real vitals at 1 Hz. Godot maps vitals to visuals: CPU to pulse speed, RAM to size or fullness, temp to color flush, battery to droopiness. Graceful handling of unavailable sensors.

**Phase 3: The left brain.** Implement `RuleBrain` first, then `JevBrain` behind the same interface. Vitals drive `mood` through the left brain. Add `user_typing` -> instant `thinking`. Log decision, confidence, and latency for every call so the user can see it working. Add a small debug overlay (toggle with hotkey) showing the last decisions and latencies.

**Phase 4: The right brain.** Text input (hotkey to toggle). Stream Claude replies to the speech bubble. System prompt: "You are this computer. These are your current vitals: {...}. Speak in first person as the machine. Be brief (1-2 sentences), have personality, and end with a mood tag." Left brain's `user_intent` and `needs_deep_reply` choose between instant canned line, short reply, or full reply.

**Phase 5: Ears (push-to-talk speech to text).**
- Global hotkey (configurable, default something unlikely to collide, e.g. a held key combo). **Mic is open only while the key is held.** Release ends capture and starts transcription. No wake word, no always-listening, no background recording.
- Transcription runs **locally** (faster-whisper or whisper.cpp). Audio is never sent to any API and never written to disk (keep it in memory, discard right after transcription). Only the resulting text goes on to the normal `user_text` path.
- On key press: instant `listening` reflex (mood + visible mic indicator in Godot, so the user always knows when the mic is live). On release: `thinking` until the transcript is ready.
- Load the Whisper model once at startup in a background thread so first use isn't slow. Show a "warming up" state until ready.
- Handle edge cases: very short presses (ignore under ~0.3s), empty or low-confidence transcripts (say nothing or ask the user to repeat, don't send junk to the LLM), key held too long (cap duration), no mic found, mic permission denied (explain how to fix it).
- Config: hotkey, model size, language, max recording seconds, input device, and a master "voice input enabled" toggle.
- Transcript flows through the same left-brain `user_intent` / `needs_deep_reply` path as typed text.

**Phase 6: Awareness.** Active window title, uptime, long-session detection, events (download finished, battery low, app launched/closed). Left brain scores `event_importance` and `should_speak`. Unprompted chatter is rate-limited and respects a mute toggle and "do not disturb while fullscreen."

**Phase 7: Voice out and polish.** TTS with amplitude-based lip-sync, tray icon, right-click menu, memory file (summarized notes persisted between sessions), settings panel for thresholds and brain choice.

---

## Ground rules

- Build one phase at a time. Each must run end to end before moving on. Commit after each phase.
- Research before coding (Step 0). Record what you verified.
- API keys only via environment variables or a gitignored `.env`. Never in code, never committed, never logged.
- All network calls: timeouts, retries with backoff, and clear fallbacks. Core must degrade gracefully (Jev down -> `RuleBrain`; LLM down -> reflex lines only).
- Microphone privacy: push-to-talk only, mic closed otherwise, local transcription only, audio never stored or transmitted, and a clearly visible indicator whenever the mic is open. Do not add wake words or continuous listening unless the user explicitly asks later.
- Never send sensitive data (file contents, full window titles from private browsing, passwords) to any API. Window titles go through a user-configurable allowlist/redaction step before reaching Jev or the LLM. Tell the user what is sent.
- Keep Godot scripts small and dumb. Keep Python modules small and testable.
- Log latencies for left and right brain separately. The whole point is that reflexes feel instant.
- Ask the user when something is ambiguous. Prefer one question at a time.

## Done criteria (v1)

- Avatar floats on desktop, reacts to real vitals within ~1 second.
- Typing triggers an instant `thinking` reaction before any LLM reply.
- Holding the push-to-talk key shows a mic indicator and `listening` mood; releasing it transcribes locally and Core replies. No audio leaves the machine or touches disk.
- Spoken replies stream in, with a mood matching the content.
- Unplugging Jev's key or network leaves a working (rule-based) avatar.
- `RESEARCH_NOTES.md` accurately reflects the docs the build was based on.
