extends RefCounted
# Low-poly models for the map's 3D landmarks, built here so no third-party assets are needed.
# Order matches the instance kinds in branchfm/tiles.py: broadleaf, conifer, house, boulder.
# Every mesh fits a unit footprint standing on y = 0. UV.x marks the tinted part (canopy,
# roof, rock face: 1) against the fixed part (trunk, walls: 0).

static func build() -> Array[ArrayMesh]:
	return [broadleaf(), conifer(), house(), boulder()]

static func _tri(st: SurfaceTool, a: Vector3, b: Vector3, c: Vector3, part: float, centre: Vector3 = Vector3(INF, 0, 0)):
	# Faces point away from the model's centre (or its axis), wound clockwise as seen from
	# outside, which is Godot's front face.
	var n = (b-a).cross(c-a)
	var mid = (a+b+c)/3.0
	var inside = centre if centre.x != INF else Vector3(0, mid.y, 0)
	var out = mid-inside
	if n.dot(out) > 0.0:
		var t = b
		b = c
		c = t
		n = -n
	n = -n.normalized()
	for p in [a, b, c]:
		st.set_normal(n)
		st.set_uv(Vector2(part, 0.0))
		st.add_vertex(p)

static func _prism(st: SurfaceTool, radius: float, y0: float, y1: float, sides: int, part: float, top_radius: float = -1.0):
	# A cylinder or cone frustum (top_radius 0 makes a cone), outward-facing, with a top cap.
	var rt = radius if top_radius < 0.0 else top_radius
	for i in sides:
		var a0 = TAU*i/sides
		var a1 = TAU*(i+1)/sides
		var b0 = Vector3(cos(a0)*radius, y0, sin(a0)*radius)
		var b1 = Vector3(cos(a1)*radius, y0, sin(a1)*radius)
		var t0 = Vector3(cos(a0)*rt, y1, sin(a0)*rt)
		var t1 = Vector3(cos(a1)*rt, y1, sin(a1)*rt)
		if rt > 0.0:
			_tri(st, b0, t1, b1, part)
			_tri(st, b0, t0, t1, part)
			_tri(st, t0, Vector3(0, y1, 0), t1, part, Vector3(0, y0, 0))
		else:
			_tri(st, b0, Vector3(0, y1, 0), b1, part)

static func _blob(st: SurfaceTool, centre: Vector3, radius: Vector3, part: float, seed: int):
	# A lumpy icosahedron: canopy or boulder.
	var t = (1.0+sqrt(5.0))/2.0
	var v = [Vector3(-1, t, 0), Vector3(1, t, 0), Vector3(-1, -t, 0), Vector3(1, -t, 0), Vector3(0, -1, t), Vector3(0, 1, t),
		Vector3(0, -1, -t), Vector3(0, 1, -t), Vector3(t, 0, -1), Vector3(t, 0, 1), Vector3(-t, 0, -1), Vector3(-t, 0, 1)]
	var f = [[0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11], [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8],
		[3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9], [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1]]
	var rng = RandomNumberGenerator.new()
	rng.seed = seed
	var p = []
	for q in v:
		var n = q.normalized()*rng.randf_range(0.85, 1.12)
		p.append(centre+Vector3(n.x*radius.x, n.y*radius.y, n.z*radius.z))
	for tri in f:
		_tri(st, p[tri[0]], p[tri[2]], p[tri[1]], part, centre)

static func _finish(st: SurfaceTool) -> ArrayMesh:
	st.index()
	return st.commit()

static func broadleaf() -> ArrayMesh:
	var st = SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	_prism(st, 0.05, 0.0, 0.42, 5, 0.0)
	_blob(st, Vector3(0, 0.62, 0), Vector3(0.42, 0.36, 0.42), 1.0, 7)
	return _finish(st)

static func conifer() -> ArrayMesh:
	var st = SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	_prism(st, 0.045, 0.0, 0.25, 5, 0.0)
	_prism(st, 0.34, 0.18, 0.72, 7, 1.0, 0.0)
	_prism(st, 0.25, 0.5, 1.05, 7, 1.0, 0.0)
	return _finish(st)

static func house() -> ArrayMesh:
	var st = SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var w = 0.5
	var d = 0.36
	var h = 0.34
	var r = 0.62
	var c = [Vector3(-w, 0, -d), Vector3(w, 0, -d), Vector3(w, 0, d), Vector3(-w, 0, d)]
	var top = [Vector3(-w, h, -d), Vector3(w, h, -d), Vector3(w, h, d), Vector3(-w, h, d)]
	var centre = Vector3(0, h*0.5, 0)
	for i in 4:
		var j = (i+1) % 4
		_tri(st, c[i], top[j], c[j], 0.0, centre)
		_tri(st, c[i], top[i], top[j], 0.0, centre)
	# Gable roof along x, eaves overhanging a little.
	var e = 1.08
	var r0 = Vector3(-w*e, r, 0)
	var r1 = Vector3(w*e, r, 0)
	var a0 = Vector3(-w*e, h-0.02, -d*e)
	var a1 = Vector3(w*e, h-0.02, -d*e)
	var b0 = Vector3(-w*e, h-0.02, d*e)
	var b1 = Vector3(w*e, h-0.02, d*e)
	var roof_centre = Vector3(0, h, 0)
	_tri(st, a0, r1, a1, 1.0, roof_centre)
	_tri(st, a0, r0, r1, 1.0, roof_centre)
	_tri(st, b0, b1, r1, 1.0, roof_centre)
	_tri(st, b0, r1, r0, 1.0, roof_centre)
	_tri(st, top[0], Vector3(-w, r, 0), top[3], 0.0, roof_centre)
	_tri(st, top[1], top[2], Vector3(w, r, 0), 0.0, roof_centre)
	return _finish(st)

static func boulder() -> ArrayMesh:
	var st = SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	_blob(st, Vector3(0, 0.12, 0), Vector3(0.45, 0.3, 0.38), 1.0, 11)
	return _finish(st)
