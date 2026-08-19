#!/usr/bin/env python3
"""Talk to a running agent service over the console channel, or just listen to one.

The console channel is raw TCP carrying newline-delimited JSON -- the same one `terminal.py` attaches
to, and it broadcasts to every attached client. Two uses:

    drive.py "Jill, walk to the patient"     send one instruction and print the reply
    drive.py --listen                        attach silently and watch a turn someone else started

The second is the diagnostic. The Play-mode launcher runs the service with its output in a hidden
window, so when a turn goes quiet there is nothing to read; attaching here shows whether it is still
producing status events (slow) or has stopped producing anything (stuck), which look identical from
the terminal you typed into.
"""
import argparse
import asyncio
import json
import sys
import time

from agent import protocol as P

NEWLINE = chr(10)


async def main(text, port, timeout, listen):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    print("attached to tcp://127.0.0.1:%d" % port, flush=True)
    if not listen:
        writer.write((json.dumps(P.event(P.T.AGENT_INSTRUCT, {"text": text}))
                      + NEWLINE).encode("utf-8"))
        await writer.drain()
        print("sent: %s" % text, flush=True)

    started = time.monotonic()
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    code = 1
    while loop.time() < deadline:
        try:
            line = await asyncio.wait_for(reader.readline(),
                                          timeout=max(1.0, deadline - loop.time()))
        except asyncio.TimeoutError:
            print("!! nothing more arrived within %ds" % timeout, flush=True)
            code = 2
            break
        if not line:
            print("!! the service closed the console channel", flush=True)
            code = 3
            break
        msg = json.loads(line.decode("utf-8"))
        kind = msg.get("type")
        data = msg.get("data") or {}
        # Seconds since attaching, on every line. A turn that is merely slow and one that has stopped
        # look the same without it.
        at = "%7.1fs" % (time.monotonic() - started)
        if kind == P.T.AGENT_REPLY:
            print(NEWLINE + "=== reply at %s ===" % at.strip(), flush=True)
            print(json.dumps(data, ensure_ascii=False, indent=2)[:4000], flush=True)
            code = 0
            if not listen:
                break
            continue
        print("%s  %-14s %s" % (at, kind, json.dumps(data, ensure_ascii=False)[:150]), flush=True)
    writer.close()
    return code


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default="")
    ap.add_argument("--listen", action="store_true",
                    help="attach without sending anything and watch what a running turn emits")
    ap.add_argument("--port", type=int, default=8771)
    ap.add_argument("--timeout", type=int, default=90)
    args = ap.parse_args()
    if not args.listen and not args.text:
        ap.error("give an instruction, or pass --listen")
    sys.exit(asyncio.run(main(args.text, args.port, args.timeout, args.listen)))
