#!/usr/bin/env python3
"""
cli.py — talk to the agent. Type at any time, including while it is working.

Reading stdin happens on a worker thread and lands on the submission queue, so the prompt is live the
whole time a turn is running. Typing during a turn steers it: the text is folded in at the top of the
next iteration rather than starting a competing turn or aborting the current one.

    <text>      say something. During a turn, this steers it.
    /stop       interrupt the running turn
    /engine     show the engine connection
    /tools      list the declared tools
    /quit

Usage:
    python cli.py                       # KB tools only; no Unity needed
    python cli.py --engine              # also serve the runtime channel and expose scene tools
    python cli.py --headless --engine       # instructions come from the console in the Unity scene
    python cli.py --model gpt-realtime-2.1-mini   # the low-latency comparison arm
"""
import argparse
import asyncio
import json
import logging
import os
import sys
import time

from agent import keys, llm
from agent.console import ConsoleServer, DEFAULT_CONSOLE_PORT, route
from agent.engine import DEFAULT_HOST, DEFAULT_PORT, EngineLink
from agent.kbindex import KBIndex
from agent.llm import DEFAULT_MODEL
from agent import prompt
from agent.loop import Ev, Op, Session
from agent.tools import ToolRegistry
from agent.tools import kb as kb_tools
from agent.tools import files as file_tools

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"


def render(kind, data):
    """The stdin session's display. Says the same things as an attached terminal, in a shape a plain
    appending stream can carry: this one cannot overwrite a line, so a call shows when it starts and
    its result follows underneath, where `terminal.py` replaces the first with the second."""
    if kind == Ev.TURN_STARTED:
        print("%s· thinking%s" % (DIM, RESET), flush=True)
    elif kind == Ev.STEERED:
        print("%s· folded in: %s%s" % (DIM, data["text"], RESET), flush=True)
    elif kind == Ev.TEXT:
        print("%s· %s%s" % (DIM, data["text"], RESET), flush=True)
    elif kind == Ev.TOOL_STARTED:
        print("%s· %s %s%s" % (DIM, data["name"], data.get("call") or "", RESET), flush=True)
    elif kind == Ev.TOOL_FINISHED:
        outcome = data.get("error") if data.get("success") is False else data.get("result")
        if outcome:
            print("%s·   -> %s (%.1fs)%s"
                  % (DIM, outcome, data.get("seconds") or 0.0, RESET), flush=True)
    elif kind == Ev.TURN_COMPLETE:
        report = data["report"]
        if report.cancelled:
            print("%s· interrupted%s" % (DIM, RESET), flush=True)
            return
        if report.text:
            print("\n%s%s%s" % (BOLD, report.text, RESET), flush=True)
        # THE SAME SPLIT THE TERMINAL SHOWS, for the reason digest.py gives: the two halves of the
        # display must not describe one turn differently. The headline is what the agent spent; the
        # seconds spent watching the character move are named beside it rather than folded in, because
        # nothing could have gone faster through those.
        moved = report.motion_at()
        waited = report.engine_wait_s()
        print("%s· %d tool call(s), %d iteration(s), %.1fs deciding%s%s%s"
              % (DIM, report.tool_calls, report.iterations, report.decision_seconds(),
                 "" if waited < 0.05 else ", +%.1fs waiting on motion" % waited,
                 "" if moved is None else ", moving at %.1fs" % moved, RESET), flush=True)
    elif kind == Ev.ERROR:
        print("\n!! %s" % data["message"], file=sys.stderr, flush=True)


def turn_recorder(path):
    """Append every finished turn, and every verdict that lands after one, to a file.

    A run that leaves nothing behind cannot be improved. Which iterations a turn spent, and on what,
    is the only evidence about where a decision's seconds went — and it is gone the moment the terminal
    scrolls. Both events go to the same file in order, because a verdict belongs to the turn above it.
    """
    directory = os.path.dirname(path)
    if directory:
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as e:
            logging.getLogger("agent.cli").warning("no trace directory: %s", e)
            return lambda kind, data: None

    def record(kind, data):
        if kind == Ev.TURN_COMPLETE:
            entry = data["report"].as_dict()
            entry["kind"] = "turn"
        elif kind == Ev.VERDICT:
            entry = {"kind": "verdict", "tool": data.get("name"), "success": data.get("success"),
                     "detail": data.get("detail")}
        else:
            return
        entry["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            # Losing the record is not worth losing the turn over.
            logging.getLogger("agent.cli").warning("could not record the turn: %s", e)

    return record


async def read_stdin(session, engine):
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:                            # EOF (Ctrl-D, or a piped script running out)
            # Let a running turn finish. Shutting down underneath it cancels the answer the user is
            # waiting for, which is the wrong reading of "no more input" -- they stopped typing, they
            # did not ask to abandon the work. /stop is how you abandon it.
            await session.wait_idle()
            await session.submit(Op(Op.SHUTDOWN))
            return
        text = line.strip()
        if not text:
            continue
        if text in ("/quit", "/exit"):
            await session.wait_idle()
            await session.submit(Op(Op.SHUTDOWN))
            return
        if text == "/stop":
            await session.submit(Op(Op.INTERRUPT))
            continue
        if text == "/tools":
            print("  " + ", ".join(session.registry.names()), flush=True)
            continue
        if text == "/engine":
            if engine is None:
                print("  not serving the runtime channel (start with --engine)", flush=True)
            elif engine.ready:
                print("  connected: %s" % engine.hello, flush=True)
            else:
                print("  listening on ws://%s:%d, no engine has connected"
                      % (engine.host, engine.port), flush=True)
            continue
        # Through the same door as the console socket, so the input guards there apply to stdin too.
        # It used to call submit_text directly, which is how one entry point would have kept a rule
        # the other had.
        await route(session, text, source="stdin")


async def main(args):
    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)-16s %(message)s")
    over = asyncio.Event()          # the run is finished; whatever is driving this should return
    kb = KBIndex.load()
    registry = kb_tools.register(ToolRegistry(), kb, measuring=not args.narrow_tools)
    if not args.narrow_tools:
        file_tools.register(registry)

    engine = None
    if args.engine:
        engine = await EngineLink(args.host, args.port).start()
        from agent.tools import scene as scene_tools
        scene_tools.register(registry, engine, kb)

    backend = llm.backend_for(args.model, keys.load_openai_key())
    session = Session(backend, registry, prompt.with_corpus(kb))
    session.on_event(render)
    if args.trace:
        session.on_event(turn_recorder(args.trace))

    console = None
    if args.console_port > 0:
        def banner():
            return {"model": args.model, "actions": len(kb.actions), "tools": registry.names(),
                    "engine": ("connected" if engine is not None and engine.ready
                               else "waiting" if engine is not None else "off")}
        console = await ConsoleServer(session, args.host, args.console_port, banner).start()

    if engine is not None and args.exit_with_engine:
        # THE RUN ENDS WHEN THE ENGINE SAYS IT HAS STOPPED, and only then. A recompile and a domain
        # reload also drop the socket and are deliberately not this — Unity reconnects on its own,
        # which is the reason the agent is the server. Without the distinction the service outlived
        # every session and had to be found and killed by hand afterwards.
        def engine_closed(deliberate, reason):
            if not deliberate:
                logging.getLogger("agent.cli").info(
                    "engine went away (%s); waiting for it to come back", reason or "no reason given")
                return
            print("\nUnity left play mode; shutting down", flush=True)

            async def wind_down():
                if console is not None:
                    await console.farewell("Unity left play mode")
                over.set()
            asyncio.ensure_future(wind_down())

        engine.on_closed(engine_closed)

    print("%s%s%s  %d actions, tools: %s"
          % (BOLD, args.model, RESET, len(kb.actions), ", ".join(registry.names())))
    if engine is not None:
        print("runtime channel on ws://%s:%d — waiting for Unity to connect (it can join any time)"
              % (args.host, args.port))
    if console is not None:
        print("console channel on tcp://%s:%d — attach a terminal with `python terminal.py`"
              % (args.host, args.console_port))
    if args.headless:
        print("headless: no stdin; instructions come from an attached console\n")
    else:
        print("type anything; /stop interrupts, /quit exits\n")

    await session.start()
    if args.headless:
        driver = asyncio.ensure_future(asyncio.sleep(float("inf")))  # until Ctrl-C or the engine stops
    else:
        driver = asyncio.ensure_future(read_stdin(session, engine))
    # Either way out ends the process: the person typing said /quit, or the run itself ended. Waiting
    # on the driver alone is what left a headless service running after Unity had gone.
    ended = asyncio.ensure_future(over.wait())
    try:
        await asyncio.wait([driver, ended], return_when=asyncio.FIRST_COMPLETED)
    finally:
        driver.cancel()
        ended.cancel()
        await session.close()
        if console is not None:
            await console.stop()
        if engine is not None:
            await engine.stop()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--engine", action="store_true", help="serve the runtime channel and expose scene tools")
    ap.add_argument("--narrow-tools", action="store_true",
                    help="withhold glob/grep/read and kb_pose/kb_transition, leaving the surface as it "
                         "stood before the agent could investigate. The comparison arm: a wider tool "
                         "surface is a trade, not a free win.")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--console-port", type=int, default=DEFAULT_CONSOLE_PORT,
                    help="where terminals attach. 0 disables the console channel.")
    ap.add_argument("--trace", default="_traces/turns.jsonl",
                    help="append each finished turn and each late verdict here. Empty disables it.")
    ap.add_argument("--keep-running", dest="exit_with_engine", action="store_false", default=True,
                    help="stay up after Unity leaves play mode, so the next play-mode entry reattaches "
                         "to the same service. The default is to shut down with it: a service that "
                         "outlives its scene is one somebody has to go and find afterwards.")
    ap.add_argument("--verbose", action="store_true",
                    help="log the runtime channel and the instruction bridge")
    ap.add_argument("--headless", action="store_true",
                    help="do not read stdin; every instruction comes from the console in the scene. "
                         "Use this when the service runs detached, where stdin is at EOF and the "
                         "terminal reader would shut the session down immediately.")
    try:
        sys.exit(asyncio.run(main(ap.parse_args())))
    except KeyboardInterrupt:
        print()
