"""
chat.py — a Chat Completions backend, so the architecture can be measured apart from one model.

Same `LlmBackend` interface, same tools, same prompt, same loop. The only difference is which model is
answering, which is what makes the comparison worth anything.

WHY THIS EXISTS. The realtime mini is unstable on this task: over three runs of the twelve-case eval, six
cases flipped. That is a fact about the model, and without a second arm there is no way to tell it apart
from a fact about the design. This arm is the control.

A persistent session is faked by keeping the message list and replaying it. The loop cannot tell the
difference, which is the point of the interface being shaped around a session in the first place.

TOOL DECLARATIONS ARE NESTED HERE — `{"type":"function","function":{...}}` — the opposite of the flat
Realtime shape. The registry emits flat, so this backend rewraps; doing it the other way round would put
a wire detail into the tool definitions.
"""
import asyncio
import json

from .base import LlmBackend, LlmError, TextDelta, ToolCall, TurnDone

DEFAULT_MODEL = "gpt-5.5-2026-04-23"


class ChatBackend(LlmBackend):
    def __init__(self, api_key, model=DEFAULT_MODEL, max_output_tokens=4096):
        self.api_key = api_key
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.messages = []
        self._events = asyncio.Queue()
        self._tools = []
        self._client = None

    async def connect(self, instructions, tools):
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise LlmError("the chat arm needs the `openai` package")
        self._client = AsyncOpenAI(api_key=self.api_key)
        self.messages = [{"role": "system", "content": instructions}]
        self._tools = [{"type": "function",
                        "function": {"name": t["name"], "description": t["description"],
                                     "parameters": t["parameters"]}}
                       for t in tools]
        return self

    @property
    def declared_tool_names(self):
        return [t["function"]["name"] for t in self._tools]

    async def send_user_text(self, text):
        self.messages.append({"role": "user", "content": text})

    async def send_user_images(self, images):
        """Chat carries images as `image_url` parts on a USER message. It cannot carry them on a tool
        result, which is why this is a separate call the loop makes right after submitting one."""
        parts = []
        for image in images:
            if image.get("caption"):
                parts.append({"type": "text", "text": image["caption"]})
            parts.append({"type": "image_url", "image_url": {"url": image["data_uri"]}})
        if parts:
            self.messages.append({"role": "user", "content": parts})

    async def request_response(self):
        try:
            response = await self._client.chat.completions.create(
                model=self.model, messages=self.messages, tools=self._tools or None,
                max_completion_tokens=self.max_output_tokens)
        except Exception as e:                       # noqa: BLE001 - surface the cause verbatim
            await self._events.put(LlmError("chat completion failed: %s" % e))
            return

        message = response.choices[0].message
        raw = {"role": "assistant", "content": message.content or ""}
        calls = []
        if message.tool_calls:
            raw["tool_calls"] = [{"id": c.id, "type": "function",
                                  "function": {"name": c.function.name,
                                               "arguments": c.function.arguments}}
                                 for c in message.tool_calls]
            calls = [ToolCall(c.id, c.function.name, c.function.arguments)
                     for c in message.tool_calls]
        self.messages.append(raw)

        if message.content:
            await self._events.put(TextDelta(message.content))
        await self._events.put(TurnDone(message.content or "", calls))

    async def submit_tool_result(self, call_id, result):
        self.messages.append({"role": "tool", "tool_call_id": call_id,
                              "content": json.dumps(result, ensure_ascii=False)})

    async def cancel(self):
        """No streaming session to abort — the loop's own cancellation is what stops the turn."""

    def events(self):
        return _Stream(self._events)

    async def close(self):
        if self._client is not None:
            await self._client.close()
            self._client = None


class _Stream:
    def __init__(self, queue):
        self._queue = queue

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._queue.get()
        if isinstance(item, LlmError):
            raise item
        return item
