# face_config.gd: the hand-tunable half of Remix's face.
# face_shapes.gd (generated from your SVGs) says WHAT each pose looks like.
# This file says how much each pose is ALLOWED to move. Edit freely.
#
# All distances are SVG design-space pixels (the 1920x1080 canvas).
#
#   eye_range    max offset of the whole EYE unit  (x = side to side, y = up/down)
#   mouth_range  max offset of the whole MOUTH unit
#   widen        max extra width of the mouth while sound plays (0.3 = +30%)
#   blink        does this pose blink at all
#   blink_gap    multiplier on the time between blinks (<1 = more often)
#   blink_speed  multiplier on blink speed (<1 = slow, heavy blink)
#   tremble      constant tiny shake in px (Spooked)
#   mouth_when_silent  false = mouth is hidden unless she is making sound
#   twitch       may the idle bottom-lid twitch fire in this pose
class_name FaceConfig

const DEFAULT_POSE := "default"

const POSES := {
	"default": {"eye_range": Vector2(70, 40), "mouth_range": Vector2(30, 18), "widen": 0.30,
		"blink": true, "blink_gap": 1.0, "blink_speed": 1.0, "tremble": 0.0,
		"mouth_when_silent": true, "twitch": false},
	"anger": {"eye_range": Vector2(35, 20), "mouth_range": Vector2(20, 10), "widen": 0.22,
		"blink": true, "blink_gap": 1.3, "blink_speed": 1.0, "tremble": 0.0,
		"mouth_when_silent": true, "twitch": true},
	"smirk": {"eye_range": Vector2(60, 35), "mouth_range": Vector2(30, 15), "widen": 0.18,
		"blink": true, "blink_gap": 1.0, "blink_speed": 1.0, "tremble": 0.0,
		"mouth_when_silent": true, "twitch": false},
	"pout": {"eye_range": Vector2(50, 30), "mouth_range": Vector2(25, 12), "widen": 0.28,
		"blink": true, "blink_gap": 1.2, "blink_speed": 0.9, "tremble": 0.0,
		"mouth_when_silent": true, "twitch": true},
	"spooked": {"eye_range": Vector2(25, 15), "mouth_range": Vector2(15, 10), "widen": 0.25,
		"blink": false, "blink_gap": 1.0, "blink_speed": 1.0, "tremble": 2.0,
		"mouth_when_silent": false, "twitch": false},
	"tired": {"eye_range": Vector2(50, 50), "mouth_range": Vector2(25, 15), "widen": 0.25,
		"blink": true, "blink_gap": 0.7, "blink_speed": 0.55, "tremble": 0.0,
		"mouth_when_silent": true, "twitch": false},
	"tsun": {"eye_range": Vector2(30, 20), "mouth_range": Vector2(20, 12), "widen": 0.30,
		"blink": true, "blink_gap": 1.0, "blink_speed": 1.0, "tremble": 0.0,
		"mouth_when_silent": true, "twitch": false},
}

# Moods the brain may still send (old protocol) and plain-English synonyms,
# mapped to the closest DRAWN pose for now. Redirect any of these freely; when
# you draw a new pose, add it to POSES (+ the generator) and point its mood here.
# Optional "look" is a default gaze for that mood (-1..1 each axis).
const ALIASES := {
	"idle": {"pose": "default"},
	"happy": {"pose": "smirk"},
	"proud": {"pose": "smirk"},
	"smug": {"pose": "smirk"},
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
}


## Resolve any incoming name (pose, mood, synonym) to {pose, look?}.
## Unknown names fall back to the default pose rather than erroring.
static func resolve(name: String) -> Dictionary:
	var key := name.strip_edges().to_lower()
	if POSES.has(key):
		return {"pose": key}
	if ALIASES.has(key):
		return ALIASES[key].duplicate()
	return {"pose": DEFAULT_POSE}
