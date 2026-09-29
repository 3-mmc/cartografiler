extends RefCounted

const CLIMATES = ["Temperate", "Alpine", "Tropical", "Desert", "Wetland"]
const LAND = [Color("686e52"), Color("93968b"), Color("4e6953"), Color("a79777"), Color("60746b")]
const WATER = Color("328c9c")
var materials = {}

func material(color: Color) -> StandardMaterial3D:
	var key = color.to_html()
	if materials.has(key):
		return materials[key]
	var mat = StandardMaterial3D.new()
	mat.albedo_color = color
	mat.roughness = 0.92
	materials[key] = mat
	return mat

func shape(parent: Node3D, mesh: Mesh, pos: Vector3, color: Color) -> MeshInstance3D:
	var instance = MeshInstance3D.new()
	instance.mesh = mesh
	instance.material_override = material(color)
	instance.position = pos
	parent.add_child(instance)
	return instance

func cone(parent: Node3D, pos: Vector3, radius: float, height: float, color: Color, sides: int = 7, top: float = 0.0):
	var mesh = CylinderMesh.new()
	mesh.top_radius = top
	mesh.bottom_radius = radius
	mesh.height = height
	mesh.radial_segments = sides
	mesh.rings = 1
	return shape(parent, mesh, pos + Vector3(0, height / 2, 0), color)

func box(parent: Node3D, pos: Vector3, dimensions: Vector3, color: Color):
	var mesh = BoxMesh.new()
	mesh.size = dimensions
	return shape(parent, mesh, pos + Vector3(0, dimensions.y / 2, 0), color)

func globe(parent: Node3D, pos: Vector3, radius: float, color: Color):
	var mesh = SphereMesh.new()
	mesh.radius = radius
	mesh.height = radius * 1.7
	mesh.radial_segments = 11
	mesh.rings = 5
	return shape(parent, mesh, pos, color)

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
			var point = Vector3(cos(angle)*radius_value,0,sin(angle)*radius_value*0.83)
			var nearest = 1000.0
			for other in points:
				nearest = minf(nearest,point.distance_squared_to(other))
			var score = nearest - point.length_squared()*0.025
			if score > best_score:
				best = point
				best_score = score
		points.append(best)
	return points

func radius(entries: Array) -> float:
	return maxf(7.0,sqrt(entries.size())*2.15+3.2)

func vertex_mesh(vertices: PackedVector3Array, colors: PackedColorArray, indices: PackedInt32Array) -> ArrayMesh:
	var arrays = []
	arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = vertices
	arrays[Mesh.ARRAY_COLOR] = colors
	arrays[Mesh.ARRAY_INDEX] = indices
	var mesh = ArrayMesh.new()
	mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES,arrays)
	var st = SurfaceTool.new()
	st.create_from(mesh,0)
	st.generate_normals()
	return st.commit()

func vertex_material() -> StandardMaterial3D:
	var mat = StandardMaterial3D.new()
	mat.vertex_color_use_as_albedo = true
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	mat.roughness = 1.0
	return mat

func river(parent: Node3D, from: Vector3, to: Vector3, seed_value: int, width: float = 0.10):
	var random = RandomNumberGenerator.new()
	random.seed = seed_value
	var curve = Curve3D.new()
	var delta = to-from
	var side = Vector3(-delta.z,0,delta.x).normalized()
	for i in 6:
		var t = float(i)/5
		var point = from.lerp(to,t)+side*sin(t*PI)*random.randf_range(-0.8,0.8)
		curve.add_point(point,-delta/18,delta/18)
	curve.bake_interval = 0.10
	var points = curve.get_baked_points()
	var vertices = PackedVector3Array()
	var colors = PackedColorArray()
	var indices = PackedInt32Array()
	for i in points.size():
		var tangent = points[mini(i+1,points.size()-1)]-points[maxi(0,i-1)]
		var normal = Vector3(-tangent.z,0,tangent.x).normalized()*width
		vertices.append(points[i]+normal+Vector3(0,0.125,0))
		vertices.append(points[i]-normal+Vector3(0,0.125,0))
		colors.append(Color("467b83")); colors.append(Color("467b83"))
		if i>0:
			var a = (i-1)*2
			indices.append_array(PackedInt32Array([a,a+2,a+1,a+1,a+2,a+3]))
	var instance = MeshInstance3D.new()
	instance.mesh = vertex_mesh(vertices,colors,indices)
	instance.material_override = vertex_material()
	parent.add_child(instance)

func land(entries: Array, climate: int, seed_value: int) -> Node3D:
	var root = Node3D.new()
	var extent = radius(entries)*1.35
	var boundary = radius(entries)*0.90
	var points = positions(entries)
	var noise = FastNoiseLite.new()
	noise.seed = seed_value
	noise.frequency = 0.42
	noise.fractal_octaves = 4
	var vertices = PackedVector3Array()
	var colors = PackedColorArray()
	var indices = PackedInt32Array()
	var resolution = 108
	var phase = float(posmod(seed_value,628))/100.0
	for z in range(resolution+1):
		for x in range(resolution+1):
			var px = (float(x)/resolution*2-1)*extent
			var pz = (float(z)/resolution*2-1)*extent
			var angle = atan2(pz,px)
			var coast = boundary*(0.85+0.12*sin(angle*3+phase)+0.09*cos(angle*5-phase)+0.045*sin(angle*9))
			var signed_land = coast-Vector2(px,pz).length()
			for point in points:
				signed_land = maxf(signed_land,2.2-Vector2(px-point.x,pz-point.z).length())
			var grain = noise.get_noise_2d(px,pz)
			var height = 0.11 if signed_land>1.0 else lerpf(-0.55,0.11,smoothstep(-0.8,1.0,signed_land))
			# Shallow drainage relief, never individual platform edges.
			if signed_land>1.0: height += grain*0.024
			var color = LAND[climate].lightened(grain*0.14)
			if signed_land < 0.8:
				color = Color("9a957d").lerp(LAND[climate],smoothstep(-0.2,0.8,signed_land))
			if signed_land < -0.1: color = Color("3c6265").lerp(Color("899181"),smoothstep(-1.0,-0.1,signed_land))
			vertices.append(Vector3(px,height,pz)); colors.append(color)
			if x<resolution and z<resolution:
				var a = z*(resolution+1)+x
				indices.append_array(PackedInt32Array([a,a+resolution+1,a+1,a+1,a+resolution+1,a+resolution+2]))
	var ground = MeshInstance3D.new()
	ground.mesh = vertex_mesh(vertices,colors,indices)
	ground.material_override = vertex_material()
	root.add_child(ground)
	var branches = 0
	for i in entries.size():
		if entries[i].directory:
			river(root,Vector3(-boundary*0.65,0,boundary*0.24),points[i],seed_value+i)
			branches+=1
	if branches==0:
		river(root,Vector3(-boundary*0.75,0,0.3),Vector3(boundary*0.65,0,1.3),seed_value,0.075)
	return root

func ridge(parent: Node3D, height: float, climate: int, seed_value: int):
	var noise = FastNoiseLite.new()
	noise.seed = seed_value
	noise.frequency = 2.3
	noise.fractal_octaves = 4
	var vertices = PackedVector3Array()
	var colors = PackedColorArray()
	var indices = PackedInt32Array()
	var nx = 54
	var nz = 38
	for z in range(nz+1):
		for x in range(nx+1):
			var px = (float(x)/nx*2-1)*2.15
			var pz = (float(z)/nz*2-1)*1.28
			var warped = pz+sin(px*2.0)*0.19
			var envelope = pow(maxf(0.0,1-pow(px/2.15,2)),1.4)*pow(maxf(0.0,1-pow(warped/1.30,2)),3)
			var spine = 0.52+0.48*absf(noise.get_noise_2d(px,pz))
			var elevation = envelope*spine*height
			var rock = Color("4e5551").lightened(noise.get_noise_2d(px*3,pz*3)*0.22)
			var color = LAND[climate].lerp(rock,smoothstep(0.02,0.44,elevation))
			if elevation>2.8: color=color.lerp(Color("bdc1b9"),smoothstep(2.8,3.8,elevation))
			vertices.append(Vector3(px,0.13+elevation,pz)); colors.append(color)
			if x<nx and z<nz:
				var a = z*(nx+1)+x
				indices.append_array(PackedInt32Array([a,a+nx+1,a+1,a+1,a+nx+1,a+nx+2]))
	var mesh = MeshInstance3D.new()
	mesh.mesh = vertex_mesh(vertices,colors,indices)
	mesh.material_override = vertex_material()
	mesh.rotation.y = float(posmod(seed_value,628))/100
	parent.add_child(mesh)

func pool(parent: Node3D, spread: float, seed_value: int):
	var random = RandomNumberGenerator.new()
	random.seed=seed_value
	var vertices = PackedVector3Array([Vector3(0,0.135,0)])
	var colors = PackedColorArray([Color("335f68")])
	var indices = PackedInt32Array()
	for i in 33:
		var angle = float(i%32)/32*TAU
		var radius_value = spread*(1+0.14*sin(angle*3)+0.1*cos(angle*5))
		vertices.append(Vector3(cos(angle)*radius_value,0.137,sin(angle)*radius_value*0.72))
		colors.append(Color("5c8480"))
		if i>0: indices.append_array(PackedInt32Array([0,i+1,i]))
	var instance = MeshInstance3D.new()
	instance.mesh = vertex_mesh(vertices,colors,indices)
	instance.material_override = vertex_material()
	parent.add_child(instance)

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

func feature(entry: Dictionary, facts: Dictionary, climate: int) -> Node3D:
	var root = Node3D.new()
	var random = RandomNumberGenerator.new()
	random.seed = hash(entry.path)
	var kind = entry.kind
	if kind == "pdf":
		var pages = facts.get("pages", 0)
		var known = pages is int or pages is float
		var height = clampf(log(float(pages) + 1)/log(2.0)*0.29, 0.45, 3.5) if known and pages > 0 else 0.55
		ridge(root,height*1.6,climate,hash(entry.path))
	elif kind == "images":
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
			var point = Vector3(cos(angle)*spread*(1.4 if w>h else 0.9),0.12,sin(angle)*spread)
			tree(root,point,growth*random.randf_range(0.48,0.90),climate,leaf.lightened(random.randf_range(-0.06,0.09)))
	elif kind in ["audio", "video"]:
		var duration = float(facts.get("duration", 0))
		var spread = clampf(0.65 + log(1+duration/60)*0.18, 0.65, 1.45)
		pool(root,spread,hash(entry.path))
		if kind == "video":
			var height = clampf(0.45+log(1+duration/60)*0.18,0.45,1.5)
			box(root, Vector3(0,0.08,-0.27),Vector3(0.68,height,0.45),Color("7b887d"))
			var width = 0.33 if int(facts.get("width",0)) >= 1920 else 0.18
			box(root,Vector3(0,height*0.05,-0.01),Vector3(width,height+0.08,0.025),Color("91d3d1"))
		else:
			for i in mini(3, int(facts.get("channels", 1))):
				var torus = TorusMesh.new()
				torus.inner_radius = spread*(0.35+i*0.2)
				torus.outer_radius = torus.inner_radius+0.014
				torus.rings = 16
				torus.ring_segments = 4
				shape(root,torus,Vector3(0,0.14,0),Color("8ca9a2"))
	elif kind == "tables":
		var rows = int(facts.get("rows", 1))
		var cols = clampi(int(facts.get("columns", 3)),2,7)
		var length = clampf(0.65 + log(float(rows)+1)*0.065,0.65,1.3)
		box(root, Vector3(0,0.075,0),Vector3(1.15,0.03,length),Color("b19a57"))
		for i in cols:
			box(root,Vector3(-0.48+i*(0.96/maxi(1,cols-1)),0.11,0),Vector3(0.055,0.055,length*0.9),Color("5f7c3e"))
	elif kind == "code":
		for i in 3:
			var pos = Vector3((i-1)*0.35,0.08,(i%2)*0.26)
			box(root,pos,Vector3(0.24,0.22,0.26),Color("d1bda0"))
			cone(root,pos+Vector3(0,0.22,0),0.23,0.21,Color("705b4b"),4)
	elif kind == "archives":
		box(root,Vector3(0,0.08,0),Vector3(0.68,0.48,0.56),Color("8d8b79"))
		for i in [-1,1]:
			box(root,Vector3(i*0.32,0.08,0),Vector3(0.20,0.68,0.62),Color("777a70"))
		box(root,Vector3(0,0.08,0.29),Vector3(0.20,0.29,0.015),Color("334a4b"))
	elif kind == "folders":
		# An unobtrusive survey marker, replaced by the real child landscape on entry.
		cone(root,Vector3(0,0.12,0),0.045,0.65,Color("8d8769"),8,0.035)
		globe(root,Vector3(0,0.80,0),0.095,Color("b7aa7c"))
	else:
		for i in 5:
			var pos = Vector3(random.randf_range(-0.5,0.5),0.08,random.randf_range(-0.5,0.5))
			cone(root,pos,0.09,0.18,Color("b7aa70") if kind=="documents" else Color("a4a18c"),5)
	return root
