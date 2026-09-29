extends Node3D
# The map is the application. Every other surface is a translucent overlay that
# appears when relevant and fades when the mouse is idle. See docs/cartography.md.

const Terrain = preload("res://terrain.gd")
const SERIF = preload("res://fonts/Cartography.ttf")
const ITALIC = preload("res://fonts/CartographyItalic.ttf")
const UI_FONT = preload("res://fonts/Interface.ttf")
const INK = Color("e6e1cc")
const MUTED = Color("a9b8ae")
const GOLD = Color("d9c68f")
const PAGE = 120
const ROOT_MOUTH = PI*0.5 + 0.35
const KIND_NAMES = {"pdf":"PDF", "images":"Image", "audio":"Audio", "video":"Video", "tables":"Table", "code":"Source",
	"archives":"Archive", "binaries":"Executable", "disks":"Disk image", "databases":"Database", "documents":"Document", "other":"File"}

var terrain = Terrain.new()
var api_url = OS.get_environment("BRANCH_API")
var token = OS.get_environment("BRANCH_TOKEN")
var regions = {}
var current = ""
var selected = -1
var metadata_cache = {}
var climate_override = {}
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
var drag_moved = 0.0
var camera_tween: Tween
var smoke = false
var capture_path = ""
var weather_visible = true
var chrome_hidden = false
var idle_time = 0.0
var clock = 0.0
var lightning_timer = 2.0
var toast_timer = 0.0

var map_container: SubViewportContainer
var viewport: SubViewport
var world: Node3D
var camera: Camera3D
var selection_ring: MeshInstance3D
var overlay: Control
var screen_ui: Control
var ui_theme: Theme
var fading = {}
# Cartouche
var cartouche: PanelContainer
var region_title: Label
var crumbs: HBoxContainer
var path_field: LineEdit
var region_info: Label
# Tools
var tools: PanelContainer
var zoom_box: PanelContainer
var weather_button: Button
var labels_button: Button
# Gazetteer
var gazetteer: PanelContainer
var file_list: ItemList
var search_field: LineEdit
var page_label: Label
var debounce: Timer
# Field notes
var notes: PanelContainer
var notes_body: VBoxContainer
var notes_toggle: Button
var detail_name: Label
var detail_meta: Label
var detail_reading: Label
var preview_text: RichTextLabel
var preview_image: TextureRect
var preview_data = {}
# Transient surfaces
var legend: PanelContainer
var legend_text: RichTextLabel
var toast: PanelContainer
var toast_label: Label
var toast_undo: Button
var carry: PanelContainer
var carry_label: Label
var hover_label: Label
var dialog: ConfirmationDialog
var dialog_field: LineEdit
var pending_action = ""
var dialog_source = ""
var command_window: AcceptDialog
var command_field: LineEdit
var command_results: ItemList
var command_status: Label
var destinations = []
var command_running = false

func _ready():
	var args = OS.get_cmdline_user_args()
	smoke = "--smoke" in args
	if smoke:
		get_tree().create_timer(90).timeout.connect(func():
			push_error("Native smoke test exceeded 90 seconds")
			get_tree().quit(2))
	var capture_index = args.find("--capture")
	if capture_index >= 0 and capture_index+1 < args.size(): capture_path = args[capture_index+1]
	_build_ui()
	_build_world()
	if api_url.is_empty():
		notify("Start this application with the atlas launcher so it can access files.", false, 60)
		return
	await navigate(OS.get_environment("BRANCH_ROOT"), "root")
	if smoke: _smoke_test()

# ---------------------------------------------------------------- interface

func glass(alpha: float = 0.72, radius: int = 8, margin: int = 12) -> StyleBoxFlat:
	var style = StyleBoxFlat.new()
	style.bg_color = Color(0.05, 0.11, 0.12, alpha)
	style.border_color = Color(0.85, 0.78, 0.56, 0.16)
	style.set_border_width_all(1)
	style.set_corner_radius_all(radius)
	style.set_content_margin_all(margin)
	return style

func flat(bg: Color, border: Color = Color(0, 0, 0, 0)) -> StyleBoxFlat:
	var style = StyleBoxFlat.new()
	style.bg_color = bg
	style.border_color = border
	style.set_border_width_all(1)
	style.set_corner_radius_all(5)
	style.content_margin_left = 8
	style.content_margin_right = 8
	style.content_margin_top = 4
	style.content_margin_bottom = 4
	return style

func label(text: String, parent: Node, font_size: int = 13, color: Color = INK, font: Font = null) -> Label:
	var item = Label.new()
	item.text = text
	item.add_theme_font_size_override("font_size", font_size)
	item.add_theme_color_override("font_color", color)
	if font: item.add_theme_font_override("font", font)
	parent.add_child(item)
	return item

func button(text: String, parent: Node, callback: Callable, tip: String = "") -> Button:
	var item = Button.new()
	item.text = text
	item.tooltip_text = tip
	item.focus_mode = Control.FOCUS_NONE
	item.pressed.connect(callback)
	parent.add_child(item)
	return item

func panel(parent: Node, alpha: float, faded: float) -> PanelContainer:
	var item = PanelContainer.new()
	item.add_theme_stylebox_override("panel", glass(alpha))
	parent.add_child(item)
	fading[item] = faded
	return item

func _build_ui():
	var canvas = CanvasLayer.new()
	add_child(canvas)
	screen_ui = Control.new()
	screen_ui.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	screen_ui.mouse_filter = Control.MOUSE_FILTER_IGNORE
	canvas.add_child(screen_ui)
	ui_theme = Theme.new()
	ui_theme.default_font = UI_FONT
	ui_theme.default_font_size = 13
	ui_theme.set_stylebox("normal", "Button", flat(Color(1, 1, 1, 0.0)))
	ui_theme.set_stylebox("hover", "Button", flat(Color(1, 1, 1, 0.08), Color(0.85, 0.78, 0.56, 0.35)))
	ui_theme.set_stylebox("pressed", "Button", flat(Color(1, 1, 1, 0.15), Color(0.85, 0.78, 0.56, 0.5)))
	ui_theme.set_stylebox("focus", "Button", StyleBoxEmpty.new())
	ui_theme.set_stylebox("disabled", "Button", flat(Color(0, 0, 0, 0)))
	ui_theme.set_color("font_color", "Button", INK)
	ui_theme.set_color("font_hover_color", "Button", Color("fff3cc"))
	ui_theme.set_stylebox("normal", "LineEdit", flat(Color(0, 0, 0, 0.28), Color(0.85, 0.78, 0.56, 0.2)))
	ui_theme.set_stylebox("focus", "LineEdit", flat(Color(0, 0, 0, 0.0), Color(0.85, 0.78, 0.56, 0.6)))
	ui_theme.set_color("font_color", "LineEdit", INK)
	ui_theme.set_stylebox("panel", "ItemList", StyleBoxEmpty.new())
	ui_theme.set_stylebox("focus", "ItemList", StyleBoxEmpty.new())
	ui_theme.set_stylebox("selected", "ItemList", flat(Color(0.85, 0.78, 0.56, 0.22)))
	ui_theme.set_stylebox("selected_focus", "ItemList", flat(Color(0.85, 0.78, 0.56, 0.3)))
	ui_theme.set_stylebox("hovered", "ItemList", flat(Color(1, 1, 1, 0.06)))
	ui_theme.set_color("font_color", "ItemList", Color("d5d8c8"))
	ui_theme.set_stylebox("embedded_border", "Window", glass(0.94, 8, 10))
	ui_theme.set_stylebox("panel", "AcceptDialog", StyleBoxEmpty.new())
	ui_theme.set_stylebox("panel", "ConfirmationDialog", StyleBoxEmpty.new())
	ui_theme.set_stylebox("panel", "TooltipPanel", glass(0.9, 5, 6))
	ui_theme.set_color("title_color", "Window", GOLD)
	screen_ui.theme = ui_theme

	map_container = SubViewportContainer.new()
	map_container.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	map_container.stretch = true
	map_container.mouse_filter = Control.MOUSE_FILTER_IGNORE
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

	# Cartouche: where you are, what the climate and weather say about it.
	cartouche = panel(screen_ui, 0.66, 0.55)
	cartouche.position = Vector2(16, 16)
	var carto = VBoxContainer.new()
	carto.add_theme_constant_override("separation", 2)
	cartouche.add_child(carto)
	var title_row = HBoxContainer.new()
	carto.add_child(title_row)
	button("↑", title_row, go_parent, "Up to the parent region (Backspace)")
	region_title = label("Branch Atlas", title_row, 24, GOLD, SERIF)
	crumbs = HBoxContainer.new()
	crumbs.add_theme_constant_override("separation", 0)
	carto.add_child(crumbs)
	path_field = LineEdit.new()
	path_field.visible = false
	path_field.custom_minimum_size = Vector2(460, 0)
	path_field.placeholder_text = "Linux path or Windows path (E:\\Photos)"
	path_field.text_submitted.connect(func(value):
		hide_path_field()
		navigate(normalize_path(value), "root"))
	carto.add_child(path_field)
	region_info = label("", carto, 12, MUTED)

	# Tools: top right, faint until the mouse moves.
	tools = panel(screen_ui, 0.58, 0.22)
	tools.add_theme_stylebox_override("panel", glass(0.58, 8, 4))
	var tool_row = HBoxContainer.new()
	tool_row.add_theme_constant_override("separation", 2)
	tools.add_child(tool_row)
	button("☰  Gazetteer", tool_row, toggle_gazetteer, "File list, filter and pages (G)")
	button("›_  Bash", tool_row, command_palette, "Bash navigation palette (Ctrl+P)")
	weather_button = button("☁  Weather", tool_row, toggle_weather, "Show or hide the weather layer (W)")
	labels_button = button("Aa  All", tool_row, cycle_labels, "Map labels: all / directories / off (L)")
	button("?  Legend", tool_row, toggle_legend, "How to read the map (K)")
	tools.set_anchors_and_offsets_preset(Control.PRESET_TOP_RIGHT, Control.PRESET_MODE_MINSIZE, 16)
	tools.grow_horizontal = Control.GROW_DIRECTION_BEGIN

	zoom_box = panel(screen_ui, 0.58, 0.18)
	zoom_box.add_theme_stylebox_override("panel", glass(0.58, 8, 4))
	var zoom_col = VBoxContainer.new()
	zoom_col.add_theme_constant_override("separation", 0)
	zoom_box.add_child(zoom_col)
	button("+", zoom_col, func(): desired_size = maxf(1, desired_size/1.3), "Zoom in")
	button("−", zoom_col, func(): desired_size = minf(800, desired_size*1.3), "Zoom out")
	button("⤢", zoom_col, overview, "Parent overview / local view (Z)")
	button("⌂", zoom_col, choose_folder, "Open another folder")
	zoom_box.set_anchors_and_offsets_preset(Control.PRESET_BOTTOM_RIGHT, Control.PRESET_MODE_MINSIZE, 16)
	zoom_box.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	zoom_box.grow_vertical = Control.GROW_DIRECTION_BEGIN

	# Gazetteer: the conventional list, hidden until asked for.
	gazetteer = panel(screen_ui, 0.8, 0.8)
	gazetteer.set_anchors_preset(Control.PRESET_LEFT_WIDE)
	gazetteer.offset_left = 16
	gazetteer.offset_right = 316
	gazetteer.offset_top = 150
	gazetteer.offset_bottom = -16
	gazetteer.visible = false
	var files = VBoxContainer.new()
	files.add_theme_constant_override("separation", 8)
	gazetteer.add_child(files)
	var gaz_head = HBoxContainer.new()
	files.add_child(gaz_head)
	var gaz_title = label("Gazetteer", gaz_head, 18, GOLD, SERIF)
	gaz_title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	button("✕", gaz_head, toggle_gazetteer, "Close (G)")
	search_field = LineEdit.new()
	search_field.placeholder_text = "Filter this region…"
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
	hidden.focus_mode = Control.FOCUS_NONE
	hidden.toggled.connect(func(value):
		hidden_files = value
		navigate(current, "refresh"))
	files.add_child(hidden)
	file_list = ItemList.new()
	file_list.size_flags_vertical = Control.SIZE_EXPAND_FILL
	file_list.item_selected.connect(func(index):
		select_file(index)
		focus_selected())
	file_list.item_activated.connect(func(index):
		select_file(index)
		enter_selected())
	files.add_child(file_list)
	var pages = HBoxContainer.new()
	files.add_child(pages)
	button("‹", pages, func(): change_page(-1))
	page_label = label("", pages, 12, MUTED)
	page_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	page_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	button("›", pages, func(): change_page(1))
	var gaz_actions = HBoxContainer.new()
	files.add_child(gaz_actions)
	button("New folder", gaz_actions, func(): ask_action("mkdir"), "Ctrl+Shift+N")
	button("Refresh", gaz_actions, refresh, "F5")

	# Field notes: only while something is selected.
	notes = panel(screen_ui, 0.8, 0.82)
	notes.set_anchors_preset(Control.PRESET_TOP_RIGHT)
	notes.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	notes.offset_right = -16
	notes.offset_left = -386
	notes.offset_top = 68
	notes.visible = false
	var notes_col = VBoxContainer.new()
	notes_col.add_theme_constant_override("separation", 6)
	notes.add_child(notes_col)
	var notes_head = HBoxContainer.new()
	notes_col.add_child(notes_head)
	detail_name = label("", notes_head, 18, INK, SERIF)
	detail_name.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	detail_name.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	detail_name.custom_minimum_size = Vector2(290, 0)
	notes_toggle = button("▾", notes_head, toggle_notes_body, "Collapse / expand (I)")
	button("✕", notes_head, deselect, "Close (Esc)")
	detail_meta = label("", notes_col, 12, MUTED)
	detail_meta.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	detail_reading = label("", notes_col, 13, GOLD, ITALIC)
	detail_reading.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	notes_body = VBoxContainer.new()
	notes_body.add_theme_constant_override("separation", 6)
	notes_col.add_child(notes_body)
	preview_image = TextureRect.new()
	preview_image.custom_minimum_size = Vector2(0, 170)
	preview_image.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	preview_image.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	preview_image.visible = false
	notes_body.add_child(preview_image)
	preview_text = RichTextLabel.new()
	preview_text.bbcode_enabled = false
	preview_text.custom_minimum_size = Vector2(0, 190)
	preview_text.selection_enabled = true
	preview_text.add_theme_font_size_override("normal_font_size", 12)
	preview_text.add_theme_color_override("default_color", Color("cfd3c4"))
	notes_body.add_child(preview_text)
	var actions = HFlowContainer.new()
	notes_body.add_child(actions)
	button("Peek", actions, peek, "Space")
	button("Open", actions, func(): operate("open"), "Open with the default application")
	button("Rename", actions, func(): ask_action("rename"), "F2")
	button("Copy", actions, func(): set_clipboard("copy"), "Ctrl+C")
	button("Cut", actions, func(): set_clipboard("move"), "Ctrl+X")
	button("Trash", actions, func(): ask_action("trash"), "Delete")

	# Legend.
	legend = panel(screen_ui, 0.92, 0.92)
	legend.custom_minimum_size = Vector2(720, 580)
	legend.set_anchors_and_offsets_preset(Control.PRESET_CENTER, Control.PRESET_MODE_MINSIZE)
	legend.grow_horizontal = Control.GROW_DIRECTION_BOTH
	legend.grow_vertical = Control.GROW_DIRECTION_BOTH
	legend.visible = false
	var legend_col = VBoxContainer.new()
	legend.add_child(legend_col)
	var legend_head = HBoxContainer.new()
	legend_col.add_child(legend_head)
	label("Reading the map", legend_head, 22, GOLD, SERIF).size_flags_horizontal = Control.SIZE_EXPAND_FILL
	button("✕", legend_head, toggle_legend, "Close (K / Esc)")
	legend_text = RichTextLabel.new()
	legend_text.bbcode_enabled = true
	legend_text.size_flags_vertical = Control.SIZE_EXPAND_FILL
	legend_text.add_theme_font_size_override("normal_font_size", 13)
	legend_text.add_theme_font_size_override("bold_font_size", 13)
	legend_text.add_theme_color_override("default_color", Color("d8dac9"))
	legend_col.add_child(legend_text)

	# Toast, carried items, hover name.
	toast = panel(screen_ui, 0.8, 0.8)
	toast.add_theme_stylebox_override("panel", glass(0.8, 8, 8))
	toast.visible = false
	fading.erase(toast)
	var toast_row = HBoxContainer.new()
	toast.add_child(toast_row)
	toast_label = label("", toast_row, 13, INK)
	toast_undo = button("Undo", toast_row, func():
		toast_undo.visible = false
		operate("undo"), "Ctrl+Z")
	toast_undo.visible = false
	toast.set_anchors_and_offsets_preset(Control.PRESET_CENTER_BOTTOM, Control.PRESET_MODE_MINSIZE, 20)
	toast.grow_horizontal = Control.GROW_DIRECTION_BOTH
	toast.grow_vertical = Control.GROW_DIRECTION_BEGIN

	carry = panel(screen_ui, 0.82, 0.82)
	carry.add_theme_stylebox_override("panel", glass(0.82, 8, 6))
	carry.visible = false
	var carry_row = HBoxContainer.new()
	carry.add_child(carry_row)
	carry_label = label("", carry_row, 13, INK)
	button("Paste here", carry_row, paste, "Ctrl+V")
	button("✕", carry_row, func():
		clipboard.clear()
		carry.visible = false, "Put it down")
	carry.set_anchors_and_offsets_preset(Control.PRESET_BOTTOM_LEFT, Control.PRESET_MODE_MINSIZE, 16)
	carry.grow_vertical = Control.GROW_DIRECTION_BEGIN

	hover_label = label("", screen_ui, 13, Color("fff3cc"), ITALIC)
	hover_label.add_theme_color_override("font_shadow_color", Color(0.05, 0.1, 0.1, 0.9))
	hover_label.add_theme_constant_override("shadow_offset_x", 1)
	hover_label.add_theme_constant_override("shadow_offset_y", 1)
	hover_label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	hover_label.visible = false

	dialog = ConfirmationDialog.new()
	dialog.min_size = Vector2i(480, 150)
	dialog.theme = ui_theme
	screen_ui.add_child(dialog)
	dialog_field = LineEdit.new()
	dialog_field.custom_minimum_size = Vector2(440, 36)
	dialog.add_child(dialog_field)
	dialog.confirmed.connect(confirm_action)
	get_window().min_size = Vector2i(900, 600)

func _build_world():
	world = Node3D.new()
	viewport.add_child(world)
	var environment = WorldEnvironment.new()
	var config = Environment.new()
	config.background_mode = Environment.BG_COLOR
	config.background_color = Color("173943")
	config.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	config.ambient_light_color = Color("c4d8dc")
	config.ambient_light_energy = 0.38
	config.tonemap_mode = Environment.TONE_MAPPER_LINEAR
	environment.environment = config
	world.add_child(environment)
	var sun = DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-53, -28, 0)
	sun.light_color = Color("ffedca")
	sun.light_energy = 0.52
	sun.shadow_enabled = true
	sun.directional_shadow_max_distance = 80
	world.add_child(sun)
	var water_mesh = PlaneMesh.new()
	water_mesh.size = Vector2(4000, 4000)
	terrain.shape(world, water_mesh, Vector3(0, -0.31, 0), Color("285a68"))
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
	selection_ring = terrain.shape(world, ring, Vector3(0, 0.16, 0), Color("ffe19b"), 1.0, 0.6)
	selection_ring.visible = false

func notify(text: String, undo: bool = false, seconds: float = 5.0):
	toast_label.text = text
	toast_undo.visible = undo
	toast.visible = true
	toast.modulate.a = 1.0
	toast.reset_size()
	toast_timer = seconds if not undo else maxf(seconds, 10.0)

# ---------------------------------------------------------------- data

func api(endpoint: String, data: Variant = null) -> Dictionary:
	var request = HTTPRequest.new()
	request.timeout = 0 if data is Dictionary and data.get("action", "") in ["copy", "move"] else 12
	add_child(request)
	var headers = PackedStringArray(["Authorization: Bearer "+token, "Content-Type: application/json"])
	var err = request.request(api_url+endpoint, headers, HTTPClient.METHOD_GET if data == null else HTTPClient.METHOD_POST, "" if data == null else JSON.stringify(data))
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
	value = value.strip_edges()
	if value.length() > 1 and value[1] == ":":
		return "/mnt/"+value[0].to_lower()+"/"+value.substr(2).replace("\\", "/").trim_prefix("/")
	return value

func climate_for(path: String, data: Dictionary) -> int:
	if climate_override.has(path): return climate_override[path]
	var fs = data.get("filesystem", {})
	if not fs.get("writable", true): return 1
	return Terrain.ZONE_CLIMATE.get(fs.get("zone", "native"), 0)

func navigate(path: String, direction: String, page: int = 0, focus: String = ""):
	if loading or moving or path.is_empty(): return
	loading = true
	region_info.text = "Surveying " + path + "…"
	var query = "/list?path="+path.uri_encode()+"&hidden="+str(hidden_files)+"&page="+str(page)
	if not focus.is_empty(): query += "&focus="+focus.uri_encode()
	if direction == "refresh": query += "&filter="+filter_text.uri_encode()
	var data = await api(query)
	if data.has("error"):
		notify(data.error)
		loading = false
		update_cartouche()
		return
	path = data.path
	# Directory sizes and activity shape the rivers, so survey before carving land.
	var subdirs = []
	for entry in data.entries:
		if entry.directory: subdirs.append(entry.path)
	if not subdirs.is_empty():
		var survey = await api("/survey", {"paths":subdirs})
		for key in survey.get("facts", {}):
			metadata_cache[key] = survey.facts[key]
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
	var mouth = ROOT_MOUTH
	if direction == "child" and regions.has(previous):
		var parent = regions[previous]
		var index = -1
		for i in parent.entries.size():
			if parent.entries[i].path == path: index = i
		if index >= 0:
			local_pos = parent.points[index]+Vector3(0, 0.085, 0)
			factor = 1.4/terrain.radius(data.entries)
			parent_key = previous
			mouth = parent.outlets.get(index, ROOT_MOUTH)
	elif regions.has(path):
		local_pos = regions[path].local_pos
		factor = regions[path].factor
		parent_key = regions[path].parent_key
		mouth = regions[path].mouth
	var region = {"path":path, "data":data, "entries":data.entries, "points":terrain.positions(data.entries),
		"radius":terrain.radius(data.entries), "climate":climate_for(path, data), "parent_key":parent_key,
		"local_pos":local_pos, "factor":factor, "mouth":mouth, "features":[]}
	if regions.has(path): regions[path].visual.queue_free()
	regions[path] = region
	build_region(region)
	if direction == "parent" and regions.has(previous) and regions[previous].parent_key.is_empty():
		for i in region.entries.size():
			if region.entries[i].path == previous:
				var child = regions[previous]
				child.parent_key = path
				child.local_pos = region.points[i]+Vector3(0, 0.085, 0)
				child.factor = 1.4/child.radius
				# The child's outlet now faces the way its tributary flows here.
				child.mouth = region.outlets.get(i, child.mouth)
				child.visual.queue_free()
				build_region(child)
	if direction == "child" and not previous.is_empty():
		# Add the child's miniature landscape before moving the camera toward it.
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
	selection_ring.visible = false
	update_listing()
	loading = false
	if focus.is_empty() or direction != "parent":
		notes.visible = false
	else:
		for i in region.entries.size():
			if region.entries[i].path == focus: select_file(i)
	load_metadata(path, request_generation)

func build_region(region: Dictionary):
	var visual = Node3D.new()
	world.add_child(visual)
	region.visual = visual
	var seed_value = hash(region.path)
	region.channels = terrain.hydrology(region.entries, region.points, metadata_cache, seed_value, region.mouth)
	region.outlets = {}
	for channel in region.channels:
		if channel.index >= 0 and channel.has("angle"): region.outlets[channel.index] = channel.angle
	region.stats = terrain.weather_stats(region.entries, metadata_cache)
	visual.add_child(terrain.land(region.entries, region.points, region.climate, seed_value, region.channels, region.stats.snow))
	region.features = []
	for i in region.entries.size():
		var entry = region.entries[i]
		var feature = terrain.feature(entry, metadata_cache.get(entry.path, {}), region.climate)
		feature.position = region.points[i]
		visual.add_child(feature)
		region.features.append(feature)
	region.weather = terrain.weather_layer(region.entries, region.stats, seed_value)
	visual.add_child(region.weather)

func apply_transforms():
	if not regions.has(current): return
	region_transforms = {current:{"pos":Vector3.ZERO, "scale":1.0}}
	var path = current
	# Keep two ancestor levels around the active landscape. Deeper levels still
	# exist in the model and reappear when zooming back out.
	for depth in 2:
		var r = regions[path]
		if r.parent_key.is_empty() or not regions.has(r.parent_key): break
		var transform = region_transforms[path]
		var parent_scale = transform.scale/r.factor
		region_transforms[r.parent_key] = {"pos":transform.pos-r.local_pos*parent_scale, "scale":parent_scale}
		path = r.parent_key
	for depth in 4:
		for key in regions:
			var r = regions[key]
			if region_transforms.has(key) or not region_transforms.has(r.parent_key): continue
			var parent = region_transforms[r.parent_key]
			if not regions[r.parent_key].entries.any(func(entry): return entry.path == key): continue
			var scale_value = parent.scale*r.factor
			if scale_value >= 0.002:
				region_transforms[key] = {"pos":parent.pos+r.local_pos*parent.scale, "scale":scale_value}
	for key in regions:
		var r = regions[key]
		r.visual.visible = region_transforms.has(key)
		if not r.visual.visible: continue
		var transform = region_transforms[key]
		r.visual.position = transform.pos
		r.visual.scale = Vector3.ONE*transform.scale
		# Weather belongs to where you stand; distant skies would hang over the view.
		r.weather.visible = weather_visible and key == current
		for i in r.entries.size():
			# Explored directories reveal real miniature landscapes; their fog lifts.
			var child_path = r.entries[i].path
			r.features[i].visible = not (r.entries[i].directory and regions.has(child_path) and regions[child_path].parent_key == key)
	_rebuild_labels()

func fly_to(point: Vector3, zoom: float):
	moving = true
	selection_ring.visible = false
	if camera_tween: camera_tween.kill()
	camera_tween = create_tween().set_parallel(true)
	camera_tween.set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_IN_OUT)
	camera_tween.tween_property(self, "camera_center", point, 0.65)
	camera_tween.tween_property(camera, "size", zoom, 0.65)
	await camera_tween.finished
	moving = false

func update_cartouche():
	if not regions.has(current): return
	var r = regions[current]
	region_title.text = current.get_file() if not current.get_file().is_empty() else "/"
	var fs = r.data.get("filesystem", {})
	var climate_text = Terrain.CLIMATES[r.climate] + " · "
	if climate_override.has(current): climate_text += "override (T)"
	else: climate_text += "%s (%s)" % [Terrain.CLIMATE_REASON[r.climate], fs.get("type", "?")]
	var text = climate_text + "\n" + Terrain.forecast(r.stats)
	if r.data.pages > 1:
		text += "\nShowing %d–%d of %d entries · Gazetteer (G) pages the rest" % [r.data.page*PAGE+1, mini((r.data.page+1)*PAGE, r.data.total), r.data.total]
	region_info.text = text
	for child in crumbs.get_children(): child.queue_free()
	var parts = current.split("/", false)
	var target = ""
	var start = maxi(0, parts.size()-6)
	if start > 0: label("…", crumbs, 12, MUTED)
	var root_crumb = button("/", crumbs, func(): travel_to("/", true), "/")
	root_crumb.add_theme_font_size_override("font_size", 12)
	for i in parts.size():
		target += "/" + parts[i]
		if i < start: continue
		var destination = target
		label("›", crumbs, 12, Color("7f978d"))
		var crumb = button(parts[i], crumbs, func(): travel_to(destination, true), destination)
		crumb.add_theme_font_size_override("font_size", 12)
	button("✎", crumbs, show_path_field, "Type a path (Ctrl+L)").add_theme_font_size_override("font_size", 12)
	cartouche.reset_size()

func update_listing():
	var r = regions[current]
	update_cartouche()
	file_list.clear()
	for entry in r.entries:
		file_list.add_item(("▸ " if entry.directory else "   ")+entry.name)
		file_list.set_item_tooltip(file_list.item_count-1, entry.path)
	page_label.text = "%d–%d of %d" % [r.data.page*PAGE+1 if r.data.total else 0, mini((r.data.page+1)*PAGE, r.data.total), r.data.total]
	if legend.visible: fill_legend()

func show_path_field():
	path_field.text = current
	path_field.visible = true
	crumbs.visible = false
	path_field.grab_focus()
	path_field.select_all()

func hide_path_field():
	path_field.visible = false
	crumbs.visible = true
	path_field.release_focus()
	cartouche.reset_size()

# ---------------------------------------------------------------- selection

func select_file(index: int):
	if not regions.has(current) or index < 0 or index >= regions[current].entries.size(): return
	selected = index
	file_list.select(index)
	file_list.ensure_current_is_visible()
	var entry = regions[current].entries[index]
	selection_ring.visible = true
	selection_ring.position = regions[current].points[index]+Vector3(0, 0.16, 0)
	selection_ring.scale = Vector3(2.7, 1, 1.8) if entry.kind == "pdf" else Vector3(1.8, 1, 1.8)
	selection_ring.rotation.y = float(posmod(hash(entry.path), 628))/100 if entry.kind == "pdf" else 0.0
	notes.visible = not chrome_hidden
	notes.modulate.a = 1.0
	detail_name.text = entry.name
	describe(entry)
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
	preview_text.text = str(data.get("kind", "Preview"))+"\n\n"+"\n".join(PackedStringArray(data.get("lines", [])))
	if data.has("image_png"):
		var image = Image.new()
		if image.load_png_from_buffer(Marshalls.base64_to_raw(data.image_png)) == OK:
			preview_image.texture = ImageTexture.create_from_image(image)
			preview_image.visible = true
	notes.reset_size()

func describe(entry: Dictionary):
	var facts = metadata_cache.get(entry.path, {})
	var parts = ["Directory" if entry.directory else KIND_NAMES.get(entry.kind, "File") + " · " + entry.size]
	if entry.get("modified", 0) > 0:
		parts.append(Time.get_datetime_string_from_unix_time(int(entry.modified), true).substr(0, 16))
	if entry.get("symlink", false): parts.append("symbolic link")
	var extra = []
	if Terrain.numeric(facts.get("pages")): extra.append(str(int(facts.pages))+" pages")
	if facts.has("width"): extra.append("%d × %d" % [facts.width, facts.get("height", 0)])
	if facts.has("duration"): extra.append(Terrain.duration_text(facts.duration))
	if facts.has("rows"): extra.append("%d rows × %d columns" % [facts.rows, facts.columns])
	if facts.has("captured"): extra.append("captured "+str(facts.captured).substr(0, 10).replace(":", "-"))
	if Terrain.numeric(facts.get("entries")): extra.append("%d entries" % int(facts.entries))
	detail_meta.text = " · ".join(PackedStringArray(parts)) + ("\n" + " · ".join(PackedStringArray(extra)) if not extra.is_empty() else "")
	detail_reading.text = terrain.reading(entry, facts)

func deselect():
	selected = -1
	selection_ring.visible = false
	notes.visible = false
	file_list.deselect_all()

func toggle_notes_body():
	notes_body.visible = not notes_body.visible
	notes_toggle.text = "▾" if notes_body.visible else "▸"
	notes.reset_size()

func focus_selected():
	if selected >= 0 and regions.has(current):
		desired_center = regions[current].points[selected]

func load_metadata(path: String, generation: int):
	var region = regions[path]
	for i in region.entries.size():
		if generation != request_generation: return
		var entry = region.entries[i]
		if entry.directory or entry.kind not in ["pdf", "images", "audio", "video", "tables", "archives", "documents"]: continue
		if entry.kind == "documents" and entry.name.get_extension().to_lower() not in ["docx", "pptx"]: continue
		if not metadata_cache.has(entry.path):
			var facts = await api("/metadata?path="+entry.path.uri_encode())
			metadata_cache[entry.path] = facts if not facts.has("error") else {}
		if generation != request_generation or not is_instance_valid(region.visual): return
		region.features[i].queue_free()
		var feature = terrain.feature(entry, metadata_cache[entry.path], region.climate)
		feature.position = region.points[i]
		region.visual.add_child(feature)
		region.features[i] = feature
		if current == path and selected == i: describe(entry)
	if generation == request_generation: apply_transforms()

# ---------------------------------------------------------------- labels

func _rebuild_labels():
	for item in labels:
		item.widget.queue_free()
	labels.clear()
	for key in region_transforms:
		var r = regions[key]
		var transform = region_transforms[key]
		var title = Label.new()
		title.text = key.get_file() if not key.get_file().is_empty() else "/"
		title.add_theme_font_override("font", SERIF)
		title.add_theme_font_size_override("font_size", 22)
		title.add_theme_color_override("font_color", Color("e4e0ce"))
		title.add_theme_color_override("font_shadow_color", Color("19393a"))
		title.add_theme_constant_override("shadow_offset_x", 2)
		title.add_theme_constant_override("shadow_offset_y", 2)
		title.mouse_filter = Control.MOUSE_FILTER_IGNORE
		overlay.add_child(title)
		labels.append({"widget":title, "point":transform.pos+Vector3(0, 0.1, -r.radius*0.78)*transform.scale, "scale":r.radius*transform.scale, "region":true, "key":key, "index":-1})
		for i in r.entries.size():
			var entry = r.entries[i]
			var item = Label.new()
			item.text = entry.name
			item.add_theme_font_override("font", ITALIC if not entry.directory else SERIF)
			item.add_theme_font_size_override("font_size", 12 if not entry.directory else 14)
			item.add_theme_color_override("font_color", Color("fff3cc") if entry.directory else Color("e9e5cc"))
			item.add_theme_color_override("font_shadow_color", Color("142d2d"))
			item.add_theme_constant_override("shadow_offset_x", 1)
			item.add_theme_constant_override("shadow_offset_y", 1)
			item.mouse_filter = Control.MOUSE_FILTER_IGNORE
			overlay.add_child(item)
			labels.append({"widget":item, "point":transform.pos+(r.points[i]+Vector3(0, 0.15, 1.15))*transform.scale, "scale":transform.scale, "region":false, "directory":entry.directory, "key":key, "index":i})

func position_labels():
	# Visible overlays count as occupied, so map names never bleed through the glass.
	var occupied = []
	for node in [cartouche, tools, zoom_box, gazetteer, notes, legend, toast, carry]:
		if node.visible: occupied.append(node.get_global_rect().grow(6))
	var ordered = labels.duplicate()
	ordered.sort_custom(func(a, b):
		var ap = 0 if a.key == current and a.index == selected else 1 if a.region else 2
		var bp = 0 if b.key == current and b.index == selected else 1 if b.region else 2
		return ap < bp)
	for data in ordered:
		var widget = data.widget
		widget.visible = false
		widget.add_theme_color_override("font_color", Color("f3d391") if data.key == current and data.index == selected else Color("e4e0ce"))
		if label_mode == 2: continue
		if label_mode == 1 and not data.region and not data.get("directory", false): continue
		var projected = data.scale/camera.size*viewport.size.y
		if projected < (80 if data.region else 17): continue
		if camera.is_position_behind(data.point): continue
		var pos = camera.unproject_position(data.point)
		var font = widget.get_theme_font("font")
		var font_size = widget.get_theme_font_size("font_size")
		var width = minf(font.get_string_size(widget.text, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size).x+4, 190)
		widget.custom_minimum_size.x = 0
		widget.size = Vector2(width, widget.get_minimum_size().y)
		widget.clip_text = true
		widget.position = pos-Vector2(width/2, 0)
		var rect = Rect2(widget.position, widget.size).grow(4)
		if not Rect2(Vector2.ZERO, Vector2(viewport.size)).encloses(rect): continue
		var collision = false
		for existing in occupied:
			if rect.intersects(existing): collision = true; break
		if collision: continue
		widget.visible = true
		occupied.append(rect)

# ---------------------------------------------------------------- frame

func _process(delta):
	if not is_instance_valid(camera): return
	clock += delta
	idle_time += delta
	if not moving:
		camera_center = camera_center.lerp(desired_center, 1-exp(-delta*9))
		camera.size = lerpf(camera.size, desired_size, 1-exp(-delta*9))
	var direction = Vector3(sin(camera_angle)*32, 40, cos(camera_angle)*32)
	camera.position = camera_center+direction
	camera.look_at(camera_center, Vector3.UP)
	position_labels()
	animate_weather(delta)
	# Chrome fades to a faint outline while the mouse rests on the map.
	var mouse = get_viewport().get_mouse_position()
	for node in fading:
		if not node.visible: continue
		var hovered = node.get_global_rect().has_point(mouse)
		var target = 1.0 if idle_time < 2.5 or hovered or dialog.visible else fading[node]
		node.modulate.a = lerpf(node.modulate.a, target, 1-exp(-delta*(8.0 if target > node.modulate.a else 2.5)))
	if toast.visible:
		toast_timer -= delta
		if toast_timer < 0.6: toast.modulate.a = maxf(0.0, toast_timer/0.6)
		if toast_timer <= 0.0: toast.visible = false

func animate_weather(delta: float):
	if not regions.has(current): return
	var r = regions[current]
	if not is_instance_valid(r.visual): return
	var layers = [r.weather] if r.weather.visible else []
	for feature in r.features:
		if is_instance_valid(feature) and feature.visible and feature.has_meta("drops"): layers.append(feature)
	for layer in layers:
		var top = float(layer.get_meta("top", 3.0))
		var speed = 0.8 if layer.get_meta("snow", false) else 5.5
		for drop in layer.get_meta("drops", []):
			if not is_instance_valid(drop): continue
			drop.position.y -= speed*delta
			if drop.position.y < 0.12: drop.position.y = top
	# Weather is read from altitude; it thins away as you come down to ground level.
	var sky_layer = r.weather.get_node_or_null("Sky")
	if sky_layer: sky_layer.visible = camera.size > r.radius*1.1
	if r.weather.visible:
		var sky = sky_layer
		if sky: sky.position.x = sin(clock*0.06)*r.radius*0.08
		var bolt = r.weather.get_node_or_null("Bolt")
		if bolt:
			lightning_timer -= delta
			if lightning_timer <= 0.0:
				bolt.visible = not bolt.visible
				lightning_timer = randf_range(0.05, 0.14) if bolt.visible else randf_range(2.0, 6.0)

func _input(event):
	if event is InputEventMouseMotion or event is InputEventKey:
		idle_time = 0.0
	if not is_instance_valid(camera): return
	if event is InputEventKey and event.pressed and event.ctrl_pressed and event.keycode == KEY_P:
		command_palette()
		get_viewport().set_input_as_handled()
		return
	if event is InputEventMouseButton and not event.pressed and event.button_index in [MOUSE_BUTTON_RIGHT, MOUSE_BUTTON_MIDDLE]:
		dragging = false
	if event is InputEventMouseMotion:
		if dragging and not moving:
			drag_moved += event.relative.length()
			var factor = camera.size/viewport.size.y
			var right = camera.global_transform.basis.x
			var forward = Vector3(-right.z, 0, right.x)
			desired_center += -right*event.relative.x*factor-forward*event.relative.y*factor*1.4
		update_hover(event.position)

func _unhandled_input(event):
	# Only events no overlay consumed reach the map.
	if not is_instance_valid(camera) or moving: return
	if event is InputEventMouseButton and event.pressed:
		if event.button_index in [MOUSE_BUTTON_RIGHT, MOUSE_BUTTON_MIDDLE]:
			dragging = true
			drag_moved = 0.0
		elif event.button_index in [MOUSE_BUTTON_WHEEL_UP, MOUSE_BUTTON_WHEEL_DOWN]:
			desired_size = clampf(desired_size*(0.84 if event.button_index == MOUSE_BUTTON_WHEEL_UP else 1.19), 1, 800)
		elif event.button_index == MOUSE_BUTTON_LEFT:
			if path_field.visible: hide_path_field()
			pick(event.position, event.double_click)

func update_hover(point: Vector2):
	hover_label.visible = false
	if dragging or moving or not regions.has(current): return
	if get_viewport().gui_get_hovered_control() != null: return
	var best = nearest_landmark(point)
	if best < 0: return
	var entry = regions[current].entries[best]
	hover_label.text = entry.name + ("  ›" if entry.directory else "")
	hover_label.reset_size()
	hover_label.position = point + Vector2(14, 10)
	hover_label.visible = true

func nearest_landmark(point: Vector2) -> int:
	var best = -1
	var distance = 44.0
	for i in regions[current].points.size():
		var pos = camera.unproject_position(regions[current].points[i]+Vector3(0, 0.3, 0))
		var d = pos.distance_to(point)
		if d < distance: best = i; distance = d
	return best

func _unhandled_key_input(event):
	if not event is InputEventKey or not event.pressed or event.echo: return
	var focus = screen_ui.get_viewport().gui_get_focus_owner()
	if event.keycode == KEY_ESCAPE:
		escape()
		return
	if event.ctrl_pressed:
		match event.keycode:
			KEY_P: command_palette()
			KEY_L: show_path_field()
			KEY_C: set_clipboard("copy")
			KEY_X: set_clipboard("move")
			KEY_V: paste()
			KEY_Z: operate("undo")
			KEY_N: if event.shift_pressed: ask_action("mkdir")
		get_viewport().set_input_as_handled()
		return
	if focus is LineEdit: return
	match event.keycode:
		KEY_SPACE: peek()
		KEY_ENTER, KEY_KP_ENTER: enter_selected()
		KEY_BACKSPACE: go_parent()
		KEY_G, KEY_TAB: toggle_gazetteer()
		KEY_K, KEY_QUESTION: toggle_legend()
		KEY_SLASH: if event.shift_pressed: toggle_legend()
		KEY_W: toggle_weather()
		KEY_L: cycle_labels()
		KEY_Z: overview()
		KEY_T: cycle_climate()
		KEY_F: toggle_chrome()
		KEY_I: toggle_notes_body()
		KEY_F2: ask_action("rename")
		KEY_DELETE: ask_action("trash")
		KEY_F5: refresh()
		KEY_EQUAL, KEY_PLUS, KEY_KP_ADD: desired_size = maxf(1, desired_size/1.3)
		KEY_MINUS, KEY_KP_SUBTRACT: desired_size = minf(800, desired_size*1.3)
		KEY_LEFT, KEY_UP: step_selection(-1)
		KEY_RIGHT, KEY_DOWN: step_selection(1)
		_: return
	get_viewport().set_input_as_handled()

func step_selection(direction: int):
	if not regions.has(current) or regions[current].entries.is_empty(): return
	select_file(clampi(selected+direction if selected >= 0 else 0, 0, regions[current].entries.size()-1))
	focus_selected()

func escape():
	if path_field.visible: hide_path_field()
	elif legend.visible: toggle_legend()
	elif gazetteer.visible: toggle_gazetteer()
	elif notes.visible: deselect()
	elif chrome_hidden: toggle_chrome()
	else: desired_center = Vector3.ZERO

func pick(point: Vector2, activate: bool):
	if not regions.has(current) or loading: return
	var best = nearest_landmark(point)
	if best >= 0:
		select_file(best)
		if activate: enter_selected()
	elif not activate:
		deselect()

func selected_entry() -> Dictionary:
	if not regions.has(current) or selected < 0 or selected >= regions[current].entries.size(): return {}
	return regions[current].entries[selected]

func enter_selected():
	var entry = selected_entry()
	if entry.is_empty(): return
	if entry.directory: navigate(entry.path, "child")
	else: peek()

func go_parent():
	if not regions.has(current): return
	var parent = regions[current].data.parent
	if parent == current: return
	navigate(parent, "parent", 0, current)

func refresh():
	for key in metadata_cache.keys():
		if regions.has(current) and key.get_base_dir() == current: metadata_cache.erase(key)
	navigate(current, "refresh", regions[current].data.page if regions.has(current) else 0)

# ---------------------------------------------------------------- toggles

func toggle_gazetteer():
	gazetteer.visible = not gazetteer.visible and not chrome_hidden
	gazetteer.modulate.a = 1.0
	if gazetteer.visible and selected >= 0: file_list.ensure_current_is_visible()

func toggle_legend():
	legend.visible = not legend.visible
	legend.modulate.a = 1.0
	if legend.visible: fill_legend()

func toggle_weather():
	weather_visible = not weather_visible
	weather_button.text = "☁  Weather" if weather_visible else "☁  Off"
	apply_transforms()

func cycle_labels():
	label_mode = (label_mode+1)%3
	labels_button.text = ["Aa  All", "Aa  Dirs", "Aa  Off"][label_mode]

func toggle_chrome():
	chrome_hidden = not chrome_hidden
	for node in [cartouche, tools, zoom_box]: node.visible = not chrome_hidden
	if chrome_hidden:
		gazetteer.visible = false
		notes.visible = false
		legend.visible = false
	elif selected >= 0: notes.visible = true
	notify("Map only. F or Esc brings the chrome back." if chrome_hidden else "Chrome restored.", false, 2.5)

func cycle_climate():
	if not regions.has(current): return
	var r = regions[current]
	r.climate = (r.climate+1)%5
	climate_override[current] = r.climate
	r.visual.queue_free()
	build_region(r)
	apply_transforms()
	update_cartouche()
	notify("Climate override for this session: %s. The filesystem hasn't changed." % Terrain.CLIMATES[r.climate])

func fill_legend():
	var r = regions.get(current, {})
	var here = ""
	if not r.is_empty():
		here = "[b]Here:[/b] %s. %s.\n\n" % [Terrain.CLIMATES[r.climate], Terrain.forecast(r.stats)]
	legend_text.text = here + """[color=#d9c68f][b]Climate: which part of the machine[/b][/color]
Temperate: Linux-native (ext4). Tropical: Windows volumes over WSL's 9p bridge. Wetland: network mounts. Desert: virtual/ephemeral (tmpfs, proc). Alpine: anywhere you cannot write. [i]T overrides it for the session.[/i]

[color=#d9c68f][b]Hydrology: how it's organised[/b][/color]
Water always flows [b]toward the parent directory[/b]; the river mouth faces [code]..[/code]
Subdirectories are tributaries, wider when they hold more. Empty directories are dry riverbeds. Generated or cache directories (node_modules, .venv, build…) are marshes.
Lakes = audio (duration → area). Waterfalls = video (duration → height). Glaciers = archives (entries → length, compression → blue ice); meltwater is extraction.

[color=#d9c68f][b]Geology: what it is and how old[/b][/color]
Documents with pages are mountains; pages set the height. Rock is age since last change:
[b]fresh basalt[/b] (black, sharp; glowing if changed today) → [b]weathered basalt[/b] (< 6 months) → [b]sandstone[/b] terraces (< 3 years) → [b]granite[/b] (pale, low, rounded).
Images: woodland. Tables: fields. Source: settlements. Executables: obsidian. Disk images: calderas. Databases: wells. Other documents: meadows. Symlinks: signposts. Unknown: cairns.

[color=#d9c68f][b]Weather: what's happening now[/b][/color]
Thunderstorm: many changes this hour or today. Showers: changes today. Cumulus: this week. Clear: this season. Snow: untouched for more than two years. Fog hides unexplored folders until you enter them.

[color=#d9c68f][b]Keys[/b][/color]
Click select · double-click / Enter enter · Backspace up · right-drag pan · wheel zoom · Z overview
G gazetteer · Ctrl+P Bash · Ctrl+L type a path · W weather · L labels · F map only · I collapse notes
Space peek · F2 rename · Delete trash · Ctrl+C / X / V copy, cut, paste · Ctrl+Z undo · Ctrl+Shift+N new folder

[i]Unknown metadata is drawn neutral, never invented. Up to 120 entries are mapped per page.[/i]"""

func change_page(direction: int):
	if not regions.has(current): return
	var data = regions[current].data
	var page = clampi(data.page+direction, 0, data.pages-1)
	if page != data.page: navigate(current, "refresh", page)

func overview():
	if not regions.has(current) or moving: return
	var r = regions[current]
	if not r.parent_key.is_empty() and region_transforms.has(r.parent_key):
		var parent = regions[r.parent_key]
		var transform = region_transforms[r.parent_key]
		if desired_size < parent.radius*transform.scale:
			desired_center = transform.pos
			desired_size = parent.radius*transform.scale*2.6
			notify("Parent landscape. Backspace goes there; Z returns to local detail.", false, 3)
			return
	desired_center = Vector3.ZERO
	desired_size = r.radius*2.6

# ---------------------------------------------------------------- Bash palette

func command_palette():
	if not is_instance_valid(command_window):
		command_window = AcceptDialog.new()
		command_window.title = "Bash navigation"
		command_window.theme = ui_theme
		command_window.min_size = Vector2i(860, 510)
		command_window.ok_button_text = "Close"
		var content = VBoxContainer.new()
		content.custom_minimum_size = Vector2(830, 440)
		command_window.add_child(content)
		label("Run Bash in the current region. Output paths become destinations.", content, 14)
		label("cd ../Photos    •    find . -iname '*.pdf'    •    rg --files | grep notes", content, 13, MUTED)
		var row = HBoxContainer.new()
		content.add_child(row)
		command_field = LineEdit.new()
		command_field.placeholder_text = "Bash command (runs only when submitted)"
		command_field.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		command_field.text_submitted.connect(func(_text): run_command())
		row.add_child(command_field)
		button("Run", row, run_command)
		command_status = label("", content, 13)
		command_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		command_results = ItemList.new()
		command_results.size_flags_vertical = Control.SIZE_EXPAND_FILL
		command_results.item_activated.connect(jump_result)
		content.add_child(command_results)
		button("Travel to selected destination", content, func():
			var selection = command_results.get_selected_items()
			if not selection.is_empty(): jump_result(selection[0]))
		screen_ui.add_child(command_window)
	command_status.text = "Working directory: "+current+". Commands run as your user in a real Bash shell."
	command_window.popup_centered()
	command_field.grab_focus()

func run_command():
	if command_running or command_field.text.strip_edges().is_empty(): return
	command_running = true
	command_status.text = "Running… (10 second limit)"
	command_results.clear()
	destinations.clear()
	var result = await api("/command", {"source":current, "command":command_field.text})
	command_running = false
	if result.has("error"):
		command_status.text = result.error
		return
	destinations = result.results
	for destination in destinations:
		command_results.add_item(("▸ " if destination.directory else "   ")+destination.path)
	command_status.text = "%d destinations. Select one and press Enter to travel." % destinations.size()
	if result.get("truncated", false): command_status.text += " Showing the first 200; narrow the command for more."
	if destinations.is_empty(): command_status.text = "No existing file paths in the output. Try find, rg --files, printf, or cd."
	else:
		command_results.select(0)
		command_results.grab_focus()
		if destinations.size() == 1: jump_result(0)

func jump_result(index: int):
	if index < 0 or index >= destinations.size() or loading or moving: return
	var target = destinations[index]
	command_window.hide()
	await travel_to(target.path, target.directory)

func travel_to(path: String, directory: bool):
	if not regions.has(current): return
	filter_text = ""
	search_field.text = ""
	var folder = path if directory else path.get_base_dir()
	var common = current
	while folder != common and not folder.begins_with(common.trim_suffix("/")+"/"):
		var parent = common.get_base_dir()
		if parent == common or parent.is_empty(): common = "/"; break
		common = parent
	var steps = 0
	while current != common and steps < 64:
		var before = current
		await navigate(regions[current].data.parent, "parent", 0, current)
		if current == before: return
		steps += 1
	var relative = folder.trim_prefix(common).trim_prefix("/")
	for component in relative.split("/", false):
		var child_path = current.trim_suffix("/")+"/"+component
		var found = false
		for entry in regions[current].entries:
			if entry.path == child_path: found = true; break
		if not found: await navigate(current, "refresh", 0, child_path)
		var before = current
		# Survey, pan over the parent geography, then descend into the chosen region.
		await fly_to(Vector3.ZERO, regions[current].radius*3.1)
		await navigate(child_path, "child")
		if current == before: return
	if not directory:
		var found = -1
		for i in regions[current].entries.size():
			if regions[current].entries[i].path == path: found = i
		if found < 0:
			await navigate(current, "refresh", 0, path)
			for i in regions[current].entries.size():
				if regions[current].entries[i].path == path: found = i
		if found >= 0:
			select_file(found)
			var destination = regions[current].points[found]
			await fly_to(camera_center, regions[current].radius*3.1)
			await fly_to(destination, regions[current].radius*3.1)
			await fly_to(destination, 5.5)
			desired_center = destination
			desired_size = 5.5
			selection_ring.visible = true
	notify("Arrived at "+path, false, 3)

# ---------------------------------------------------------------- file operations

func choose_folder():
	var chooser = FileDialog.new()
	chooser.file_mode = FileDialog.FILE_MODE_OPEN_DIR
	chooser.access = FileDialog.ACCESS_FILESYSTEM
	chooser.current_dir = current
	chooser.theme = ui_theme
	chooser.dir_selected.connect(func(path):
		navigate(path, "root")
		chooser.queue_free())
	chooser.canceled.connect(chooser.queue_free)
	screen_ui.add_child(chooser)
	chooser.popup_centered_ratio(0.7)

func peek():
	var entry = selected_entry()
	if entry.is_empty(): return
	var popup = AcceptDialog.new()
	popup.title = entry.name
	popup.min_size = Vector2i(760, 560)
	popup.theme = ui_theme
	popup.ok_button_text = "Close"
	var content = VBoxContainer.new()
	content.custom_minimum_size = Vector2(740, 500)
	popup.add_child(content)
	if preview_image.visible:
		var image = TextureRect.new()
		image.texture = preview_image.texture
		image.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		image.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
		image.custom_minimum_size = Vector2(720, 380)
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
	dialog_source = current if action == "mkdir" else entry.path
	dialog.title = {"rename":"Rename", "mkdir":"New folder", "trash":"Move to Branch trash?"}[action]
	dialog.dialog_text = ("“%s” goes to Branch's own recoverable trash." % entry.name) if action == "trash" else ""
	dialog_field.visible = action != "trash"
	dialog_field.text = entry.name if action == "rename" else ""
	dialog.popup_centered()
	if dialog_field.visible:
		dialog_field.grab_focus()
		dialog_field.select_all()

func confirm_action():
	var data = {"action":pending_action, "source":dialog_source}
	if pending_action in ["rename", "mkdir"]: data.name = dialog_field.text
	var result = await api("/operation", data)
	if result.has("error"):
		notify(result.error)
		return
	await navigate(current, "refresh", regions[current].data.page)
	match pending_action:
		"rename": notify("Renamed.", true)
		"trash": notify("Moved to Branch trash.", true)
		"mkdir": notify("Folder created. New folders can't be undone; trash it instead.")

func operate(action: String):
	var entry = selected_entry()
	if action != "undo" and entry.is_empty(): return
	var result = await api("/operation", {"action":action, "source":entry.get("path", current)})
	if result.has("error"): notify(result.error)
	elif action == "open": notify("Opening "+entry.name+" externally.", false, 3)
	else:
		navigate(current, "refresh", regions[current].data.page)
		if action == "undo": notify("Undone.", false, 3)

func set_clipboard(action: String):
	var entry = selected_entry()
	if entry.is_empty(): return
	clipboard = {"action":action, "source":entry.path}
	carry_label.text = ("Copying  " if action == "copy" else "Moving  ") + entry.name
	carry.visible = true
	carry.reset_size()

func paste():
	if clipboard.is_empty(): return
	var data = clipboard.duplicate()
	data.destination = current
	notify("Transferring…", false, 30)
	var result = await api("/operation", data)
	if result.has("error"):
		notify(result.error)
		return
	var moved = clipboard.action == "move"
	if moved:
		clipboard.clear()
		carry.visible = false
	await navigate(current, "refresh", regions[current].data.page)
	notify("Moved here." if moved else "Copied here. Copies aren't undoable.", moved)

# ---------------------------------------------------------------- smoke test

func _smoke_test():
	await get_tree().create_timer(2.0).timeout
	var original = current
	var entries = regions[current].entries
	assert(regions[current].channels.size() >= 1, "No trunk river")
	assert(regions[current].stats.has("condition"), "No weather computed")
	toggle_gazetteer()
	assert(gazetteer.visible, "Gazetteer did not open")
	toggle_gazetteer()
	var child = -1
	for i in entries.size():
		if entries[i].directory: child = i; break
	if child >= 0:
		select_file(child)
		assert(notes.visible, "Field notes did not appear on selection")
		var outlet = regions[current].outlets.get(child, ROOT_MOUTH)
		await navigate(entries[child].path, "child")
		assert(current != original, "Directory descent failed")
		assert(regions.has(original), "Parent geography was discarded")
		assert(is_equal_approx(regions[current].mouth, outlet), "Child outlet does not follow its tributary")
		# Cross from one nested region to a sibling through their common parent.
		for sibling in entries:
			if sibling.directory and sibling.path != current:
				var survey = await api("/list?path="+sibling.path.uri_encode())
				if not survey.has("error"):
					for candidate in survey.entries:
						if not candidate.directory:
							await travel_to(candidate.path, false)
							assert(selected_entry().path == candidate.path, "Cross-directory travel failed")
							break
				break
		await navigate(original, "parent")
		assert(current == original, "Return to parent failed")
	cycle_labels()
	assert(label_mode == 1)
	cycle_labels()
	assert(label_mode == 2)
	cycle_labels()
	cycle_climate()
	climate_override.erase(current)
	var command_result = await api("/command", {"source":current, "command":"find . -maxdepth 1 -type f -print0"})
	assert(not command_result.has("error"), "Bash command failed")
	if not command_result.results.is_empty():
		var destination = command_result.results[0]
		await travel_to(destination.path, false)
		assert(selected_entry().path == destination.path, "Bash camera destination mismatch")
	var r = regions[current]
	r.climate = climate_for(current, r.data)
	r.visual.queue_free()
	build_region(r)
	apply_transforms()
	update_cartouche()
	desired_center = Vector3.ZERO
	desired_size = r.radius*2.6
	var args = OS.get_cmdline_user_args()
	var focus_name = args[args.find("--focus")+1] if args.find("--focus") >= 0 and args.find("--focus")+1 < args.size() else ""
	for i in r.entries.size():
		if (focus_name.is_empty() and r.entries[i].kind == "pdf") or r.entries[i].name == focus_name:
			await select_file(i)
			if not focus_name.is_empty():
				desired_center = r.points[i]
				desired_size = 6.0
			break
	if "--legend" in args: toggle_legend()
	if "--gazetteer" in args: toggle_gazetteer()
	await get_tree().create_timer(2.5).timeout
	if not capture_path.is_empty() and DisplayServer.get_name() != "headless":
		idle_time = 0.0
		for node in fading: node.modulate.a = 1.0
		await RenderingServer.frame_post_draw
		get_viewport().get_texture().get_image().save_png(capture_path)
		assert(labels.filter(func(item): return item.widget.visible and item.widget.size.x > 5).size() > 0, "Labels did not render")
	print("BRANCH_SMOKE_OK regions=", regions.size(), " labels=", labels.size(), " files=", r.entries.size(), " channels=", r.channels.size(), " weather=", r.stats.condition)
	get_tree().quit()
