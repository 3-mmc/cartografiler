// One tile of the world. Heights and colours come from the survey service; lighting,
// water and the weather-radar overlay are done here, per pixel.
shader_type spatial;
render_mode cull_back, depth_draw_opaque, diffuse_burley, specular_schlick_ggx;

uniform sampler2D heightmap : filter_linear, repeat_disable;
uniform sampler2D colormap : source_color, filter_linear_mipmap_anisotropic, repeat_disable;
uniform sampler2D auxmap : filter_linear, repeat_disable;
uniform float height_scale = 1.0;    // render units per world unit, vertical exaggeration included
uniform float height_offset = 0.0;   // (tile base - reference height), already in render units
uniform float texel_world = 1.0;     // world units between height samples
uniform float exaggeration = 4.0;
uniform float weather_on = 1.0;
uniform float time_s = 0.0;
uniform float sink = 0.0;            // coarse fallback tiles sit just under finer ones

varying vec2 tuv;
varying vec3 world_pos;

const float SAMPLES = 257.0;

vec2 texel_uv(vec2 uv) {
	return (uv * (SAMPLES - 1.0) + 0.5) / SAMPLES;
}

float hash(vec2 p) {
	return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

float vnoise(vec2 p) {
	vec2 i = floor(p);
	vec2 f = fract(p);
	f = f * f * (3.0 - 2.0 * f);
	return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x), f.y);
}

void vertex() {
	tuv = texel_uv(UV);
	float h = textureLod(heightmap, tuv, 0.0).r;
	VERTEX.y = h * height_scale + height_offset - sink;
	world_pos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
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

	// Water: gloss, a little depth tint, and slow ripples.
	if (water > 0.0) {
		vec2 p = world_pos.xz * 0.9 + vec2(time_s * 0.05, time_s * 0.03);
		vec2 ripple = vec2(vnoise(p * 3.0) - 0.5, vnoise(p * 3.0 + 17.0) - 0.5) * 0.12;
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
	ROUGHNESS = mix(0.92, 0.18, water);
	SPECULAR = mix(0.25, 0.55, water);
}
