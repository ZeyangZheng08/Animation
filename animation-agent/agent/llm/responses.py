"""
responses.py — the Responses API backend, and the default for reasoning models.

WHY THIS EXISTS AND CHAT DOES NOT SUFFICE. `gpt-5.6-luna` refuses function tools on
/v1/chat/completions outright:

    Function tools with reasoning_effort are not supported for gpt-5.6-luna in
    /v1/chat/completions. To use function tools, use /v1/responses or set reasoning_effort to 'none'.

The second option is not an option: the reasoning is the reason for choosing the model, and this task
is four to ten dependent tool calls deep. So reasoning models go through /v1/responses.

TOOL DECLARATIONS ARE FLAT HERE — `{"type": "function", "name": ..., "parameters": ...}` — the same
shape the Realtime API wants and the opposite of Chat's nested one. The registry emits flat, so unlike
`chat.py` this backend passes them through untouched.

A persistent session is faked the same way `chat.py` does it: keep the input list, replay it. The one
difference that matters is that reasoning items come back in `output` and MUST be replayed alongside
the function calls, or the model loses the thread it was holding between calls.
"""
import asyncio
import json

from .base import LlmBackend, LlmError, TextDelta, ToolCall, TurnDone

DEFAULT_MODEL = "gpt-5.6-luna"


class ResponsesBackend(LlmBackend):
    def __init__(self, api_key, model=DEFAULT_MODEL, max_output_tokens=8192, reasoning_effort=None):
        self.api_key = api_key
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.reasoning_effort = reasoning_effort
        self.instructions = ""
        self.input = []
        self._events = asyncio.Queue()
        self._tools = []
        self._client = None

    async def connect(self, instructions, tools):
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise LlmError("the responses arm needs the `openai` package")
        self._client = AsyncOpenAI(api_key=self.api_key)
        self.instructions = instructions
        self.input = []
        # Flat, and passed through as the registry emits them.
        self._tools = [dict(t) for t in tools]
        return self

    @property
    def declared_tool_names(self):
        return [t["name"] for t in self._tools]

    async def send_user_text(self, text):
        self.input.append({"role": "user", "content": [{"type": "input_text", "text": text}]})

    async def send_user_images(self, images):
        parts = []
        for image in images:
            if image.get("caption"):
                parts.append({"type": "input_text", "text": image["caption"]})
            parts.append({"type": "input_image", "image_url": image["data_uri"]})
        if parts:
            self.input.append({"role": "user", "content": parts})

    async def request_response(self):
        kwargs = {
            "model": self.model,
            "instructions": self.instructions,
            "input": self.input,
            "max_output_tokens": self.max_output_tokens,
        }
        if self._tools:
            kwargs["tools"] = self._tools
        if self.reasoning_effort:
            kwargs["reasoning"] = {"effort": self.reasoning_effort}
        try:
            response = await self._client.responses.create(**kwargs)
        except Exception as e:                       # noqa: BLE001 - surface the cause verbatim
            await self._events.put(LlmError("responses call failed: %s" % e))
            return

        text_parts, calls = [], []
        for item in response.output:
            kind = getattr(item, "type", None)
            if kind == "function_call":
                calls.append(ToolCall(item.call_id, item.name, item.arguments))
            elif kind == "message":
                for part in getattr(item, "content", None) or []:
                    if getattr(part, "type", None) == "output_text":
                        text_parts.append(part.text)
            # Reasoning items carry no user-visible text but must be replayed; that happens below.
            self.input.append(_as_input(item))

        text = "".join(text_parts)
        if text:
            await self._events.put(TextDelta(text))
        await self._events.put(TurnDone(text, calls, status=getattr(response, "status", "completed")))

    async def submit_tool_result(self, call_id, result):
        self.input.append({"type": "function_call_output", "call_id": call_id,
                           "output": json.dumps(result, ensure_ascii=False)})

    async def cancel(self):
        """No streaming session to abort — the loop's own cancellation is what stops the turn."""

    def events(self):
        return _Stream(self._events)

    async def close(self):
        if self._client is not None:
            await self._client.close()
            self._client = None


def _as_input(item):
    """Turn one output item back into an input item.

    Replaying reasoning items verbatim is not optional for a reasoning model: drop them and each
    round trip starts from scratch, which on a four-call chain reads as the model forgetting what it
    just looked up. The SDK objects round-trip through their own dict form.
    """
    if hasattr(item, "model_dump"):
        return item.model_dump(exclude_none=True)
    return dict(item)


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
