# Remix protocol v2 (Python brain <-> Godot face)

Transport: WebSocket, JSON text frames, `ws://localhost:8765` (Python is the server,
Godot the client; Godot reconnects every 2 s). Godot only draws and plays audio;
every decision comes from the brain. Include `"v": 2` in messages you send (Godot
does not require it).

## The performance model (how the two brain halves line up)

1. **Reaction (Jev, instant).** Input or an event arrives; Jev picks a stance/pose
   from the list in `ready.poses`. Send `pose` right away so the face reacts before
   any words exist.
2. **Words (LLM).** Give the LLM the stance; it writes the line (words only, no mood tags).
3. **Acting (Jev, per beat).** Split the line into beats (clauses or sentences). For
   each beat, Jev picks `{pose, look}` from short enumerated lists.
4. **Voice.** Synthesize each beat's audio, concatenate into one WAV, and record where
   each beat starts (cumulative durations). Those are your cue times.
5. **Playback.** Send ONE `perform` message. Godot plays the audio and fires each cue
   when the audio's own playback clock reaches `t`, so face and voice cannot drift.
   The mouth widens from the live audio level.
6. **Silent life.** When she is not speaking, keep sending `pose` / `look` from vitals
   and events (including silent Spooked).

## Brain -> Godot

| type | fields | effect |
|---|---|---|
| `pose` | `pose`, optional `look: [x,y]` | switch pose. `look` absent = mood's default gaze or centered |
| `look` | `look: [x,y]` (-1..1) | move gaze without changing pose |
| `perform` | `id`, `audio` (absolute path to PCM WAV), `cues[]`, optional `then` | speak with acting (below) |
| `stop` | | cancel the running performance (reports `ok:false`) |
| `mouth` | `level` 0..1 | drive the mouth yourself (streaming TTS you measure); held for 0.25 s |
| `twitch` | | one bottom-lid twitch now |
| `listening` | `state: "start"/"stop"` | mic-live marker (pale blue, top-right) while push-to-talk is held |
| `notice` | `text`, optional `seconds` (4) | the ONLY text she ever shows (muted / volume low) |
| `theme` | `color: "#rrggbb"` | face color |
| `vitals`, `event` | any | passed through as signals; unused by the face |
| `mood` | `mood`, optional `look` | legacy; mapped to the closest pose (`scripts/face_config.gd`) |

`perform`:

```json
{"v":2,"type":"perform","id":"p17","audio":"/tmp/remix/p17.wav",
 "cues":[{"t":0.0,"pose":"anger","look":[0,0]},
         {"t":1.8,"pose":"tired","look":[0.5,0.3]},
         {"t":3.1,"twitch":true}],
 "then":{"pose":"default"}}
```

- `t` is seconds into the audio. A cue may carry `pose`, `look`, or `twitch`.
- A new `perform` cancels the running one (you get `perform_done` ok=false for it).
- Missing/unreadable audio: all cues apply at once, `then` applies, and you get
  `perform_done` with `ok:false, error:"audio"`. (Use this for muted/no-voice lines.)
- Audio must be PCM WAV, 8 or 16-bit, mono or stereo (what Piper writes).

## Godot -> Brain

| type | fields | when |
|---|---|---|
| `ready` | `poses[]`, `moods[]` | every time the socket opens; use `poses` as Jev's option list |
| `perform_done` | `id`, `ok`, optional `error` | performance ended (finished, cancelled, or failed) |

## Poses and limits

Poses are whatever `ready.poses` says (today: default, anger, smirk, pout, spooked,
tired, tsun). Moods without art map to the closest pose (happy -> smirk,
stressed/alert -> spooked, sleepy -> tired, bored -> pout, thinking -> default
looking up-left, listening -> default). Gaze limits, mouth-widening caps, blink
rules and the Spooked "no mouth unless sound" rule live in `scripts/face_config.gd`.
The brain never sends coordinates; it picks a pose and a look and the pose decides
how far things may move.
