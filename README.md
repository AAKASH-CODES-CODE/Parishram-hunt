# Parishram Hunt — Ultra-Fast Authoritative Socket.IO Server

A zero-cost, high-performance, authoritative multiplayer room management backend built on **Python Flask-SocketIO** designed specifically for deployment on **Render's Free Tier** ($0 budget).

---

## Key Features

1. **Persistent WebSockets (`Flask-SocketIO`):** No slow HTTP polling. Instant bi-directional communication with sub-25ms response time.
2. **100% In-Memory RAM Storage:** Zero external databases. All room state, players, and session IDs live in fast Python RAM dictionaries.
3. **Authoritative Server Architecture:** Clients send *intentions*; the server validates rules, balances teams, sets match seeds, and broadcasts the entire authoritative room dictionary via `room_state_update`.
4. **Auto-Disconnect & Instant Host Migration:** If a player closes the app or loses signal, `@socketio.on('disconnect')` cleans them up. If the Host disconnected, the next player is automatically promoted to Host and a notification is broadcast.
5. **Anti-Sleep Keep-Alive Endpoint:** `@app.route('/keep-alive')` responds with HTTP 200 `"I am awake!"` to prevent Render's free tier from sleeping after 15 minutes.

---

## Render Free Tier Deployment Guide (Step-by-Step)

### Step 1: Push the `server` folder to GitHub
1. In this directory (`c:\Users\AAKASH\Desktop\3D\server`), initialize git:
   ```bash
   git init
   git add .
   git commit -m "Initial commit for Parishram Hunt Socket.IO server"
   ```
2. Create a new GitHub repository (e.g. `parishram-prophunt-server`) and push your code:
   ```bash
   git remote add origin https://github.com/<your-username>/parishram-prophunt-server.git
   git branch -M main
   git push -u origin main
   ```

### Step 2: Create a Web Service on Render
1. Go to [dashboard.render.com](https://dashboard.render.com) and click **New +** > **Web Service**.
2. Connect your GitHub repository.
3. Configure the settings:
   - **Name:** `parishram-prop-hunt-server` (or your choice)
   - **Region:** Choose the region closest to you (e.g., *Singapore* or *Frankfurt*)
   - **Branch:** `main`
   - **Runtime:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command (CRITICAL):**
     ```bash
     gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker -w 1 -b 0.0.0.0:$PORT app:app
     ```
   - **Instance Type:** `Free` ($0/month)
4. Click **Create Web Service**. Render will build and deploy your WebSocket server in ~2 minutes!

> [!IMPORTANT]
> **Why `-w 1` is mandatory:**
> Because all room data lives directly in Python's in-memory dictionaries, you must use **exactly 1 worker process (`-w 1`)**. Multiple workers have separate memory heaps, meaning a player on Worker 1 would not see rooms created on Worker 2. One gevent worker can handle thousands of concurrent WebSocket connections easily!

---

## Anti-Sleep Keep-Alive Setup (Free 24/7 Uptime)

Render's free tier spins down after 15 minutes of inactivity. To keep your server awake 24/7 without paying anything:

1. Go to free monitoring service [cron-job.org](https://cron-job.org) or [uptimerobot.com](https://uptimerobot.com).
2. Create a new monitor / cron job:
   - **URL:** `https://<your-render-app-name>.onrender.com/keep-alive`
   - **Method:** `GET`
   - **Schedule / Interval:** Every **10 minutes**
3. Whenever pinged, the server responds with `"I am awake!"` and resets Render's 15-minute inactivity timer.

---

## WebSocket API Specification

### Client Emits (Intentions)

| Event | Payload | Description |
| :--- | :--- | :--- |
| `create_room` | `{"player_name": "Aakash", "max_players": 8, "preferred_role": "Hunter"}` | Creates a room and assigns sender as Host |
| `join_room` | `{"room_code": "XY89A", "player_name": "Player2", "preferred_role": "Prop"}` | Validates and joins room |
| `leave_room` | `{}` | Leaves room; triggers Host Migration if host left |
| `toggle_ready` | `{"is_ready": true}` | Toggles player ready status |
| `set_role` | `{"preferred_role": "Hunter"}` | Updates preferred role ("Hunter", "Prop", "Random") |
| `start_match` | `{}` | Host-only: balances teams, picks at least 1 Hunter, sets `match_seed`, sets `status: 'in_game'` |
| `kick_player` | `{"target_sid": "..."}` | Host-only: removes player from room |
| `ping_check` | `{"client_timestamp": 123456}` | Instant latency test; replies with `pong_check` |

### Server Emits (Authoritative Broadcasts)

| Event | Payload | Description |
| :--- | :--- | :--- |
| `room_state_update` | `{"success": true, "notification": "...", "room": {...}}` | Broadcasts the full room state to all room occupants |
| `match_started` | `{"room_code": "...", "match_seed": 738192, ...}` | Match start alert with synchronized seed |
| `error_response` | `{"success": false, "message": "Room not found."}` | Targeted error message to requesting client |
| `kicked` | `{"message": "You were kicked by the host."}` | Sent to kicked client |

---

## Client Integration Examples

Check the `client_examples/` directory for ready-to-use scripts:
- **Unity C#:** [`client_examples/Unity_SocketIORoomManager.cs`](file:///c:/Users/AAKASH/Desktop/3D/server/client_examples/Unity_SocketIORoomManager.cs)
- **Godot 4.x GDScript:** [`client_examples/Godot_SocketIORoomClient.gd`](file:///c:/Users/AAKASH/Desktop/3D/server/client_examples/Godot_SocketIORoomClient.gd)
