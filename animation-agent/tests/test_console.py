"""The console channel: terminals, over a socket of their own.

What is worth testing is the direction of travel. Text from a console has to reach the same
`Session.submit_text` that stdin calls, and the turn it produces has to come back as events the
terminal can render. Nothing in the loop, the tools or the model changes — that is the claim.
"""
import asyncio
import json

import pytest

from agent import protocol as P
from agent.console import ConsoleServer
from agent.loop import Session
from agent.tools import ToolRegistry
from tests.scripted_llm import ScriptedBackend, calls, says

pytestmark = pytest.mark.asyncio


async def settled(predicate, timeout=2.0):
    """Wait until `predicate()` is true. The server and the session are separately scheduled, so a
    message crossing between them is never observable on the next line."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False

class Terminal:
    """A stand-in for `terminal.py`, speaking the same line-delimited JSON."""

    def __init__(self, port):
        self.port = port
        self.seen = []

    async def __aenter__(self):
        self.reader, self.writer = await asyncio.open_connection("127.0.0.1", self.port)
        self._pump = asyncio.ensure_future(self._read())
        return self

    async def __aexit__(self, *exc):
        self._pump.cancel()
        self.writer.close()

    async def _read(self):
        while True:
            line = await self.reader.readline()
            if not line:
                return
            self.seen.append(json.loads(line.decode("utf-8")))

    async def say(self, text):
        self.writer.write((json.dumps(P.event(P.T.AGENT_INSTRUCT, {"text": text})) + "\n").encode())
        await self.writer.drain()

    async def send_raw(self, raw):
        self.writer.write(raw)
        await self.writer.drain()

    def of_type(self, msg_type):
        return [m for m in self.seen if m["type"] == msg_type]


async def served(port, script, registry=None):
    session = Session(ScriptedBackend(script), registry or ToolRegistry(), "test")
    await session.start()
    server = await ConsoleServer(session, "127.0.0.1", port,
                                 lambda: {"model": "test", "actions": 8}).start()
    return session, server


async def test_a_line_from_a_terminal_runs_a_turn(unused_tcp_port):
    session, server = await served(unused_tcp_port, [says("walking it is")])
    try:
        async with Terminal(unused_tcp_port) as t:
            assert await settled(lambda: t.of_type(P.T.CONSOLE_HELLO)), "no banner on attach"
            assert t.of_type(P.T.CONSOLE_HELLO)[0]["data"]["actions"] == 8

            await t.say("walk to the bedside")
            assert await settled(lambda: t.of_type(P.T.AGENT_REPLY))
            assert session.backend.user_messages == ["walk to the bedside"]
            assert t.of_type(P.T.AGENT_REPLY)[0]["data"]["text"] == "walking it is"
    finally:
        await server.stop()
        await session.close()


async def test_two_terminals_watch_the_same_turn(unused_tcp_port):
    """Zero or more consoles, none of them privileged — that is the whole reason this is not the
    engine channel, which holds exactly one connection on purpose."""
    session, server = await served(unused_tcp_port, [says("done")])
    try:
        async with Terminal(unused_tcp_port) as a, Terminal(unused_tcp_port) as b:
            assert await settled(lambda: server.attached == 2)
            await a.say("go")
            assert await settled(lambda: a.of_type(P.T.AGENT_REPLY) and b.of_type(P.T.AGENT_REPLY)), \
                "the terminal that stayed silent still has to see the turn"
            assert b.of_type(P.T.AGENT_REPLY)[0]["data"]["text"] == "done"
    finally:
        await server.stop()
        await session.close()


async def test_a_terminal_leaving_mid_turn_does_not_take_the_turn_down(unused_tcp_port):
    """Closing the window must not be able to stop the run — that is the point of a detached service."""
    session, server = await served(unused_tcp_port, [says("finished anyway")])
    try:
        t = Terminal(unused_tcp_port)
        await t.__aenter__()
        await t.say("go")
        await t.__aexit__()
        assert await settled(lambda: session.last_turn is not None and session.last_turn.text)
        assert session.last_turn.text == "finished anyway"
    finally:
        await server.stop()
        await session.close()


async def test_a_malformed_line_is_dropped_not_fatal(unused_tcp_port):
    session, server = await served(unused_tcp_port, [says("still here")])
    try:
        async with Terminal(unused_tcp_port) as t:
            await t.send_raw(b"{not json at all\n")
            await t.send_raw(json.dumps({"v": 999, "type": "agent.instruct",
                                         "data": {"text": "wrong version"}}).encode() + b"\n")
            await t.say("this one is fine")
            assert await settled(lambda: t.of_type(P.T.AGENT_REPLY))
            assert session.backend.user_messages == ["this one is fine"]
    finally:
        await server.stop()
        await session.close()


async def test_a_terminal_cannot_send_an_agent_to_console_event(unused_tcp_port):
    """Direction is contract. A console says text; it does not report status."""
    session, server = await served(unused_tcp_port, [says("unused")])
    try:
        async with Terminal(unused_tcp_port) as t:
            await t.send_raw((json.dumps(P.event(P.T.AGENT_STATUS, {"state": "thinking"})) + "\n")
                             .encode())
            await asyncio.sleep(0.15)
            assert session.backend.user_messages == []
    finally:
        await server.stop()
        await session.close()


async def test_stop_from_a_terminal_interrupts(unused_tcp_port):
    hold = asyncio.Event()
    backend = ScriptedBackend([says("never gets here")], hold=hold)
    session = Session(backend, ToolRegistry(), "test")
    await session.start()
    server = await ConsoleServer(session, "127.0.0.1", unused_tcp_port).start()
    try:
        async with Terminal(unused_tcp_port) as t:
            await t.say("do something long")
            assert await settled(lambda: session.turn is not None)
            await t.say("/stop")
            assert await settled(lambda: backend.cancelled > 0)
    finally:
        hold.set()
        await server.stop()
        await session.close()


# ---- the invariants that outlived the in-scene console -----------------------------------------
#
# These were written against the Unity text box. They are not about that transport at all — they are
# about what a console is — so they moved rather than went out with it.

async def test_a_second_instruction_after_the_first_turn_ends_runs_again(unused_tcp_port):
    """The console is not one-shot. Measured against the live model, the second line typed in produced
    no turn at all, which is the failure this pins."""
    session, server = await served(unused_tcp_port, [says("first"), says("second")])
    try:
        async with Terminal(unused_tcp_port) as t:
            await t.say("do the first thing")
            assert await settled(lambda: len(t.of_type(P.T.AGENT_REPLY)) == 1)
            await t.say("now the second thing")
            assert await settled(lambda: len(t.of_type(P.T.AGENT_REPLY)) == 2), \
                "the second instruction never started a turn"
            assert [m["data"]["text"] for m in t.of_type(P.T.AGENT_REPLY)] == ["first", "second"]
    finally:
        await server.stop()
        await session.close()


async def test_progress_arrives_while_the_turn_runs(unused_tcp_port):
    """Without this a console is a box that goes quiet for twenty seconds."""
    registry = ToolRegistry()
    registry.add("kb_search", "find actions",
                 {"type": "object", "additionalProperties": False,
                  "properties": {"query": {"type": "string"}}},
                 lambda query=None: {"actions": ["walking"]})
    session, server = await served(
        unused_tcp_port, [calls(("kb_search", {"query": "walk"})), says("done")], registry)
    try:
        async with Terminal(unused_tcp_port) as t:
            await t.say("find me a walk")
            assert await settled(lambda: t.of_type(P.T.AGENT_REPLY))
            states = [m["data"]["state"] for m in t.of_type(P.T.AGENT_STATUS)]
            assert states[0] == "thinking"
            assert "tool" in states
            assert next(m["data"]["detail"] for m in t.of_type(P.T.AGENT_STATUS)
                        if m["data"]["state"] == "tool") == "kb_search"
    finally:
        await server.stop()
        await session.close()


async def test_typing_again_mid_turn_steers_rather_than_starting_a_second_turn(unused_tcp_port):
    """Same rule stdin has. A console is another input source, not another mode."""
    hold = asyncio.Event()
    backend = ScriptedBackend([says("first"), says("second")], hold=hold)
    session = Session(backend, ToolRegistry(), "test")
    await session.start()
    server = await ConsoleServer(session, "127.0.0.1", unused_tcp_port).start()
    try:
        async with Terminal(unused_tcp_port) as t:
            await t.say("walk to the bedside")
            assert await settled(lambda: any(m["data"]["state"] == "thinking"
                                             for m in t.of_type(P.T.AGENT_STATUS)))
            await t.say("and take the bottle")
            assert await settled(lambda: any(m["data"]["state"] == "steered"
                                             for m in t.of_type(P.T.AGENT_STATUS)))
            assert session.turn is not None and not session.turn.done()
            hold.set()
            assert await settled(lambda: t.of_type(P.T.AGENT_REPLY))
            assert len(t.of_type(P.T.AGENT_REPLY)) == 1, "steering must not produce a second turn"
    finally:
        await server.stop()
        await session.close()


async def test_a_blank_line_does_nothing(unused_tcp_port):
    session, server = await served(unused_tcp_port, [says("hi")])
    try:
        async with Terminal(unused_tcp_port) as t:
            await t.say("   ")
            await asyncio.sleep(0.1)
            assert session.backend.user_messages == []
    finally:
        await server.stop()
        await session.close()


async def test_a_non_english_line_never_becomes_a_turn(unused_tcp_port):
    """The trace file records `report.prompt` verbatim, so an instruction in another script would land
    in a committed artifact. Refused at the door, where both entry points pass -- `cli.py`'s stdin
    goes through the same `route`, so a guard in a client would only cover one of them."""
    session, server = await served(unused_tcp_port, [says("should not run")])
    try:
        async with Terminal(unused_tcp_port) as t:
            # Built from code points rather than written out: this file is a repository artifact
            # too, and the rule it tests applies to it. The characters are what matter.
            not_english = "".join(chr(c) for c in (0x8D70, 0x5230, 0x684C, 0x524D))
            await t.say(not_english)
            assert await settled(lambda: t.of_type(P.T.AGENT_REPLY))
            assert session.backend.user_messages == [], "the turn must not have started"
            assert "English" in t.of_type(P.T.AGENT_REPLY)[0]["data"]["error"]

            await t.say("walk to the laptop and sit down to type")
            assert await settled(lambda: session.backend.user_messages)
            assert session.backend.user_messages == ["walk to the laptop and sit down to type"]
    finally:
        await server.stop()
        await session.close()


async def test_a_farewell_reaches_every_terminal_before_the_socket_goes(unused_tcp_port):
    """Stopping play mode has to close the windows watching it. The reason travels ahead of the close
    so a terminal can say why it left rather than reporting a lost connection."""
    session, server = await served(unused_tcp_port, [says("idle")])
    try:
        async with Terminal(unused_tcp_port) as a, Terminal(unused_tcp_port) as b:
            assert await settled(lambda: server.attached == 2)
            await server.farewell("Unity left play mode")
            assert await settled(lambda: a.of_type(P.T.CONSOLE_BYE) and b.of_type(P.T.CONSOLE_BYE))
            assert a.of_type(P.T.CONSOLE_BYE)[0]["data"]["reason"] == "Unity left play mode"
            assert server.attached == 0
    finally:
        await server.stop()
        await session.close()


async def test_the_console_speaks_english():
    """Every string a person reads here is English, in both directions. Not a style rule: the turn a
    console produces is written verbatim into the run trace, which is a repository artifact."""
    import agent.console as console_module
    import agent.prompt as prompt_module
    import terminal as terminal_module

    for module in (console_module, prompt_module, terminal_module):
        source = open(module.__file__, encoding="utf-8").read()
        cjk = {ch for ch in source if 0x2E80 <= ord(ch) <= 0x9FFF}
        assert not cjk, "%s carries %r" % (module.__name__, sorted(cjk))

    assert "in English" in prompt_module.INSTRUCTIONS
