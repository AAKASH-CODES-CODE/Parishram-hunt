extends Node
class_name Godot_SocketIORoomClient

## Authoritative Room Manager for Godot 4.x (Flask-SocketIO Server on Render)
## Handles instant room creation, joining, full state updates, and host migration.

signal connected_to_server()
signal disconnected_from_server(reason: String)
signal room_state_updated(room_dict: Dictionary, notification: String)
signal match_started(seed_val: int, hunters: int, props: int)
signal error_received(message: String)
signal kicked_from_room(message: String)

@export var server_url: String = "wss://parishram-hunt.onrender.com/socket.io/?EIO=4&transport=websocket"

var socket: WebSocketPeer = WebSocketPeer.new()
var is_connected: bool = false
var local_sid: String = ""
var current_room: Dictionary = {}
var local_player: Dictionary = {}

func _ready() -> void:
	connect_to_server()

func connect_to_server() -> void:
	print("[SocketIO] Connecting to: ", server_url)
	var err = socket.connect_to_url(server_url)
	if err != OK:
		printerr("[SocketIO] Connection failed: ", err)

func _process(_delta: float) -> void:
	socket.poll()
	var state = socket.get_ready_state()

	if state == WebSocketPeer.STATE_OPEN:
		if not is_connected:
			is_connected = true
			print("[SocketIO] WebSocket Open!")
			connected_to_server.emit()

		while socket.get_available_packet_count() > 0:
			var packet = socket.get_packet().get_string_from_utf8()
			_handle_raw_packet(packet)

	elif state == WebSocketPeer.STATE_CLOSED:
		if is_connected:
			is_connected = false
			var code = socket.get_close_code()
			var reason = socket.get_close_reason()
			print("[SocketIO] Disconnected. Code: ", code, " Reason: ", reason)
			disconnected_from_server.emit(reason)

func _handle_raw_packet(raw: String) -> void:
	# Socket.IO Engine.IO v4 framing:
	# "0{...}" -> Open handshake (sid, pingInterval, pingTimeout)
	# "2" -> Ping from server -> reply with "3" (Pong)
	# "42[\"event_name\", data]" -> Socket.IO message
	if raw.begins_with("0"):
		var json_str = raw.substr(1)
		var json = JSON.parse_string(json_str)
		if json and json.has("sid"):
			local_sid = json["sid"]
			print("[SocketIO] Handshake OK. SID: ", local_sid)

	elif raw.begins_with("2"):
		socket.send_text("3") # Send Pong back to keep alive

	elif raw.begins_with("42"):
		var event_str = raw.substr(2)
		var parsed = JSON.parse_string(event_str)
		if parsed is Array and parsed.size() >= 2:
			var event_name = parsed[0]
			var event_data = parsed[1]
			_route_socketio_event(event_name, event_data)

func _route_socketio_event(event_name: String, data: Variant) -> void:
	match event_name:
		"room_state_update":
			if data is Dictionary and data.has("room"):
				current_room = data["room"]
				var notif = data.get("notification", "")
				_find_local_player()
				print("[SocketIO] Room update: ", current_room.get("room_code"), " Players: ", current_room.get("players", []).size())
				room_state_updated.emit(current_room, notif)

		"match_started":
			if data is Dictionary:
				match_started.emit(data.get("match_seed", 0), data.get("hunters_count", 0), data.get("props_count", 0))

		"error_response":
			if data is Dictionary:
				var msg = data.get("message", "Error")
				error_received.emit(msg)

		"kicked":
			if data is Dictionary:
				current_room = {}
				local_player = {}
				kicked_from_room.emit(data.get("message", "Kicked"))

func _find_local_player() -> void:
	var players = current_room.get("players", [])
	for p in players:
		if p.get("sid") == local_sid:
			local_player = p
			return
	if players.size() > 0:
		local_player = players[0]

# --- Client Intent Actions ---

func send_event(event_name: String, payload: Dictionary) -> void:
	if not is_connected:
		return
	var packet = "42" + JSON.stringify([event_name, payload])
	socket.send_text(packet)

func create_room(player_name: String, max_players: int = 8, preferred_role: String = "Random") -> void:
	send_event("create_room", {
		"player_name": player_name,
		"max_players": max_players,
		"preferred_role": preferred_role
	})

func join_room(room_code: String, player_name: String, preferred_role: String = "Random") -> void:
	send_event("join_room", {
		"room_code": room_code.strip_edges().to_upper(),
		"player_name": player_name,
		"preferred_role": preferred_role
	})

func leave_room() -> void:
	send_event("leave_room", {})
	current_room = {}
	local_player = {}

func toggle_ready(is_ready: bool) -> void:
	send_event("toggle_ready", { "is_ready": is_ready })

func set_role(role: String) -> void:
	send_event("set_role", { "preferred_role": role })

func start_match() -> void:
	send_event("start_match", {})

func kick_player(target_sid: String) -> void:
	send_event("kick_player", { "target_sid": target_sid })
