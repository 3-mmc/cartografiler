extends SceneTree

class Map:
	extends "res://main.gd"
	var zooms = []
	func zoom_by(factor: float, point: Vector2): zooms.append([factor, point])

var failures = 0

func check(ok: bool, message: String):
	if not ok:
		printerr(message)
		failures += 1

func _initialize():
	var map = Map.new()
	var pinch = InputEventMagnifyGesture.new()
	pinch.position = Vector2(321, 123)
	pinch.factor = 1.2
	map._unhandled_input(pinch)
	check(map.zooms.size() == 1 and map.zooms[-1][0] < 1.0, "pinch open must zoom in")
	check(map.zooms[-1][1] == pinch.position, "pinch must anchor at the cursor")
	pinch.factor = 1.0/1.2
	map._unhandled_input(pinch)
	check(is_equal_approx(map.zooms[0][0]*map.zooms[1][0], 1.0), "opposite pinches must cancel")
	pinch.factor = 0.0
	map._unhandled_input(pinch)
	check(map.zooms.size() == 2, "invalid pinch must not change zoom")
	var scroll = InputEventPanGesture.new()
	scroll.position = pinch.position
	scroll.delta = Vector2(0, -0.25)
	map._unhandled_input(scroll)
	check(map.zooms[-1][0] < 1.0 and map.zooms[-1][0] > 0.8, "fractional scroll must smoothly zoom in")
	scroll.delta.y = 0.25
	map._unhandled_input(scroll)
	check(is_equal_approx(map.zooms[-2][0]*map.zooms[-1][0], 1.0), "opposite scrolls must cancel")
	check(map.zooms[-1][1] == scroll.position, "scroll must anchor at the cursor")
	scroll.delta = Vector2(2, 0)
	map._unhandled_input(scroll)
	check(map.zooms.size() == 4, "horizontal scroll must not change zoom")
	map.idle_time = 10.0
	map._input(scroll)
	check(map.idle_time == 0.0, "gestures must keep overlays active")
	var wheel = InputEventMouseButton.new()
	wheel.pressed = true
	wheel.button_index = MOUSE_BUTTON_WHEEL_UP
	map._unhandled_input(wheel)
	check(is_equal_approx(map.zooms[-1][0], 0.8), "mouse wheel zoom must remain available")
	map.free()
	print("GESTURE_TESTS failures=", failures)
	quit(1 if failures else 0)
