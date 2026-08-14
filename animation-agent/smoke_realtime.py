#!/usr/bin/env python3
"""
smoke_realtime.py — prove the Realtime backend against the real endpoint.

Not a unit test: it costs money and needs a key, so it is a script, not part of `pytest`. Its job is to
settle by observation what this API version actually accepts, rather than what the docs describe — the
Realtime surface has changed shape more than once.

It answers four things:
  1. does the session open and accept a flat tool declaration?
  2. does the model actually CALL a tool, and with parseable arguments?
  3. does submitting a result plus response.create make it continue?
  4. what event types does the client not model? (empty is the goal)

Usage:  python smoke_realtime.py [--model gpt-realtime-2.1-mini]
"""
import argparse
import asyncio
import json
import sys

from agent import keys
from agent.kbindex import KBIndex
from agent.llm.base import TextDelta, ToolCall, TurnDone
from agent.llm.realtime import DEFAULT_MODEL, RealtimeBackend
from agent.tools import ToolRegistry
from agent.tools import kb as kb_tools

INSTRUCTIONS = (
    "You select character animations for a nurse in a hospital simulation. "
    "Use kb_search to find candidate motions before answering. "
    "Answer in one short sentence naming the action_id you chose."
)


async def drain(backend, registry, budget=6):
    """Run the ReAct cycle until the model stops asking for tools. Returns a trace."""
    trace = []
    for _ in range(budget):
        done = None
        async for event in backend.events():
            if isinstance(event, TextDelta):
                continue
            if isinstance(event, ToolCall):
                continue
            if isinstance(event, TurnDone):
                done = event
                break
        if done is None:
            raise RuntimeError("stream ended without a TurnDone")

        if not done.tool_calls:
            trace.append(("text", done.text))
            return trace

        for call in done.tool_calls:
            result = await registry.dispatch(call.name, call.arguments)
            trace.append(("tool", call.name, json.loads(call.arguments or "{}"),
                          result.get("success")))
            await backend.submit_tool_result(call.call_id, result)
        await backend.request_response()
    trace.append(("text", "<budget exhausted>"))
    return trace


async def main(model):
    kb = KBIndex.load()
    registry = kb_tools.register(ToolRegistry(), kb)
    backend = RealtimeBackend(keys.load_openai_key(), model=model)

    print("== connecting to %s ==" % model)
    await backend.connect(INSTRUCTIONS, registry.declarations())
    print("   session opened; tools declared:", ", ".join(registry.names()))
    declared = [t.get("name") for t in (backend.session or {}).get("tools", [])]
    print("   server echoed tools:", declared or "(none — the declaration was rejected)")

    try:
        for question in ("The nurse should press on the patient's chest repeatedly.",
                         "Now have her walk across the room."):
            print("\n== user: %s" % question)
            await backend.send_user_text(question)
            await backend.request_response()
            for step in await drain(backend, registry):
                if step[0] == "tool":
                    print("   tool  %-14s %-52s -> success=%s"
                          % (step[1], json.dumps(step[2], ensure_ascii=False)[:52], step[3]))
                else:
                    print("   text  %s" % step[1])
    finally:
        unhandled = backend.unhandled_event_types
        print("\n== unmodelled event types: %s" % (unhandled or "none"))
        await backend.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()
    try:
        sys.exit(asyncio.run(main(args.model)))
    except KeyboardInterrupt:
        pass
