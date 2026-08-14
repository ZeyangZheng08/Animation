"""
base.py — what the ReAct loop needs from a model, and nothing more.

The interface is deliberately shaped around a PERSISTENT SESSION rather than a request/response call,
because that is what makes "type new text at any time" work without hand-rolling a steering queue. A
Chat Completions backend fakes the session by replaying a message list; the loop cannot tell the
difference, which is the point.

Events, not return values. `request_response()` starts a response and returns immediately; what the
model produced arrives on `events()`. That is the only shape that lets the submission loop keep
accepting user input while a response is streaming.
"""


class TextDelta:
    """A fragment of assistant text."""

    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text

    def __repr__(self):
        return "TextDelta(%r)" % self.text


class ToolCall:
    """The model wants a tool run. `arguments` is the raw JSON string the model emitted."""

    __slots__ = ("call_id", "name", "arguments")

    def __init__(self, call_id, name, arguments):
        self.call_id = call_id
        self.name = name
        self.arguments = arguments

    def __repr__(self):
        return "ToolCall(%s, %s)" % (self.name, self.call_id)


class TurnDone:
    """One response finished. `tool_calls` is what it asked for; empty means it answered in text.

    `text` is the full assistant message, if any. The loop decides whether to iterate again based on
    whether there were tool calls or whether new user input is pending.
    """

    __slots__ = ("text", "tool_calls", "status")

    def __init__(self, text, tool_calls, status="completed"):
        self.text = text
        self.tool_calls = tool_calls
        self.status = status

    def __repr__(self):
        return "TurnDone(%d tool call(s), status=%s)" % (len(self.tool_calls), self.status)


class LlmError(RuntimeError):
    """The backend failed. Fatal to the turn; the session may or may not survive."""


class LlmBackend:
    """Interface. Implementations: `realtime.RealtimeBackend`, and a scripted one for tests."""

    async def connect(self, instructions, tools):
        """Open the session, set the system instructions and declare the tools."""
        raise NotImplementedError

    async def send_user_text(self, text):
        """Add a user message to the conversation WITHOUT triggering a response.

        Split from `request_response` on purpose: steering appends input to a running turn, and the loop
        decides when to ask for the next response. Merging them would make every injected message start
        a competing response.
        """
        raise NotImplementedError

    async def send_user_images(self, images):
        """Add images to the conversation WITHOUT triggering a response.

        Separate from `submit_tool_result` because neither API can put a picture in a tool result: a
        Chat `role: "tool"` message carries a string, and the Realtime function_call_output carries a
        string. So a tool that produces an image returns its description as the result and the picture
        arrives as a user message right behind it. `images` is a list of
        {"data_uri": ..., "caption": ...}.

        Backends that cannot take images should no-op rather than raise: losing the picture degrades an
        answer, while raising would kill a turn over a nice-to-have.
        """
        raise NotImplementedError

    async def request_response(self):
        """Ask the model to respond to the conversation so far. Returns immediately."""
        raise NotImplementedError

    async def submit_tool_result(self, call_id, result):
        """Hand back one tool result. Does NOT trigger a response — the loop calls
        `request_response` once, after all of a turn's results are in."""
        raise NotImplementedError

    async def cancel(self):
        """Abort an in-flight response. Used by explicit interrupt, never by ordinary steering."""
        raise NotImplementedError

    def events(self):
        """Async iterator of TextDelta / ToolCall / TurnDone."""
        raise NotImplementedError

    async def close(self):
        raise NotImplementedError
