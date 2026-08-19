#!/usr/bin/env python3
"""
terminal.py — the terminal you type into. Runs on Windows; the agent runs in WSL.

    (Windows)  python terminal.py            <-- you are here, typing
                    |  tcp://127.0.0.1:8771, one JSON object per line
    (WSL)      python cli.py --engine --headless
                    |  ws://127.0.0.1:8770
    (Windows)  Unity

WHY A CLIENT AND NOT `wsl.exe -- python cli.py`. Running the service through WSL interop puts the
terminal and the agent in one process, so closing the window kills the service — and the service is
what Unity is connected to. Detached service plus attachable client means the window is disposable and
the run is not. It also means more than one terminal can watch the same turn, and none of them is the
one keeping it alive.

WHY IT NEEDS NOTHING INSTALLED. Standard library only, on purpose: the Windows side of this machine has
Python but no third-party packages, and a debugging console that requires a pip install before it can
tell you what went wrong is a console you will not use. The console channel is line-delimited JSON over
TCP for the same reason — `websockets` is not in the standard library and framing is not worth writing
twice.

WHY THE LINE EDITOR IS HAND-ROLLED. `input()` on Windows has no history, and events arrive while you
are mid-line: without redrawing, an agent status message lands in the middle of what you are typing.
`msvcrt` is standard library and gives raw keys, which is all this needs. On anything else it falls
back to `input()`, which is fine — that platform has readline.
"""
import argparse
import json
import os
import socket
import sys
import threading

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8771

CSI = "\033["
RESET = CSI + "0m"
DIM = CSI + "2m"
BOLD = CSI + "1m"
BLUE = CSI + "38;5;75m"
GREY = CSI + "38;5;245m"
RED = CSI + "38;5;203m"
GREEN = CSI + "38;5;114m"
YELLOW = CSI + "38;5;179m"

PROMPT = BLUE + "› " + RESET


def enable_vt():
    """Turn on ANSI processing. Windows Terminal has it already; conhost needs asking."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        handle = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not k.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(k.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:                                    # noqa: BLE001 — colour is not worth a crash
        return False


class Screen:
    """One terminal, written to from two threads.

    The socket reader and the line editor both print. Without a lock and a redraw, a status line that
    arrives mid-word lands inside what you are typing and neither is readable afterwards. Every write
    goes through `line()`: erase whatever is on the bottom row, print, put it back.

    THE BOTTOM ROW IS TRANSIENT and holds one of two things: the prompt you are typing at, or a tool
    that is still running. Both are redrawn after every line, and both are erased before one. That is
    what lets a call appear the moment it starts and then be REPLACED by the same call with its result
    — rather than printing twice, which is what a terminal that can only append has to do.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.buffer = ""
        self.showing_prompt = False
        self.status = ""
        self.row = []          # successful tool names accumulating on one line; see tool_done

    def _bottom(self):
        if self.showing_prompt:
            return PROMPT + self.buffer
        return self.status

    def _redraw(self):
        bottom = self._bottom()
        if bottom:
            sys.stdout.write(bottom)

    def line(self, text):
        with self._lock:
            # A row of tool names lives on the bottom line while it fills, so anything else printed
            # has to close it first or it is erased by the write below.
            self._flush_row_locked()
            sys.stdout.write("\r" + CSI + "2K")
            sys.stdout.write(text + "\n")
            self._redraw()
            sys.stdout.flush()

    def transient(self, text):
        """Show something on the bottom row that the next `line()` is expected to replace."""
        with self._lock:
            self.status = text
            if not self.showing_prompt:
                sys.stdout.write("\r" + CSI + "2K" + text)
                sys.stdout.flush()

    def clear_transient(self):
        with self._lock:
            self.status = ""
            if not self.showing_prompt:
                sys.stdout.write("\r" + CSI + "2K")
                sys.stdout.flush()

    def prompt(self, buffer=""):
        with self._lock:
            self.buffer = buffer
            self.showing_prompt = True
            sys.stdout.write("\r" + CSI + "2K" + PROMPT + buffer)
            sys.stdout.flush()

    def done(self):
        with self._lock:
            self.showing_prompt = False
            sys.stdout.write("\n")
            sys.stdout.flush()

    # ---- the row of tools ----------------------------------------------------------------------
    #
    # A SUCCESSFUL CALL IS ITS NAME. Every call used to print its arguments, its result and its time,
    # and a turn filled the screen: the run that mattered was somewhere inside twenty lines of things
    # that had gone fine. The names of the tools it used, in order, is what a person watching actually
    # reads — how it got there — and the arguments and results are still recorded in full in
    # `_traces/turns.jsonl`, which is where anyone auditing a turn was always going to look.
    #
    # FAILURES STAY WHOLE. This is opencode's `tool_details_visibility`, which hides a tool "if
    # showDetails is false and the tool completed successfully": collapsing the failures too would
    # leave a turn that went wrong as a row of names with no way to see why, and the trace is the wrong
    # place to have to go for that.

    def tool_done(self, name):
        """Add one successful call to the row being built, wrapping when the row is full."""
        with self._lock:
            if self.row and sum(len(n) + 3 for n in self.row) + len(name) + 3 > ROW_WIDTH:
                self._flush_row_locked()
            self.row.append(name)
            sys.stdout.write("\r" + CSI + "2K" + self._row_text())
            sys.stdout.flush()

    def _row_text(self):
        return GREY + "  " + "  ".join("● " + n for n in self.row) + RESET

    def _flush_row_locked(self):
        if not self.row:
            return
        sys.stdout.write("\r" + CSI + "2K" + self._row_text() + "\n")
        self.row = []

    def end_row(self):
        """Close the row off before something that is not a tool name is printed."""
        with self._lock:
            self._flush_row_locked()
            self._redraw()
            sys.stdout.flush()


class Farewell(Exception):
    """The agent said the run is over. Unwinds the reader thread; the main loop then exits."""


NAME_COL = 14       # `kb_get_action` and `plan_motion` are the longest; longer ones just push right
CALL_COL = 42       # digest.WIDTH, so a clipped argument phrase lands inside its column
ROW_WIDTH = 76      # how much of a line the collapsed names may fill before wrapping


def tool_line(failed, name, call, result, seconds):
    """One FAILED call: what it was, what it asked for, what came back, how long it took.

    Wrapped rather than truncated when it does not fit. A result that has been cut in half is worse
    than a second line, because the half that survives reads like the whole answer.

    Successes no longer come through here — see `Screen.tool_done`. This is deliberately the shape it
    always was, because a failure is the one moment all of it is worth reading.
    """
    mark = (RED + "  ✗ ") if failed else (GREY + "  ● ")
    # `is None`, not falsiness: a call that took under 50 ms rounds to 0.0 and was showing no time at
    # all, so the fastest calls looked like the ones that had not been timed.
    took = "" if seconds is None else "%s%6.2fs%s" % (DIM, seconds, RESET)
    head = "%s%-*s%s %s" % (mark, NAME_COL, name, RESET, call)
    plain = len(name) + len(call) + 5
    tail = (RED + result + RESET) if failed else (GREY + result + RESET)
    if plain + len(result) <= CALL_COL + NAME_COL:
        return "%s%s  %s %s" % (head, " " * max(1, CALL_COL - len(call)), tail, took)
    return "%s\n%s%s %s" % (head, " " * (NAME_COL + 5), tail, took)


def show(screen, msg):
    """One protocol event, as lines on the screen. Mirrors `agent/console.py: render` on the other
    side — the same turn reads the same way here as it does in the Unity window."""
    kind = msg.get("type")
    data = msg.get("data") or {}

    if kind == "console.hello":
        tools = data.get("tools") or []
        screen.line("%sattached%s  %s%s%s  %d actions, %d tools, engine %s"
                    % (GREEN, RESET, BOLD, data.get("model", "?"), RESET,
                       data.get("actions", 0), len(tools), data.get("engine", "?")))
        screen.line(DIM + "  " + "  ".join(tools) + RESET)
        return

    if kind == "agent.status":
        state, detail = data.get("state"), data.get("detail") or ""
        if state == "thinking":
            screen.transient(GREY + "  thinking" + RESET)
        elif state == "steered":
            screen.line(GREY + "  folded in: " + detail + RESET)
        elif state == "said":
            screen.line(GREY + "  ⏺ " + detail + RESET)
        elif state == "tool":
            screen.transient("%s  ● %-12s %s%s" % (GREY, detail, data.get("call") or "", RESET))
        elif state == "tool_done":
            screen.tool_done(detail)
        elif state == "tool_failed":
            screen.line(tool_line(True, detail, data.get("call") or "",
                                  data.get("result") or "", data.get("seconds")))
        return

    if kind == "console.bye":
        # The run is over. Not a lost connection — the agent said so, which it only does when Unity
        # has actually stopped. Handled by leaving: a prompt in front of a scene that no longer
        # exists is a window somebody has to notice and close.
        screen.clear_transient()
        screen.line(YELLOW + "  " + (data.get("reason") or "the run ended") + RESET)
        raise Farewell()

    if kind == "gate.verdict":
        # Deliberately after the reply and visibly separate from it. This is the geometry's account of
        # what happened; the line above is the model's. They are allowed to disagree, and when they do
        # this one is right.
        passed = data.get("status") == "pass"
        screen.line(("%s  ✓ verified%s " % (GREEN, RESET) if passed
                     else "%s  ✗ NOT verified%s " % (RED, RESET)) + (data.get("detail") or ""))
        return

    if kind == "agent.reply":
        screen.clear_transient()
        screen.end_row()
        error = data.get("error")
        if error:
            screen.line(RED + "  " + error + RESET)
            return
        if data.get("cancelled"):
            screen.line(YELLOW + "  interrupted" + RESET)
            return
        if data.get("text"):
            screen.line("")
            screen.line("  " + data["text"])
        # ONE NUMBER FOR THE AGENT, AND THE WAITING SHOWN SEPARATELY. The turn's wall clock is mostly
        # the character walking on a turn that walks anywhere -- three seconds during which nothing
        # could have gone faster -- so quoting it made a quick decision behind a long animation read as
        # a slow agent. The headline is what the agent spent; the wait is named beside it rather than
        # dropped, because a reader who sees only the small number will wonder where the time went.
        waited = data.get("engine_wait_s") or 0.0
        deciding = data.get("deciding_s")
        if deciding is None:
            deciding = data.get("seconds", 0.0) or 0.0
        parts = ["%d tools" % data.get("tool_calls", 0), "%.1fs deciding" % deciding]
        if waited >= 0.05:
            parts.append("+%.1fs waiting on motion" % waited)
        if data.get("generated"):
            parts.append("%d generated" % data["generated"])
        screen.line(DIM + "  " + " · ".join(parts) + RESET)


def reader(sock, screen, stop):
    """Read whole lines off the socket. A short read is normal on TCP, so this buffers rather than
    assuming one recv is one message — the bug that makes a client work until a turn gets busy."""
    pending = b""
    said_goodbye = False
    try:
        while not stop.is_set():
            chunk = sock.recv(65536)
            if not chunk:
                break
            pending += chunk
            while b"\n" in pending:
                raw, pending = pending.split(b"\n", 1)
                if not raw.strip():
                    continue
                try:
                    show(screen, json.loads(raw.decode("utf-8")))
                except ValueError:
                    screen.line(RED + "  (unreadable message from the agent)" + RESET)
    except Farewell:
        said_goodbye = True
    except OSError:
        pass
    finally:
        if not stop.is_set() and not said_goodbye:
            screen.line(RED + "  the agent service closed the connection" + RESET)
        stop.set()


def protocol_version():
    """The version this console speaks, taken from the contract instead of repeated here.

    THE BUG THIS CLOSES, AND IT COST THREE SESSIONS. This file said `{"v": 3}` and the contract went
    to 4. Every line typed here was then dropped by the console channel as malformed -- logged on the
    service side into a file nobody was reading, invisible in this window -- so the prompt came back
    and nothing ever happened. It looked exactly like an intermittent hang in the model, which is what
    was investigated. The service now answers a mismatch instead of dropping it, and this reads the
    number rather than remembering it; either one alone would have been enough, which is why both are
    here.

    `agent/protocol.py` is pure standard library and sits beside this file, so importing it costs this
    console nothing it did not already have -- see the module note on why that matters. Reading the
    constant out of the source is the fallback for a copy of this file living somewhere else, and it
    drifts no more than the import does. If neither works the version goes out as None, which the
    service refuses out loud: a guess that is wrong again is worse than a refusal that says so.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        if here not in sys.path:
            sys.path.insert(0, here)
        from agent.protocol import PROTOCOL_VERSION
        return PROTOCOL_VERSION
    except Exception:                                # noqa: BLE001 - a console must still start
        pass
    try:
        import re
        with open(os.path.join(here, "agent", "protocol.py"), encoding="utf-8") as fh:
            found = re.search(r"^PROTOCOL_VERSION\s*=\s*(\d+)", fh.read(), re.M)
        if found:
            return int(found.group(1))
    except (OSError, ValueError):
        pass
    return None


PROTOCOL_VERSION = protocol_version()


def send(sock, text):
    msg = {"v": PROTOCOL_VERSION, "type": "agent.instruct", "data": {"text": text}}
    sock.sendall((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))


# ---- line editing --------------------------------------------------------------------------------

def read_line_msvcrt(screen, history, stop):
    """A line, with history on the arrow keys. Returns None on Ctrl-C, Ctrl-D, or the run ending.

    POLLED RATHER THAN BLOCKING. `getwch` cannot be woken, so a window sitting at the prompt could not
    be closed by anything but the person in front of it — including by the agent saying the scene had
    stopped. 20 ms is below what a typist can perceive and costs nothing measurable.
    """
    import msvcrt
    import time
    buffer = ""
    at = len(history)
    screen.prompt("")
    while not stop.is_set():
        if not msvcrt.kbhit():
            time.sleep(0.02)
            continue
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):                       # a two-part key: arrows, F-keys, Home/End
            code = msvcrt.getwch()
            if code == "H" and history:                  # up
                at = max(0, at - 1)
                buffer = history[at]
            elif code == "P" and history:                # down
                at = min(len(history), at + 1)
                buffer = history[at] if at < len(history) else ""
            else:
                continue
            screen.prompt(buffer)
            continue
        if ch == "\r":
            screen.done()
            return buffer
        if ch == "\x08":                                 # backspace
            buffer = buffer[:-1]
            screen.prompt(buffer)
            continue
        if ch in ("\x03", "\x04"):                       # Ctrl-C, Ctrl-D
            screen.done()
            return None
        if ch == "\x1b":                                 # Esc clears the line
            buffer = ""
            screen.prompt(buffer)
            continue
        if ch.isprintable():
            buffer += ch
            screen.prompt(buffer)
    screen.done()
    return None


def read_line_plain(screen, history, stop):
    screen.prompt("")
    screen.showing_prompt = False
    try:
        return input()
    except (EOFError, KeyboardInterrupt):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Attach a terminal to the animation agent.")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args(argv)

    enable_vt()
    screen = Screen()
    sock = socket.socket()
    try:
        sock.connect((args.host, args.port))
    except OSError as e:
        print("%scannot reach the agent service at %s:%d%s\n  %s\n  start it in WSL with: "
              "python cli.py --engine --headless"
              % (RED, args.host, args.port, RESET, e))
        return 1

    stop = threading.Event()
    threading.Thread(target=reader, args=(sock, screen, stop), daemon=True).start()
    screen.line(DIM + "English only — what you type is recorded in the run trace. "
                      "/stop interrupts a running turn, /quit leaves." + RESET)
    screen.line(DIM + "Closing this window leaves the agent running; stopping play mode in Unity "
                      "closes both." + RESET)

    read_line = read_line_msvcrt if os.name == "nt" else read_line_plain
    history = []
    try:
        while not stop.is_set():
            text = read_line(screen, history, stop)
            if text is None or text.strip() in ("/quit", "/exit"):
                break
            text = text.strip()
            if not text:
                continue
            if text == "/clear":
                sys.stdout.write(CSI + "2J" + CSI + "H")
                sys.stdout.flush()
                continue
            history.append(text)
            try:
                send(sock, text)
            except OSError:
                screen.line(RED + "  could not send — the agent service is gone" + RESET)
                break
    finally:
        stop.set()
        try:
            sock.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
