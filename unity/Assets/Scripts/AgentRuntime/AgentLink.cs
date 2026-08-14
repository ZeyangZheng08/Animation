using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;

namespace AgentRuntime
{
    /// <summary>
    /// The runtime channel. Unity is the CLIENT and the agent service is the server.
    ///
    /// That direction is not arbitrary. The editor drops all managed state on every script recompile and
    /// on each entry into and exit from play mode, so the side that must reconnect with backoff is this
    /// one. A client does that naturally; a server would need the agent to track Unity's lifecycle.
    ///
    /// SHAPED TO THE FRAME LOOP, NOT TO THE WIRE. Measured round trip on this link is p50 0.320 ms, but a
    /// message is only actionable at the next Update — up to 16.7 ms at 60 fps, about fifty times the
    /// wire cost. So there is nothing to win by optimising serialisation, and everything to win by never
    /// touching the scene graph off the main thread. Receive happens on a background task, lands in a
    /// concurrent queue, and is drained inside Update where handlers may touch anything.
    ///
    /// The drain is BUDGETED. A burst of messages must not starve a frame: whatever is left over waits
    /// for the next one, which costs 16.7 ms and is invisible, where a dropped frame is not.
    ///
    /// LEAVING PLAY MODE IS NOT THE SAME AS A RECOMPILE, and the agent cannot tell them apart from its
    /// side: both drop the socket, and both are followed by a reconnect on almost every occasion. So
    /// the difference is stated here, where it is known — OnApplicationQuit fires when play mode ends
    /// and does NOT fire for a domain reload — and travels as the WebSocket close reason, which costs
    /// no new message type and no protocol version. An agent that gets `play_mode_exited` shuts its
    /// consoles down; one that gets an abrupt close keeps listening, because Unity is coming back.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class AgentLink : MonoBehaviour
    {
        [Header("Agent service")]
        [Tooltip("The agent runs the WebSocket server; this component connects into it.")]
        [SerializeField] private string url = "ws://127.0.0.1:8770";

        [Tooltip("Messages handled per frame. The remainder waits one frame rather than dropping one.")]
        [SerializeField] private int drainBudget = 32;

        [Header("Wiring")]
        [SerializeField] private SceneQueryService sceneQuery;
        [SerializeField] private AgentCharacter[] characters;

        /// <summary>The close reason that tells the agent this was deliberate. Mirrored in
        /// agent/engine.py; changing it on one side alone leaves consoles hanging after a stop.</summary>
        public const string StoppedReason = "play_mode_exited";

        private readonly ConcurrentQueue<string> _incoming = new ConcurrentQueue<string>();
        private readonly ConcurrentQueue<string> _outgoing = new ConcurrentQueue<string>();
        private CancellationTokenSource _cancel;
        private Task _pump;
        private ClientWebSocket _socket;
        private volatile bool _connected;
        private volatile bool _helloPending;
        private volatile bool _stopping;

        public bool Connected { get { return _connected; } }

        private void OnEnable()
        {
            // THE PLAYER LOOP MUST RUN WHILE THE EDITOR IS IN THE BACKGROUND. Every request is answered
            // on the main thread out of Update, so with the default setting an unfocused Editor stops at
            // frame 2 and every scene query times out while the socket looks perfectly healthy -- the
            // handshake completes, because the pump is a background task, and nothing after it does.
            // The agent drives this from another process by design, so it is never the focused window.
            Application.runInBackground = true;

            _cancel = new CancellationTokenSource();
            _pump = Task.Run(() => PumpAsync(_cancel.Token));
        }

        /// <summary>Fires when play mode ends, and NOT on a domain reload. That asymmetry is the whole
        /// signal: it is the one moment Unity knows the run is over rather than interrupted.</summary>
        private void OnApplicationQuit()
        {
            _stopping = true;
            SayGoodbye();
        }

        private void OnDisable()
        {
            // Fires on recompile and on leaving play mode, which is exactly when the socket must go.
            // On a stop the close frame has already gone out above; on a recompile it deliberately
            // has not, and the agent reads that silence as "coming back".
            if (_cancel != null) { _cancel.Cancel(); _cancel = null; }
            _connected = false;
            _socket = null;

            // Hands let go and carried props go back where they were picked up. Nothing else does
            // this: IkBinder's own teardown hook never fires under the composer, and an object
            // parented to a hand bone otherwise survives a domain reload welded to a skeleton whose
            // plan did not. StopAll had no caller at all until here.
            for (int i = 0; characters != null && i < characters.Length; i++)
            {
                if (characters[i] != null) characters[i].StopAll();
            }
        }

        /// <summary>Close the socket with a reason, before the pump is cancelled out from under it.
        ///
        /// Blocking in OnApplicationQuit is allowed and this one is bounded: a close frame on
        /// localhost lands in well under a millisecond, and the wait is capped so a wedged socket
        /// costs a quarter of a second on the way out rather than hanging the editor. Timing out
        /// degrades to exactly the old behaviour — an abrupt close, which the agent treats as a
        /// recompile — so the failure mode is a console that stays open, not one that cannot.
        /// </summary>
        private void SayGoodbye()
        {
            ClientWebSocket socket = _socket;
            if (socket == null || socket.State != WebSocketState.Open) return;
            try
            {
                socket.CloseOutputAsync(WebSocketCloseStatus.NormalClosure, StoppedReason,
                                        CancellationToken.None).Wait(250);
            }
            catch (Exception e)
            {
                Debug.Log("[AgentLink] could not say goodbye: " + e.Message);
            }
        }

        private void Update()
        {
            // The handshake is built HERE, not in the pump. SceneManager.GetActiveScene and
            // Application.unityVersion are main-thread only, and calling them from the socket task
            // throws every reconnect attempt -- which presents as "connects, then never says hello".
            // The pump only raises a flag; everything that touches engine state happens on this side.
            if (_helloPending)
            {
                _helloPending = false;
                _outgoing.Enqueue(Protocol.Event(Protocol.EngineHello, HelloPayload()));
            }

            int budget = drainBudget;
            string json;
            while (budget-- > 0 && _incoming.TryDequeue(out json))
            {
                Dispatch(json);
            }
        }

        /// <summary>Push an unsolicited event (motion status, gate report, a typed instruction). Safe
        /// from the main thread.</summary>
        public void Emit(string type, object data)
        {
            _outgoing.Enqueue(Protocol.Event(type, data));
        }

        // ---- dispatch, on the main thread ------------------------------------------------------

        private void Dispatch(string json)
        {
            Inbound inbound;
            try
            {
                inbound = Protocol.Parse(json);
            }
            catch (Exception e)
            {
                // One bad frame must not take the link down; during development it almost always means
                // somebody edited one side of the contract and not the other.
                Debug.LogError("[AgentLink] dropping malformed message: " + e.Message);
                return;
            }

            if (!inbound.IsRequest)
            {
                // Nothing on this side displays a turn any more -- consoles attach to the agent's own
                // console channel instead. The classification stays because it is what makes an
                // unexpected frame a dropped event rather than a misparsed request.
                return;
            }

            Request request = inbound.Request;
            try
            {
                object data = Handle(request);
                _outgoing.Enqueue(Protocol.Ok(request.Id, data));
            }
            catch (AgentRequestException e)
            {
                _outgoing.Enqueue(Protocol.Error(request.Id, e.Code, e.Message));
            }
            catch (Exception e)
            {
                Debug.LogException(e);
                _outgoing.Enqueue(Protocol.Error(request.Id, Protocol.Err.Internal, e.Message));
            }
        }

        private object Handle(Request request)
        {
            switch (request.Type)
            {
                case Protocol.SceneFind:
                    RequireScene();
                    return sceneQuery.Find(request);
                case Protocol.SceneDescribe:
                    RequireScene();
                    return sceneQuery.Describe(request.Str("object_id"));
                case Protocol.SceneAnchors:
                    RequireScene();
                    return sceneQuery.Anchors();
                case Protocol.ScenePosition:
                    RequireScene();
                    return sceneQuery.Position(request);
                case Protocol.MotionAssemble:
                    return Assemble(request);
                case Protocol.MotionLocomote:
                    RequireScene();
                    return RequireCharacter(request.Str("character")).Locomote(request, sceneQuery);
                case Protocol.GateRun:
                    return RequireCharacter(request.Str("character")).GateReport();
                default:
                    throw new AgentRequestException(Protocol.Err.UnknownType,
                        "this executor does not implement " + request.Type);
            }
        }

        private void RequireScene()
        {
            if (sceneQuery == null)
            {
                throw new AgentRequestException(Protocol.Err.NotReady,
                    "no SceneQueryService is wired to this AgentLink");
            }
        }

        private object Assemble(Request request)
        {
            return RequireCharacter(request.Str("character")).Apply(request, sceneQuery, this);
        }

        private AgentCharacter RequireCharacter(string characterId)
        {
            AgentCharacter character = FindCharacter(characterId);
            if (character == null)
            {
                throw new AgentRequestException(Protocol.Err.NotFound,
                    "no character '" + characterId + "'; available: " + string.Join(", ", CharacterIds()));
            }
            return character;
        }

        private AgentCharacter FindCharacter(string id)
        {
            if (characters == null) return null;
            // An unnamed character is meaningful when there is exactly one — the demo drives one nurse
            // and making the model name it correctly buys nothing.
            if (string.IsNullOrEmpty(id)) return characters.Length == 1 ? characters[0] : null;
            for (int i = 0; i < characters.Length; i++)
            {
                if (characters[i] != null && characters[i].Id == id) return characters[i];
            }
            return null;
        }

        private List<string> CharacterIds()
        {
            List<string> ids = new List<string>();
            if (characters != null)
            {
                for (int i = 0; i < characters.Length; i++)
                {
                    if (characters[i] != null) ids.Add(characters[i].Id);
                }
            }
            return ids;
        }

        private object HelloPayload()
        {
            return new Dictionary<string, object>
            {
                { "scene", UnityEngine.SceneManagement.SceneManager.GetActiveScene().name },
                { "unity", Application.unityVersion },
                { "protocol", Protocol.Version },
                { "characters", CharacterIds() },
                // WHAT EACH OF THEM IS CALLED. An instruction names a person -- "Jill, walk to the
                // patient" -- and the protocol names an id. With one character the gap did not exist,
                // because whatever was asked for was the only answer. With three it does, and the
                // agent cannot close it without being told: nothing about `chr:CPRNurse` says Jill.
                // Sent in the handshake so resolving a name costs no round trip.
                { "character_names", CharacterNames() },
                { "objects", sceneQuery == null ? 0 : sceneQuery.EntryCount }
            };
        }

        private Dictionary<string, string> CharacterNames()
        {
            Dictionary<string, string> names = new Dictionary<string, string>();
            for (int i = 0; characters != null && i < characters.Length; i++)
            {
                if (characters[i] != null) names[characters[i].Id] = characters[i].DisplayName;
            }
            return names;
        }

        // ---- the socket, off the main thread ---------------------------------------------------

        private async Task PumpAsync(CancellationToken token)
        {
            int backoffMs = 250;
            while (!token.IsCancellationRequested)
            {
                ClientWebSocket socket = null;
                try
                {
                    socket = new ClientWebSocket();
                    await socket.ConnectAsync(new Uri(url), token);
                    _socket = socket;          // so OnApplicationQuit can close it with a reason
                    _connected = true;
                    backoffMs = 250;

                    // The hello is the handshake: the agent waits for it, not for the socket, because it
                    // carries the scene, the characters and the protocol version. Update builds it.
                    _helloPending = true;

                    Task send = SendLoop(socket, token);
                    await ReceiveLoop(socket, token);
                    await send;
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (Exception e)
                {
                    Debug.Log("[AgentLink] " + e.Message + " — retrying in " + backoffMs + "ms");
                }
                finally
                {
                    _connected = false;
                    if (_socket == socket) _socket = null;
                    if (socket != null) socket.Dispose();
                }

                if (token.IsCancellationRequested || _stopping) break;
                try { await Task.Delay(backoffMs, token); } catch (OperationCanceledException) { break; }
                backoffMs = Mathf.Min(backoffMs * 2, 4000);
            }
        }

        private async Task ReceiveLoop(ClientWebSocket socket, CancellationToken token)
        {
            byte[] buffer = new byte[64 * 1024];
            StringBuilder message = new StringBuilder();
            while (socket.State == WebSocketState.Open && !token.IsCancellationRequested)
            {
                WebSocketReceiveResult result = await socket.ReceiveAsync(
                    new ArraySegment<byte>(buffer), token);
                if (result.MessageType == WebSocketMessageType.Close)
                {
                    await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "bye", token);
                    return;
                }
                message.Append(Encoding.UTF8.GetString(buffer, 0, result.Count));
                if (result.EndOfMessage)
                {
                    _incoming.Enqueue(message.ToString());
                    message.Length = 0;
                }
            }
        }

        private async Task SendLoop(ClientWebSocket socket, CancellationToken token)
        {
            while (socket.State == WebSocketState.Open && !token.IsCancellationRequested)
            {
                string json;
                if (_outgoing.TryDequeue(out json))
                {
                    byte[] bytes = Encoding.UTF8.GetBytes(json);
                    await socket.SendAsync(new ArraySegment<byte>(bytes),
                        WebSocketMessageType.Text, true, token);
                }
                else
                {
                    await Task.Delay(4, token);
                }
            }
        }
    }

    /// <summary>A failure the agent should see as a typed error rather than a stack trace.</summary>
    public sealed class AgentRequestException : Exception
    {
        public readonly string Code;

        public AgentRequestException(string code, string message) : base(message)
        {
            Code = code;
        }
    }
}
