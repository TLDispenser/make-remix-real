# face.gd: Remix's face engine.
#
# The face is two UNITS: the eyes (both, together) and the mouth (one piece).
# They are chosen INDEPENDENTLY: any eye shape can pair with any mouth shape
# (face_config.gd: 7 eyes x 7 mouths). A "pose" is only a named shortcut for one
# eyes+mouth pair. Each shape (scripts/face_shapes.gd, generated from
# art/faces/*.svg) is a 10-point ribbon polygon. Springs pull every point toward
# the current shapes, so anything eases into anything, and changing your mind
# mid-move stays smooth.
#
# On top of the shapes, additively:
#   look      the brain's gaze (-1..1, -1..1): shifts the eye unit a lot and the
#             mouth unit a little, never beyond that pose's limits (face_config.gd)
#   speech    audio level OPENS the whole mouth: every rung of the mouth ribbon
#             thickens by the same factor about its own midpoint, up to the mouth's
#             `open` multiplier (face_config.gd). Same spine, same length, fatter.
#   lipsync   while she talks the mouth becomes a straight bar (face_config.gd
#             TALK_*) and returns to her expression's mouth after the sound stops.
#   blink     the eye unit squashes to a line (not in every pose)
#   twitch    brief bottom-lid flick (the "bottomlid" eye shape), eye shapes opt in
#   life      breathing glow, tiny gaze drift, tremble (Spooked)
#   props     the PROP UNIT: extra polygons riding with the eye unit
#             (.cls-5 = spins, .cls-4 = static; Thunking's loading spinner),
#             plus per-stroke-endpoint teleport jitter (Scribble's pulsing knot)
#
# Godot draws only. Everything that decides WHICH pose lives in the brain.
extends Node2D

const POINTS := 10
const EYE_KEYS := ["left_eye", "right_eye"]

@export var theme_color := Color("#ff5148")
@export var stiffness := 190.0
@export var damping := 20.0
@export var blink_min_gap := 2.4
@export var blink_max_gap := 7.5
@export var blink_seconds := 0.18
@export var twitch_seconds := 0.14
@export var aa_width := 2.0          # antialiasing outline (design px)
@export var level_attack := 42.0     # mouth opens fast ...
@export var level_release := 14.0    # ... and closes a bit slower
@export var spinner_speed := 300.0   # spinning prop, degrees per second
@export var jitter_min := 0.06       # stroke endpoint rerolls between ...
@export var jitter_max := 0.30       # ... these many seconds

var eyes_name := "default"
var mouth_name := "default"
var pose_name := FaceConfig.DEFAULT_POSE   # shortcut name if eyes+mouth match one, else "eyes/mouth"
var look := Vector2.ZERO

var _shapes: Dictionary = {}

var _cur := {}   # feature -> PackedVector2Array (live geometry)
var _vel := {}
var _tgt := {}

# Scalar springs: name -> [value, velocity, target]
var _sc := {
	"fill": [1.0, 0.0, 1.0],       # eye fill opacity
	"outline": [0.0, 0.0, 0.0],    # eye outline opacity
	"outline_w": [7.0, 0.0, 7.0],  # outline thickness
	"mouth": [1.0, 0.0, 1.0],      # mouth opacity (Spooked hides it when silent)
	"lines": [0.0, 0.0, 0.0],      # extra line strokes opacity
	"props": [0.0, 0.0, 0.0],      # prop polygons opacity (thunk's spinner)
}
var _ln_a := PackedVector2Array()
var _ln_b := PackedVector2Array()
var _ln_va := PackedVector2Array()
var _ln_vb := PackedVector2Array()
var _ln_tgt_a := PackedVector2Array()
var _ln_tgt_b := PackedVector2Array()
var _ln_w := PackedFloat32Array()

# PROP UNIT: geometry rides with the eye unit, but no springs — the spinner is
# rigid, so it rotates directly (a spring would wobble it out of round).
var _pr_base: Array[PackedVector2Array] = []   # authored points, rel design space
var _pr_hub: Array[Vector2] = []               # rotation center (bbox center)
var _pr_spin: Array[bool] = []                 # .cls-5 props rotate, .cls-4 don't
var _spin_a := 0.0                             # spinner angle in degrees
var _jit_r := 0.0                              # stroke teleport radius (0 = settled)
var _jit_o := PackedVector2Array()             # live offset, ONE per stroke endpoint
var _jit_t := PackedFloat32Array()             # seconds until each endpoint rerolls

var _eye_off := Vector2.ZERO
var _eye_off_v := Vector2.ZERO
var _mouth_off := Vector2.ZERO
var _mouth_off_v := Vector2.ZERO

var _t := 0.0
var _blink_in := 3.0
var _blink_p := -1.0
var _twitch_in := 12.0
var _twitch_left := 0.0
var _twitch_side := ""       # which eye is twitching: "left_eye" | "right_eye" (one eye, like a tic)

var _level_raw := 0.0
var level := 0.0           # smoothed 0..1 (drives mouth opening)
var speaking := false
var _talking := false      # mouth is currently the straight talking bar
var _talk_hold := 0.0      # seconds left before it returns to the expression mouth


func _ready() -> void:
	_shapes = FaceShapes.all()
	var d: Dictionary = FaceConfig.resolve(FaceConfig.DEFAULT_POSE)
	eyes_name = d["eyes"]
	mouth_name = d["mouth"]
	var e_src: Dictionary = _shapes[_ecfg()["src"]]
	var m_src: Dictionary = _shapes[_mcfg()["src"]]
	for key in ["left_eye", "right_eye"]:
		_cur[key] = PackedVector2Array(e_src[key])
		_vel[key] = _zeros(POINTS)
		_tgt[key] = PackedVector2Array(e_src[key])
	_cur["mouth"] = PackedVector2Array(m_src["mouth"])
	_vel["mouth"] = _zeros(POINTS)
	_tgt["mouth"] = PackedVector2Array(m_src["mouth"])
	_blink_in = _next_blink_gap()
	_twitch_in = randf_range(10.0, 25.0)


# ------------------------------------------------------------------ public API

## Named shortcuts (your original 7 drawings).
func poses() -> Array:
	return FaceConfig.POSES.keys()


func eyes_list() -> Array:
	return FaceConfig.EYES.keys()


func mouths_list() -> Array:
	return FaceConfig.MOUTHS.keys()


## Switch to a named pose / mood (sets BOTH eyes and mouth). `new_look` (Vector2,
## -1..1) defaults to the mood's own gaze or centered. Unknown names fall back
## to the default pose.
func set_pose(name: String, new_look: Variant = null) -> void:
	var r := FaceConfig.resolve(name)
	var gaze: Variant = new_look
	if not (gaze is Vector2) and r.has("look"):
		gaze = r["look"]
	set_face(String(r["eyes"]), String(r["mouth"]), gaze)


## Set the eyes and/or the mouth independently. "" keeps that unit as it is.
## Unknown names are ignored (the unit stays as it was). Gaze: `new_look` if
## given; otherwise it is kept when the EYES don't change and re-centered when
## they do (a mouth-only change never moves her gaze).
func set_face(eyes: String = "", mouth: String = "", new_look: Variant = null) -> void:
	var e := eyes.strip_edges().to_lower()
	var m := mouth.strip_edges().to_lower()
	var eyes_changed := false
	if e != "" and FaceConfig.has_eyes(e) and e != eyes_name:
		eyes_name = e
		eyes_changed = true
	if m != "" and FaceConfig.has_mouth(m):
		mouth_name = m
	var named := FaceConfig.pose_for(eyes_name, mouth_name)
	pose_name = named if named != "" else "%s/%s" % [eyes_name, mouth_name]
	if new_look is Vector2:
		look = _clamp_look(new_look)
	elif eyes_changed:
		look = Vector2.ZERO
	_retarget(eyes_changed)


func set_look(v: Vector2) -> void:
	look = _clamp_look(v)


## Raw audio level 0..1 (call every frame while sound plays; smoothing is internal).
func set_level(v: float) -> void:
	_level_raw = clampf(v, 0.0, 1.0)


func set_speaking(on: bool) -> void:
	speaking = on
	_scalar_target("mouth", 1.0 if (_mcfg()["silent"] or on) else 0.0)


## One eye's bottom lid flicks (a tic, not a blink). `side` = "left_eye" or
## "right_eye"; "" picks one at random each time.
func twitch(side: String = "") -> void:
	_twitch_side = side if side in EYE_KEYS else String(EYE_KEYS[randi() % 2])
	_twitch_left = twitch_seconds
	_retarget(false)


func set_theme_color(c: Color) -> void:
	theme_color = c
	queue_redraw()


func is_active() -> bool:
	if speaking or _blink_p >= 0.0 or _twitch_left > 0.0:
		return true
	if _sc["props"][0] > 0.01 or _jit_r > 0.0:
		return true   # a spinning spinner or a teleporting knot never rests
	for key in _vel:
		for v: Vector2 in _vel[key]:
			if v.length_squared() > 4.0:
				return true
	return false


func debug_cycle(step: int) -> void:
	var keys: Array = poses()
	var i: int = keys.find(pose_name)
	set_pose(String(keys[wrapi(i + step, 0, keys.size())]))


func debug_cycle_eyes(step: int) -> void:
	var keys: Array = eyes_list()
	set_face(String(keys[wrapi(keys.find(eyes_name) + step, 0, keys.size())]), "")


func debug_cycle_mouth(step: int) -> void:
	var keys: Array = mouths_list()
	set_face("", String(keys[wrapi(keys.find(mouth_name) + step, 0, keys.size())]))


## Live numbers for tests and the debug overlay.
func snapshot() -> Dictionary:
	return {
		"pose": pose_name, "eyes": eyes_name, "mouth": mouth_name,
		"look": look, "eye_off": _eye_off, "mouth_off": _mouth_off,
		"level": level, "mouth_alpha": _sc["mouth"][0], "fill": _sc["fill"][0],
		"outline": _sc["outline"][0], "lines": _sc["lines"][0],
		"blink_scale": _blink_scale(), "mouth_width": _mouth_width(),
		"mouth_height": _mouth_height(), "mouth_open_scale": _mouth_open_scale(),
	}


# --------------------------------------------------------------------- helpers

func _zeros(n: int) -> PackedVector2Array:
	var v := PackedVector2Array()
	for i in n:
		v.append(Vector2.ZERO)
	return v


func _clamp_look(v: Vector2) -> Vector2:
	return v.limit_length(1.0)


func _ecfg() -> Dictionary:
	return FaceConfig.EYES[eyes_name]


func _mcfg() -> Dictionary:
	return FaceConfig.MOUTHS[mouth_name]


func _next_blink_gap() -> float:
	return randf_range(blink_min_gap, blink_max_gap) * float(_ecfg()["blink_gap"])


func _scalar_target(key: String, v: float) -> void:
	_sc[key][2] = v


## Point the springs at the current eyes + mouth (plus a twitch override on the eyes).
func _retarget(eyes_changed: bool) -> void:
	var eye_shape: Dictionary = _shapes[_ecfg()["src"]]
	var mouth_shape: Dictionary = _shapes[_mcfg()["src"]]
	var twitch_shape: Dictionary = _shapes[FaceConfig.EYES[FaceConfig.TWITCH_EYES]["src"]]
	for key in EYE_KEYS:
		# Only the twitching eye takes the bottom-lid shape; the other keeps her expression.
		var src: Dictionary = twitch_shape if (_twitch_left > 0.0 and key == _twitch_side) else eye_shape
		_tgt[key] = PackedVector2Array(src[key])
	_tgt["mouth"] = _mouth_target()

	var outlined: bool = float(eye_shape["eye_outline"]) > 0.0
	_scalar_target("fill", 0.0 if outlined else 1.0)
	_scalar_target("outline", 1.0 if outlined else 0.0)
	if outlined:
		_scalar_target("outline_w", float(eye_shape["eye_outline"]))
	_scalar_target("mouth", 1.0 if (_mcfg()["silent"] or speaking) else 0.0)

	if eyes_changed:
		_retarget_lines(eye_shape)
		_retarget_props(eye_shape)
		if not _ecfg()["blink"]:
			_blink_p = -1.0
		_blink_in = _next_blink_gap()


## The mouth the springs aim at: her expression's own mouth, or, while she is
## talking, a straight bar (lipsync can't hold an angled mouth) sitting at the
## same height the expression's mouth sits at, so it doesn't hop.
func _mouth_target() -> PackedVector2Array:
	var own: PackedVector2Array = _shapes[_mcfg()["src"]]["mouth"]
	if not _talking:
		return PackedVector2Array(own)
	var talk_name := String(_mcfg().get("talk", FaceConfig.TALK_MOUTH))
	if not FaceConfig.MOUTHS.has(talk_name):
		talk_name = FaceConfig.TALK_MOUTH
	var bar: PackedVector2Array = _shapes[FaceConfig.MOUTHS[talk_name]["src"]]["mouth"]
	var dy := _home_y(own) - _home_y(bar)
	var out := PackedVector2Array(bar)
	for i in out.size():
		out[i] = Vector2(bar[i].x, bar[i].y + dy)
	return out


## Where a mouth "sits": the median height of its rung midpoints (robust to a
## tick or a tail that sticks out).
func _home_y(pts: PackedVector2Array) -> float:
	var ys: Array[float] = []
	for k in 5:
		ys.append((pts[k].y + pts[POINTS - 1 - k].y) * 0.5)
	ys.sort()
	return ys[2]


## Stroke count varies per shape (blush 10, spooked 4, scribble's knot 21), so
## the stroke arrays grow to fit. Returns how many entries were just appended
## (brand-new strokes must "draw on" instead of sliding in from off-screen).
func _ensure_lines(n: int) -> int:
	var old := _ln_a.size()
	while _ln_a.size() < n:
		_ln_a.append(Vector2.ZERO)
		_ln_b.append(Vector2.ZERO)
		_ln_va.append(Vector2.ZERO)
		_ln_vb.append(Vector2.ZERO)
		_ln_tgt_a.append(Vector2.ZERO)
		_ln_tgt_b.append(Vector2.ZERO)
		_ln_w.append(3.0)
		# One jitter slot per stroke endpoint.
		_jit_o.append(Vector2.ZERO)
		_jit_o.append(Vector2.ZERO)
		_jit_t.append(randf_range(jitter_min, jitter_max))
		_jit_t.append(randf_range(jitter_min, jitter_max))
	return _ln_a.size() - old


func _retarget_lines(shape: Dictionary) -> void:
	var lines: Array = shape["lines"]
	var appended := _ensure_lines(lines.size())
	if lines.is_empty():
		# Retract in place, then fade.
		for i in _ln_a.size():
			var mid := (_ln_a[i] + _ln_b[i]) * 0.5
			_ln_tgt_a[i] = mid
			_ln_tgt_b[i] = mid
		_scalar_target("lines", 0.0)
	else:
		var from_hidden: bool = _sc["lines"][0] < 0.05
		for i in _ln_a.size():
			if i < lines.size():
				var l: Array = lines[i]
				_ln_tgt_a[i] = l[0]
				_ln_tgt_b[i] = l[1]
				_ln_w[i] = float(l[2])
				if from_hidden or i >= _ln_a.size() - appended:
					# Draw on from the middle of the line instead of
					# sliding in (fresh strokes must never trace from
					# the screen's origin).
					var mid: Vector2 = ((l[0] as Vector2) + (l[1] as Vector2)) * 0.5
					_ln_a[i] = mid
					_ln_b[i] = mid
					_ln_va[i] = Vector2.ZERO
					_ln_vb[i] = Vector2.ZERO
			else:
				# Strokes the new shape doesn't have: shrink into their
				# own middle until they're dots (and so, invisible).
				var dead := (_ln_a[i] + _ln_b[i]) * 0.5
				_ln_tgt_a[i] = dead
				_ln_tgt_b[i] = dead
		_scalar_target("lines", 1.0)
	# Teleport radius: per-shape (Scribble's knot stutters; blush stays put).
	var jit := float(shape.get("jitter", 0.0))
	if jit != _jit_r:
		_jit_r = jit
		for i in _jit_t.size():
			_jit_t[i] = randf_range(jitter_min, jitter_max)
			_jit_o[i] = Vector2.ZERO


## Swap in this shape's prop polygons (none for every pose but Thunking so far).
## Props fade with the "props" spring and keep spinning while they fade out.
func _retarget_props(shape: Dictionary) -> void:
	var props: Array = shape["props"]
	_scalar_target("props", 1.0 if not props.is_empty() else 0.0)
	if props.is_empty():
		return
	_pr_base.clear()
	_pr_hub.clear()
	_pr_spin.clear()
	for pr in props:
		var pts: PackedVector2Array = pr["pts"]
		_pr_base.append(PackedVector2Array(pts))
		_pr_hub.append(_bbox_center(pts))
		_pr_spin.append(bool(pr["spin"]))


# ------------------------------------------------------------------ simulation

func _process(delta: float) -> void:
	delta = minf(delta, 0.05)
	_t += delta
	var ecfg: Dictionary = _ecfg()
	var mcfg: Dictionary = _mcfg()

	# Point springs.
	for key in ["left_eye", "right_eye", "mouth"]:
		var ci: PackedVector2Array = _cur[key]
		var vi: PackedVector2Array = _vel[key]
		var ti: PackedVector2Array = _tgt[key]
		for i in POINTS:
			vi[i] += ((ti[i] - ci[i]) * stiffness - vi[i] * damping) * delta
			ci[i] += vi[i] * delta
		_cur[key] = ci
		_vel[key] = vi
	for i in _ln_a.size():
		_ln_va[i] += ((_ln_tgt_a[i] - _ln_a[i]) * stiffness - _ln_va[i] * damping) * delta
		_ln_vb[i] += ((_ln_tgt_b[i] - _ln_b[i]) * stiffness - _ln_vb[i] * damping) * delta
		_ln_a[i] += _ln_va[i] * delta
		_ln_b[i] += _ln_vb[i] * delta

	# Scalar springs.
	for key in _sc:
		var s: Array = _sc[key]
		s[1] += ((s[2] - s[0]) * stiffness - s[1] * damping) * delta
		s[0] += s[1] * delta
	for key in ["fill", "outline", "mouth", "lines", "props"]:
		_sc[key][0] = clampf(_sc[key][0], 0.0, 1.0)

	# PROP UNIT: the spinner turns; stroke endpoints teleport. A teleport is a
	# clock expiring, then the endpoint either SNAPS home (re-forms with the
	# knot, the "gather" beat) or snaps to a fresh spot at random — instant
	# jumps on purpose; lerping them would just look like mush.
	_spin_a = fmod(_spin_a + spinner_speed * delta, 360.0)
	if _jit_r > 0.0:
		for i in _jit_t.size():
			_jit_t[i] -= delta
			if _jit_t[i] <= 0.0:
				_jit_t[i] = randf_range(jitter_min, jitter_max)
				if randf() < 0.35:
					_jit_o[i] = Vector2.ZERO
				else:
					var d := randf_range(_jit_r * 0.3, _jit_r)
					_jit_o[i] = Vector2.from_angle(randf_range(0.0, TAU)) * d

	# Unit offsets: gaze scaled by this pose's limits (mouth moves less).
	# Critically damped on purpose, so a unit can never overshoot its limit.
	var eye_t: Vector2 = Vector2(look.x * ecfg["range"].x, look.y * ecfg["range"].y) * FaceConfig.GAZE_GAIN
	var mouth_t: Vector2 = Vector2(look.x * mcfg["range"].x, look.y * mcfg["range"].y)
	_eye_off_v += ((eye_t - _eye_off) * 120.0 - _eye_off_v * 22.0) * delta
	_eye_off += _eye_off_v * delta
	_mouth_off_v += ((mouth_t - _mouth_off) * 100.0 - _mouth_off_v * 20.0) * delta
	_mouth_off += _mouth_off_v * delta

	# Speech level: quick attack, slightly slower release.
	var rate := level_attack if _level_raw > level else level_release
	level = lerpf(level, _level_raw, 1.0 - exp(-rate * delta))

	# Talking: once sound really opens the mouth it becomes a straight bar, and it
	# goes back to the expression's mouth only after a short quiet.
	if level > FaceConfig.TALK_ON_LEVEL:
		_talk_hold = FaceConfig.TALK_RELEASE
	elif _talk_hold > 0.0:
		_talk_hold -= delta
	var talk_now := _talk_hold > 0.0
	if talk_now != _talking:
		_talking = talk_now
		_retarget(false)

	# Blink.
	if ecfg["blink"]:
		if _blink_p >= 0.0:
			_blink_p += delta / (blink_seconds / float(ecfg["blink_speed"]))
			if _blink_p >= 1.0:
				# About one blink in five she does the quick second flick:
				# restart mid-blink (from the hold, so it's shallow and fast)
				# instead of scheduling a fresh full gap.
				_blink_p = 0.55 if randf() < 0.18 else -1.0
				if _blink_p < 0.0:
					_blink_in = _next_blink_gap()
		else:
			_blink_in -= delta
			if _blink_in <= 0.0:
				_blink_p = 0.0

	# Twitch: brief bottom-lid flick, only in poses that allow it.
	if _twitch_left > 0.0:
		_twitch_left -= delta
		if _twitch_left <= 0.0:
			_twitch_left = 0.0
			_retarget(false)
	elif ecfg["twitch"]:
		_twitch_in -= delta
		if _twitch_in <= 0.0:
			_twitch_in = randf_range(10.0, 25.0)
			twitch()

	queue_redraw()


# --------------------------------------------------------------------- drawing

func _blink_scale() -> float:
	# Eye-openness multiplier over one blink (_blink_p 0..1), the OLD sine
	# replaced 2026-11-09: the sine is symmetric and boneless, so every blink
	# read as the same slow metronome sweep. A real blink is: shut FAST, waste
	# a moment with the lids down, then a slower open with a little overshoot
	# pop (the eyes spring past rest for an instant — that's what reads alive).
	if _blink_p < 0.0:
		return 1.0
	var p := _blink_p
	if p < 0.35:                      # close: fast out of the gate, easing shut
		var u: float = p / 0.35
		return 1.0 - 0.96 * (1.0 - (1.0 - u) * (1.0 - u) * (1.0 - u))
	if p < 0.5:                       # a quick hold, nearly but not fully shut
		return 0.04
	var u: float = (p - 0.5) / 0.5
	# open: ease-out-back — rises to ~1.06 then settles exactly on 1.0
	var back: float = 1.0 + 2.70158 * (u - 1.0) * (u - 1.0) * (u - 1.0) \
			+ 1.70158 * (u - 1.0) * (u - 1.0)
	return 0.04 + 0.96 * back


func _bbox_center(pts: PackedVector2Array) -> Vector2:
	var lo := pts[0]
	var hi := pts[0]
	for p in pts:
		lo = lo.min(p)
		hi = hi.max(p)
	return (lo + hi) * 0.5


## How far speech has opened the mouth: ONE thickness multiplier used for every
## part of the mouth. Level 0 = as drawn (1.0x); level 1 = the mouth's `open`.
func _mouth_open_scale() -> float:
	if _cur.is_empty():
		return 1.0
	return lerpf(1.0, maxf(float(_mcfg()["open"]), 1.0), level)


## The mouth after speech has opened it (offsets/gaze are applied later).
## "rung": each of the ribbon's 5 rungs (point k <-> point 9-k) scales about its
## own midpoint, so the mouth keeps its spine and gets thicker along its own shape.
## A rung far longer than the mouth's usual thickness (Pout's tick) is SHAPE, not
## stroke, so it holds its drawn size (smoothly, so a morph never pops).
## "whole": everything scales vertically about the overall center.
func _open_mouth(pts: PackedVector2Array) -> PackedVector2Array:
	var out := PackedVector2Array(pts)
	var k_open := _mouth_open_scale()
	if absf(k_open - 1.0) < 0.001:
		return out
	if FaceConfig.MOUTH_OPEN_MODE == "rung":
		var lens: Array[float] = []
		for k in 5:
			lens.append(pts[k].distance_to(pts[POINTS - 1 - k]))
		var sorted := lens.duplicate()
		sorted.sort()
		var typical := maxf(float(sorted[2]), 1.0)   # median rung length
		for k in 5:
			var hold := smoothstep(2.0, 4.0, lens[k] / typical)
			var ks := lerpf(k_open, 1.0, hold)
			var a := pts[k]
			var b := pts[POINTS - 1 - k]
			var mid := (a + b) * 0.5
			out[k] = mid + (a - mid) * ks
			out[POINTS - 1 - k] = mid + (b - mid) * ks
	else:
		var cy := _bbox_center(pts).y
		for i in pts.size():
			out[i] = Vector2(pts[i].x, cy + (pts[i].y - cy) * k_open)
	return out


## Mouth size as drawn now (after speech has opened it).
func _mouth_width() -> float:
	return _mouth_size().x


func _mouth_height() -> float:
	return _mouth_size().y


func _mouth_size() -> Vector2:
	if _cur.is_empty():
		return Vector2.ZERO
	var pts := _open_mouth(_cur["mouth"])
	var lo := pts[0]
	var hi := pts[0]
	for p in pts:
		lo = lo.min(p)
		hi = hi.max(p)
	return hi - lo


func _draw() -> void:
	if _cur.is_empty():
		return
	var ecfg: Dictionary = _ecfg()
	var mcfg: Dictionary = _mcfg()
	var breath := 0.95 + 0.05 * sin(_t * 1.35)
	var col := Color(theme_color.r, theme_color.g, theme_color.b, breath)

	var tremble := Vector2.ZERO
	if float(ecfg["tremble"]) > 0.0:
		tremble = Vector2(randf_range(-1.0, 1.0), randf_range(-1.0, 1.0)) * float(ecfg["tremble"])
	var drift := Vector2(sin(_t * 0.37) * 5.0, sin(_t * 0.21) * 3.0)
	var eye_off := _eye_off + drift + tremble
	var mouth_off := _mouth_off + tremble

	# ---- eye unit
	var eye_cy := (_bbox_center(_cur["left_eye"]).y + _bbox_center(_cur["right_eye"]).y) * 0.5
	var sy := _blink_scale()
	var fill_a: float = _sc["fill"][0]
	var out_a: float = _sc["outline"][0]
	for key in EYE_KEYS:
		var pts := _xform(_cur[key], eye_off, Vector2.ONE, Vector2(0.0, eye_cy), Vector2(1.0, sy))
		if fill_a > 0.01:
			_fill_ribbon(pts, _alpha(col, fill_a))
		if out_a > 0.01:
			_outline_ribbon(pts, _alpha(col, out_a), float(_sc["outline_w"][0]))
	var lines_a: float = _sc["lines"][0]
	if lines_a > 0.01:
		var lc := _alpha(col, lines_a)
		var jit_on := _jit_r > 0.0
		for i in _ln_a.size():
			var a := _ln_a[i]
			var b := _ln_b[i]
			if jit_on:
				a += _jit_o[2 * i]
				b += _jit_o[2 * i + 1]
			draw_line(
				_xf_point(a, eye_off, Vector2(0.0, eye_cy), Vector2(1.0, sy)),
				_xf_point(b, eye_off, Vector2(0.0, eye_cy), Vector2(1.0, sy)),
				lc, maxf(_ln_w[i], 1.0), true
			)

	# ---- mouth unit: the ENTIRE mouth opens with sound (uniformly, per rung)
	var mouth_a: float = _sc["mouth"][0]
	if mouth_a > 0.01:
		var mpts := _xform(_open_mouth(_cur["mouth"]), mouth_off, Vector2.ONE, Vector2.ZERO, Vector2.ONE)
		_fill_ribbon(mpts, _alpha(col, mouth_a))

	# ---- prop unit: rides with the EYE unit's drift (gaze, tremble) but never
	# blinks — the spinner is a thought, not a body part. Rigid rotate; no
	# springs, so the band stays perfectly round while it turns.
	var props_a: float = _sc["props"][0]
	if props_a > 0.01:
		var pc := _alpha(col, props_a)
		var rad := deg_to_rad(_spin_a)
		for p in _pr_base.size():
			var pts := _rot_pts(_pr_base[p], _pr_hub[p], rad if _pr_spin[p] else 0.0)
			_fill_ribbon(_xform(pts, eye_off, Vector2.ONE, Vector2.ZERO, Vector2.ONE), pc)


func _alpha(c: Color, a: float) -> Color:
	return Color(c.r, c.g, c.b, c.a * a)


func _xf_point(p: Vector2, off: Vector2, pivot: Vector2, scale_xy: Vector2) -> Vector2:
	return Vector2(pivot.x + (p.x - pivot.x) * scale_xy.x, pivot.y + (p.y - pivot.y) * scale_xy.y) + off


func _xform(pts: PackedVector2Array, off: Vector2, _unused: Vector2, pivot: Vector2, scale_xy: Vector2) -> PackedVector2Array:
	var out := PackedVector2Array()
	out.resize(pts.size())
	for i in pts.size():
		out[i] = _xf_point(pts[i], off, pivot, scale_xy)
	return out


## Rotate a polygon about `hub` by `rad` radians (0 = pass through untouched).
func _rot_pts(pts: PackedVector2Array, hub: Vector2, rad: float) -> PackedVector2Array:
	if rad == 0.0:
		return pts
	var s := sin(rad)
	var c := cos(rad)
	var out := PackedVector2Array()
	out.resize(pts.size())
	for i in pts.size():
		var d := pts[i] - hub
		out[i] = hub + Vector2(d.x * c - d.y * s, d.x * s + d.y * c)
	return out


## Fill a 10-point ribbon: 4 quads between rungs (i, 9-i), each split into two
## triangles with the SAME layout every frame, so no morph can break the draw.
func _fill_ribbon(p: PackedVector2Array, col: Color) -> void:
	var cols := PackedColorArray([col, col, col])
	var no_uv := PackedVector2Array()
	for k in 4:
		var a := p[k]
		var b := p[k + 1]
		var c := p[8 - k]
		var d := p[9 - k]
		draw_primitive(PackedVector2Array([a, b, c]), cols, no_uv)
		draw_primitive(PackedVector2Array([a, c, d]), cols, no_uv)
	# Antialiased rim (draw_primitive has no AA of its own).
	var loop := PackedVector2Array(p)
	loop.append(p[0])
	draw_polyline(loop, col, aa_width, true)


func _outline_ribbon(p: PackedVector2Array, col: Color, width: float) -> void:
	var loop := PackedVector2Array(p)
	loop.append(p[0])
	draw_polyline(loop, col, width, true)
