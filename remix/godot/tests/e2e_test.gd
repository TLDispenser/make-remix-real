extends SceneTree
# End-to-end with tools/fake_brain.py --once (see tests/run_all.sh).
# Passes when Remix connects, reports `ready`, plays the brain's performance and
# applies every message type the demo sends.

func _initialize() -> void:
	var main: Node = load("res://scenes/main.tscn").instantiate()
	root.add_child(main)
	var st := {"online": false, "done": 0, "ok": false}
	main.brain_online.connect(func(o: bool) -> void: if o: st["online"] = true)
	main.performance_done.connect(func(_id: String, ok: bool) -> void:
		st["done"] += 1
		st["ok"] = ok)
	var start := Time.get_ticks_msec()
	while (Time.get_ticks_msec() - start) < 20000 and not (st["done"] >= 1 and main.log_lines.has("theme")):
		await process_frame
	var need := ["pose", "perform", "listening", "notice", "twitch", "theme", "mouth"]
	var missing: Array = need.filter(func(k: String) -> bool: return not main.log_lines.has(k))
	var good: bool = st["online"] and st["done"] >= 1 and st["ok"] and missing.is_empty()
	print("E2E online=%s performances=%d ok=%s missing=%s" % [st["online"], st["done"], st["ok"], missing])
	print("E2E_%s" % ("OK" if good else "FAILED"))
	quit(0 if good else 1)
