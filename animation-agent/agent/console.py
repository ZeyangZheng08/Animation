"""
console.py — the input sources that are not stdin, and the one way a turn is displayed.

A console is anything that can put a line of text in and watch what comes out. `ConsoleServer` is the
socket terminals attach to; text from it goes to the same `Session.submit_text` that `cli.py` calls
from stdin, so it is not a mode but an input source. Steering works from either, because
`Session._route_text` already folds text into a running turn instead of starting a competing one.

There used to be a second transport here, bridging a text box drawn inside the Unity scene. It is gone:
it cost a quarter of the game view, it could not take a keystroke without depending on which input
backend the project was configured for, and a terminal does everything it did better. The message types
it introduced live on — they were always console events, and now they have the right transport.

WHY THE CONSOLE CHANNEL IS ITS OWN SOCKET, AND NOT THE ENGINE'S. The engine link holds exactly one
connection on purpose: there is one executor, it is a pure reactor, and a second connection displacing
it would take the scene down. Consoles are the opposite in every respect — zero or more of them, each
attaching and detaching whenever a person opens or closes a window, none of them ever answering a
request. Those are different contracts, so they get different sockets. This does not soften the rule
that the two ENGINE channels never merge: this is not an engine channel. Nothing here reaches the
executor, and no code crosses it in either direction — only text in and display events out.

WHY IT IS PLAIN TCP WITH ONE JSON OBJECT PER LINE. The engine channel is a WebSocket because Unity is
on the other end. A console's other end is a terminal, and the client for it should need nothing
installed — one JSON object per line is reachable from the standard library of anything, and readable
with a socket and a text editor when it is not.

WHY THE PROGRESS IS BEST-EFFORT AND THE INSTRUCTION IS NOT. An instruction that fails to arrive is a
turn that never happens, and the user is looking straight at the box they typed it into. A status line
that fails to arrive costs a line of scrollback. So `submit_text` is awaited and the pushes are not
allowed to raise: the display of a turn must never be able to take down the turn.
"""
import asyncio
import json
import logging

from . import protocol as P
from .loop import Ev, Op

log = logging.getLogger("agent.console")

DEFAULT_CONSOLE_PORT = 8771


def is_english(text):
    """Whether a line is written in the alphabet this console works in.

    Latin letters, digits and punctuation only. Deliberately a character-class test rather than
    language detection: the question is not "which language is this" but "will this end up in a
    repository artifact", and every character outside this range answers yes.
    """
    return all(ord(ch) < 0x0250 for ch in text)


NOT_ENGLISH = ("this console works in English — the turn you type is recorded verbatim in the run "
               "trace, which is an English-only artifact. Say it again in English.")


async def route(session, text, source="a console"):
    """Text from any console, into the one session. Returns whether anything was submitted.

    ENGLISH IN AS WELL AS OUT, and refused HERE rather than in a terminal. `cli.py`'s stdin and the
    console socket both arrive through this function on their way to `submit_text`, so one guard
    covers both entry points; putting it in the client would leave the other way in unguarded.

    The reason is not tidiness. `cli.py: turn_recorder` writes `report.prompt` verbatim into
    `_traces/turns.jsonl`, so an instruction in another script lands in a committed artifact, against
    the rule that every repository-resident artifact is English. Stopping it at the door keeps the
    traces clean without anything downstream having to know.
    """
    text = (text or "").strip()
    if not text:
        return False
    if text in ("/stop", "/interrupt"):
        await session.submit(Op(Op.INTERRUPT))
        return True
    if not is_english(text):
        log.info("refused a non-English instruction from %s", source)
        session.notice(NOT_ENGLISH)
        return False
    log.info("instruction from %s: %s", source, text)
    await session.submit_text(text)
    return True


class ConsoleServer:
    """A socket terminals attach to. Zero or more at once; each sees the same turn.

    Deliberately has no request/response half. A console asks the agent for nothing — it says a line
    and it watches. That keeps this file free of the correlation, timeout and reconnect machinery the
    engine link needs, and it is why a client for it fits in one stdlib file.
    """

    def __init__(self, session, host="127.0.0.1", port=DEFAULT_CONSOLE_PORT, banner=None):
        self.session = session
        self.host = host
        self.port = port
        self.banner = banner or (lambda: {})
        self._clients = set()
        self._server = None
        session.on_event(self._from_session)

    async def start(self):
        self._server = await asyncio.start_server(self._serve, self.host, self.port)
        log.info("console channel listening on tcp://%s:%d", self.host, self.port)
        return self

    async def stop(self):
        for writer in list(self._clients):
            writer.close()
        self._clients.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def farewell(self, reason):
        """Tell every attached terminal the run is over, then let go of them.

        A console outlives the window it is shown in — that is what makes the service detachable —
        but it must not outlive the RUN. Stopping play mode in Unity used to leave a terminal sitting
        at a live prompt in front of a scene that no longer existed, and a service behind it that had
        to be found and killed by hand. The message is sent before the sockets close so the reason
        arrives; closing alone would say only that something happened.
        """
        for writer in list(self._clients):
            await self._write(writer, P.T.CONSOLE_BYE, {"reason": reason})
        for writer in list(self._clients):
            try:
                writer.close()
            except OSError:
                pass
        self._clients.clear()

    @property
    def attached(self):
        return len(self._clients)

    # ---- one terminal ----------------------------------------------------------------------

    async def _serve(self, reader, writer):
        peer = writer.get_extra_info("peername")
        self._clients.add(writer)
        log.info("console attached from %s (%d now)", peer, len(self._clients))
        try:
            await self._write(writer, P.T.CONSOLE_HELLO, self.banner())
            while True:
                line = await reader.readline()
                if not line:
                    break
                await self._handle(line, writer)
        except (ConnectionError, OSError):
            pass
        finally:
            self._clients.discard(writer)
            try:
                writer.close()
            except OSError:
                pass
            log.info("console from %s detached (%d left)", peer, len(self._clients))

    async def _handle(self, line, writer=None):
        try:
            msg = json.loads(line.decode("utf-8"))
            P.validate(msg)
        except (ValueError, P.ProtocolError) as e:
            # A MALFORMED LINE IS THE SENDER'S PROBLEM, AND THE SENDER HAS TO BE TOLD.
            #
            # This used to log and drop. That is the right instinct about the SESSION -- there is no
            # turn to fail -- and precisely the wrong one about the person who typed the line.
            # Measured, at a cost of three investigations: `terminal.py` still said `"v": 3` after the
            # contract went to 4, so every instruction typed into the Play-mode window was dropped
            # here. The warning went into a log file nobody was reading, the terminal drew a fresh
            # prompt, and the result looked exactly like an intermittent hang somewhere in the model
            # -- which is what got investigated, three times, while the actual fault was deterministic
            # and one line long.
            #
            # The refusal goes back down the same socket the line came up, so the window that cannot
            # be heard is the window that finds out.
            log.warning("dropped a malformed console message: %s", e)
            await self._refuse(writer, "this console said something the service could not read, so "
                                       "nothing was submitted: %s" % e)
            return
        if msg.get("type") not in P.FROM_CONSOLE_EVENTS:
            log.warning("a console sent %r, which is not its to send", msg.get("type"))
            await self._refuse(writer, "a console may not send %r, so nothing was submitted"
                                       % msg.get("type"))
            return
        await route(self.session, (msg.get("data") or {}).get("text"))

    async def _refuse(self, writer, message):
        """Tell one console why its line went nowhere. Best effort, like every other write here: a
        display that cannot be reached must not be able to take down the work it describes."""
        if writer is None:
            return
        # The shape a console already understands. A new one would render as nothing on any client
        # that has not been taught it, which is the failure this is here to stop.
        await self._write(writer, P.T.AGENT_REPLY, {"text": "", "error": message})

    # ---- session -> every terminal ----------------------------------------------------------

    def _from_session(self, kind, data):
        message = render(kind, data)
        if message is not None:
            asyncio.ensure_future(self._broadcast(*message))

    async def _broadcast(self, msg_type, payload):
        for writer in list(self._clients):
            await self._write(writer, msg_type, payload)

    async def _write(self, writer, msg_type, payload):
        try:
            writer.write(json.dumps(P.event(msg_type, payload), ensure_ascii=False).encode("utf-8"))
            writer.write(b"\n")
            await writer.drain()
        except (ConnectionError, OSError):
            self._clients.discard(writer)     # see the module docstring: display never takes down work


def render(kind, data):
    """One session event, as the message a console shows. Shared by every transport so a turn reads
    the same in a Unity window and in a terminal."""
    if kind == Ev.TURN_STARTED:
        return P.T.AGENT_STATUS, {"state": "thinking", "detail": data.get("text", "")}
    if kind == Ev.STEERED:
        return P.T.AGENT_STATUS, {"state": "steered", "detail": data.get("text", "")}
    if kind == Ev.TEXT:
        # What the model said on its way to the next tool call. The reply only ever carries the last
        # of these, and a turn that took seven iterations made six other decisions.
        return P.T.AGENT_STATUS, {"state": "said", "detail": data.get("text", "")}
    if kind == Ev.TOOL_STARTED:
        # Shown while it runs, so a three-second walk is not three seconds of blank screen. A console
        # that can overwrite a line replaces this with the finished one; one that cannot has both.
        return P.T.AGENT_STATUS, {"state": "tool", "detail": data.get("name", ""),
                                  "call": data.get("call", "")}
    if kind == Ev.TOOL_PROGRESS:
        # A tool talking while it is still running. Same transient row as `tool`, so a walk that takes
        # three seconds says what it is doing instead of showing a frozen line.
        return P.T.AGENT_STATUS, {"state": "tool", "detail": data.get("name", ""),
                                  "call": data.get("detail", "")}
    if kind == Ev.TOOL_FINISHED:
        # Both outcomes now. It used to be failures only — a successful call showed as its own name
        # and nothing else, so a turn read as a list of things attempted rather than of things found.
        return P.T.AGENT_STATUS, {
            "state": "tool_done" if data.get("success") is not False else "tool_failed",
            "detail": data.get("name", ""),
            "call": data.get("call", ""),
            "result": data.get("error") if data.get("success") is False else data.get("result", ""),
            "seconds": data.get("seconds"),
        }
    if kind == Ev.TURN_COMPLETE:
        report = data["report"]
        return P.T.AGENT_REPLY, {"text": report.text or "",
                                 "cancelled": bool(report.cancelled),
                                 "tool_calls": report.tool_calls,
                                 "iterations": report.iterations,
                                 "seconds": round(report.seconds, 1),
                                 # The turn's time, split by what it went on. The headline figure is
                                 # the deciding one: the seconds spent watching the character cross a
                                 # room are not seconds the agent could have gone faster through.
                                 "deciding_s": round(report.decision_seconds(), 1),
                                 "engine_wait_s": round(report.engine_wait_s(), 1),
                                 # Next to the reply, because the reply is the model's account of the
                                 # turn and this is the trace's. When they disagree the reply is the
                                 # one that is wrong.
                                 "generated": report.generated(),
                                 # How long until something moved, which is the number the latency
                                 # target is about — the total also contains whatever was said after.
                                 "motion_at_s": report.motion_at(),
                                 "tools_used": report.tools_used()}
    if kind == Ev.VERDICT:
        # Arrives after the reply it belongs to, because the thing it measures had not happened yet.
        # Shown on its own line rather than folded into the reply: the reply is the model's account and
        # this is the geometry's, and a reader has to be able to see them disagree.
        return P.T.GATE_VERDICT, {"status": "pass" if data.get("success") else "fail",
                                  "check": data.get("name", ""),
                                  "detail": data.get("detail", "")}
    if kind == Ev.ERROR:
        return P.T.AGENT_REPLY, {"text": "", "error": data.get("message", "")}
    return None
