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
    python cli.py --locomotion-action mx_Walk_Forward   # swap a runtime primitive; both are checked
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
from agent.tools import scene as scene_tools

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


SERVICE_LOG = os.path.join("_traces", "service.log")


def open_service_log():
    """Where a detached service says things, and where it can be asked where it is stuck.

    THE REASON THIS EXISTS IS THREE FAILED INVESTIGATIONS. Started from Unity the process runs with
    its output on a hidden Windows console: a turn went silent, and the log, the traceback and every
    progress line went somewhere nobody could read. Each time the only way forward was to kill the
    service and try to reproduce it by hand, which is not a diagnosis — it is a coin toss with extra
    steps.

    Two signals come with it, because the two ways a service goes quiet need different tools:

        kill -USR1 <pid>    every THREAD's stack, written by faulthandler from inside the signal
                            handler. This is the one that still works when the process is blocked in
                            a write or any other syscall, which is exactly when nothing else does.
        kill -USR2 <pid>    every asyncio TASK and what it is awaiting. Needs the loop to be running,
                            and says far more when it is: a turn stuck on a response and a turn that
                            was never created look identical from outside and nothing alike here.
    """
    try:
        os.makedirs(os.path.dirname(SERVICE_LOG) or ".", exist_ok=True)
        handle = open(SERVICE_LOG, "a", encoding="utf-8", buffering=1)
    except OSError:
        return None

    import signal
    try:
        import faulthandler
        faulthandler.register(signal.SIGUSR1, file=handle, all_threads=True)
    except (AttributeError, ValueError, RuntimeError):
        pass

    def dump_tasks(_signum, _frame):
        import traceback
        try:
            handle.write("\n==== tasks at %s ====\n" % time.strftime("%H:%M:%S"))
            for task in asyncio.all_tasks():
                handle.write("\n-- %r\n" % (task,))
                for frame in task.get_stack():
                    handle.write("".join(traceback.format_stack(frame, limit=1)))
            handle.flush()
        except Exception:                            # noqa: BLE001 - a diagnostic must not add a fault
            pass

    try:
        signal.signal(signal.SIGUSR2, dump_tasks)
    except (AttributeError, ValueError):
        pass
    return handle


async def main(args):
    # A DETACHED SERVICE MUST NOT LOG TO A CONSOLE NOBODY DRAINS. See open_service_log: that is where
    # three investigations went to die. Attached, stderr is right there and is what a person expects.
    service_log = open_service_log() if args.headless else None
    if args.verbose or service_log is not None:
        logging.basicConfig(
            level=logging.INFO if args.verbose else logging.WARNING,
            format="%(asctime)s %(name)-16s %(message)s",
            **({"stream": service_log} if service_log is not None else {}))
    over = asyncio.Event()          # the run is finished; whatever is driving this should return
    kb = KBIndex.load()

    # THE PRIMITIVES ARE CHECKED BEFORE ANYTHING ELSE, AND WHETHER OR NOT UNITY IS COMING. Travelling
    # and standing still are the two actions the system reaches for on its own; a bad one surfaces as
    # a character marching on the spot in front of a viewer, seconds after a tool reported success.
    # Every property is a lookup in data already loaded, so the check costs milliseconds and turns
    # that into a message naming the option to change. Raises SystemExit with the reason.
    primitives = scene_tools.validate_primitives(kb, args.locomotion_action, args.idle_action)

    registry = kb_tools.register(ToolRegistry(), kb, measuring=not args.narrow_tools)
    if not args.narrow_tools:
        file_tools.register(registry)

    engine = None
    if args.engine:
        engine = await EngineLink(args.host, args.port).start()
        scene_tools.register(registry, engine, kb,
                             locomotion=args.locomotion_action, idle=args.idle_action)

    backend = llm.backend_for(args.model, keys.load_openai_key(),
                              silence_timeout=args.model_silence_s)
    # THE CORPUS IS NO LONGER IN THE PROMPT. `with_corpus` appended all eight actions, which was a
    # good trade at forty tokens and is not one at 2446 rows; what replaces it is `motion_search`,
    # and the instructions say how to read its diagnostics instead of how to read a list.
    session = Session(backend, registry, prompt.INSTRUCTIONS)
    # NOTHING READS STDOUT WHEN THE SERVICE IS DETACHED, and rendering to it anyway is not merely
    # useless. `render` is blocking `print` called from inside the event loop, and the launcher starts
    # this with its output on a hidden Windows console; a write that stalls there stops the loop
    # itself -- no events out, no sockets serviced, no trace line, zero CPU, which is precisely what a
    # wedged service looked like three times in a row. A headless service has consoles attached over
    # the console channel, which is async and drops a slow client rather than waiting for it.
    if not args.headless:
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
    # SAID AT START-UP, because these two are the only actions the system chooses for itself and a
    # run that swapped one silently would be a run whose walk nobody agreed to.
    print("runtime primitives: " + "; ".join(
        "%s %s (%s)" % (fact["role"], action_id,
                        "%d frames, loop gap %.1f deg" % (fact["sampled_frames"],
                                                          fact["loop_gap_deg"])
                        if fact["role"] == "locomotion" else
                        "busiest channel %s at %.3f" % (fact["busiest_channel"],
                                                        fact["motion_magnitude"]))
        for action_id, fact in primitives.items()))
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
                    help="withhold glob/grep/read and motion_timing/motion_compose/motion_transition, "
                         "leaving the surface as it stood before the agent could investigate. The "
                         "comparison arm: a wider tool surface is a trade, not a free win.")
    ap.add_argument("--locomotion-action", default=scene_tools.DEFAULT_LOCOMOTION_ACTION,
                    help="the walk cycle played under the navigation agent. Checked at start-up: it "
                         "has to be an animation rather than a pose asset, move the body and both "
                         "legs, be performed standing, and loop without a visible jump.")
    ap.add_argument("--idle-action", default=scene_tools.DEFAULT_IDLE_ACTION,
                    help="the stance a plan settles into when its walk is over. Checked at start-up: "
                         "standing, and still enough to hold indefinitely.")
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
    ap.add_argument("--model-silence-s", type=float, default=llm.DEFAULT_SILENCE_S,
                    help="how long the model may say nothing while the loop is waiting for it "
                         "before the turn is given up on. Not a turn budget -- progress resets it. "
                         "0 waits for ever, which is what this used to do.")
    ap.add_argument("--verbose", action="store_true",
                    help="log the runtime channel and the instruction bridge")
    ap.add_argument("--headless", action="store_true",
                    help="do not read stdin; every instruction comes from the console in the scene. "
                         "Use this when the service runs detached, where stdin is at EOF and the "
                         "terminal reader would shut the session down immediately. Also stops "
                         "rendering turns to a stdout nobody reads, and puts the log in "
                         "_traces/service.log — where `kill -USR1` and `kill -USR2` write too.")
    try:
        sys.exit(asyncio.run(main(ap.parse_args())))
    except KeyboardInterrupt:
        print()
