extends Node3D

const Terrain = preload("res://terrain.gd")
const SERIF = preload("res://fonts/Cartography.ttf")
const ITALIC = preload("res://fonts/CartographyItalic.ttf")
var terrain = Terrain.new()
var api_url = OS.get_environment("BRANCH_API")
var token = OS.get_environment("BRANCH_TOKEN")
var regions = {}
var current = ""
var selected = -1
var metadata_cache = {}
var hidden_files = false
var filter_text = ""
var request_generation = 0
var preview_generation = 0
var loading = false
var moving = false
var clipboard = {}
var label_mode = 0
var labels = []
var region_transforms = {}
var camera_center = Vector3.ZERO
var desired_center = Vector3.ZERO
var desired_size = 20.0
var camera_angle = 0.0
var dragging = false
var camera_tween: Tween
var smoke = false
var capture_path = ""

var map_container: SubViewportContainer
var viewport: SubViewport
var world: Node3D
var camera: Camera3D
var selection_ring: MeshInstance3D
var overlay: Control
var screen_ui: Control
var file_list: ItemList
var path_field: LineEdit
var search_field: LineEdit
var status: Label
var region_heading: Label
var detail_name: Label
var detail_meta: Label
var preview_text: RichTextLabel
var preview_image: TextureRect
var page_label: Label
var climate_button: Button
var labels_button: Button
var breadcrumbs: HBoxContainer
var debounce: Timer
var dialog: ConfirmationDialog
var dialog_field: LineEdit
var pending_action = ""
var dialog_source = ""
var preview_data = {}
var command_window: AcceptDialog
var command_field: LineEdit
var command_results: ItemList
var command_status: Label
var destinations = []
var command_running = false
var left_panel: PanelContainer
var right_panel: PanelContainer
var map_focus = false

func _ready():
	var args = OS.get_cmdline_user_args()
	smoke = "--smoke" in args
	if smoke:
		get_tree().create_timer(75).timeout.connect(func():
			push_error("Native smoke test exceeded 75 seconds")
			get_tree().quit(2))
	var capture_index = args.find("--capture")
	if capture_index >= 0 and capture_index+1 < args.size(): capture_path = args[capture_index+1]
	_build_ui()
	_build_world()
	if api_url.is_empty():
		status.text = "Start this application with the atlas launcher so it can access files."
		return
	await navigate(OS.get_environment("BRANCH_ROOT"), "root")
	if smoke: _smoke_test()

func panel_style(color: Color, border: Color = Color("405c5c")) -> StyleBoxFlat:
	var style = StyleBoxFlat.new()
	style.bg_color = color
	style.border_color = border
	style.set_border_width_all(1)
	style.set_content_margin_all(14)
	return style

func label(text: String, parent: Node, font_size: int = 14, color: Color = Color("dddccb")) -> Label:
	var item = Label.new()
	item.text = text
	item.add_theme_font_size_override("font_size", font_size)
	item.add_theme_color_override("font_color", color)
	parent.add_child(item)
	return item

func button(text: String, parent: Node, callback: Callable) -> Button:
	var item = Button.new()
	item.text = text
	item.pressed.connect(callback)
	parent.add_child(item)
	return item

func _build_ui():
	var canvas = CanvasLayer.new()
	add_child(canvas)
	screen_ui = Control.new()
	screen_ui.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	screen_ui.mouse_filter = Control.MOUSE_FILTER_IGNORE
	canvas.add_child(screen_ui)
	var theme = Theme.new()
	theme.default_font_size = 14
	theme.set_stylebox("normal", "Button", panel_style(Color("243d3e")))
	theme.set_stylebox("hover", "Button", panel_style(Color("375551"), Color("b5a26c")))
	theme.set_stylebox("pressed", "Button", panel_style(Color("4d6357"), Color("d2bd7f")))
	theme.set_stylebox("focus", "Button", panel_style(Color(0,0,0,0), Color("d2bd7f")))
	theme.set_color("font_color", "Button", Color("e3dfc9"))
	theme.set_stylebox("normal", "LineEdit", panel_style(Color("162d31")))
	theme.set_color("font_color", "LineEdit", Color("e0ddc8"))
	theme.set_stylebox("panel", "ItemList", panel_style(Color("162d31")))
	theme.set_stylebox("selected", "ItemList", panel_style(Color("3e5b52"), Color("bca66d")))
	theme.set_color("font_color", "ItemList", Color("d5d8c8"))
	screen_ui.theme = theme
	var top = PanelContainer.new()
	top.set_anchors_and_offsets_preset(Control.PRESET_TOP_WIDE)
	top.offset_bottom = 100
	top.add_theme_stylebox_override("panel", panel_style(Color("142c30")))
	screen_ui.add_child(top)
	var top_rows = VBoxContainer.new()
	top.add_child(top_rows)
	var row = HBoxContainer.new()
	row.add_theme_constant_override("separation", 12)
	top_rows.add_child(row)
	var title = label("Branch Atlas", row, 24, Color("d9c68f"))
	title.add_theme_font_override("font", SERIF)
	button("↑ Parent", row, go_parent)
	path_field = LineEdit.new()
	path_field.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	path_field.placeholder_text = "Folder path"
	path_field.text_submitted.connect(func(value): navigate(normalize_path(value), "root"))
	row.add_child(path_field)
	button("Go", row, func(): navigate(normalize_path(path_field.text), "root"))
	button("Choose folder", row, choose_folder)
	button("Bash  Ctrl+P", row, command_palette)
	button("?", row, show_help)
	breadcrumbs = HBoxContainer.new()
	top_rows.add_child(breadcrumbs)
	map_container = SubViewportContainer.new()
	map_container.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	map_container.offset_left = 252
	map_container.offset_top = 101
	map_container.offset_right = -348
	map_container.offset_bottom = -82
	map_container.stretch = true
	map_container.mouse_filter = Control.MOUSE_FILTER_PASS
	screen_ui.add_child(map_container)
	viewport = SubViewport.new()
	viewport.own_world_3d = true
	viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	viewport.msaa_3d = Viewport.MSAA_2X
	map_container.add_child(viewport)
	overlay = Control.new()
	overlay.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	overlay.mouse_filter = Control.MOUSE_FILTER_IGNORE
	map_container.add_child(overlay)
	var left = PanelContainer.new()
	left_panel = left
	left.set_anchors_and_offsets_preset(Control.PRESET_LEFT_WIDE)
	left.offset_top = 101
	left.offset_bottom = -82
	left.offset_right = 251
	left.add_theme_stylebox_override("panel", panel_style(Color("182f31")))
	screen_ui.add_child(left)
	var files = VBoxContainer.new()
	files.add_theme_constant_override("separation", 9)
	left.add_child(files)
	region_heading = label("Exploring", files, 18, Color("d9c68f"))
	region_heading.add_theme_font_override("font", SERIF)
	region_heading.clip_text = true
	search_field = LineEdit.new()
	search_field.placeholder_text = "Filter this directory…"
	files.add_child(search_field)
	debounce = Timer.new()
	debounce.one_shot = true
	debounce.wait_time = 0.3
	add_child(debounce)
	search_field.text_changed.connect(func(_v): debounce.start())
	debounce.timeout.connect(func():
		filter_text = search_field.text
		navigate(current, "refresh"))
	var hidden = CheckButton.new()
	hidden.text = "Hidden files"
	hidden.toggled.connect(func(value):
		hidden_files = value
		navigate(current, "refresh"))
	files.add_child(hidden)
	file_list = ItemList.new()
	file_list.size_flags_vertical = Control.SIZE_EXPAND_FILL
	file_list.item_selected.connect(select_file)
	file_list.item_activated.connect(func(index):
		select_file(index)
		enter_selected())
	files.add_child(file_list)
	var pages = HBoxContainer.new()
	files.add_child(pages)
	button("‹", pages, func(): change_page(-1))
	page_label = label("", pages, 12)
	page_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	page_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	button("›", pages, func(): change_page(1))
	button("New folder", files, func(): ask_action("mkdir"))
	button("Refresh", files, func():
		metadata_cache.clear()
		navigate(current, "refresh"))
	var right = PanelContainer.new()
	right_panel = right
	right.set_anchors_and_offsets_preset(Control.PRESET_RIGHT_WIDE)
	right.offset_left = -347
	right.offset_top = 101
	right.offset_bottom = -82
	right.add_theme_stylebox_override("panel", panel_style(Color("182f31")))
	screen_ui.add_child(right)
	var inspector = VBoxContainer.new()
	inspector.add_theme_constant_override("separation", 10)
	right.add_child(inspector)
	label("Field notes", inspector, 21, Color("d9c68f")).add_theme_font_override("font", SERIF)
	detail_name = label("Select a landmark", inspector, 17)
	detail_name.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	detail_meta = label("", inspector, 12, Color("a8bcb2"))
	detail_meta.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	preview_image = TextureRect.new()
	preview_image.custom_minimum_size = Vector2(0,180)
	preview_image.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	preview_image.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	preview_image.visible = false
	inspector.add_child(preview_image)
	preview_text = RichTextLabel.new()
	preview_text.bbcode_enabled = false
	preview_text.size_flags_vertical = Control.SIZE_EXPAND_FILL
	preview_text.selection_enabled = true
	preview_text.add_theme_font_size_override("normal_font_size", 13)
	inspector.add_child(preview_text)
	var actions = GridContainer.new()
	actions.columns = 2
	inspector.add_child(actions)
	button("Peek  Space", actions, peek)
	button("Open externally", actions, func(): operate("open"))
	button("Rename", actions, func(): ask_action("rename"))
	button("Copy", actions, func(): set_clipboard("copy"))
	button("Cut", actions, func(): set_clipboard("move"))
	button("Paste here", actions, paste)
	button("Move to trash", actions, func(): ask_action("trash"))
	button("Undo move", actions, func(): operate("undo"))
	var bottom = PanelContainer.new()
	bottom.set_anchors_and_offsets_preset(Control.PRESET_BOTTOM_WIDE)
	bottom.offset_top = -81
	bottom.add_theme_stylebox_override("panel", panel_style(Color("142c30")))
	screen_ui.add_child(bottom)
	var bottom_rows = VBoxContainer.new()
	bottom.add_child(bottom_rows)
	var controls = HBoxContainer.new()
	controls.add_theme_constant_override("separation",8)
	bottom_rows.add_child(controls)
	button("World / local  Z", controls, overview)
	labels_button = button("Labels: all  L", controls, cycle_labels)
	climate_button = button("Climate", controls, cycle_climate)
	button("−", controls, func(): desired_size = minf(800, desired_size*1.3))
	button("+", controls, func(): desired_size = maxf(1, desired_size/1.3))
	button("Map focus  F", controls, toggle_map_focus)
	label("Drag to pan · wheel to zoom", controls, 12, Color("a8bcb2"))
	status = label("Surveying the landscape…", bottom_rows, 12, Color("cdbb87"))
	status.clip_text = true
	dialog = ConfirmationDialog.new()
	dialog.min_size = Vector2i(480,150)
	dialog.theme = theme
	screen_ui.add_child(dialog)
	dialog_field = LineEdit.new()
	dialog_field.custom_minimum_size = Vector2(440,40)
	dialog.add_child(dialog_field)
	dialog.confirmed.connect(confirm_action)
	get_window().min_size = Vector2i(1080,700)

func _build_world():
	world = Node3D.new()
	viewport.add_child(world)
	var environment = WorldEnvironment.new()
	var config = Environment.new()
	config.background_mode = Environment.BG_COLOR
	config.background_color = Color("173943")
	config.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	config.ambient_light_color = Color("c4d8dc")
	config.ambient_light_energy = 0.35
	config.tonemap_mode = Environment.TONE_MAPPER_LINEAR
	environment.environment = config
	world.add_child(environment)
	var sun = DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-53,-28,0)
	sun.light_color = Color("ffedca")
	sun.light_energy = 0.50
	sun.shadow_enabled = true
	sun.directional_shadow_max_distance = 80
	world.add_child(sun)
	var water_mesh = PlaneMesh.new()
	water_mesh.size = Vector2(4000,4000)
	terrain.shape(world,water_mesh,Vector3(0,-0.31,0),Color("285a68"))
	camera = Camera3D.new()
	camera.projection = Camera3D.PROJECTION_ORTHOGONAL
	camera.near = 0.005
	camera.far = 3000
	camera.size = desired_size
	camera.current = true
	world.add_child(camera)
	var ring = TorusMesh.new()
	ring.inner_radius = 0.83
	ring.outer_radius = 0.88
	ring.rings = 32
	ring.ring_segments = 5
	selection_ring = terrain.shape(world,ring,Vector3(0,0.16,0),Color("ffe19b"))
	selection_ring.visible = false

func api(endpoint: String, data: Variant = null) -> Dictionary:
	var request = HTTPRequest.new()
	request.timeout = 0 if data is Dictionary and data.get("action","") in ["copy","move"] else 12
	add_child(request)
	var headers = PackedStringArray(["Authorization: Bearer "+token, "Content-Type: application/json"])
	var err = request.request(api_url+endpoint,headers,HTTPClient.METHOD_GET if data == null else HTTPClient.METHOD_POST, "" if data == null else JSON.stringify(data))
	if err != OK:
		request.queue_free()
		return {"error":"Cannot reach the local file service."}
	var response = await request.request_completed
	request.queue_free()
	if response[0] != HTTPRequest.RESULT_SUCCESS:
		return {"error":"The file service request failed or timed out."}
	var parsed = JSON.parse_string(response[3].get_string_from_utf8())
	return parsed if parsed is Dictionary else {"error":"Invalid response from the file service."}

func normalize_path(value: String) -> String:
	if value.length()>2 and value[1]==":":
		return "/mnt/"+value[0].to_lower()+"/"+value.substr(2).replace("\\","/").trim_prefix("/")
	return value

func navigate(path: String, direction: String, page: int = 0, focus: String = ""):
	if loading or moving or path.is_empty(): return
	loading = true
	status.text = "Surveying " + path
	var query = "/list?path="+path.uri_encode()+"&hidden="+str(hidden_files)+"&page="+str(page)
	if not focus.is_empty(): query += "&focus="+focus.uri_encode()
	if direction == "refresh": query += "&filter="+filter_text.uri_encode()
	var data = await api(query)
	if data.has("error"):
		status.text = data.error
		loading = false
		return
	path = data.path
	request_generation += 1
	if direction == "root":
		for region in regions.values(): region.visual.queue_free()
		regions.clear()
		current = ""
	if direction != "refresh":
		filter_text = ""
		search_field.text = ""
	var previous = current
	var local_pos = Vector3.ZERO
	var factor = 1.0
	var parent_key = ""
	var weather = posmod(hash(path),5)
	if direction == "child" and regions.has(previous):
		var parent = regions[previous]
		var index = -1
		for i in parent.entries.size():
			if parent.entries[i].path == path: index = i
		if index >= 0:
			local_pos = parent.points[index]+Vector3(0,0.085,0)
			factor = 1.4/terrain.radius(data.entries)
			parent_key = previous
			weather = (parent.climate+index+1)%5
	elif regions.has(path):
		local_pos = regions[path].local_pos
		factor = regions[path].factor
		parent_key = regions[path].parent_key
		weather = regions[path].climate
	var region = {"path":path,"data":data,"entries":data.entries,"points":terrain.positions(data.entries),
		"radius":terrain.radius(data.entries),"climate":weather,"parent_key":parent_key,"local_pos":local_pos,"factor":factor,"features":[]}
	if regions.has(path): regions[path].visual.queue_free()
	regions[path] = region
	build_region(region)
	if direction == "parent" and regions.has(previous) and regions[previous].parent_key.is_empty():
		for i in region.entries.size():
			if region.entries[i].path == previous:
				regions[previous].parent_key = path
				regions[previous].local_pos = region.points[i]+Vector3(0,0.085,0)
				regions[previous].factor = 1.4/regions[previous].radius
	if direction == "child" and not previous.is_empty():
		# Add the child's tiny landscape before moving the camera toward it.
		current = previous
		apply_transforms()
		await fly_to(local_pos, region.radius*2.6*factor)
	elif direction == "parent" and regions.has(previous) and regions[previous].parent_key == path:
		apply_transforms()
		var child = regions[previous]
		await fly_to(-child.local_pos/child.factor, region.radius*2.6/child.factor)
	current = path
	camera_center = Vector3.ZERO
	desired_center = Vector3.ZERO
	desired_size = region.radius*2.6
	camera.size = desired_size
	apply_transforms()
	selected = -1
	update_listing()
	loading = false
	status.text = "Directory regions nest inside their parent. Rivers trace branches; coastal streams and climate are illustrative."
	if not region.entries.is_empty(): select_file(0)
	else:
		detail_name.text = "Empty region"
		preview_text.text = "This directory has no matching files."
		preview_image.visible = false
	load_metadata(path,request_generation)

func build_region(region: Dictionary):
	var visual = Node3D.new()
	world.add_child(visual)
	region.visual = visual
	visual.add_child(terrain.land(region.entries,region.climate,hash(region.path)))
	region.features = []
	for i in region.entries.size():
		var entry = region.entries[i]
		var feature = terrain.feature(entry,metadata_cache.get(entry.path,{}),region.climate)
		feature.position = region.points[i]
		visual.add_child(feature)
		region.features.append(feature)

func apply_transforms():
	if not regions.has(current): return
	region_transforms = {current:{"pos":Vector3.ZERO,"scale":1.0}}
	var path = current
	# Keep two ancestor levels around the active landscape. Deeper levels still
	# exist in the model and reappear when zooming back out.
	for depth in 2:
		var r = regions[path]
		if r.parent_key.is_empty() or not regions.has(r.parent_key): break
		var transform = region_transforms[path]
		var parent_scale = transform.scale/r.factor
		region_transforms[r.parent_key] = {"pos":transform.pos-r.local_pos*parent_scale,"scale":parent_scale}
		path = r.parent_key
	for depth in 4:
		for key in regions:
			var r = regions[key]
			if region_transforms.has(key) or not region_transforms.has(r.parent_key): continue
			var parent = region_transforms[r.parent_key]
			if not regions[r.parent_key].entries.any(func(entry): return entry.path==key): continue
			var scale_value = parent.scale*r.factor
			if scale_value >= 0.002:
				region_transforms[key] = {"pos":parent.pos+r.local_pos*parent.scale,"scale":scale_value}
	for key in regions:
		var r = regions[key]
		r.visual.visible = region_transforms.has(key)
		if not r.visual.visible: continue
		var transform = region_transforms[key]
		r.visual.position = transform.pos
		r.visual.scale = Vector3.ONE*transform.scale
		for i in r.entries.size():
			# Explored directories reveal actual miniature landscapes in their region.
			var child_path = r.entries[i].path
			r.features[i].visible = not (r.entries[i].directory and regions.has(child_path) and regions[child_path].parent_key==key)
	_rebuild_labels()

func fly_to(point: Vector3, zoom: float):
	moving = true
	selection_ring.visible = false
	if camera_tween: camera_tween.kill()
	camera_tween = create_tween().set_parallel(true)
	camera_tween.set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_IN_OUT)
	camera_tween.tween_property(self,"camera_center",point,0.65)
	camera_tween.tween_property(camera,"size",zoom,0.65)
	await camera_tween.finished
	moving = false

func update_listing():
	var r = regions[current]
	path_field.text = current
	region_heading.text = current.get_file() if not current.get_file().is_empty() else "/"
	climate_button.text = "Climate: "+Terrain.CLIMATES[r.climate]
	file_list.clear()
	for entry in r.entries:
		file_list.add_item(("▸ " if entry.directory else "  ")+entry.name)
		file_list.set_item_tooltip(file_list.item_count-1,entry.path)
	page_label.text = "%d–%d / %d" % [r.data.page*120+1 if r.data.total else 0,mini((r.data.page+1)*120,r.data.total),r.data.total]
	for child in breadcrumbs.get_children(): child.queue_free()
	var chain = []
	var path = current
	while regions.has(path):
		chain.push_front(path)
		path = regions[path].parent_key
	for i in chain.size():
		var target = chain[i]
		var name = target.get_file()
		if name.is_empty(): name = "/"
		var crumb = button(name, breadcrumbs, func(): navigate(target,"parent"))
		crumb.tooltip_text = target
		if i < chain.size()-1: label("›",breadcrumbs,14,Color("829b92"))

func select_file(index: int):
	if not regions.has(current) or index<0 or index>=regions[current].entries.size(): return
	selected = index
	file_list.select(index)
	file_list.ensure_current_is_visible()
	var entry = regions[current].entries[index]
	selection_ring.visible = true
	selection_ring.position = regions[current].points[index]+Vector3(0,0.16,0)
	selection_ring.scale = Vector3(2.7,1,1.8) if entry.kind=="pdf" else Vector3(1.8,1,1.8)
	selection_ring.rotation.y = float(posmod(hash(entry.path),628))/100 if entry.kind=="pdf" else 0
	detail_name.text = entry.name
	detail_meta.text = ("Directory" if entry.directory else entry.kind.capitalize()+" · "+entry.size) + (" · symbolic link" if entry.get("symlink",false) else "")
	preview_text.text = "Reading preview…"
	preview_image.visible = false
	preview_data = {}
	preview_generation += 1
	var generation = preview_generation
	var data = await api("/preview?path="+entry.path.uri_encode())
	if generation != preview_generation: return
	preview_data = data
	if data.has("error"):
		preview_text.text = data.error
		return
	preview_text.text = str(data.get("kind","Preview"))+"\n\n"+"\n".join(PackedStringArray(data.get("lines",[])))
	if data.has("image_png"):
		var image = Image.new()
		if image.load_png_from_buffer(Marshalls.base64_to_raw(data.image_png)) == OK:
			preview_image.texture = ImageTexture.create_from_image(image)
			preview_image.visible = true
	elif not data.get("pixels",[]).is_empty():
		var pixels = data.pixels
		var image = Image.create(pixels[0].size(),pixels.size(),false,Image.FORMAT_RGB8)
		for y in pixels.size():
			for x in pixels[y].size():
				var rgb = pixels[y][x]
				image.set_pixel(x,y,Color(rgb[0]/255.0,rgb[1]/255.0,rgb[2]/255.0))
		preview_image.texture = ImageTexture.create_from_image(image)
		preview_image.visible = true
	append_facts(entry.path)

func append_facts(path: String):
	var facts = metadata_cache.get(path,{})
	var parts = []
	if facts.get("pages") != null: parts.append(str(int(facts.pages))+" pages")
	if facts.has("width"): parts.append("%d × %d" % [facts.width,facts.get("height",0)])
	if facts.has("duration"): parts.append("%.1f seconds" % facts.duration)
	if facts.has("rows"): parts.append("%d rows × %d columns" % [facts.rows,facts.columns])
	if facts.has("captured"): parts.append("Captured "+facts.captured)
	if not parts.is_empty():
		var e = regions[current].entries[selected]
		detail_meta.text = e.kind.capitalize()+" · "+e.size+"\n"+" · ".join(PackedStringArray(parts))

func load_metadata(path: String, generation: int):
	var region = regions[path]
	for i in region.entries.size():
		if generation != request_generation: return
		var entry = region.entries[i]
		if entry.directory or entry.kind not in ["pdf","images","audio","video","tables"]: continue
		if not metadata_cache.has(entry.path):
			var facts = await api("/metadata?path="+entry.path.uri_encode())
			metadata_cache[entry.path] = facts if not facts.has("error") else {}
		if generation != request_generation or not is_instance_valid(region.visual): return
		region.features[i].queue_free()
		var feature = terrain.feature(entry,metadata_cache[entry.path],region.climate)
		feature.position = region.points[i]
		region.visual.add_child(feature)
		region.features[i] = feature
		if current==path and selected==i: append_facts(entry.path)

func _rebuild_labels():
	for item in labels:
		item.widget.queue_free()
	labels.clear()
	for key in region_transforms:
		var r = regions[key]
		var transform = region_transforms[key]
		var title = Label.new()
		title.text = key.get_file() if not key.get_file().is_empty() else "/"
		title.add_theme_font_override("font",SERIF)
		title.add_theme_font_size_override("font_size",22)
		title.add_theme_color_override("font_color",Color("e4e0ce"))
		title.add_theme_color_override("font_shadow_color",Color("19393a"))
		title.add_theme_constant_override("shadow_offset_x",2)
		title.add_theme_constant_override("shadow_offset_y",2)
		title.mouse_filter = Control.MOUSE_FILTER_IGNORE
		overlay.add_child(title)
		labels.append({"widget":title,"point":transform.pos+Vector3(0,0.1,-r.radius*0.78)*transform.scale,"scale":r.radius*transform.scale,"region":true,"key":key,"index":-1})
		for i in r.entries.size():
			var entry = r.entries[i]
			var item = Label.new()
			item.text = entry.name
			item.add_theme_font_override("font",ITALIC if not entry.directory else SERIF)
			item.add_theme_font_size_override("font_size",12 if not entry.directory else 14)
			item.add_theme_color_override("font_color",Color("fff3cc") if entry.directory else Color("e9e5cc"))
			item.add_theme_color_override("font_shadow_color",Color("142d2d"))
			item.add_theme_constant_override("shadow_offset_x",1)
			item.add_theme_constant_override("shadow_offset_y",1)
			item.mouse_filter = Control.MOUSE_FILTER_IGNORE
			overlay.add_child(item)
			labels.append({"widget":item,"point":transform.pos+(r.points[i]+Vector3(0,0.15,1.15))*transform.scale,"scale":transform.scale,"region":false,"directory":entry.directory,"key":key,"index":i})

func position_labels():
	var occupied = []
	var ordered = labels.duplicate()
	ordered.sort_custom(func(a,b):
		var ap = 0 if a.key==current and a.index==selected else 1 if a.region else 2
		var bp = 0 if b.key==current and b.index==selected else 1 if b.region else 2
		return ap < bp)
	for data in ordered:
		var widget = data.widget
		widget.visible = false
		widget.add_theme_color_override("font_color",Color("f3d391") if data.key==current and data.index==selected else Color("e4e0ce"))
		if label_mode == 2: continue
		if label_mode == 1 and not data.region and not data.get("directory",false): continue
		var projected = data.scale/camera.size*viewport.size.y
		if projected < (80 if data.region else 17): continue
		if camera.is_position_behind(data.point): continue
		var pos = camera.unproject_position(data.point)
		var font = widget.get_theme_font("font")
		var font_size = widget.get_theme_font_size("font_size")
		var width = minf(font.get_string_size(widget.text,HORIZONTAL_ALIGNMENT_LEFT,-1,font_size).x+4,190)
		widget.custom_minimum_size.x = 0
		widget.size = Vector2(width,widget.get_minimum_size().y)
		widget.clip_text = true
		widget.position = pos-Vector2(width/2,0)
		var rect = Rect2(widget.position,widget.size).grow(4)
		if not Rect2(Vector2.ZERO,Vector2(viewport.size)).encloses(rect): continue
		var collision = false
		for existing in occupied:
			if rect.intersects(existing): collision = true; break
		if collision: continue
		widget.visible = true
		occupied.append(rect)

func _process(delta):
	if not is_instance_valid(camera): return
	if not moving:
		camera_center = camera_center.lerp(desired_center,1-exp(-delta*9))
		camera.size = lerpf(camera.size,desired_size,1-exp(-delta*9))
	var direction = Vector3(sin(camera_angle)*32,40,cos(camera_angle)*32)
	camera.position = camera_center+direction
	camera.look_at(camera_center,Vector3.UP)
	position_labels()

func _input(event):
	if not is_instance_valid(camera) or moving: return
	if event is InputEventKey and event.pressed and event.ctrl_pressed and event.keycode==KEY_P:
		command_palette()
		get_viewport().set_input_as_handled()
		return
	if event is InputEventMouseButton:
		if event.button_index == MOUSE_BUTTON_MIDDLE: dragging = event.pressed
		if event.button_index == MOUSE_BUTTON_RIGHT and not event.pressed: dragging = false
		if not map_container.get_global_rect().has_point(event.position): return
		if event.button_index == MOUSE_BUTTON_RIGHT: dragging = event.pressed
		if event.pressed and event.button_index in [MOUSE_BUTTON_WHEEL_UP,MOUSE_BUTTON_WHEEL_DOWN]:
			desired_size = clampf(desired_size*(0.84 if event.button_index==MOUSE_BUTTON_WHEEL_UP else 1.19),1,800)
		if event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
			pick(event.position-map_container.global_position,event.double_click)
	elif event is InputEventMouseMotion and dragging:
		var factor = camera.size/viewport.size.y
		var right = camera.global_transform.basis.x
		var forward = Vector3(-right.z,0,right.x)
		desired_center += -right*event.relative.x*factor-forward*event.relative.y*factor*1.4

func _unhandled_key_input(event):
	if not event is InputEventKey or not event.pressed or event.echo: return
	if event.ctrl_pressed and event.keycode == KEY_P:
		command_palette()
		get_viewport().set_input_as_handled()
		return
	if screen_ui.get_viewport().gui_get_focus_owner() is LineEdit: return
	match event.keycode:
		KEY_SPACE: peek()
		KEY_ENTER: enter_selected()
		KEY_BACKSPACE: go_parent()
		KEY_L: cycle_labels()
		KEY_Z: overview()
		KEY_T: cycle_climate()
		KEY_F: toggle_map_focus()
		KEY_F5: navigate(current,"refresh")
		KEY_ESCAPE: desired_center = Vector3.ZERO
		KEY_LEFT: select_file(maxi(0,selected-1))
		KEY_RIGHT: select_file(mini(regions[current].entries.size()-1,selected+1))
		KEY_UP: select_file(maxi(0,selected-1))
		KEY_DOWN: select_file(mini(regions[current].entries.size()-1,selected+1))

func pick(point: Vector2, activate: bool):
	if not regions.has(current) or loading: return
	var best = -1
	var distance = 48.0
	for i in regions[current].points.size():
		var pos = camera.unproject_position(regions[current].points[i]+Vector3(0,0.3,0))
		var d = pos.distance_to(point)
		if d < distance: best=i; distance=d
	if best >= 0:
		select_file(best)
		if activate: enter_selected()

func selected_entry() -> Dictionary:
	if not regions.has(current) or selected<0 or selected>=regions[current].entries.size(): return {}
	return regions[current].entries[selected]

func enter_selected():
	var entry = selected_entry()
	if entry.is_empty(): return
	if entry.directory: navigate(entry.path,"child")
	else: peek()

func go_parent():
	if not regions.has(current): return
	var parent = regions[current].data.parent
	if parent == current: return
	navigate(parent,"parent",0,current)

func command_palette():
	if not is_instance_valid(command_window):
		command_window = AcceptDialog.new()
		command_window.title = "Bash navigation"
		command_window.theme = screen_ui.theme
		command_window.min_size = Vector2i(860,510)
		var content = VBoxContainer.new()
		content.custom_minimum_size = Vector2(830,440)
		command_window.add_child(content)
		label("Run Bash in the active directory. Output paths become destinations.",content,14)
		label("Examples: cd ../Photos    •    find . -iname '*.pdf'    •    rg --files | grep notes",content,13,Color("b8c5b5"))
		var row = HBoxContainer.new()
		content.add_child(row)
		command_field = LineEdit.new()
		command_field.placeholder_text = "Bash command (runs only when submitted)"
		command_field.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		command_field.text_submitted.connect(func(_text): run_command())
		row.add_child(command_field)
		button("Run",row,run_command)
		command_status = label("",content,13)
		command_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		command_results = ItemList.new()
		command_results.size_flags_vertical = Control.SIZE_EXPAND_FILL
		command_results.item_activated.connect(jump_result)
		content.add_child(command_results)
		button("Travel to selected destination",content,func():
			var selection = command_results.get_selected_items()
			if not selection.is_empty(): jump_result(selection[0]))
		screen_ui.add_child(command_window)
	command_status.text = "Working directory: "+current+". Commands run as your user; this is a real Bash shell."
	command_window.popup_centered()
	command_field.grab_focus()

func run_command():
	if command_running or command_field.text.strip_edges().is_empty(): return
	command_running = true
	command_status.text = "Running… (10 second limit)"
	command_results.clear()
	destinations.clear()
	var result = await api("/command",{"source":current,"command":command_field.text})
	command_running = false
	if result.has("error"):
		command_status.text = result.error
		return
	destinations = result.results
	for destination in destinations:
		command_results.add_item(("▸ " if destination.directory else "  ")+destination.path)
	command_status.text = "%d destinations. Select one and press Enter to travel." % destinations.size()
	if result.get("truncated",false): command_status.text += " Showing the first 200; narrow the command for more."
	if destinations.is_empty(): command_status.text="No existing file paths in the output. Try find, rg --files, printf, or cd."
	else:
		command_results.select(0)
		command_results.grab_focus()
		if destinations.size()==1: jump_result(0)

func jump_result(index: int):
	if index<0 or index>=destinations.size() or loading or moving: return
	var target = destinations[index]
	command_window.hide()
	await travel_to(target.path,target.directory)

func travel_to(path: String, directory: bool):
	filter_text = ""
	search_field.text = ""
	var folder = path if directory else path.get_base_dir()
	var common = current
	while folder != common and not folder.begins_with(common.trim_suffix("/")+"/"):
		var parent = common.get_base_dir()
		if parent==common or parent.is_empty(): common="/"; break
		common=parent
	var steps = 0
	while current!=common and steps<64:
		var before = current
		await navigate(regions[current].data.parent,"parent",0,current)
		if current==before: return
		steps+=1
	var relative = folder.trim_prefix(common).trim_prefix("/")
	for component in relative.split("/",false):
		var child_path = current.trim_suffix("/")+"/"+component
		var found = false
		for entry in regions[current].entries:
			if entry.path==child_path: found=true; break
		if not found: await navigate(current,"refresh",0,child_path)
		var before = current
		# Survey, pan over the parent geography, then descend into the chosen region.
		await fly_to(Vector3.ZERO,regions[current].radius*3.1)
		await navigate(child_path,"child")
		if current==before: return
	if not directory:
		var found = -1
		for i in regions[current].entries.size():
			if regions[current].entries[i].path==path: found=i
		if found<0:
			await navigate(current,"refresh",0,path)
			for i in regions[current].entries.size():
				if regions[current].entries[i].path==path: found=i
		if found>=0:
			select_file(found)
			var destination = regions[current].points[found]
			await fly_to(camera_center,regions[current].radius*3.1)
			await fly_to(destination,regions[current].radius*3.1)
			await fly_to(destination,5.5)
			desired_center=destination
			desired_size=5.5
			selection_ring.visible=true
	status.text="Arrived at "+path

func overview():
	if not regions.has(current) or moving: return
	var r = regions[current]
	if not r.parent_key.is_empty() and region_transforms.has(r.parent_key):
		var parent = regions[r.parent_key]
		var transform = region_transforms[r.parent_key]
		if desired_size < parent.radius*transform.scale:
			desired_center = transform.pos
			desired_size = parent.radius*transform.scale*2.6
			status.text = "Parent landscape. Backspace returns to it; Z returns to local detail."
			return
	desired_center = Vector3.ZERO
	desired_size = r.radius*2.6

func cycle_labels():
	label_mode = (label_mode+1)%3
	labels_button.text = ["Labels: all  L","Labels: directories  L","Labels: off  L"][label_mode]

func toggle_map_focus():
	map_focus = not map_focus
	left_panel.visible = not map_focus
	right_panel.visible = not map_focus
	map_container.offset_left = 0 if map_focus else 252
	map_container.offset_right = 0 if map_focus else -348
	status.text = "Map focus. F restores the file list and preview. Selected: "+selected_entry().get("name","") if map_focus else "File list and preview restored."

func cycle_climate():
	if not regions.has(current): return
	var r = regions[current]
	r.climate = (r.climate+1)%5
	r.visual.queue_free()
	build_region(r)
	apply_transforms()
	climate_button.text = "Climate: "+Terrain.CLIMATES[r.climate]
	status.text = "Climate changed for this region. File meanings and metadata remain unchanged."

func change_page(direction: int):
	if not regions.has(current): return
	var data = regions[current].data
	var page = clampi(data.page+direction,0,data.pages-1)
	if page != data.page: navigate(current,"refresh",page)

func choose_folder():
	var chooser = FileDialog.new()
	chooser.file_mode = FileDialog.FILE_MODE_OPEN_DIR
	chooser.access = FileDialog.ACCESS_FILESYSTEM
	chooser.current_dir = current
	chooser.dir_selected.connect(func(path):
		navigate(path,"root")
		chooser.queue_free())
	chooser.canceled.connect(chooser.queue_free)
	screen_ui.add_child(chooser)
	chooser.popup_centered_ratio(0.7)

func peek():
	var entry = selected_entry()
	if entry.is_empty(): return
	var popup = AcceptDialog.new()
	popup.title = entry.name
	popup.min_size = Vector2i(740,550)
	popup.theme = screen_ui.theme
	var content = VBoxContainer.new()
	content.custom_minimum_size = Vector2(720,490)
	popup.add_child(content)
	if preview_image.visible:
		var image = TextureRect.new()
		image.texture = preview_image.texture
		image.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		image.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
		image.custom_minimum_size = Vector2(700,370)
		content.add_child(image)
	var text = RichTextLabel.new()
	text.bbcode_enabled = false
	text.text = preview_text.text
	text.selection_enabled = true
	text.size_flags_vertical = Control.SIZE_EXPAND_FILL
	content.add_child(text)
	popup.confirmed.connect(popup.queue_free)
	popup.canceled.connect(popup.queue_free)
	screen_ui.add_child(popup)
	popup.popup_centered()

func ask_action(action: String):
	var entry = selected_entry()
	if action != "mkdir" and entry.is_empty(): return
	pending_action = action
	dialog_source = current if action=="mkdir" else entry.path
	dialog.title = {"rename":"Rename file or folder","mkdir":"Create folder","trash":"Move to Branch trash?"}[action]
	dialog.dialog_text = "Recoverable with Undo move. Stored in Branch's own trash." if action=="trash" else ""
	dialog_field.visible = action != "trash"
	dialog_field.text = entry.name if action=="rename" else ""
	dialog.popup_centered()
	if dialog_field.visible:
		dialog_field.grab_focus()
		dialog_field.select_all()

func confirm_action():
	var data = {"action":pending_action,"source":dialog_source}
	if pending_action in ["rename","mkdir"]: data.name = dialog_field.text
	var result = await api("/operation",data)
	if result.has("error"): status.text = result.error
	else:
		await navigate(current,"refresh",regions[current].data.page)
		status.text = "Operation complete. Moves, renames and trash can be undone this session."

func operate(action: String):
	var entry = selected_entry()
	if action != "undo" and entry.is_empty(): return
	var result = await api("/operation",{"action":action,"source":entry.get("path",current)})
	if result.has("error"): status.text = result.error
	elif action != "open": navigate(current,"refresh",regions[current].data.page)
	else: status.text = "Requested external open: "+entry.name

func set_clipboard(action: String):
	var entry = selected_entry()
	if entry.is_empty(): return
	clipboard = {"action":action,"source":entry.path}
	status.text = action.capitalize()+" ready: "+entry.name+". Navigate to the destination and use Paste here."

func paste():
	if clipboard.is_empty(): status.text="Copy or cut a file first."; return
	var data = clipboard.duplicate()
	data.destination = current
	status.text = "Transferring files…"
	var result = await api("/operation",data)
	if result.has("error"): status.text = result.error
	else:
		if clipboard.action == "move": clipboard.clear()
		navigate(current,"refresh",regions[current].data.page)

func show_help():
	var popup = AcceptDialog.new()
	popup.title = "Reading the atlas"
	popup.dialog_text = "Folders are regions within their parent's landscape.\nDouble-click a folder to approach it; Backspace pulls back.\nRight / middle drag pans. Wheel zooms. Z shows the parent landscape.\n\nPDFs: mountains, height from page count (bounded logarithmic scale).\nImages: vegetation; dimensions affect growth and orientation affects shape.\nAudio: lakes, area from duration; channels add ripples.\nVideo: waterfalls, height from duration and width from resolution.\nCSV / TSV: fields; rows affect area and columns form furrows.\nCode: settlements. Archives: vaults. Other documents: meadows.\n\nL cycles all / directory-only / no cartographic labels.\nClimates and coastlines are illustrative; T cycles a region's climate.\nImage capture-month accents are not a claim about local seasons.\nRivers trace directory branches; leaf regions have decorative coastal streams.\n\nThe file list and inspector always show real names.\nUnknown metadata uses neutral terrain, never an invented number.\nUp to 120 files are mapped per page; filter and page controls cover larger folders.\nCopy and new-folder actions are not undoable. Trash is recoverable."
	screen_ui.add_child(popup)
	popup.confirmed.connect(popup.queue_free)
	popup.canceled.connect(popup.queue_free)
	popup.popup_centered(Vector2i(750,590))

func _smoke_test():
	await get_tree().create_timer(2.0).timeout
	var original = current
	var entries = regions[current].entries
	var child = -1
	for i in entries.size():
		if entries[i].directory: child=i; break
	if child>=0:
		select_file(child)
		await navigate(entries[child].path,"child")
		assert(current != original, "Directory descent failed")
		assert(regions.has(original), "Parent geography was discarded")
		# Cross from one nested region to a sibling through their common parent.
		for sibling in entries:
			if sibling.directory and sibling.path!=current:
				var survey = await api("/list?path="+sibling.path.uri_encode())
				if not survey.has("error"):
					for candidate in survey.entries:
						if not candidate.directory:
							await travel_to(candidate.path,false)
							assert(selected_entry().path==candidate.path,"Cross-directory travel failed")
							break
				break
		await navigate(original,"parent")
		assert(current==original, "Return to parent failed")
	cycle_labels()
	assert(label_mode==1)
	cycle_labels()
	assert(label_mode==2)
	cycle_labels()
	cycle_climate()
	var command_result = await api("/command",{"source":current,"command":"find . -maxdepth 1 -type f -print0"})
	assert(not command_result.has("error"),"Bash command failed")
	if not command_result.results.is_empty():
		var destination = command_result.results[0]
		await travel_to(destination.path,false)
		assert(selected_entry().path==destination.path,"Bash camera destination mismatch")
	var r = regions[current]
	r.climate=0
	r.visual.queue_free()
	build_region(r)
	apply_transforms()
	climate_button.text="Climate: Temperate"
	desired_center=Vector3.ZERO
	desired_size=r.radius*2.6
	for i in r.entries.size():
		if r.entries[i].kind=="pdf":
			await select_file(i)
			break
	await get_tree().create_timer(2.0).timeout
	if not capture_path.is_empty() and DisplayServer.get_name()!="headless":
		await RenderingServer.frame_post_draw
		get_viewport().get_texture().get_image().save_png(capture_path)
		assert(labels.filter(func(item): return item.widget.visible and item.widget.size.x>5).size()>0,"Labels did not render")
	print("BRANCH_SMOKE_OK regions=",regions.size()," labels=",labels.size()," files=",regions[current].entries.size())
	get_tree().quit()
