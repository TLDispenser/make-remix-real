extends SceneTree
# Deterministic render probe: cycles EVERY expression with real morph /
# blink timing so face.gd's quad-triangle renderer goes through arbitrary
# mid-morph states. Pass condition (checked outside): zero "ERROR:" lines
# about polygon data in the engine output.

var f := 0
var node: Node
var expr_index := 0
var exprs: Array = []


func _initialize() -> void:
	node = load("res://scenes/main.tscn").instantiate()
	root.add_child(node)
	exprs = node.get_node("%Face").names()


func _process(_d: float) -> bool:
	f += 1
	if f < 6:
		return false
	var face: Node = node.get_node("%Face")
	if f % 30 == 1:  # new expression every 0.5s of headless time
		if expr_index >= exprs.size():
			print("PROBE_DONE expressions=%d" % exprs.size())
			quit()
			return false
		var next: String = exprs[expr_index]
		face.set_expression(next)
		expr_index += 1
	if f % 8 == 1:
		face._blink = 0.1  # force mid-blink squash states too
	return false
