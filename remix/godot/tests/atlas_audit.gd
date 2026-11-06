extends SceneTree
# Atlas contract audit: every expression must be EXACTLY
# FaceAtlas.PTS (16) points per feature and finite. (Rendering correctness
# is guaranteed by face.gd's quad-triangle draw; see tests/probe_exprs.gd.)

var _finsub := false


func _initialize() -> void:
	var atlas: Dictionary = load("res://scripts/face_atlas.gd").all()
	var bad := 0
	for expr_name: String in atlas:
		var expr: Dictionary = atlas[expr_name]
		for feature in ["left_eye", "right_eye", "mouth"]:
			var pts: PackedVector2Array = expr[feature]
			if pts.size() != 16:
				print("FAIL %s/%s size=%d" % [expr_name, feature, pts.size()])
				bad += 1
			for p in pts:
				if is_nan(p.x) or is_nan(p.y) or is_inf(p.x) or is_inf(p.y):
					print("FAIL %s/%s has non-finite point" % [expr_name, feature])
					bad += 1
	if bad == 0:
		print("ATLAS_OK expressions=%d" % atlas.size())
	quit()


func _process(_d: float) -> bool:
	return _finsub
