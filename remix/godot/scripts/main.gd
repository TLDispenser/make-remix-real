# main.gd: Remix's display layer. No decisions are made here.
#
# It (1) sets up the window, (2) talks to the Python brain over a local
# WebSocket, (3) plays the audio the brain hands it and fires the brain's pose
# cues against the audio's own playback clock (so face and voice can't drift),
# and (4) feeds the live audio level to the mouth.
#
# Protocol v2 is documented in PROTOCOL.md. Short version, brain -> Godot:
#   {"type":"pose","pose":"anger","look":[0.3,-0.2]}      silent pose change
#   {"type":"perform","id":"p1","audio":"/tmp/x.wav",
#    "cues":[{"t":0.0,"pose":"anger"},{"t":1.8,"pose":"tired","look":[0,0.3]}],
#    "then":{"pose":"default"}}                            speak with acting
#   {"type":"look","look":[x,y]}  {"type":"mouth","level":0..1}  {"type":"twitch"}
#   {"type":"listening","state":"start"|"stop"}  {"type":"notice","text":"..."}
# Old {"type":"mood","mood":"stressed"} messages still work (mapped to poses).
extends Control

signal brain_online(open: bool)
signal vitals(data: Dictionary)
signal event_fired(event_name: String)
signal performance_started(id: String)
signal performance_done(id: String, ok: bool)

const WS_URL := "ws://localhost:8765"
const PROTOCOL := 2
const RECONNECT_DELAY := 2.0
const VOICE_BUS := "Voice"
const EXTERNAL_LEVEL_HOLD := 0.25   # seconds an external {"type":"mouth"} level stays "live"

@export var base_window := Vector2i(640, 360)
@export var fps_idle := 30
@export var fps_active := 60

@onready var face: Node2D = %Face
@onready var hud: Control = %Hud

var _ws := WebSocketPeer.new()
var _open := false
var _reconnect_timer := 0.0

var _voice: AudioStreamPlayer
var _voice_bus := -1
var _window_scale := 1.0

# performance (audio + cues)
var _perf_active := false
var _perf_id := ""
var _perf_cues: Array = []
var _perf_next := 0
var _perf_then: Dictionary = {}
var _perf_last_pos := 0.0

var _ext_level := 0.0
var _ext_level_left := 0.0
var _fps_timer := 0.0

var log_lines: Array = []   # last few protocol events, for tests/debug


func _ready() -> void:
	_setup_window()
	_setup_audio()
	_ws.connect_to_url(WS_URL)


func _setup_window() -> void:
	var w := get_window()
	w.title = "Remix"   # fixed title: the Hyprland window rule matches on it
	w.borderless = true
	# always_on_top is X11-only; on Hyprland the compositor pins her (tools/hyprland-setup.md).
	w.always_on_top = true
	w.size = base_window
	Engine.max_fps = fps_idle


func _setup_audio() -> void:
	_voice_bus = AudioServer.get_bus_index(VOICE_BUS)
	if _voice_bus == -1:
		AudioServer.add_bus()
		_voice_bus = AudioServer.bus_count - 1
		AudioServer.set_bus_name(_voice_bus, VOICE_BUS)
		AudioServer.set_bus_send(_voice_bus, "Master")
	_voice = AudioStreamPlayer.new()
	_voice.bus = VOICE_BUS
	add_child(_voice)
	_voice.finished.connect(func() -> void: _finish_performance(true))


# ------------------------------------------------------------------- main loop

func _process(delta: float) -> void:
	_poll_socket(delta)
	_tick_performance()
	_tick_external_level(delta)
	_fps_timer -= delta
	if _fps_timer <= 0.0:
		_fps_timer = 0.25
		Engine.max_fps = fps_active if face.is_active() else fps_idle


func _poll_socket(delta: float) -> void:
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
		WebSocketPeer.STATE_CLOSED:
			if _open:
				_set_online(false)


func _set_online(open: bool) -> void:
	if _open == open:
		return
	_open = open
	brain_online.emit(open)
	if open:
		send_json({"v": PROTOCOL, "type": "ready", "poses": face.poses(),
			"moods": FaceConfig.ALIASES.keys()})
	else:
		_finish_performance(false)
		hud.set_mic(false)


func send_json(msg: Dictionary) -> void:
	if _open:
		_ws.send_text(JSON.stringify(msg))


# -------------------------------------------------------------------- protocol

func _vec(v: Variant) -> Variant:
	if v is Array and (v as Array).size() == 2:
		return Vector2(float(v[0]), float(v[1]))
	return null


func _handle(raw: String) -> void:
	var parser := JSON.new()
	if parser.parse(raw) != OK or typeof(parser.data) != TYPE_DICTIONARY:
		return  # malformed input is ignored quietly (no engine error spam)
	var d: Dictionary = parser.data
	var kind := String(d.get("type", ""))
	_log(kind)
	match kind:
		"pose":
			face.set_pose(String(d.get("pose", "default")), _vec(d.get("look")))
		"mood":  # legacy protocol
			face.set_pose(String(d.get("mood", "idle")), _vec(d.get("look")))
		"look":
			var l: Variant = _vec(d.get("look"))
			if l != null:
				face.set_look(l)
		"perform":
			_start_performance(d)
		"stop":
			_finish_performance(false)
		"mouth":
			_ext_level = clampf(float(d.get("level", 0.0)), 0.0, 1.0)
			_ext_level_left = EXTERNAL_LEVEL_HOLD
		"twitch":
			face.twitch()
		"listening":
			hud.set_mic(String(d.get("state", "stop")) == "start")
		"notice":
			hud.show_notice(String(d.get("text", "")), float(d.get("seconds", 4.0)))
		"theme":
			var c := Color.from_string(String(d.get("color", "")), Color(0, 0, 0, 0))
			if c.a > 0.0:
				face.set_theme_color(c)
				hud.set_theme_color(c)
		"vitals":
			vitals.emit(d)
		"event":
			event_fired.emit(String(d.get("name", "")))
		_:
			pass  # "say", "reflex_line" and unknown types: nothing is displayed


func _log(kind: String) -> void:
	log_lines.append(kind)
	if log_lines.size() > 30:
		log_lines.pop_front()


# ----------------------------------------------------------------- performance

func _start_performance(d: Dictionary) -> void:
	_finish_performance(false)  # a new performance replaces any running one
	_perf_id = String(d.get("id", ""))
	_perf_cues = (d.get("cues", []) as Array).duplicate()
	_perf_cues.sort_custom(func(a: Variant, b: Variant) -> bool: return float(a.get("t", 0.0)) < float(b.get("t", 0.0)))
	_perf_next = 0
	_perf_then = d.get("then", {})
	_perf_last_pos = 0.0
	var path := String(d.get("audio", ""))
	var stream: AudioStreamWAV = WavLoader.load_file(path) if path != "" else null
	if stream == null:
		# Nothing to play: run all cues at once, finish, and tell the brain why.
		_perf_active = true
		_fire_due_cues(INF)
		_finish_performance(false, "audio")
		return
	_perf_active = true
	_voice.stream = stream
	_voice.play()
	face.set_speaking(true)
	performance_started.emit(_perf_id)
	_tick_performance()  # fire t=0 cues this frame


func _playback_pos() -> float:
	var p := _voice.get_playback_position() + AudioServer.get_time_since_last_mix() \
			- AudioServer.get_output_latency()
	_perf_last_pos = maxf(_perf_last_pos, p)  # never run backwards
	return _perf_last_pos


func _tick_performance() -> void:
	if not _perf_active:
		return
	_fire_due_cues(_playback_pos())
	face.set_level(_audio_level())


func _fire_due_cues(pos: float) -> void:
	while _perf_next < _perf_cues.size() and float(_perf_cues[_perf_next].get("t", 0.0)) <= pos:
		_apply_cue(_perf_cues[_perf_next])
		_perf_next += 1


func _apply_cue(c: Dictionary) -> void:
	if c.has("pose"):
		face.set_pose(String(c["pose"]), _vec(c.get("look")))
	elif c.has("look"):
		var l: Variant = _vec(c["look"])
		if l != null:
			face.set_look(l)
	if bool(c.get("twitch", false)):
		face.twitch()


func _finish_performance(ok: bool, error: String = "") -> void:
	if not _perf_active:
		return
	_perf_active = false
	var id := _perf_id
	if _voice.playing:
		_voice.stop()
	face.set_speaking(false)
	face.set_level(0.0)
	if not _perf_then.is_empty():
		face.set_pose(String(_perf_then.get("pose", "default")), _vec(_perf_then.get("look")))
	var report := {"v": PROTOCOL, "type": "perform_done", "id": id, "ok": ok}
	if error != "":
		report["error"] = error
	send_json(report)
	performance_done.emit(id, ok)
	_perf_then = {}


func _audio_level() -> float:
	var db := maxf(AudioServer.get_bus_peak_volume_left_db(_voice_bus, 0),
			AudioServer.get_bus_peak_volume_right_db(_voice_bus, 0))
	return pow(clampf(inverse_lerp(-42.0, -8.0, db), 0.0, 1.0), 0.7)


## Mouth level from the brain (e.g. streaming TTS it measures itself) when no
## audio file is playing from here.
func _tick_external_level(delta: float) -> void:
	if _perf_active:
		return
	if _ext_level_left > 0.0:
		_ext_level_left -= delta
		face.set_speaking(true)
		face.set_level(_ext_level)
		if _ext_level_left <= 0.0:
			face.set_speaking(false)
			face.set_level(0.0)


# ----------------------------------------------------------------------- input

func _gui_input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.pressed:
		match event.button_index:
			MOUSE_BUTTON_LEFT:
				DisplayServer.window_start_drag(get_window().get_window_id())
				accept_event()
			MOUSE_BUTTON_WHEEL_UP:
				_rescale(0.1)
			MOUSE_BUTTON_WHEEL_DOWN:
				_rescale(-0.1)


func _rescale(step: float) -> void:
	_window_scale = clampf(_window_scale + step, 0.5, 2.0)
	var w := get_window()
	var center := w.position + w.size / 2
	w.size = Vector2i(Vector2(base_window) * _window_scale)
	w.position = center - w.size / 2   # honored on X11; Hyprland rules decide on Wayland


# Debug keys (window must be focused): F8/F9 cycle poses, F10 twitch.
func _input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		match event.keycode:
			KEY_F8:
				face.debug_cycle(1)
			KEY_F9:
				face.debug_cycle(-1)
			KEY_F10:
				face.twitch()
