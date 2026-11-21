#!/usr/bin/env python3
"""
verify_shapes.py: proves the generated ribbon data reproduces your SVGs.

For every pose it draws the shapes the way the Godot engine does (4 quads per
feature, each split into 2 triangles, same fixed layout) and compares the
result with a real SVG render. Needs: pip install cairosvg pillow numpy
Prints per-pose overlap (IoU) and exits non-zero if any pose drifts.
"""
import io
import sys
from pathlib import Path

import cairosvg
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
import svg_to_shapes as g  # noqa: E402

W, H = 1920, 1080
BG = (0x0D, 0x10, 0x18)


def ribbon_tris(p):
    """Same layout as face.gd: quad k = (a=p[k], b=p[k+1], c=p[8-k], d=p[9-k])."""
    tris = []
    for k in range(4):
        a, b, c, d = p[k], p[k + 1], p[8 - k], p[9 - k]
        tris.append((a, b, c))
        tris.append((a, c, d))
    return tris


def render_pose(pose):
    img = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(img)

    def abs_(p):
        return (p[0] + g.CENTER[0], p[1] + g.CENTER[1])

    for key in ("left_eye", "right_eye", "mouth"):
        if key == "mouth" and not pose["has_mouth"]:
            continue
        pts = pose[key]
        if key != "mouth" and pose["eye_outline"] > 0:
            loop = [abs_(q) for q in pts] + [abs_(pts[0])]
            dr.line(loop, fill=255, width=int(pose["eye_outline"]), joint="curve")
            continue
        for t in ribbon_tris(pts):
            dr.polygon([abs_(q) for q in t], fill=255)
    for a, b, w in pose["lines"]:
        dr.line([abs_(a), abs_(b)], fill=255, width=int(w))
    return np.array(img) > 127


def render_svg(name):
    png = cairosvg.svg2png(url=str(g.ART / f"{name}.svg"), output_width=W, output_height=H)
    arr = np.array(Image.open(io.BytesIO(png)).convert("RGB")).astype(int)
    # "lit" = clearly brighter than the background
    return (arr[..., 0] > 90)


def main():
    bad = 0
    for name in g.POSES:
        pose = g.parse_pose(name)
        mine = render_pose(pose)
        ref = render_svg(name)
        inter = np.logical_and(mine, ref).sum()
        union = np.logical_or(mine, ref).sum()
        iou = inter / union if union else 1.0
        status = "ok" if iou > 0.93 else "DRIFT"
        if status != "ok":
            bad += 1
        print(f"{name:8s} IoU={iou:.4f} {status}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
