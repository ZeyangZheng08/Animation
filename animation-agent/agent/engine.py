"""
engine.py — the runtime channel. This service is the SERVER; the engine connects in as a client.

That direction is not arbitrary. The Unity editor drops its managed state on every script recompile and
on each entry into and exit from play mode, so the side that must reconnect with backoff is the engine.
A client does that naturally; a server would need the agent to chase Unity's lifecycle instead.

Windows reaches this listener over localhost because WSL runs with `networkingMode=mirrored`. Under NAT
it could not, and that is the harder of the two directions — hence mirrored networking is a requirement
of this design, not a tuning choice.

LATENCY SHAPE. The wire is not the floor. Measured round-trip on this link is p50 0.320 ms, but a message
is only actionable at the engine's next `Update`, i.e. up to 16.7 ms at 60 fps — about fifty times the
wire cost. So there is nothing to gain by optimizing the transport further, and the engine side is what
must be shaped to the frame loop (background receive -> concurrent queue -> drain in `Update`). The
timeouts here are set against the frame loop, not the network.

FAILING FAST ON DISCONNECT. When Unity recompiles mid-request the socket drops. Every in-flight call is
then failed immediately with `EngineUnavailable` rather than left to time out, because a stalled agent
turn is far more confusing to debug than an explicit "the engine went away" — and during development
Unity recompiles constantly.
"""
import asyncio
import json
import logging

import websockets

from . import protocol as P

log = logging.getLogger("agent.engine")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8770

# Generous against the 16.7 ms frame floor: covers a stalled editor frame or a long domain reload,
# while still bounded so a wedged engine surfaces as an error instead of hanging the agent turn.
DEFAULT_REQUEST_TIMEOUT = 10.0

# The WebSocket close reason Unity sends when play mode ENDS, as opposed to when it drops the socket
# for a domain reload. Mirrored in AgentLink.cs as `StoppedReason`.
#
# WHY A CLOSE REASON RATHER THAN A MESSAGE. The two cases are indistinguishable from this side — both
# are a socket that went away, and both are usually followed by a reconnect a second later. Only the
# engine knows which it was, and the close frame is the one channel that is guaranteed to still exist
# at the moment it knows. It also costs no message type and no protocol version, and an engine too old
# to send it degrades to exactly the previous behaviour: wait, because Unity is coming back.
STOPPED_REASON = "play_mode_exited"


class EngineUnavailable(RuntimeError):
    """No engine is connected, or it disconnected while a call was in flight."""


class EngineTimeout(TimeoutError):
    """The engine is connected but did not answer in time."""


class EngineError(RuntimeError):
    """The engine answered with an error. `code` is one of `protocol.E`.

    Tools surface this to the model as an ordinary failed tool result rather than raising, so the agent
    can react to it within the same turn.
    """

    def __init__(self, code, message):
        super().__init__("%s: %s" % (code, message))
        self.code = code
        self.msg = message


class EngineLink:
    """One engine at a time — the demo drives a single Unity instance."""

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, request_timeout=DEFAULT_REQUEST_TIMEOUT):
        self.host = host
        self.port = port
        self.request_timeout = request_timeout

        self._server = None
        self._conn = None
        self._pending = {}          # id -> Future
        self._event_handlers = []
        self._closed_handlers = []
        self._next_id = 0
        self._ready = asyncio.Event()
        self.hello = None           # payload of the last engine.hello

    # ---- lifecycle -------------------------------------------------------------------------

    async def start(self):
        self._server = await websockets.serve(
            self._handle, self.host, self.port, ping_interval=20, ping_timeout=20)
        log.info("engine channel listening on ws://%s:%d", self.host, self.port)
        return self

    async def stop(self):
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._fail_pending(EngineUnavailable("agent service stopped"))

    async def __aenter__(self):
        return await self.start()

    async def __aexit__(self, *exc):
        await self.stop()

    # ---- connection state ------------------------------------------------------------------

    @property
    def connected(self):
        """A socket is open. Not the same as ready — see `wait_ready`."""
        return self._conn is not None

    @property
    def ready(self):
        return self._ready.is_set()

    async def wait_ready(self, timeout=None):
        """Block until an engine has connected AND identified itself, and return its hello payload.

        Waiting on the socket alone is not enough: `engine.hello` is the handshake, carrying the scene
        name, the available actors and the engine's protocol version, and it arrives one frame after the
        connection. Returning before it would hand callers a None payload nondeterministically.
        """
        await asyncio.wait_for(self._ready.wait(), timeout)
        return self.hello

    def on_event(self, handler):
        """Register a coroutine `handler(msg_type, data)` for unsolicited engine messages."""
        self._event_handlers.append(handler)
        return handler

    def on_closed(self, handler):
        """Register `handler(deliberate, reason)` for the engine going away.

        `deliberate` is true only when the engine said it was stopping — see STOPPED_REASON. False
        covers a recompile, a domain reload and a crash alike, and none of those is a reason to take
        anything down: Unity reconnects on its own within a second or two.
        """
        self._closed_handlers.append(handler)
        return handler

    # There is no `notify`. Events run engine -> agent only again: the executor sends motion status and
    # gate reports, and nothing on that side displays a turn, so nothing on this side pushes one. A
    # console attaches to the console channel instead, which is a socket of its own.

    # ---- requests --------------------------------------------------------------------------

    async def call(self, msg_type, params=None, timeout=None):
        """Send a request and await its response. Returns the `data` object.

        Raises EngineUnavailable / EngineTimeout / EngineError.
        """
        conn = self._conn
        if conn is None:
            raise EngineUnavailable(
                "no engine connected on ws://%s:%d — open the Unity scene and enter play mode"
                % (self.host, self.port))

        self._next_id += 1
        msg_id = "r%d" % self._next_id
        msg = P.request(msg_type, params, msg_id)

        fut = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        try:
            await conn.send(json.dumps(msg, ensure_ascii=False))
            return await asyncio.wait_for(fut, timeout if timeout is not None else self.request_timeout)
        except asyncio.TimeoutError:
            raise EngineTimeout("engine did not answer %s within %.1fs"
                                % (msg_type, timeout if timeout is not None else self.request_timeout))
        except websockets.ConnectionClosed:
            raise EngineUnavailable("engine disconnected while sending %s" % msg_type)
        finally:
            self._pending.pop(msg_id, None)

    # ---- receive ---------------------------------------------------------------------------

    async def _handle(self, conn):
        if self._conn is not None:
            # Unity reconnecting after a domain reload can arrive before the old socket is reaped.
            log.info("second engine connected; dropping the previous one")
            old = self._conn
            self._conn = None
            await old.close()
            self._fail_pending(EngineUnavailable("engine reconnected"))

        self._conn = conn
        log.info("engine connected")
        try:
            async for raw in conn:
                self._dispatch(raw)
        except websockets.ConnectionClosed:
            pass
        finally:
            if self._conn is conn:
                self._conn = None
                self._ready.clear()
                self.hello = None
            reason = getattr(conn, "close_reason", None) or ""
            deliberate = reason == STOPPED_REASON
            log.info("engine disconnected%s", " (stopped)" if deliberate else "")
            self._fail_pending(EngineUnavailable("engine disconnected"))
            for handler in self._closed_handlers:
                try:
                    handler(deliberate, reason)
                except Exception:                    # noqa: BLE001
                    # This runs in the connection handler's teardown. An exception here would be
                    # swallowed by the server task and the shutdown would silently not happen, which
                    # is the failure this whole path exists to remove.
                    log.exception("a close handler raised")

    def _dispatch(self, raw):
        try:
            msg = json.loads(raw)
            kind = P.validate(msg)
        except (ValueError, P.ProtocolError) as e:
            # Do not kill the connection over one bad frame; the engine may still be usable, and a
            # dropped link during development mostly means "you edited Protocol.cs".
            log.error("dropping malformed engine message: %s", e)
            return

        if kind == "response":
            fut = self._pending.get(msg["id"])
            if fut is None or fut.done():
                log.warning("response to unknown or already-settled request %s", msg["id"])
                return
            if msg["ok"]:
                fut.set_result(msg.get("data", {}))
            else:
                fut.set_exception(EngineError(msg["err"]["code"], msg["err"]["msg"]))
            return

        if kind == "event":
            if msg["type"] == P.T.ENGINE_HELLO:
                self.hello = msg.get("data", {})
                self._ready.set()
                log.info("engine hello: %s", self.hello)
            for handler in self._event_handlers:
                asyncio.ensure_future(handler(msg["type"], msg.get("data", {})))
            return

        log.warning("engine sent a request (%s); this channel is agent-driven", msg.get("type"))

    def _fail_pending(self, exc):
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()
