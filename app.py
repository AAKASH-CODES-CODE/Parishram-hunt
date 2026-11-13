# Eventlet monkey patching must happen at the very beginning
try:
    import eventlet
    eventlet.monkey_patch()
    ASYNC_MODE = "eventlet"
except ImportError:
    try:
        import gevent
        ASYNC_MODE = "gevent"
    except ImportError:
        ASYNC_MODE = "threading"

import os
import time
import random
import string
import logging
import threading
from flask import Flask, jsonify, request
from flask_socketio import SocketIO, emit, join_room, leave_room, disconnect
from flask_cors import CORS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("PropHuntServer")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "parishram-hunt-auth-secret-key-2026")
CORS(app, resources={r"/*": {"origins": "*"}})

logger.info(f"Starting Flask-SocketIO with async_mode={ASYNC_MODE}")

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode=ASYNC_MODE,
    ping_timeout=25,
    ping_interval=10,
    logger=False,
    engineio_logger=False
)

# ---------------------------------------------------------------------------
# 100% In-Memory Room Storage (RAM)
# ---------------------------------------------------------------------------
# rooms schema:
# {
#     "ROOM_CODE": {
#         "room_code": "AB79K",
#         "host_id": "socket_session_id",
#         "status": "waiting",           # "waiting" | "in_game"
#         "max_players": 8,
#         "created_at": 1731481200.0,
#         "match_seed": 0,
#         "players": [
#             {
#                 "sid": "socket_session_id",
#                 "player_name": "AakashPro",
#                 "is_host": True,
#                 "is_ready": True,
#                 "preferred_role": "Hunter", # "Hunter" | "Prop" | "Random"
#                 "assigned_role": None       # "Hunter" | "Prop" (assigned upon start_match)
#             }
#         ]
#     }
# }
rooms = {}

# O(1) reverse lookup mapping: sid -> room_code
sid_to_room = {}

# Thread lock to guarantee atomic mutations in concurrent async greenlets/threads
memory_lock = threading.Lock()

SERVER_START_TIME = time.time()


def generate_unique_room_code(length=5):
    """Generates an unambiguous 5-character uppercase alphanumeric room code."""
    # Exclude easily confused characters like 'O', '0', 'I', '1'
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(random.choices(alphabet, k=length))
        if code not in rooms:
            return code


def broadcast_room_state(room_code, notification=""):
    """Authoritative Full State Broadcast: Emits the entire room dictionary to all occupants."""
    if room_code not in rooms:
        return

    payload = {
        "success": True,
        "event_type": "state_update",
        "notification": notification or "",
        "timestamp": time.time(),
        "room": rooms[room_code]
    }
    socketio.emit("room_state_update", payload, to=room_code)
    logger.info(f"Broadcasted room_state_update for room {room_code} (players: {len(rooms[room_code]['players'])})")


def remove_player_from_room(sid, notify_reason="Player left"):
    """
    Safely removes a player by sid, performs automatic Host Migration if needed,
    and broadcasts the updated state or deletes empty rooms.
    """
    with memory_lock:
        room_code = sid_to_room.pop(sid, None)
        if not room_code or room_code not in rooms:
            return None, None

        room = rooms[room_code]
        leaving_player = next((p for p in room["players"] if p["sid"] == sid), None)
        if not leaving_player:
            return None, None

        room["players"].remove(leaving_player)
        leaver_name = leaving_player.get("player_name", "A player")

        # Case 1: Room is now empty -> Delete room immediately from RAM
        if len(room["players"]) == 0:
            del rooms[room_code]
            logger.info(f"Room {room_code} is now empty and was deleted from memory.")
            return room_code, None

        # Case 2: The leaving player was the Host -> Host Migration
        migration_notice = f"{leaver_name} left."
        if leaving_player.get("is_host", False) or room.get("host_id") == sid:
            new_host = room["players"][0]
            new_host["is_host"] = True
            new_host["is_ready"] = True
            room["host_id"] = new_host["sid"]
            migration_notice = f"{leaver_name} left. {new_host['player_name']} is now the Host!"
            logger.info(f"Host Migration in room {room_code}: {new_host['player_name']} (sid={new_host['sid']}) is new host.")

    # Broadcast outside of lock
    broadcast_room_state(room_code, notification=migration_notice)
    return room_code, migration_notice


# ---------------------------------------------------------------------------
# HTTP Routes (Anti-Sleep Keep-Alive & Health Check)
# ---------------------------------------------------------------------------

@app.route("/keep-alive", methods=["GET", "HEAD"])
def keep_alive():
    """
    Ultra-lightweight anti-sleep route for Render Free Tier (< 100 bytes).
    Compatible with cron-job.org, UptimeRobot, and all ping monitors.
    """
    if request.method == "HEAD":
        return "", 200
    return "OK", 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/", methods=["GET"])
def index():
    """Root health check returning server status JSON."""
    return jsonify({
        "status": "online",
        "service": "Parishram Hunt Authoritative Multiplayer Server",
        "version": "1.0.0",
        "async_mode": ASYNC_MODE,
        "uptime_seconds": int(time.time() - SERVER_START_TIME),
        "active_rooms": len(rooms),
        "online_players": len(sid_to_room)
    }), 200


# ---------------------------------------------------------------------------
# WebSocket (Socket.IO) Events
# ---------------------------------------------------------------------------

@socketio.on("connect")
def handle_connect():
    sid = request.sid
    logger.info(f"Client connected: sid={sid} ip={request.remote_addr}")
    emit("connected", {
        "success": True,
        "sid": sid,
        "message": "Connected to Parishram Hunt Authoritative Server."
    })


@socketio.on("disconnect")
def handle_disconnect():
    sid = request.sid
    logger.info(f"Client disconnected: sid={sid}")
    remove_player_from_room(sid, notify_reason="disconnected")


@socketio.on("create_room")
def handle_create_room(data):
    """
    Creates an authoritative room in RAM and assigns the sender as Host.
    Payload: {
        "player_name": "Aakash",
        "max_players": 8,
        "preferred_role": "Hunter"  # optional ("Hunter", "Prop", "Random")
    }
    """
    sid = request.sid
    data = data or {}

    player_name = str(data.get("player_name", "")).strip() or "Host"
    room_name = str(data.get("room_name", "")).strip()
    try:
        max_players = int(data.get("max_players", 8))
    except (ValueError, TypeError):
        max_players = 8
    max_players = max(2, min(16, max_players))

    pref_role = str(data.get("preferred_role", "Random")).capitalize()
    if pref_role not in ["Hunter", "Prop", "Random"]:
        pref_role = "Random"

    # If player is already in another room, remove them first
    if sid in sid_to_room:
        old_room, _ = remove_player_from_room(sid, notify_reason="switching rooms")
        if old_room:
            leave_room(old_room)

    with memory_lock:
        code = generate_unique_room_code()
        display_room_name = room_name if room_name else f"Classroom {code}"
        room_data = {
            "room_code": code,
            "room_name": display_room_name,
            "host_id": sid,
            "status": "waiting",
            "max_players": max_players,
            "created_at": time.time(),
            "match_seed": 0,
            "players": [
                {
                    "sid": sid,
                    "player_name": player_name,
                    "is_host": True,
                    "is_ready": True,
                    "preferred_role": pref_role,
                    "assigned_role": None
                }
            ]
        }
        rooms[code] = room_data
        sid_to_room[sid] = code

    join_room(code)
    logger.info(f"Room {code} created by {player_name} (sid={sid}, max={max_players})")

    # Authoritative full state emit
    broadcast_room_state(code, notification=f"Room {code} created.")
    emit("room_created", {"success": True, "room_code": code})


@socketio.on("join_room")
def handle_join_room(data):
    """
    Validates room existence, capacity, and status before joining.
    Payload: {
        "room_code": "AB79K",
        "player_name": "ProPlayer",
        "preferred_role": "Prop" # optional
    }
    """
    sid = request.sid
    data = data or {}

    target_code = str(data.get("room_code", "")).strip().upper()
    player_name = str(data.get("player_name", "")).strip() or "Student"
    pref_role = str(data.get("preferred_role", "Random")).capitalize()
    if pref_role not in ["Hunter", "Prop", "Random"]:
        pref_role = "Random"

    if not target_code:
        emit("error_response", {"success": False, "message": "Room code cannot be empty."})
        return

    with memory_lock:
        if target_code not in rooms:
            emit("error_response", {"success": False, "message": f"Room '{target_code}' not found."})
            return

        room = rooms[target_code]

        if room["status"] != "waiting":
            emit("error_response", {"success": False, "message": "Match already in progress in this room."})
            return

        if len(room["players"]) >= room["max_players"]:
            emit("error_response", {"success": False, "message": f"Room '{target_code}' is full ({room['max_players']}/{room['max_players']})."})
            return

        # If player was already in another room, clean it up
        if sid in sid_to_room:
            old_room = sid_to_room[sid]
            if old_room != target_code:
                # Leave old room outside lock? We handle inline:
                pass

        # Check if sid is already in this room
        existing = next((p for p in room["players"] if p["sid"] == sid), None)
        if existing:
            existing["player_name"] = player_name
            existing["preferred_role"] = pref_role
        else:
            room["players"].append({
                "sid": sid,
                "player_name": player_name,
                "is_host": False,
                "is_ready": False,
                "preferred_role": pref_role,
                "assigned_role": None
            })
            sid_to_room[sid] = target_code

    join_room(target_code)
    logger.info(f"Player '{player_name}' (sid={sid}) joined room {target_code}")
    broadcast_room_state(target_code, notification=f"{player_name} joined the room.")
    emit("room_joined", {"success": True, "room_code": target_code})


@socketio.on("leave_room")
def handle_leave_room(data=None):
    """Explicit client leave request."""
    sid = request.sid
    room_code, _ = remove_player_from_room(sid, notify_reason="Player left")
    if room_code:
        leave_room(room_code)
        emit("left_room", {"success": True, "message": f"Left room {room_code}"})


@socketio.on("toggle_ready")
def handle_toggle_ready(data):
    """
    Toggles player readiness.
    Payload: { "is_ready": bool }
    """
    sid = request.sid
    data = data or {}
    new_ready = bool(data.get("is_ready", True))

    with memory_lock:
        room_code = sid_to_room.get(sid)
        if not room_code or room_code not in rooms:
            emit("error_response", {"success": False, "message": "You are not inside an active room."})
            return

        room = rooms[room_code]
        player = next((p for p in room["players"] if p["sid"] == sid), None)
        if not player:
            emit("error_response", {"success": False, "message": "Player record not found."})
            return

        # Host is always ready by default, but allows client update
        player["is_ready"] = new_ready
        pname = player["player_name"]

    broadcast_room_state(room_code, notification=f"{pname} is {'READY' if new_ready else 'NOT READY'}")


@socketio.on("set_role")
def handle_set_role(data):
    """
    Updates player preferred role ("Hunter", "Prop", "Random").
    Payload: { "preferred_role": "Hunter" }
    """
    sid = request.sid
    data = data or {}
    role = str(data.get("preferred_role", "Random")).capitalize()
    if role not in ["Hunter", "Prop", "Random"]:
        role = "Random"

    with memory_lock:
        room_code = sid_to_room.get(sid)
        if not room_code or room_code not in rooms:
            emit("error_response", {"success": False, "message": "You are not inside an active room."})
            return

        player = next((p for p in rooms[room_code]["players"] if p["sid"] == sid), None)
        if not player:
            return
        player["preferred_role"] = role

    broadcast_room_state(room_code, notification=f"{player['player_name']} wants {role}")


@socketio.on("start_match")
def handle_start_match(data=None):
    """
    Authoritative Game Start:
    Validates that requester is Host, minimum players are met,
    authoritatively balances teams (guaranteeing at least 1 Hunter),
    generates a synchronized match seed, sets status='in_game', and broadcasts.
    """
    sid = request.sid
    with memory_lock:
        room_code = sid_to_room.get(sid)
        if not room_code or room_code not in rooms:
            emit("error_response", {"success": False, "message": "Room not found."})
            return

        room = rooms[room_code]

        # 1. Host authority check
        if room.get("host_id") != sid:
            emit("error_response", {"success": False, "message": "Only the room Host can start the match."})
            return

        # 2. Check minimum player count
        players = room["players"]
        if len(players) < 1:
            emit("error_response", {"success": False, "message": "Cannot start match without players."})
            return

        # 3. Authoritative Role Balancing:
        # Guarantee at least 1 Hunter. If more than 3 players, calculate ~25-30% Hunters.
        total_count = len(players)
        hunter_target = max(1, total_count // 4) if total_count >= 4 else 1

        hunters = []
        props = []
        unassigned = []

        for p in players:
            pref = p.get("preferred_role", "Random")
            if pref == "Hunter":
                hunters.append(p)
            elif pref == "Prop":
                props.append(p)
            else:
                unassigned.append(p)

        # If too many wanted Hunter, randomly pick hunter_target and move rest to props
        if len(hunters) > hunter_target:
            random.shuffle(hunters)
            overflow = hunters[hunter_target:]
            hunters = hunters[:hunter_target]
            props.extend(overflow)

        # If not enough hunters, pull from unassigned, then from props if still empty
        while len(hunters) < hunter_target and unassigned:
            pick = unassigned.pop(random.randrange(len(unassigned)))
            hunters.append(pick)

        while len(hunters) < hunter_target and props:
            pick = props.pop(random.randrange(len(props)))
            hunters.append(pick)

        # Rest become Props
        props.extend(unassigned)

        for p in hunters:
            p["assigned_role"] = "Hunter"
        for p in props:
            p["assigned_role"] = "Prop"

        # 4. Generate synchronized match seed
        seed = random.randint(100000, 999999)
        room["match_seed"] = seed
        room["status"] = "in_game"

    logger.info(f"Match started in room {room_code}! Seed={seed}, Hunters={len(hunters)}, Props={len(props)}")
    broadcast_room_state(room_code, notification="Match is starting! Roles assigned.")
    socketio.emit("match_started", {
        "success": True,
        "room_code": room_code,
        "match_seed": seed,
        "hunters_count": len(hunters),
        "props_count": len(props)
    }, to=room_code)


@socketio.on("kick_player")
def handle_kick_player(data):
    """
    Host-only kick player authority.
    Payload: { "target_sid": "..." }
    """
    sid = request.sid
    data = data or {}
    target_sid = data.get("target_sid")
    if not target_sid:
        return

    with memory_lock:
        room_code = sid_to_room.get(sid)
        if not room_code or room_code not in rooms:
            return

        room = rooms[room_code]
        if room.get("host_id") != sid:
            emit("error_response", {"success": False, "message": "Only the Host can kick players."})
            return

        if target_sid == sid:
            emit("error_response", {"success": False, "message": "Host cannot kick themselves."})
            return

        target_player = next((p for p in room["players"] if p["sid"] == target_sid), None)
        if not target_player:
            return

        target_name = target_player.get("player_name", "Player")
        room["players"].remove(target_player)
        sid_to_room.pop(target_sid, None)

    # Notify kicked player & broadcast update
    socketio.emit("kicked", {"message": "You were kicked by the room host."}, to=target_sid)
    socketio.server.leave_room(target_sid, room_code)
    broadcast_room_state(room_code, notification=f"{target_name} was kicked by the host.")


@socketio.on("send_chat")
def handle_send_chat(data):
    """
    Lobby & In-Match Text Chat.
    Payload: { "message": "hello team" }
    """
    sid = request.sid
    data = data or {}
    raw_msg = str(data.get("message", "")).strip()
    if not raw_msg:
        return

    # Cap message length to prevent overflow
    msg = raw_msg[:200]

    with memory_lock:
        room_code = sid_to_room.get(sid)
        if not room_code or room_code not in rooms:
            return

        room = rooms[room_code]
        player = next((p for p in room["players"] if p["sid"] == sid), None)
        if not player:
            return

        pname = player.get("player_name", "Player")
        role = player.get("assigned_role") or player.get("preferred_role", "Random")
        is_host = player.get("is_host", False)

    socketio.emit("new_chat", {
        "room_code": room_code,
        "sender_sid": sid,
        "sender_name": pname,
        "sender_role": role,
        "is_host": is_host,
        "message": msg,
        "timestamp": time.time()
    }, to=room_code)


@socketio.on("get_open_rooms")
def handle_get_open_rooms():
    """Returns list of active public rooms in waiting status."""
    open_list = []
    with memory_lock:
        for code, r in rooms.items():
            if r.get("status") == "waiting" and not r.get("is_private", False):
                open_list.append({
                    "room_code": code,
                    "room_name": r.get("room_name", f"Room {code}"),
                    "player_count": len(r.get("players", [])),
                    "max_players": r.get("max_players", 4)
                })
    emit("open_rooms_list", {"rooms": open_list})


@socketio.on("prop_hit")
def handle_prop_hit(data):
    """
    Authoritative Combat Hit Processing.
    Prop Health: 100 max, 25 damage per hit (4 hits to eliminate).
    Payload: {
        "target_sid": "...",
        "damage": 25,
        "muzzle_pos": [x, y, z],
        "impact_pos": [x, y, z]
    }
    """
    sid = request.sid
    data = data or {}
    target_sid = data.get("target_sid")
    damage = int(data.get("damage", 25))
    muzzle_pos = data.get("muzzle_pos", [0, 0, 0])
    impact_pos = data.get("impact_pos", [0, 0, 0])

    with memory_lock:
        room_code = sid_to_room.get(sid)
        if not room_code or room_code not in rooms:
            return

        room = rooms[room_code]
        attacker = next((p for p in room["players"] if p["sid"] == sid), None)
        target = next((p for p in room["players"] if p["sid"] == target_sid), None)
        if not attacker or not target:
            return

        # Ensure health tracking
        if "health" not in target:
            target["health"] = 100

        target["health"] = max(0, target["health"] - damage)
        remaining_hp = target["health"]
        is_fatal = (remaining_hp <= 0)

        attacker_name = attacker.get("player_name", "Hunter")
        target_name = target.get("player_name", "Prop")

    logger.info(f"Combat Hit in room {room_code}: {attacker_name} -> {target_name} (-{damage} HP, remaining={remaining_hp}, fatal={is_fatal})")

    # Broadcast damage update to everyone in room
    socketio.emit("player_damaged", {
        "room_code": room_code,
        "target_sid": target_sid,
        "target_name": target_name,
        "attacker_sid": sid,
        "attacker_name": attacker_name,
        "damage": damage,
        "remaining_hp": remaining_hp,
        "is_fatal": is_fatal,
        "muzzle_pos": muzzle_pos,
        "impact_pos": impact_pos
    }, to=room_code)

    # If fatal (4th bullet), trigger the Cinematic Kill-Cam and elimination event for all clients!
    if is_fatal:
        socketio.emit("trigger_killcam", {
            "room_code": room_code,
            "attacker_sid": sid,
            "attacker_name": attacker_name,
            "victim_sid": target_sid,
            "victim_name": target_name,
            "muzzle_pos": muzzle_pos,
            "impact_pos": impact_pos
        }, to=room_code)
        socketio.emit("player_eliminated", {
            "room_code": room_code,
            "victim_sid": target_sid,
            "victim_name": target_name,
            "killer_sid": sid,
            "killer_name": attacker_name
        }, to=room_code)


@socketio.on("player_update")
def handle_player_update(data):
    """
    15Hz Real-Time Player Snapshot Replication (Bug 5, Bug 6)
    Payload: {
        "px": float, "py": float, "pz": float,
        "yaw": float, "pitch": float,
        "is_moving": bool, "is_grounded": bool,
        "is_morphed": bool, "prop_id": str,
        "hp": int, "is_alive": bool
    }
    """
    sid = request.sid
    room_code = sid_to_room.get(sid)
    if not room_code:
        return
    data = data or {}
    data["sid"] = sid
    # Relay to all other players in this room
    emit("player_moved", data, to=room_code, include_self=False)


@socketio.on("whistle_fired")
def handle_whistle_fired(data):
    """
    Whistle Taunt Spatial Audio & Radar Ping (Bug 7)
    Payload: { "x": float, "y": float, "z": float }
    """
    sid = request.sid
    room_code = sid_to_room.get(sid)
    if not room_code:
        return
    data = data or {}
    data["sid"] = sid
    emit("whistle_broadcast", data, to=room_code, include_self=False)


@socketio.on("blaster_fired")
def handle_blaster_fired(data):
    """
    Hunter Laser Tracer & Muzzle Flash (Bug 8)
    Payload: { "origin": [x, y, z], "direction": [x, y, z], "hit_point": [x, y, z] }
    """
    sid = request.sid
    room_code = sid_to_room.get(sid)
    if not room_code:
        return
    data = data or {}
    data["sid"] = sid
    emit("blaster_broadcast", data, to=room_code, include_self=False)


@socketio.on("stun_burst")
def handle_stun_burst(data):
    """
    Prop Stun Ability AoE Flash (Bug 12)
    Payload: { "x": float, "y": float, "z": float, "radius": float }
    """
    sid = request.sid
    room_code = sid_to_room.get(sid)
    if not room_code:
        return
    data = data or {}
    data["sid"] = sid
    emit("stun_broadcast", data, to=room_code, include_self=False)


@socketio.on("spawn_decoy")
def handle_spawn_decoy(data):
    """
    Prop Decoy Clone Spawn Sync (Bug 13)
    Payload: { "prop_id": str, "x": float, "y": float, "z": float, "yaw": float }
    """
    sid = request.sid
    room_code = sid_to_room.get(sid)
    if not room_code:
        return
    data = data or {}
    data["sid"] = sid
    emit("decoy_broadcast", data, to=room_code, include_self=False)


@socketio.on("voice_data")
def handle_voice_data(data):
    """
    Team Voice Chat Audio Streaming (Bug 14)
    Payload: { "pcm_base64": str, "sample_rate": int }
    """
    sid = request.sid
    room_code = sid_to_room.get(sid)
    if not room_code or room_code not in rooms:
        return
    data = data or {}
    room = rooms[room_code]
    sender = next((p for p in room["players"] if p["sid"] == sid), None)
    if not sender:
        return
    team = sender.get("assigned_role") or sender.get("preferred_role", "Random")
    data["sender_sid"] = sid
    data["sender_name"] = sender.get("player_name", "Player")
    data["team"] = team
    emit("voice_stream", data, to=room_code, include_self=False)


@socketio.on("report_elimination")
def handle_report_elimination(data):
    """
    Elimination and Kill Feed Report (Bug 17)
    Payload: { "victim_sid": str, "killer_name": str }
    """
    sid = request.sid
    room_code = sid_to_room.get(sid)
    if not room_code or room_code not in rooms:
        return
    data = data or {}
    victim_sid = data.get("victim_sid") or sid
    killer_name = data.get("killer_name") or "Hunter"
    room = rooms[room_code]
    victim = next((p for p in room["players"] if p["sid"] == victim_sid), None)
    victim_name = victim.get("player_name", "Prop") if victim else "Prop"

    socketio.emit("player_eliminated", {
        "room_code": room_code,
        "victim_sid": victim_sid,
        "victim_name": victim_name,
        "killer_sid": sid,
        "killer_name": killer_name
    }, to=room_code)


@socketio.on("ping_check")
def handle_ping_check(data=None):
    """Instant ping/pong latency check."""
    client_ts = (data or {}).get("client_timestamp", 0)
    emit("pong_check", {
        "client_timestamp": client_ts,
        "server_timestamp": time.time()
    })


# ---------------------------------------------------------------------------
# Direct Entry Point (for Local Testing)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting development server on port {port}...")
    socketio.run(app, host="0.0.0.0", port=port, debug=False)
