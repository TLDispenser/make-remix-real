# Remix: Upgrade Plan V2

Prepared for TLDispenser | November 11, 2026 | Based on the repo at commit b8b5346 (HEAD, re-read for this version)

## What changed from V1

V1 treated the program as the thing that makes Remix better. V2 starts from your correction: the project is a vehicle for her brain, and her brain is two AIs working together.

- **New foundation (Part 1): the Brain Contract.** Jev (left brain) makes the decisions. The LLM (right brain) speaks plain words and nothing else. The program is her body and the walls around her.
- **Removed from V1:** the self-authored `self.md` persona, LLM tool calls and file tools, the code runner, any structured or tagged LLM output, the code-forced refusal ledger, the config-table reaction policy with a chance of speaking, and the parsed facts file.
- **Persona is untouched.** Remix is an established character and her prompt is set. That includes the temper drift, which you asked for.
- **Kept:** every verified bug fix, the senses, the platform items, and the four priorities (now rebuilt on the contract).
- **Every decision moved to Jev ships in shadow mode first,** is logged, can be inspected with `/why`, and falls back to today's local behavior.

Items marked (verified) were re-checked against the code while writing V2. Items carried from V1 without a mark were verified or reproduced in V1.

## How to read this

Part 1 is the contract and an audit of where the program still drives her. Part 2 is your four priorities. Part 3 is bugs to fix first. Part 4 is more ideas. Part 5 is the build order. Open questions and draft Jev questions are at the end.

## Canon note: she is a flatscreen

Remix is a modern flatscreen. The CRT look belongs to her companion, which is a CRT. The repo still describes her as the CRT in several places (verified):

- NEW_SPEC.md: line 117 ("Remix keeps the CRT-head idea") and line 123 ("CRT feel via shader").
- remix/README.md line 14 ("CRT shader") and remix/godot/README.md lines 3 and 45 ("a light CRT look", "CRT look: uniforms in shaders/screen_fx.gdshader").
- shaders/screen_fx.gdshader, main.tscn and project.godot also mention CRT.

Suggested fix: reword these to something like "her display, styled after her companion's CRT", and add a short Canon section to NEW_SPEC.md so any model handed the spec does not build a CRT character. The persona prompt in `speech.py` is already safe: she is "the user's laptop" and the prompt only describes her face. Nothing in this plan edits persona text.

## Part 1. The Brain Contract

### 1.1 Who owns what

| Layer | What it is | It owns | It never does |
| --- | --- | --- | --- |
| Jev (left brain, System 1) | A decisions endpoint with three live primitives: `noul` (probability of yes), `choice`, `score` | Stance, faces, whether to speak, in what form (speak, caption, leave a note, stay quiet), user intent, memory relevance, commitments, judging her finished reply | Write words |
| The LLM (right brain, System 2) | Plain-text generation as Remix | Her words, in her voice | Emit JSON, tags, tool calls, field lists or anything a program must parse. Beyond the voice marks her prompt already teaches, no special formatting |
| The program | Her body and the walls | Sensors, TTS, face rendering, storage, search, safety, caps, privacy | Write a line in her voice, roll dice about her feelings, or replace her reply with canned text |
| Autonomic layer (`rules.py`) | Involuntary posing from vitals, no LLM or Jev calls (your credit rule) | The resting face while nothing is happening | Boss her around. It is the default, and her decisions colour it (see 1.7) |

### 1.2 Rules

1. **LLM output is plain speech.** When she is asked for something longer (a diary entry, a reflection, a day summary) it is ordinary prose, stored exactly as written. Nothing is parsed out of it.
2. **No program-written line ever stands in for her words.** Functional UI status ("ears warming up", "no llm key") is status, not her voice, and stays.
3. **Every decision moved to Jev has four things:** a local fallback (the pattern the code already uses), a runtime switch (`off`, `shadow`, `live`), a shadow period where it runs beside the old behavior and only logs, and a log entry.
4. **The walls stay hard:** WebSocket origin check, sandbox path confinement, privacy scrubbing, the mic hard cap, free-providers-only, spend and rate caps, quiet hours, fullscreen do-not-disturb, never speaking over the mic.
5. **Persona text is not changed by this plan.**
6. **No Jev key means today's behavior.** Everything below degrades to the current local logic when `JEV_API_KEY` is empty or the hourly cap is hit.

### 1.3 Audit: where the program still drives her

| # | Finding | Where | Change |
| --- | --- | --- | --- |
| 1 | Her unprompted stance is a dice roll: `rng.choice(STANCE_CHOICES[plan])` (verified). On replies Jev picks it, so the two paths disagree | proactive.py | Jev picks among the same per-trigger options, which stay as the allowed menu |
| 2 | Whether she speaks is a schedule: fixed priority order, per-trigger cooldowns, 20 min gap, 8 per day, 120 s hush, a 3% ambient roll (verified) | proactive.py, config.py | The caps stay as ceilings. Inside them, Jev decides whether and in what form |
| 3 | Her reply can be overwritten: a word list (`UNSEEN`) flags words like file, app and browser, and the reply is replaced by one of four canned `DEFLECTIONS` (verified) | grounding.py | See 1.5 |
| 4 | Pre-written flavour lines: `MOCK_NOTICES` and the strings in `_notice_pool` (verified). This contradicts the "no pre-written pools" rule at the top of proactive.py | rules.py | Remove. Flavour captions come from the LLM through the normal path, or not at all |
| 5 | The program editorializes events: idle_deep tells her "they never said they're ok" (verified) | proactive.py `_situation` | Plain facts only ("idle 45 minutes"). She decides how she feels |
| 6 | A second message sent mid-reply is dropped and the UI says "brain busy (upstream overloaded)" (verified) | speech.py, main.py | Queue it and answer in order |
| 7 | Idle face rolls: 8% pout, 6% tired, 3% smirk on an idle machine | rules.py `_pick` | Stays. It is autonomic fidgeting, not a decision. See 1.7 |
| 8 | Random per-boot drift | speech.py `_DRIFTS` | Stays. You asked for it |

### 1.4 The Jev budget: same number of requests, more questions

Today each exchange costs up to two Jev requests (verified in `main.py`): `react` before the words exist, `act` after. One request can carry many named questions, and `act` already sends two per beat across up to four beats, so adding questions to these two requests costs no new requests.

After V2, an exchange looks like this:

1. Local: FTS5 search over memory (Part 2.1).
2. Jev `react`: stance, eyes, mouth, plus user intent, a commitment check and memory relevance.
3. The LLM writes her words, with the stance and the chosen memory rows in the prompt.
4. Jev `act`: per-beat faces, plus two judgments on the finished reply (does it claim to see something it cannot, did it dodge the question).
5. TTS, then perform.

Costs and limits to be honest about:

- `act` is skipped for one-beat replies today, because there is nothing to vary. The judge questions mean one-beat replies now cost a second request (a small one). Worst case is still two requests per exchange.
- At the hourly cap of 60 and two requests per exchange, that is about 30 exchanges an hour. Peeks keep their 20-call reserve.
- Priority when the cap bites: `react`, then `act` and judges, then proactive decisions, then bits, then ambient (opt-in only).
- The judge adds latency before she speaks. Jev's timeout is 3 seconds. If it times out, the local check decides (fail open).
- Input tokens are the cost. Keep `state` short: the reply, a compact list of what she can sense, and the user line.
- Privacy: sending retrieved memory rows to Jev's API for ranking sends past lines to a third party. Gate it behind `REMIX_MEMORY_TO_JEV`, default off, the same way peeks are opt-in. With it off, ranking is local FTS score only.

### 1.5 Grounding without a script

1. **Keep local what is arithmetic or checkable.** The vitals-number check stays. The past-claims check (`_memory_ok`) stays and is extended to accept anything actually retrieved this turn.
2. **Retire the word list as the gate.** Replace it with a Jev `noul`: does the reply claim to see or know something about the user's computer that the state does not list? Run both for a week in shadow mode, log disagreements, review them, then switch.
3. **Failure handling.** One retry with the problem named in plain prose (`retry_note` already works this way). After that:
   - A soft problem (the judge or the word list): deliver her second try unchanged. Her words, her responsibility, logged.
   - A hard problem (a wrong vitals number): she does not speak that turn. A face reaction only, logged.
   - Never a canned `DEFLECTION`.
4. **Shrink the problem.** Real senses (Part 4: top processes, battery wear, and so on) make "I can't see that" rarer, because she can actually see more.

### 1.6 The decision log and `/why`

- Every Jev decision is logged: timestamp, question key, answer, confidence, whether it was used or fell back, and what the local logic would have done.
- Use a rotating log file. This also fixes the unrotated `brain.log`.
- `/why` prints the last few decisions in plain words, for example "spoke: should_speak 0.81, stance fond" or "stayed quiet: fullscreen".
- Each decision type has a mode: `off`, `shadow`, `live`. Thresholds (for example, speak at 0.6 or above) live in config.

### 1.7 Afterglow (optional)

After a reply, Jev's stance face becomes her settle pose for a decaying window, instead of snapping straight back to the vitals pose. Real alarms (overheat, pegged CPU) still override. No extra calls. This is how her decisions colour the autonomic layer.

## Part 2. Your four priorities

### 2.1 Memory

What I found (carried from V1):

- The journal keeps newest-first rows, capped at about 400 (`MEMORY_MAX_ENTRIES`), each cut to 400 characters. Row kinds: user, her, thought, event.
- What reaches her prompt is a fixed window: the 8 newest rows plus 5 evenly spaced older ones, each trimmed to 160 characters. The choice has nothing to do with what you just said (verified in `_memory_block`).
- Her conversation history is a deque of 8 messages, which is 4 exchanges (verified).
- Using memory depends on the prompt telling her to find the row whose timestamp best matches. Nothing in code retrieves anything.

What to build, in order:

1. **Storage.** Daily journal files plus an index. Cap what is injected, not what is kept. The index is rebuildable, so keep it outside git.
2. **Retrieval.** A local SQLite FTS5 search on the content words of each user line, plus time-matched rows for phrases like "this morning" or "yesterday". These are candidates. If `MEMORY_TO_JEV` is on, Jev ranks them as one `choice` question, with the candidates as the criteria, and the code reads the `probabilities` it already reads for faces. If off, FTS score decides. The winners go into the same plain-prose "WHAT YOU REMEMBER" block that exists today, and the recency window stays as the base. The ranking question rides inside the `react` request, so it costs no extra call.
3. **Longer history.** Raise the 8-message deque and make it configurable (16 to 24 is a sensible start).
4. **Reflections.** At graceful shutdown, or at the next boot if shutdown was too abrupt, the LLM answers one plain question: what did you learn about this person? The answer is stored as written, with no parsing, and retrieval treats it like any other row.
5. **Commitments.** `react` adds a `noul` ("did they say they will do something later, or ask to be reminded?") and a `choice` for when (later today, tomorrow, no time given). It is stored with the row. When it falls due, it becomes a proactive event like any other: Jev decides whether and how, the LLM writes the line. Precise timers belong to the Hands decision in Part 4.
6. **Callbacks.** There is no separate callback engine. A due commitment or a strongly relevant old row is just another proactive event.
7. **Day summaries.** When a day leaves the recent window, the LLM writes one plain paragraph about it in her voice. It is stored and retrievable, so a week-old morning stays reachable.
8. **Honesty guard.** `_memory_ok` accepts anything retrieved this turn and keeps rejecting the rest.
9. **Your controls.** `/memory` shows what she would inject right now. `/forget <topic>` removes matching rows from the files and the index and commits the change. A per-session no-remember toggle.

Done when: you can ask "what did I say I'd do after work on Tuesday?" and she answers from a real row, and a wrong guess still gets a plain "I don't have that page".

### 2.2 The sandbox

What I found (carried from V1):

- The sandbox is meant to be her own git repo (`brain/sandbox/`). Today it holds one file, `memory.json`, and the optional GitHub mirror pushes it. The header of `memory.py` says later wiring can let her write files there too. That wiring does not exist.
- `SANDBOX_DIR` defaults to a folder inside the project tree. A comment in `memory.py` records an earlier accident where git calls ran against the project repo instead of the sandbox.

What to build:

1. **Move `SANDBOX_DIR` outside the project,** for example `~/.local/share/remix/sandbox`. Keep the existing refusal to push unless the sandbox is its own repo.
2. **A layout she can live in:** `diary/` (one file per day), `journal/` (daily rows), `reflections.md`, `summaries/`, `inbox/` (you drop files for her), `outbox/` (notes she leaves for you). The FTS index is untracked.
3. **She has no file tools.** The program writes what she says. This removes the whole class of path-escape and tool-call-format problems, and keeps the LLM producing speech only.
4. **A real diary.** At graceful shutdown, or at the next boot covering the gap, the LLM writes a short plain entry in her own voice. The program saves it and commits it with the entry's first sentence as the commit message, so her day reads in `git log`.
5. **Inbox.** Drop a text file in `inbox/`. The program hands her a capped excerpt as a situation ("they left you a file called X"), Jev decides the form of her reaction, and the LLM writes it. Text only. Note that the excerpt goes to the cloud LLM, so this is only for things you are happy to send.
6. **Outbox.** When Jev's decision for an unprompted line is "leave a note" (you are away or she is out of sight), the line is saved to `outbox/` as plain text. Jev also decides whether it deserves the urgent flag.
7. **Safety pass before any push to GitHub.** Redact emails, URLs, paths and tokens (reuse the awareness scrubber), refuse to sync to a public repo, and keep the design where the token is read at push time and never stored.

Removed from V1: the self-authored `self.md`, the file tools, and the code runner.

Done when: after a day of use the repo holds a readable diary, reflections and a few notes, and opening it on GitHub shows her day.

### 2.3 Bits (she is your laptop, so she notices what you do)

What I found (carried from V1):

- `audio.py` polls `wpctl` and caches a single quiet flag. The flag only decides whether flavour text may show. If you mute her mid-sentence, the WAV keeps playing into a muted sink and her face keeps performing.
- The urgent flag exists end to end (the brain sends it, Godot pulses an orange frame and requests attention every 45 s) but fires only for `muted_long` and `idle_deep`, and ignores where she is.
- She already knows where she is relative to you (awareness reads `hyprctl clients` and monitors). That knowledge is not used to decide when to flag.
- From your own `hyprland-setup.md`: on Wayland, the attention request becomes an xdg-activation request and Hyprland decides what it means. With the default `focus_on_activate = false` she is only marked urgent, and your bar must show it. She is pinned to all workspaces, so a bar that only flags other workspaces may show nothing for her.

What to build:

1. **React to mute and volume live.** Replace polling with events (`pactl subscribe` via pipewire-pulse, or a PipeWire monitor; verify for your setup) so a change is seen within about 100 ms. The reflex is local and instant: send `stop`, let the face finish in silence, show the line as a caption (your on-screen text rule), and give a flat look. Whether a reaction is worth a caption is Jev's call, and the LLM writes it. There is no canned "...rude." On unmute, at most one dry beat, rate limited.
2. **Make "she is out of sight" a first-class state** (local): her workspace is not the active one, or she is covered, or she is not pinned. Combine it with "she has something to say".
3. **Escalate in steps, and start the ladder by decision.** Jev decides whether what she has to say deserves pulling your attention. Then (a) the Godot attention request, (b) a `notify-send` notification with urgency set, (c) only if you opt in, a Hyprland dispatch to bring her forward. Each rung has a cooldown, and everything clears the moment you focus her or reply.
4. **Listen to Hyprland's event socket (socket2)** instead of polling every 5 s. It publishes workspace changes, active window, open and close, fullscreen and urgent events (verify event names against the wiki for your version).
5. **More bits, all local and cheap:** lid close and open, suspend and resume (she should know she was asleep), screen lock, headphones or HDMI plugged in, bluetooth connect, Wi-Fi up and down, brightness change, USB plug, screenshot taken.
6. **The policy is a decision, not a table.** A bit arrives as an event with plain facts. The reflex face is local and instant. Jev decides speak, caption, leave a note, or quiet. The only config left is walls: per-bit cooldown floors, the daily cap, and "needs the room quiet".

Done when: muting her mid-line stops her and she reacts once; with her on another workspace and a reply waiting, your bar glows within a second and clears when you look.

### 2.4 Cooperation

What I found. These are hypotheses ranked by how likely I think they are. Test before assuming.

a. **Length rules starve real help.** The prompt says "One to six words is normal. Two short sentences is a lot", and `beats()` caps replies at 4 beats (verified).
b. **The examples teach refusing.** Small free models copy examples harder than rules. The "REAL LINES" block includes "We're NOT doing this again.", and several examples are reactions rather than answers.
c. **The default temper is cranky.** `REMIX_TEMPER` defaults to `cranky`, which adds no softening text.
d. **Grounding can eat the answer.** The word list flags file, process, browser, download, game and video when you did not say them first. A helpful "close some of your browsers" fails and is replaced by a canned deflection, which is a non-answer.
e. **She drops messages** while a reply is in flight.
f. **Four exchanges of memory.** She loses the task between turns.
g. **She cannot do anything yet.** See Hands in Part 4.
h. **Model size.** A small free model holds a strong persona and a task at the same time poorly.

Hypotheses a to c are about persona text, which is yours. I am proposing no changes there, only that the eval below measures them so you can decide with data.

What to build:

1. **Structural fixes, no persona changes:** queue messages instead of dropping them, longer history (2.1 step 3), grounding that no longer replaces answers (1.5), and one shared limiter for all LLM calls with replies taking priority (Part 3).
2. **Intent from Jev.** The `react` request adds a `user_intent` choice (question, command, chitchat, venting, other). It is logged and used by the dodge judge. Nothing in code forces behavior from it.
3. **Dodge judge.** In `act`, a Jev `noul`: did the reply try to do or answer what was asked? It only matters for question and command intents. A high "dodged" score gets one retry with a plain-prose nudge, and her second try is delivered either way.
4. **Objections are remembered, not ledgered.** Longer history and retrieval carry what she already said, so "object once, then stand down" is something she can see, not a rule code enforces.
5. **The cooperation eval, as a measurement only.** About 40 requests (how-to, lookup, fix this, explain, do a chore, a repeat of an earlier request). Score each reply: did it attempt the task, did the answer come first, how many grumbles, was it a refusal. Run it on every prompt or model change. It never steers her at runtime.
6. **`TEMPER=balanced` for a week as an A/B** is your call.

Done when: on the eval, 90 percent of requests get a real attempt in the first beat.

## Part 3. Fix first (verified)

1. **WebSocket origin (reproduced in V1; code re-checked).** `serve()` in `ws_server.py` passes no `origins` argument and sets `max_size=None`, so a web page in your browser can connect to `ws://127.0.0.1:8765`, open the mic with a `ptt` start message, send `user_text`, and read her transcript. Fix: pass `origins=[None]` (browsers get HTTP 403, Godot sends no Origin header and still connects), and replace `max_size=None` with a real limit.
2. **LLM failover stall (reproduced in V1; code re-checked).** `_call_llm` sleeps after every failed attempt, even when the next step is a different provider, so the backup answers only after 8 to 14 s of pure sleeping. Fix: back off only when retrying the same provider.
3. **Chatter and replies can collide (read, not reproduced).** They use separate locks, and a chatter call that times out keeps running in its worker thread. On a free tier that allows one concurrent request, your reply can get a 429 while she is mid-thought. Fix: one shared limiter for all LLM calls, replies first.
4. **Smaller items:**
   - The "brain busy (upstream overloaded)" notice shows for any `None` reply (verified). Queueing fixes most of it.
   - `brain.log` is never rotated (the decision log in 1.6 uses rotation).
   - `.run/*.pid` and `godot/godot.log` are tracked in git, and so is a file named `-` at the repo root (verified). Add them to `.gitignore` and remove them from the index.
   - `CORE_SPEC.md` is marked superseded; consider moving it to a `docs/history` folder.

## Part 4. More ideas

**Senses**

- **Real senses.** Top processes, swap, network and disk I/O (psutil), whitelisted in the grounding facts. "Why is my PC slow?" can then be answered truthfully: "firefox, 71%."
- **Body truths.** Battery wear and cycle count, SMART health, and kernel errors from `journalctl`, so "old as hell" is literally true.
- **Shell hook.** A tiny zsh or fish hook sends the command name and exit code over a unix socket. She reacts to failed builds and finished long jobs. Plain facts to Jev and the LLM, like any other event.
- **Media and notifications.** `playerctl` gives her the current track; a dbus listener gives notification counts, not contents.
- **Look at my screen, on request only.** Local OCR (tesseract) on a screenshot. Nothing leaves the machine unless you opt in.

**Hands (deferred, reshaped)**

No LLM tool calls. If you want her to do things (timers, launching apps, volume), Jev's intent `choice` selects from a small closed list, tiny local parsers extract parameters ("10 minutes"), the program executes, and the LLM speaks about it in plain words. Anything destructive needs an explicit typed or spoken yes. Jev has no extraction primitive, which is why the parsing is local. Decide this after Phases 2 and 3.

**Speed and reliability**

- **Cut latency.** There is a tension to resolve: streaming the LLM and speaking sentence one early conflicts with `act`, which needs finished words to choose per-beat faces. A workable approach (to verify): speak sentence one with the face from `react` while `act` runs for the rest. Show the thunk face instead of "one sec" while waiting.
- **Fix idle detection.** Keyboard-only typing reads as idle today. Use `ext-idle-notify` via swayidle or hypridle, as your own notes suggest.
- **Local LLM fallback** (Ollama or llama.cpp) as an offline backup. The code is already OpenAI-compatible, so this is mostly a config preset. On an i5-4310M it needs a very small model, so treat it as a fallback.

**Voice and face**

- MBROLA voices through espeak-ng (still non-neural, so your voice constraint holds).
- Non-verbal sounds: a wake-up thunk, small blips for quiet mode.
- Barge-in: the protocol already has `stop`, so she can go quiet when you start typing or hold the key.
- A scribble "no signal" glitch when the brain disconnects, and a dim or sleep state at deep idle.
- Finish the open Phase 7 items: tray icon and a settings panel; let temperature tint the face colour, as the original spec wanted.

**Plumbing**

- GitHub Actions CI running the unittest suite and the Godot headless tests.
- systemd user units (or an AUR package) in place of `restart.sh`, plus a `pyproject.toml`.
- A persona regression harness of about 50 scripted prompts, checked for length, banned phrases, stage directions and grounding, so a model or provider swap cannot silently break her voice. The cooperation eval can live here. It is a measurement, never a runtime governor.
- Abstract the awareness layer if you ever want to share her. It is currently tied to Hyprland, `wpctl` and `pacman`.

## Part 5. Suggested order

| Phase | Focus | Contents |
| --- | --- | --- |
| 1 | Safety and stability | Origin check, failover fix, shared LLM limiter and message queue, repo hygiene, move the sandbox out of the tree |
| 2 | The seam | Decision log and `/why`, off/shadow/live switches, proactive stance from Jev, should-speak and form decisions, remove canned lines, plain event text, grounding judge in shadow |
| 3 | Cooperation and memory core | Longer history, dodge judge, FTS5 retrieval, reflections, commitments, `/memory` and `/forget`, cooperation eval |
| 4 | Bits | Live mute reaction, out-of-sight state, escalation ladder, Hyprland socket, more bits |
| 5 | The sandbox as a home | Diary, inbox and outbox, day summaries, push safety |
| 6 | Senses, then the Hands decision | Top processes, body truths, shell hook |
| 7 | Polish | Latency, voice and face extras, CI and packaging |

Why this order: the security fix is one line and the mic is involved, so it goes first. Phase 2 builds the log, the switches and the shadow mode that every later Jev decision depends on, and it is where you find out whether Jev's judgments are good enough to take over from the old logic. Cooperation and memory change how every other feature feels, so they come before new capabilities.

## Open questions

1. Does the CRT companion ever appear on screen or in sound? If so, scanline flicker and static bursts could become the companion bleeding through, but nothing here depends on it.
2. Is she pinned to all workspaces right now, and which bar do you use (waybar?) for showing urgent state?
3. Are you comfortable with the free LLM writing her diary and reflections, given the content goes to the provider? The alternative is a local-only, rule-based version.
4. Are you comfortable with memory rows going to Jev's API for ranking (`REMIX_MEMORY_TO_JEV`)? The default in this plan is no.
5. On a hard grounding failure (a wrong vitals number after one retry), should she stay silent that turn, or deliver her second try anyway?
6. Ambient chatter and idle posing: keep them local-only (default here), or allow an opt-in slow Jev heartbeat under a credit reserve?
7. Jev limits I could not verify from the repo: is there a maximum number of questions or a state size limit per request, and what hourly cap are you comfortable spending?
8. Hands: which tools first (timers, launching apps, notes), or defer entirely?
9. Afterglow (1.7): on or off?

## Appendix A. Draft Jev questions

Wordings are drafts to be tuned during shadow mode. Shapes follow the Jev API notes: `noul` returns a probability, `choice` takes named criteria and returns probabilities.

**Added to `react` (before her words exist)**

- `intent` (choice): question / command / chitchat / venting / other.
- `commitment` (noul): "Did the user say they will do something later, or ask to be reminded?"
- `when` (choice): later_today / tomorrow / no_time_given.
- `memory` (choice, only if `MEMORY_TO_JEV`): the FTS candidates as criteria.

**Added to `act` (after her words exist)**

- `unseen` (noul): "Does the reply claim to see or know something about the user's computer, files or screen that the state does not list?" State: the reply plus a compact list of what she can sense.
- `dodged` (noul): "Did the reply fail to try to answer or do what the user asked?" Only read for question and command intents.

**For proactive events and bits**

- `form` (choice): speak / caption / leave_note / quiet.
- `stance` (choice): the per-trigger menu already in `STANCE_CHOICES`.
- `urgent` (noul): "Does what she has to say deserve pulling the user's attention while they are away or she is out of sight?"
