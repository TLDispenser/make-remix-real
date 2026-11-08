extends SceneTree
# Contract audit for the generated shapes + hand config.
#   godot --headless --path . --script res://tests/shapes_audit.gd
# Every pose: 10 finite points per eye/mouth, 0 or 4 lines, a config entry,
# and both eyes mirror each other's point layout (same rung order).

func _initialize() -> void:
	var bad := 0
	var shapes := FaceShapes.all()
	for pose_name: String in shapes:
		var s: Dictionary = shapes[pose_name]
		if not FaceConfig.POSES.has(pose_name):
			print("FAIL %s has no FaceConfig pose entry" % pose_name); bad += 1
		for key in ["left_eye", "right_eye", "mouth"]:
			var pts: PackedVector2Array = s[key]
			if pts.size() != FaceShapes.POINTS:
				print("FAIL %s/%s has %d points" % [pose_name, key, pts.size()]); bad += 1
			for p in pts:
				if not (is_finite(p.x) and is_finite(p.y)):
					print("FAIL %s/%s non-finite point" % [pose_name, key]); bad += 1
		var n: int = (s["lines"] as Array).size()
		if n != 0 and n != 4:
			print("FAIL %s has %d lines (need 0 or 4)" % [pose_name, n]); bad += 1
		# Eyes face each other: point 0 is the OUTER bottom corner on both sides.
		var l: PackedVector2Array = s["left_eye"]
		var r: PackedVector2Array = s["right_eye"]
		if not (l[0].x < 0.0 and r[0].x > 0.0):
			print("FAIL %s eye rung order is not mirrored" % pose_name); bad += 1
	for e: String in FaceConfig.EYES:
		if not shapes.has(FaceConfig.EYES[e]["src"]):
			print("FAIL eyes %s -> missing shape %s" % [e, FaceConfig.EYES[e]["src"]]); bad += 1
	for m: String in FaceConfig.MOUTHS:
		if not shapes.has(FaceConfig.MOUTHS[m]["src"]):
			print("FAIL mouth %s -> missing shape %s" % [m, FaceConfig.MOUTHS[m]["src"]]); bad += 1
	for p: String in FaceConfig.POSES:
		if not FaceConfig.EYES.has(FaceConfig.POSES[p]["eyes"]) or not FaceConfig.MOUTHS.has(FaceConfig.POSES[p]["mouth"]):
			print("FAIL pose %s points at missing eyes/mouth" % p); bad += 1
	for alias: String in FaceConfig.ALIASES:
		var a: Dictionary = FaceConfig.ALIASES[alias]
		if a.has("pose") and not FaceConfig.POSES.has(a["pose"]):
			print("FAIL alias %s -> missing pose" % alias); bad += 1
		if not a.has("pose") and not (FaceConfig.EYES.has(a.get("eyes", "")) and FaceConfig.MOUTHS.has(a.get("mouth", ""))):
			print("FAIL alias %s -> missing eyes/mouth" % alias); bad += 1
	if bad == 0:
		print("SHAPES_OK poses=%d eyes=%d mouths=%d" % [shapes.size(), FaceConfig.EYES.size(), FaceConfig.MOUTHS.size()])
	quit(1 if bad > 0 else 0)
