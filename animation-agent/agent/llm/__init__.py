"""
llm — the model behind the ReAct loop, behind an interface thin enough to swap.

The loop talks to `LlmBackend` and never to OpenAI directly, so the eval can run the same tools and the
same prompts against a different model. That comparison is itself a result worth reporting: the latency
arm and the reasoning arm answer the same tasks very differently, and the gap is the finding.

ONE DEFAULT, HERE. `DEFAULT_MODEL` is the model the demo is driven with, and every entry point takes it
from this line. It used to be decided per script — the CLI defaulted to the latency arm while the probe
defaulted to the reasoning one — and the cost of that was a live console session that quietly ran the
weak arm and looked like a regression in the code under test.

THE DEFAULT IS THE REASONING ARM, AND IT CHANGED WHEN THE LIBRARY DID. It used to be the streaming
one, on the argument that the claim is interaction and a turn costs iterations times a round trip. That
argument was made against a corpus of eight actions and five tools, where the retrieval was a lookup and
the only real decision was how to arrange two clips.

The library is 2446 actions now and the surface is thirteen tools, of which five answer questions about
motion that the agent has to ASK IN ORDER. Finding a clip is a search whose result has to be read;
whether two clips join is a question about their ends; a posture change is a search, then a ranking,
then a choice between candidates that are geometrically indistinguishable and semantically not. None of
that is one round trip made faster. It is four to ten dependent calls, and a wrong one at step two is a
turn that ends somewhere plausible and wrong.

So correctness per turn is what the default optimises, and latency is the comparison arm rather than the
target. `LATENCY_MODEL` is pinned to `gpt-realtime-2.1-mini` for exactly that: it is the arm the write-up
measures against, and pinning it separately from `DEFAULT_MODEL` is what stopped the two moving together
and destroying the comparison.

THE OLD NUMBERS, KEPT AND LABELLED. Same instruction, same scene, EIGHT actions and the pre-corpus tool
surface:

    gpt-realtime-2.1-mini    3 iterations,  3.7 s   (~1.2 s per round trip)
    gpt-5.6-luna            14 iterations, 41.3 s   (~3.0 s per round trip)

Those are luna-era measurements over a library that no longer exists, so they say nothing about terra
over 2446 records and are here as history rather than as evidence. Re-measuring both arms on the new
surface is what replaces them.
"""
from .base import LlmBackend, LlmError, ToolCall, TurnDone, TextDelta

from .base import DEFAULT_SILENCE_S            # noqa: F401 - re-exported for cli.py

DEFAULT_MODEL = "gpt-5.6-terra"

# The same model, under the name that says WHY it is the default. Both point at one string so a change
# cannot leave them disagreeing about which arm is which.
REASONING_MODEL = DEFAULT_MODEL

# The comparison arm, PINNED. It used to be an alias for DEFAULT_MODEL, which meant the two moved
# together -- and a comparison whose control arm follows the treatment is not a comparison. Naming it
# outright is what keeps the latency measurement about the same model it was about before.
LATENCY_MODEL = "gpt-realtime-2.1-mini"

__all__ = ["LlmBackend", "LlmError", "ToolCall", "TurnDone", "TextDelta", "backend_for",
           "DEFAULT_MODEL", "LATENCY_MODEL", "REASONING_MODEL"]


def backend_for(model, api_key, transport=None, **kwargs):
    """Pick the endpoint a model actually speaks.

    One place, because there are three and each rejects the others' models. The Realtime endpoint
    answers `invalid_model` for a chat model and closes the socket. Chat Completions refuses function
    tools for a reasoning model outright:

        Function tools with reasoning_effort are not supported for gpt-5.6-luna in
        /v1/chat/completions. To use function tools, use /v1/responses ...

    The rule is the NAME, not a table: anything with "realtime" in it goes over the websocket and
    everything else over /v1/responses. So the default landing on a reasoning model needs no entry
    here -- `gpt-5.6-terra` routes itself.

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
