// screen_fx.gdshader: Remix's glass, styled after her companion's CRT. A full-window post effect over the
// whole screen (face, hud, everything). Compatibility-renderer friendly:
// ~10 texture reads per pixel, no loops over big radii.
//   glow      soft phosphor bloom (8 taps)
//   scanlines fine horizontal lines, fixed in real screen pixels
//   bleed     tiny red/blue fringe at edges of bright shapes
//   vignette  gentle darkening toward the corners
//   flicker   very subtle brightness wobble
shader_type canvas_item;
render_mode blend_disabled;

uniform sampler2D screen_tex : hint_screen_texture, filter_linear, repeat_disable;
uniform float glow_strength : hint_range(0.0, 1.5) = 0.55;
uniform float glow_radius : hint_range(0.0, 12.0) = 5.0;
uniform float scanline_strength : hint_range(0.0, 0.5) = 0.10;
uniform float bleed_px : hint_range(0.0, 3.0) = 0.8;
uniform float vignette : hint_range(0.0, 1.0) = 0.35;
uniform float flicker_strength : hint_range(0.0, 0.2) = 0.03;

void fragment() {
	vec2 px = SCREEN_PIXEL_SIZE;
	vec2 uv = SCREEN_UV;

	// base with tiny chromatic bleed
	float r = texture(screen_tex, uv + vec2(bleed_px, 0.0) * px).r;
	float g = texture(screen_tex, uv).g;
	float b = texture(screen_tex, uv - vec2(bleed_px, 0.0) * px).b;
	vec3 col = vec3(r, g, b);

	// phosphor bloom
	vec3 glow = vec3(0.0);
	for (int i = 0; i < 8; i++) {
		float a = float(i) * 0.7853982;
		vec2 o = vec2(cos(a), sin(a)) * glow_radius * px;
		glow += texture(screen_tex, uv + o).rgb;
	}
	col += (glow / 8.0) * glow_strength;

	// scanlines (period 3 real pixels)
	float scan = 0.5 + 0.5 * sin(FRAGCOORD.y * 2.0943951);
	col *= 1.0 - scanline_strength * scan;

	// vignette
	vec2 d = uv - vec2(0.5);
	col *= 1.0 - vignette * dot(d, d) * 1.6;

	// flicker
	col *= 1.0 - flicker_strength * fract(sin(TIME * 13.7) * 43758.5453) * 0.6;

	// Square pixel-art screen: no rounded corners, fully opaque, no compositor
	// transparency needed.
	COLOR = vec4(col, 1.0);
}
