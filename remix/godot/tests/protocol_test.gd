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

	# eyes and mouth are separate fields, mixed freely
	main._handle('{"type":"pose","eyes":"tired","mouth":"smirk","look":[0.2,0.1]}')
	check(face.eyes_name == "tired" and face.mouth_name == "smirk", "eyes + mouth set independently")
	check(face.look.is_equal_approx(Vector2(0.2, 0.1)), "mixed face takes its look")
	main._handle('{"type":"pose","mouth":"pout"}')
	check(face.eyes_name == "tired" and face.mouth_name == "pout", "mouth-only message keeps the eyes")
	main._handle('{"type":"pose","eyes":"anger"}')
	check(face.eyes_name == "anger" and face.mouth_name == "pout", "eyes-only message keeps the mouth")
	main._handle('{"type":"pose","eyes":"bogus","mouth":"bogus"}')
	check(face.eyes_name == "anger" and face.mouth_name == "pout", "unknown eyes/mouth are ignored, face unchanged")
	main._handle('{"type":"pose","pose":"smirk","mouth":"tsun"}')
	check(face.eyes_name == "bottomlid" and face.mouth_name == "tsun", "pose shortcut then a per-unit override")
	main._handle('{"type":"mood","mood":"deadpan"}')
	check(face.eyes_name == "really" and face.mouth_name == "default", "mixed-face mood alias")
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
	# urgent: a real OS attention request (taskbar flash / bar's urgent class),
	# raised/cleared by the brain. Nothing is drawn on her window.
	# (Headless: nothing has focus, so the request goes out.)
	check(not hud.urgent_visible(), "urgent starts off")
	main.focus_probe = func() -> bool: return true     # you are looking at her
	var asked_f: int = main.attention_requests
	main._handle('{"type":"urgent","state":true,"reason":"idle_deep"}')
	check(hud.urgent_visible(), "urgent state shows even when she is focused")
	check(main.attention_requests == asked_f, "no taskbar request while she is the focused window")
	main._handle('{"type":"urgent","state":false}')
	main.focus_probe = func() -> bool: return false    # she is in the background
	var asked0: int = main.attention_requests
	main._handle('{"type":"urgent","state":true,"reason":"muted_long"}')
	check(hud.urgent_visible(), "urgent state shows")
	check(main.attention_requests == asked0 + 1, "urgent asks the OS for attention (taskbar)")
	main._tick_attention(10.0)
	check(main.attention_requests == asked0 + 1, "it does not nag every few seconds")
	main._tick_attention(main.ATTENTION_REPEAT_S)
	check(main.attention_requests == asked0 + 2, "it re-asks after the repeat interval while still ignored")
	main._handle('{"type":"urgent","state":false}')
	check(not hud.urgent_visible(), "urgent clears")
	main._tick_attention(1000.0)
	check(main.attention_requests == asked0 + 2, "no more requests once cleared")
	main._handle('{"type":"urgent","state":false}')
	check(main.attention_requests == asked0 + 2, "clearing never asks for attention")
	# what the mic heard shows on the dim top line, and is not the notice
	main._handle('{"type":"transcript","text":"hello there"}')
	check(hud.heard_visible() and hud._heard.text == "you: hello there", "transcript shows on the heard line")
	main._handle('{"type":"transcript","text":"   "}')
	check(hud._heard.text == "you: hello there", "blank transcript is ignored")
	main._handle('{"type":"theme","color":"#3366ff"}')
	check(face.theme_color.is_equal_approx(Color("#3366ff")), "theme color")
	main._handle('{"type":"say","text":"hello","final":true}')
	check(not hud.notice_visible() or hud._notice.text != "hello", "speech text is never displayed")
	# text input bar: opens, sends user_text, closes; typing is throttled
	var sent: Array = []
	main.send_json_hook = func(m: Dictionary) -> void: sent.append(m)
	check(not hud.input_open(), "input bar starts closed")
	hud.open_input()
	check(hud.input_open(), "input bar opens")
	hud._edit.text = "  yo remix  "
	hud._edit.text_submitted.emit(hud._edit.text)
	check(not hud.input_open(), "input bar closes on send")
	var ut: Array = sent.filter(func(m: Dictionary) -> bool: return m["type"] == "user_text")
	check(ut.size() == 1 and ut[0]["text"] == "yo remix", "user_text is sent trimmed (%s)" % str(ut))
	hud.open_input(); hud._edit.text_submitted.emit("   ")
	check(sent.filter(func(m: Dictionary) -> bool: return m["type"] == "user_text").size() == 1, "blank line is not sent")
	var types_before := sent.size()
	hud.open_input()
	hud._on_changed("a"); hud._on_changed("ab"); hud._on_changed("abc")
	check(sent.size() == types_before + 1 and sent[-1]["type"] == "user_typing", "user_typing fires once per burst")
	check(sent[-1].get("text", "") == "a", "user_typing carries the partial line (leading edge)")
	hud._edit.text = "abc"
	hud._flush_typing()
	check(sent[-1].get("type", "") == "user_typing" and sent[-1].get("text", "") == "abc", "the trailing edge sends the latest text")
	hud.close_input()
	check(sent[-1].get("type", "") == "user_typing" and sent[-1].get("text", "x") == "", "Esc (closing unsent) clears what she was listening to")

	# window fit: a quarter of the screen's area on any screen, face-shaped (16:9)
	var F: Callable = main.fit_window
	check(F.call(Vector2i(1920, 1080), 0.5, Vector2i(320, 180)) == Vector2i(960, 540), "1080p -> 960x540")
	check(F.call(Vector2i(1280, 720), 0.5, Vector2i(320, 180)) == Vector2i(640, 360), "720p -> 640x360")
	check(F.call(Vector2i(2560, 1440), 0.5, Vector2i(320, 180)) == Vector2i(1280, 720), "1440p -> 1280x720")
	check(F.call(Vector2i(3840, 2160), 0.25, Vector2i(320, 180)) == Vector2i(960, 540), "fraction is configurable")
	var tall: Vector2i = F.call(Vector2i(1080, 1920), 0.5, Vector2i(320, 180))
	check(tall.x <= 540 and absf(float(tall.x) / float(tall.y) - 16.0 / 9.0) < 0.02, "portrait screen still gets a 16:9 window that fits (%s)" % str(tall))
	check(F.call(Vector2i(400, 300), 0.5, Vector2i(320, 180)) == Vector2i(320, 180), "tiny screen never goes below the floor")
	check(F.call(Vector2i(0, 0), 0.5, Vector2i(320, 180)) == Vector2i(320, 180), "unknown screen -> floor size")

	# the face fills whatever canvas it gets: centered, scaled to fit, any shape
	main.size = Vector2(960, 540); main._layout()
	check(face.position.is_equal_approx(Vector2(480, 270)) and is_equal_approx(face.scale.x, 0.5), "960x540 canvas: scale 0.5, centered")
	main.size = Vector2(1920, 1080); main._layout()
	check(is_equal_approx(face.scale.x, 1.0) and face.position.is_equal_approx(Vector2(960, 540)), "bigger canvas: scales up")
	main.size = Vector2(1200, 540); main._layout()
	check(is_equal_approx(face.scale.x, 0.5) and face.position.is_equal_approx(Vector2(600, 270)), "wide canvas: height-limited, centered")
	main.size = Vector2(640, 800); main._layout()
	check(is_equal_approx(face.scale.x, 640.0 / 1920.0) and face.position.is_equal_approx(Vector2(320, 400)), "tall canvas: width-limited, centered")

	# she is told where she is
	var cx: Dictionary = main.context()
	check(cx["type"] == "context" and cx["window"].size() == 2 and cx["screen"].size() == 2 and cx.has("area_fraction"), "context carries window/screen facts")
	check(cx.has("pos") and cx["pos"].size() == 2 and cx.has("screen_count") and cx["screen_count"] >= 1, "context carries her position and screen count")

	# robustness
	main._handle("not json at all")
	main._handle('{"type":"wat"}')
	main._handle('[1,2,3]')
	main._handle('{"type":"pose"}')
	main._handle('{"type":"perform"}')
	check(true, "garbage input does not crash")

	print("PROTOCOL_%s fails=%d" % ["OK" if fails == 0 else "FAILED", fails])
	quit(1 if fails > 0 else 0)
