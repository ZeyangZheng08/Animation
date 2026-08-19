"""
llm — the model behind the ReAct loop, behind an interface thin enough to swap.

The loop talks to `LlmBackend` and never to OpenAI directly, so the eval can run the same tools and the
same prompts against a different model. That comparison is itself a result worth reporting: the latency
arm and the reasoning arm answer the same tasks very differently, and the gap is the finding.

ONE DEFAULT, HERE. `DEFAULT_MODEL` is the model the demo is driven with, and every entry point takes it
from this line. It used to be decided per script — the CLI defaulted to the latency arm while the probe
defaulted to the reasoning one — and the cost of that was a live console session that quietly ran the
weak arm and looked like a regression in the code under test.

THE DEFAULT IS THE STREAMING ARM, BECAUSE THE CLAIM IS INTERACTION. Measured on the same instruction,
same tools, same scene:

    gpt-realtime-2.1-mini    3 iterations,  3.7 s   (~1.2 s per round trip)
    gpt-5.6-luna            14 iterations, 41.3 s   (~3.0 s per round trip)

A turn costs iterations x round trip; the tools are sub-millisecond, so the model is the whole budget.
At three seconds a round trip there is no tool surface that reaches a five-second turn — one round trip
and a half is not a turn. So the target picks the arm, and the work is making the fast arm decide
correctly rather than making the careful arm faster. Where it is still wrong, that gap is the finding,
and it belongs in the write-up rather than being hidden by defaulting away from it.
"""
from .base import LlmBackend, LlmError, ToolCall, TurnDone, TextDelta

from .base import DEFAULT_SILENCE_S            # noqa: F401 - re-exported for cli.py

DEFAULT_MODEL = "gpt-realtime-2.1-mini"

# The careful arm. Slower per round trip and better at multi-step retrieval; kept as the comparison and
# as the fallback for anything the streaming arm cannot yet decide.
REASONING_MODEL = "gpt-5.6-luna"
LATENCY_MODEL = DEFAULT_MODEL          # the old name, kept so callers asking for it still get it

__all__ = ["LlmBackend", "LlmError", "ToolCall", "TurnDone", "TextDelta", "backend_for",
           "DEFAULT_MODEL", "LATENCY_MODEL", "REASONING_MODEL"]


def backend_for(model, api_key, transport=None, **kwargs):
    """Pick the endpoint a model actually speaks.

    One place, because there are three and each rejects the others' models. The Realtime endpoint
    answers `invalid_model` for a chat model and closes the socket. Chat Completions refuses function
    tools for a reasoning model outright:

        Function tools with reasoning_effort are not supported for gpt-5.6-luna in
        /v1/chat/completions. To use function tools, use /v1/responses ...

    So: realtime models over the websocket, everything else over /v1/responses, and Chat only when
    asked for by name — it is kept because it is the arm that was measured against, not because
    anything currently needs it.

    The eval runner and the CLI both come through here, so they cannot drift into disagreeing about
    which model runs where.
    """
    transport = transport or ("realtime" if "realtime" in model else "responses")
    if transport == "realtime":
        from .realtime import RealtimeBackend
        return RealtimeBackend(api_key, model=model, **kwargs)
    if transport == "chat":
        from .chat import ChatBackend
        return ChatBackend(api_key, model=model, **kwargs)
    from .responses import ResponsesBackend
    return ResponsesBackend(api_key, model=model, **kwargs)
