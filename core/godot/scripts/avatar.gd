# avatar.gd — PLACEHOLDER body so Phase 1 is visible without your art.
# Replace this node (or its rendering) with your own scene later; keep it
# listening to Main's signals the same way. Mood colors + pulse speed map:
#   CPU -> pulse speed, RAM -> size fullness, temp -> redness flush,
#   battery -> droopiness (Phase 2 gets the real mapping rules).
extends Node2D

const MOOD_COLORS := {
	"idle": Color(0.55, 0.65, 0.75),
	"happy": Color(0.35, 0.85, 0.5),
	"stressed": Color(0.95, 0.35, 0.25),
	"sleepy": Color(0.25, 0.3, 0.5),
	"thinking": Color(0.6, 0.4, 0.9),
	"listening": Color(0.2, 0.8, 0.9),
	"alert": Color(0.95, 0.85, 0.3),
	"bored": Color(0.45, 0.45, 0.5),
}

var _cpu := 0.0
var _ram := 0.0
var _temp := 40.0
var _battery := 1.0
var _mood := "idle"
var _phase := 0.0


func set_vitals(v: Dictionary) -> void:
	_cpu = float(v.get("cpu", _cpu))
	_ram = float(v.get("ram", _ram))
	_temp = float(v.get("temp", _temp))
	_battery = float(v.get("battery", _battery * 100.0)) / 100.0
	queue_redraw()


func set_mood(mood_name: String) -> void:
	if MOOD_COLORS.has(mood_name) and _mood != mood_name:
		_mood = mood_name
		queue_redraw()


func _process(delta: float) -> void:
	_phase += delta * (0.6 + (_cpu / 100.0) * 4.0)
	queue_redraw()


func _draw() -> void:
	var flush := clampf((_temp - 40.0) / 45.0, 0.0, 1.0)
	var col: Color = MOOD_COLORS.get(_mood, MOOD_COLORS["idle"]).lerp(
			Color(0.95, 0.25, 0.2), flush * 0.6)
	var droop := (1.0 - _battery) * 26.0
	var r := 60.0 + sin(_phase) * (2.0 + (_cpu / 100.0) * 6.0) + (_ram / 100.0) * 14.0
	draw_circle(Vector2(0, droop), r, col)
	draw_circle(Vector2(0, droop), r * 0.8, col.lightened(0.15))
