# REMIX — a PC-as-avatar with a two-brain architecture

Godot window = **body** (display/input only). Python process = **brain**
(left brain: instant typed decisions; right brain: an $0 LLM writes the words).
See `../NEW_SPEC.md` for the why, `../RESEARCH_NOTES.md` for everything
verified against live docs, and the user's constraints locked in on 2026-11-05:
**free LLM only · Godot visuals authored by the user · TTS non-AI (espeak-ng).**

## Status

| Phase | State |
|---|---|
| 0 Research | ✅ done — see `../RESEARCH_NOTES.md` |
| 1 The body | ✅ **user-built**: SVG-driven ribbon face, CRT shader, protocol v2 (see `godot/README.md`, `godot/PROTOCOL.md`) |
| 2 Nervous system (real vitals) | ✅ done — psutil feed verified on her host (`REMIX_MOCK=0` is now the default) |
| 3 Left brain | ✅ RuleBrain ($0 vitals→poses) live • ✅ JevBrain live (typed input only, 1 call/reaction: stance + pose + beat arc; budget policy below; smoke-tested 2026-11-06) |
| 4 Right brain (LLM words) | ✅ live; now **grounded** (she only knows her vitals/hardware/window/time/this chat; invented tabs/windows/memories/vitals are screened) |
| 5 Push-to-talk (local STT) | ✅ done — faster-whisper, hold-to-talk, hard 15 s mic cap, see "Voice" below |
| 6 Awareness | ✅ done — Hyprland app sensing (names only, titles redacted), idle/power/audio senses, unprompted lines from local text pools (DND + rate limits) |
| 7 Voice out + polish | ✅ voice done: **espeak-ng** (not an AI voice), lip-sync from the audio, per-beat faces timed to it. Tray/settings still open |
| 8 Criticism round (2026-11-08) | ✅ mostly done — persistent **memory sandbox** (`brain/sandbox/`, optional GitHub mirror), time sense (uptime/awaken/dated journal), temper knob + slight random personality drift, softer sides (dry ≠ cruel), honest stats everywhere, **ambient thoughts**, **urgent marker** (6.4), motion boost + pacing, per-beat espeak prosody. Godot-side single-eye twitch + urgent (OS taskbar attention request + orange frame) now applied (2026-11-08c, see `godot/PROPOSED_face_edits.md`) |

Loose-ends pass (2026-11-08): `CORE_*` settings renamed to `REMIX_*` (old names
still read — see `.env.example`), on-screen text rule enforced (flavor notices
only while the machine is muted/quiet — `audio.py`), Jev stance question given
explicit concerned/fond triggers + a calibration harness (`brain/tools/stance_probe.py`),
Godot lipsync tests rewritten for the ribbon mouth (`talk_test.gd` added).

## Phase 6: awareness (2026-11-08)

She can now sense a little of the world outside her own vitals — `brain/awareness.py`
polls every `REMIX_AWARENESS_INTERVAL_S` (5 s) and never crashes on a missing sensor:

| sense | how | what she gets |
|---|---|---|
| focused app | `hyprctl activewindow -j` | the app **class name only** (`firefox`), + fullscreen flag |
| user idle | `hyprctl cursorpos` + app-switch watching | seconds since the cursor or focused app last changed |
| AC power | `/sys/class/power_supply` | plugged / on battery |
| audio | `wpctl` (shared with the notice gate) | muted or very low |
| session age | process uptime | for long-session nudges |

Idle is a cursor/app heuristic: a keyboard-only stretch with no window change reads
as idle (documented limit; `ext-idle-notify-v1` via hypridle is the future upgrade).

**Privacy (spec, enforced in code, tested):** window **titles never leave
`awareness.py` raw**. By default ONLY the app class passes to Jev/the LLM. List
app classes in `REMIX_TITLE_ALLOW` (e.g. `mpv,spotify`) to let their titles
through — and even those get scrubbed of emails, URLs, IPs and file paths first.
Grounding (`grounding.py`) also widens her "what you can see" list by exactly one
line: the focused app's NAME. She still cannot read the screen or claim tabs.

**Opt-in everything** (`REMIX_TITLES=scrub|raw`): `scrub` passes every
focused-window title through the scrubber; `raw` passes them untouched
(200-char cap). Know what that means: titles ride inside the prompts to the
CLOUD LLM (z.ai free endpoint) and she may read them aloud or caption them —
raw titles often carry tokens, which is why scrub is the recommended opt-in.
Grounding widens to match: she can quote a visible title without the screen
flagging it as invention. Default stays `off`; `/aware` shows what she sees.

**Surroundings (2026-11-08c) — what's AROUND her.** One extra `hyprctl clients -j`
per poll (+ `monitors -j`, cached 30 s) gives her every window's app **class** and
rectangle. She finds her own window (class or title matches `REMIX_SELF_MATCH`,
default `remix`) and works out where everything else sits *relative to her*:

```
firefox (focused): to my left, close, huge
spotify: right behind me (I'm sitting on top of it), small
kitty: above me, close, mid-sized
discord: on workspace 3 (out of sight)
```

That block goes into her "WHAT YOU CAN SEE" prompt, and the grounding screen allows
exactly those app names (and "browser"/"terminal"/... for apps that are really
there) — anything not listed is still flagged as invention. Other workspaces and
other monitors are named but never pointed at (no position). It is names +
geometry only: no contents, and titles only through the same `REMIX_TITLES` /
`REMIX_TITLE_ALLOW` gate as before, applied per window. `REMIX_SURROUNDINGS=0`
turns the whole thing off; `REMIX_SURROUNDINGS_MAX` (8) caps the list.
`/aware` prints it.

**Gesturing (fixed).** Two bugs, both mine: (1) the gaze was measured from the
*screen center*, as if she filled the screen — she's a floating quarter-screen
window, so it's now measured from *her own window* to the target; (2) the gaze
went out as a single `pose` message that the spoken `perform`'s per-beat cues
overwrote the moment her voice started. Now every beat carries its own look: if a
beat names a window she can see ("firefox", "that terminal"), her eyes turn toward
it for that beat; otherwise the usual ambient drift. It also works for any visible
window the human names, not just the focused one. Limit: she only has eyes, so
this is a gaze direction, not a pointing arm. Her eyes now travel `GAZE_GAIN` (1.6) x each shape's range
(`face_config.gd`) so an aimed gaze actually reads; set it to 1.0 for the old
subtlety.

**Unprompted lines** (`brain/proactive.py` + `speech.generate_proactive_line`,
the "random speech triggers"): the **trigger is a local reflex** — $0, no
network, never Jev — that decides *whether* she has reason to speak and *what
she noticed*. The **words are written fresh, in the moment**, by the same $0
provider pool that writes her replies (same persona, same grounding screen,
deduped against things she already said). No pre-written pools: if generation
fails she stays quiet — "saying nothing beats filler" is her own rule. Triggers,
most urgent first (stance is picked locally to fit what she noticed):

| trigger | fires when | stance |
|---|---|---|
| greeting | once per boot, after the face connects | playful/tsun/fond |
| battery_low | crosses `REMIX_BATT_LOW` (15%) while unplugged | concerned/annoyed |
| plugged | AC plugged in (edge) | tsun/playful |
| welcome_back | you return after `REMIX_AWAY_S` (30 min) away | playful/flat/fond |
| idle_deep | idle passes `REMIX_IDLE_DEEP_S` (45 min) | concerned |
| idle_gentle | idle passes `REMIX_IDLE_GENTLE_S` (15 min) | playful/pout/flat |
| muted_long | audio quiet for `REMIX_MUTED_NAG_S` (10 min) | pout/tsun/flat |
| long_session | `REMIX_SESSION_LONG_S` (3 h) with no real break | concerned/flat |
| app_stuck | same app for `REMIX_APP_STUCK_S` (90 min) | flat/playful/pout |

A line only fires if ALL hold: she's not mid-speech, mic closed, no exchange in
the last 2 min, not fullscreen (**DND while fullscreen is spec** — poses still
show, words don't), not quiet hours (`REMIX_QUIET_HOURS="1-8"`), ≥20 min since
her last unprompted line, per-trigger cooldowns, and ≤`REMIX_PROACTIVE_MAX_PER_DAY`
(8). Edges fire once per crossing — a fullscreen movie eats the event, she
doesn't queue nagging. The words may take a moment to write (`REMIX_CHATTER_TIMEOUT_S`,
25 s); before delivering she re-checks the room (mic, speech gate, you started
typing, fullscreen) and drops the line rather than talk over you. Spoken
through the same pipeline as replies (espeak, per-beat faces, one `perform`);
captions only per the on-screen text rule.

**Typed commands** (never sent to any LLM): `/poke` (say something now),
`/aware` (print the redacted snapshot — verify redaction live), `/quiet`
(toggle unprompted chatter at runtime).

## Memory & time (Nov-8 round)

`brain/memory.py` keeps a dated journal in `remix/brain/sandbox/` (her repo):
what was said both ways, her unprompted thoughts, her recent faces, the
chatter-dedup set and the last shutdown timestamp. That single file is what
gives her a past — the system prompt now carries a **WHAT YOU REMEMBER**
block straight from it, the grounding rules allow past-talk exactly as far
back as the journal reaches (and no further), and a boot after a real gap
turns the greeting into an *awaken* line ("we were apart for about X").

Optional mirror to GitHub (her request 3 — put whatever she wants out there,
space becomes a non-issue):

```ini
REMIX_MEMORY_REPO=https://github.com/you/remix-sandbox.git   # PRIVATE
REMIX_MEMORY_TOKEN=          # fine-grained PAT, contents read/write
```

The token is read at push time and never stored in `.git/config`. Journal is
bounded (~400 rows), so even without the mirror it's kilobytes, not megabytes.

## The face (user's Godot work)

The Godot half was rebuilt by the user 2026-11-06: faces are authored as SVGs
(`godot/art/faces/*.svg -> scripts/face_shapes.gd` via `godot/tools/svg_to_shapes.py`),
every feature is a 10-point ribbon, poses carry per-pose motion limits, and the
playback contract is audio-clock-synced `perform` cues. **Read** `godot/README.md`
and `godot/PROTOCOL.md` — the protocol lives THERE (single source of truth).

## Voice (ears + mouth)

**She talks:** install `espeak-ng` (`pacman -S espeak-ng`). Each reply is synthesized per
beat, joined into one WAV, and sent as one `perform`; Godot fires every face change from
the audio clock and the mouth follows the audio level. No espeak-ng = no voice, and she
falls back to captions on her face. Voice knobs: `REMIX_TTS_VOICE/SPEED/PITCH` (see
`.env.example`). Captions are off while she has a voice (`REMIX_SUBTITLES=always` to keep them).

**On-screen text rule (2026-11-08):** her rare flavor notices ("disk's 99% full",
fan grumbles) only appear while the machine's audio is **muted or low** — text is
the wrong channel when her voice would be audible anyway. Detection is `wpctl`
(PipeWire; `REMIX_QUIET_VOLUME`, default 0.15 = "low"); if wpctl can't answer,
notices show rather than disappear. Functional lines are exempt on purpose: the
mic transcript (you always get to check what she heard — privacy), "one sec", and
ear-problem notices. `REMIX_NOTICE_GATE=0` restores always-show.

**She listens (push-to-talk, local):** needs a recorder (`parec` from pipewire-pulse,
else `pw-record`, else `arecord`) and `faster-whisper` (already in the venv). The first run
downloads the whisper model once (`tiny.en`); then nothing leaves the machine.
Three ways to hold-to-talk:
1. **Hotkey (global):** bind press+release in Hyprland to `ptt.sh start` / `ptt.sh stop`
   (`bind` + `bindr`, see `godot/tools/hyprland-setup.md`).
2. **Her window:** focus it and hold **F2**.
3. **Terminal:** type `/talk` in `./talk.sh`, speak, press Enter.
The mic is only open while held; the brain also closes it after 15 s no matter what; audio is
never written to disk. What it heard shows on a dim line at the top of her screen. Very
short presses, silence and whisper's silence-hallucinations ("Thank you.") are discarded.

**You type:** focus her window, press **Enter** (or `/`), type, Enter sends, Esc closes.


**Expressive voice (2026-11-08d).** Still espeak-ng + plain arithmetic, **no AI
voice**. Measured on espeak 1.51: speed and pitch only *shift* a voice (pitch
spread stayed ~35 Hz whatever was set); what makes it sound flat or alive is
**intonation range** (SSML `<prosody range>`: 0 % = dead monotone, 100 % = default,
200 % = animated). That was the missing lever. Now each beat's voice follows the face
she's making for it (`tts.py` `_EYE_VOICE` / `_MOUTH_VOICE`, edit freely):

| face (eyes) | what it sounds like |
|---|---|
| default | unchanged |
| anger | faster, lower, louder, livelier |
| bottomlid (smug) | slower, a bit higher, sing-song |
| really (deadpan; + pout mouth = sulky) | slow, low, nearly monotone |
| spooked | fastest, high, wide, with a ~7 Hz tremble |
| tired | slow, low, quiet, muffled, longer pauses |
| tsun (flustered) | fast, high, wide, first word sometimes repeats (`i-it's`) |

Also: `*word*` and ALL-CAPS get emphasis; `...` is a real pause and ` - ` a short one;
a whole beat in `(parentheses)` is **whispered**; `*sigh*` / "sigh" is a synthesized
breath (filtered noise) instead of the word; `!` pushes, `?` lifts. And when the real
machine is strained (temp 70→90 °C or CPU 85→100 %, from the same vitals as her
face) her voice gets glitchy: nothing below ~35 % strain. The DSP lives in
`brain/vocal_fx.py` (tremble / muffle / crush / sigh; pure numpy, same length so
perform cues stay valid). The voice is synthesized *after* the acting plan now
(~45 ms for a 4-beat reply, so no visible latency). Knobs: `REMIX_TTS_EXPRESSIVE=0`
restores the exact old voice; `REMIX_TTS_EXPRESSIVENESS` (1.0; 0.5 = half as
dramatic, 2.0 = double); `REMIX_TTS_DSP=0` espeak-only; `REMIX_TTS_STRAIN=0`.
The LLM isn't told about `*word*`/`(aside)`/`*sigh*` yet: she'll only use them if her
persona prompt invites it (one line in `speech.py`'s system prompt would).

## Run it

```bash
# Easiest — chat with her right in a terminal (starts the brain if needed):
./talk.sh
#    Type; Enter to send; "exit" or Ctrl-C to stop (brain keeps running).
#    Her words show here AND on her face if she's displayed.
# One-liner instead of a REPL:
./talk.sh "hey there"

#    /talk in the REPL = push-to-talk without any hotkey setup.
# Manual two-terminal run (if you'd rather start pieces yourself):
#   remix/brain/run.sh      -> "ws server on ws://127.0.0.1:8765 (v2)"
#   godot --path remix/godot   # or open remix/godot/project.godot in the editor
```

First-run checklist (Hyprland):
- `hyprctl clients | grep -iA3 remix` → find the avatar's window **title** (`Remix`)
- pin her above everything (Hyprland 0.53+ rule syntax, verified against the wiki):

```ini
windowrule = match:title ^Remix$, float on, pin on, no_initial_focus on
```

  (Godot's `always_on_top` is X11-only on Wayland — verified in docs. Older
  Hyprland used `windowrulev2 = pin on, title:^(Remix)$` — deprecated.)
- She is a plain opaque square-cornered window now; no transparency needed. She sizes
  herself to a quarter of whatever screen she's on and tells Remix the size.
- Click-through: NOT available under Wayland (Godot implements
  mouse-passthrough for X11 only). Options: XWayland run
  (`env -u WAYLAND_DISPLAY godot ...` gets passthrough + always-on-top), or
  keep the window tight around the avatar. Tell me which you prefer.

## Her face is eyes + mouth, mixed

Your drawings are 7 eye shapes and 7 mouth shapes (the slash in a file title is the split),
so Jev picks the eyes and the mouth separately: 49 faces, not 7. Jev's answer is *sampled*
from its own probabilities with a penalty on recent faces, a second call after her words
exist gives each beat its own face, and the no-Jev fallback guesses a face from the words.
Costs: 1 Jev call per exchange, +1 when her line has 2+ beats; both count toward
`REMIX_JEV_MAX_PER_HOUR`.

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

- `REMIX_JEV_COOLDOWN_S` (3) — minimum seconds between any two Jev calls
- `REMIX_JEV_MAX_PER_HOUR` (60) — hard hourly cap; over cap -> RuleBrain answer, silently
- `REMIX_JEV_CACHE_TTL_S` (300) — identical state signature = cached decision, no call
- One Jev call carries BOTH the reaction stance AND the per-beat acting plan
  (pose list per beat) — no per-beat calls.

## Keys & env

Copy `remix/.env.example` -> `remix/.env` and fill:
- `JEV_API_KEY` — from https://console.typesafe.ai/keys (left brain; optional,
  `RuleBrain` runs without it)
- `REMIX_LLM_API_KEY` — **free** key from z.ai (right brain, default
  `glm-4.7-flash`, $0; Groq/Gemini/OpenRouter drop-in via two lines)

Getting the GLM key (free, no card): 1) make an account at z.ai; 2) profile
menu -> **API Keys** -> Create a new API key; 3) paste it in `remix/.env`
above. The free flash model allows one concurrent request (config already
respects that).

**Provider failover:** `speech.py` rotates a pool of providers if the
primary proves stuck (z.ai's free tier intermittently sheds load). Add
backups with `REMIX_LLM_BACKUP1_URL/_MODEL/_API_KEY` in `.env` — AI Studio's
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
  -H "Authorization: Bearer $REMIX_LLM_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"glm-4.7-flash","messages":[{"role":"user","content":"Say hello in one sentence."}]}'
```

## Privacy rules baked in (non-negotiable, spec)

- Push-to-talk only; mic closed otherwise; transcription fully local
  (whisper.cpp/faster-whisper, base/tiny model on CPU — no GPU); audio
  never written or uploaded; always-visible mic indicator.
- Window titles never leave `brain/awareness.py` raw: only the focused app's
  NAME passes by default; `REMIX_TITLE_ALLOW` opt-ins a class's titles, which
  are still scrubbed of emails/URLs/IPs/paths before Jev or the LLM sees them.
- Keys in `.env` (gitignored) only — never code, logs, or version control.
