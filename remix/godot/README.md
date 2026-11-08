# Remix (Godot half)

The window IS her screen: a red glyph face on dark navy glass with a light CRT look.
Godot draws and plays audio; the Python brain decides everything (see PROTOCOL.md).

    godot --path .                      # run (start tools/fake_brain.py first to see it act)
    python3 tools/fake_brain.py         # demo brain: cycles poses, performs a line, etc.
    bash tests/run_all.sh godot         # full test suite (headless; e2e uses the fake brain)

Keys (window focused): **Enter** (or `/`) opens the text bar, Enter sends, Esc closes.
Hold **F2** to talk (fallback push-to-talk). Debug: F4/F5 mouth, F6/F7 eyes, F8/F9 pose
shortcut, F10 twitch. Left-drag moves, mouse wheel resizes.

Window: square corners, opaque (no compositor transparency needed). On start she sizes
herself to a quarter of the screen's area (`screen_fraction` 0.5 per axis on `main.gd`),
16:9, on whatever screen she is on, re-fits if the monitor/resolution changes, and the
face scales to fit any window size or shape you drag/tile her into. She is told her
window and screen size (protocol `context`).

## Your art -> her face
`art/faces/*.svg` are your drawings. `python3 tools/svg_to_shapes.py` turns them into
`scripts/face_shapes.gd` (generated; never edit by hand). `python3 tools/verify_shapes.py`
proves the result matches the SVGs (needs `pip install cairosvg pillow numpy`).
Every eye/mouth is a 10-point "ribbon" (point i pairs with 9-i), which is why any pose
can ease into any other. To add a drawing: draw the SVG the same way, add its name to `POSES` in the generator,
then add its eye shape to `EYES` and/or its mouth shape to `MOUTHS` (and a pose pair if you
want a shortcut) in `scripts/face_config.gd`.

## Model
- Two units: EYES (both, together) and MOUTH (one piece), chosen independently: 7 eye
  shapes x 7 mouth shapes (see `face_config.gd`). Each has its own gaze offset, clamped
  by that shape's limits. A pose is just a named pair.
- One face at a time. No blending, no intensity.
- Sound widens the whole mouth, capped per pose.
- The `none` mouth (the Spooked drawing) is hidden unless she's making sound. Tired/Spooked
  eye lines draw on and retract. Spooked eyes crossfade fill <-> outline and tremble.
  Tired eyes + any mouth, Spooked eyes + a smirk, etc. all work.
- Blink (not in Spooked), idle bottom-lid twitch (Anger, Pout), breathing glow, tiny drift.
- Text appears only for `notice` (her caption line), `transcript` (what the mic heard) and
  the input bar.

## Tuning
All in `scripts/face_config.gd` (limits, widen caps, blink, which moods map to which pose)
and the exports on `scripts/face.gd` (spring stiffness/damping, blink timing).
CRT look: uniforms in `shaders/screen_fx.gdshader`.

## Assumptions I made (change if wrong)
- The "BottomEyeLid (for twitch) + SmirkMouth" art is BOTH the `smirk` pose and the twitch
  frame (eyes flick to that shape briefly).
- Spooked while speaking borrows Default's mouth until you draw one.
- Undrawn moods map to the closest drawn pose (face_config.gd `ALIASES`).
- Verified here: shape fidelity vs. your SVGs, the engine, protocol, audio-synced cues,
  real websocket end-to-end, and real renders under software GL. NOT verified: Hyprland
  behavior (see tools/hyprland-setup.md) and real sound hardware.

`legacy/` holds the previous face (generic glyph atlas) for reference; Godot ignores it.
