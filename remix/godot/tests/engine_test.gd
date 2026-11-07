extends SceneTree
# Face engine behaviour, stepped deterministically (no real-time waiting).
#   godot --headless --path . --script res://tests/engine_test.gd
# Also exercises the draw path; any engine "ERROR" in the output fails the run
# (the runner script greps for it).

var fails := 0
var face: Node2D


func check(ok: bool, what: String) -> void:
	if not ok:
		fails += 1
		print("FAIL ", what)


func step(frames: int) -> void:
	for i in frames:
		face._process(1.0 / 60.0)


func max_err(key: String, target: PackedVector2Array) -> float:
	var e := 0.0
	for i in 10:
		e = maxf(e, (face._cur[key][i] - target[i]).length())
	return e


func _initialize() -> void:
	var main: Node = load("res://scenes/main.tscn").instantiate()
	root.add_child(main)
	face = main.get_node("%Face")
	main.set_process(false)   # we drive time ourselves
	face.set_process(false)
	var shapes := FaceShapes.all()
	var names: Array = shapes.keys()
	await process_frame       # let _draw run once for real

	# 1) every pose settles on its shape, and any A->B morph stays finite mid-flight
	for a: String in names:
		face.set_pose(a)
		step(150)
		for k in ["left_eye", "right_eye", "mouth"]:
			check(max_err(k, shapes[a][k]) < 1.0, "%s/%s settles on its SVG shape" % [a, k])
		for b: String in names:
			if a == b:
				continue
			face.set_pose(a); step(120)
			face.set_pose(b); step(4)
			for k in ["left_eye", "right_eye", "mouth"]:
				for p: Vector2 in face._cur[k]:
					if not (is_finite(p.x) and is_finite(p.y)):
						check(false, "%s->%s %s went non-finite mid-morph" % [a, b, k])
		await process_frame   # draw a mid-morph frame too

	# 2) look limits per pose (the brain asks for the corner; the pose decides)
	for p: String in names:
		face.set_pose(p, Vector2(1, 0)); step(150)
		var lim: Vector2 = FaceConfig.POSES[p]["eye_range"]
		var s: Dictionary = face.snapshot()
		check(absf(s["eye_off"].x) <= lim.x + 0.01, "%s eye x within limit" % p)
		check(absf(s["eye_off"].x) >= lim.x - 1.0, "%s eye reaches its limit" % p)
		face.set_pose(p, Vector2(0, -1)); step(150)
		s = face.snapshot()
		check(absf(s["eye_off"].y) <= FaceConfig.POSES[p]["eye_range"].y + 0.01, "%s eye y within limit" % p)
		check(absf(s["mouth_off"].y) <= FaceConfig.POSES[p]["mouth_range"].y + 0.01, "%s mouth y within limit" % p)
		face.set_pose(p, Vector2(5, 5))
		check(face.look.length() <= 1.0001, "%s look is clamped to unit length" % p)

	# 3) the ENTIRE mouth widens with sound, capped per pose
	face.set_pose("default"); step(150)
	var w0: float = face.snapshot()["mouth_width"]
	face.set_level(1.0); step(60)
	var w1: float = face.snapshot()["mouth_width"]
	check(absf(w1 / w0 - (1.0 + FaceConfig.POSES["default"]["widen"])) < 0.02, "mouth widens by the pose's cap (%.3f)" % (w1 / w0))
	face.set_level(0.0); step(90)
	check(absf(face.snapshot()["mouth_width"] - w0) < 1.0, "mouth narrows back when quiet")

	# 4) Spooked: mouth only exists while she makes sound
	face.set_pose("spooked"); step(150)
	check(face.snapshot()["mouth_alpha"] < 0.02, "spooked silent has no mouth")
	check(face.snapshot()["outline"] > 0.98 and face.snapshot()["fill"] < 0.02, "spooked eyes are outlines")
	face.set_speaking(true); step(90)
	check(face.snapshot()["mouth_alpha"] > 0.98, "spooked (speaking) shows a mouth")
	face.set_speaking(false); step(90)
	check(face.snapshot()["mouth_alpha"] < 0.02, "mouth hides again when she stops")

	# 5) lines draw on for Tired/Spooked and retract elsewhere
	face.set_pose("tired"); step(120)
	check(face.snapshot()["lines"] > 0.98, "tired has under-eye lines")
	face.set_pose("default"); step(120)
	check(face.snapshot()["lines"] < 0.02, "lines retract for default")

	# 6) blink collapses the eye unit; Spooked never blinks
	face.set_pose("default"); step(60)
	face._blink_p = 0.5
	check(face.snapshot()["blink_scale"] < 0.1, "blink squashes the eyes to a line")
	face.set_pose("spooked"); step(600)
	check(face._blink_p < 0.0, "spooked stares (no blink in 10 s)")

	# 7) twitch swaps the eye shape briefly, then returns
	face.set_pose("anger"); step(120)
	face.twitch(); step(6)
	check(face._tgt["left_eye"] == shapes["smirk"]["left_eye"], "twitch targets the bottom-lid eyes")
	step(30)
	check(face._tgt["left_eye"] == shapes["anger"]["left_eye"], "twitch returns to the pose")

	# 8) names: poses, moods, unknowns
	check(FaceConfig.resolve("happy")["pose"] == "smirk", "happy -> smirk")
	check(FaceConfig.resolve("THINKING").has("look"), "thinking carries a gaze")
	check(FaceConfig.resolve("???")["pose"] == "default", "unknown name -> default")

	await process_frame
	print("ENGINE_%s fails=%d poses=%d" % ["OK" if fails == 0 else "FAILED", fails, names.size()])
	quit(1 if fails > 0 else 0)
