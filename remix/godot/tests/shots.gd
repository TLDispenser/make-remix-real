extends SceneTree
# Dev tool: renders every pose to a PNG so you can eyeball the real thing.
#   SHOT_DIR=/tmp/remix_shots godot --path . --script res://tests/shots.gd
# Needs a real display (it will not work with --headless). Under a server with
# no screen: xvfb-run -a godot --path . --rendering-driver opengl3 --script res://tests/shots.gd

func _initialize() -> void:
	var dir := OS.get_environment("SHOT_DIR")
	if dir == "":
		dir = "/tmp/remix_shots"
	DirAccess.make_dir_recursive_absolute(dir)
	var main: Node = load("res://scenes/main.tscn").instantiate()
	root.add_child(main)
	var face: Node2D = main.get_node("%Face")
	main.set_process(false)
	face.set_process(false)
	await process_frame
	for name: String in face.poses():
		face.set_pose(name)
		for i in 150:
			face._process(1.0 / 60.0)
		face._blink_p = -1.0
		await process_frame
		await process_frame
		root.get_texture().get_image().save_png("%s/%s.png" % [dir, name])
		print("shot ", name)
	# mixed faces: a few eyes x mouth pairs the 7 original drawings never showed together
	for pair in [["really", "smirk"], ["tired", "default"], ["tsun", "pout"], ["anger", "trauma"],
			["spooked", "smirk"], ["bottomlid", "anger"], ["really", "default"]]:
		face.set_face(pair[0], pair[1], Vector2.ZERO)
		for i in 150:
			face._process(1.0 / 60.0)
		face._blink_p = -1.0
		await process_frame
		await process_frame
		root.get_texture().get_image().save_png("%s/mix_%s_%s.png" % [dir, pair[0], pair[1]])
		print("shot mix ", pair)
	# extras: talking mouth, looking aside, mic marker, notice
	face.set_pose("default", Vector2(1, -0.5))
	face.set_speaking(true); face.set_level(1.0)
	for i in 150:
		face._process(1.0 / 60.0)
	await process_frame; await process_frame
	root.get_texture().get_image().save_png(dir + "/default_talking_look.png")
	main.get_node("%Hud").set_mic(true)
	main.get_node("%Hud").show_notice("// you're muted //", 30.0)
	await process_frame; await process_frame
	root.get_texture().get_image().save_png(dir + "/hud.png")
	# long wrapped notice + heard line + open input bar
	var hud: Control = main.get_node("%Hud")
	hud.set_mic(false)
	face.set_pose("default")
	hud.show_notice("because you've got forty fucking tabs open and my disk is almost full. close some shit.", 30.0)
	hud.show_heard("you: why is my pc so slow", 30.0)
	hud.open_input()
	hud._edit.text = "no u"
	await process_frame; await process_frame; await process_frame
	root.get_texture().get_image().save_png(dir + "/hud_input.png")
	print("SHOTS_DONE")
	quit()
