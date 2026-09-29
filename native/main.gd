extends Node3D
# Branch Atlas: one continuous world drawn from the survey index. The map is streamed as
# terrain tiles at the level of detail the view needs; every overlay (cartouche, field
# notes, gazetteer, legend, palette) is translucent and appears when relevant.
# The cartographic grammar is in docs/cartography.md.

const TERRAIN_SHADER = preload("res://terrain.gdshader")
const INSTANCE_SHADER = preload("res://instances.gdshader")
const MODELS = preload("res://models.gd")
# Fixed (untinted) colour per model (MODEL_NAMES in branchfm/tiles.py): trunks, walls, stone.
const MODEL_FIXED = [Color(0.30, 0.24, 0.18), Color(0.28, 0.22, 0.16), Color(0.80, 0.76, 0.68), Color(0.5, 0.48, 0.45),
	Color(0.45, 0.36, 0.26), Color(0.36, 0.5, 0.3), Color(0.3, 0.26, 0.2), Color(0.28, 0.22, 0.16), Color(0.78, 0.66, 0.5),
	Color(0.55, 0.5, 0.46), Color(0.62, 0.62, 0.6), Color(0.8, 0.79, 0.76), Color(0.6, 0.58, 0.56), Color(0.62, 0.58, 0.52),
	Color(0.84, 0.78, 0.66), Color(0.86, 0.8, 0.66), Color(0.95, 0.96, 0.96), Color(0.7, 0.66, 0.58), Color(0.78, 0.66, 0.52),
	Color(0.84, 0.82, 0.78), Color(0.7, 0.72, 0.74), Color(0.86, 0.8, 0.66), Color(0.82, 0.76, 0.64)]
const STEAM_MODEL = 16
const FOV = 38.0                # horizontal field of view in perspective
const SERIF = preload("res://fonts/Cartography.ttf")
const ITALIC = preload("res://fonts/CartographyItalic.ttf")
const UI_FONT = preload("res://fonts/Interface.ttf")
const INK = Color("e6e1cc")
const MUTED = Color("a9b8ae")
const GOLD = Color("d9c68f")
const RENDER = 100.0            # render units across the view, at every zoom (floating origin)
const EXAG = 4.0                # vertical exaggeration
const SAMPLES = 257
const TILES_ACROSS = 2.6        # tile size relative to the view width
const FALLBACK_LEVELS = 2       # how far up a coarse stand-in may come from
const SINK_PER_LEVEL = 1.2      # clearance under a coarse fallback, in render units.
                                # It must exceed the coarse-vs-fine height error or the
                                # fallback punches through the finer tile and z-fights.
const MAX_INFLIGHT = 6
const DETAIL_REPEATS = 5.0      # detail texture repeats across the view (per octave)
const KIND_NAMES = {"pdf":"PDF", "images":"Image", "audio":"Audio", "video":"Video", "tables":"Table", "code":"Source",
	"archives":"Archive", "binaries":"Executable", "disks":"Disk image", "databases":"Database", "documents":"Document", "other":"File"}

var api_url = OS.get_environment("BRANCH_API")
var token = OS.get_environment("BRANCH_TOKEN")

# Camera, in world units (float64). The world is the unit square.
var cam_x = 0.5
var cam_y = 0.5
var view = 1.25
var target_x = 0.5
var target_y = 0.5
var target_view = 1.25
var heading = 0.0
var tilt = 50.0                 # Civ V plays at about 45-50 degrees, in perspective
var perspective = true
var models_on = true
var h_ref = 0.0
var flying = false
var fly_tween: Tween

# Tiles.
var tiles = {}                  # "l:x:y" -> {node, mat, level, x, y, base, heights, S}
var wanted = {}
var queued = []
var inflight = 0
var level = 0
var serial = 0
var tile_mesh: PlaneMesh
var model_meshes: Array[ArrayMesh] = []

# Selection and places.
var selected = {}
var here = []                   # chain of places under the view centre
var place_labels = []
var places_dirty = true
var places_timer = 0.0
var status_timer = 0.0
var here_timer = 0.0
var weather_on = false
var label_mode = 0
var clock = 0.0
var idle_time = 0.0
var chrome_hidden = false
var dragging = false
var rotating = false
var drag_moved = 0.0
var smoke = false
var capture_path = ""
var survey = {}

var world: Node3D
var camera: Camera3D
var sun: DirectionalLight3D
var detail_array: Texture2DArray
var ocean: MeshInstance3D
var marker: MeshInstance3D
var screen_ui: Control
var overlay: Control
var ui_theme: Theme
var fading = {}
var cartouche: PanelContainer
var region_title: Label
var crumbs: HBoxContainer
var path_field: LineEdit
var region_info: Label
var tools: PanelContainer
var zoom_box: PanelContainer
var weather_button: Button
var labels_button: Button
var gazetteer: PanelContainer
var gazetteer_title: Label
var file_list: ItemList
var search_field: LineEdit
var listing = []
var notes: PanelContainer
var notes_body: VBoxContainer
var notes_toggle: Button
var detail_name: Label
var detail_meta: Label
var detail_reading: Label
var preview_text: RichTextLabel
var preview_image: TextureRect
var preview_generation = 0
var legend: PanelContainer
var legend_text: RichTextLabel
var toast: PanelContainer
var toast_label: Label
var toast_undo: Button
var toast_timer = 0.0
var carry: PanelContainer
var carry_label: Label
var clipboard = {}
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
	var capture_index = args.find("--capture")
	if capture_index >= 0 and capture_index+1 < args.size(): capture_path = args[capture_index+1]
	_build_ui()
	_build_world()
	if api_url.is_empty():
		notify("Start this application with the atlas launcher so it can reach the survey.", false, 60)
		return
	var start = OS.get_environment("BRANCH_ROOT")
	if not start.is_empty() and start != "/":
		await travel_to(start, true, false)
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

	overlay = Control.new()
	overlay.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	overlay.mouse_filter = Control.MOUSE_FILTER_IGNORE
	screen_ui.add_child(overlay)

	# Cartouche: where you are, and what the survey knows about it.
	cartouche = panel(screen_ui, 0.66, 0.55)
	cartouche.position = Vector2(16, 16)
	var carto = VBoxContainer.new()
	carto.add_theme_constant_override("separation", 2)
	cartouche.add_child(carto)
	var title_row = HBoxContainer.new()
	carto.add_child(title_row)
	button("↑", title_row, go_parent, "Up to the enclosing region (Backspace)")
	region_title = label("The world", title_row, 24, GOLD, SERIF)
	crumbs = HBoxContainer.new()
	crumbs.add_theme_constant_override("separation", 0)
	carto.add_child(crumbs)
	path_field = LineEdit.new()
	path_field.visible = false
	path_field.custom_minimum_size = Vector2(460, 0)
	path_field.placeholder_text = "Linux path or Windows path (E:\\Photos)"
	path_field.text_submitted.connect(func(value):
		hide_path_field()
		travel_to(normalize_path(value), true))
	carto.add_child(path_field)
	region_info = label("", carto, 12, MUTED)

	tools = panel(screen_ui, 0.58, 0.22)
	tools.add_theme_stylebox_override("panel", glass(0.58, 8, 4))
	var tool_row = HBoxContainer.new()
	tool_row.add_theme_constant_override("separation", 2)
	tools.add_child(tool_row)
	button("☰  Gazetteer", tool_row, toggle_gazetteer, "Contents of the region you are over (G)")
	button("›_  Bash", tool_row, command_palette, "Bash navigation palette (Ctrl+P)")
	weather_button = button("☂  Radar off", tool_row, toggle_weather, "Weather radar: files changed today (R)")
	labels_button = button("Aa  All", tool_row, cycle_labels, "Map labels: all / regions / off (L)")
	button("?  Legend", tool_row, toggle_legend, "How to read the map (K)")
	tools.set_anchors_and_offsets_preset(Control.PRESET_TOP_RIGHT, Control.PRESET_MODE_MINSIZE, 16)
	tools.grow_horizontal = Control.GROW_DIRECTION_BEGIN

	zoom_box = panel(screen_ui, 0.58, 0.18)
	zoom_box.add_theme_stylebox_override("panel", glass(0.58, 8, 4))
	var zoom_col = VBoxContainer.new()
	zoom_col.add_theme_constant_override("separation", 0)
	zoom_box.add_child(zoom_col)
	button("+", zoom_col, func(): zoom_by(0.6, get_viewport().get_visible_rect().size/2), "Zoom in")
	button("−", zoom_col, func(): zoom_by(1.6, get_viewport().get_visible_rect().size/2), "Zoom out")
	button("⤢", zoom_col, func(): fly_to(0.5, 0.5, 1.25), "The whole world (Home)")
	button("⌂", zoom_col, func(): travel_to(OS.get_environment("HOME"), true), "Your home")
	zoom_box.set_anchors_and_offsets_preset(Control.PRESET_BOTTOM_RIGHT, Control.PRESET_MODE_MINSIZE, 16)
	zoom_box.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	zoom_box.grow_vertical = Control.GROW_DIRECTION_BEGIN

	gazetteer = panel(screen_ui, 0.82, 0.82)
	gazetteer.set_anchors_preset(Control.PRESET_LEFT_WIDE)
	gazetteer.offset_left = 16
	gazetteer.offset_right = 330
	gazetteer.offset_top = 170
	gazetteer.offset_bottom = -16
	gazetteer.visible = false
	var files = VBoxContainer.new()
	files.add_theme_constant_override("separation", 8)
	gazetteer.add_child(files)
	var gaz_head = HBoxContainer.new()
	files.add_child(gaz_head)
	gazetteer_title = label("Gazetteer", gaz_head, 18, GOLD, SERIF)
	gazetteer_title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	gazetteer_title.clip_text = true
	button("✕", gaz_head, toggle_gazetteer, "Close (G)")
	search_field = LineEdit.new()
	search_field.placeholder_text = "Filter…"
	search_field.text_changed.connect(func(_v): fill_gazetteer())
	files.add_child(search_field)
	file_list = ItemList.new()
	file_list.size_flags_vertical = Control.SIZE_EXPAND_FILL
	file_list.item_selected.connect(func(index): gazetteer_pick(index, false))
	file_list.item_activated.connect(func(index): gazetteer_pick(index, true))
	files.add_child(file_list)
	var gaz_actions = HBoxContainer.new()
	files.add_child(gaz_actions)
	button("New folder", gaz_actions, func(): ask_action("mkdir"), "Ctrl+Shift+N")

	notes = panel(screen_ui, 0.82, 0.82)
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
	button("Go there", actions, func(): if not selected.is_empty(): fly_to_place(selected), "Double-click / Enter")
	button("Peek", actions, peek, "Space")
	button("Open", actions, func(): operate("open"), "Open with the default application")
	button("Rename", actions, func(): ask_action("rename"), "F2")
	button("Copy", actions, func(): set_clipboard("copy"), "Ctrl+C")
	button("Cut", actions, func(): set_clipboard("move"), "Ctrl+X")
	button("Trash", actions, func(): ask_action("trash"), "Delete")

	legend = panel(screen_ui, 0.92, 0.92)
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
	legend.custom_minimum_size = Vector2(740, 600)
	legend.set_anchors_and_offsets_preset(Control.PRESET_CENTER, Control.PRESET_MODE_MINSIZE)
	legend.grow_horizontal = Control.GROW_DIRECTION_BOTH
	legend.grow_vertical = Control.GROW_DIRECTION_BOTH

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
	button("Paste here", carry_row, paste, "Ctrl+V: into the region you are over")
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
	load_detail_textures()
	world = Node3D.new()
	add_child(world)
	var environment = WorldEnvironment.new()
	var config = Environment.new()
	config.background_mode = Environment.BG_COLOR
	config.background_color = Color("17323b")
	config.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	config.ambient_light_color = Color("b9cdd6")
	config.ambient_light_energy = 0.55
	config.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	config.tonemap_exposure = 1.05
	config.glow_enabled = true
	config.glow_intensity = 0.25
	config.glow_bloom = 0.02
	config.adjustment_enabled = true
	config.adjustment_saturation = 0.92
	config.adjustment_contrast = 1.06
	environment.environment = config
	world.add_child(environment)
	sun = DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-30, -35, 0)
	sun.light_color = Color("fff0d6")
	sun.light_energy = 1.35
	sun.shadow_enabled = true
	sun.shadow_bias = 0.04
	sun.directional_shadow_mode = DirectionalLight3D.SHADOW_ORTHOGONAL
	sun.directional_shadow_max_distance = 600
	world.add_child(sun)
	var fill = DirectionalLight3D.new()
	fill.rotation_degrees = Vector3(-60, 140, 0)
	fill.light_color = Color("9fb9d6")
	fill.light_energy = 0.25
	world.add_child(fill)
	camera = Camera3D.new()
	camera.keep_aspect = Camera3D.KEEP_WIDTH
	camera.size = RENDER
	camera.fov = FOV
	model_meshes = MODELS.build()
	camera.near = 1.0
	camera.far = 20000.0
	camera.current = true
	world.add_child(camera)
	var sea = PlaneMesh.new()
	sea.size = Vector2(RENDER*200, RENDER*200)   # reaches the horizon in perspective
	var sea_mat = StandardMaterial3D.new()
	sea_mat.albedo_color = Color8(24, 58, 72)   # the tiles' deep-sea colour, lit the same way
	sea_mat.roughness = 0.45
	sea_mat.metallic_specular = 0.3
	ocean = MeshInstance3D.new()
	ocean.mesh = sea
	ocean.material_override = sea_mat
	ocean.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	world.add_child(ocean)
	tile_mesh = PlaneMesh.new()
	tile_mesh.size = Vector2(1, 1)
	tile_mesh.subdivide_width = 127
	tile_mesh.subdivide_depth = 127
	var ring = TorusMesh.new()
	ring.inner_radius = 0.975
	ring.outer_radius = 1.0
	ring.rings = 48
	ring.ring_segments = 6
	var ring_mat = StandardMaterial3D.new()
	ring_mat.albedo_color = Color("ffe19b")
	ring_mat.emission_enabled = true
	ring_mat.emission = Color("ffe19b")
	ring_mat.emission_energy_multiplier = 0.8
	ring_mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	marker = MeshInstance3D.new()
	marker.mesh = ring
	marker.material_override = ring_mat
	marker.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	marker.visible = false
	world.add_child(marker)

func notify(text: String, undo: bool = false, seconds: float = 5.0):
	toast_label.text = text
	toast_undo.visible = undo
	toast.visible = true
	toast.modulate.a = 1.0
	toast.reset_size()
	toast_timer = seconds if not undo else maxf(seconds, 10.0)

# ---------------------------------------------------------------- service

func api(endpoint: String, data: Variant = null) -> Dictionary:
	var request = HTTPRequest.new()
	request.timeout = 0 if data is Dictionary and data.get("action", "") in ["copy", "move"] else 20
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

static func num(value: float) -> String:
	# Full double precision for world coordinates at any zoom.
	return String.num_scientific(value)

func normalize_path(value: String) -> String:
	value = value.strip_edges()
	if value.length() > 1 and value[1] == ":":
		return "/mnt/"+value[0].to_lower()+"/"+value.substr(2).replace("\\", "/").trim_prefix("/")
	return value

# ---------------------------------------------------------------- tiles

func tile_key(l: int, x: int, y: int) -> String:
	return "%d:%d:%d" % [l, x, y]

func update_tiles():
	level = clampi(int(round(log(TILES_ACROSS/view)/log(2.0))), 0, 50)
	var S = pow(0.5, level)
	# The ground the view actually sees (a trapezoid in perspective), with a margin.
	var b = view_bbox()
	var margin = view*0.12
	var count = 1 << mini(level, 62)
	var x0 = clampi(int(floor((b[0]-margin)/S)), 0, count-1)
	var x1 = clampi(int(floor((b[2]+margin)/S)), 0, count-1)
	var y0 = clampi(int(floor((b[1]-margin)/S)), 0, count-1)
	var y1 = clampi(int(floor((b[3]+margin)/S)), 0, count-1)
	if b[2]+margin < 0 or b[0]-margin > 1 or b[3]+margin < 0 or b[1]-margin > 1:
		x1 = x0-1
	wanted.clear()
	var order = []
	for ty in range(y0, y1+1):
		for tx in range(x0, x1+1):
			var key = tile_key(level, tx, ty)
			wanted[key] = true
			if not tiles.has(key):
				var d = Vector2((tx+0.5)*S-cam_x, (ty+0.5)*S-cam_y).length()
				order.append([d, level, tx, ty])
	order.sort_custom(func(a, b): return a[0] < b[0])
	queued = order
	# Keep a coarse ancestor for anything still loading; drop the rest (cache recent ones).
	var keep = {}
	for key in wanted:
		if tiles.has(key) and not tiles[key].get("loading", false):
			keep[key] = true
			continue
		var parts = key.split(":")
		var l = int(parts[0]); var tx = int(parts[1]); var ty = int(parts[2])
		# Only a near ancestor stands in. Further up, the fallback is drawn from a territory
		# the finer tile has since descended out of, so its ground disagrees by more than any
		# clearance can hide and the two surfaces fight.
		var floor_l = maxi(0, l-FALLBACK_LEVELS)
		while l > floor_l:
			l -= 1; tx >>= 1; ty >>= 1
			var up = tile_key(l, tx, ty)
			if tiles.has(up) and not tiles[up].get("loading", false):
				keep[up] = true
				break
	for key in tiles.keys():
		var t = tiles[key]
		if t.get("loading", false): continue
		t.node.visible = keep.has(key)
		# Models only from tiles at the current level: a coarse fallback's larger trees would
		# double up with the finer tile's.
		for mmi in t.get("models", []): mmi.visible = t.node.visible and models_on and t.level == level
		if t.node.visible:
			t.age = 0
		else:
			t.age = t.get("age", 0)+1
			if t.age > 300:
				free_tile(t)
				tiles.erase(key)
	pump_requests()

func pump_requests():
	while inflight < MAX_INFLIGHT and not queued.is_empty():
		var item = queued.pop_front()
		var key = tile_key(item[1], item[2], item[3])
		if tiles.has(key) or not wanted.has(key):
			continue
		tiles[key] = {"loading":true, "level":item[1], "x":item[2], "y":item[3]}
		inflight += 1
		fetch_tile(item[1], item[2], item[3])

func fetch_tile(l: int, x: int, y: int):
	var key = tile_key(l, x, y)
	var request = HTTPRequest.new()
	request.timeout = 90
	add_child(request)
	request.request(api_url+"/tile?l=%d&x=%d&y=%d" % [l, x, y], PackedStringArray(["Authorization: Bearer "+token]))
	var response = await request.request_completed
	request.queue_free()
	inflight -= 1
	if response[0] == HTTPRequest.RESULT_SUCCESS and response[1] == 200:
		install_tile(key, response[3])
	elif tiles.has(key) and tiles[key].get("loading", false):
		tiles.erase(key)
	pump_requests()

func install_tile(key: String, body: PackedByteArray):
	if body.size() < 36 or body.slice(0, 4).get_string_from_ascii() != "BTL3":
		if tiles.has(key) and tiles[key].get("loading", false): tiles.erase(key)
		return
	var n = body.decode_u32(4)
	var l = body.decode_u32(8)
	var x = body.decode_s64(12)
	var y = body.decode_s64(20)
	var base = body.decode_double(28)
	var plane = n*n
	var offset = 36
	var heights = Image.create_from_data(n, n, false, Image.FORMAT_RF, body.slice(offset, offset+plane*4))
	offset += plane*4
	var colour = Image.create_from_data(n, n, false, Image.FORMAT_RGBA8, body.slice(offset, offset+plane*4))
	colour.generate_mipmaps()
	offset += plane*4
	var aux = Image.create_from_data(n, n, false, Image.FORMAT_RGBA8, body.slice(offset, offset+plane*4))
	offset += plane*4
	var mat_a = Image.create_from_data(n, n, false, Image.FORMAT_RGBA8, body.slice(offset, offset+plane*4))
	offset += plane*4
	var mat_b = Image.create_from_data(n, n, false, Image.FORMAT_RGBA8, body.slice(offset, offset+plane*4))
	offset += plane*4
	var models = build_models(body, offset)
	var mat = ShaderMaterial.new()
	mat.shader = TERRAIN_SHADER
	mat.set_shader_parameter("heightmap", ImageTexture.create_from_image(heights))
	mat.set_shader_parameter("colormap", ImageTexture.create_from_image(colour))
	mat.set_shader_parameter("auxmap", ImageTexture.create_from_image(aux))
	mat.set_shader_parameter("mat_a", ImageTexture.create_from_image(mat_a))
	mat.set_shader_parameter("mat_b", ImageTexture.create_from_image(mat_b))
	if detail_array != null:
		mat.set_shader_parameter("detail", detail_array)
	var S = pow(0.5, l)
	mat.set_shader_parameter("texel_world", S/float(n-1))
	mat.set_shader_parameter("exaggeration", EXAG)
	var node = MeshInstance3D.new()
	node.mesh = tile_mesh
	node.material_override = mat
	node.extra_cull_margin = 1000.0   # displaced in the shader; keep the CPU from culling it
	world.add_child(node)
	var old = tiles.get(key, {})
	if old.has("node") and is_instance_valid(old.node):
		free_tile(old)
	tiles[key] = {"node":node, "mat":mat, "level":l, "x":x, "y":y, "base":base, "heights":heights, "S":S, "age":0,
		"models":models, "born":clock}
	places_dirty = true

func free_tile(t: Dictionary):
	if t.has("node") and is_instance_valid(t.node): t.node.queue_free()
	for mmi in t.get("models", []):
		if is_instance_valid(mmi): mmi.queue_free()

func build_models(body: PackedByteArray, offset: int) -> Array:
	# Trees, houses and boulders: one MultiMesh per kind, positions relative to the tile.
	var out = []
	if body.size() < offset+4: return out
	var count = body.decode_u32(offset)
	offset += 4
	if count == 0 or body.size() < offset+count*24: return out
	var per_kind = []
	for i in MODEL_FIXED.size(): per_kind.append([])
	for i in count:
		var o = offset+i*24
		var kind = body[o+16]
		if kind < per_kind.size(): per_kind[kind].append(o)
	for kind in per_kind.size():
		var rows = per_kind[kind]
		if rows.is_empty(): continue
		var mm = MultiMesh.new()
		mm.transform_format = MultiMesh.TRANSFORM_3D
		mm.use_colors = true
		mm.mesh = model_meshes[kind]
		mm.instance_count = rows.size()
		for i in rows.size():
			var o = rows[i]
			var s = body.decode_float(o+12)
			var yaw = body[o+17]/256.0*TAU
			var tall = maxf(1.0, body[o+21]/32.0)   # building height factor (city centres)
			var basis = Basis(Vector3.UP, yaw).scaled(Vector3(s, s*tall, s))
			mm.set_instance_transform(i, Transform3D(basis, Vector3(body.decode_float(o), body.decode_float(o+8), body.decode_float(o+4))))
			mm.set_instance_color(i, Color8(body[o+18], body[o+19], body[o+20]))
		var mmi = MultiMeshInstance3D.new()
		mmi.multimesh = mm
		var m = ShaderMaterial.new()
		m.shader = INSTANCE_SHADER
		m.set_shader_parameter("fixed_colour", MODEL_FIXED[kind])
		m.set_shader_parameter("animate", 1.0 if kind == STEAM_MODEL else 0.0)
		mmi.material_override = m
		# Placed in the shader, so the CPU must not cull by the (unit-sized) instance bounds.
		mmi.custom_aabb = AABB(Vector3(-1e5, -1e5, -1e5), Vector3(2e5, 2e5, 2e5))
		mmi.visible = false
		world.add_child(mmi)
		out.append(mmi)
	return out

func load_detail_textures():
	# CC0 ground detail (native/textures, fetched by tools/fetch_textures.py), one layer per material.
	var images: Array[Image] = []
	for name in ["meadow", "forest", "field", "town", "wet", "snow", "rock", "sand"]:
		var image = Image.load_from_file(ProjectSettings.globalize_path("res://textures/%s.png" % name))
		if image == null or image.is_empty():
			push_warning("Missing detail texture %s; run tools/fetch_textures.py" % name)
			return
		image.convert(Image.FORMAT_RGBA8)
		image.generate_mipmaps()
		images.append(image)
	detail_array = Texture2DArray.new()
	detail_array.create_from_images(images)

func coverage(t) -> Array:
	# Which sub-cells of a coarse stand-in the finer tiles have already drawn, so it can drop
	# them rather than fight the finer ground for the same pixels.
	var d = level-t.level
	if d <= 0 or d > FALLBACK_LEVELS:
		return [0, 1]
	var n = 1 << d
	var mask = 0
	for j in n:
		for i in n:
			var k = tile_key(level, t.x*n+i, t.y*n+j)
			if tiles.has(k) and not tiles[k].get("loading", false) and tiles[k].node.visible:
				mask |= 1 << (j*n+i)
	return [mask, n]

func place_tiles():
	var scale = RENDER/view*EXAG
	# Detail repeats every P world units (P a power of two near a sixth of the view), cross-faded
	# with the next octave so the grain never jumps while zooming.
	var lp = log(view/DETAIL_REPEATS)/log(2.0)
	var P0 = pow(2.0, floor(lp))
	var P1 = P0*2.0
	var mix_f = lp-floor(lp)
	for key in tiles:
		var t = tiles[key]
		if t.get("loading", false) or not t.node.visible:
			continue
		var S = t.S
		t.node.position = Vector3(((t.x+0.5)*S-cam_x)/view*RENDER, 0, ((t.y+0.5)*S-cam_y)/view*RENDER)
		t.node.scale = Vector3(S/view*RENDER, 1.0, S/view*RENDER)
		t.mat.set_shader_parameter("height_scale", scale)
		t.mat.set_shader_parameter("height_offset", (t.base-h_ref)*scale)
		t.mat.set_shader_parameter("sink", 0.0 if t.level == level else SINK_PER_LEVEL*(level-t.level))
		var cover = coverage(t)
		t.mat.set_shader_parameter("covered", cover[0])
		t.mat.set_shader_parameter("covered_n", cover[1])
		t.mat.set_shader_parameter("weather_on", 1.0 if weather_on else 0.0)
		t.mat.set_shader_parameter("time_s", clock)
		var ox = t.x*S
		var oy = t.y*S
		t.mat.set_shader_parameter("detail_uv0", Vector4(fposmod(ox/P0, 1.0), fposmod(oy/P0, 1.0), S/P0, S/P0))
		t.mat.set_shader_parameter("detail_uv1", Vector4(fposmod(ox/P1, 1.0), fposmod(oy/P1, 1.0), S/P1, S/P1))
		t.mat.set_shader_parameter("detail_mix", mix_f)
		for mmi in t.get("models", []):
			if not mmi.visible: continue
			var m: ShaderMaterial = mmi.material_override
			m.set_shader_parameter("tile_origin", Vector2((t.x*S-cam_x)/view*RENDER, (t.y*S-cam_y)/view*RENDER))
			m.set_shader_parameter("tile_render", S/view*RENDER)
			m.set_shader_parameter("tile_world", S)
			m.set_shader_parameter("height_scale", scale)
			m.set_shader_parameter("height_offset", (t.base-h_ref)*scale)
			m.set_shader_parameter("sink", 0.0 if t.level == level else SINK_PER_LEVEL*(level-t.level))
			m.set_shader_parameter("appear", smoothstep(0.0, 0.5, clock-float(t.get("born", 0.0))))
	ocean.position = Vector3(0, (0.0-h_ref)*scale-0.05, 0)

func height_at(x: float, y: float) -> float:
	# From the finest loaded tile covering the point; the view floats at this height.
	var best = null
	for key in tiles:
		var t = tiles[key]
		if t.get("loading", false): continue
		var S = t.S
		if x < t.x*S or x > (t.x+1)*S or y < t.y*S or y > (t.y+1)*S: continue
		if best == null or t.level > best.level: best = t
	if best == null: return h_ref
	var u = clampi(int((x-best.x*best.S)/best.S*(SAMPLES-1)), 0, SAMPLES-1)
	var v = clampi(int((y-best.y*best.S)/best.S*(SAMPLES-1)), 0, SAMPLES-1)
	return best.base + best.heights.get_pixel(u, v).r

# ---------------------------------------------------------------- camera

func screen_to_world(point: Vector2) -> Vector2:
	var origin = camera.project_ray_origin(point)
	var normal = camera.project_ray_normal(point)
	if absf(normal.y) < 1e-6: return Vector2(cam_x, cam_y)
	var t = -origin.y/normal.y
	var hit = origin+normal*t
	return Vector2(cam_x+hit.x/RENDER*view, cam_y+hit.z/RENDER*view)

func world_to_screen(x: float, y: float, h: float = 0.0) -> Vector2:
	var p = Vector3((x-cam_x)/view*RENDER, (h-h_ref)*RENDER/view*EXAG, (y-cam_y)/view*RENDER)
	return camera.unproject_position(p)

func zoom_by(factor: float, point: Vector2):
	var p = screen_to_world(point)
	target_view = clampf(target_view*factor, 1e-13, 2.5)
	var f = target_view/view
	target_x = p.x+(cam_x-p.x)*f
	target_y = p.y+(cam_y-p.y)*f
	if fly_tween:
		fly_tween.kill()
		flying = false
	places_dirty = true

func fly_to(x: float, y: float, v: float, duration: float = -1.0):
	# Smooth zoom-and-pan: rise enough to see both ends, travel, then descend.
	if fly_tween: fly_tween.kill()
	var x0 = cam_x
	var y0 = cam_y
	var v0 = view
	var distance = Vector2(x-x0, y-y0).length()
	var v_mid = maxf(maxf(v0, v), distance*1.6)
	if duration < 0:
		duration = clampf(0.7+0.18*absf(log(v_mid/minf(v0, v))/log(2.0)), 0.7, 3.2)
	flying = true
	fly_tween = create_tween()
	fly_tween.tween_method(func(t):
		var s = t*t*(3.0-2.0*t)
		var lv
		if t < 0.5: lv = lerpf(log(v0), log(v_mid), smoothstep(0.0, 0.5, t))
		else: lv = lerpf(log(v_mid), log(v), smoothstep(0.5, 1.0, t))
		view = exp(lv)
		cam_x = lerpf(x0, x, s)
		cam_y = lerpf(y0, y, s)
		target_x = cam_x
		target_y = cam_y
		target_view = view, 0.0, 1.0, duration)
	await fly_tween.finished
	flying = false
	places_dirty = true

func fly_to_place(p: Dictionary):
	var side = float(p.get("side", float(p.get("r", 0.0005))*2))
	await fly_to(float(p.x), float(p.y), maxf(side*1.35, 1e-12))

# ---------------------------------------------------------------- frame

func _process(delta):
	clock += delta
	idle_time += delta
	if not flying:
		var k = 1.0-exp(-delta*10.0)
		cam_x = lerpf(cam_x, target_x, k)
		cam_y = lerpf(cam_y, target_y, k)
		view = exp(lerpf(log(view), log(target_view), k))
	h_ref = lerpf(h_ref, height_at(cam_x, cam_y), 1.0-exp(-delta*6.0))
	camera.projection = Camera3D.PROJECTION_PERSPECTIVE if perspective else Camera3D.PROJECTION_ORTHOGONAL
	if perspective: tilt = maxf(tilt, 35.0)
	var d = (RENDER*0.5)/tan(deg_to_rad(FOV*0.5)) if perspective else 400.0
	var r = deg_to_rad(tilt)
	camera.position = Vector3(sin(heading)*cos(r)*d, sin(r)*d, cos(heading)*cos(r)*d)
	camera.look_at(Vector3.ZERO, Vector3.UP)
	update_tiles()
	place_tiles()
	if not selected.is_empty():
		var sp = selected
		marker.position = Vector3((float(sp.x)-cam_x)/view*RENDER, (height_at(float(sp.x), float(sp.y))-h_ref)*RENDER/view*EXAG+0.2, (float(sp.y)-cam_y)/view*RENDER)
		var rr = maxf(float(sp.get("side", float(sp.get("r", 0.0))*2))*0.55/view*RENDER, 0.6)
		marker.scale = Vector3(rr, 0.25, rr)
		# A place that fills the view needs no ring around it.
		marker.visible = rr < RENDER*0.3
	places_timer -= delta
	if places_dirty and places_timer <= 0.0:
		places_dirty = false
		places_timer = 0.35
		refresh_places()
	here_timer -= delta
	if here_timer <= 0.0:
		here_timer = 0.6
		refresh_here()
	status_timer -= delta
	if status_timer <= 0.0:
		status_timer = 2.0
		poll_status()
	position_labels()
	fade_chrome(delta)

func fade_chrome(delta: float):
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

# ---------------------------------------------------------------- places and labels

func view_bbox() -> Array:
	var vp = get_viewport().get_visible_rect().size
	var corners = [screen_to_world(Vector2.ZERO), screen_to_world(Vector2(vp.x, 0)), screen_to_world(vp), screen_to_world(Vector2(0, vp.y))]
	var xs = corners.map(func(c): return c.x)
	var ys = corners.map(func(c): return c.y)
	return [xs.min(), ys.min(), xs.max(), ys.max()]

func refresh_places():
	var b = view_bbox()
	var px = view/get_viewport().get_visible_rect().size.x
	var data = await api("/places?x0=%s&y0=%s&x1=%s&y1=%s&px=%s" % [num(b[0]), num(b[1]), num(b[2]), num(b[3]), num(px)])
	if data.has("error"): return
	for item in place_labels: item.widget.queue_free()
	place_labels.clear()
	for r in data.get("regions", []):
		var continent = r.kind == "continent"
		var text = r.name
		if continent: text = spaced(r.name.to_upper())
		var size_px = float(r.side)/px
		var font_size = 26 if continent else clampi(int(9+log(size_px)/log(2.0)*1.5), 12, 22)
		var w = make_label(text, SERIF, font_size, Color("f1ead4") if continent else Color("e6dfc6"), 2 if continent else 1)
		var item = {"widget":w, "x":float(r.x), "y":float(r.y), "priority":size_px*(4.0 if continent else 1.0), "region":true, "data":r}
		if String(r.get("subtitle", "")) != "" and size_px > 110:
			# What a place is, under its name: "video library · 55 videos".
			var sub = make_label(r.subtitle, ITALIC, maxi(11, font_size-5), Color("e9d9a8"), 1)
			sub.get_parent().remove_child(sub)
			w.add_child(sub)
			sub.visible = true
			item["sub"] = sub
		place_labels.append(item)
	for q in data.get("patches", []):
		var size_px = float(q.side)/px
		var w = make_label(q.name, ITALIC, clampi(int(8+log(size_px)/log(2.0)*1.2), 11, 16), Color("e9d9a8"), 1)
		place_labels.append({"widget":w, "x":float(q.x), "y":float(q.y), "priority":size_px*1.5, "region":false, "data":q})
	for f in data.get("files", []):
		var w = make_label(f.name, ITALIC, 12, Color("f3edd8"), 1)
		place_labels.append({"widget":w, "x":float(f.x), "y":float(f.y)+float(f.r)*1.1, "priority":float(f.r)/px, "region":false, "data":f})
	# Each label stands on its own ground: on a tilted view a label drawn at the view's
	# reference height slides off its place wherever the land is higher or lower.
	for item in place_labels: item["h"] = height_at(item.x, item.y)
	place_labels.sort_custom(func(a, b): return a.priority > b.priority)

static func spaced(text: String) -> String:
	var out = ""
	for ch in text:
		out += ch + (" " if ch != " " else "  ")
	return out.strip_edges()

func make_label(text: String, font: Font, size: int, colour: Color, shadow: int) -> Label:
	var w = Label.new()
	w.text = text
	w.add_theme_font_override("font", font)
	w.add_theme_font_size_override("font_size", size)
	w.add_theme_color_override("font_color", colour)
	w.add_theme_color_override("font_shadow_color", Color(0.06, 0.12, 0.12, 0.85))
	w.add_theme_constant_override("shadow_offset_x", shadow)
	w.add_theme_constant_override("shadow_offset_y", shadow)
	w.add_theme_constant_override("shadow_outline_size", 3)
	w.mouse_filter = Control.MOUSE_FILTER_IGNORE
	w.visible = false
	overlay.add_child(w)
	return w

func position_labels():
	var occupied = []
	for node in [cartouche, tools, zoom_box, gazetteer, notes, legend, toast, carry]:
		if node.visible: occupied.append(node.get_global_rect().grow(6))
	var screen = Rect2(Vector2.ZERO, get_viewport().get_visible_rect().size)
	for item in place_labels:
		var w: Label = item.widget
		w.visible = false
		if label_mode == 2 or (label_mode == 1 and not item.region): continue
		var pos = world_to_screen(item.x, item.y, item.get("h", h_ref))
		var size = w.get_minimum_size()
		w.size = size
		w.position = pos-size/2
		var rect = Rect2(w.position, size).grow(3)
		if item.has("sub"):
			var sub: Label = item.sub
			var ss = sub.get_minimum_size()
			sub.size = ss
			sub.position = Vector2((size.x-ss.x)/2, size.y-3)
			rect = rect.merge(Rect2(w.position+sub.position, ss).grow(3))
		if not screen.encloses(rect): continue
		var clash = false
		for other in occupied:
			if rect.intersects(other):
				clash = true
				break
		if clash: continue
		occupied.append(rect)
		w.visible = true

func refresh_here():
	var vp = get_viewport().get_visible_rect().size
	var c = screen_to_world(vp/2)
	var px = view/vp.x
	var data = await api("/at?x=%s&y=%s&px=%s" % [num(c.x), num(c.y), num(px*4)])
	if data.has("error"): return
	# You are "in" the deepest place that still fills a good part of the view.
	var chain = []
	for entry in data.get("chain", []):
		if float(entry.side) >= view*0.45: chain.append(entry)
	var changed = chain.size() != here.size() or (not chain.is_empty() and chain[-1].path != here[-1].path)
	here = chain
	if changed:
		update_cartouche()
		if gazetteer.visible: load_gazetteer()

func update_cartouche():
	var place = here[-1] if not here.is_empty() else {}
	region_title.text = place.get("name", "The world")
	for child in crumbs.get_children(): child.queue_free()
	var world_crumb = button("World", crumbs, func(): fly_to(0.5, 0.5, 1.25), "The whole world")
	world_crumb.add_theme_font_size_override("font_size", 12)
	for i in here.size():
		var entry = here[i]
		label("›", crumbs, 12, Color("7f978d"))
		var crumb = button(entry.name, crumbs, func(): fly_to_place(entry), entry.path)
		crumb.add_theme_font_size_override("font_size", 12)
	button("✎", crumbs, show_path_field, "Type a path (Ctrl+L)").add_theme_font_size_override("font_size", 12)
	update_info()
	cartouche.reset_size()

func update_info():
	var lines = []
	if not here.is_empty():
		var p = here[-1]
		lines.append("%s files · %s folders" % [big_number(p.files), big_number(p.dirs)])
		if int(p.get("day", 0)) > 0: lines.append("☂ %d changed today" % int(p.day))
		elif int(p.get("week", 0)) > 0: lines.append("%d changed this week" % int(p.week))
		if not p.get("scanned", true): lines.append("Terra incognita: the survey has been asked to go here")
	if not survey.is_empty():
		var s = "Survey: %s files, %s folders" % [big_number(survey.get("files", 0)), big_number(survey.get("dirs", 0))]
		if int(survey.get("queued", 0)) > 0: s += " · %s folders queued" % big_number(survey.queued)
		lines.append(s)
	region_info.text = "\n".join(PackedStringArray(lines))

static func big_number(value) -> String:
	var n = float(value)
	if n >= 1e6: return "%.1f M" % (n/1e6)
	if n >= 1e4: return "%d k" % int(n/1e3)
	return str(int(n))

func poll_status():
	var data = await api("/status?since=%d" % serial)
	if data.has("error"): return
	survey = data
	serial = int(data.get("serial", serial))
	for box in data.get("invalid", []):
		for key in tiles.keys():
			var t = tiles[key]
			if t.get("loading", false): continue
			var S = t.S
			if (t.x+1)*S < box[0] or (t.y+1)*S < box[1] or t.x*S > box[2] or t.y*S > box[3]: continue
			if wanted.has(key):
				# Redraw in place: the old tile stays until the new one arrives.
				inflight += 1
				fetch_tile(t.level, t.x, t.y)
			else:
				free_tile(t)
				tiles.erase(key)
	if not data.get("invalid", []).is_empty(): places_dirty = true
	update_info()

# ---------------------------------------------------------------- input

func _input(event):
	if event is InputEventMouseMotion or event is InputEventKey:
		idle_time = 0.0
	if event is InputEventKey and event.pressed and event.ctrl_pressed and event.keycode == KEY_P:
		command_palette()
		get_viewport().set_input_as_handled()
		return
	if event is InputEventMouseButton and not event.pressed:
		if event.button_index == MOUSE_BUTTON_LEFT and dragging:
			dragging = false
			if drag_moved < 5.0: pick(event.position, false)
		if event.button_index in [MOUSE_BUTTON_RIGHT, MOUSE_BUTTON_MIDDLE]:
			rotating = false
	if event is InputEventMouseMotion:
		if dragging:
			drag_moved += event.relative.length()
			if drag_moved >= 5.0:
				var a = screen_to_world(event.position-event.relative)
				var b = screen_to_world(event.position)
				cam_x -= b.x-a.x
				cam_y -= b.y-a.y
				target_x = cam_x
				target_y = cam_y
				places_dirty = true
				if fly_tween:
					fly_tween.kill()
					flying = false
		elif rotating:
			heading -= event.relative.x*0.006
			tilt = clampf(tilt-event.relative.y*0.15, 30.0, 88.0)
			places_dirty = true
		update_hover(event.position)

func _unhandled_input(event):
	if event is InputEventMouseButton and event.pressed:
		if event.button_index == MOUSE_BUTTON_LEFT:
			if path_field.visible: hide_path_field()
			if event.double_click:
				dragging = false
				pick(event.position, true)
			else:
				dragging = true
				drag_moved = 0.0
		elif event.button_index in [MOUSE_BUTTON_RIGHT, MOUSE_BUTTON_MIDDLE]:
			rotating = true
		elif event.button_index == MOUSE_BUTTON_WHEEL_UP:
			zoom_by(0.8, event.position)
		elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			zoom_by(1.25, event.position)

func _unhandled_key_input(event):
	if not event is InputEventKey or not event.pressed: return
	var focus = screen_ui.get_viewport().gui_get_focus_owner()
	if event.keycode == KEY_ESCAPE:
		escape()
		return
	if event.ctrl_pressed:
		match event.keycode:
			KEY_L: show_path_field()
			KEY_C: set_clipboard("copy")
			KEY_X: set_clipboard("move")
			KEY_V: paste()
			KEY_Z: operate("undo")
			KEY_N: if event.shift_pressed: ask_action("mkdir")
		get_viewport().set_input_as_handled()
		return
	if focus is LineEdit: return
	var pan = view*0.08
	var fwd = Vector2(-sin(heading), -cos(heading))
	var right = Vector2(cos(heading), -sin(heading))
	match event.keycode:
		KEY_W, KEY_UP: nudge(fwd*pan)
		KEY_S, KEY_DOWN: nudge(-fwd*pan)
		KEY_A, KEY_LEFT: nudge(-right*pan)
		KEY_D, KEY_RIGHT: nudge(right*pan)
		KEY_Q: heading += 0.12
		KEY_E: heading -= 0.12
		KEY_PAGEUP: tilt = clampf(tilt+4, 30, 88)
		KEY_PAGEDOWN: tilt = clampf(tilt-4, 30, 88)
		KEY_EQUAL, KEY_PLUS, KEY_KP_ADD: zoom_by(0.7, get_viewport().get_visible_rect().size/2)
		KEY_MINUS, KEY_KP_SUBTRACT: zoom_by(1.4, get_viewport().get_visible_rect().size/2)
		KEY_HOME: fly_to(0.5, 0.5, 1.25)
		KEY_SPACE: peek()
		KEY_ENTER, KEY_KP_ENTER: if not selected.is_empty(): fly_to_place(selected)
		KEY_BACKSPACE: go_parent()
		KEY_G, KEY_TAB: toggle_gazetteer()
		KEY_K, KEY_QUESTION: toggle_legend()
		KEY_SLASH: if event.shift_pressed: toggle_legend()
		KEY_R: toggle_weather()
		KEY_V:
			perspective = not perspective
			notify("Perspective view (as in Civ V)." if perspective else "Flat map view.", false, 2.5)
		KEY_M:
			models_on = not models_on
			notify("Trees, houses and boulders shown." if models_on else "3D landmarks hidden.", false, 2.5)
		KEY_L: cycle_labels()
		KEY_F: toggle_chrome()
		KEY_I: toggle_notes_body()
		KEY_F2: ask_action("rename")
		KEY_DELETE: ask_action("trash")
		_: return
	places_dirty = true
	get_viewport().set_input_as_handled()

func nudge(offset: Vector2):
	target_x += offset.x
	target_y += offset.y

func update_hover(point: Vector2):
	hover_label.visible = false
	if dragging or rotating or flying: return
	if get_viewport().gui_get_hovered_control() != null: return
	var best = null
	var best_d = 26.0
	for item in place_labels:
		if item.region: continue
		var d = world_to_screen(item.x, float(item.data.y), item.get("h", h_ref)).distance_to(point)
		if d < best_d:
			best = item
			best_d = d
	if best == null: return
	hover_label.text = best.data.name
	hover_label.reset_size()
	hover_label.position = point+Vector2(14, 10)
	hover_label.visible = true

func escape():
	if path_field.visible: hide_path_field()
	elif legend.visible: toggle_legend()
	elif gazetteer.visible: toggle_gazetteer()
	elif notes.visible: deselect()
	elif chrome_hidden: toggle_chrome()

# ---------------------------------------------------------------- selection

func pick(point: Vector2, activate: bool):
	var w = screen_to_world(point)
	var px = view/get_viewport().get_visible_rect().size.x
	var data = await api("/at?x=%s&y=%s&px=%s" % [num(w.x), num(w.y), num(px)])
	if data.has("error"): return
	var place = {}
	if data.get("file") != null:
		place = data.file
	else:
		# The smallest place that is still comfortably clickable at this zoom.
		for entry in data.get("chain", []):
			if float(entry.side)/px >= 30: place = entry
	if place.is_empty():
		deselect()
		return
	select_place(place)
	if activate: fly_to_place(place)

func select_place(place: Dictionary):
	selected = place
	marker.visible = true
	notes.visible = not chrome_hidden
	notes.modulate.a = 1.0
	detail_name.text = place.name
	var is_file = not place.has("files")
	if is_file:
		detail_meta.text = "%s · %s" % [KIND_NAMES.get(place.get("kind", "other"), "File"), place.path]
		detail_reading.text = reading_for_file(place)
	else:
		detail_meta.text = "%s files · %s folders · %s" % [big_number(place.get("files", 0)), big_number(place.get("dirs", 0)), place.path]
		detail_reading.text = reading_for_region(place)
	preview_text.text = "Reading preview…"
	preview_image.visible = false
	preview_generation += 1
	var generation = preview_generation
	var data = await api("/preview?path="+String(place.path).uri_encode())
	if generation != preview_generation: return
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

func reading_for_region(p: Dictionary) -> String:
	var kinds = p.get("kinds", {})
	var top = []
	for k in kinds: top.append([int(kinds[k]), k])
	top.sort_custom(func(a, b): return a[0] > b[0])
	var cover = {"images":"forest", "video":"canyon country", "tables":"fields", "code":"towns", "databases":"towns", "pdf":"meadow and uplands",
		"documents":"meadow", "audio":"wetland", "archives":"ice", "binaries":"bare rock", "disks":"bare rock", "other":"scrub"}
	var parts = []
	for i in mini(3, top.size()): parts.append("%s (%s)" % [cover.get(top[i][1], "scrub"), top[i][1]])
	var virtual = String(p.get("path", "")) in ["/proc", "/sys", "/dev", "/run"]
	var text = ("Land cover follows content: " + ", ".join(PackedStringArray(parts)) + ".") if not parts.is_empty() else ("A virtual filesystem made by the kernel: not storage, never surveyed." if virtual else "Nothing surveyed inside yet.")
	if int(p.get("day", 0)) > 0: text += " Rain on the radar: %d changed today." % int(p.day)
	var newest = float(p.get("newest", 0))
	if newest > 0:
		var years = (Time.get_unix_time_from_system()-newest)/31557600.0
		if years > 2: text += " Snowbound: untouched for %d years." % int(years)
	return text

func reading_for_file(p: Dictionary) -> String:
	var roles = {"hall":"The town hall: this project's manifest.", "power":"A power station: AI model weights.",
		"factory":"A factory: build output.", "depot":"A warehouse: a dependency brought in from elsewhere.",
		"silo":"A silo: a database.", "arch":"A natural arch: a link to somewhere else.",
		"oak":"An old oak: a camera original (RAW).", "shrub":"A shrub: a screenshot or small image."}
	var role = String(p.get("role", ""))
	if roles.has(role): return roles[role]
	var forms = {"pdf":"A peak of the massif; its rock shows age (dark basalt when new, pale granite when old).", "images":"A stand of the forest; its leaves turn with age.",
		"audio":"A reed bed with pools.", "video":"A mesa of banded strata: film is banded in frames, and a long one stands tall.", "tables":"A field.",
		"code":"A house in town; the architecture is the language, and code untouched for three years stands in ruins.", "databases":"A silo.",
		"archives":"A tongue of the glacier.", "binaries":"An obsidian flow.", "disks":"A caldera.", "documents":"A meadow in flower.",
		"weights":"A power station: AI model weights.", "other":"Scrub with a cairn."}
	return forms.get(p.get("kind", "other"), "A cairn.")

func deselect():
	selected = {}
	marker.visible = false
	notes.visible = false

func toggle_notes_body():
	notes_body.visible = not notes_body.visible
	notes_toggle.text = "▾" if notes_body.visible else "▸"
	notes.reset_size()

func go_parent():
	if here.size() >= 2: fly_to_place(here[-2])
	elif here.size() == 1: fly_to(0.5, 0.5, 1.25)

# ---------------------------------------------------------------- gazetteer

func toggle_gazetteer():
	gazetteer.visible = not gazetteer.visible and not chrome_hidden
	gazetteer.modulate.a = 1.0
	if gazetteer.visible: load_gazetteer()

func load_gazetteer():
	var path = here[-1].path if not here.is_empty() else "/"
	gazetteer_title.text = here[-1].name if not here.is_empty() else "The world"
	var data = await api("/list?path=%s&page_size=1000" % path.uri_encode())
	listing = [] if data.has("error") else data.entries
	fill_gazetteer()

func fill_gazetteer():
	file_list.clear()
	var needle = search_field.text.to_lower()
	for entry in listing:
		if not needle.is_empty() and not entry.name.to_lower().contains(needle): continue
		file_list.add_item(("▸ " if entry.directory else "   ")+entry.name)
		file_list.set_item_metadata(file_list.item_count-1, entry)
		file_list.set_item_tooltip(file_list.item_count-1, entry.path)

func gazetteer_pick(index: int, activate: bool):
	var entry = file_list.get_item_metadata(index)
	var place = await api("/region?path="+String(entry.path).uri_encode())
	if place.has("error"):
		notify(place.error)
		return
	if not place.has("kind"): place.kind = entry.kind
	select_place(place)
	if activate: fly_to_place(place)
	else:
		target_x = float(place.x)
		target_y = float(place.y)

# ---------------------------------------------------------------- toggles

func toggle_legend():
	legend.visible = not legend.visible
	legend.modulate.a = 1.0
	if legend.visible: fill_legend()

func toggle_weather():
	weather_on = not weather_on
	weather_button.text = "☂  Radar on" if weather_on else "☂  Radar off"
	if weather_on:
		notify("Radar: coloured cells are files changed today.  green a few  ·  yellow many  ·  red hundreds", false, 8)

func cycle_labels():
	label_mode = (label_mode+1)%3
	labels_button.text = ["Aa  All", "Aa  Regions", "Aa  Off"][label_mode]

func toggle_chrome():
	chrome_hidden = not chrome_hidden
	for node in [cartouche, tools, zoom_box]: node.visible = not chrome_hidden
	if chrome_hidden:
		gazetteer.visible = false
		notes.visible = false
		legend.visible = false
	elif not selected.is_empty(): notes.visible = true
	notify("Map only. F or Esc brings the chrome back." if chrome_hidden else "Chrome restored.", false, 2.5)

func show_path_field():
	path_field.text = here[-1].path if not here.is_empty() else "/"
	path_field.visible = true
	crumbs.visible = false
	path_field.grab_focus()
	path_field.select_all()

func hide_path_field():
	path_field.visible = false
	crumbs.visible = true
	path_field.release_focus()
	cartouche.reset_size()

func fill_legend():
	legend_text.text = """[color=#d9c68f][b]One world[/b][/color]
The whole filesystem is surveyed into one continuous map. The survey keeps running in the background, coarse to fine; parchment marks terra incognita it has not reached yet. Every folder owns a territory sized by what it holds; its own files stand in its home district.

[color=#d9c68f][b]Continents are disks[/b][/color]
The root disk is one continent, named for the system it carries; every other drive is another across open sea — under WSL that means each Windows drive (C:, D:, E:…) and each cloud drive. Inside a disk everything is one landmass: provinces share borders along ridgelines.

[color=#d9c68f][b]Water flows toward the parent folder[/b][/color]
Every folder's water leaves at its outlet and runs down the valleys between provinces to its parent, and on to the sea. Streams meet as tributaries and widen with what they carry. Lakes pool where many subfolders meet; waterfalls mark where a river crosses onto other ground (another filesystem, or the edge of what you may write); each disk's great river ends in a delta.

[color=#d9c68f][b]Land cover is content[/b][/color]
A folder's own files lie as fields: each kind is one patch (a forest of images, a field system of tables, a town of source files) and each file one parcel of it, in alphabetical order across the patch. Far off, a patch is one colour; closer, it divides into fields with hedgerows, city blocks with streets, the peaks of a massif (PDFs, rock by age), a crevassed glacier (archives), mesas of banded strata (video), reed beds and pools (audio), obsidian flows (executables), calderas (disk images), flowering meadow (documents) and scrub with cairns (anything else).

[color=#d9c68f][b]Buildings are roles[/b][/color]
A big town of code is a city, tallest at its centre. Houses are source files, built in their language's style (Python terracotta, JavaScript white flat roofs, C slate, Rust rust-red, Go blue). A town hall stands for a project's manifest, a walled town with a keep for a git repository, factories for build output, warehouses for vendored dependencies, silos for databases, power stations for AI model weights. Code untouched for three years stands in ruins.

[color=#d9c68f][b]Trees are the climate[/b][/color]
Broadleaf woods on Linux, jungle and palms on Windows drives, conifers where you cannot write. Virtual filesystems (/proc, /sys) are a volcanic wasteland: the kernel's live state, remade every moment. Camera originals (RAW) grow as old oaks, screenshots as shrubs; photographs turn autumnal after a year.

[color=#d9c68f][b]Landforms[/b][/color]
Geysers: files changed in the last 15 minutes. Volcano: most of a folder changed this week. Salt flat: an empty folder. Fenced ground: a folder that could not be read. Slot canyon: a folder holding only one folder. PDF and film libraries rise as one range or tableland, their members its summits and mesas. Natural arch: a link. Monument: the largest file on each disk.

[color=#d9c68f][b]Rock is age, snow is dormancy[/b][/color]
Ridgelines show the age of their region: dark basalt when changed recently, sandstone within three years, pale granite when old. Regions untouched for over two years are snowbound.

[color=#d9c68f][b]Weather radar[/b][/color]
Green to red cells: files changed today. R toggles the radar.

[color=#d9c68f][b]Climate is the mount[/b][/color]
Temperate: Linux. Tropical: Windows drives. Wetland: network mounts. Desert: virtual filesystems. Alpine: anywhere you cannot write.

[color=#d9c68f][b]Moving[/b][/color]
Drag to pan · wheel to zoom at the cursor · right-drag to turn and tilt · double-click to go to a place · Backspace up · Home: the whole world
WASD / arrows pan · Q/E turn · PgUp/PgDn tilt · V perspective or flat map · M 3D landmarks · G gazetteer · Ctrl+P Bash · Ctrl+L path · R radar · L labels · F map only
Space peek · F2 rename · Delete trash · Ctrl+C / X / V copy, cut, paste into the region you are over · Ctrl+Z undo"""

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
		label("Run Bash in the region you are over. Output paths become destinations.", content, 14)
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
			var chosen = command_results.get_selected_items()
			if not chosen.is_empty(): jump_result(chosen[0]))
		screen_ui.add_child(command_window)
	command_status.text = "Working directory: "+current_path()+". Commands run as your user in a real Bash shell."
	command_window.popup_centered()
	command_field.grab_focus()

func current_path() -> String:
	return here[-1].path if not here.is_empty() else OS.get_environment("HOME")

func run_command():
	if command_running or command_field.text.strip_edges().is_empty(): return
	command_running = true
	command_status.text = "Running… (10 second limit)"
	command_results.clear()
	destinations.clear()
	var result = await api("/command", {"source":current_path(), "command":command_field.text})
	command_running = false
	if result.has("error"):
		command_status.text = result.error
		return
	destinations = result.results
	for destination in destinations:
		command_results.add_item(("▸ " if destination.directory else "   ")+destination.path)
	command_status.text = "%d destinations. Select one and press Enter to travel." % destinations.size()
	if destinations.is_empty(): command_status.text = "No existing file paths in the output. Try find, rg --files, printf, or cd."
	else:
		command_results.select(0)
		command_results.grab_focus()
		if destinations.size() == 1: jump_result(0)

func jump_result(index: int):
	if index < 0 or index >= destinations.size(): return
	var target = destinations[index]
	command_window.hide()
	await travel_to(target.path, target.directory)

func travel_to(path: String, directory: bool, animate: bool = true):
	var place = await api("/region?path="+path.uri_encode())
	if place.has("error"):
		notify(place.error+" ("+path+")", false, 6)
		return
	if not directory and not place.has("kind"): place.kind = "other"
	select_place(place)
	if animate:
		await fly_to_place(place)
	else:
		var side = float(place.get("side", 0.001))
		cam_x = float(place.x)
		cam_y = float(place.y)
		view = maxf(side*1.35, 1e-12)
		target_x = cam_x
		target_y = cam_y
		target_view = view

# ---------------------------------------------------------------- file operations

func peek():
	if selected.is_empty(): return
	api("/visit", {"source":selected.path})
	var popup = AcceptDialog.new()
	popup.title = selected.name
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
	if action != "mkdir" and selected.is_empty(): return
	pending_action = action
	dialog_source = current_path() if action == "mkdir" else selected.path
	dialog.title = {"rename":"Rename", "mkdir":"New folder in "+current_path().get_file(), "trash":"Move to Branch trash?"}[action]
	dialog.dialog_text = ("“%s” goes to Branch's own recoverable trash." % selected.name) if action == "trash" else ""
	dialog_field.visible = action != "trash"
	dialog_field.text = selected.name if action == "rename" else ""
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
	match pending_action:
		"rename": notify("Renamed. The survey redraws the map there.", true)
		"trash":
			notify("Moved to Branch trash.", true)
			deselect()
		"mkdir": notify("Folder created. New folders can't be undone; trash it instead.")

func operate(action: String):
	if action != "undo" and selected.is_empty(): return
	var result = await api("/operation", {"action":action, "source":selected.get("path", current_path())})
	if result.has("error"): notify(result.error)
	elif action == "open":
		api("/visit", {"source":selected.path})
		notify("Opening "+selected.name+" externally.", false, 3)
	elif action == "undo": notify("Undone.", false, 3)

func set_clipboard(action: String):
	if selected.is_empty(): return
	clipboard = {"action":action, "source":selected.path}
	carry_label.text = ("Copying  " if action == "copy" else "Moving  ") + selected.name
	carry.visible = true
	carry.reset_size()

func paste():
	if clipboard.is_empty(): return
	var data = clipboard.duplicate()
	data.destination = current_path()
	notify("Transferring into "+current_path()+"…", false, 30)
	var result = await api("/operation", data)
	if result.has("error"):
		notify(result.error)
		return
	var moved = clipboard.action == "move"
	if moved:
		clipboard.clear()
		carry.visible = false
	notify("Moved here." if moved else "Copied here. Copies aren't undoable.", moved)

# ---------------------------------------------------------------- smoke test

func wait_for_terrain(limit_ms: int):
	# Complete and settled: every wanted tile loaded for three checks in a row, with the camera
	# still (the wanted set changes while a flight or the height reference is settling).
	var started = Time.get_ticks_msec()
	var steady = 0
	while Time.get_ticks_msec()-started < limit_ms:
		await get_tree().create_timer(0.5).timeout
		var loaded = 0
		for key in wanted:
			if tiles.has(key) and not tiles[key].get("loading", false): loaded += 1
		steady = steady+1 if loaded == wanted.size() and loaded > 0 and not flying else 0
		if steady >= 3: return

func _smoke_test():
	var args = OS.get_cmdline_user_args()
	await wait_for_terrain(90000)
	assert(not tiles.is_empty(), "No terrain tiles arrived")
	var enter_index = args.find("--enter")
	if enter_index >= 0 and enter_index+1 < args.size():
		await travel_to(args[enter_index+1], true)
		await wait_for_terrain(90000)
	var pick_result = await api("/at?x=%s&y=%s&px=%s" % [num(cam_x), num(cam_y), num(view/1440.0)])
	assert(not pick_result.has("error"), "Picking failed")
	refresh_places()
	await get_tree().create_timer(2.0).timeout
	if "--legend" in args: toggle_legend()
	if not capture_path.is_empty() and DisplayServer.get_name() != "headless":
		idle_time = 0.0
		for node in fading: node.modulate.a = 1.0
		await RenderingServer.frame_post_draw
		get_viewport().get_texture().get_image().save_png(capture_path)
	# Frames over a short settled window: the renderer's own cost, with every tile already in.
	var frames = 0
	var t_start = Time.get_ticks_msec()
	while Time.get_ticks_msec()-t_start < 2000:
		await RenderingServer.frame_post_draw
		frames += 1
	var fps = frames*1000.0/maxf(1.0, Time.get_ticks_msec()-t_start)
	print("BRANCH_SMOKE_OK tiles=", tiles.size(), " level=", level, " labels=", place_labels.size(),
		" fps=", "%.1f" % fps, " tris=", tiles.size()*(tile_mesh.subdivide_width+1)*(tile_mesh.subdivide_depth+1)*2,
		" here=", here.map(func(p): return p.name))
	get_tree().quit()
