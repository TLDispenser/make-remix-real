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
| `pose` | `pose` and/or `eyes` / `mouth`, optional `look: [x,y]` | set the face. `pose` = a shortcut that sets BOTH units; `eyes` / `mouth` set one unit each and override `pose`; an omitted unit stays as it is; unknown names are ignored. Gaze: `look` if given, else kept when the eyes don't change, re-centered when they do |
| `look` | `look: [x,y]` (-1..1) | move gaze without changing pose |
| `perform` | `id`, `audio` (absolute path to PCM WAV), `cues[]`, optional `then` | speak with acting (below) |
| `stop` | | cancel the running performance (reports `ok:false`) |
| `mouth` | `level` 0..1 | drive the mouth yourself (streaming TTS you measure); held for 0.25 s |
| `twitch` | | one bottom-lid twitch now, on ONE eye (random side) |
| `listening` | `state: "start"/"stop"` | mic-live marker (pale blue, top-right) while push-to-talk is held |
| `notice` | `text`, optional `seconds` (4) | her one caption/status line (word-wrapped; muted, volume low, no-voice captions, mic problems) |
| `urgent` | `state: true/false`, optional `reason` | she needs attention (muted/ignored too long; she said a line first). Godot shows an orange pulsing frame on her window AND asks the OS for the real taskbar flash (`DisplayServer.window_request_attention`, re-asked every 45 s while still urgent, skipped while she is focused). The OS clears it when you focus her; ignored by older face builds |
| `transcript` | `text`, optional `seconds` (4) | what the mic heard, on a dim line at the top (`you: ...`) |
| `theme` | `color: "#rrggbb"` | face color |
| `vitals`, `event`, `say` | any | passed through as signals; unused by the face (`say` carries her reply text for terminals/logs) |
| `mood` | `mood`, optional `look` | legacy; mapped to the closest pose (`scripts/face_config.gd`) |

`perform`:

```json
{"v":2,"type":"perform","id":"p17","audio":"/tmp/remix/p17.wav",
 "cues":[{"t":0.0,"pose":"anger","look":[0,0]},
         {"t":1.8,"pose":"tired","look":[0.5,0.3]},
         {"t":3.1,"twitch":true}],
 "then":{"pose":"default"}}
```

- `t` is seconds into the audio. A cue may carry `pose`, `eyes`, `mouth`, `look`, or `twitch`
  (`then` takes the same fields). Per-unit cues are how one line gets e.g.
  `{"t":0,"eyes":"really","mouth":"smirk"}` then `{"t":1.4,"mouth":"pout"}`.
- A new `perform` cancels the running one (you get `perform_done` ok=false for it).
- Missing/unreadable audio: all cues apply at once, `then` applies, and you get
  `perform_done` with `ok:false, error:"audio"`. (Use this for muted/no-voice lines.)
- Audio must be PCM WAV, 8 or 16-bit, mono or stereo (what Piper writes).

## Godot -> Brain

| type | fields | when |
|---|---|---|
| `ready` | `poses[]`, `eyes[]`, `mouths[]`, `moods[]` | every time the socket opens; `eyes` / `mouths` are Jev's option lists |
| `context` | `window [w,h]`, `screen [w,h]`, `pos [x,y]`, `screen_index`, `screen_count`, `area_fraction`, `face_scale`, `dpi_scale`, `focused` | on connect, and (debounced) whenever the window or screen changes — including when she is MOVED (2 s move watch); `pos` is her position within the screen she's on; the brain turns it into placement words for her, and `REMIX_WINDOW_POS=0` ignores it |
| `perform_done` | `id`, `ok`, optional `error` | performance ended (finished, cancelled, or failed) |
| `user_text` | `text` | a line typed into the on-screen text bar |
| `user_typing` | | you are typing in the bar (throttled): the brain answers with an instant "thinking" look |
| `ptt` | `state: "start"/"stop"` | focused-window fallback push-to-talk (hold **F2**); the brain does the recording |

## Eyes, mouths, poses and limits

Eyes and mouth are two independent units. Your 7 drawings give 7 eye shapes
(`default anger bottomlid really spooked tired tsun`) and 7 mouth shapes
(`default anger smirk pout trauma tsun none`), so any of 49 faces can be sent.
(`"Tired/Trauma"` = tired eyes + trauma mouth, `"Really?"/Pout` = really eyes + pout
mouth, `BottomEyeLid + SmirkMouth` = bottomlid eyes + smirk mouth.) A **pose** is a
named eyes+mouth pair (the original 7: default, anger, smirk, pout, spooked, tired,
tsun) and mood words map to one (happy -> smirk, bored -> pout, ...; a few map to
mixed faces: deadpan, skeptical, sheepish, numb). `none` = no mouth unless she makes
sound. Gaze limits (eye range x `GAZE_GAIN`), mouth-widening caps, blink rules and trembling live in
`scripts/face_config.gd`, per eye shape and per mouth shape. The brain never sends
coordinates; it picks eyes, a mouth and a look and the shapes decide how far things move.
