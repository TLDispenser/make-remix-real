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
		face._twitch_in = 1.0e9   # keep the RANDOM idle twitch out of deterministic checks
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
	var names: Array = face.poses()
	await process_frame       # let _draw run once for real

	# 1) every pose settles on its shapes, and any A->B morph stays finite mid-flight
	for a: String in names:
		face.set_pose(a)
		step(150)
		var pa: Dictionary = FaceConfig.POSES[a]
		var ea: Dictionary = shapes[FaceConfig.EYES[pa["eyes"]]["src"]]
		var ma: Dictionary = shapes[FaceConfig.MOUTHS[pa["mouth"]]["src"]]
		for k in ["left_eye", "right_eye"]:
			check(max_err(k, ea[k]) < 1.0, "%s/%s settles on its SVG shape" % [a, k])
		check(max_err("mouth", ma["mouth"]) < 1.0, "%s/mouth settles on its SVG shape" % a)
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

	# 2) look limits per EYE shape and per MOUTH shape (the brain asks for the
	#    corner; the shape decides)
	for e: String in FaceConfig.EYES:
		face.set_face(e, "default", Vector2(1, 0)); step(150)
		var lim: Vector2 = FaceConfig.EYES[e]["range"] * FaceConfig.GAZE_GAIN
		var s: Dictionary = face.snapshot()
		check(absf(s["eye_off"].x) <= lim.x + 0.01, "eyes %s x within limit" % e)
		check(absf(s["eye_off"].x) >= lim.x - 1.0, "eyes %s reach their limit" % e)
		face.set_face(e, "default", Vector2(0, -1)); step(150)
		s = face.snapshot()
		check(absf(s["eye_off"].y) <= lim.y + 0.01, "eyes %s y within limit" % e)
		face.set_face(e, "", Vector2(5, 5))
		check(face.look.length() <= 1.0001, "look is clamped to unit length (eyes %s)" % e)
	for m: String in FaceConfig.MOUTHS:
		face.set_face("default", m, Vector2(0, -1)); step(150)
		check(absf(face.snapshot()["mouth_off"].y) <= FaceConfig.MOUTHS[m]["range"].y + 0.01,
				"mouth %s y within limit" % m)

	# 2b) EYES AND MOUTH ARE INDEPENDENT: every eyes x mouth pair settles on its own
	#     two shapes (7 x 7 originally; the count now follows the config), and
	#     changing one unit never disturbs the other.
	var mixes := 0
	for e: String in FaceConfig.EYES:
		for m: String in FaceConfig.MOUTHS:
			face.set_face(e, m, Vector2.ZERO)
			step(140)
			var es: Dictionary = shapes[FaceConfig.EYES[e]["src"]]
			var ms: Dictionary = shapes[FaceConfig.MOUTHS[m]["src"]]
			var ok: bool = max_err("left_eye", es["left_eye"]) < 1.0 \
					and max_err("right_eye", es["right_eye"]) < 1.0 \
					and max_err("mouth", ms["mouth"]) < 1.0
			check(ok, "mix %s eyes + %s mouth settles on both shapes" % [e, m])
			mixes += 1
	check(mixes == FaceConfig.EYES.size() * FaceConfig.MOUTHS.size(),
			"all %d eyes x mouth faces exist (%d)" % [FaceConfig.EYES.size() * FaceConfig.MOUTHS.size(), mixes])
	face.set_face("tired", "smirk", Vector2(0.5, 0.2)); step(150)
	var lk_before: Vector2 = face.look
	var eyes_before: PackedVector2Array = face._cur["left_eye"].duplicate()
	face.set_face("", "pout"); step(150)
	check(face.eyes_name == "tired" and face.mouth_name == "pout", "mouth-only change leaves the eyes alone")
	check(max_err("left_eye", eyes_before) < 0.5, "eye geometry untouched by a mouth change")
	check(face.look.is_equal_approx(lk_before), "mouth-only change keeps the gaze")
	var mouth_before: PackedVector2Array = face._cur["mouth"].duplicate()
	face.set_face("anger", ""); step(150)
	check(face.mouth_name == "pout" and max_err("mouth", mouth_before) < 0.5, "eyes-only change leaves the mouth alone")
	face.set_face("nonsense", "garbage")
	check(face.eyes_name == "anger" and face.mouth_name == "pout", "unknown eyes/mouth names are ignored")
	check(face.pose_name == "anger/pout", "a custom mix is named eyes/mouth (%s)" % face.pose_name)
	face.set_face("tired", "trauma")
	check(face.pose_name == "tired", "a matching pair reports its pose shortcut")

	# 3) speech OPENS the mouth (thicker, same length) and rests when quiet.
	#    (The old "widens" check predates the ribbon-lipsync rewrite: the
	#    mouth keeps its drawn length forever — face_config.gd `open`.)
	face.set_pose("default"); step(150)
	var w0: float = face.snapshot()["mouth_width"]
	var h0: float = face.snapshot()["mouth_height"]
	face.set_level(1.0); step(60)
	var s3: Dictionary = face.snapshot()
	check(s3["mouth_open_scale"] > FaceConfig.MOUTHS["default"]["open"] - 0.1,
			"mouth opens to the shape's cap (%.2f)" % s3["mouth_open_scale"])
	check(s3["mouth_height"] > h0 * 2.0, "the opened mouth is taller (%.0f -> %.0f)" % [h0, s3["mouth_height"]])
	check(absf(s3["mouth_width"] - w0) <= maxf(1.0, w0 * 0.05),
			"the mouth never gets LONGER (%.1f -> %.1f)" % [w0, s3["mouth_width"]])
	face.set_level(0.0); step(120)
	check(face.snapshot()["mouth_open_scale"] < 1.02, "mouth rests when quiet")

	# 4) the "none" mouth only exists while she makes sound (works with ANY eyes)
	face.set_face("tsun", "none"); step(150)
	check(face.snapshot()["mouth_alpha"] < 0.02, "no-mouth with tsun eyes is silent-hidden")
	face.set_face("default", "default"); step(150)
	check(face.snapshot()["mouth_alpha"] > 0.98, "default mouth comes back")
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

	# 6) blink collapses the eye unit; spooked EYES never blink (whatever the mouth)
	face.set_pose("default"); step(60)
	face._blink_p = 0.5
	check(face.snapshot()["blink_scale"] < 0.1, "blink squashes the eyes to a line")
	face.set_face("spooked", "smirk"); step(600)
	check(face._blink_p < 0.0, "spooked eyes stare (no blink in 10 s) even with a smirk")

	# 7) twitch: ONE eye swaps to the bottom-lid shape briefly, the other keeps her
	#    expression; then it returns. (A tic, not a blink: random side when unspecified.)
	var tw_src: String = FaceConfig.EYES[FaceConfig.TWITCH_EYES]["src"]
	face.set_pose("anger"); step(120)
	face.twitch("left_eye"); step(6)
	check(face._tgt["left_eye"] == shapes[tw_src]["left_eye"], "twitch targets the bottom-lid eye (left)")
	check(face._tgt["right_eye"] == shapes["anger"]["right_eye"], "the OTHER eye does not twitch")
	step(30)
	check(face._tgt["left_eye"] == shapes["anger"]["left_eye"], "twitch returns to the eyes")
	face.twitch("right_eye"); step(6)
	check(face._tgt["right_eye"] == shapes[tw_src]["right_eye"], "twitch can be the right eye")
	check(face._tgt["left_eye"] == shapes["anger"]["left_eye"], "...and then the left stays put")
	step(30)
	var sides := {}
	for i in 40:
		face.twitch(); sides[face._twitch_side] = true; step(30)
	check(sides.size() == 2, "unspecified twitch picks either eye over time")
	face.set_face("default", "default", Vector2(1, 0)); step(150)
	check(absf(face.snapshot()["eye_off"].x) > FaceConfig.EYES["default"]["range"].x + 1.0
			or FaceConfig.GAZE_GAIN <= 1.0, "GAZE_GAIN widens the eye gaze past the bare range")
	check(face.snapshot()["eye_off"].x + 260.0 < 960.0, "gaze at full gain keeps the eyes on the canvas")

	# 8) names: poses, moods, mixed-mood aliases, unknowns
	check(FaceConfig.resolve("happy")["eyes"] == "bottomlid" and FaceConfig.resolve("happy")["mouth"] == "smirk", "happy -> smirk pose")
	check(FaceConfig.resolve("THINKING").has("look"), "thinking carries a gaze")
	check(FaceConfig.resolve("???")["eyes"] == "default", "unknown name -> default")
	var dp: Dictionary = FaceConfig.resolve("deadpan")
	check(dp["eyes"] == "really" and dp["mouth"] == "default", "deadpan is a mixed face (really eyes, flat mouth)")

	# 9) CLOSING LIDS DON'T WIND: thunk's lid ribbon stacks its rungs
	#    bottom-to-top like the eye shapes, so EVERY point travels almost
	#    straight down. The old vertical-rung layout forced each rung to
	#    swing 90 degrees mid-morph (the eyes visibly rotated shut).
	for e_key in ["left_eye", "right_eye"]:
		face.set_pose("default"); step(150)
		var cur: PackedVector2Array = face._cur[e_key].duplicate()
		face.set_face("thunk", "none")
		var dx_max := 0.0
		var dy_max := 0.0
		var th: PackedVector2Array = face._tgt[e_key]
		for i in 10:
			dy_max = maxf(dy_max, absf(th[i].y - cur[i].y))
			dx_max = maxf(dx_max, absf(th[i].x - cur[i].x))
		check(dx_max <= 70.0, "closing into thunk drops nearly straight down (%s max dx %.0f <= 70)" % [e_key, dx_max])
		check(dy_max >= 100.0, "closing into thunk travels vertically (%s max dy %.0f >= 100)" % [e_key, dy_max])

	# 10) props: the wheel ring arrives with the pose and spins forever (the
	#     spinner is one ring band; the hub is its bbox center — authored
	#     that way). The pose never goes idle while props show.
	face.set_pose("thunk"); step(120)
	check(face.snapshot()["props"] > 0.98, "thunk shows its prop wheel")
	check(face.is_active(), "a spinning wheel keeps the face active")
	check(face._pr_hub[0].distance_to(Vector2(700, 396.7)) < 0.5, "wheel hub is its true center (rel 700, 396.7)")
	face.set_pose("smug"); step(120)
	check(face.snapshot()["props"] < 0.02, "the wheel fades out with the pose")

	# 11) scribble's knot teleports as CONNECTED joints: strokes share
	#     endpoints exactly when one is the next's — the whole knot moves
	#     together, no detached dashes.
	face.set_face("scribble", "none"); step(30)
	check(face._jit_r == 45.0, "scribble carries its teleport radius")
	var knot: Array = shapes["scribble"]["lines"]
	check(face._jit_o.size() == 21, "the closed 21-segment knot has 21 joints (%d)" % face._jit_o.size())
	var chained := true
	for i in knot.size() - 1:
		if face._jit_node[2 * i + 1] != face._jit_node[2 * i + 2]:
			chained = false
	check(chained, "each stroke's end joint is the next stroke's start joint")
	check(face._jit_node[2 * (knot.size() - 1) + 1] == face._jit_node[0], "the knot is closed: last stroke returns to the first's joint")
	face.set_pose("scribble"); step(150)   # let the springs settle onto the knot
	var seam_gap: float = 0.0
	for i in knot.size() - 1:
		# Drawn geometry = spring position + its joint's offset; two strokes
		# sharing a joint use the same offset, so their drawn tips touch.
		seam_gap = maxf(seam_gap, face._ln_b[i].distance_to(face._ln_a[i + 1]))
	check(seam_gap < 1.0, "strokes share their joints on the knot (max seam %.2f px < 1)" % seam_gap)

	# 12) blush is GLOBAL: the cheek hatching is its own overlay worn in
	#     every pose; the toggle fades it everywhere and touches nothing else.
	face.set_pose("default"); step(120)
	check(face.snapshot()["blush"] > 0.98, "the blush overlay is worn in default (global)")
	check(face._bl_a.size() == 10, "the overlay carries 10 hatch strokes (%d)" % face._bl_a.size())
	face.set_blush(false); step(120)
	check(face.snapshot()["blush"] < 0.02, "blush off fades the overlay")
	face.set_pose("bashful"); step(120)
	check(face.snapshot()["blush"] < 0.02, "blush stays off across poses")
	check(face.snapshot()["lines"] < 0.02, "bashful's eyes carry no strokes of their own anymore")
	face.set_pose("tired"); step(120)
	check(face.snapshot()["blush"] < 0.02, "blush still off in tired")
	check(face.snapshot()["lines"] > 0.98, "tired's under-eye bags are unaffected by the toggle")
	face.set_blush(true); step(120)
	check(face.snapshot()["blush"] > 0.98, "blush on brings the hatching back")
	face.set_pose("default"); step(120)
	check(face.snapshot()["blush"] > 0.98, "blush on persists across poses")

	# 13) DEAD STROKES DIE: a stroke the new eyes don't use must fade out,
	#     not sit on screen forever as a dot. The array is a superset sized
	#     for the biggest shape seen, and the channel alpha is shared by
	#     everything in it — so each stroke carries its own fade on top.
	face.set_face("scribble", "none"); step(120)
	check(face._ln_f.size() == face._ln_a.size(), "every stroke slot has a fade value")
	check(face.snapshot()["lines"] > 0.98, "scribble's knot is live before the swap")
	face.set_face("tired", ""); step(120)   # 21 knot strokes -> 4 bag strokes
	check(face.snapshot()["lines"] > 0.98, "tired's own strokes keep the channel up")
	for i in 4:
		check(face._ln_f[i] > 0.98, "bag stroke %d stays live" % i)
	for i in range(4, face._ln_a.size()):
		check(face._ln_f[i] < 0.01, "dead knot stroke %d faded out (%.3f)" % [i, face._ln_f[i]])
		check(face._ln_a[i].distance_squared_to(face._ln_b[i]) < 0.25,
				"dead knot stroke %d collapsed shut" % i)
	face.set_face("scribble", "none"); step(120)
	face.set_face("spooked", "none"); step(120)   # 21 -> 4 again, other geometry
	for i in range(4, face._ln_a.size()):
		check(face._ln_f[i] < 0.01, "dead knot stroke %d faded into spooked (%.3f)" % [i, face._ln_f[i]])

	await process_frame
	print("ENGINE_%s fails=%d poses=%d mixes=%d" % ["OK" if fails == 0 else "FAILED", fails, names.size(), mixes])
	quit(1 if fails > 0 else 0)
