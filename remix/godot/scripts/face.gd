# face.gd: Remix's face engine.
#
# The face is two UNITS: the eyes (both, together) and the mouth (one piece).
# Each pose (scripts/face_shapes.gd, generated from art/faces/*.svg) is a set of
# 10-point ribbon polygons. Springs pull every point toward the current pose,
# so any pose eases into any other, and changing your mind mid-move stays smooth.
#
# On top of the pose, additively:
#   look      the brain's gaze (-1..1, -1..1): shifts the eye unit a lot and the
#             mouth unit a little, never beyond that pose's limits (face_config.gd)
#   speech    audio level widens the WHOLE mouth (proportional), capped per pose
#   blink     the eye unit squashes to a line (not in every pose)
#   twitch    brief bottom-lid flick (the "smirk" eye shape), poses opt in
#   life      breathing glow, tiny gaze drift, tremble (Spooked)
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

var pose_name := FaceConfig.DEFAULT_POSE
var look := Vector2.ZERO

var _shapes: Dictionary = {}
var _cfg: Dictionary = {}

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
}
var _ln_a := PackedVector2Array()
var _ln_b := PackedVector2Array()
var _ln_va := PackedVector2Array()
var _ln_vb := PackedVector2Array()
var _ln_tgt_a := PackedVector2Array()
var _ln_tgt_b := PackedVector2Array()
var _ln_w := PackedFloat32Array()

var _eye_off := Vector2.ZERO
var _eye_off_v := Vector2.ZERO
var _mouth_off := Vector2.ZERO
var _mouth_off_v := Vector2.ZERO

var _t := 0.0
var _blink_in := 3.0
var _blink_p := -1.0
var _twitch_in := 12.0
var _twitch_left := 0.0

var _level_raw := 0.0
var level := 0.0           # smoothed 0..1 (drives mouth widening)
var speaking := false


func _ready() -> void:
	_shapes = FaceShapes.all()
	_cfg = FaceConfig.POSES
	var start: Dictionary = _shapes[FaceConfig.DEFAULT_POSE]
	for key in ["left_eye", "right_eye", "mouth"]:
		_cur[key] = PackedVector2Array(start[key])
		_vel[key] = _zeros(POINTS)
		_tgt[key] = PackedVector2Array(start[key])
	for i in 4:
		_ln_a.append(Vector2.ZERO)
		_ln_b.append(Vector2.ZERO)
		_ln_va.append(Vector2.ZERO)
		_ln_vb.append(Vector2.ZERO)
		_ln_tgt_a.append(Vector2.ZERO)
		_ln_tgt_b.append(Vector2.ZERO)
		_ln_w.append(3.0)
	_blink_in = _next_blink_gap()
	_twitch_in = randf_range(10.0, 25.0)


# ------------------------------------------------------------------ public API

func poses() -> Array:
	return _shapes.keys()


## Switch pose. `new_look` (Vector2, -1..1) defaults to the mood's own gaze or
## centered. Unknown names fall back to the default pose.
func set_pose(name: String, new_look: Variant = null) -> void:
	var r := FaceConfig.resolve(name)
	var key: String = r["pose"]
	var gaze: Vector2 = Vector2.ZERO
	if new_look is Vector2:
		gaze = new_look
	elif r.has("look"):
		gaze = r["look"]
	elif key == pose_name:
		gaze = look
	pose_name = key
	look = _clamp_look(gaze)
	_retarget(true)


func set_look(v: Vector2) -> void:
	look = _clamp_look(v)


## Raw audio level 0..1 (call every frame while sound plays; smoothing is internal).
func set_level(v: float) -> void:
	_level_raw = clampf(v, 0.0, 1.0)


func set_speaking(on: bool) -> void:
	speaking = on
	_scalar_target("mouth", 1.0 if (_cfg[pose_name]["mouth_when_silent"] or on) else 0.0)


func twitch() -> void:
	_twitch_left = twitch_seconds
	_retarget(false)


func set_theme_color(c: Color) -> void:
	theme_color = c
	queue_redraw()


func is_active() -> bool:
	if speaking or _blink_p >= 0.0 or _twitch_left > 0.0:
		return true
	for key in _vel:
		for v: Vector2 in _vel[key]:
			if v.length_squared() > 4.0:
				return true
	return false


func debug_cycle(step: int) -> void:
	var keys: Array = _shapes.keys()
	var i: int = keys.find(pose_name)
	set_pose(String(keys[wrapi(i + step, 0, keys.size())]))


## Live numbers for tests and the debug overlay.
func snapshot() -> Dictionary:
	return {
		"pose": pose_name, "look": look, "eye_off": _eye_off, "mouth_off": _mouth_off,
		"level": level, "mouth_alpha": _sc["mouth"][0], "fill": _sc["fill"][0],
		"outline": _sc["outline"][0], "lines": _sc["lines"][0],
		"blink_scale": _blink_scale(), "mouth_width": _mouth_width(),
	}


# --------------------------------------------------------------------- helpers

func _zeros(n: int) -> PackedVector2Array:
	var v := PackedVector2Array()
	for i in n:
		v.append(Vector2.ZERO)
	return v


func _clamp_look(v: Vector2) -> Vector2:
	return v.limit_length(1.0)


func _next_blink_gap() -> float:
	var gap_mult: float = _cfg[pose_name]["blink_gap"] if _cfg.has(pose_name) else 1.0
	return randf_range(blink_min_gap, blink_max_gap) * gap_mult


func _scalar_target(key: String, v: float) -> void:
	_sc[key][2] = v


## Point the springs at the current pose (plus a twitch override on the eyes).
func _retarget(pose_changed: bool) -> void:
	var shape: Dictionary = _shapes[pose_name]
	var cfg: Dictionary = _cfg[pose_name]
	var eye_src: Dictionary = _shapes["smirk"] if _twitch_left > 0.0 else shape
	for key in EYE_KEYS:
		_tgt[key] = PackedVector2Array(eye_src[key])
	_tgt["mouth"] = PackedVector2Array(shape["mouth"])

	var outlined: bool = float(shape["eye_outline"]) > 0.0
	_scalar_target("fill", 0.0 if outlined else 1.0)
	_scalar_target("outline", 1.0 if outlined else 0.0)
	if outlined:
		_scalar_target("outline_w", float(shape["eye_outline"]))
	_scalar_target("mouth", 1.0 if (cfg["mouth_when_silent"] or speaking) else 0.0)

	if pose_changed:
		_retarget_lines(shape)
		if not cfg["blink"]:
			_blink_p = -1.0
		_blink_in = _next_blink_gap()


func _retarget_lines(shape: Dictionary) -> void:
	var lines: Array = shape["lines"]
	if lines.is_empty():
		# Retract in place, then fade.
		for i in 4:
			var mid := (_ln_a[i] + _ln_b[i]) * 0.5
			_ln_tgt_a[i] = mid
			_ln_tgt_b[i] = mid
		_scalar_target("lines", 0.0)
		return
	var from_hidden: bool = _sc["lines"][0] < 0.05
	for i in 4:
		var l: Array = lines[i]
		_ln_tgt_a[i] = l[0]
		_ln_tgt_b[i] = l[1]
		_ln_w[i] = float(l[2])
		if from_hidden:
			# Draw on from the middle of the line instead of sliding in.
			var mid: Vector2 = ((l[0] as Vector2) + (l[1] as Vector2)) * 0.5
			_ln_a[i] = mid
			_ln_b[i] = mid
			_ln_va[i] = Vector2.ZERO
			_ln_vb[i] = Vector2.ZERO
	_scalar_target("lines", 1.0)


# ------------------------------------------------------------------ simulation

func _process(delta: float) -> void:
	delta = minf(delta, 0.05)
	_t += delta
	var cfg: Dictionary = _cfg[pose_name]

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
	for i in 4:
		_ln_va[i] += ((_ln_tgt_a[i] - _ln_a[i]) * stiffness - _ln_va[i] * damping) * delta
		_ln_vb[i] += ((_ln_tgt_b[i] - _ln_b[i]) * stiffness - _ln_vb[i] * damping) * delta
		_ln_a[i] += _ln_va[i] * delta
		_ln_b[i] += _ln_vb[i] * delta

	# Scalar springs.
	for key in _sc:
		var s: Array = _sc[key]
		s[1] += ((s[2] - s[0]) * stiffness - s[1] * damping) * delta
		s[0] += s[1] * delta
	for key in ["fill", "outline", "mouth", "lines"]:
		_sc[key][0] = clampf(_sc[key][0], 0.0, 1.0)

	# Unit offsets: gaze scaled by this pose's limits (mouth moves less).
	# Critically damped on purpose, so a unit can never overshoot its limit.
	var eye_t: Vector2 = Vector2(look.x * cfg["eye_range"].x, look.y * cfg["eye_range"].y)
	var mouth_t: Vector2 = Vector2(look.x * cfg["mouth_range"].x, look.y * cfg["mouth_range"].y)
	_eye_off_v += ((eye_t - _eye_off) * 120.0 - _eye_off_v * 22.0) * delta
	_eye_off += _eye_off_v * delta
	_mouth_off_v += ((mouth_t - _mouth_off) * 100.0 - _mouth_off_v * 20.0) * delta
	_mouth_off += _mouth_off_v * delta

	# Speech level: quick attack, slightly slower release.
	var rate := level_attack if _level_raw > level else level_release
	level = lerpf(level, _level_raw, 1.0 - exp(-rate * delta))

	# Blink.
	if cfg["blink"]:
		if _blink_p >= 0.0:
			_blink_p += delta / (blink_seconds / cfg["blink_speed"])
			if _blink_p >= 1.0:
				_blink_p = -1.0
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
	elif cfg["twitch"]:
		_twitch_in -= delta
		if _twitch_in <= 0.0:
			_twitch_in = randf_range(10.0, 25.0)
			twitch()

	queue_redraw()


# --------------------------------------------------------------------- drawing

func _blink_scale() -> float:
	if _blink_p < 0.0:
		return 1.0
	return 1.0 - 0.94 * sin(PI * _blink_p)


func _bbox_center(pts: PackedVector2Array) -> Vector2:
	var lo := pts[0]
	var hi := pts[0]
	for p in pts:
		lo = lo.min(p)
		hi = hi.max(p)
	return (lo + hi) * 0.5


func _mouth_width() -> float:
	var pts: PackedVector2Array = _cur["mouth"] if not _cur.is_empty() else PackedVector2Array([Vector2.ZERO])
	var lo := pts[0].x
	var hi := pts[0].x
	for p in pts:
		lo = minf(lo, p.x)
		hi = maxf(hi, p.x)
	var widen: float = 1.0 + float(_cfg[pose_name]["widen"]) * level if _cfg.has(pose_name) else 1.0
	return (hi - lo) * widen


func _draw() -> void:
	if _cur.is_empty():
		return
	var cfg: Dictionary = _cfg[pose_name]
	var breath := 0.95 + 0.05 * sin(_t * 1.35)
	var col := Color(theme_color.r, theme_color.g, theme_color.b, breath)

	var tremble := Vector2.ZERO
	if cfg["tremble"] > 0.0:
		tremble = Vector2(randf_range(-1.0, 1.0), randf_range(-1.0, 1.0)) * float(cfg["tremble"])
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
		for i in 4:
			var a := _xf_point(_ln_a[i], eye_off, Vector2(0.0, eye_cy), Vector2(1.0, sy))
			var b := _xf_point(_ln_b[i], eye_off, Vector2(0.0, eye_cy), Vector2(1.0, sy))
			draw_line(a, b, lc, maxf(_ln_w[i], 1.0), true)

	# ---- mouth unit: the ENTIRE mouth widens with sound
	var mouth_a: float = _sc["mouth"][0]
	if mouth_a > 0.01:
		var mc := _bbox_center(_cur["mouth"])
		var wx := 1.0 + float(cfg["widen"]) * level
		var mpts := _xform(_cur["mouth"], mouth_off, Vector2.ONE, Vector2(mc.x, 0.0), Vector2(wx, 1.0))
		_fill_ribbon(mpts, _alpha(col, mouth_a))


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
