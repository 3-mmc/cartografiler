// 3D landmarks (trees, houses, boulders) instanced per tile. Each instance's transform holds
// its tile-relative position (origin: u, height/S, v) and its rotation and size (basis, in tile
// units), so float32 keeps precision at any zoom; this shader maps them into the floating
// origin the same way the terrain tile is mapped, so models stand exactly on the ground.
shader_type spatial;
render_mode skip_vertex_transform, cull_back, diffuse_burley;

uniform vec2 tile_origin;        // render units: the tile's min corner (x, z)
uniform float tile_render = 1.0; // render units across the tile
uniform float tile_world = 1.0;  // world units across the tile
uniform float height_scale = 1.0;
uniform float height_offset = 0.0;
uniform float sink = 0.0;
uniform vec3 fixed_colour : source_color = vec3(0.30, 0.24, 0.18);   // trunks, walls
uniform float appear = 1.0;      // shrinks models in as the tile arrives
uniform float animate = 0.0;     // steam: geysers and volcanic smoke billow

varying float part;
varying vec3 tint;

void vertex() {
	vec3 o = MODEL_MATRIX[3].xyz;
	vec3 v = VERTEX;
	if (animate > 0.0) {
		float phase = o.x * 97.0 + o.z * 53.0;
		v.y *= 0.85 + 0.2 * sin(TIME * 2.3 + phase);
		v.xz *= 1.0 + 0.18 * sin(TIME * 1.7 + v.y * 6.0 + phase);
		v.x += 0.06 * sin(TIME * 0.9 + phase) * v.y;
	}
	vec3 local = (MODEL_MATRIX * vec4(v, 0.0)).xyz * appear;
	vec3 p = vec3(tile_origin.x + (o.x + local.x) * tile_render,
	              o.y * tile_world * height_scale + height_offset - sink + local.y * tile_render,
	              tile_origin.y + (o.z + local.z) * tile_render);
	VERTEX = (VIEW_MATRIX * vec4(p, 1.0)).xyz;
	NORMAL = normalize((VIEW_MATRIX * vec4(mat3(MODEL_MATRIX) * NORMAL, 0.0)).xyz);
	part = UV.x;
	tint = COLOR.rgb;
}

void fragment() {
	ALBEDO = mix(fixed_colour, tint, part);
	ROUGHNESS = 0.88;
	SPECULAR = 0.2;
}
