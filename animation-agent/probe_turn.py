#!/usr/bin/env python3
"""probe_turn.py — run one turn and print the tool trace, arguments included.

Exists because "the model called kb_search seven times" is not a diagnosis. What it searched FOR is.
"""
import argparse
import asyncio
import json
import sys

from agent import keys, llm, prompt
from agent.kbindex import KBIndex
from agent.loop import Session
from agent.tools import ToolRegistry
from agent.tools import kb as kb_tools
from agent.tools import files as file_tools


async def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("text")
    ap.add_argument("--model", default=llm.DEFAULT_MODEL)
    ap.add_argument("--narrow-tools", action="store_true")
    ap.add_argument("--no-corpus", action="store_true")
    ap.add_argument("--engine", action="store_true")
    ap.add_argument("--full", action="store_true", help="do not truncate arguments")
    args = ap.parse_args(argv)

    kb = KBIndex.load()
    registry = kb_tools.register(ToolRegistry(), kb, measuring=not args.narrow_tools)
    if not args.narrow_tools:
        file_tools.register(registry)

    engine = None
    if args.engine:
        from agent.engine import EngineLink
        from agent.tools import scene as scene_tools
        engine = await EngineLink().start()
        print("waiting for Unity ...")
        await engine.wait_ready(timeout=90)
        scene_tools.register(registry, engine, kb)

    instructions = prompt.INSTRUCTIONS if args.no_corpus else prompt.with_corpus(kb)
    session = Session(llm.backend_for(args.model, keys.load_openai_key()), registry, instructions)
    await session.start()
    report = await session.run_turn(args.text)
    await session.close()
    if engine is not None:
        await engine.stop()

    generated = 0
    for i, step in enumerate(report.trace, 1):
        arguments = json.dumps(step.get("arguments"), ensure_ascii=False)
        if not args.full and len(arguments) > 110:
            arguments = arguments[:110] + "..."
        print("%2d %-14s %-6s %s" % (i, step["tool"], "ok" if step.get("success") else "FAIL", arguments))
        if step.get("generated"):
            generated += step["generated"]
            print("     -> GENERATED %d transition(s): frames the library does not contain"
                  % step["generated"])
        if step.get("error"):
            print("     -> %s" % step["error"])
    print("\n%d calls, %d iterations, %.1fs" % (report.tool_calls, report.iterations, report.seconds))
    # The reply is the model's account of the turn; this line is the trace's. When they disagree, the
    # reply is the one that is wrong.
    print("generated transitions: %d" % generated)
    print("answer: %s" % (report.text or "(none)"))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
