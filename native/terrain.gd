extends RefCounted
# Procedural cartography. The grammar is in docs/cartography.md:
# climate = mount, hydrology = directory tree, geology = file type and age,
# weather = recent activity. Unknown metadata always yields a neutral form.

const CLIMATES = ["Temperate", "Alpine", "Tropical", "Desert", "Wetland"]
const ZONE_CLIMATE = {"native":0, "windows":2, "ephemeral":3, "network":4}
const CLIMATE_REASON = ["Linux-native filesystem", "not writable by you: system high country",
	"Windows volume across the WSL 9p bridge", "virtual filesystem, wiped at boot", "network mount, across the water"]
const LAND = [Color("686e52"), Color("8e928a"), Color("4e6953"), Color("a79777"), Color("60746b")]
const SNOW = Color("e4e8e6")
const WATER = Color("3f7580")
const GENERATED = ["node_modules","__pycache__",".cache","cache","build","dist","target",".venv","venv",".git",
	".tox",".mypy_cache",".pytest_cache",".gradle",".next","tmp","temp",".parcel-cache","obj"]
const DAY = 86400.0
const GROUND = 0.11
const CARVE = 0.07
var materials = {}
var shared_vertex_material: StandardMaterial3D

# ---------------------------------------------------------------- primitives

func material(color: Color, alpha: float = 1.0, glow: float = 0.0, gloss: float = 0.0) -> StandardMaterial3D:
	var key = "%s/%.2f/%.2f/%.2f" % [color.to_html(), alpha, glow, gloss]
	if materials.has(key):
		return materials[key]
	var mat = StandardMaterial3D.new()
	mat.albedo_color = Color(color, alpha)
	mat.roughness = 0.92 - gloss*0.8
	mat.metallic = gloss*0.35
	if alpha < 1.0:
		mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	if glow > 0.0:
		mat.emission_enabled = true
		mat.emission = color
		mat.emission_energy_multiplier = glow
	materials[key] = mat
	return mat

func shape(parent: Node3D, mesh: Mesh, pos: Vector3, color: Color, alpha: float = 1.0, glow: float = 0.0, gloss: float = 0.0) -> MeshInstance3D:
	var instance = MeshInstance3D.new()
	instance.mesh = mesh
	instance.material_override = material(color, alpha, glow, gloss)
	instance.position = pos
	if alpha < 1.0:
		instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	parent.add_child(instance)
	return instance

func cone(parent: Node3D, pos: Vector3, radius_value: float, height: float, color: Color, sides: int = 7, top: float = 0.0, gloss: float = 0.0):
	var mesh = CylinderMesh.new()
	mesh.top_radius = top
	mesh.bottom_radius = radius_value
	mesh.height = height
	mesh.radial_segments = sides
	mesh.rings = 1
	return shape(parent, mesh, pos + Vector3(0, height / 2, 0), color, 1.0, 0.0, gloss)

func box(parent: Node3D, pos: Vector3, dimensions: Vector3, color: Color, alpha: float = 1.0, glow: float = 0.0):
	var mesh = BoxMesh.new()
	mesh.size = dimensions
	return shape(parent, mesh, pos + Vector3(0, dimensions.y / 2, 0), color, alpha, glow)

func globe(parent: Node3D, pos: Vector3, radius_value: float, color: Color, alpha: float = 1.0, flatten: float = 0.85, glow: float = 0.0):
	var mesh = SphereMesh.new()
	mesh.radius = radius_value
	mesh.height = radius_value * 2.0 * flatten
	mesh.radial_segments = 11
	mesh.rings = 5
	return shape(parent, mesh, pos, color, alpha, glow)

func vertex_mesh(vertices: PackedVector3Array, colors: PackedColorArray, indices: PackedInt32Array) -> ArrayMesh:
	var arrays = []
	arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = vertices
	arrays[Mesh.ARRAY_COLOR] = colors
	arrays[Mesh.ARRAY_INDEX] = indices
	var mesh = ArrayMesh.new()
	mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
	var st = SurfaceTool.new()
	st.create_from(mesh, 0)
	st.generate_normals()
	return st.commit()

func vertex_material() -> StandardMaterial3D:
	if shared_vertex_material == null:
		shared_vertex_material = StandardMaterial3D.new()
		shared_vertex_material.vertex_color_use_as_albedo = true
		shared_vertex_material.cull_mode = BaseMaterial3D.CULL_DISABLED
		shared_vertex_material.roughness = 1.0
	return shared_vertex_material

func mesh_node(parent: Node3D, vertices: PackedVector3Array, colors: PackedColorArray, indices: PackedInt32Array) -> MeshInstance3D:
	var instance = MeshInstance3D.new()
	if indices.is_empty():
		return instance
	instance.mesh = vertex_mesh(vertices, colors, indices)
	instance.material_override = vertex_material()
	parent.add_child(instance)
	return instance

static func numeric(value) -> bool:
	return typeof(value) in [TYPE_INT, TYPE_FLOAT]

# ---------------------------------------------------------------- time → rock

static func age_days(entry: Dictionary) -> float:
	var modified = float(entry.get("modified", 0.0))
	if modified <= 0.0:
		return -1.0
	return maxf(0.0, (Time.get_unix_time_from_system() - modified) / DAY)

static func ago(days: float) -> String:
	if days < 0.0: return "at an unknown time"
	if days < 1.0/24.0: return "within the hour"
	if days < 1.0: return "%d hours ago" % int(days*24)
	if days < 14.0: return "%d days ago" % int(days)
	if days < 60.0: return "%d weeks ago" % int(days/7)
	if days < 730.0: return "%d months ago" % int(days/30.4)
	return "%d years ago" % int(days/365.25)

static func rock(age: float) -> Dictionary:
	# Erosion sequence: sharp dark basalt when new, pale rounded granite when old.
	if age < 0.0:
		return {"name":"Undated outcrop","low":Color("6d6c66"),"high":Color("8b8a83"),"sharp":1.0,"steps":0,"spread":1.0,"freq":1.8,"ember":false}
	if age < 7.0:
		return {"name":"Fresh basalt, still cooling" if age < 1.0 else "Fresh basalt","low":Color("222224"),"high":Color("45413f"),
			"sharp":2.3,"steps":0,"spread":0.82,"freq":2.9,"ember":age < 1.0}
	if age < 180.0:
		return {"name":"Weathered basalt","low":Color("3f3a35"),"high":Color("6e645a"),"sharp":1.6,"steps":0,"spread":0.92,"freq":2.3,"ember":false}
	if age < 1095.0:
		return {"name":"Sandstone","low":Color("8a6443"),"high":Color("c79f6d"),"sharp":1.1,"steps":5,"spread":1.05,"freq":1.6,"ember":false}
	return {"name":"Granite","low":Color("8d8480"),"high":Color("d3cac1"),"sharp":0.65,"steps":0,"spread":1.3,"freq":1.0,"ember":false}

# ---------------------------------------------------------------- layout

func positions(entries: Array) -> Array:
	# Seeded best-candidate sampling: no cells, grid, or equal-sized parcels.
	var points = []
	var extent = maxf(4.0, sqrt(entries.size())*2.15)
	for i in entries.size():
		var random = RandomNumberGenerator.new()
		random.seed = hash(entries[i].path)
		var best = Vector3.ZERO
		var best_score = -1.0
		for attempt in 36:
			var angle = random.randf()*TAU
			var radius_value = sqrt(random.randf())*extent
			var point = Vector3(cos(angle)*radius_value, 0, sin(angle)*radius_value*0.83)
			var nearest = 1000.0
			for other in points:
				nearest = minf(nearest, point.distance_squared_to(other))
			var score = nearest - point.length_squared()*0.025
			if score > best_score:
				best = point
				best_score = score
		points.append(best)
	return points

func radius(entries: Array) -> float:
	return maxf(7.0, sqrt(entries.size())*2.15+3.2)

# ---------------------------------------------------------------- hydrology

func meander(from: Vector3, to: Vector3, seed_value: int, amplitude: float, interval: float) -> PackedVector3Array:
	var random = RandomNumberGenerator.new()
	random.seed = seed_value
	var curve = Curve3D.new()
	var delta = to - from
	var side = Vector3(-delta.z, 0, delta.x).normalized()
	var steps = clampi(int(delta.length()/1.6), 3, 9)
	for i in steps+1:
		var t = float(i)/steps
		var swing = sin(t*PI)*random.randf_range(-amplitude, amplitude) if i > 0 and i < steps else 0.0
		curve.add_point(from.lerp(to, t) + side*swing, -delta/(steps*3.0), delta/(steps*3.0))
	curve.bake_interval = interval
	return curve.get_baked_points()

static func nearest_on(line: PackedVector3Array, point: Vector3) -> Vector3:
	var best = line[0]
	for candidate in line:
		if candidate.distance_squared_to(point) < best.distance_squared_to(point):
			best = candidate
	return best

func grid_cell(entries: Array) -> float:
	return radius(entries)*1.35*2.0/128.0

func hydrology(entries: Array, points: Array, facts: Dictionary, seed_value: int, mouth_angle: float) -> Array:
	# Downstream is always toward the parent: the trunk reaches the sea at the outlet.
	var boundary = radius(entries)*0.90
	var interval = maxf(0.07, grid_cell(entries)*0.45)
	var out = Vector3(cos(mouth_angle), 0, sin(mouth_angle)*0.83).normalized()
	var mouth = out*boundary*1.12
	var source = -out*boundary*0.38 + Vector3(-out.z, 0, out.x)*boundary*0.12
	var trunk = meander(source, mouth, seed_value, boundary*0.12, interval)
	var channels = [{"points":trunk, "width":0.075+0.028*log(1.0+entries.size()), "kind":"river", "index":-1}]
	for i in entries.size():
		var entry = entries[i]
		var kind = ""
		var width = 0.035
		if entry.directory:
			var f = facts.get(entry.path, {})
			width = 0.035 + 0.02*log(1.0 + float(f.get("items", 3)))
			if f.get("readable", true) == false: kind = "river"
			elif numeric(f.get("items")) and int(f.items) == 0: kind = "dry"
			elif f.get("generated", false) or entry.name in GENERATED: kind = "marsh"
			else: kind = "river"
		elif entry.kind in ["audio", "video", "archives"]:
			kind = "creek"  # lakes drain, falls run on, glaciers melt
			width = 0.03
		else:
			continue
		var join = nearest_on(trunk, points[i])
		if kind == "creek" and points[i].distance_to(join) > boundary*0.6:
			continue
		var line = meander(points[i], join, seed_value+i*7919, minf(0.9, points[i].distance_to(join)*0.18), interval)
		var direction = join - points[i]
		channels.append({"points":line, "width":width, "kind":kind, "index":i,
			"angle":atan2(direction.z/0.83, direction.x) if direction.length() > 0.01 else mouth_angle})
	return channels

func draw_channel(parent: Node3D, channel: Dictionary):
	var points: PackedVector3Array = channel.points
	var width: float = channel.width
	var kind: String = channel.kind
	var color = {"river":Color("3f7580"), "creek":Color("4b8189"), "marsh":Color("56654a"), "dry":Color("b2a27f")}[kind]
	var level = GROUND - CARVE*0.45 if kind != "dry" else GROUND - CARVE*0.35
	var braids = [0.0] if kind != "marsh" else [-1.3, 0.0, 1.2]
	var vertices = PackedVector3Array()
	var colors = PackedColorArray()
	var indices = PackedInt32Array()
	for braid in braids:
		var base = vertices.size()
		var w = width if braid == 0.0 else width*0.5
		for i in points.size():
			var tangent = points[mini(i+1, points.size()-1)] - points[maxi(0, i-1)]
			var normal = Vector3(-tangent.z, 0, tangent.x).normalized()
			var centre = points[i] + normal*braid*width*(1.0+0.6*sin(i*0.3+braid))
			# Rivers widen gently downstream.
			var taper = w*(0.7 + 0.3*float(i)/maxf(1, points.size()-1))
			vertices.append(centre + normal*taper + Vector3(0, level, 0))
			vertices.append(centre - normal*taper + Vector3(0, level, 0))
			colors.append(color); colors.append(color.lightened(0.06))
			if i > 0:
				var a = base + (i-1)*2
				indices.append_array(PackedInt32Array([a, a+2, a+1, a+1, a+2, a+3]))
	mesh_node(parent, vertices, colors, indices)
	if kind == "marsh":
		for i in range(0, points.size(), 5):
			cone(parent, points[i] + Vector3(width*2.2*(1 if i%2 else -1), GROUND-0.02, 0), 0.03, 0.22, Color("7d7a4a"), 4)

# ---------------------------------------------------------------- land

func land(entries: Array, points: Array, climate: int, seed_value: int, channels: Array, snow: float) -> Node3D:
	var root = Node3D.new()
	var extent = radius(entries)*1.35
	var boundary = radius(entries)*0.90
	var noise = FastNoiseLite.new()
	noise.seed = seed_value
	noise.frequency = 0.42
	noise.fractal_octaves = 4
	var resolution = 128
	var cell = extent*2.0/resolution
	var count = (resolution+1)*(resolution+1)
	# Stamp landmark lift and river carving onto the grid instead of testing every
	# vertex against every landmark.
	var lift = PackedFloat32Array(); lift.resize(count); lift.fill(-100.0)
	var carve = PackedFloat32Array(); carve.resize(count); carve.fill(0.0)
	for point in points:
		stamp(lift, point, 2.2, cell, extent, resolution, func(d): return 2.2 - d, true)
	for channel in channels:
		var valley = maxf(channel.width*4.0, cell*1.8)
		var depth = CARVE if channel.kind != "dry" else CARVE*0.55
		for p in channel.points:
			stamp(carve, p, valley, cell, extent, resolution, func(d): return depth*(1.0 - smoothstep(0.0, valley, d)), true)
	var vertices = PackedVector3Array()
	var colors = PackedColorArray()
	var indices = PackedInt32Array()
	var phase = float(posmod(seed_value, 628))/100.0
	for z in range(resolution+1):
		for x in range(resolution+1):
			var index = z*(resolution+1)+x
			var px = (float(x)/resolution*2-1)*extent
			var pz = (float(z)/resolution*2-1)*extent
			var angle = atan2(pz, px)
			var coast = boundary*(0.85+0.12*sin(angle*3+phase)+0.09*cos(angle*5-phase)+0.045*sin(angle*9))
			var signed_land = maxf(coast - Vector2(px, pz).length(), lift[index])
			var grain = noise.get_noise_2d(px, pz)
			var height = GROUND if signed_land > 1.0 else lerpf(-0.55, GROUND, smoothstep(-0.8, 1.0, signed_land))
			if signed_land > 1.0: height += grain*0.024
			height -= carve[index]
			var color = LAND[climate].lightened(grain*0.14)
			if carve[index] > 0.0:
				color = color.lerp(Color("4a5641"), carve[index]/CARVE*0.55)
			if snow > 0.0 and signed_land > 0.4:
				color = color.lerp(SNOW, clampf(snow*(0.55+grain*1.4), 0.0, 0.9))
			if signed_land < 0.8:
				color = Color("9a957d").lerp(color, smoothstep(-0.2, 0.8, signed_land))
			if signed_land < -0.1:
				color = Color("3c6265").lerp(Color("899181"), smoothstep(-1.0, -0.1, signed_land))
			vertices.append(Vector3(px, height, pz)); colors.append(color)
			if x < resolution and z < resolution:
				indices.append_array(PackedInt32Array([index, index+resolution+1, index+1, index+1, index+resolution+1, index+resolution+2]))
	mesh_node(root, vertices, colors, indices)
	for channel in channels:
		draw_channel(root, channel)
	return root

func stamp(grid: PackedFloat32Array, point: Vector3, reach: float, cell: float, extent: float, resolution: int, value: Callable, maximum: bool):
	var cx = int(round((point.x/extent+1.0)*0.5*resolution))
	var cz = int(round((point.z/extent+1.0)*0.5*resolution))
	var span = int(ceil(reach/cell))
	for z in range(maxi(0, cz-span), mini(resolution, cz+span)+1):
		for x in range(maxi(0, cx-span), mini(resolution, cx+span)+1):
			var px = (float(x)/resolution*2-1)*extent
			var pz = (float(z)/resolution*2-1)*extent
			var d = Vector2(px-point.x, pz-point.z).length()
			if d > reach: continue
			var index = z*(resolution+1)+x
			var v = value.call(d)
			if v > grid[index]: grid[index] = v

# ---------------------------------------------------------------- landforms

func mountain(parent: Node3D, height: float, age: float, climate: int, seed_value: int):
	var r = rock(age)
	if age >= 1095.0: height *= 0.72  # old ranges are worn down
	var noise = FastNoiseLite.new()
	noise.seed = seed_value
	noise.frequency = r.freq
	noise.fractal_octaves = 4
	var t = clampf((r.sharp-0.65)/(2.3-0.65), 0.0, 1.0)
	var lx = 2.15*r.spread
	var lz = 1.28*r.spread
	var nx = 56
	var nz = 40
	var vertices = PackedVector3Array()
	var colors = PackedColorArray()
	var indices = PackedInt32Array()
	var inside = PackedByteArray()
	var summit = Vector3(0, -1, 0)
	var snowline = 1.7 if climate == 1 else 2.7 if climate in [0, 4] else 3.6
	for z in range(nz+1):
		for x in range(nx+1):
			var px = (float(x)/nx*2-1)*lx
			var pz = (float(z)/nz*2-1)*lz
			var u = px/lx
			var w = pz/lz + sin(u*3.0 + seed_value % 7)*0.14
			var p = clampf(1.0 - sqrt(u*u + w*w), 0.0, 1.0)
			# Basalt: concave flanks and a spike. Granite: a convex, rounded dome.
			var profile = lerpf(1.0 - pow(1.0-p, 2.2), pow(p, 1.25), t)
			var ridged = 1.0 - absf(noise.get_noise_2d(px, pz))
			var k = lerpf(0.15, 0.55, t)
			var elevation = height*profile*(1.0-k + k*pow(ridged, 1.5))
			var band = 0
			if r.steps > 0 and elevation > 0.0:
				var q = elevation/height*r.steps
				band = int(q)
				elevation = (floor(q) + smoothstep(0.72, 1.0, q-floor(q)))/r.steps*height
			var f = elevation/maxf(height, 0.01)
			var detail = noise.get_noise_2d(px*3.1, pz*3.1)
			var color = LAND[climate].lerp(r.low, smoothstep(0.0, 0.18, f)).lerp(r.high, smoothstep(0.35, 1.0, f)*0.75).lightened(detail*0.12)
			if r.steps > 0 and band % 2 == 1: color = color.darkened(0.10)
			if elevation > snowline: color = color.lerp(SNOW, smoothstep(snowline, snowline+0.8, elevation))
			var v = Vector3(px, 0.13+elevation, pz)
			if v.y > summit.y: summit = v
			vertices.append(v); colors.append(color); inside.append(1 if p > 0.0 else 0)
			if x < nx and z < nz:
				var a = z*(nx+1)+x
				indices.append_array(PackedInt32Array([a, a+nx+1, a+1, a+1, a+nx+1, a+nx+2]))
	# Drop quads entirely outside the footprint so no flat skirt covers the land.
	var kept = PackedInt32Array()
	for i in range(0, indices.size(), 3):
		var a = indices[i]; var b = indices[i+1]; var c = indices[i+2]
		if inside[a] + inside[b] + inside[c] > 0:
			kept.append_array(PackedInt32Array([a, b, c]))
	var holder = Node3D.new()
	holder.rotation.y = float(posmod(seed_value, 628))/100
	parent.add_child(holder)
	mesh_node(holder, vertices, colors, kept)
	if r.ember:
		globe(holder, summit + Vector3(0, 0.02, 0), 0.08, Color("ff7a2a"), 1.0, 0.6, 2.4)
		globe(holder, summit + Vector3(0.05, 0.45, 0), 0.16, Color("8a8580"), 0.35)
		globe(holder, summit + Vector3(0.14, 0.75, 0.04), 0.22, Color("a09b95"), 0.22)

func pool(parent: Node3D, spread: float, seed_value: int, murky: bool = false):
	var random = RandomNumberGenerator.new()
	random.seed = seed_value
	var deep = Color("25505c") if not murky else Color("3d4a38")
	var shallow = Color("5f8c88") if not murky else Color("6b7453")
	var shore = Color("a39c80") if not murky else Color("5f6444")
	var wobble = [random.randf_range(0.08, 0.18), random.randf_range(0.05, 0.12), random.randf()*TAU]
	var vertices = PackedVector3Array([Vector3(0, 0.133, 0)])
	var colors = PackedColorArray([deep])
	var indices = PackedInt32Array()
	var rings = [[0.55, 0.134, deep.lerp(shallow, 0.4)], [1.0, 0.135, shallow], [1.14, 0.128, shore]]
	for ring in rings.size():
		for i in 32:
			var angle = float(i)/32*TAU
			var r = spread*rings[ring][0]*(1+wobble[0]*sin(angle*3+wobble[2])+wobble[1]*cos(angle*5))
			vertices.append(Vector3(cos(angle)*r, rings[ring][1], sin(angle)*r*0.74))
			colors.append(rings[ring][2])
	for i in 32:
		var n = (i+1) % 32
		indices.append_array(PackedInt32Array([0, 1+n, 1+i]))
		for ring in range(1, rings.size()):
			var inner = 1+(ring-1)*32
			var outer = 1+ring*32
			indices.append_array(PackedInt32Array([inner+i, inner+n, outer+i, outer+i, inner+n, outer+n]))
	mesh_node(parent, vertices, colors, indices)

func lake(parent: Node3D, facts: Dictionary, seed_value: int):
	var duration = float(facts.get("duration", 0))
	var spread = clampf(0.65 + log(1+duration/60)*0.2, 0.65, 1.55)
	pool(parent, spread, seed_value)
	for i in mini(3, int(facts.get("channels", 0))):
		var torus = TorusMesh.new()
		torus.inner_radius = spread*(0.3+i*0.18)
		torus.outer_radius = torus.inner_radius+0.014
		torus.rings = 20
		torus.ring_segments = 4
		var ripple = shape(parent, torus, Vector3(0, 0.14, 0), Color("8ca9a2"))
		ripple.scale = Vector3(1, 1, 0.74)

func waterfall(parent: Node3D, facts: Dictionary, seed_value: int):
	var duration = float(facts.get("duration", 0))
	var height = clampf(0.5+log(1+duration/60)*0.24, 0.5, 1.8)
	var fall = clampf(0.1 + float(facts.get("width", 0))/3840.0*0.32, 0.1, 0.42)
	var noise = FastNoiseLite.new()
	noise.seed = seed_value
	noise.frequency = 3.0
	# The cliff: a plateau behind the fall's lip.
	var vertices = PackedVector3Array()
	var colors = PackedColorArray()
	var indices = PackedInt32Array()
	var nx = 24
	var nz = 16
	for z in range(nz+1):
		for x in range(nx+1):
			var px = (float(x)/nx*2-1)*0.95
			var pz = lerpf(-1.05, 0.12, float(z)/nz)
			var edge = 1.0 - pow(absf(px)/0.95, 3.0)
			var rise = smoothstep(0.1, -0.12, pz)*edge*(1.0 - smoothstep(-0.75, -1.05, pz)*0.6)
			var y = 0.12 + height*rise + noise.get_noise_2d(px, pz)*0.05*height*rise
			var color = Color("5d6258").lerp(Color("7a7f6c"), rise).lightened(noise.get_noise_2d(px*2, pz*2)*0.1)
			vertices.append(Vector3(px, y, pz)); colors.append(color)
			if x < nx and z < nz:
				var a = z*(nx+1)+x
				indices.append_array(PackedInt32Array([a, a+nx+1, a+1, a+1, a+nx+1, a+nx+2]))
	mesh_node(parent, vertices, colors, indices)
	box(parent, Vector3(0, 0.12+height*0.98, -0.55), Vector3(fall, 0.02, 0.95), Color("467f89"))
	# The falling sheet, dark at the lip and white where it breaks.
	var sheet = PackedVector3Array()
	var tint = PackedColorArray()
	var tris = PackedInt32Array()
	for i in 9:
		var t = float(i)/8
		var y = 0.12 + height*(1.0-t) + 0.02
		var z = -0.06 + 0.16*t*t
		sheet.append(Vector3(-fall/2, y, z)); sheet.append(Vector3(fall/2, y, z))
		var c = Color("5c9aa3").lerp(Color("eef6f4"), smoothstep(0.2, 1.0, t))
		tint.append(c); tint.append(c)
		if i > 0:
			var a = (i-1)*2
			tris.append_array(PackedInt32Array([a, a+2, a+1, a+1, a+2, a+3]))
	mesh_node(parent, sheet, tint, tris)
	var basin = Node3D.new()
	basin.position = Vector3(0, 0, 0.5)
	parent.add_child(basin)
	pool(basin, 0.42, seed_value)
	for i in 3:
		globe(parent, Vector3((i-1)*fall*0.6, 0.22+i*0.04, 0.18), 0.12+i*0.02, Color("eef3f2"), 0.3)

func glacier(parent: Node3D, facts: Dictionary, seed_value: int):
	var entries = int(facts.get("entries", 0)) if numeric(facts.get("entries")) else 0
	var length = clampf(0.9 + log(1.0+entries)*0.2, 0.9, 2.4)
	var density = clampf(1.0 - float(facts.ratio), 0.0, 1.0) if numeric(facts.get("ratio")) else 0.3
	var head = Color("f1f4f5")
	var toe = Color("a9c6cf").lerp(Color("3f84a8"), density)
	var phase = float(posmod(seed_value, 628))/100.0
	var vertices = PackedVector3Array()
	var colors = PackedColorArray()
	var indices = PackedInt32Array()
	var nx = 44
	var nz = 12
	for i in range(nx+1):
		var u = float(i)/nx
		var half = lerpf(0.62, 0.24, u)*(1.0+0.08*sin(u*9.0+phase))
		var cx = lerpf(-length*0.9, length*0.8, u)
		var bend = sin(u*2.4+phase)*0.25
		for j in range(nz+1):
			var v = float(j)/nz*2-1
			var surface = lerpf(0.66, 0.16, pow(u, 0.8)) + 0.07*(1-v*v)
			var color = head.lerp(toe, smoothstep(0.1, 0.95, u))
			if sin(u*38.0 + v*2.0 + phase) > 0.86 and absf(v) < 0.8 and u > 0.18 and u < 0.85:
				color = color.darkened(0.28)  # crevasse field
			if absf(v) > 0.8:
				surface += 0.04
				color = Color("6b6862").lerp(color, 0.25)  # lateral moraine
			vertices.append(Vector3(cx, surface, v*half+bend)); colors.append(color)
			if i < nx and j < nz:
				var a = i*(nz+1)+j
				indices.append_array(PackedInt32Array([a, a+1, a+nz+1, a+1, a+nz+2, a+nz+1]))
	mesh_node(parent, vertices, colors, indices)
	# Cirque walls where the ice accumulates.
	for side in [-1, 0, 1]:
		var arc = side*0.95
		var base = Vector3(-length*0.9-cos(arc)*0.5, 0.1, sin(arc)*0.62+sin(phase)*0.25)
		var peak = 0.6+absf(side)*0.12
		cone(parent, base, 0.46, peak, Color("5f615c"), 6)
		cone(parent, base+Vector3(0, peak*0.62, 0), 0.46*0.38, peak*0.38, SNOW, 6)

func obsidian(parent: Node3D, bytes: float, random: RandomNumberGenerator):
	var count = clampi(2+int(log(1.0+bytes/65536.0)/log(2.5)), 2, 7)
	for i in count:
		var angle = random.randf()*TAU
		var spread = random.randf_range(0.0, 0.45)
		var spire = cone(parent, Vector3(cos(angle)*spread, 0.1, sin(angle)*spread), random.randf_range(0.07, 0.13),
			random.randf_range(0.3, 0.85), Color("17151b"), 5, 0.01, 0.9)
		spire.rotation = Vector3(random.randf_range(-0.2, 0.2), random.randf()*TAU, random.randf_range(-0.2, 0.2))

func caldera(parent: Node3D, bytes: float, climate: int):
	var r = clampf(0.45 + log(1.0+bytes/1.0e9)*0.28, 0.45, 1.5)
	var vertices = PackedVector3Array()
	var colors = PackedColorArray()
	var indices = PackedInt32Array()
	var n = 36
	var span = r*1.7
	var keep = PackedByteArray()
	for z in range(n+1):
		for x in range(n+1):
			var px = (float(x)/n*2-1)*span
			var pz = (float(z)/n*2-1)*span
			var d = Vector2(px, pz).length()
			var rim = exp(-pow((d-r)/(r*0.3), 2))*0.42*r
			var y = 0.12 + rim - (0.05 if d < r*0.8 else 0.0)
			var color = LAND[climate].lerp(Color("5a4a42"), smoothstep(0.02, 0.2, rim)).lerp(Color("8a7263"), smoothstep(0.25, 0.42, rim/r))
			vertices.append(Vector3(px, y, pz)); colors.append(color)
			keep.append(1 if d < r*1.55 else 0)
			if x < n and z < n:
				var a = z*(n+1)+x
				if keep[a] == 1:
					indices.append_array(PackedInt32Array([a, a+n+1, a+1, a+1, a+n+1, a+n+2]))
	mesh_node(parent, vertices, colors, indices)
	var water = CylinderMesh.new()
	water.top_radius = r*0.72
	water.bottom_radius = r*0.72
	water.height = 0.02
	water.radial_segments = 28
	shape(parent, water, Vector3(0, 0.1, 0), Color("3c7a82"))

func well(parent: Node3D):
	var ring = TorusMesh.new()
	ring.inner_radius = 0.15
	ring.outer_radius = 0.25
	ring.rings = 16
	ring.ring_segments = 6
	shape(parent, ring, Vector3(0, 0.17, 0), Color("8a877c"))
	var water = CylinderMesh.new()
	water.top_radius = 0.16
	water.bottom_radius = 0.16
	water.height = 0.04
	shape(parent, water, Vector3(0, 0.16, 0), Color("1d3b47"))
	for side in [-1, 1]:
		box(parent, Vector3(side*0.24, 0.12, 0), Vector3(0.035, 0.42, 0.035), Color("6a5541"))
	box(parent, Vector3(0, 0.52, 0), Vector3(0.62, 0.035, 0.3), Color("7a4f3a"))

func settlement(parent: Node3D, bytes: float, random: RandomNumberGenerator):
	var houses = clampi(1+int(log(1.0+bytes/2048.0)/log(3.0)), 1, 7)
	for i in houses:
		var angle = float(i)/houses*TAU + random.randf()*0.4
		var d = 0.0 if houses == 1 else random.randf_range(0.2, 0.5)
		var pos = Vector3(cos(angle)*d, 0.08, sin(angle)*d)
		var w = random.randf_range(0.18, 0.28)
		var house = box(parent, pos, Vector3(w, 0.2, 0.24), Color("d1bda0"))
		house.rotation.y = angle
		var roof = cone(parent, pos+Vector3(0, 0.2, 0), w*0.9, 0.2, Color("705b4b"), 4)
		roof.rotation.y = angle + PI/4

func meadow(parent: Node3D, entry: Dictionary, random: RandomNumberGenerator):
	var bytes = float(entry.get("bytes", 0))
	var count = clampi(4+int(log(1.0+bytes/1024.0)*2.0), 4, 18)
	var spread = 0.35 + count*0.035
	var palette = [Color("d8c46a"), Color("c98f6c"), Color("b7a8d8"), Color("e6e1cf")]
	var bloom = palette[posmod(hash(entry.name.get_extension()), palette.size())]
	for i in count:
		var pos = Vector3(random.randf_range(-spread, spread), 0.09, random.randf_range(-spread, spread)*0.8)
		cone(parent, pos, 0.07, 0.14, Color("7c8a52"), 5)
		if i % 3 == 0:
			globe(parent, pos+Vector3(0, 0.17, 0), 0.035, bloom)

func cairn(parent: Node3D):
	for i in 3:
		globe(parent, Vector3(0, 0.14+i*0.1, 0), 0.14-i*0.03, Color("8f8c80").darkened(i*0.06), 1.0, 0.55)

func tree(parent: Node3D, pos: Vector3, growth: float, climate: int, accent: Color):
	var bark = Color("66503a")
	cone(parent, pos, 0.043, 0.55 * growth, bark, 5, 0.03)
	if climate == 3:
		box(parent, pos + Vector3(0, 0.22, 0), Vector3(0.09, growth * 0.48, 0.09), Color("71844e"))
		box(parent, pos + Vector3(-0.17, 0.38 * growth, 0), Vector3(0.34, 0.075, 0.075), Color("71844e"))
		box(parent, pos + Vector3(-0.17, 0.38 * growth, 0), Vector3(0.07, 0.18, 0.07), Color("71844e"))
	elif climate == 2:
		for angle in [0.0, PI/2, PI, PI*1.5]:
			var leaf = box(parent, pos + Vector3(sin(angle)*0.13, 0.53*growth, cos(angle)*0.13), Vector3(0.13, 0.045, 0.5), accent)
			leaf.rotation = Vector3(0.25, angle, 0)
	elif climate == 1:
		cone(parent, pos + Vector3(0, 0.18, 0), 0.28, 0.65*growth, accent, 6)
		cone(parent, pos + Vector3(0, 0.60*growth, 0), 0.13, 0.28*growth, Color("e0e3d5"), 6)
	else:
		globe(parent, pos + Vector3(0, 0.55*growth, 0), 0.27*growth, accent)
		if climate == 4:
			for side in [-1, 1]:
				var root = box(parent, pos + Vector3(side*0.065, 0.05, 0), Vector3(0.035, 0.25, 0.035), bark)
				root.rotation.z = side * 0.5

func woodland(parent: Node3D, facts: Dictionary, climate: int, random: RandomNumberGenerator):
	var w = float(facts.get("width", 0))
	var h = float(facts.get("height", 0))
	var growth = clampf(0.8 + log(1+w*h/1000000)*0.14, 0.8, 1.55)
	var count = 24 if w > h*1.3 and h > 0 else 16
	var leaf = Color("345f3c") if climate != 2 else Color("28754e")
	var captured = str(facts.get("captured", ""))
	if captured.length() >= 7:
		var month = captured.substr(5, 2).to_int()
		if month in [9, 10, 11]: leaf = Color("b88b42")
		elif month in [12, 1, 2]: leaf = leaf.lightened(0.24)
	if h > w and w > 0: growth *= 1.2
	for i in count:
		var angle = random.randf()*TAU
		var spread = sqrt(random.randf())*1.05
		var point = Vector3(cos(angle)*spread*(1.4 if w > h else 0.9), 0.12, sin(angle)*spread)
		tree(parent, point, growth*random.randf_range(0.48, 0.90), climate, leaf.lightened(random.randf_range(-0.06, 0.09)))

func fields(parent: Node3D, facts: Dictionary):
	var rows = int(facts.get("rows", 1))
	var cols = clampi(int(facts.get("columns", 3)), 2, 7)
	var length = clampf(0.65 + log(float(rows)+1)*0.065, 0.65, 1.3)
	box(parent, Vector3(0, 0.075, 0), Vector3(1.15, 0.03, length), Color("b19a57"))
	for i in cols:
		box(parent, Vector3(-0.48+i*(0.96/maxi(1, cols-1)), 0.11, 0), Vector3(0.055, 0.055, length*0.9), Color("5f7c3e"))

func cloud(parent: Node3D, pos: Vector3, size: float, color: Color, alpha: float, random: RandomNumberGenerator) -> Node3D:
	var holder = Node3D.new()
	holder.position = pos
	parent.add_child(holder)
	for i in 5:
		var offset = Vector3(random.randf_range(-1, 1)*size*0.9, random.randf_range(0, 0.35)*size, random.randf_range(-1, 1)*size*0.5)
		globe(holder, offset, size*random.randf_range(0.45, 0.75), color, alpha, 0.6)
	return holder

func rain(parent: Node3D, centre: Vector3, spread: float, count: int, top: float, random: RandomNumberGenerator, color: Color = Color("b8c8cf")) -> Array:
	var drops = []
	var mesh = BoxMesh.new()
	mesh.size = Vector3(0.012, 0.3, 0.012)
	for i in count:
		var drop = shape(parent, mesh, centre + Vector3(random.randf_range(-spread, spread), random.randf_range(0.15, top), random.randf_range(-spread, spread)*0.6), color, 0.5)
		drops.append(drop)
	return drops

func directory_marker(parent: Node3D, entry: Dictionary, facts: Dictionary, random: RandomNumberGenerator):
	# Unexplored directories are surveyed from afar: cairn and pennant under fog. The fog
	# lifts when the child's real landscape replaces this marker.
	if facts.get("readable", true) == false:
		for i in 5:
			globe(parent, Vector3(random.randf_range(-0.5, 0.5), 0.3+i*0.08, random.randf_range(-0.4, 0.4)), 0.42, Color("d7dcdc"), 0.55)
		return
	var items = int(facts.get("items", 0)) if numeric(facts.get("items")) else -1
	if items == 0:
		for i in 6:
			globe(parent, Vector3(-0.5+i*0.2, 0.11, sin(i*1.3)*0.12), 0.07, Color("c9bd98"), 1.0, 0.4)  # dry bed pebbles
		return
	if facts.get("generated", false) or entry.name in GENERATED:
		for i in 4:
			var patch = Node3D.new()
			patch.position = Vector3(random.randf_range(-0.5, 0.5), 0, random.randf_range(-0.4, 0.4))
			parent.add_child(patch)
			pool(patch, random.randf_range(0.16, 0.28), random.randi(), true)
		for i in 10:
			cone(parent, Vector3(random.randf_range(-0.7, 0.7), 0.1, random.randf_range(-0.5, 0.5)), 0.025, random.randf_range(0.16, 0.3), Color("7d7a4a"), 4)
		return
	cone(parent, Vector3(0, 0.12, 0), 0.045, 0.65, Color("8d8769"), 8, 0.035)
	box(parent, Vector3(0.1, 0.62, 0), Vector3(0.18, 0.1, 0.012), Color("c9a95b"))
	var fog = 2 + (mini(4, int(log(1.0+items)/log(3.0))) if items > 0 else 1)
	for i in fog:
		var angle = random.randf()*TAU
		globe(parent, Vector3(cos(angle)*0.35, 0.3+random.randf()*0.25, sin(angle)*0.25), random.randf_range(0.3, 0.5), Color("dde2e0"), 0.26)
	if int(facts.get("changed_day", 0)) > 0:
		cloud(parent, Vector3(0, 1.45, 0), 0.4, Color("8d969a"), 0.8, random)
		var drops = rain(parent, Vector3(0, 0, 0), 0.35, 8, 1.3, random)
		parent.set_meta("drops", drops)
		parent.set_meta("top", 1.3)

func signpost(parent: Node3D):
	box(parent, Vector3(0.55, 0.1, 0.45), Vector3(0.025, 0.38, 0.025), Color("6a5541"))
	var board = box(parent, Vector3(0.62, 0.4, 0.45), Vector3(0.18, 0.06, 0.015), Color("d9c68f"))
	board.rotation.y = 0.3

func feature(entry: Dictionary, facts: Dictionary, climate: int) -> Node3D:
	var root = Node3D.new()
	var random = RandomNumberGenerator.new()
	random.seed = hash(entry.path)
	var kind = entry.kind
	var bytes = float(entry.get("bytes", 0))
	var pages = facts.get("pages")
	if kind == "pdf" or (kind == "documents" and numeric(pages) and pages > 0):
		var height = clampf(log(float(pages)+1)/log(2.0)*0.29, 0.45, 3.5) if numeric(pages) and pages > 0 else 0.55
		mountain(root, height*1.6, age_days(entry), climate, hash(entry.path))
	elif kind == "images": woodland(root, facts, climate, random)
	elif kind == "audio": lake(root, facts, hash(entry.path))
	elif kind == "video": waterfall(root, facts, hash(entry.path))
	elif kind == "tables": fields(root, facts)
	elif kind == "code": settlement(root, bytes, random)
	elif kind == "archives": glacier(root, facts, hash(entry.path))
	elif kind == "binaries": obsidian(root, bytes, random)
	elif kind == "disks": caldera(root, bytes, climate)
	elif kind == "databases": well(root)
	elif kind == "folders": directory_marker(root, entry, facts, random)
	elif kind == "documents": meadow(root, entry, random)
	else: cairn(root)
	if entry.get("symlink", false): signpost(root)
	return root

# ---------------------------------------------------------------- reading

func reading(entry: Dictionary, facts: Dictionary) -> String:
	# Say in words why the landform looks as it does.
	var age = age_days(entry)
	var when = "Modified " + ago(age) + "."
	var kind = entry.kind
	var pages = facts.get("pages")
	if kind == "folders":
		if facts.get("readable", true) == false: return "Fog: this directory cannot be read."
		var items = facts.get("items")
		if not numeric(items): return "Tributary valley, not yet surveyed."
		if int(items) == 0: return "Dry riverbed: an empty directory."
		var text = "%s: %d%s items drain down this valley." % ["Marsh (generated / cache)" if facts.get("generated", false) else "Tributary", int(items), "+" if facts.get("more", false) else ""]
		if int(facts.get("changed_day", 0)) > 0: text += " Rain: %d changed today." % int(facts.changed_day)
		elif int(facts.get("changed_week", 0)) > 0: text += " %d changed this week." % int(facts.changed_week)
		return text + " Enter to lift the fog."
	if kind == "pdf" or (kind == "documents" and numeric(pages) and pages > 0):
		var r = rock(age)
		var bulk = ("%d pages set its height." % int(pages)) if numeric(pages) and pages > 0 else "Page count unknown, so the height is neutral."
		return "%s ridge. %s %s" % [r.name, bulk, when]
	match kind:
		"images":
			if facts.has("width"): return "Woodland: %d × %d pixels set tree growth%s. %s" % [facts.width, facts.height, ", capture month tints the foliage" if facts.has("captured") else "", when]
			return "Woodland, dimensions unknown. " + when
		"audio":
			if facts.has("duration"): return "Lake: %s of audio set its area; %d channel ripple%s. %s" % [duration_text(facts.duration), int(facts.get("channels", 0)), "" if int(facts.get("channels", 0)) == 1 else "s", when]
			return "Lake, duration unknown. " + when
		"video":
			if facts.has("duration"): return "Waterfall: %s sets its height%s. %s" % [duration_text(facts.duration), (", %d px wide sets the fall's width" % int(facts.width)) if facts.has("width") else "", when]
			return "Waterfall, duration unknown. " + when
		"tables":
			if facts.has("rows"): return "Fields: %d rows set the length, %d columns the furrows. %s" % [facts.rows, facts.columns, when]
			return "Fields, size unread. " + when
		"archives":
			if numeric(facts.get("entries")):
				var text = "Glacier: %d entries set its length" % int(facts.entries)
				if numeric(facts.get("ratio")): text += "; packed to %d%% of original size, so %s ice" % [int(float(facts.ratio)*100), "dense blue" if facts.ratio < 0.5 else "pale"]
				return text + ". Meltwater is extraction. " + when
			return "Glacier; this archive's index was not read. " + when
		"code": return "Settlement: file size sets the number of buildings. " + when
		"binaries": return "Obsidian: compiled or executable, rock transformed by heat and pressure. " + when
		"disks": return "Caldera: a disk image, a whole world collapsed into one crater. Size sets the diameter. " + when
		"databases": return "Well: a database, a deep store you draw from. " + when
		"documents": return "Meadow: a document with no known page count. Size sets its extent. " + when
	return "Cairn: an uncharted file type. " + when

static func duration_text(seconds) -> String:
	var s = int(seconds)
	return "%d:%02d" % [s/60, s%60] if s < 3600 else "%d:%02d:%02d" % [s/3600, (s/60)%60, s%60]

# ---------------------------------------------------------------- weather

func weather_stats(entries: Array, facts: Dictionary) -> Dictionary:
	var now = Time.get_unix_time_from_system()
	var s = {"hour":0, "day":0, "week":0, "newest":0.0}
	for entry in entries:
		var f = facts.get(entry.path, {})
		if entry.directory and f.has("items"):
			s.hour += int(f.get("changed_hour", 0))
			s.day += int(f.get("changed_day", 0))
			s.week += int(f.get("changed_week", 0))
			s.newest = maxf(s.newest, float(f.get("newest", 0)))
			continue
		var modified = float(entry.get("modified", 0))
		if modified <= 0.0: continue
		s.newest = maxf(s.newest, modified)
		var age = now - modified
		if age < 3600: s.hour += 1
		if age < DAY: s.day += 1
		if age < 7*DAY: s.week += 1
	var quiet = (now - s.newest)/DAY if s.newest > 0 else -1.0
	s["quiet"] = quiet
	if s.hour >= 3 or s.day >= 12: s["condition"] = "storm"
	elif s.day > 0: s["condition"] = "showers"
	elif s.week > 0: s["condition"] = "cumulus"
	elif quiet > 730: s["condition"] = "snow"
	elif quiet >= 0 and quiet < 90: s["condition"] = "clear"
	elif quiet < 0: s["condition"] = "unknown"
	else: s["condition"] = "settled"
	s["snow"] = clampf((quiet-730.0)/1460.0, 0.25, 0.8) if s.condition == "snow" else 0.0
	return s

static func forecast(s: Dictionary) -> String:
	match s.get("condition", "unknown"):
		"storm": return "Thunderstorm: %d changes today, %d in the last hour" % [s.day, s.hour]
		"showers": return "Showers: %d change%s today" % [s.day, "" if s.day == 1 else "s"]
		"cumulus": return "Fair-weather cloud: %d changed this week" % s.week
		"clear": return "Clear: last change " + ago(s.quiet)
		"settled": return "Settled: quiet since " + ago(s.quiet)
		"snow": return "Snowbound: untouched for " + ago(s.quiet).trim_suffix(" ago")
	return "No weather data"

func weather_layer(entries: Array, s: Dictionary, seed_value: int) -> Node3D:
	var root = Node3D.new()
	root.name = "Weather"
	var random = RandomNumberGenerator.new()
	random.seed = seed_value
	var reach = radius(entries)*0.65
	var drops = []
	var condition = s.get("condition", "")
	# Weather must never hide the data: small, translucent, mostly over the margins.
	var clouds = {"storm":4, "showers":clampi(2+s.day/3, 2, 4), "cumulus":clampi(1+s.week/4, 2, 3)}.get(condition, 0)
	var sky = Node3D.new()
	sky.name = "Sky"
	root.add_child(sky)
	for i in clouds:
		var bearing = random.randf()*TAU
		var out = random.randf_range(0.55, 1.0)*reach
		var pos = Vector3(cos(bearing)*out, random.randf_range(3.0, 3.8), sin(bearing)*out*0.8)
		var color = Color("5a6166") if condition == "storm" else Color("9aa3a6") if condition == "showers" else Color("f3f4f0")
		var size = random.randf_range(0.55, 0.9)
		cloud(sky, pos, size, color, 0.5 if condition != "cumulus" else 0.62, random)
		globe(root, Vector3(pos.x+1.2, GROUND+0.03, pos.z+0.9), size*1.1, Color("1c2b2d"), 0.13, 0.05)  # cloud shadow
		if condition in ["storm", "showers"]:
			drops.append_array(rain(sky, Vector3(pos.x, 0, pos.z), size, 24 if condition == "storm" else clampi(6+s.day*2, 8, 18), pos.y-0.2, random))
	if condition == "snow":
		drops.append_array(rain(sky, Vector3.ZERO, reach, 60, 3.5, random, Color("f4f6f6")))
	if condition == "storm":
		var bolt = Node3D.new()
		bolt.name = "Bolt"
		bolt.visible = false
		var p = Vector3(random.randf_range(-reach, reach)*0.5, 3.2, random.randf_range(-reach, reach)*0.4)
		for i in 4:
			var q = p + Vector3(random.randf_range(-0.35, 0.35), -0.75, random.randf_range(-0.2, 0.2))
			var seg = box(bolt, (p+q)/2 - Vector3(0, 0.4, 0), Vector3(0.035, 0.8, 0.035), Color("fff4c2"), 1.0, 3.0)
			seg.rotation.z = atan2(q.x-p.x, 0.75)
			p = q
		root.add_child(bolt)
	root.set_meta("drops", drops)
	root.set_meta("top", 3.6)
	root.set_meta("snow", condition == "snow")
	return root
