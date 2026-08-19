"""The model connection, without a network.

Two questions, and both of them cost a live session.

When the session to the model ENDS, does whoever is waiting for the next event find out? The reader
task is the queue's only producer, so a reader that returns quietly leaves a turn waiting for ever.

And when the session stays perfectly healthy and the model simply never answers, does anything ever
give up? That is the one that actually bit: a service at zero CPU, the socket to the model
ESTABLISHED and answering pings, no event, no trace line, and `/stop` unable to reach it.
"""
import asyncio

import pytest
import websockets

from agent.llm.base import LlmError, TextDelta
from agent.llm.realtime import RealtimeBackend

pytestmark = pytest.mark.asyncio


class Socket:
    """A connection that yields some frames and then ends, the way one does."""

    def __init__(self, frames=(), ending=None):
        self._frames = list(frames)
        self._ending = ending          # an exception to raise, or None for a clean close

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._frames:
            return self._frames.pop(0)
        if self._ending is not None:
            raise self._ending
        raise StopAsyncIteration

    async def close(self):
        pass


def backend(socket):
    b = RealtimeBackend(api_key="not-used")
    b._conn = socket
    return b


async def drain(b, timeout=2.0):
    """The next thing off the event stream, or a TimeoutError — which is the hang, made finite."""
    stream = b.events()
    return await asyncio.wait_for(stream.__anext__(), timeout)


async def test_a_dropped_session_reaches_the_waiting_turn():
    """websockets pings every 20s and gives up 20s later, so a half-open connection surfaces as
    ConnectionClosed about forty seconds in. That is the detection working; what was missing was
    anybody being told."""
    b = backend(Socket(ending=websockets.ConnectionClosed(None, None)))
    await b._read_loop()
    with pytest.raises(LlmError) as caught:
        await drain(b)
    assert "closed mid-turn" in str(caught.value)


async def test_a_session_that_ends_without_answering_is_also_reported():
    """The same failure without an exception: the peer closed cleanly and said nothing. Silence here
    is indistinguishable from a model that is merely slow, which is exactly how it presented."""
    b = backend(Socket())
    await b._read_loop()
    with pytest.raises(LlmError) as caught:
        await drain(b)
    assert "without answering" in str(caught.value)


async def test_our_own_shutdown_is_not_reported_as_a_fault():
    """`close()` ends the session on purpose. Reporting that as an error would turn every normal exit
    into a failed turn."""
    b = backend(Socket())
    b._closing = True
    await b._read_loop()
    with pytest.raises(asyncio.TimeoutError):
        await drain(b, timeout=0.2)


async def test_frames_still_arrive_before_the_end():
    """The fix must not eat the events that did arrive. A text delta ahead of the close still lands."""
    b = backend(Socket(frames=['{"type":"response.text.delta","delta":"hel"}'],
                       ending=websockets.ConnectionClosed(None, None)))
    await b._read_loop()
    first = await drain(b)
    assert getattr(first, "text", None) == "hel"
    with pytest.raises(LlmError):
        await drain(b)


# ---- and the other half: a session that is fine, answering nothing --------------------------------

async def test_a_model_that_never_answers_ends_the_turn():
    """The failure that prompted the deadline. Nothing is wrong with the connection -- no close, no
    error, no ping timeout -- and no answer is coming. Without a bound this waits for ever, which is
    exactly what a person sees as a frozen terminal."""
    b = backend(Socket(ending=asyncio.CancelledError))     # a reader that is simply not producing
    b.silence_timeout = 0.15
    with pytest.raises(LlmError) as caught:
        await drain(b, timeout=3.0)
    assert "said nothing for" in str(caught.value)


async def test_the_deadline_is_a_silence_bound_not_a_turn_budget():
    """Progress resets it. A turn that keeps producing events is never cut off, however long it runs
    in total -- measured, one correct turn on this setup spent 163.5 s across seven iterations."""
    b = backend(Socket())
    b.silence_timeout = 0.3

    async def dribble():
        stream = b.events()
        got = []
        for _ in range(4):
            await asyncio.sleep(0.15)                      # under the deadline, every time
            b._events.put_nowait(TextDelta("."))
            got.append(await stream.__anext__())
        return got

    assert len(await asyncio.wait_for(dribble(), 3.0)) == 4


async def test_the_deadline_can_be_switched_off():
    """`--model-silence-s 0` is the old behaviour, kept reachable on purpose: a deliberate choice to
    wait for ever should not require editing the source."""
    b = RealtimeBackend(api_key="not-used", silence_timeout=0)
    assert b.silence_timeout is None
    with pytest.raises(asyncio.TimeoutError):
        await drain(b, timeout=0.2)                        # ours, not the backend's


async def test_giving_up_tries_to_cancel_the_response_first():
    """So the session is reusable if it can be. It must not be able to replace the timeout with an
    exception of its own -- by then the session is not obviously working."""
    b = backend(Socket(ending=asyncio.CancelledError))
    b.silence_timeout = 0.1
    asked = []

    async def cancel():
        asked.append(True)
        raise RuntimeError("the session is past helping")

    b.cancel = cancel
    with pytest.raises(LlmError):
        await drain(b, timeout=3.0)
    assert asked == [True], "it has to have tried"
