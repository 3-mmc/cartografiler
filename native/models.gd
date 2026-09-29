extends RefCounted
# Low-poly models for the map's 3D landmarks, built here so no third-party assets are needed.
# Order matches the instance kinds in branchfm/tiles.py (MODEL_NAMES there).
# Every mesh fits a unit footprint standing on y = 0. UV.x marks the tinted part (canopy,
# roof, rock face: 1) against the fixed part (trunk, walls: 0).

static func build() -> Array[ArrayMesh]:
	return [broadleaf(), conifer(), house(), boulder(), palm(), cactus(), shrub(), oak(), flat_house(), factory(),
		warehouse(), silo(), power_station(), ruin(), town_hall(), keep(), steam(), obelisk(), arch(), block(), tower(),
		wall_tower(), aqueduct()]

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

static func _box(st: SurfaceTool, lo: Vector3, hi: Vector3, part: float, top: bool = true):
	var c = (lo+hi)*0.5
	var p = [Vector3(lo.x, lo.y, lo.z), Vector3(hi.x, lo.y, lo.z), Vector3(hi.x, lo.y, hi.z), Vector3(lo.x, lo.y, hi.z),
		Vector3(lo.x, hi.y, lo.z), Vector3(hi.x, hi.y, lo.z), Vector3(hi.x, hi.y, hi.z), Vector3(lo.x, hi.y, hi.z)]
	for i in 4:
		var j = (i+1) % 4
		_tri(st, p[i], p[4+j], p[j], part, c)
		_tri(st, p[i], p[4+i], p[4+j], part, c)
	if top:
		_tri(st, p[4], p[5], p[6], part, c)
		_tri(st, p[4], p[6], p[7], part, c)

static func _gable(st: SurfaceTool, w: float, d: float, h: float, ridge: float, part: float, x0: float = 0.0, z0: float = 0.0):
	# A pitched roof along x over a w x d footprint centred at (x0, z0), eaves at h.
	var a0 = Vector3(x0-w, h, z0-d)
	var a1 = Vector3(x0+w, h, z0-d)
	var b0 = Vector3(x0-w, h, z0+d)
	var b1 = Vector3(x0+w, h, z0+d)
	var r0 = Vector3(x0-w, ridge, z0)
	var r1 = Vector3(x0+w, ridge, z0)
	var c = Vector3(x0, h, z0)
	_tri(st, a0, r1, a1, part, c)
	_tri(st, a0, r0, r1, part, c)
	_tri(st, b0, b1, r1, part, c)
	_tri(st, b0, r1, r0, part, c)
	_tri(st, a0, b0, r0, part, c)
	_tri(st, a1, r1, b1, part, c)

static func _begin() -> SurfaceTool:
	var st = SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	return st

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

# ---------------------------------------------------------------- vegetation

static func palm() -> ArrayMesh:
	var st = _begin()
	# A leaning trunk in three segments, then a crown of drooping fronds.
	var lean = Vector3(0.12, 0, 0.0)
	var pts = [Vector3.ZERO, Vector3(0.04, 0.35, 0), Vector3(0.1, 0.68, 0), Vector3(0.16, 0.95, 0)]
	for i in 3:
		var a = pts[i]
		var b = pts[i+1]
		for k in 5:
			var t0 = TAU*k/5
			var t1 = TAU*(k+1)/5
			var r = 0.045-0.008*i
			var o0 = Vector3(cos(t0)*r, 0, sin(t0)*r)
			var o1 = Vector3(cos(t1)*r, 0, sin(t1)*r)
			_tri(st, a+o0, b+o1, a+o1, 0.0, (a+b)*0.5)
			_tri(st, a+o0, b+o0, b+o1, 0.0, (a+b)*0.5)
	var top = pts[3]
	for k in 7:
		var ang = TAU*k/7+0.3
		var dir = Vector3(cos(ang), 0, sin(ang))
		var side = Vector3(-dir.z, 0, dir.x)*0.12
		var tip = top+dir*0.62+Vector3(0, -0.3, 0)
		var mid = top+dir*0.32+Vector3(0, 0.07, 0)
		_tri(st, top, mid+side, mid-side, 1.0, top+Vector3(0, -0.3, 0))
		_tri(st, mid-side, mid+side, tip, 1.0, top+Vector3(0, -0.3, 0))
	return _finish(st)

static func cactus() -> ArrayMesh:
	var st = _begin()
	_prism(st, 0.09, 0.0, 0.9, 6, 1.0)
	for side in [-1.0, 1.0]:
		var x = side*0.2
		for k in 6:
			var t0 = TAU*k/6
			var t1 = TAU*(k+1)/6
			var r = 0.055
			var a = Vector3(x, 0.35+0.1*side, 0)
			var b = Vector3(x, 0.72+0.05*side, 0)
			var o0 = Vector3(cos(t0)*r, 0, sin(t0)*r)
			var o1 = Vector3(cos(t1)*r, 0, sin(t1)*r)
			_tri(st, a+o0, b+o1, a+o1, 1.0, (a+b)*0.5)
			_tri(st, a+o0, b+o0, b+o1, 1.0, (a+b)*0.5)
		_box(st, Vector3(min(0.0, x), 0.33+0.1*side, -0.04), Vector3(max(0.0, x), 0.42+0.1*side, 0.04), 1.0)
	return _finish(st)

static func shrub() -> ArrayMesh:
	var st = _begin()
	_blob(st, Vector3(-0.12, 0.16, 0.05), Vector3(0.24, 0.18, 0.22), 1.0, 21)
	_blob(st, Vector3(0.14, 0.14, -0.06), Vector3(0.22, 0.16, 0.2), 1.0, 22)
	return _finish(st)

static func oak() -> ArrayMesh:
	var st = _begin()
	_prism(st, 0.09, 0.0, 0.34, 6, 0.0)
	_blob(st, Vector3(0, 0.62, 0), Vector3(0.52, 0.36, 0.5), 1.0, 31)
	_blob(st, Vector3(0.22, 0.52, 0.12), Vector3(0.3, 0.24, 0.3), 1.0, 32)
	_blob(st, Vector3(-0.2, 0.55, -0.14), Vector3(0.3, 0.24, 0.3), 1.0, 33)
	return _finish(st)

# ---------------------------------------------------------------- buildings

static func flat_house() -> ArrayMesh:
	var st = _begin()
	_box(st, Vector3(-0.45, 0, -0.35), Vector3(0.45, 0.38, 0.35), 0.0, false)
	_box(st, Vector3(-0.48, 0.38, -0.38), Vector3(0.48, 0.43, 0.38), 1.0)
	return _finish(st)

static func factory() -> ArrayMesh:
	var st = _begin()
	_box(st, Vector3(-0.5, 0, -0.35), Vector3(0.5, 0.32, 0.35), 0.0, false)
	for i in 3:
		# A sawtooth roof: north lights.
		var x0 = -0.5+i/3.0
		var x1 = x0+1.0/3.0
		var c = Vector3((x0+x1)/2, 0.32, 0)
		_tri(st, Vector3(x0, 0.32, -0.35), Vector3(x1, 0.52, -0.35), Vector3(x1, 0.32, -0.35), 1.0, c)
		_tri(st, Vector3(x0, 0.32, 0.35), Vector3(x1, 0.32, 0.35), Vector3(x1, 0.52, 0.35), 1.0, c)
		_tri(st, Vector3(x0, 0.32, -0.35), Vector3(x0, 0.32, 0.35), Vector3(x1, 0.52, 0.35), 1.0, c)
		_tri(st, Vector3(x0, 0.32, -0.35), Vector3(x1, 0.52, 0.35), Vector3(x1, 0.52, -0.35), 1.0, c)
		_tri(st, Vector3(x1, 0.32, -0.35), Vector3(x1, 0.52, -0.35), Vector3(x1, 0.52, 0.35), 1.0, Vector3(x1+0.1, 0.4, 0))
		_tri(st, Vector3(x1, 0.32, -0.35), Vector3(x1, 0.52, 0.35), Vector3(x1, 0.32, 0.35), 1.0, Vector3(x1+0.1, 0.4, 0))
	_prism(st, 0.06, 0.0, 1.1, 8, 0.0, 0.05)
	return _finish(st)

static func warehouse() -> ArrayMesh:
	var st = _begin()
	_box(st, Vector3(-0.55, 0, -0.28), Vector3(0.55, 0.26, 0.28), 0.0, false)
	_gable(st, 0.56, 0.3, 0.26, 0.36, 1.0)
	return _finish(st)

static func silo() -> ArrayMesh:
	var st = _begin()
	for x in [-0.22, 0.22]:
		for k in 8:
			var t0 = TAU*k/8
			var t1 = TAU*(k+1)/8
			var r = 0.19
			var a0 = Vector3(x+cos(t0)*r, 0, sin(t0)*r)
			var a1 = Vector3(x+cos(t1)*r, 0, sin(t1)*r)
			var b0 = a0+Vector3(0, 0.85, 0)
			var b1 = a1+Vector3(0, 0.85, 0)
			var axis = Vector3(x, 0.4, 0)
			_tri(st, a0, b1, a1, 0.0, axis)
			_tri(st, a0, b0, b1, 0.0, axis)
			_tri(st, b0, Vector3(x, 1.02, 0), b1, 1.0, Vector3(x, 0.8, 0))
	return _finish(st)

static func power_station() -> ArrayMesh:
	var st = _begin()
	# Two cooling towers (waisted frustums) and a turbine hall.
	for x in [-0.25, 0.2]:
		var levels = [[0.26, 0.0], [0.17, 0.5], [0.19, 0.8]]
		for i in 2:
			for k in 10:
				var t0 = TAU*k/10
				var t1 = TAU*(k+1)/10
				var ra = levels[i][0]
				var rb = levels[i+1][0]
				var ya = levels[i][1]
				var yb = levels[i+1][1]
				var axis = Vector3(x, (ya+yb)/2, 0.05)
				var a0 = Vector3(x+cos(t0)*ra, ya, 0.05+sin(t0)*ra)
				var a1 = Vector3(x+cos(t1)*ra, ya, 0.05+sin(t1)*ra)
				var b0 = Vector3(x+cos(t0)*rb, yb, 0.05+sin(t0)*rb)
				var b1 = Vector3(x+cos(t1)*rb, yb, 0.05+sin(t1)*rb)
				_tri(st, a0, b1, a1, 1.0, axis)
				_tri(st, a0, b0, b1, 1.0, axis)
	_box(st, Vector3(-0.2, 0, -0.45), Vector3(0.35, 0.3, -0.22), 0.0)
	return _finish(st)

static func ruin() -> ArrayMesh:
	var st = _begin()
	# Broken walls, no roof.
	_box(st, Vector3(-0.45, 0, -0.35), Vector3(0.45, 0.28, -0.29), 0.0)
	_box(st, Vector3(-0.45, 0, -0.35), Vector3(-0.39, 0.2, 0.35), 0.0)
	_box(st, Vector3(0.39, 0, -0.35), Vector3(0.45, 0.12, 0.1), 0.0)
	_box(st, Vector3(-0.45, 0, 0.29), Vector3(0.0, 0.16, 0.35), 0.0)
	_blob(st, Vector3(0.2, 0.02, 0.2), Vector3(0.14, 0.06, 0.12), 1.0, 41)
	return _finish(st)

static func town_hall() -> ArrayMesh:
	var st = _begin()
	_box(st, Vector3(-0.5, 0, -0.32), Vector3(0.5, 0.38, 0.32), 0.0, false)
	_gable(st, 0.52, 0.34, 0.38, 0.56, 1.0)
	_box(st, Vector3(-0.1, 0, -0.1), Vector3(0.1, 0.95, 0.1), 0.0)
	_prism(st, 0.13, 0.95, 1.25, 4, 1.0, 0.0)
	return _finish(st)

static func keep() -> ArrayMesh:
	var st = _begin()
	_box(st, Vector3(-0.28, 0, -0.28), Vector3(0.28, 0.95, 0.28), 0.0, false)
	_box(st, Vector3(-0.31, 0.9, -0.31), Vector3(0.31, 0.95, 0.31), 0.0)
	for i in 4:
		for j in 4:
			if i in [1, 2] and j in [1, 2]: continue
			var x = -0.31+i*0.155
			var z = -0.31+j*0.155
			_box(st, Vector3(x, 0.95, z), Vector3(x+0.1, 1.07, z+0.1), 0.0)
	_prism(st, 0.12, 0.95, 1.35, 4, 1.0, 0.0)   # a banner-pole spire, tinted
	return _finish(st)

# ---------------------------------------------------------------- landforms

static func steam() -> ArrayMesh:
	# A geyser's plume (or a volcano's smoke): stacked puffs, animated in the shader.
	var st = _begin()
	_blob(st, Vector3(0, 0.25, 0), Vector3(0.14, 0.25, 0.14), 1.0, 51)
	_blob(st, Vector3(0.03, 0.6, 0.02), Vector3(0.2, 0.2, 0.2), 1.0, 52)
	_blob(st, Vector3(0.08, 0.9, -0.03), Vector3(0.26, 0.18, 0.24), 1.0, 53)
	return _finish(st)

static func obelisk() -> ArrayMesh:
	var st = _begin()
	_box(st, Vector3(-0.3, 0, -0.3), Vector3(0.3, 0.08, 0.3), 0.0)
	_prism(st, 0.12, 0.08, 1.0, 4, 1.0, 0.08)
	_prism(st, 0.08, 1.0, 1.15, 4, 1.0, 0.0)
	return _finish(st)

static func aqueduct() -> ArrayMesh:
	# One span of a Roman arcade, built to abut its neighbours: the deck runs the full unit
	# width so a row of them joins into a continuous channel, and the piers stand at the unit
	# edges so two units share a pier. Masonry, unlike the natural arch above it in this file.
	var st = _begin()
	var half = 0.5
	var pier = 0.12
	var spring = 0.42          # where the arch springs from the piers
	var deck = 0.86
	var depth = 0.16
	# Piers, at both edges so neighbouring spans share them.
	_box(st, Vector3(-half, 0.0, -depth), Vector3(-half+pier, spring, depth), 1.0, false)
	_box(st, Vector3(half-pier, 0.0, -depth), Vector3(half, spring, depth), 1.0, false)
	# The arch ring over the opening, as a band of voussoirs.
	var segs = 10
	var r = half-pier
	var prev_in = Vector3.ZERO
	var prev_out = Vector3.ZERO
	for i in segs+1:
		var a2 = PI*float(i)/segs
		var cx = -cos(a2)*r
		var cy = spring+sin(a2)*r*0.62
		var nx = -cos(a2)
		var ny = sin(a2)*0.62
		var l = sqrt(nx*nx+ny*ny)
		nx /= l
		ny /= l
		var inner = Vector3(cx, cy, 0)
		var outer = Vector3(cx+nx*0.10, cy+ny*0.10, 0)
		if i > 0:
			for s in [-depth, depth]:
				var a3 = prev_in+Vector3(0, 0, s)
				var b3 = prev_out+Vector3(0, 0, s)
				var c3 = outer+Vector3(0, 0, s)
				var d3 = inner+Vector3(0, 0, s)
				_tri(st, a3, b3, c3, 1.0, Vector3(0, spring, 0))
				_tri(st, a3, c3, d3, 1.0, Vector3(0, spring, 0))
			# The soffit: the underside of the opening.
			_tri(st, prev_in+Vector3(0, 0, -depth), inner+Vector3(0, 0, -depth), inner+Vector3(0, 0, depth), 1.0, Vector3(0, 9, 0))
			_tri(st, prev_in+Vector3(0, 0, -depth), inner+Vector3(0, 0, depth), prev_in+Vector3(0, 0, depth), 1.0, Vector3(0, 9, 0))
		prev_in = inner
		prev_out = outer
	# The deck, spanning the whole unit so a run of them is unbroken.
	_box(st, Vector3(-half, deck, -depth), Vector3(half, deck+0.14, depth), 1.0)
	return _finish(st)

static func arch() -> ArrayMesh:
	# A sandstone arch in the manner of Delicate Arch: a slickrock fin at the base, two massive
	# buttressed legs, and a thick span thinning to a waist at the top, all banded red rock.
	var st = _begin()
	_blob(st, Vector3(0, 0.05, 0), Vector3(0.75, 0.14, 0.42), 0.0, 61)
	_blob(st, Vector3(-0.5, 0.2, 0.02), Vector3(0.22, 0.3, 0.24), 1.0, 62)
	_blob(st, Vector3(0.52, 0.18, -0.02), Vector3(0.24, 0.26, 0.22), 1.0, 63)
	var segs = 14
	var ring = 7
	var prev = []
	for i in segs+1:
		var t = float(i)/segs
		var a = PI*t
		var cx = -cos(a)*0.5
		var cy = sin(a)*1.0+0.12
		var tangent = Vector3(sin(a)*0.5, cos(a)*1.0, 0).normalized()
		var waist = sin(a)
		var thick = lerpf(0.2, 0.1, waist)*(1.0+0.15*sin(t*19.0+1.3))
		var depth = lerpf(0.2, 0.12, waist)
		var normal = Vector3(-tangent.y, tangent.x, 0)
		var pts = []
		for k in ring:
			var th = TAU*k/ring
			pts.append(Vector3(cx, cy, 0)+normal*cos(th)*thick+Vector3(0, 0, sin(th)*depth))
		if i > 0:
			var c = Vector3(cx, cy, 0)
			for k in ring:
				var k1 = (k+1) % ring
				_tri(st, prev[k], pts[k1], prev[k1], 1.0, c)
				_tri(st, prev[k], pts[k], pts[k1], 1.0, c)
		prev = pts
	return _finish(st)

static func block() -> ArrayMesh:
	# A mid-rise city block: a flat-roofed slab with a parapet and a roof-top box.
	var st = _begin()
	_box(st, Vector3(-0.42, 0, -0.36), Vector3(0.42, 0.62, 0.36), 1.0, false)
	_box(st, Vector3(-0.44, 0.62, -0.38), Vector3(0.44, 0.67, 0.38), 0.0)
	_box(st, Vector3(-0.12, 0.67, -0.1), Vector3(0.14, 0.76, 0.12), 0.0)
	return _finish(st)

static func tower() -> ArrayMesh:
	# A tower: a tall shaft on a podium, with a setback and a crown.
	var st = _begin()
	_box(st, Vector3(-0.45, 0, -0.4), Vector3(0.45, 0.22, 0.4), 0.0, false)
	_box(st, Vector3(-0.3, 0.22, -0.28), Vector3(0.3, 1.2, 0.28), 1.0, false)
	_box(st, Vector3(-0.22, 1.2, -0.2), Vector3(0.22, 1.45, 0.2), 1.0, false)
	_box(st, Vector3(-0.24, 1.45, -0.22), Vector3(0.24, 1.5, 0.22), 0.0)
	return _finish(st)

static func wall_tower() -> ArrayMesh:
	# A round wall tower with a crenellated top: strung along a repository's rampart.
	var st = _begin()
	_prism(st, 0.3, 0.0, 0.7, 8, 1.0)
	for k in 8:
		if k % 2 == 1: continue
		var a = TAU*k/8
		var c = Vector3(cos(a)*0.26, 0.7, sin(a)*0.26)
		_box(st, c-Vector3(0.07, 0, 0.07), c+Vector3(0.07, 0.12, 0.07), 1.0)
	return _finish(st)
