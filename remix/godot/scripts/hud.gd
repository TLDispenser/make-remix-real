# hud.gd: the only non-face things on screen.
#   - a small mic-live marker (pale blue-white, so it can never be mistaken for a mood)
#   - a quiet notice line, ONLY when the brain asks for one (muted / volume low).
# Remix does not otherwise show text.
extends Control

const MIC_COLOR := Color("#cfe0ff")

var _mic_on := false
var _notice: Label
var _notice_left := 0.0
var _t := 0.0


func _ready() -> void:
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	var font := SystemFont.new()
	font.font_names = PackedStringArray(["JetBrainsMono NFM", "JetBrains Mono", "monospace"])
	_notice = Label.new()
	_notice.add_theme_font_override("font", font)
	_notice.add_theme_font_size_override("font_size", 22)
	_notice.add_theme_color_override("font_color", Color("#ff5148"))
	_notice.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_notice.set_anchors_and_offsets_preset(Control.PRESET_BOTTOM_WIDE)
	_notice.offset_top = -52.0
	_notice.offset_bottom = -22.0
	_notice.modulate.a = 0.0
	_notice.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(_notice)


func set_mic(on: bool) -> void:
	_mic_on = on
	queue_redraw()


func set_theme_color(c: Color) -> void:
	_notice.add_theme_color_override("font_color", c)


## Show a short line (e.g. "// you're muted //") for `seconds`, then fade out.
func show_notice(text: String, seconds: float = 4.0) -> void:
	_notice.text = text
	_notice_left = maxf(seconds, 0.5)
	_notice.modulate.a = 1.0


func notice_visible() -> bool:
	return _notice.modulate.a > 0.01


func _process(delta: float) -> void:
	_t += delta
	if _notice_left > 0.0:
		_notice_left -= delta
		if _notice_left < 0.6:
			_notice.modulate.a = clampf(_notice_left / 0.6, 0.0, 1.0)
	elif _notice.modulate.a > 0.0:
		_notice.modulate.a = 0.0
	if _mic_on:
		queue_redraw()


func _draw() -> void:
	if not _mic_on:
		return
	var pulse := 0.75 + 0.25 * sin(_t * 6.0)
	var c := Color(MIC_COLOR.r, MIC_COLOR.g, MIC_COLOR.b, pulse)
	var p := Vector2(size.x - 34.0, 34.0)
	draw_circle(p, 9.0, c)
	draw_arc(p, 16.0, 0.0, TAU, 32, Color(c.r, c.g, c.b, 0.45), 2.0, true)
