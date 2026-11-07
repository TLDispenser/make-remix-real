extends SceneTree
# Message handling without a socket: feed raw JSON straight to main._handle().
#   godot --headless --path . --script res://tests/protocol_test.gd

var fails := 0


func check(ok: bool, what: String) -> void:
	if not ok:
		fails += 1
		print("FAIL ", what)


func _initialize() -> void:
	var main: Node = load("res://scenes/main.tscn").instantiate()
	root.add_child(main)
	var face: Node2D = main.get_node("%Face")
	var hud: Control = main.get_node("%Hud")
	await process_frame

	main._handle('{"type":"pose","pose":"anger","look":[0.5,-0.5]}')
	check(face.pose_name == "anger" and face.look.is_equal_approx(Vector2(0.5, -0.5)), "pose + look")
	main._handle('{"type":"mood","mood":"stressed","confidence":0.9}')
	check(face.pose_name == "spooked", "legacy mood 'stressed' -> spooked")
	main._handle('{"type":"mood","mood":"sleepy"}')
	check(face.pose_name == "tired", "legacy mood 'sleepy' -> tired")
	main._handle('{"type":"pose","pose":"nonsense"}')
	check(face.pose_name == "default", "unknown pose -> default")
	main._handle('{"type":"look","look":[2,0]}')
	check(face.look.length() <= 1.0001, "look is clamped")
	main._handle('{"type":"mouth","level":0.8}')
	await process_frame
	await process_frame
	check(face.speaking and face.level > 0.0, "external mouth level drives the mouth")
	main._handle('{"type":"listening","state":"start"}')
	check(hud._mic_on, "mic marker on")
	main._handle('{"type":"listening","state":"stop"}')
	check(not hud._mic_on, "mic marker off")
	main._handle('{"type":"notice","text":"// you are muted //","seconds":2}')
	check(hud.notice_visible(), "notice shows when asked")
	check(hud._notice.text == "// you are muted //", "notice text")
	main._handle('{"type":"theme","color":"#3366ff"}')
	check(face.theme_color.is_equal_approx(Color("#3366ff")), "theme color")
	main._handle('{"type":"say","text":"hello","final":true}')
	check(not hud.notice_visible() or hud._notice.text != "hello", "speech text is never displayed")
	# robustness
	main._handle("not json at all")
	main._handle('{"type":"wat"}')
	main._handle('[1,2,3]')
	main._handle('{"type":"pose"}')
	main._handle('{"type":"perform"}')
	check(true, "garbage input does not crash")

	print("PROTOCOL_%s fails=%d" % ["OK" if fails == 0 else "FAILED", fails])
	quit(1 if fails > 0 else 0)
