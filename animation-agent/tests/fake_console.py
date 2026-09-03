#!/usr/bin/env python3
"""A stand-in for the agent's console channel, so terminal.py can be driven without the service.

Listens on 8779 (NOT 8770/8771 — Unity may be attached to the real service), says hello, and replays
one realistic turn per instruction it receives, with delays, so the redraw under concurrent output is
visible. Standard library only, like the client.

    python3 fake_console.py [--port 8779] [--auto]

--auto replays a turn on connect without waiting for an instruction, for the Windows check where
typing into the window is the thing being tested separately.
"""
import argparse
import json
import socketserver
import threading
import time

V = 5


def turn(send, text):
    send("agent.status", {"state": "thinking", "detail": text})
    time.sleep(1.2)
    send("agent.status", {"state": "said", "detail":
                          "I will look for a sitting clip first, then check whether she can reach "
                          "the chair without walking."})
    time.sleep(0.4)

    send("agent.status", {"state": "tool", "detail": "motion_search", "call": '"sit down on chair"'})
    time.sleep(1.6)
    send("agent.status", {"state": "tool_done", "detail": "motion_search",
                          "call": '"sit down on chair"',
                          "result": "mx_Standing_To_Sitting_Transition +3", "seconds": 1.62})

    send("agent.status", {"state": "tool", "detail": "unity_query", "call": "the whole room"})
    time.sleep(0.9)
    send("agent.status", {"state": "tool_done", "detail": "unity_query", "call": "the whole room",
                          "result": "2 of 5 within reach", "seconds": 0.91})

    send("agent.status", {"state": "tool", "detail": "unity_locomotion", "call": "chair_1"})
    time.sleep(0.8)
    send("agent.status", {"state": "tool", "detail": "unity_locomotion", "call": "walking, 1.2 m to go"})
    time.sleep(1.4)
    send("agent.status", {"state": "tool", "detail": "unity_locomotion", "call": "walking, 0.3 m to go"})
    time.sleep(1.0)
    send("agent.status", {"state": "tool_done", "detail": "unity_locomotion", "call": "chair_1",
                          "result": "arrived, walked 3.4 m", "seconds": 3.21})

    send("agent.status", {"state": "tool", "detail": "unity_execute", "call": "mx_Sitting_Idle"})
    time.sleep(1.1)
    send("agent.status", {"state": "tool_failed", "detail": "unity_execute",
                          "call": "mx_Sitting_Idle · sit on chair_1",
                          "result": "the seat anchor of chair_1 is occupied by patient_1, so the "
                                    "descent was refused before anything was committed; free the "
                                    "seat or pick another chair",
                          "seconds": 1.08})

    send("agent.status", {"state": "tool", "detail": "unity_execute", "call": "sit on chair_2"})
    time.sleep(1.3)
    send("agent.status", {"state": "tool_done", "detail": "unity_execute", "call": "sit on chair_2",
                          "result": "committed · walked 1.1 m · 3 steps", "seconds": 1.34})

    send("agent.status", {"state": "said", "detail": "The first chair was taken, so I used the "
                                                     "other one."})
    time.sleep(0.3)
    send("agent.reply", {
        "text": "She is sitting on `chair_2` now.\n\n"
                "- the first chair was occupied by `patient_1`\n"
                "- the walk was **1.1 m** and the descent used a generated transition\n"
                "- three steps were committed:\n\n"
                "```\nmx_Walk_Forward -> mx_Stand_To_Sit -> mx_Sitting_Idle\n```\n\n"
                "Ask again if you want her back on her feet.",
        "cancelled": False, "tool_calls": 5, "iterations": 4, "seconds": 12.4,
        "deciding_s": 4.1, "engine_wait_s": 8.3, "generated": 1,
        "motion_at_s": 3.9, "tools_used": ["motion_search", "unity_query"]})
    time.sleep(0.8)
    send("gate.verdict", {"status": "pass", "check": "seat_alignment",
                          "detail": "hips 0.031 m from the seat anchor, tolerance 0.05 m"})


class Handler(socketserver.StreamRequestHandler):
    auto = False

    def send(self, kind, data):
        line = json.dumps({"v": V, "type": kind, "data": data}) + "\n"
        self.wfile.write(line.encode("utf-8"))
        self.wfile.flush()

    def handle(self):
        print("terminal attached from %s" % (self.client_address,))
        self.send("console.hello", {"model": "gpt-5.6-terra", "actions": 2446, "engine": "connected",
                                    "tools": ["motion_search", "motion_channels", "motion_timing",
                                              "motion_transition", "motion_compose", "unity_query",
                                              "unity_locomotion", "unity_execute", "unity_validate",
                                              "unity_measure", "glob", "grep", "read"]})
        if self.auto:
            threading.Thread(target=turn, args=(self.send, "sit on the chair"), daemon=True).start()
        for raw in self.rfile:
            try:
                msg = json.loads(raw.decode("utf-8"))
            except ValueError:
                continue
            text = (msg.get("data") or {}).get("text") or ""
            print("instruction: %r (v=%s)" % (text, msg.get("v")))
            if msg.get("v") != V:
                self.send("agent.reply", {"text": "", "error": "this console speaks v%s, the "
                                                               "service speaks v%d" % (msg.get("v"), V)})
                continue
            if text in ("/stop", "/interrupt"):
                self.send("agent.reply", {"text": "", "cancelled": True, "tool_calls": 2,
                                          "deciding_s": 1.0, "engine_wait_s": 0.0})
                continue
            if text == "/bye":
                self.send("console.bye", {"reason": "Unity left play mode"})
                return
            threading.Thread(target=turn, args=(self.send, text), daemon=True).start()
        print("terminal detached")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8779)
    ap.add_argument("--auto", action="store_true")
    args = ap.parse_args()
    Handler.auto = args.auto
    print("fake console channel on 127.0.0.1:%d  (type /bye in the terminal to end the run)"
          % args.port)
    Server(("127.0.0.1", args.port), Handler).serve_forever()
