// One tile of the world. Heights and colours come from the survey service; lighting,
// water and the weather-radar overlay are done here, per pixel.
shader_type spatial;
render_mode cull_back, depth_draw_opaque, diffuse_burley, specular_schlick_ggx;

uniform sampler2D heightmap : filter_linear, repeat_disable;
uniform sampler2D colormap : source_color, filter_linear_mipmap_anisotropic, repeat_disable;
uniform sampler2D auxmap : filter_linear, repeat_disable;
// Material weights (MATERIALS order in branchfm/tiles.py): meadow, forest, field, town | wet, snow, rock, sand.
uniform sampler2D mat_a : filter_linear, repeat_disable;
uniform sampler2D mat_b : filter_linear, repeat_disable;
// CC0 detail maps (native/textures): RG normal, B luminance detail. Sampled at two world-anchored
// scales one octave apart and cross-faded with zoom, so ground grain looks the same at any height.
uniform sampler2DArray detail : filter_linear_mipmap_anisotropic, repeat_enable;
uniform vec4 detail_uv0 = vec4(0.0, 0.0, 8.0, 8.0);   // offset.xy, repeats across the tile.zw
uniform vec4 detail_uv1 = vec4(0.0, 0.0, 4.0, 4.0);
uniform float detail_mix = 0.0;
uniform float detail_strength = 0.4;
uniform float height_scale = 1.0;    // render units per world unit, vertical exaggeration included
uniform float height_offset = 0.0;   // (tile base - reference height), already in render units
uniform float texel_world = 1.0;     // world units between height samples
uniform float exaggeration = 4.0;
uniform float weather_on = 1.0;
uniform float time_s = 0.0;
uniform float sink = 0.0;            // coarse fallback tiles sit just under finer ones
// Which of this tile's sub-cells a finer tile has already drawn. A coarse stand-in and the
// finer tile over it disagree about the ground by far more than any clearance can hide (a
// quarter of the view, where the finer tile has descended into a child territory), so the
// stand-in gives up those cells entirely instead of fighting for them.
uniform int covered = 0;             // bitmask, row-major over covered_n x covered_n
uniform int covered_n = 1;

varying vec2 tuv;
varying vec2 puv;
varying vec3 world_pos;

const float SAMPLES = 257.0;

vec2 texel_uv(vec2 uv) {
	return (uv * (SAMPLES - 1.0) + 0.5) / SAMPLES;
}

float hash(vec2 p) {
	return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

// Gradient noise and its slope, for relief finer than a tile sample.
vec2 hash2(vec2 p) {
	p = vec2(dot(p, vec2(127.1, 311.7)), dot(p, vec2(269.5, 183.3)));
	return fract(sin(p) * 43758.5453) * 2.0 - 1.0;
}

// Analytic gradient of the same cubic-interpolated gradient noise. Four corner
// hashes replace four complete noise evaluations per slope (16 corner hashes).
vec2 gnoise_slope(vec2 p) {
	vec2 i = floor(p);
	vec2 f = fract(p);
	vec2 u = f * f * (3.0 - 2.0 * f);
	vec2 du = 6.0 * f * (1.0 - f);
	vec2 g00 = hash2(i);
	vec2 g10 = hash2(i + vec2(1.0, 0.0));
	vec2 g01 = hash2(i + vec2(0.0, 1.0));
	vec2 g11 = hash2(i + vec2(1.0, 1.0));
	float a = dot(g00, f);
	float b = dot(g10, f - vec2(1.0, 0.0));
	float c = dot(g01, f - vec2(0.0, 1.0));
	float d = dot(g11, f - vec2(1.0, 1.0));
	return mix(mix(g00, g10, u.x), mix(g01, g11, u.x), u.y)
		+ du * vec2(mix(b - a, d - c, u.y), mix(c - a, d - b, u.x));
}

vec2 micro_slope(vec2 p) {
	// Three octaves of analytic slope, with the existing octave weights.
	vec2 g = vec2(0.0);
	float amp = 1.0;
	for (int o = 0; o < 3; o++) {
		g += amp * gnoise_slope(p);
		p = p * 2.07 + vec2(17.3, 9.1);
		amp *= 0.5;
	}
	return g;
}

float vnoise(vec2 p) {
	vec2 i = floor(p);
	vec2 f = fract(p);
	f = f * f * (3.0 - 2.0 * f);
	return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x), f.y);
}

void vertex() {
	tuv = texel_uv(UV);
	puv = UV;
	float h = textureLod(heightmap, tuv, 0.0).r;
	VERTEX.y = h * height_scale + height_offset - sink;
	world_pos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	if (covered != 0) {
		int cx = int(floor(clamp(puv.x, 0.0, 0.999) * float(covered_n)));
		int cy = int(floor(clamp(puv.y, 0.0, 0.999) * float(covered_n)));
		if ((covered & (1 << (cy * covered_n + cx))) != 0) {
			discard;
		}
	}
	float e = 1.0 / SAMPLES;
	float hl = textureLod(heightmap, tuv - vec2(e, 0.0), 0.0).r;
	float hr = textureLod(heightmap, tuv + vec2(e, 0.0), 0.0).r;
	float hd = textureLod(heightmap, tuv - vec2(0.0, e), 0.0).r;
	float hu = textureLod(heightmap, tuv + vec2(0.0, e), 0.0).r;
	// Slopes are scale-free: rise over run in world units, times the exaggeration.
	vec3 n = normalize(vec3(-(hr - hl) / (2.0 * texel_world) * exaggeration, 1.0, -(hu - hd) / (2.0 * texel_world) * exaggeration));

	vec4 c = texture(colormap, tuv);
	vec4 aux = texture(auxmap, tuv);
	float water = smoothstep(0.35, 0.8, c.a);
	vec3 albedo = c.rgb;

	// Ground detail from the materials under this pixel.
	vec4 wa = texture(mat_a, tuv);
	vec4 wb = texture(mat_b, tuv);
	float weights[8] = float[8](wa.r, wa.g, wa.b, wa.a, wb.r, wb.g, wb.b, wb.a);
	vec2 uv0 = detail_uv0.xy + puv * detail_uv0.zw;
	vec2 uv1 = detail_uv1.xy + puv * detail_uv1.zw;
	float lum = 0.0;
	vec2 bump = vec2(0.0);
	float total = 0.0;
	for (int i = 0; i < 8; i++) {
		float w = weights[i];
		if (w < 0.02) continue;
		vec4 d = mix(texture(detail, vec3(uv0, float(i))), texture(detail, vec3(uv1, float(i))), detail_mix);
		lum += w * (d.b - 0.5);
		bump += w * (d.rg * 2.0 - 1.0);
		total += w;
	}
	if (total > 0.0) {
		float k = clamp(total, 0.0, 1.0) * (1.0 - water);
		albedo *= 1.0 + detail_strength * 2.0 * lum * k;
		n = normalize(n + vec3(bump.x, 0.0, -bump.y) * 0.4 * k);
		// Micro-relief below the tile's sample spacing, drawn here at screen resolution:
		// rough on rock, soft on fields and water-smoothed ground. Same world-anchored
		// frames as the detail textures, so it neither swims nor jumps while zooming.
		float rough = dot(wa, vec4(0.35, 0.55, 0.12, 0.2)) + dot(wb, vec4(0.15, 0.3, 1.0, 0.25));
		vec2 s0 = micro_slope(uv0 * 6.0);
		vec2 s1 = micro_slope(uv1 * 6.0);
		vec2 slope = mix(s0, s1, detail_mix) * rough / max(total, 0.001);
		n = normalize(n + vec3(-slope.x, 0.0, -slope.y) * 0.06 * k);
	}

	// Water: gloss, a little depth tint, and slow ripples.
	if (water > 0.0) {
		vec2 p = world_pos.xz * 0.9 + vec2(time_s * 0.05, time_s * 0.03);
		// Open ocean is calm, like the plane beyond the world's edge; rivers and shallows ripple.
		vec2 ripple = vec2(vnoise(p * 3.0) - 0.5, vnoise(p * 3.0 + 17.0) - 0.5) * 0.12 * (1.0 - 0.9 * aux.b);
		n = normalize(mix(n, normalize(vec3(ripple.x, 1.0, ripple.y)), water));
		albedo = mix(albedo, albedo * vec3(0.85, 0.95, 1.0), water * aux.b);
	}

	// Weather radar: files changed today, drawn the way rain radar is.
	float rain = aux.r;
	if (weather_on > 0.5 && rain > 0.01) {
		vec2 p = world_pos.xz * 0.35 + vec2(time_s * 0.04, -time_s * 0.02);
		float cells = smoothstep(0.35, 0.75, vnoise(p) * 0.6 + vnoise(p * 2.7) * 0.4 + rain * 0.35);
		vec3 radar = rain < 0.35 ? mix(vec3(0.20, 0.70, 0.35), vec3(0.85, 0.85, 0.25), rain / 0.35)
		                         : mix(vec3(0.85, 0.85, 0.25), vec3(0.85, 0.30, 0.20), clamp((rain - 0.35) / 0.65, 0.0, 1.0));
		albedo = mix(albedo, radar, 0.42 * cells * smoothstep(0.01, 0.08, rain));
	}

	ALBEDO = albedo;
	NORMAL = normalize((VIEW_MATRIX * vec4(n, 0.0)).xyz);
	ROUGHNESS = mix(0.92, 0.45, water);
	SPECULAR = mix(0.25, 0.3, water);
}
