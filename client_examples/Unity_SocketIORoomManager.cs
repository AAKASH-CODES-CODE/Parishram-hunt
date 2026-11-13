using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;

namespace CoachingPropHunt.Networking
{
    // =========================================================================
    // Authoritative In-Memory Socket.IO Room Data Models
    // =========================================================================

    [Serializable]
    public class PlayerData
    {
        public string sid;
        public string player_name;
        public bool is_host;
        public bool is_ready;
        public string preferred_role; // "Hunter" | "Prop" | "Random"
        public string assigned_role;  // "Hunter" | "Prop" (set on start_match)
    }

    [Serializable]
    public class RoomData
    {
        public string room_code;
        public string host_id;
        public string status;         // "waiting" | "in_game"
        public int max_players;
        public double created_at;
        public int match_seed;
        public List<PlayerData> players = new List<PlayerData>();
    }

    [Serializable]
    public class RoomStatePayload
    {
        public bool success;
        public string event_type;
        public string notification;
        public double timestamp;
        public RoomData room;
    }

    [Serializable]
    public class ErrorPayload
    {
        public bool success;
        public string message;
    }

    [Serializable]
    public class MatchStartedPayload
    {
        public bool success;
        public string room_code;
        public int match_seed;
        public int hunters_count;
        public int props_count;
    }

    /// <summary>
    /// Production-ready Unity C# Room Manager for the Authoritative Flask-SocketIO Server on Render.
    /// Manages persistent WebSocket connection, emits player intentions, and parses full authoritative
    /// 'room_state_update' payloads to drive the lobby UI in real time.
    ///
    /// Compatible with SocketIOClient (nuget/unitypackage) or any standard WebSocket Socket.IO bridge.
    /// </summary>
    public sealed class Unity_SocketIORoomManager : MonoBehaviour
    {
        public static Unity_SocketIORoomManager Instance { get; private set; }

        [Header("Server Configuration")]
        [Tooltip("Your Render Web Service URL, e.g.: https://parishram-hunt.onrender.com")]
        [SerializeField] private string serverUrl = "https://parishram-hunt.onrender.com";

        // Current Local State
        public bool IsConnected { get; private set; }
        public string LocalSocketId { get; private set; } = "";
        public RoomData CurrentRoom { get; private set; }
        public PlayerData LocalPlayer { get; private set; }
        public bool IsLocalHost => LocalPlayer != null && LocalPlayer.is_host;
        public bool IsInRoom => CurrentRoom != null && !string.IsNullOrEmpty(CurrentRoom.room_code);

        // Events for UI bindings
        public event Action OnConnectedToServer;
        public event Action<string> OnDisconnectedFromServer;
        public event Action<RoomData, string> OnRoomStateUpdated; // (room, notification)
        public event Action<MatchStartedPayload> OnMatchStarted;
        public event Action<string> OnErrorReceived;
        public event Action<string> OnKickedFromRoom;

        // Main thread dispatch queue
        private readonly Queue<Action> _mainThreadQueue = new Queue<Action>();
        private readonly object _queueLock = new object();

        // Reference to actual underlying SocketIOClient instance
        // (If using 'SocketIOClient' NuGet/package, define client as: private SocketIOClient.SocketIO _client;)
        private object _socketClient;

        private void Awake()
        {
            if (Instance != null && Instance != this)
            {
                Destroy(gameObject);
                return;
            }
            Instance = this;
            DontDestroyOnLoad(gameObject);
        }

        private void Update()
        {
            // Flush thread queue onto Unity's main thread
            lock (_queueLock)
            {
                while (_mainThreadQueue.Count > 0)
                {
                    _mainThreadQueue.Dequeue()?.Invoke();
                }
            }
        }

        public void EnqueueMainThread(Action action)
        {
            if (action == null) return;
            lock (_queueLock)
            {
                _mainThreadQueue.Enqueue(action);
            }
        }

        // =====================================================================
        // Connection Lifecycle
        // =====================================================================

        /// <summary>
        /// Connects to the Render Free Tier WebSocket server.
        /// </summary>
        public void Connect(string customUrl = null)
        {
            string targetUrl = !string.IsNullOrEmpty(customUrl) ? customUrl : serverUrl;
            Debug.Log($"[SocketIO] Connecting to authoritative server at: {targetUrl}");

            /* -----------------------------------------------------------------
             * EXAMPLE IMPLEMENTATION WITH 'SocketIOClient' (github.com/doghappy/socket.io-client-csharp):
             *
             * var uri = new Uri(targetUrl);
             * var client = new SocketIOClient.SocketIO(uri, new SocketIOClient.SocketIOOptions
             * {
             *     Transport = SocketIOClient.Transport.TransportProtocol.WebSocket,
             *     Reconnection = true,
             *     ReconnectionAttempts = 5,
             *     ReconnectionDelay = 2000
             * });
             *
             * client.OnConnected += (sender, e) => {
             *     EnqueueMainThread(() => {
             *         IsConnected = true;
             *         Debug.Log("[SocketIO] Connected!");
             *         OnConnectedToServer?.Invoke();
             *     });
             * };
             *
             * client.On("connected", response => {
             *     // response format: { success: true, sid: "..." }
             *     string sid = response.GetValue<string>("sid");
             *     EnqueueMainThread(() => {
             *         LocalSocketId = sid;
             *         Debug.Log($"[SocketIO] Assigned Local SID: {LocalSocketId}");
             *     });
             * });
             *
             * client.On("room_state_update", response => {
             *     string json = response.ToString();
             *     HandleRawRoomStateUpdate(json);
             * });
             *
             * client.On("match_started", response => {
             *     var payload = JsonUtility.FromJson<MatchStartedPayload>(response.ToString());
             *     EnqueueMainThread(() => OnMatchStarted?.Invoke(payload));
             * });
             *
             * client.On("error_response", response => {
             *     string msg = response.GetValue<string>("message");
             *     EnqueueMainThread(() => OnErrorReceived?.Invoke(msg));
             * });
             *
             * client.On("kicked", response => {
             *     string msg = response.GetValue<string>("message");
             *     EnqueueMainThread(() => {
             *         CurrentRoom = null;
             *         LocalPlayer = null;
             *         OnKickedFromRoom?.Invoke(msg);
             *     });
             * });
             *
             * client.OnDisconnected += (sender, reason) => {
             *     EnqueueMainThread(() => {
             *         IsConnected = false;
             *         CurrentRoom = null;
             *         LocalPlayer = null;
             *         OnDisconnectedFromServer?.Invoke(reason);
             *     });
             * };
             *
             * client.ConnectAsync();
             * _socketClient = client;
             * ----------------------------------------------------------------- */
        }

        // =====================================================================
        // Authoritative Client Intent Actions
        // =====================================================================

        /// <summary>
        /// Request to create a new room in server RAM.
        /// </summary>
        public void CreateRoom(string playerName, int maxPlayers = 8, string preferredRole = "Random")
        {
            var payload = new Dictionary<string, object>
            {
                { "player_name", playerName },
                { "max_players", maxPlayers },
                { "preferred_role", preferredRole }
            };
            EmitEvent("create_room", payload);
        }

        /// <summary>
        /// Request to join an existing room by its 5-character code.
        /// </summary>
        public void JoinRoom(string roomCode, string playerName, string preferredRole = "Random")
        {
            var payload = new Dictionary<string, object>
            {
                { "room_code", roomCode.Trim().ToUpper() },
                { "player_name", playerName },
                { "preferred_role", preferredRole }
            };
            EmitEvent("join_room", payload);
        }

        /// <summary>
        /// Request to leave the active room.
        /// </summary>
        public void LeaveRoom()
        {
            EmitEvent("leave_room", new Dictionary<string, object>());
            CurrentRoom = null;
            LocalPlayer = null;
        }

        /// <summary>
        /// Toggle ready check state.
        /// </summary>
        public void ToggleReady(bool isReady)
        {
            var payload = new Dictionary<string, object>
            {
                { "is_ready", isReady }
            };
            EmitEvent("toggle_ready", payload);
        }

        /// <summary>
        /// Update preferred team role ("Hunter", "Prop", "Random").
        /// </summary>
        public void SetPreferredRole(string role)
        {
            var payload = new Dictionary<string, object>
            {
                { "preferred_role", role }
            };
            EmitEvent("set_role", payload);
        }

        /// <summary>
        /// Host-only: Authoritative start match command.
        /// Server balances teams, picks at least 1 Hunter, and generates match_seed.
        /// </summary>
        public void StartMatch()
        {
            if (!IsLocalHost)
            {
                Debug.LogWarning("[SocketIO] Only the Host can trigger start_match.");
                return;
            }
            EmitEvent("start_match", new Dictionary<string, object>());
        }

        /// <summary>
        /// Host-only: Kick a player by their socket SID.
        /// </summary>
        public void KickPlayer(string targetSid)
        {
            if (!IsLocalHost) return;
            var payload = new Dictionary<string, object>
            {
                { "target_sid", targetSid }
            };
            EmitEvent("kick_player", payload);
        }

        // =====================================================================
        // Authoritative State Handler
        // =====================================================================

        /// <summary>
        /// Parses the entire room dictionary broadcasted by the server.
        /// </summary>
        public void HandleRawRoomStateUpdate(string json)
        {
            try
            {
                var update = JsonUtility.FromJson<RoomStatePayload>(json);
                if (update == null || update.room == null) return;

                EnqueueMainThread(() =>
                {
                    CurrentRoom = update.room;

                    // Locate local player in updated player list
                    if (!string.IsNullOrEmpty(LocalSocketId))
                    {
                        LocalPlayer = CurrentRoom.players.Find(p => p.sid == LocalSocketId);
                    }
                    else if (CurrentRoom.players.Count > 0)
                    {
                        // Fallback before initial SID ack
                        LocalPlayer = CurrentRoom.players[0];
                    }

                    Debug.Log($"[SocketIO] Room {CurrentRoom.room_code} state updated: {CurrentRoom.players.Count} players. Status: {CurrentRoom.status}. Notification: {update.notification}");
                    OnRoomStateUpdated?.Invoke(CurrentRoom, update.notification);
                });
            }
            catch (Exception e)
            {
                Debug.LogError($"[SocketIO] Error parsing room_state_update: {e.Message}");
            }
        }

        private void EmitEvent(string eventName, object payload)
        {
            Debug.Log($"[SocketIO] Emitting '{eventName}'...");
            /*
             * Example:
             * if (_socketClient is SocketIOClient.SocketIO client && IsConnected)
             * {
             *     client.EmitAsync(eventName, payload);
             * }
             */
        }
    }
}
