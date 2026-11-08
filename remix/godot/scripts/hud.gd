# hud.gd: the only non-face things on screen.
#   - a small mic-live marker (pale blue-white, so it can never be mistaken for a mood)
#   - a quiet notice line (word-wrapped), ONLY when the brain asks for one
#     (muted / volume low / subtitles when she has no voice)
#   - a dim "heard" line at the top: what the mic caught, or what you just typed
#   - a text-input bar (Enter opens it, Enter sends, Esc closes)
#   - an orange pulsing FRAME around her whole window while she is "urgent" (the
#     brain raises it when she has been muted or ignored too long). It is the
#     in-window twin of a taskbar button turning orange; main.gd also asks the OS
#     for the real thing (window_request_attention).
# Remix does not otherwise show text. Everything is square-cornered (pixel art).
extends Control

signal text_submitted(text: String)
signal typing()          # fires (throttled) while you type, for the instant "thinking" reflex

const MIC_COLOR := Color("#cfe0ff")
const BAR_H := 40.0
const MARGIN := 24.0
const TYPING_THROTTLE_MS := 1500

var _mic_on := false
var _notice: Label
var _notice_left := 0.0
var _heard: Label
var _heard_left := 0.0
var _edit: LineEdit
var _t := 0.0
var _theme := Color("#ff5148")
var _last_typing_ms := -100000
const URGENT_COLOR := Color("#ff9a1f")   # taskbar-orange, deliberately NOT the face theme
const URGENT_BORDER := 8.0

var _urgent_on := false
var _urgent_t := 0.0


func _ready() -> void:
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	var font := SystemFont.new()
	font.font_names = PackedStringArray(["JetBrainsMono NFM", "JetBrains Mono", "monospace"])

	_notice = Label.new()
	_notice.add_theme_font_override("font", font)
	_notice.add_theme_font_size_override("font_size", 22)
	_notice.add_theme_color_override("font_color", _theme)
	_notice.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_notice.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	# Anchored just above the input bar; wrapped text grows UPWARD.
	_notice.anchor_left = 0.0
	_notice.anchor_right = 1.0
	_notice.anchor_top = 1.0
	_notice.anchor_bottom = 1.0
	_notice.offset_left = MARGIN
	_notice.offset_right = -MARGIN
	_notice.offset_bottom = -(BAR_H + MARGIN + 12.0)
	_notice.offset_top = _notice.offset_bottom - 28.0
	_notice.grow_vertical = Control.GROW_DIRECTION_BEGIN
	_notice.modulate.a = 0.0
	_notice.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(_notice)

	_heard = Label.new()
	_heard.add_theme_font_override("font", font)
	_heard.add_theme_font_size_override("font_size", 18)
	_heard.add_theme_color_override("font_color", MIC_COLOR)
	_heard.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_heard.anchor_right = 1.0
	_heard.offset_left = MARGIN
	_heard.offset_right = -80.0       # leave the mic marker's corner alone
	_heard.offset_top = 16.0
	_heard.offset_bottom = 44.0
	_heard.modulate.a = 0.0
	_heard.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(_heard)

	_edit = LineEdit.new()
	_edit.add_theme_font_override("font", font)
	_edit.add_theme_font_size_override("font_size", 22)
	_edit.placeholder_text = "say something to her  (Enter sends, Esc closes)"
	_edit.anchor_left = 0.0
	_edit.anchor_right = 1.0
	_edit.anchor_top = 1.0
	_edit.anchor_bottom = 1.0
	_edit.offset_left = MARGIN
	_edit.offset_right = -MARGIN
	_edit.offset_bottom = -MARGIN
	_edit.offset_top = -(MARGIN + BAR_H)
	_edit.max_length = 400
	_edit.context_menu_enabled = false
	_edit.visible = false
	_edit.text_submitted.connect(_on_submit)
	_edit.text_changed.connect(_on_changed)
	add_child(_edit)
	_restyle_input()


func _restyle_input() -> void:
	# Flat, square, theme-colored: matches the pixel-art screen (no rounded corners).
	var box := StyleBoxFlat.new()
	box.bg_color = Color(0.03, 0.04, 0.07, 0.96)
	box.set_corner_radius_all(0)
	box.set_border_width_all(2)
	box.border_color = _theme
	box.content_margin_left = 12.0
	box.content_margin_right = 12.0
	box.content_margin_top = 4.0
	box.content_margin_bottom = 4.0
	for st in ["normal", "focus", "read_only"]:
		_edit.add_theme_stylebox_override(st, box)
	_edit.add_theme_color_override("font_color", _theme)
	_edit.add_theme_color_override("caret_color", _theme)
	_edit.add_theme_color_override("font_placeholder_color", Color(_theme.r, _theme.g, _theme.b, 0.45))
	_edit.add_theme_color_override("selection_color", Color(_theme.r, _theme.g, _theme.b, 0.35))


## Orange pulsing frame around her window while she needs attention. `reason`
## is the brain's trigger name ("muted_long", "idle_deep"); the frame has no text.
func set_urgent(on: bool, _reason: String = "") -> void:
	_urgent_on = on
	_urgent_t = 0.0
	queue_redraw()


func urgent_visible() -> bool:
	return _urgent_on


func set_mic(on: bool) -> void:
	_mic_on = on
	queue_redraw()


func set_theme_color(c: Color) -> void:
	_theme = c
	_notice.add_theme_color_override("font_color", c)
	_restyle_input()


## Show a short line (e.g. "// you're muted //") for `seconds`, then fade out.
func show_notice(text: String, seconds: float = 4.0) -> void:
	_notice.text = text
	_notice_left = maxf(seconds, 0.5)
	_notice.modulate.a = 1.0


func notice_visible() -> bool:
	return _notice.modulate.a > 0.01


## Dim top line: what the mic heard / what you just typed.
func show_heard(text: String, seconds: float = 3.5) -> void:
	_heard.text = text
	_heard_left = maxf(seconds, 0.5)
	_heard.modulate.a = 1.0


func heard_visible() -> bool:
	return _heard.modulate.a > 0.01


# ------------------------------------------------------------------ text input

func input_open() -> bool:
	return _edit.visible


func open_input() -> void:
	if _edit.visible:
		return
	_edit.text = ""
	_edit.visible = true
	_edit.grab_focus()


func close_input() -> void:
	if not _edit.visible:
		return
	_edit.release_focus()
	_edit.visible = false
	_edit.text = ""


func _on_submit(text: String) -> void:
	var t := text.strip_edges()
	close_input()
	if t != "":
		text_submitted.emit(t)


func _on_changed(_text: String) -> void:
	var now := Time.get_ticks_msec()
	if now - _last_typing_ms >= TYPING_THROTTLE_MS:
		_last_typing_ms = now
		typing.emit()


func _process(delta: float) -> void:
	_t += delta
	_notice_left = _fade(_notice, _notice_left, delta)
	_heard_left = _fade(_heard, _heard_left, delta)
	if _urgent_on:
		_urgent_t += delta
	if _mic_on or _urgent_on:
		queue_redraw()


func _fade(label: Label, left: float, delta: float) -> float:
	if left > 0.0:
		left -= delta
		if left < 0.6:
			label.modulate.a = clampf(left / 0.6, 0.0, 1.0)
	elif label.modulate.a > 0.0:
		label.modulate.a = 0.0
	return left


func _draw() -> void:
	if _urgent_on:
		var a := 0.45 + 0.55 * (0.5 + 0.5 * sin(_urgent_t * 4.0))
		var c := Color(URGENT_COLOR.r, URGENT_COLOR.g, URGENT_COLOR.b, a)
		# Inset by half the stroke so the whole border stays inside the window.
		draw_rect(Rect2(Vector2.ONE * URGENT_BORDER * 0.5, size - Vector2.ONE * URGENT_BORDER),
				c, false, URGENT_BORDER)
	if not _mic_on:
		return
	var pulse := 0.75 + 0.25 * sin(_t * 6.0)
	var mc := Color(MIC_COLOR.r, MIC_COLOR.g, MIC_COLOR.b, pulse)
	var p := Vector2(size.x - 34.0, 34.0)
	draw_circle(p, 9.0, mc)
	draw_arc(p, 16.0, 0.0, TAU, 32, Color(mc.r, mc.g, mc.b, 0.45), 2.0, true)
