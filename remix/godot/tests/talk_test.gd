extends SceneTree
# The lipsync handoff, stepped deterministically (no audio file needed).
#   godot --headless --path . --script res://tests/talk_test.gd
# A mouth can't hold a smirk or a ^ while it talks (face_config.gd TALK_*):
#   1. a sub-threshold level must NOT flip her into the talking bar
#   2. real sound: the mouth becomes the straight TALK bar, moved to the
#      expression mouth's own height, opened by that mouth's `open` cap
#   3. swapping the mouth mid-speech re-seats the bar at the new height
#   4. after TALK_RELEASE of quiet, her expression mouth comes back

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


func max_err(target: PackedVector2Array) -> float:
        var e := 0.0
        for i in 10:
                e = maxf(e, (face._cur["mouth"][i] - target[i]).length())
        return e


## Same "where does this mouth sit" rule as face.gd: median rung midpoint.
func _home_y(pts: PackedVector2Array) -> float:
        var ys: Array[float] = []
        for k in 5:
                ys.append((pts[k].y + pts[9 - k].y) * 0.5)
        ys.sort()
        return ys[2]


func _bbox_h(pts: PackedVector2Array) -> float:
        var lo := pts[0].y
        var hi := pts[0].y
        for p in pts:
                lo = minf(lo, p.y)
                hi = maxf(hi, p.y)
        return hi - lo


func _bar_at(own: PackedVector2Array) -> PackedVector2Array:
        var bar: PackedVector2Array = FaceShapes.all()[FaceConfig.MOUTHS[FaceConfig.TALK_MOUTH]["src"]]["mouth"]
        var dy: float = _home_y(own) - _home_y(bar)
        var out := PackedVector2Array(bar)
        for i in out.size():
                out[i] = Vector2(bar[i].x, bar[i].y + dy)
        return out


func _initialize() -> void:
        var main: Node = load("res://scenes/main.tscn").instantiate()
        root.add_child(main)
        face = main.get_node("%Face")
        main.set_process(false)   # we drive time ourselves
        face.set_process(false)
        var shapes := FaceShapes.all()
        await process_frame       # let _draw run once for real

        # 1) sub-threshold level: her own mouth stays
        face.set_pose("smirk"); step(150)
        var own: PackedVector2Array = shapes[FaceConfig.MOUTHS["smirk"]["src"]]["mouth"]
        face.set_level(0.05); step(120)
        check(not face._talking, "level under TALK_ON_LEVEL keeps her expression mouth")
        check(max_err(own) < 1.0, "a near-quiet smirk keeps its own shape")

        # 2) real sound: straight bar at the expression's height, opened by the cap
        face.set_level(1.0); step(150)
        check(face._talking, "sound flips her to the talking bar")
        var bar: PackedVector2Array = _bar_at(own)
        check(max_err(bar) < 1.0, "talking target is the bar at the smirk's height")
        var t: float = _bbox_h(FaceShapes.all()[FaceConfig.MOUTHS[FaceConfig.TALK_MOUTH]["src"]]["mouth"])
        var s: Dictionary = face.snapshot()
        check(s["mouth_height"] > t * float(FaceConfig.MOUTHS["smirk"]["open"]) * 0.9,
                        "the bar opens by the shape's cap (%.1f px vs cap %.1f)" % [s["mouth_height"], t * float(FaceConfig.MOUTHS["smirk"]["open"])])
        check(s["mouth_alpha"] > 0.98, "talking bar is visible")

        # 3) mouth unit changes mid-speech: still talking, bar re-seats
        face.set_face("", "pout"); step(2)
        check(face._talking, "mouth swap mid-speech keeps her talking")
        var own2: PackedVector2Array = shapes[FaceConfig.MOUTHS["pout"]["src"]]["mouth"]
        step(150)
        check(max_err(_bar_at(own2)) < 1.0, "mid-speech bar sits at the new mouth's height")

        # 4) quiet: after TALK_RELEASE the expression mouth returns
        face.set_level(0.0); step(120)   # 2 s > TALK_RELEASE (0.35 s)
        check(not face._talking, "quiet ends the talking bar")
        check(max_err(own2) < 1.0, "her expression mouth returns after the bar")
        check(face.snapshot()["mouth_open_scale"] < 1.02, "opening rests at 1.0x")

        await process_frame
        print("TALK_%s fails=%d" % ["OK" if fails == 0 else "FAILED", fails])
        quit(1 if fails > 0 else 0)
