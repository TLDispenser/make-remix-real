# APPLIED 2026-11-08 — both edits are in the Godot code (face.gd `twitch(side)`;
# for (2) the text badge below was REPLACED by what was actually asked for: an
# OS taskbar attention request + an orange frame, see main.gd `_set_urgent`), with tests in
# engine_test.gd / protocol_test.gd. Kept for the reasoning. One correction to
# the "overlap" note in (1): both eyes shift together as ONE unit, so their gap
# never changes and a bigger gaze can't make them cross; the only ceiling is the
# canvas. See FaceConfig.GAZE_GAIN.

# PROPOSED face edits — Nov 8 criticism round (Godot side is YOURS; the brain
# side of this round is already in and running. These are ready-to-paste
# snippets for two things you asked for. Apply or ignore.)

## 1) Single-eye twitch (you: "you only twitch one, no?")

`scripts/face.gd` — the twitch currently swaps BOTH eyes to the
`TWITCH_EYES` shape (`_retarget`, the `for key in EYE_KEYS` loop). Make it
one eye, side picked at random per twitch:

```gdscript
# near the other twitch vars (~line 75):
var _twitch_side := 0        # -1 left, +1 right, 0 = both (posed shapes)

func twitch() -> void:       # existing func, add the line
	_twitch_side = [-1, 1][randi() % 2]   # one eye, random side
	_twitch_left = twitch_seconds
```

and in `_retarget`, replace the two-line `var eye_src` + eye loop with:

```gdscript
	var eye_src: Dictionary = _shapes[FaceConfig.EYES[FaceConfig.TWITCH_EYES]["src"]] \
			if _twitch_left > 0.0 else eye_shape
	var twitch_key := "left_eye" if _twitch_side < 0 else "right_eye" \
			if _twitch_side > 0 else ""    # "" = both eyes (real expression)
	for key in EYE_KEYS:
		# standalone shape (e.g. the brain sets "bottomlid" eyes) twitches BOTH;
		# a timed twitch() flick is ONE eye. Same shape, different scope.
		var src: Dictionary = eye_src \
				if (_twitch_left > 0.0 and (key == twitch_key or twitch_key == "")) \
				else eye_shape if key == "left_eye" else _shapes[
					FaceConfig.EYES[_ecfg()["src"]]["src"]]
		_tgt[key] = PackedVector2Array(src[key])
```

(If GDScript hates the nested ternary, split it into an `if/else` — the
logic is: `key == twitch_key or twitch_key == ""` when twitching, else that
eye's normal shape.)

**Constraint note (separation/overlap):** both eyes draw from the same
`_shapes` tables, so a one-eye twitch cannot drift the eyes apart — geometry
source stays shared. The real overlap risk is look-translation: gaze limits
per shape already live in `face_config.gd` (`EYES[..]["look_gain"]` or
equivalent — clamp both eyes' shared offset so one eye can never cross the
bridge: keep `|offset| <= eye_gap * 0.5`).

## 2) Urgent marker (brain now sends `{"type":"urgent","state":true|false}`)

Unknown types already fall into `_ -> pass`, so this is purely additive.

`scripts/main.gd` — in the big `match kind:`, add above `"notice":`:

```gdscript
		"urgent":
			hud.set_urgent(bool(d.get("state", false)), String(d.get("reason", "")))
```

`scripts/hud.gd` — quick pulse badge in the top-right (the mic-marker rail):

```gdscript
var _urgent: Label
var _urgent_t := 0.0

# in _setup (next to _notice), create it:
	_urgent = Label.new()
	_urgent.add_theme_font_override("font", font)
	_urgent.add_theme_font_size_override("font_size", 26)
	_urgent.add_theme_color_override("font_color", Color(1, 0.35, 0.25))
	_urgent.text = "/!\\ muted"        # set per-reason in setter
	_urgent.visible = false
	add_child(_urgent)

func set_urgent(on: bool, reason: String = "") -> void:
	_urgent.text = "/!\\ " + (reason if reason != "" else "attention")
	_urgent.visible = on
	_urgent_t = 0.0

# in _process(_):
	if _urgent.visible:
		_urgent_t += delta
		_urgent.modulate.a = 0.55 + 0.45 * sin(_urgent_t * 6.0)   # pulse
```

Reason strings the brain sends today: `"muted_long"` and `"idle_deep"` —
map them to words if you like (`muted_long -> "muted"`, `idle_deep ->
"ignored"`) in the setter.
