# face.gd — Remix's face ENGINE (NEW_SPEC "Face rendering requirements").
# Two eyes + one mouth, each a Polygon2D-concept polygon of a FIXED point
# count (FaceAtlas.PTS = 16). Expressions are target point sets; springs pull
# every point toward its target, so any expression eases into any other and
# interrupting a morph is smooth. Unmorphable glyph shapes (scribble/static)
# swap in with a static burst instead of a morph.
#
# Additive idle life on top: blinking (eyes collapse to a line), breathing
# glow, gaze drift, per-expression jitter. Theme color = ONE exported value
# that also drives the CRT shader uniform on the screen node.
extends Node2D

const FaceAtlasScript := preload("res://scripts/face_atlas.gd")

@export var theme_color := Color(0.95, 0.12, 0.10)
@export var screen_path: NodePath  # ColorRect holding the CRT shader
@export var spring_stiffness := 190.0
@export var spring_damping := 20.0
@export var blink_min_gap := 2.4
@export var blink_max_gap := 7.5

const FALLBACK := "idle"

var _exprs: Dictionary = {}
var _current_name := FALLBACK
var _cur := {}      # feature -> PackedVector2Array (drawn geometry, local)
var _vel := {}      # feature -> PackedVector2Array (spring velocities)
var _tgt := {}      # feature -> PackedVector2Array (targets)
var _meta := {}
var _glitching := false
var _burst := 0.0   # static burst seconds remaining
var _t := 0.0
var _blink_at := 0.0
var _blink := 0.0   # 1 = open, 0 = closed
var _yoff := 0.0    # whole-face droop from meta "dy", itself spring-damped

@onready var _screen: ColorRect = get_node(screen_path) if screen_path != NodePath() else null


func _ready() -> void:
	_exprs = FaceAtlasScript.all()
	_current_name = FALLBACK
	for key in ["left_eye", "right_eye", "mouth"]:
		var start: PackedVector2Array = _exprs[FALLBACK][key]
		_cur[key] = PackedVector2Array(start)  # own copies: they will be mutated
		_vel[key] = _zeros(start.size())
		_tgt[key] = PackedVector2Array(start)
	_blink_at = _rand_blink_gap()
	_apply_theme_color()
	if _screen and _screen.material is ShaderMaterial:
		(_screen.material as ShaderMaterial).set_shader_parameter(
				"theme_color", theme_color)


func set_theme_color(color: Color) -> void:
	theme_color = color
	_apply_theme_color()
	if _screen and _screen.material is ShaderMaterial:
		(_screen.material as ShaderMaterial).set_shader_parameter(
				"theme_color", color)
	queue_redraw()


func _apply_theme_color() -> void:
	modulate = theme_color  # glyphs drawn white-ish; ONE color tints all


## mood name from brain -> expression. Unknown moods fall back to idle.
func set_expression(name: String) -> void:
	if not _exprs.has(name):
		name = FALLBACK
	var expr: Dictionary = _exprs[name]
	_meta = expr.get("meta", {})
	_glitching = bool(expr.get("glitch", false))
	for key in ["left_eye", "right_eye", "mouth"]:
		_tgt[key] = PackedVector2Array(expr[key])  # own copy; springs read it
	if _glitching:
		# Static burst covers the unmorphable swap (and hides geometry swap).
		_burst = 0.16
		_cur["left_eye"] = PackedVector2Array(_tgt["left_eye"])
		_cur["right_eye"] = PackedVector2Array(_tgt["right_eye"])
		_cur["mouth"] = PackedVector2Array(_tgt["mouth"])
		for k in _vel:
			_vel[k] = _zeros(FaceAtlasScript.PTS)
	_current_name = name
	queue_redraw()


func _zeros(n: int) -> PackedVector2Array:
	var v := PackedVector2Array()
	for i in n:
		v.append(Vector2.ZERO)
	return v


func _rand_blink_gap() -> float:
	return randf_range(blink_min_gap, blink_max_gap)


func _process(delta: float) -> void:
	_t += delta

	# Springs: any expression eases into any other; interrupts stay smooth.
	var stiff := spring_stiffness
	for key in ["left_eye", "right_eye", "mouth"]:
		var ci: PackedVector2Array = _cur[key]
		var vi: PackedVector2Array = _vel[key]
		var ti: PackedVector2Array = _tgt[key]
		for i in ci.size():
			var dv := (ti[i] - ci[i]) * stiff - vi[i] * spring_damping
			vi[i] += dv * delta
			ci[i] += vi[i] * delta
		_cur[key] = ci
		_vel[key] = vi

	# Additive idle life.
	_blink_at -= delta
	if _blink > 0.0:
		_blink -= delta * 4.4
		if _blink < -0.20:
			_blink = 0.0  # blink cycle finished
	elif _blink_at <= 0.0:
		_blink_at = _rand_blink_gap()
		_blink = 0.20  # start a blink (writes both ways of the triangle)

	_burst = max(0.0, _burst - delta)
	_yoff = lerpf(_yoff, float(_meta.get("dy", 0.0)), 1.0 - exp(-6.0 * delta))
	queue_redraw()


func _draw() -> void:
	if _glitching and _burst <= 0.0:
		# Scribble face: draw procedural static scribbles (pure CRT chaos).
		_draw_scribble()
		return
	var breath := 0.92 + 0.08 * sin(_t * 1.35)          # breathing glow
	var col := Color(0.95, 0.92, 0.9, breath)
	var gaze := Vector2(sin(_t * 0.37) * 2.5, sin(_t * 0.21) * 1.5)
	var jitter := float(_meta.get("jitter", 0.0))
	var shake := Vector2.ZERO
	if jitter > 0.0:
		shake = Vector2(randf_range(-jitter, jitter), randf_range(-jitter, jitter))

	# Cheap phosphor-ish halo: same glyphs, enlarged, far dimmer, behind.
	var halo := Color(col.r, col.g, col.b, col.a * 0.13)
	_draw_feature(_cur["left_eye"], halo, gaze + shake, _blink_collapse(), 1.22)
	_draw_feature(_cur["right_eye"], halo, gaze + shake, _blink_collapse(), 1.22)
	_draw_feature(_cur["mouth"], halo, shake, 1.0, 1.22)
	# Bodies.
	_draw_feature(_cur["left_eye"], col, gaze + shake, _blink_collapse(), 1.0)
	_draw_feature(_cur["right_eye"], col, gaze + shake, _blink_collapse(), 1.0)
	_draw_feature(_cur["mouth"], col, shake, 1.0, 1.0)

	if _burst > 0.0:
		_draw_static(_burst / 0.16)


func _blink_collapse() -> float:
	# Triangle: open (1.0) -> closed (0.06) -> open. Eyes squash to a thin
	# line around the eye-row center (spec: blink collapses to line).
	if _blink <= 0.0:
		return 1.0
	return lerpf(0.06, 1.0, absf(_blink) / 0.20)


## Renders a feature's flat 16-point array = 4 quads of 4 points. Each quad
## is drawn as two 3-point triangles whose diagonal is chosen by which split
## covers more |area| — and any individual triangle whose area is ~0 is
## skipped. Guarantees the rasterizer's earcut only ever sees a valid
## 3-point polygon, no matter how wild the spring morph gets mid-flight.
func _draw_feature(pts: PackedVector2Array, col: Color, off: Vector2, squash_y: float, scale: float) -> void:
	var row := FaceAtlasScript.EY
	for qi in range(0, pts.size(), 4):
		var a := Vector2(pts[qi].x * scale, row + (pts[qi].y - row) * squash_y * scale) + off
		var b := Vector2(pts[qi + 1].x * scale, row + (pts[qi + 1].y - row) * squash_y * scale) + off
		var c := Vector2(pts[qi + 2].x * scale, row + (pts[qi + 2].y - row) * squash_y * scale) + off
		var d := Vector2(pts[qi + 3].x * scale, row + (pts[qi + 3].y - row) * squash_y * scale) + off
		var abc := absf((b - a).cross(c - a))
		var acd := absf((c - a).cross(d - a))
		var abd := absf((b - a).cross(d - a))
		var bcd := absf((c - b).cross(d - b))
		if abd + bcd > abc + acd:
			if abd >= 0.01:
				draw_polygon(PackedVector2Array([a, b, d]), [col])
			if bcd >= 0.01:
				draw_polygon(PackedVector2Array([b, c, d]), [col])
		else:
			if abc >= 0.01:
				draw_polygon(PackedVector2Array([a, b, c]), [col])
			if acd >= 0.01:
				draw_polygon(PackedVector2Array([a, c, d]), [col])


func _draw_scribble() -> void:
	# Hand-jittered scribble strokes: twitchy, organic, un-geometric chaos.
	var col := Color(0.95, 0.92, 0.9, 0.85)
	for stroke in 12:
		var p := Vector2(randf_range(-70, 70), randf_range(-55, 40))
		var q := p + Vector2(randf_range(-50, 50), randf_range(-35, 35))
		draw_line(p, q, col, randf_range(1.0, 3.0))
		var r := q + Vector2(randf_range(-40, 40), randf_range(-28, 28))
		draw_line(q, r, col, randf_range(1.0, 3.0))


func _draw_static(strength: float) -> void:
	# Static burst: short horizontal noise tiles (CRT de-sync feel).
	var n := int(140.0 * strength)
	for i in n:
		var y := randf_range(-70.0, 60.0)
		var w := randf_range(3.0, 40.0)
		var x := randf_range(-90.0, 90.0)
		draw_line(Vector2(x, y), Vector2(x + w, y),
				Color(0.9, 0.9, 0.9, randf_range(0.05, 0.5)), randf_range(1.0, 2.5))


# Debug support: cycle expressions when the window is focused (F8/F9 later).
func names() -> Array:
	return _exprs.keys()


func debug_cycle(step: int) -> void:
	var keys: Array = _exprs.keys()
	keys.sort()
	var i: int = keys.find(_current_name)
	set_expression(String(keys[wrapi(i + step, 0, keys.size())]))
