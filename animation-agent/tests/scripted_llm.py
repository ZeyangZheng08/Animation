"""
scripted_llm.py — an LlmBackend that replays a fixed script, so the loop is testable without the API.

The interesting behaviour of the loop is not what the model says; it is when steering gets folded in,
whether a failed tool keeps the turn alive, and whether the budgets bite. All of that is deterministic
given a script.
"""
import asyncio
import inspect
import json

from agent.llm.base import LlmBackend, TextDelta, ToolCall, TurnDone


def says(text):
    return {"text": text, "calls": []}


def calls(*specs, **kwargs):
    """calls(("kb_search", {"query": "x"}), ...) -> one response asking for those tools.

    `text` is what the model said on its way to them, which real responses carry and which the console
    shows as the turn happens -- the reply only ever holds the last iteration's.
    """
    return {"text": kwargs.get("text", ""), "calls": list(specs)}


class ScriptedBackend(LlmBackend):
    """`hold` is an Event the first response waits on, so a test can act while a turn is genuinely
    in flight. Without it the script completes synchronously and every steering test becomes a race."""

    def __init__(self, script, hold=None):
        self.hold = hold
        self.script = list(script)
        self.responses = 0
        self.user_messages = []       # everything the loop sent, in order
        self.user_images = []         # images lifted out of tool results and sent behind them
        self.tool_results = []
        self.cancelled = 0
        self.closed = False
        self._events = asyncio.Queue()
        self._call_seq = 0

    async def connect(self, instructions, tools):
        self.instructions = instructions
        self.tools = tools
        return self

    async def send_user_text(self, text):
        self.user_messages.append(text)

    async def send_user_images(self, images):
        self.user_images.extend(images)

    async def request_response(self):
        if self.hold is not None:
            hold, self.hold = self.hold, None
            await hold.wait()

        if self.responses >= len(self.script):
            step = says("(script exhausted)")
        else:
            step = self.script[self.responses]
        self.responses += 1

        if callable(step):
            step = step(self)
            if inspect.isawaitable(step):
                step = await step

        tool_calls = []
        for name, arguments in step["calls"]:
            self._call_seq += 1
            tool_calls.append(ToolCall("call_%d" % self._call_seq, name,
                                       json.dumps(arguments) if not isinstance(arguments, str)
                                       else arguments))
        if step["text"]:
            await self._events.put(TextDelta(step["text"]))
        await self._events.put(TurnDone(step["text"], tool_calls))

    async def submit_tool_result(self, call_id, result):
        self.tool_results.append((call_id, result))

    async def cancel(self):
        self.cancelled += 1

    def events(self):
        return _Stream(self._events)

    async def close(self):
        self.closed = True


class _Stream:
    def __init__(self, queue):
        self._queue = queue

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self._queue.get()
