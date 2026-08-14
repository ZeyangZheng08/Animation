"""The runtime channel, exercised against a fake engine.

The cases that matter here are the failure ones. A happy round-trip is easy; what wrecks a demo is the
editor recompiling mid-request, or one malformed frame taking the whole link down.
"""
import asyncio

import pytest

from agent import protocol as P
from agent.engine import (STOPPED_REASON, EngineError, EngineLink, EngineTimeout,
                          EngineUnavailable)
from tests.fake_engine import FakeEngine, FakeEngineError

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def link(unused_tcp_port):
    async with EngineLink("127.0.0.1", unused_tcp_port, request_timeout=2.0) as link:
        yield link


def url(link):
    return "ws://%s:%d" % (link.host, link.port)


async def test_hello_and_round_trip(link):
    handlers = {P.T.SCENE_FIND: lambda p: {"objects": [{"id": "aspirin_bottle", "category": p["category"]}]}}
    async with FakeEngine(url(link), handlers, hello={"scene": "EmergencyRoom", "actors": ["CPRNurse"]}):
        hello = await link.wait_ready(timeout=2)
        assert hello["scene"] == "EmergencyRoom"
        assert hello["actors"] == ["CPRNurse"]

        data = await link.call(P.T.SCENE_FIND, {"category": "medication"})
        assert data["objects"][0]["id"] == "aspirin_bottle"


async def test_engine_error_becomes_a_typed_exception(link):
    def boom(_params):
        raise FakeEngineError(P.E.NOT_FOUND, "no such anchor: Bedside")

    async with FakeEngine(url(link), {P.T.SCENE_ANCHORS: boom}):
        await link.wait_ready(timeout=2)
        with pytest.raises(EngineError) as e:
            await link.call(P.T.SCENE_ANCHORS)
        # code and msg are preserved because tools turn them into model-visible tool results
        assert e.value.code == P.E.NOT_FOUND
        assert "Bedside" in e.value.msg


async def test_call_without_an_engine_is_actionable(link):
    with pytest.raises(EngineUnavailable, match="play mode"):
        await link.call(P.T.SCENE_ANCHORS)


async def test_disconnect_fails_in_flight_calls_immediately(link):
    """Unity recompiles constantly during development. A dropped socket must not leave the agent turn
    stalled until the timeout — it must fail now, and say why."""
    engine = await FakeEngine(url(link), {}, frame_delay=5.0).__aenter__()
    await link.wait_ready(timeout=2)

    call = asyncio.ensure_future(link.call(P.T.SCENE_ANCHORS, timeout=30))
    await asyncio.sleep(0.05)
    await engine.close()

    with pytest.raises(EngineUnavailable, match="disconnected"):
        await asyncio.wait_for(call, timeout=2)


async def test_timeout_when_engine_is_connected_but_silent(link):
    async with FakeEngine(url(link), {}, frame_delay=5.0):
        await link.wait_ready(timeout=2)
        with pytest.raises(EngineTimeout, match="did not answer"):
            await link.call(P.T.SCENE_ANCHORS, timeout=0.2)


async def test_one_malformed_frame_does_not_kill_the_link(link):
    handlers = {P.T.SCENE_ANCHORS: lambda p: {"anchors": ["Bedside"]}}
    async with FakeEngine(url(link), handlers) as engine:
        await link.wait_ready(timeout=2)
        await engine.send_raw("{not json")
        await engine.send_raw('{"v": 99, "id": "rX", "ok": true, "data": {}}')
        await asyncio.sleep(0.05)

        assert link.connected
        assert (await link.call(P.T.SCENE_ANCHORS))["anchors"] == ["Bedside"]


async def test_reconnect_replaces_the_previous_engine(link):
    """A domain reload can land the new connection before the old socket is reaped."""
    handlers = {P.T.SCENE_ANCHORS: lambda p: {"anchors": ["first"]}}
    first = await FakeEngine(url(link), handlers).__aenter__()
    await link.wait_ready(timeout=2)

    async with FakeEngine(url(link), {P.T.SCENE_ANCHORS: lambda p: {"anchors": ["second"]}}):
        await asyncio.sleep(0.05)
        assert (await link.call(P.T.SCENE_ANCHORS))["anchors"] == ["second"]
    await first.close()


async def test_events_reach_registered_handlers(link):
    seen = []

    async def handler(msg_type, data):
        seen.append((msg_type, data))

    link.on_event(handler)
    async with FakeEngine(url(link), {}) as engine:
        await link.wait_ready(timeout=2)
        await engine.emit(P.T.MOTION_STATUS, {"phase": "started", "t_engine_ms": 12})
        await asyncio.sleep(0.1)

    # engine.hello is an event too, so handlers see it first, then the status
    assert [t for t, _ in seen] == [P.T.ENGINE_HELLO, P.T.MOTION_STATUS]
    assert seen[1][1] == {"phase": "started", "t_engine_ms": 12}


async def settled(predicate, timeout=2.0):
    """Wait until `predicate()` is true. A close is observed in the connection handler's teardown,
    which is separately scheduled from the test."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


async def test_a_stop_and_a_recompile_are_told_apart(unused_tcp_port):
    """The agent cannot see the difference from its own side -- both are a socket that went away, and
    both are usually followed by a reconnect. Unity says which, in the close reason, because it is the
    only thing that knows. Getting this wrong in one direction leaves a service running after every
    session; in the other it kills one mid-recompile.
    """
    seen = []
    async with EngineLink("127.0.0.1", unused_tcp_port, request_timeout=1.0) as link:
        link.on_closed(lambda deliberate, reason: seen.append((deliberate, reason)))

        engine = FakeEngine("ws://127.0.0.1:%d" % unused_tcp_port)
        await engine.__aenter__()
        await link.wait_ready(timeout=2)
        await engine.close()                       # a recompile: gone, with nothing said
        assert await settled(lambda: seen)
        assert seen[-1][0] is False

        engine = FakeEngine("ws://127.0.0.1:%d" % unused_tcp_port)
        await engine.__aenter__()
        await link.wait_ready(timeout=2)
        await engine.close(reason=STOPPED_REASON)  # play mode ended
        assert await settled(lambda: len(seen) > 1)
        assert seen[-1] == (True, STOPPED_REASON)


async def test_a_raising_close_handler_does_not_swallow_the_others(unused_tcp_port):
    """This runs in the connection handler's teardown, where an exception is swallowed by the server
    task -- so a shutdown would silently not happen, which is the failure the whole path exists to
    remove."""
    seen = []
    async with EngineLink("127.0.0.1", unused_tcp_port, request_timeout=1.0) as link:
        link.on_closed(lambda deliberate, reason: 1 / 0)
        link.on_closed(lambda deliberate, reason: seen.append(deliberate))

        engine = FakeEngine("ws://127.0.0.1:%d" % unused_tcp_port)
        await engine.__aenter__()
        await link.wait_ready(timeout=2)
        await engine.close(reason=STOPPED_REASON)
        assert await settled(lambda: seen)
        assert seen == [True]
