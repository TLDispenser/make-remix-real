extends SceneTree
# Audio + cue playback, in real time, against the real scene.
#   godot --headless --path . --script res://tests/perform_test.gd
# Checks: cues fire in order against the audio clock, the mouth opens while
# sound plays and rests in the quiet gap, `then` pose lands, perform_done is
# reported, bad audio degrades gracefully, stop cancels.

var fails := 0
var main: Node
var face: Node2D
var done_events: Array = []


func check(ok: bool, what: String) -> void:
	if not ok:
		fails += 1
		print("FAIL ", what)


func wait_until(cond: Callable, timeout: float) -> bool:
	var start := Time.get_ticks_msec()
	while (Time.get_ticks_msec() - start) / 1000.0 < timeout:
		if cond.call():
			return true
		await process_frame
	return cond.call()


func _initialize() -> void:
	main = load("res://scenes/main.tscn").instantiate()
	root.add_child(main)
	face = main.get_node("%Face")
	main.performance_done.connect(func(id: String, ok: bool) -> void: done_events.append([id, ok]))
	await process_frame
	Engine.max_fps = 0
	main.fps_idle = 0
	main.fps_active = 0

	var wav := ProjectSettings.globalize_path("res://tests/fixtures/beeps.wav")
	check(WavLoader.load_file(wav) != null, "WAV fixture loads")
	check(WavLoader.from_bytes(PackedByteArray([1, 2, 3])) == null, "garbage bytes are rejected")

	# --- a real performance: 0.9 s, loud 0-0.3 s, quiet 0.3-0.5 s, loud 0.5-0.9 s
	face.set_pose("default")
	main._handle(JSON.stringify({
		"type": "perform", "id": "t1", "audio": wav,
		"cues": [{"t": 0.0, "pose": "anger"}, {"t": 0.55, "pose": "tired", "look": [0.5, 0.0]}],
		"then": {"pose": "pout"},
	}))
	var pose_seq: Array = []
	var acc := {"max_level": 0.0, "max_open": 0.0, "min_open": 10.0}   # a Dictionary, because lambdas copy plain floats
	var finished := await wait_until(func() -> bool:
		var s: Dictionary = face.snapshot()
		if pose_seq.is_empty() or pose_seq[-1] != s["pose"]:
			pose_seq.append(s["pose"])
		acc["max_level"] = maxf(acc["max_level"], s["level"])
		acc["max_open"] = maxf(acc["max_open"], s["mouth_open_scale"])
		if acc["max_level"] > 0.25:   # only after she was audibly loud does a dip mean "resting"
			acc["min_open"] = minf(acc["min_open"], s["mouth_open_scale"])
		return not done_events.is_empty(), 4.0)
	check(finished, "performance finishes by itself")
	check(pose_seq.size() >= 3 and pose_seq[0] == "anger" and "tired" in pose_seq and pose_seq[-1] == "pout",
			"cues fired in order, then landed on `then`: %s" % str(pose_seq))
	check(acc["max_level"] > 0.25, "audio level reaches the face (max %.2f)" % acc["max_level"])
	check(acc["max_open"] > 1.3, "mouth opened while sound played (max %.2f)" % acc["max_open"])
	check(acc["min_open"] < 1.5, "mouth rested in the quiet gap (min %.2f)" % acc["min_open"])
	check(done_events.size() == 1 and done_events[0][0] == "t1" and done_events[0][1] == true, "perform_done(t1, ok)")
	check(not face.speaking, "speaking flag cleared afterwards")

	# --- cues and `then` can mix eyes and mouth freely
	done_events.clear()
	face.set_pose("default")
	main._handle(JSON.stringify({
		"type": "perform", "id": "t1b", "audio": wav,
		"cues": [{"t": 0.0, "eyes": "really", "mouth": "smirk"},
				{"t": 0.35, "mouth": "pout"},
				{"t": 0.6, "eyes": "tired", "look": [0.4, 0.2]}],
		"then": {"eyes": "default", "mouth": "tsun"},
	}))
	var mix_seq: Array = []
	await wait_until(func() -> bool:
		var s2: Dictionary = face.snapshot()
		var tag: String = "%s/%s" % [s2["eyes"], s2["mouth"]]
		if mix_seq.is_empty() or mix_seq[-1] != tag:
			mix_seq.append(tag)
		return not done_events.is_empty(), 4.0)
	check(mix_seq.has("really/smirk") and mix_seq.has("really/pout") and mix_seq.has("tired/pout"),
			"per-unit cues applied in order: %s" % str(mix_seq))
	check(face.eyes_name == "default" and face.mouth_name == "tsun", "`then` can be a mixed face (%s)" % face.pose_name)

	# --- missing audio: still acts, reports failure, no crash
	done_events.clear()
	main._handle(JSON.stringify({"type": "perform", "id": "t2", "audio": "/nonexistent.wav",
		"cues": [{"t": 0.0, "pose": "tsun"}], "then": {"pose": "default"}}))
	check(done_events.size() == 1 and done_events[0][1] == false, "bad audio -> perform_done ok=false")
	check(face.pose_name == "default", "bad audio still lands on `then`")

	# --- stop cancels a running performance
	done_events.clear()
	main._handle(JSON.stringify({"type": "perform", "id": "t3", "audio": wav,
		"cues": [], "then": {"pose": "default"}}))
	await wait_until(func() -> bool: return false, 0.15)
	main._handle(JSON.stringify({"type": "stop"}))
	check(done_events.size() == 1 and done_events[0][0] == "t3" and done_events[0][1] == false, "stop cancels with ok=false")

	# --- a new performance replaces a running one
	done_events.clear()
	main._handle(JSON.stringify({"type": "perform", "id": "a", "audio": wav, "cues": []}))
	main._handle(JSON.stringify({"type": "perform", "id": "b", "audio": wav, "cues": []}))
	check(done_events.size() == 1 and done_events[0][0] == "a", "second performance cancels the first")
	main._handle(JSON.stringify({"type": "stop"}))

	print("PERFORM_%s fails=%d" % ["OK" if fails == 0 else "FAILED", fails])
	quit(1 if fails > 0 else 0)
