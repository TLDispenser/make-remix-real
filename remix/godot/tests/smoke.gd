extends SceneTree
# Headless smoke test for Remix's Godot side.
#   Godot_v4.7 --headless --path godot --script res://tests/smoke.gd
# Requires the brain (remix/brain/run.sh) running; PASS requires it ONLINE.
# Checks: scene loads, springs morph the face (measured in a controlled
# window where main's WS handling is paused so mock moods can't interfere),
# WebSocket reaches OPEN, WS actually pushes mood changes through.

var frames := 0
var main_node: Node
var start_pt := Vector2.ZERO
var measured_diff := -1.0
var ws_open_seen := false


func _initialize() -> void:
	main_node = load("res://scenes/main.tscn").instantiate()
	root.add_child(main_node)


func _process(_d: float) -> bool:
	frames += 1
	if frames == 8:
		# Controlled morph window: pause main so brain moods don't interfere.
		main_node.set_process(false)
		start_pt = main_node.get_node("%Face")._cur["left_eye"][2]
		main_node.get_node("%Face").set_expression("happy")
	if frames == 40:  # mid/late morph must have moved the glyph
		var now: Vector2 = main_node.get_node("%Face")._cur["left_eye"][2]
		measured_diff = (now - start_pt).length()
	if frames == 41:
		main_node.set_process(true)
	if frames > 250:
		for key in ["left_eye", "right_eye", "mouth"]:
			pass
		if frames == 251:
			main_node._ws.poll()
			ws_open_seen = main_node._ws.get_ready_state() == WebSocketPeer.STATE_OPEN
			var moods: Array = main_node._moods_seen
			print("WS_STATE=", main_node._ws.get_ready_state())
			print("SPRINGS_MOVED=", measured_diff > 2.0, " (", snappedf(measured_diff, 0.1), " px)")
			print("MOODS_PUSHED=", moods.size(), " moods=", moods)
			print("SMOKE_HEADLESS_OK=", (ws_open_seen and measured_diff > 2.0
					and moods.size() >= 2))
			quit()
	return false
