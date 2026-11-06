# face_atlas.gd — Remix's starter glyph vocabulary (NEW_SPEC character spec).
# Same language as `references/Faces.png` (bars, wedges, chevrons, hollow
# outlines, doubled bars, hooked bar, scribble) — REMIX's red-flavored atlas,
# not Mech's green one. Drawn neutral and tinted by ONE theme color.
#
# GEOMETRY CONTRACT (why quads):
# Every feature = EXACTLY 16 points = EXACTLY 4 quads of 4 points. Any glyph
# in this vocabulary decomposes into bars/wedges (<= 4 quads), and face.gd
# renders each quad as two triangles with a convexity-checked diagonal — so
# spring-driven per-point morphs can NEVER produce an invalid draw (polygons
# built of repeated/degenerate points would, and did — see RESEARCH_NOTES).
# Unused quads are padded with REAL duplicated geometry (not zeros) so every
# point has plausible neighbors during a morph crossfade.
#
# Format: expression = Dictionary:
#   gl (glyph): PackedVector2Array — EXACTLY 16 pts (4 quads flat)
#   glitch: bool — scribble/static face (procedural overlay, no morph)
#   meta — "dy" (droop px), "jitter" (px shake at draw time)
# Face-local space: eyes near y=-22 (FaceAtlas.EY), mouth near y=26 (MY).
class_name FaceAtlas

const PTS := 16
const QUADS := 4          # quads per feature
const EY := -22.0         # eye row y
const MY := 26.0          # mouth row y
const ESEP := 34.0        # eye center offset each way from face center


# ---------------------------------------------------------------- builders

## Solid bar/rect centered on `center`, size `size` (axis-aligned).
## `lean_top` raises the INNER-top corner by that many px (negative drops it),
## mimicking Mech's tall tapering eyes. Mirroring is automatic per side sign.
static func _bar(center: Vector2, size: Vector2, lean_top: float = 0.0) -> PackedVector2Array:
	var h := size * 0.5
	var inner := -h.x if center.x < 0.0 else h.x
	var x := center.x
	var y := center.y
	return PackedVector2Array([
		Vector2(x + inner, y - h.y - lean_top),  # inner-top
		Vector2(x - inner, y - h.y),             # outer-top
		Vector2(x - inner, y + h.y),             # outer-bottom
		Vector2(x + inner, y + h.y),             # inner-bottom
	])


## Triangle shot as a 4-point quad: `><`-style chevron, apex pointing inward.
static func _chevron(center: Vector2, spread: float, height: float) -> PackedVector2Array:
	var dir := 1.0 if center.x < 0.0 else -1.0  # apex toward face center
	var x := center.x
	var y := center.y
	var apex := Vector2(x + dir * spread, y)
	return PackedVector2Array([
		Vector2(x - dir * spread, y - height),
		apex,
		Vector2(x - dir * spread, y + height),
		apex,  # duplicate = degenerate 4th corner; renderer copes
	])


## Square outline (hollow glyph) = 4 thin bars, exactly the full quad budget.
static func _hollow(center: Vector2, size: Vector2, t: float) -> Array:
	var h := size * 0.5
	var x := center.x
	var y := center.y
	var out: Array[PackedVector2Array] = []
	out.append(_bar(Vector2(x, y - h.y + t * 0.5), Vector2(size.x, t)))
	out.append(_bar(Vector2(x, y + h.y - t * 0.5), Vector2(size.x, t)))
	out.append(_bar(Vector2(x - h.x + t * 0.5, y), Vector2(t, size.y)))
	out.append(_bar(Vector2(x + h.x - t * 0.5, y), Vector2(t, size.y)))
	return out


## Hooked bar — Remix's signature idle mouth (from Anger.png): flat bar with
## a short downturn at one end.
static func _hook(center: Vector2, width: float, down: float) -> Array:
	var t := 6.0
	var x := center.x
	var y := center.y
	var stub := Vector2(x + width * 0.5 - t * 0.5, y + down * 0.5)
	return [
		_bar(Vector2(x - width * 0.06, y), Vector2(width, t)),
		_bar(stub, Vector2(t, down + t)),
	]


## Smile that bends upward in the middle — 2 slope bars sharing a joint.
static func _arc(center: Vector2, width: float, rise: float) -> Array:
	var t := 6.0
	var x := center.x
	var y := center.y
	var dy := -rise * 0.5
	return [
		PackedVector2Array([
			Vector2(x - width * 0.5, y + 2.0), Vector2(x - width * 0.5 + 2.0, y + dy - 1.0),
			Vector2(x - width * 0.5 + 2.0, y + dy + t - 1.0), Vector2(x - width * 0.5, y + 2.0 + t),
		]),
		PackedVector2Array([
			Vector2(x - width * 0.5 + width * 0.5, y + dy - 1.0), Vector2(x + width * 0.5 - 2.0, y + 2.0),
			Vector2(x + width * 0.5 - 2.0, y + 2.0 + t), Vector2(x, y + dy + t - 1.0),
		]),
	]


## Flatten 1..4 quads into the 16-pt glyph. Missing budget slots get REAL
## copies of the last real quad (hidden duplicates) — springs above a
## zero/degenerate point set would destabilize morphs (RESEARCH_NOTES).
static func _gl(quads: Array) -> PackedVector2Array:
	var out := PackedVector2Array()
	for i in QUADS:
		var q: PackedVector2Array = quads[i] if i < quads.size() else quads[quads.size() - 1]
		out.append_array(q)
	return out


## Depth-2 flatten used inside all() below.
## The static/scribble face: geometry fully collapsed; face.gd covers the
## swap with a static burst and draws procedural scribbles instead.
static func _collapsed() -> PackedVector2Array:
	var out := PackedVector2Array()
	for i in PTS:
		out.append(Vector2.ZERO)
	return out

# ------------------------------------------------------------ expressions

## All expressions. Keys = protocol mood names + "glitch".
static func all() -> Dictionary:
	var E := ESEP
	var eyes_tall := Vector2(14.0, 42.0)
	var eyes_wide := Vector2(20.0, 36.0)
	var eyes_half := Vector2(14.0, 22.0)
	var eyes_thin := Vector2(15.0, 8.0)
	var out := {}

	# idle — tall tapering eyes (Anger.png family), hooked bar mouth.
	out["idle"] = {
		"left_eye": _gl([_bar(Vector2(-E, EY), eyes_tall, 8.0)]),
		"right_eye": _gl([_bar(Vector2(E, EY), eyes_tall, 8.0)]),
		"mouth": _gl(_hook(Vector2(3.0, MY), 40.0, 9.0)),
		"glitch": false, "meta": {},
	}

	# happy — chevrons up, thin arced mouth.
	out["happy"] = {
		"left_eye": _gl([_chevron(Vector2(-E, EY + 2.0), 9.0, 11.0)]),
		"right_eye": _gl([_chevron(Vector2(E, EY + 2.0), 9.0, 11.0)]),
		"mouth": _gl(_arc(Vector2(0.0, MY), 34.0, 8.0)),
		"glitch": false, "meta": {},
	}

	# stressed — wedges slanting DOWN (distress) with a clenched doubled bar.
	out["stressed"] = {
		"left_eye": _gl([_bar(Vector2(-E, EY), eyes_tall, -7.0)]),
		"right_eye": _gl([_bar(Vector2(E, EY), eyes_tall, -7.0)]),
		"mouth": _gl([
			_bar(Vector2(0.0, MY - 5.0), Vector2(34.0, 5.0)),
			_bar(Vector2(0.0, MY + 5.0), Vector2(34.0, 5.0)),
		]),
		"glitch": false, "meta": {"jitter": 1.5},
	}

	# sleepy — flat dashes, low thin mouth, droop everything.
	out["sleepy"] = {
		"left_eye": _gl([_bar(Vector2(-E + 2.0, EY + 9.0), eyes_thin)]),
		"right_eye": _gl([_bar(Vector2(E - 2.0, EY + 9.0), eyes_thin)]),
		"mouth": _gl([_bar(Vector2(0.0, MY + 5.0), Vector2(30.0, 5.0))]),
		"glitch": false, "meta": {"dy": 5.0},
	}

	# thinking — asymmetric: left tapered, right lidded half; small hook.
	out["thinking"] = {
		"left_eye": _gl([_bar(Vector2(-E, EY), eyes_tall, 4.0)]),
		"right_eye": _gl([_bar(Vector2(E - 2.0, EY + 7.0), eyes_half)]),
		"mouth": _gl(_hook(Vector2(-2.0, MY), 26.0, 6.0)),
		"glitch": false, "meta": {},
	}

	# listening — big wide eyes, small solid "o" mouth.
	out["listening"] = {
		"left_eye": _gl([_bar(Vector2(-E, EY), eyes_wide)]),
		"right_eye": _gl([_bar(Vector2(E, EY), eyes_wide)]),
		"mouth": _gl([_bar(Vector2(0.0, MY), Vector2(15.0, 15.0))]),
		"glitch": false, "meta": {},
	}

	# alert — full-size wide eyes, open mouth, everything lifted.
	out["alert"] = {
		"left_eye": _gl([_bar(Vector2(-E, EY - 3.0), Vector2(20.0, 46.0))]),
		"right_eye": _gl([_bar(Vector2(E, EY - 3.0), Vector2(20.0, 46.0))]),
		"mouth": _gl([_bar(Vector2(0.0, MY), Vector2(18.0, 18.0))]),
		"glitch": false, "meta": {"dy": -2.0},
	}

	# bored — half-lidded tall eyes, long low bar, droop.
	out["bored"] = {
		"left_eye": _gl([_bar(Vector2(-E + 2.0, EY + 11.0), Vector2(13.0, 18.0))]),
		"right_eye": _gl([_bar(Vector2(E - 2.0, EY + 11.0), Vector2(13.0, 18.0))]),
		"mouth": _gl([_bar(Vector2(3.0, MY + 3.0), Vector2(26.0, 5.0))]),
		"glitch": false, "meta": {"dy": 4.0},
	}

	# glitch — the scribble/static face: morph cannot carry this shape.
	out["glitch"] = {
		"left_eye": _collapsed(),
		"right_eye": _collapsed(),
		"mouth": _collapsed(),
		"glitch": true, "meta": {},
	}

	# hollow variant kept for the vocabulary (>< outline squint family):
	# used experimentally via the F8 cycle for tweaking.
	out["squint"] = {
		"left_eye": _gl(_hollow(Vector2(-E, EY), Vector2(18.0, 30.0), 5.0)),
		"right_eye": _gl(_hollow(Vector2(E, EY), Vector2(18.0, 30.0), 5.0)),
		"mouth": _gl([_bar(Vector2(0.0, MY + 2.0), Vector2(22.0, 5.0))]),
		"glitch": false, "meta": {},
	}

	return out
