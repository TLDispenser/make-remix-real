// CRT screen shader — Remix's head-panel look. Verified against Godot 4.7
// canvas_item shader docs. Effects are independent + cheap (compat renderer):
//   glow_color/glow_strength  — phosphor halo from screen center
//   scanline_strength         — horizontal scanlines
//   flicker_strength          — subtle CRT brightness wobble over TIME
//   bleed                     — alpha fade at panel edges (phosphor bleed)
// theme_color mirrors face.gd's theme_color so ONE value drives the face.
shader_type canvas_item;

uniform vec4 theme_color : source_color = vec4(0.95, 0.12, 0.10, 1.0);
uniform float glow_strength : hint_range(0.0, 1.0) = 0.38;
uniform float scanline_strength : hint_range(0.0, 1.0) = 0.14;
uniform float flicker_strength : hint_range(0.0, 1.0) = 0.05;
uniform float bleed : hint_range(0.0, 0.5) = 0.25;

void fragment() {
	vec4 base = COLOR;

	// Phosphor halo: brightest near panel center, falls off to corners.
	float d = length(UV - vec2(0.5)) * 1.6;
	float halo = pow(max(1.0 - d, 0.0), 2.0);
	base.rgb += theme_color.rgb * glow_strength * halo * 0.55;

	// Scanlines: fine horizontal lines, scaled to panel UV.
	float scan = 0.5 + 0.5 * sin(UV.y * 6.2831853 * 170.0);
	base.rgb *= 1.0 - scanline_strength * scan * 0.9;

	// Subtle flicker: low-frequency brightness wobble, never harsh.
	float n = fract(sin(TIME * 13.7) * 43758.5453);
	base.rgb *= 1.0 - flicker_strength * n * 0.6;

	// Edge bleed: fade alpha slightly toward panel border.
	float edge = max(abs(UV.x - 0.5), abs(UV.y - 0.5)) * 2.0;
	base.a *= 1.0 - bleed * smoothstep(0.75, 1.0, edge);

	COLOR = base;
}
