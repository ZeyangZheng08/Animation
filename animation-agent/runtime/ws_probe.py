#!/usr/bin/env python3
"""
ws_probe.py — measure the round-trip latency of the runtime channel, engine -> agent -> engine.

Generates a `System.Net.WebSockets.ClientWebSocket` probe, runs it in the live Unity editor over the MCP
bridge, and reports the distribution. The probe leaves nothing behind in the Unity project: no component,
no asset, no script file. Building the real executor is Phase 2 work; this only answers "is the WSL <->
Windows boundary cheap enough", which the architecture's real-time claim depends on.

What this measures is WIRE latency: the probe blocks on the main thread, so there is no frame boundary in
the loop. A real executor receives on a background thread and drains the queue in Update, which adds up to
one frame (16.7 ms at 60 fps) — two orders of magnitude more than the wire. That is the number to design
against, and the reason not to spend effort optimizing the transport further.

Start the server first:  python runtime/echo_server.py
Then:                    python runtime/ws_probe.py [--n 1000] [--port 8770]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unity_sampler  # noqa: E402


def build_probe_csharp(url, n, payload_bytes):
    """C# (CodeDom C# 6 method body) that opens one WebSocket, round-trips `n` small frames on it, and
    returns the per-iteration microsecond timings as a comma-separated line.

    CodeDom gives us a non-async method body, so the Task-based WebSocket API is driven with .Wait() /
    .Result rather than await. Blocking is correct here: we want the bare wire cost with nothing else in
    the loop.
    """
    return r'''
string URL="%s"; int N=%d; int PAY=%d;
var ws = new System.Net.WebSockets.ClientWebSocket();
var ct = System.Threading.CancellationToken.None;
try { ws.ConnectAsync(new System.Uri(URL), ct).Wait(10000); }
catch (System.Exception e) { return "ERROR connect: "+e.Message; }
if (ws.State != System.Net.WebSockets.WebSocketState.Open) return "ERROR state: "+ws.State;

var msg = new byte[PAY];
for (int i=0;i<PAY;i++) msg[i] = (byte)(48 + (i %% 10));
var buf = new byte[8192];
var sb = new System.Text.StringBuilder();
var sw = new System.Diagnostics.Stopwatch();

// one untimed warm-up round-trip so TCP/TLS/first-allocation costs do not land in the sample
ws.SendAsync(new System.ArraySegment<byte>(msg), System.Net.WebSockets.WebSocketMessageType.Text, true, ct).Wait();
ws.ReceiveAsync(new System.ArraySegment<byte>(buf), ct).Wait();

for (int i=0;i<N;i++) {
  sw.Restart();
  ws.SendAsync(new System.ArraySegment<byte>(msg), System.Net.WebSockets.WebSocketMessageType.Text, true, ct).Wait();
  var r = ws.ReceiveAsync(new System.ArraySegment<byte>(buf), ct).Result;
  sw.Stop();
  if (r.Count != PAY) { return "ERROR echo size: got "+r.Count+" want "+PAY; }
  if (i>0) sb.Append(",");
  sb.Append((sw.Elapsed.Ticks / 10L));           // TimeSpan tick = 100 ns -> microseconds
}
try { ws.CloseAsync(System.Net.WebSockets.WebSocketCloseStatus.NormalClosure, "done", ct).Wait(2000); } catch {}
ws.Dispose();
return sb.ToString();
''' % (url, n, payload_bytes)


def _pct(sorted_us, q):
    if not sorted_us:
        return float("nan")
    return sorted_us[min(len(sorted_us) - 1, int(len(sorted_us) * q))]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000, help="round trips to time")
    ap.add_argument("--bytes", type=int, default=256, help="payload size per frame")
    ap.add_argument("--ws-host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8770, help="echo server port")
    ap.add_argument("--host", default=unity_sampler.DEFAULT_HOST, help="Unity MCP bridge host")
    ap.add_argument("--bridge-port", type=int, default=unity_sampler.DEFAULT_PORT)
    ap.add_argument("--instance", default=None)
    a = ap.parse_args(argv)

    url = "ws://%s:%d" % (a.ws_host, a.port)
    if not unity_sampler.bridge_healthy(a.host, a.bridge_port):
        print("Unity MCP bridge not reachable at %s:%d." % (a.host, a.bridge_port))
        return 1

    print("probing %s from the Unity editor — %d round trips x %d bytes" % (url, a.n, a.bytes))
    cs = build_probe_csharp(url, a.n, a.bytes)
    ok, result_text, _ = unity_sampler.run_csharp_over_http(
        cs, host=a.host, port=a.bridge_port, instance=a.instance, timeout=300)
    unity_sampler.close_connections()
    if not ok or result_text.startswith("ERROR"):
        print("probe failed: %s" % result_text.strip()[:400])
        print("is the echo server running?  python runtime/echo_server.py")
        return 1

    try:
        us = sorted(int(x) for x in result_text.strip().split(",") if x)
    except ValueError:
        print("unparseable probe output: %r" % result_text[:200])
        return 1
    if not us:
        print("probe returned no samples")
        return 1

    print("\n  n     = %d" % len(us))
    print("  min   = %.3f ms" % (us[0] / 1000.0))
    print("  p50   = %.3f ms" % (_pct(us, 0.50) / 1000.0))
    print("  p99   = %.3f ms" % (_pct(us, 0.99) / 1000.0))
    print("  max   = %.3f ms" % (us[-1] / 1000.0))
    print("\n  wire only — a frame-bound executor adds up to one frame (16.7 ms at 60 fps) on top.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
