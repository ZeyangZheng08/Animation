"""
fake_engine.py — a stand-in for the Unity executor, so the agent side is testable without an editor.

It speaks the same protocol and nothing else: connect, say hello, answer requests, emit events. What it
deliberately does NOT model is the frame loop — a real engine can only act on a message at its next
`Update`, up to 16.7 ms later. Tests that care about that use `frame_delay`.
"""
import asyncio
import json

import websockets

from agent import protocol as P


class FakeEngine:
    """Connects to the agent's listener and serves canned answers.

    handlers: {message_type: callable(params) -> data}, or a callable raising FakeEngineError to
    produce a protocol error response.
    """

    def __init__(self, url, handlers=None, hello=None, frame_delay=0.0):
        self.url = url
        self.handlers = handlers or {}
        self.hello = hello if hello is not None else {"scene": "TestScene", "actors": ["TestNurse"]}
        self.frame_delay = frame_delay
        self.received = []          # every request seen, for assertions
        self.events = []            # every unsolicited event from the agent, same purpose
        self._conn = None
        self._task = None

    async def __aenter__(self):
        self._conn = await websockets.connect(self.url)
        await self._conn.send(json.dumps(P.event(P.T.ENGINE_HELLO, self.hello)))
        self._task = asyncio.ensure_future(self._serve())
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def close(self, reason=""):
        """Go away. `reason` is the WebSocket close reason, which is how the real executor tells the
        agent that play mode ENDED rather than that it is reloading and will be back."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._conn is not None:
            await self._conn.close(code=1000, reason=reason)
            self._conn = None

    async def emit(self, msg_type, data):
        """Push an unsolicited event, the way motion.status and gate.report arrive."""
        await self._conn.send(json.dumps(P.event(msg_type, data)))

    async def send_raw(self, text):
        """Send something the protocol will reject, to test that a bad frame does not kill the link."""
        await self._conn.send(text)

    async def _serve(self):
        try:
            async for raw in self._conn:
                msg = json.loads(raw)
                # Since v3 the agent also sends events, and an event has no id to answer. Classifying
                # here rather than assuming is the same fix the real executor needed: a stand-in that
                # answers everything would pass tests the engine would fail.
                if P.classify(msg) == "event":
                    self.events.append(msg)
                    continue
                self.received.append(msg)
                if self.frame_delay:
                    await asyncio.sleep(self.frame_delay)
                await self._conn.send(json.dumps(self._answer(msg)))
        except (websockets.ConnectionClosed, asyncio.CancelledError):
            pass

    async def instruct(self, text):
        """Type a line in the scene. The event that starts a turn from the engine side."""
        await self.emit(P.T.AGENT_INSTRUCT, {"text": text})

    def replies(self):
        return [m["data"] for m in self.events if m["type"] == P.T.AGENT_REPLY]

    def statuses(self):
        return [m["data"] for m in self.events if m["type"] == P.T.AGENT_STATUS]

    def _answer(self, msg):
        handler = self.handlers.get(msg["type"])
        if handler is None:
            return P.err(msg["id"], P.E.UNKNOWN_TYPE, "no handler for %s" % msg["type"])
        try:
            return P.ok(msg["id"], handler(msg.get("params", {})))
        except FakeEngineError as e:
            return P.err(msg["id"], e.code, e.msg)


class FakeEngineError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code
        self.msg = msg
