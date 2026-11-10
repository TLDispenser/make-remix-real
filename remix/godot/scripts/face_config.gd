# face_config.gd: the hand-tunable half of Remix's face.
# face_shapes.gd (generated from your SVGs) says WHAT each drawn shape looks like.
# This file says how the shapes are USED and how much each may move. Edit freely.
#
# EYES and MOUTH are two separate units, in expression too. Every SVG you drew
# holds one eye shape and one mouth shape (the slash in a file title, e.g.
# "Tired/Trauma", is exactly that split). Currently 12 drawings -> 12 eye
# shapes and 10 mouths (Thunk and Scribble are mouthless and reuse "none"),
# and any eyes can pair with any mouth.
# A POSE is just a named shortcut for one eyes+mouth pair.
#   Original 7: default anger smirk pout spooked tired tsun.
#   From the reference folder (Bashful, Eyes-Closed-Thunking, Scribble,
#   Strange/-, You-like-Krabby-Patties...): bashful thunk scribble strange smug.
#
# All distances are SVG design-space pixels (the 1920x1080 canvas).
#
# EYES entries
#   src          which drawing in face_shapes.gd supplies this eye shape
#   range        max offset of the whole EYE unit (x = side to side, y = up/down),
#                before GAZE_GAIN (below) scales it
#   blink        does this eye shape blink at all
#   blink_gap    multiplier on the time between blinks (<1 = more often)
#   blink_speed  multiplier on blink speed (<1 = slow, heavy blink)
#   tremble      constant tiny shake in px, applied to the whole face (Spooked)
#   twitch       may the idle bottom-lid twitch fire with these eyes
# MOUTHS entries
#   src          which drawing supplies this mouth shape
#   range        max offset of the MOUTH unit
#   open         how far the mouth opens at full volume, as a thickness multiplier
#                (3.0 = three times as thick). Applied uniformly to every rung (or to
#                the whole mouth in "whole" mode, see MOUTH_OPEN_MODE). The mouth
#                keeps its shape and its drawn length; it never gets longer.
#   silent       false = the mouth is hidden unless she is making sound
#   talk         (optional) a different straight mouth to talk with, instead of TALK_MOUTH
class_name FaceConfig

const DEFAULT_POSE := "default"
const TWITCH_EYES := "bottomlid"   # the eye shape the idle twitch flicks to

# How speech opens the mouth (see `open` below).
#   "rung"  each rung of the mouth ribbon (the cross-section from one edge of the
#           stroke to the other) scales about ITS OWN midpoint. The mouth keeps its
#           spine and layout and just gets thicker along its own shape: bars open
#           up and down, an angled tick or a ^ gets fatter strokes without growing
#           taller overall.
#   "whole" the entire mouth scales vertically about its overall center instead.
const MOUTH_OPEN_MODE := "rung"

# LIPSYNC. A mouth can't hold a smirk or a ^ while it talks. So the moment sound
# really opens the mouth, it eases to a STRAIGHT bar (kept at the same height her
# expression's mouth sits at, so it doesn't hop) and opens from there. When the
# sound stops it eases back to her expression's own mouth.
const TALK_MOUTH := "default"   # which straight mouth to talk with (any MOUTHS entry)
const TALK_ON_LEVEL := 0.10     # smoothed sound level (0..1) that counts as "talking"
const TALK_RELEASE := 0.35      # seconds of quiet before the expression mouth returns
								# (so the gaps between words don't flap the shape)

# GAZE. `look` (-1..1) times a shape's `range` is how far the eye unit may move.
# These ranges are small against the 1920x1080 canvas, so a gaze AIMED at a
# window on her desktop (the brain turns her eyes toward whatever she names)
# barely read at 1.0. GAZE_GAIN multiplies the EYE range only (the mouth keeps
# its own, so the face doesn't smear). Both eyes shift together as one unit, so
# the gap between them never changes: the only ceiling is the canvas itself.
# 1.0 = the old behavior. Per-shape limits (`range`) stay the thing to hand-tune.
const GAZE_GAIN := 1.6

const EYES := {
	"default": {"src": "default", "range": Vector2(70, 40), "blink": true,
		"blink_gap": 1.0, "blink_speed": 1.0, "tremble": 0.0, "twitch": false},
	"anger": {"src": "anger", "range": Vector2(35, 20), "blink": true,
		"blink_gap": 1.3, "blink_speed": 1.0, "tremble": 0.0, "twitch": true},
	"bottomlid": {"src": "smirk", "range": Vector2(60, 35), "blink": true,
		"blink_gap": 1.0, "blink_speed": 1.0, "tremble": 0.0, "twitch": false},
	"really": {"src": "pout", "range": Vector2(50, 30), "blink": true,
		"blink_gap": 1.2, "blink_speed": 0.9, "tremble": 0.0, "twitch": true},
	"spooked": {"src": "spooked", "range": Vector2(25, 15), "blink": false,
		"blink_gap": 1.0, "blink_speed": 1.0, "tremble": 2.0, "twitch": false},
	"tired": {"src": "tired", "range": Vector2(50, 50), "blink": true,
		"blink_gap": 0.7, "blink_speed": 0.55, "tremble": 0.0, "twitch": false},
	"tsun": {"src": "tsun", "range": Vector2(30, 20), "blink": true,
		"blink_gap": 1.0, "blink_speed": 1.0, "tremble": 0.0, "twitch": false},
	# ---- from the reference folder ----
	"bashful": {"src": "bashful", "range": Vector2(60, 40), "blink": true,
		"blink_gap": 1.0, "blink_speed": 1.0, "tremble": 0.0, "twitch": false},
	"thunk": {"src": "thunk", "range": Vector2(30, 15), "blink": false,
		"blink_gap": 1.0, "blink_speed": 1.0, "tremble": 0.0, "twitch": false},
	"scribble": {"src": "scribble", "range": Vector2(25, 15), "blink": false,
		"blink_gap": 1.0, "blink_speed": 1.0, "tremble": 2.0, "twitch": false},
	"strange": {"src": "strange", "range": Vector2(35, 20), "blink": true,
		"blink_gap": 1.4, "blink_speed": 0.9, "tremble": 0.5, "twitch": true},
	"smug": {"src": "smug", "range": Vector2(30, 18), "blink": true,
		"blink_gap": 1.2, "blink_speed": 1.0, "tremble": 0.0, "twitch": false},
}

const MOUTHS := {
	"default": {"src": "default", "range": Vector2(30, 18), "open": 3.0, "silent": true},
	"anger": {"src": "anger", "range": Vector2(20, 10), "open": 3.0, "silent": true},
	"smirk": {"src": "smirk", "range": Vector2(30, 15), "open": 3.0, "silent": true},
	"pout": {"src": "pout", "range": Vector2(25, 12), "open": 3.0, "silent": true},
	"trauma": {"src": "tired", "range": Vector2(25, 15), "open": 2.6, "silent": true},
	"tsun": {"src": "tsun", "range": Vector2(20, 12), "open": 3.0, "silent": true},
	# No mouth drawn at all (Spooked, Thunk, Scribble); borrows Default's shape when she speaks.
	"none": {"src": "spooked", "range": Vector2(15, 10), "open": 3.0, "silent": false},
	# ---- from the reference folder ----
	"bashful": {"src": "bashful", "range": Vector2(30, 18), "open": 3.0, "silent": true},
	"strange": {"src": "strange", "range": Vector2(25, 12), "open": 3.0, "silent": true},
	"smug": {"src": "smug", "range": Vector2(30, 20), "open": 3.2, "silent": true},
}

# Your original 7 drawings as named shortcuts: pose -> eyes + mouth.
const POSES := {
	"default": {"eyes": "default", "mouth": "default"},
	"anger": {"eyes": "anger", "mouth": "anger"},
	"smirk": {"eyes": "bottomlid", "mouth": "smirk"},
	"pout": {"eyes": "really", "mouth": "pout"},
	"spooked": {"eyes": "spooked", "mouth": "none"},
	"tired": {"eyes": "tired", "mouth": "trauma"},
	"tsun": {"eyes": "tsun", "mouth": "tsun"},
	# ---- from the reference folder ----
	"bashful": {"eyes": "bashful", "mouth": "bashful"},
	"thunk": {"eyes": "thunk", "mouth": "none"},
	"scribble": {"eyes": "scribble", "mouth": "none"},
	"strange": {"eyes": "strange", "mouth": "strange"},
	"smug": {"eyes": "smug", "mouth": "smug"},
}

# Moods the brain may still send (old protocol) and plain-English synonyms.
# Each points at a POSE (or gives eyes/mouth directly). Optional "look" is a
# default gaze for that mood (-1..1 each axis).
const ALIASES := {
	"idle": {"pose": "default"},
	"happy": {"pose": "smirk"},
	"proud": {"pose": "smirk"},
	"stressed": {"pose": "spooked"},
	"alert": {"pose": "spooked"},
	"scared": {"pose": "spooked"},
	"sleepy": {"pose": "tired"},
	"exhausted": {"pose": "tired"},
	"thinking": {"pose": "default", "look": Vector2(-0.6, -0.8)},
	"listening": {"pose": "default"},
	"bored": {"pose": "pout", "look": Vector2(0.5, 0.2)},
	"unimpressed": {"pose": "pout"},
	"annoyed": {"pose": "anger"},
	"angry": {"pose": "anger"},
	"embarrassed": {"pose": "tsun"},
	"flustered": {"pose": "tsun"},
	# mixed faces the shortcuts can't express
	"deadpan": {"eyes": "really", "mouth": "default"},
	"skeptical": {"eyes": "really", "mouth": "smirk"},
	"sheepish": {"eyes": "tsun", "mouth": "pout"},
	"numb": {"eyes": "tired", "mouth": "default"},
	# ---- from the reference folder ----
	"flattered": {"pose": "bashful"},
	"blushy": {"pose": "bashful"},
	"shutdown": {"pose": "thunk"},
	"rebooting": {"pose": "thunk"},
	"asleep": {"pose": "thunk"},
	"scrambled": {"pose": "scribble"},
	"overloaded": {"pose": "scribble"},
	"glitched": {"pose": "scribble"},
	"unhinged": {"pose": "strange"},
	"glitchy": {"pose": "strange"},
	"gloating": {"pose": "smug"},
	"grinning": {"pose": "smug"},
}


## Resolve a pose / mood / synonym to {eyes, mouth, look?}.
## Unknown names fall back to the default pose rather than erroring.
static func resolve(name: String) -> Dictionary:
	var key := name.strip_edges().to_lower()
	if POSES.has(key):
		return (POSES[key] as Dictionary).duplicate()
	if ALIASES.has(key):
		var a: Dictionary = (ALIASES[key] as Dictionary).duplicate()
		if a.has("pose"):
			var p: Dictionary = (POSES[a["pose"]] as Dictionary).duplicate()
			if a.has("look"):
				p["look"] = a["look"]
			return p
		return a
	return (POSES[DEFAULT_POSE] as Dictionary).duplicate()


## The pose name whose eyes AND mouth match, or "" for a custom mix.
static func pose_for(eyes: String, mouth: String) -> String:
	for n: String in POSES:
		if POSES[n]["eyes"] == eyes and POSES[n]["mouth"] == mouth:
			return n
	return ""


static func has_eyes(name: String) -> bool:
	return EYES.has(name.strip_edges().to_lower())


static func has_mouth(name: String) -> bool:
	return MOUTHS.has(name.strip_edges().to_lower())
