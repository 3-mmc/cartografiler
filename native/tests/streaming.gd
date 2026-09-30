extends SceneTree

class Map:
	extends "res://main.gd"
	var bounds = [0.1, 0.1, 0.4, 0.4]
	var launched = []
	func view_bbox() -> Array: return bounds
	func fetch_tile(l: int, x: int, y: int): launched.append(tile_key(l, x, y))

var failures = 0

func check(ok: bool, message: String):
	if not ok:
		printerr(message)
		failures += 1

func loaded(map, l: int, x: int, y: int):
	var node = MeshInstance3D.new()
	node.visible = false
	map.add_child(node)
	map.tiles[map.tile_key(l, x, y)] = {"node":node, "level":l, "x":x, "y":y, "S":pow(0.5,l), "last_used":map.clock}

func settle(map):
	for key in map.wanted.keys():
		var p = key.split(":")
		loaded(map, int(p[0]), int(p[1]), int(p[2]))
	map.tile_requests.clear()
	map.inflight = 0
	map.update_tiles()

func _initialize():
	var map = Map.new()
	map.view = 0.65 # level 2
	map.update_tiles()
	settle(map)
	check(map.level == 2, "initial level must settle")
	var previous = map.wanted.keys()
	map.view = 1.3 # zoom out to level 1
	map.update_tiles()
	check(map.level == 2, "zoom out must retain displayed LOD until replacements load")
	for key in previous: check(map.tiles[key].node.visible, "zoom out hid existing ground")
	settle(map)
	check(map.level == 1, "zoom out must commit loaded LOD")
	for key in previous: check(not map.tiles[key].node.visible, "old LOD must hide after swap")
	map.view = 0.65
	map.update_tiles()
	check(map.level == 2, "zoom back must reuse cached tiles")
	map.view = 0.325
	map.update_tiles()
	check(map.level == 2, "zoom in must retain displayed LOD")
	settle(map)
	check(map.level == 3, "zoom in must commit loaded LOD")
	map.view = 2.6/pow(2.0, 3.51)
	map.update_tiles()
	check(map.requested_level == 3, "LOD boundary must have hysteresis")
	map.clock = 10
	map.update_tiles()
	check(map.tiles.has(previous[0]), "cache must survive more than five seconds")
	map.invalidate_tiles([[0,0,1,1], [0,0,1,1]])
	map.update_tiles()
	check(map.inflight <= map.MAX_INFLIGHT, "refreshes exceeded request limit")
	var before = map.launched.size()
	map.invalidate_tiles([[0,0,1,1]])
	map.update_tiles()
	check(map.launched.size() == before, "repeated invalidations duplicated requests")
	check(map.dirty_tiles.size() > 0, "in-flight invalidations must survive for a follow-up")
	map.free()
	map = Map.new()
	map.view = 0.65
	map.update_tiles()
	settle(map)
	previous = map.wanted.keys()
	map.bounds = [0.6, 0.6, 0.9, 0.9]
	map.update_tiles()
	settle(map)
	map.clock = 12
	map.bounds = [0.1, 0.1, 0.4, 0.4]
	before = map.launched.size()
	map.update_tiles()
	check(map.launched.size() == before, "pan back must reuse cached terrain")
	for key in previous: check(map.tiles[key].node.visible, "pan back must immediately display cached terrain")
	map.clock = 50
	map.update_tiles()
	check(not map.tiles.has("2:3:3"), "unused tiles must expire by elapsed time")
	map.free()
	print("STREAMING_TESTS failures=", failures)
	quit(1 if failures else 0)
