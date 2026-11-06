# main.gd — Core's display/body layer.
# Godot rule (spec): this file only (1) sets up the window, (2) connects the
# WebSocket to the Python brain, (3) re-broadcasts brain messages as signals,
# (4) starts a window drag. NO logic beyond that.
#
# Signals you hook for your own visuals:
#   vitals(data)             {"cpu":..,"ram":..,"temp":..,"battery":..,"disk":..}
#   mood_changed(name, conf) "idle|happy|stressed|..."
#   said(text, final)        streaming speech chunks; final=true = whole line
#   reflex_line(text)        instant canned line (before any LLM words)
#   event_fired(name)        "download_done" etc.
#   brain_online(open)      connection state changed
extends Control

signal vitals(data: Dictionary)
signal mood_changed(mood_name: String, confidence: float)
signal said(text: String, final: bool)
signal reflex_line(text: String)
signal event_fired(event_name: String)
signal brain_online(open: bool)

const WS_URL := "ws://localhost:8765"
const RECONNECT_DELAY := 2.0

var _ws := WebSocketPeer.new()
var _open := false
var _reconnect_timer := 0.0

@onready var bubble: PanelContainer = %Bubble
@onready var bubble_text: Label = %Bubble/Text
@onready var debug_label: Label = %Debug


func _ready() -> void:
	_setup_window()
	_ws.connect_to_url(WS_URL)
	bubble.hide()
	var avatar := get_node("%Avatar")
	vitals.connect(avatar.set_vitals)
	mood_changed.connect(func(mood_name: String, _conf: float) -> void:
		avatar.set_mood(mood_name))


func _setup_window() -> void:
	get_window().title = "Core"
	get_window().borderless = true
	# Docs list always_on_top as X11-only; harmless no-op under Wayland.
	# On Hyprland we pin from the compositor side instead (see README).
	get_window().always_on_top = true
	if not DisplayServer.is_window_transparency_available():
		# Degrade gracefully: opaque dark background instead of transparent.
		var bg := ColorRect.new()
		bg.name = "FallbackBg"
		bg.color = Color(0.08, 0.08, 0.12, 1.0)
		bg.set_anchors_preset(Control.PRESET_FULL_RECT)
		bg.show_behind_parent = true
		add_child(bg)
		move_child(bg, 0)


func _process(delta: float) -> void:
	if not _open:
		_reconnect_timer -= delta
		if _reconnect_timer <= 0.0:
			_reconnect_timer = RECONNECT_DELAY
			if _ws.get_ready_state() == WebSocketPeer.STATE_CLOSED:
				_ws = WebSocketPeer.new()
				_ws.connect_to_url(WS_URL)
	_ws.poll()
	match _ws.get_ready_state():
		WebSocketPeer.STATE_OPEN:
			_set_online(true)
			while _ws.get_available_packet_count() > 0:
				_handle(_ws.get_packet().get_string_from_utf8())
		WebSocketPeer.STATE_CONNECTING, WebSocketPeer.STATE_CLOSING:
			pass
		WebSocketPeer.STATE_CLOSED:
			if _open:
				_set_online(false)
			if _ws.get_close_code() != -1:
				_ws = WebSocketPeer.new()
				_ws.connect_to_url(WS_URL)


func _handle(raw: String) -> void:
	var msg: Variant = JSON.parse_string(raw)
	if typeof(msg) != TYPE_DICTIONARY:
		return
	var d: Dictionary = msg
	match String(d.get("type", "")):
		"vitals":
			vitals.emit(d)
		"mood":
			mood_changed.emit(String(d.get("mood", "idle")), float(d.get("confidence", 0.0)))
		"say":
			var text := String(d.get("text", ""))
			if text.is_empty():
				_clear_bubble()
			else:
				bubble_text.text = text
				bubble.show()
			said.emit(text, bool(d.get("final", true)))
		"reflex_line":
			var line := String(d.get("text", ""))
			bubble_text.text = line
			bubble.show()
			reflex_line.emit(line)
		"event":
			event_fired.emit(String(d.get("name", "")))
		_:
			pass


func _clear_bubble() -> void:
	bubble.hide()
	bubble_text.text = ""


func _set_online(open: bool) -> void:
	if _open == open:
		return
	_open = open
	debug_label.text = "brain online" if open else "brain offline"
	brain_online.emit(open)
	if not open:
		_clear_bubble()


# ---- shared helpers other scripts (or your visuals) may want ----

func send_json(msg: Dictionary) -> void:
	if _open:
		_ws.send_text(JSON.stringify(msg))


func set_cleared_line_hint() -> void:
	# Godot -> Python: user started typing (triggers instant "thinking" reflex).
	send_json({"v": 1, "type": "user_typing"})


# Drag: compositor-owned window move, works on Wayland + X11 (verified 4.7 docs).
func start_window_drag() -> void:
	DisplayServer.window_start_drag(get_window().get_window_id())


func _gui_input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.pressed \
			and event.button_index == MOUSE_BUTTON_LEFT:
		start_window_drag()
		accept_event()
