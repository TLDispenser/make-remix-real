#!/usr/bin/env python3
"""
svg_to_shapes.py: turns Remix's face SVGs (art/faces/*.svg) into
scripts/face_shapes.gd, the data file the Godot face engine morphs between.

Re-run this any time you edit or add an SVG:
    python3 tools/svg_to_shapes.py

THE RIBBON CONTRACT (why every pose can ease into every other)
  Every eye and mouth is a 10-point polygon that is really a "ribbon" of
  5 rungs: point i is paired with point 9-i, and each rung is a cross-section.
  The engine fills it as 4 quads between neighbouring rungs, always with the
  same triangle layout, so a spring-driven morph can never produce an
  invalid draw. Shapes drawn with fewer points (Tsun's 6-point chevron eyes)
  are resampled to 5 rungs EXACTLY (linear split of existing rungs), so they
  look identical but morph like everything else.

Per pose the generator extracts:
  left_eye, right_eye, mouth   10 points each, relative to screen center
  eye_fill / eye_stroke        filled eyes vs. outline eyes (Spooked)
  lines                        thin extra strokes (Spooked, Tired); 0 or 4
Coordinates are in the SVG's 1920x1080 design space, shifted so (0,0) is the
screen center.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "art" / "faces"
OUT = ROOT / "scripts" / "face_shapes.gd"

CENTER = (960.0, 540.0)
RUNGS = 5
# svg file stem -> pose name. Add new SVGs here. First 7: the originals
# (user's reference art). Last 5: poses from the reference folder that were
# never wired in until now: Bashful (lowered eyes + blush hatching),
# Eyes-Closed-Thunking (wedge lids) -> thunk, Scribble (the 21-segment knot,
# carried as degenerate eyes + line strokes), Strange/- (asymmetric deranged),
# and the Squidward smug grin -> smug.
POSES = ["default", "anger", "smirk", "pout", "spooked", "tired", "tsun",
         "bashful", "thunk", "scribble", "strange", "smug"]

# Horizontal: every pose's eye pair and mouth are centered on x = 0 (always on).
# Vertical: your SVGs put the whole face ABOVE the screen's middle (the default
# face sits ~82 design px high). With RECENTER_VERTICAL, every pose is shifted
# down by the SAME amount so the ANCHOR pose's face is vertically centered.
# Poses keep their drawn relationship to each other (a low Tired mouth stays low).
RECENTER_VERTICAL = True
ANCHOR = "default"


def parse_styles(svg: str) -> dict:
    styles = {}
    for m in re.finditer(r"\.(cls-\d+)\{([^}]*)\}", svg):
        props = {}
        for kv in m.group(2).split(";"):
            if ":" in kv:
                k, v = kv.split(":", 1)
                props[k.strip()] = v.strip()
        styles[m.group(1)] = props
    return styles


def parse_points(s: str):
    return [tuple(map(float, p.split(","))) for p in s.split()]


def lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def to_ribbon(pts):
    """Return 10 points ordered [a0..a4, b4..b0] (rung k = a_k, b_k)."""
    n = len(pts)
    if n == 10:
        return pts
    if n == 6:
        # Chevron: rungs are (p0,p1) bottom end, (p5,p2) tip, (p4,p3) top end.
        r0 = (pts[0], pts[1])
        r1 = (pts[5], pts[2])
        r2 = (pts[4], pts[3])
        m01 = (lerp(r0[0], r1[0], 0.5), lerp(r0[1], r1[1], 0.5))
        m12 = (lerp(r1[0], r2[0], 0.5), lerp(r1[1], r2[1], 0.5))
        rungs = [r0, m01, r1, m12, r2]
        a = [r[0] for r in rungs]
        b = [r[1] for r in rungs]
        return a + b[::-1]
    raise ValueError(f"unsupported point count {n}")


def mirror_ok(pts, side):
    """side -1 = left eye (outer = left/negative x), +1 = right eye."""
    a = sum(p[0] for p in pts[:5])
    b = sum(p[0] for p in pts[5:])
    return a < b if side < 0 else a > b


def center_units(p):
    """Per pose, find each unit's own horizontal origin and shift it to x = 0:
    the EYE unit (both eyes + their lines, as one block) and the MOUTH.
    Vertical placement is left exactly as drawn (a low Tired mouth is on purpose)."""
    xs = [q[0] for q in p["left_eye"] + p["right_eye"]]
    ex = (min(xs) + max(xs)) / 2
    p["left_eye"] = [(round(x - ex, 3), y) for x, y in p["left_eye"]]
    p["right_eye"] = [(round(x - ex, 3), y) for x, y in p["right_eye"]]
    p["lines"] = [((round(a[0] - ex, 3), a[1]), (round(b[0] - ex, 3), b[1]), w) for a, b, w in p["lines"]]
    mx = [q[0] for q in p["mouth"]]
    mc = (min(mx) + max(mx)) / 2
    p["mouth"] = [(round(x - mc, 3), y) for x, y in p["mouth"]]
    return ex, mc


def face_bbox_cy(p):
    ys = [q[1] for q in p["left_eye"] + p["right_eye"] + p["mouth"]]
    return (min(ys) + max(ys)) / 2


def shift_y(p, dy):
    p["left_eye"] = [(x, round(y + dy, 3)) for x, y in p["left_eye"]]
    p["right_eye"] = [(x, round(y + dy, 3)) for x, y in p["right_eye"]]
    p["mouth"] = [(x, round(y + dy, 3)) for x, y in p["mouth"]]
    p["lines"] = [((a[0], round(a[1] + dy, 3)), (b[0], round(b[1] + dy, 3)), w) for a, b, w in p["lines"]]


def centroid(pts):
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def rel(p):
    return (round(p[0] - CENTER[0], 3), round(p[1] - CENTER[1], 3))


def classify(polys):
    """Assign polygons to left_eye / right_eye / mouth by position."""
    out = {}
    eyes = []
    for pts in polys:
        cx, cy = centroid(pts)
        if 900 <= cx <= 1020 and cy > 560:
            out["mouth"] = pts
        else:
            eyes.append((cx, pts))
    eyes.sort(key=lambda e: e[0])
    if len(eyes) != 2:
        raise ValueError(f"expected 2 eye polygons, found {len(eyes)}")
    out["left_eye"], out["right_eye"] = eyes[0][1], eyes[1][1]
    return out


def parse_pose(name: str) -> dict:
    svg = (ART / f"{name}.svg").read_text()
    styles = parse_styles(svg)
    polys, outline_of = [], {}
    for m in re.finditer(r'<polygon points="([^"]+)" class="([^"]+)"', svg):
        pts = parse_points(m.group(1))
        sty = styles.get(m.group(2), {})
        outline_of[id(pts)] = float(sty.get("stroke-width", "0")) if sty.get("fill") == "none" else 0.0
        polys.append(pts)
    lines = []
    for m in re.finditer(
        r'<line x1="([\d.]+)" y1="([\d.]+)" x2="([\d.]+)" y2="([\d.]+)" class="([^"]+)"', svg
    ):
        x1, y1, x2, y2 = map(float, m.groups()[:4])
        w = float(styles.get(m.group(5), {}).get("stroke-width", "3"))
        lines.append(((x1, y1), (x2, y2), w))
    feats = classify(polys)
    has_mouth = "mouth" in feats
    eye_outline = min(outline_of[id(feats["left_eye"])], outline_of[id(feats["right_eye"])])
    left = to_ribbon(feats["left_eye"])
    right = to_ribbon(feats["right_eye"])
    # Eyes must be mirror images in POINT ORDER (point 0 = outer side on both),
    # or the morph twists. Reversing a ribbon's list swaps its two sides.
    if not mirror_ok(left, -1):
        left = left[::-1]
    if not mirror_ok(right, +1):
        right = right[::-1]
    return {
        "left_eye": [rel(p) for p in left],
        "right_eye": [rel(p) for p in right],
        "has_mouth": has_mouth,
        "mouth": [rel(p) for p in to_ribbon(feats["mouth"])] if has_mouth else None,
        "eye_outline": eye_outline,
        "lines": [(rel(a), rel(b), w) for a, b, w in lines],
    }


def v(p):
    return f"Vector2({p[0]:g}, {p[1]:g})"


def emit(poses: dict) -> str:
    out = []
    out.append("# face_shapes.gd: GENERATED by tools/svg_to_shapes.py from art/faces/*.svg.")
    out.append("# Do not edit by hand; edit the SVGs and re-run the generator.")
    out.append("# Coordinates: SVG design space (1920x1080), (0,0) = screen center.")
    out.append("# Each eye/mouth = 10 points = 5 'ribbon' rungs (point i pairs with 9-i).")
    out.append("class_name FaceShapes")
    out.append("")
    out.append("const POINTS := 10")
    out.append("const RUNGS := 5")
    out.append("")
    out.append("static func all() -> Dictionary:")
    out.append("\treturn {")
    for name, p in poses.items():
        out.append(f'\t\t"{name}": {{')
        for key in ("left_eye", "right_eye"):
            out.append(f'\t\t\t"{key}": PackedVector2Array([{", ".join(v(q) for q in p[key])}]),')
        # Mouthless poses (Spooked) already borrowed Default's mouth in main().
        out.append(f'\t\t\t"mouth": PackedVector2Array([{", ".join(v(q) for q in p["mouth"])}]),')
        out.append(f'\t\t\t"has_mouth": {str(p["has_mouth"]).lower()},')
        out.append(f'\t\t\t"eye_outline": {p["eye_outline"]:g},')
        ls = ", ".join(f"[{v(a)}, {v(b)}, {w:g}]" for a, b, w in p["lines"])
        out.append(f'\t\t\t"lines": [{ls}],')
        out.append("\t\t},")
    out.append("\t}")
    out.append("")
    return "\n".join(out)


def build_poses(verbose=False):
    poses = {}
    for name in POSES:
        poses[name] = parse_pose(name)
    # Mouthless poses (Spooked) borrow Default's mouth shape for when she
    # speaks; the engine hides it while she is silent (see face_config.gd).
    for name, p in poses.items():
        if not p["has_mouth"]:
            p["mouth"] = list(poses["default"]["mouth"])
    for name, p in poses.items():
        ex, mc = center_units(p)
        if verbose:
            print(f"  centered {name:8s} eyes {ex:+7.1f}px  mouth {mc:+7.1f}px")
    if RECENTER_VERTICAL:
        dy = -face_bbox_cy(poses[ANCHOR])
        for p in poses.values():
            shift_y(p, dy)
        if verbose:
            print(f"  vertical recenter: every pose shifted {dy:+.1f}px so '{ANCHOR}' sits on the screen's middle")
    return poses


def main():
    poses = build_poses(verbose=True)
    OUT.write_text(emit(poses))
    print(f"wrote {OUT.relative_to(ROOT)}: {len(poses)} poses")
    for name, p in poses.items():
        print(f"  {name:8s} outline={p['eye_outline']:g} lines={len(p['lines'])} mouth_drawn={p['has_mouth']}")


if __name__ == "__main__":
    sys.exit(main())