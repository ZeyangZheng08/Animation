#!/usr/bin/env python3
"""
echo_server.py — the minimal shape of the runtime channel between this agent service and the engine.

Not the real protocol. This is the skeleton that proves the channel: the agent is the SERVER and the
engine connects in as a client. That direction is deliberate — the Unity editor drops its managed state
on every script recompile and on entering/leaving play mode, so the party that must reconnect with
backoff is the engine, and a client does that naturally while a server would not.

This channel is SEPARATE from the Unity MCP bridge and must stay that way. The MCP bridge ships C# to be
compiled at runtime; it exists only to build the KB offline. The runtime engine side is a pre-compiled
executor that receives typed messages — assembly plans out, gate diagnostics and bake completions back —
and never receives code. Merging the two would reintroduce runtime-compiled C#, which the architecture
forbids.

Windows reaches this server on localhost because WSL runs with networkingMode=mirrored; without that,
the host cannot address a listener inside the WSL VM without chasing its dynamic IP.

Usage:  python runtime/echo_server.py [--host 127.0.0.1] [--port 8770]
"""
import argparse
import asyncio

import websockets

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8770


async def handler(conn):
    """Echo every frame straight back. A real handler will dispatch on a typed message envelope."""
    async for message in conn:
        await conn.send(message)


async def main(host, port):
    async with websockets.serve(handler, host, port, ping_interval=20, ping_timeout=20):
        print("echo server listening on ws://%s:%d (ctrl-c to stop)" % (host, port), flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    a = ap.parse_args()
    try:
        asyncio.run(main(a.host, a.port))
    except KeyboardInterrupt:
        pass
