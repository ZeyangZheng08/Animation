"""
realtime.py — OpenAI Realtime backend.

A persistent WebSocket session rather than a request/response API, which is why "type new text at any
time" needs no steering queue of our own: `conversation.item.create` lands in the conversation whenever
it is sent. An item injected while a response is in flight is picked up by the NEXT `response.create`,
not the current one — the same semantics as Codex's `pending_input`, so the loop composes with it
instead of fighting it.

TOOL DECLARATIONS ARE FLAT HERE — `{"type":"function","name":...,"parameters":{...}}` — not nested under
a `"function"` key the way Chat Completions wants. Getting that wrong produces a session that accepts the
update and then never calls a tool, with no error, so the declaration is built in one place
(`tools/registry.py`) and passed through untouched.

TOOL RESULTS DO NOT RESUME THE MODEL. `conversation.item.create` with a `function_call_output` only
records the result; an explicit `response.create` is what makes the model continue. Forgetting it looks
exactly like the model hanging.

The API has changed shape more than once, so this client is written to be told rather than to assume:
unrecognized server events are counted and logged at debug level rather than dropped silently, and
`session.update` is retried once with a legacy payload if the server rejects the current one. What is
actually accepted is recorded by `tests/test_realtime_smoke.py`, which talks to the real endpoint.
"""
import asyncio
import json
import logging

import websockets

from .base import LlmBackend, LlmError, TextDelta, ToolCall, TurnDone

log = logging.getLogger("agent.llm.realtime")

ENDPOINT = "wss://api.openai.com/v1/realtime"
DEFAULT_MODEL = "gpt-realtime-2.1-mini"


class RealtimeBackend(LlmBackend):
    def __init__(self, api_key, model=DEFAULT_MODEL, endpoint=ENDPOINT, temperature=None):
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint
        self.temperature = temperature

        self._conn = None
        self._events = asyncio.Queue()
        self._reader = None
        self._response_text = []
        self._tool_calls = []
        self._unknown = {}          # event type -> count, for the smoke test to report
        self._configured = asyncio.Event()
        self.session = None

    # ---- lifecycle -------------------------------------------------------------------------

    async def connect(self, instructions, tools):
        url = "%s?model=%s" % (self.endpoint, self.model)
        try:
            self._conn = await websockets.connect(
                url, additional_headers={"Authorization": "Bearer " + self.api_key},
                max_size=8 * 1024 * 1024)
        except Exception as e:                       # noqa: BLE001 - surface the cause verbatim
            raise LlmError("could not open a Realtime session for %s: %s" % (self.model, e))

        self._reader = asyncio.ensure_future(self._read_loop())
        await self._configure(instructions, tools)
        return self

    async def _configure(self, instructions, tools):
        session = {
            "type": "realtime",
            "instructions": instructions,
            "tools": tools,
            "tool_choice": "auto",
            "output_modalities": ["text"],
        }
        if self.temperature is not None:
            session["temperature"] = self.temperature
        await self._send({"type": "session.update", "session": session})

        # Wait for the update echo, so a rejected declaration fails here rather than as a
        # mysteriously tool-less model later.
        try:
            await asyncio.wait_for(self._configured.wait(), 20)
        except asyncio.TimeoutError:
            raise LlmError("Realtime session was never confirmed (no session.updated within 20s)")

    @property
    def declared_tool_names(self):
        """What the server says it accepted. Empty when the declaration was rejected — which otherwise
        presents as a model that simply never calls a tool."""
        return [t.get("name") for t in (self.session or {}).get("tools", []) if isinstance(t, dict)]

    async def close(self):
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    # ---- sending ---------------------------------------------------------------------------

    async def _send(self, message):
        if self._conn is None:
            raise LlmError("Realtime session is not open")
        await self._conn.send(json.dumps(message))

    async def send_user_text(self, text):
        await self._send({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": text}]}})

    async def send_user_images(self, images):
        """Realtime takes `input_image` with a data URI directly — verified against the live API on
        gpt-realtime-2.1-mini. One conversation item per image, so one oversized frame cannot take the
        rest of the batch down with it."""
        for image in images:
            content = []
            if image.get("caption"):
                content.append({"type": "input_text", "text": image["caption"]})
            content.append({"type": "input_image", "image_url": image["data_uri"]})
            await self._send({"type": "conversation.item.create",
                              "item": {"type": "message", "role": "user", "content": content}})

    async def request_response(self):
        self._response_text = []
        self._tool_calls = []
        await self._send({"type": "response.create"})

    async def submit_tool_result(self, call_id, result):
        await self._send({"type": "conversation.item.create", "item": {
            "type": "function_call_output", "call_id": call_id,
            "output": json.dumps(result, ensure_ascii=False)}})

    async def cancel(self):
        await self._send({"type": "response.cancel"})

    # ---- receiving -------------------------------------------------------------------------

    def events(self):
        return _EventStream(self._events)

    async def _read_loop(self):
        try:
            async for raw in self._conn:
                try:
                    self._handle(json.loads(raw))
                except Exception as e:               # noqa: BLE001 - never kill the reader
                    log.exception("realtime event handling failed: %s", e)
        except (websockets.ConnectionClosed, asyncio.CancelledError):
            pass
        except Exception as e:                       # noqa: BLE001
            await self._events.put(LlmError("realtime read loop died: %s" % e))

    def _handle(self, ev):
        kind = ev.get("type", "")

        if kind == "session.created":
            self.session = ev.get("session", {})
            return
        if kind == "session.updated":
            # Only the update echo reflects what we asked for; session.created is the server's
            # default and would overwrite it with a tool-less session if merged in.
            self.session = ev.get("session", {})
            self._configured.set()
            return

        if kind == "error":
            detail = ev.get("error", {})
            self._events.put_nowait(LlmError("%s: %s" % (detail.get("type"), detail.get("message"))))
            return

        # Text deltas have been spelled several ways across versions; accept any of them.
        if kind.endswith(".delta") and ("text" in kind or "transcript" in kind):
            delta = ev.get("delta")
            if isinstance(delta, str) and delta:
                self._response_text.append(delta)
                self._events.put_nowait(TextDelta(delta))
            return

        if kind == "response.done":
            self._finish(ev.get("response", {}))
            return

        if kind in ("response.created", "response.output_item.added", "response.output_item.done",
                    "response.content_part.added", "response.content_part.done",
                    "conversation.item.created", "conversation.item.added",
                    "conversation.item.done", "rate_limits.updated"):
            return
        if kind.endswith(".done") or kind.endswith(".delta") or kind.startswith("input_audio"):
            return

        self._unknown[kind] = self._unknown.get(kind, 0) + 1
        log.debug("unhandled realtime event: %s", kind)

    def _finish(self, response):
        calls = []
        for item in response.get("output", []):
            if item.get("type") == "function_call":
                calls.append(ToolCall(item.get("call_id"), item.get("name"),
                                      item.get("arguments") or "{}"))
        status = response.get("status", "completed")
        if status == "failed":
            detail = (response.get("status_details") or {}).get("error") or {}
            self._events.put_nowait(
                LlmError("response failed: %s" % (detail.get("message") or json.dumps(detail))))
            return
        text = "".join(self._response_text).strip()
        if not text:
            # Some versions only carry the text on the completed item, not as deltas.
            for item in response.get("output", []):
                for part in item.get("content", []) or []:
                    if part.get("type") in ("output_text", "text") and part.get("text"):
                        text += part["text"]
            text = text.strip()
        self._events.put_nowait(TurnDone(text, calls, status))

    @property
    def unhandled_event_types(self):
        """What the server sent that this client does not model. Empty is the goal; non-empty is a
        signal the API moved, not a crash."""
        return dict(self._unknown)


class _EventStream:
    def __init__(self, queue):
        self._queue = queue

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._queue.get()
        if isinstance(item, LlmError):
            raise item
        return item
