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
the run is not, and more than one terminal can watch the same turn.

WHY IT NEEDS NOTHING INSTALLED. Standard library only, on purpose: the Windows side of this machine
has Python but no third-party packages, and a console that needs a pip install before it can say what
went wrong is a console you will not use. That rules out rich, prompt_toolkit and textual, curses is
not on Windows, and it is why the channel is line-delimited JSON over TCP rather than a WebSocket.

WHY THE LINE EDITOR IS HAND-ROLLED. `input()` has no history on Windows, and events arrive while you
are mid-line: without redrawing, a status message lands in the middle of what you are typing. Raw keys
come from `msvcrt` on Windows and `termios`/`tty` on Linux — both standard library — so one editor and
one way of drawing run on both sides rather than either degrading to `input()`.

HOW THE SCREEN IS DIVIDED. Everything above scrolls and is append-only; the bottom three or four rows
are erased and redrawn on every change, and they are the LAST rows of the window at all times — on a
fresh attach the transcript is padded down to them, and a window that grows is padded again, so the
input box never floats up into the middle of an empty screen:

    ...transcript...
      ⠹ motion_search   "sit on a chair"                        1.2s   <- only while a tool runs
     ──────────────────────────────────────────────────────────────
     › what you are typing
       ⠹ working 4.2s · 2 tools              gpt-5.6-terra · connected

A tool appears in the bottom area when it starts and is REPLACED by its finished row in the transcript
when it ends, so a call costs one line whether it took 20 ms or three seconds. Failures keep the whole
result, wrapped, in red: that is the one moment all of it is worth reading, and the run trace is the
wrong place to have to go for it.

TWO THREADS, ONE SCREEN. The socket reader and the key loop both draw. Every write goes through
`Screen` under one lock: erase the bottom area, print, draw it again. A ticker redraws the bottom area
ten times a second so the spinner turns and the seconds count up, and the width is re-read from
`shutil.get_terminal_size()` on every draw — Windows has no resize signal.

RENDERING IS PURE WHERE IT CAN BE. Wrapping, markdown, the rows, the status line and the editor's key
transitions are functions over values, so `tests/test_terminal.py` covers them without a terminal or a
socket. `Screen` is the only part that writes.
"""
import argparse
import json
import os
import re
import shutil
import socket
import sys
import threading
import time

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8771

CSI = "\033["
RESET = CSI + "0m"
DIM = CSI + "2m"
BOLD = CSI + "1m"
# 256-colour and mid-range on purpose: these read on a white background and on a black one, which the
# default eight do not — bright white vanishes on a light theme, blue 4 is unreadable on a dark one.
BLUE = CSI + "38;5;75m"
GREY = CSI + "38;5;245m"
RED = CSI + "38;5;203m"
GREEN = CSI + "38;5;114m"
YELLOW = CSI + "38;5;179m"
CODE = CSI + "38;5;109m"

SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
NAME_COL = 16          # `motion_transition` is the longest tool name; longer ones just push right
GUTTER = 2             # everything the agent does is indented under the line you typed


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
    except Exception:                                # noqa: BLE001 — colour is not worth a crash
        return False


# ---- text ----------------------------------------------------------------------------------------

ANSI = re.compile(r"\033\[[0-9;]*m")
FENCE = re.compile(r"^\s*```")
BULLET = re.compile(r"^[-*+]\s+(.*)$")
NUMBER = re.compile(r"^(\d+)[.)]\s+(.*)$")
INLINE_CODE = re.compile(r"`([^`]+)`")
INLINE_BOLD = re.compile(r"\*\*([^*]+)\*\*")


def strip_ansi(text):
    return ANSI.sub("", text)


def clip(text, width):
    """`text` cut to `width` printable columns, with the cut marked."""
    text = " ".join(str(text).split())
    if width <= 0:
        return ""
    return text if len(text) <= width else text[:max(0, width - 1)] + "…"


def wrap(text, width):
    """`text` as lines of at most `width` columns. Blank lines are kept, runs of spaces are not, and a
    word too long to fit is broken: one that overflows makes the terminal wrap it itself, which leaves
    the cursor in a column the redraw does not know about."""
    width = max(8, width)
    out = []
    for paragraph in str(text).split("\n"):
        words = []
        for word in paragraph.split():
            while len(word) > width:
                words.append(word[:width])
                word = word[width:]
            words.append(word)
        if not words:
            out.append("")
            continue
        line = words[0]
        for word in words[1:]:
            if len(line) + 1 + len(word) <= width:
                line += " " + word
            else:
                out.append(line)
                line = word
        out.append(line)
    return out


def markdown_lines(text, width, gutter=GUTTER):
    """Assistant text as styled lines, wrapped to `width` and indented by `gutter`.

    Bullets, numbered lists, inline code, bold and fenced blocks, and nothing else. A reply is a
    sentence or two about what was done; what is worth having is that a list reads as a list and a
    clip id does not arrive wrapped in backticks. Not a markdown engine.
    """
    body = max(20, width - gutter - 1)
    pad = " " * gutter
    out = []
    fenced = False
    for raw in str(text).split("\n"):
        line = raw.rstrip()
        if FENCE.match(line):
            fenced = not fenced                        # the fence itself is chrome, not content
            continue
        if fenced:
            out.append(pad + "  " + CODE + clip(line.strip(), body - 2) + RESET)
            continue
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        indent = " " * min(8, len(line) - len(line.lstrip()))
        bullet, number = BULLET.match(stripped), NUMBER.match(stripped)
        if bullet:
            marker, rest = "• ", bullet.group(1)
        elif number:
            marker, rest = number.group(1) + ". ", number.group(2)
        else:
            marker, rest = "", stripped
        room = max(12, body - len(indent) - len(marker))
        for i, one in enumerate(wrap(rest, room)):
            styled = INLINE_BOLD.sub(BOLD + r"\1" + RESET,
                                     INLINE_CODE.sub(CODE + r"\1" + RESET, one))
            out.append(pad + indent + (marker if i == 0 else " " * len(marker)) + styled)
    return out


def fmt(seconds, coarse=False):
    """A duration. Two decimals under ten seconds, because a call that took 40 ms used to round to 0.0
    and read as one that had not been timed; `coarse` is for a clock being watched, which would
    otherwise flicker its last digit ten times a second."""
    if seconds is None:
        return ""
    if seconds >= 60:
        return "%dm%02ds" % (int(seconds) // 60, int(seconds) % 60)
    return "%.1fs" % seconds if coarse or seconds >= 10 else "%.2fs" % seconds


def share(left, right, room):
    """Two phrases into one row: keep both whole if they fit, otherwise cut the greedy one."""
    left, right = " ".join(str(left).split()), " ".join(str(right).split())
    sep = 3 if (left and right) else 0
    if len(left) + len(right) + sep <= room:
        return left, right
    half = max(6, (room - sep) // 2)
    if len(left) <= half:
        return left, clip(right, room - sep - len(left))
    if len(right) <= half:
        return clip(left, room - sep - len(right)), right
    return clip(left, half), clip(right, room - sep - half)


def pad_right(plain, styled, right_plain, right_styled, width):
    """`styled` on the left, `right_styled` at the right margin. The plain forms are what the columns
    are counted in — the styled ones carry escape sequences that occupy none."""
    gap = (width - 1) - len(plain) - len(right_plain)
    if not right_plain or gap < 1:
        return styled                       # no room for the time; the row itself is what matters
    return styled + " " * gap + right_styled


def tool_rows(kind, name, phrase, result, seconds, width, tick=0):
    """One tool call as screen rows. `kind` is "run", "done" or "fail".

    A running call is one row in the bottom area and a finished one is the same row in the transcript,
    so a call costs one line either way. Successes were once collapsed to a row of bare names to keep
    a turn short; a row carries what the call asked for and what came back, which is what tells four
    `unity_query` calls apart. A failure keeps its whole result, wrapped rather than cut: half a
    message reads like all of it.
    """
    glyph, colour = {"run": (SPIN[tick % len(SPIN)], BLUE),
                     "done": ("●", GREEN), "fail": ("✗", RED)}[kind]
    took = fmt(seconds, coarse=(kind == "run"))
    column = max(len(name), NAME_COL)
    room = max(12, (width - 1) - GUTTER - 2 - column - 1 - len(took) - 1)
    if kind == "done":
        phrase, result = share(phrase, result, room)
        tail = ("  → %s" % result) if result else ""
    else:
        phrase, tail = clip(phrase, room), ""
    plain = "%s%s %-*s %s%s" % (" " * GUTTER, glyph, column, name, phrase, tail)
    styled = "%s%s%s %s%-*s%s %s%s%s%s" % (" " * GUTTER, colour, glyph, BOLD, column, name, RESET,
                                           GREY, phrase, tail, RESET)
    rows = [pad_right(plain, styled, took, DIM + took + RESET, width)]
    if kind == "fail":
        indent = " " * (GUTTER + 2 + column + 1)
        rows += [indent + RED + one + RESET
                 for one in wrap(result or "", max(20, (width - 1) - len(indent)))]
    return rows


def status_line(width, state):
    """What the turn is doing on the left, what it is attached to on the right. The right half is
    dropped first when the window is narrow, because the turn changes and the model name does not."""
    pad = " " * GUTTER
    if not state.connected:
        left_plain, left = "not connected", RED + "not connected" + RESET
    elif state.phase == "idle":
        left_plain, left = "ready", DIM + "ready" + RESET
    else:
        spin = SPIN[state.tick % len(SPIN)]
        word = "thinking" if state.phase == "thinking" else "working"
        tail = fmt(state.elapsed(), coarse=True)
        if state.tools:
            tail += " · %d tool%s" % (state.tools, "" if state.tools == 1 else "s")
        left_plain = "%s %s %s" % (spin, word, tail)
        left = "%s%s%s %s %s%s%s" % (BLUE, spin, RESET, word, DIM, tail, RESET)
    right_plain = " · ".join(p for p in (state.model, state.engine) if p)
    right = DIM + right_plain + RESET
    if len(left_plain) + len(right_plain) + GUTTER + 4 > width:
        right_plain, right = "", ""
    return pad_right(pad + left_plain, pad + left, right_plain, right, width)


def input_line(width, buffer, cursor, placeholder=""):
    """The line you type on, and the column the terminal cursor belongs in.

    It scrolls sideways rather than wrapping: a second row would move the box down whenever a long
    instruction crossed the edge, and the redraw would have to guess how many rows to erase after a
    resize. Three columns go to the prompt and one to the cursor standing past the last character —
    without that one the cursor at the end of a full line lands at the start of the next row.
    """
    room = max(10, width - 4)
    start = max(0, cursor - room)
    prompt = " " + BLUE + "›" + RESET + " "
    if not buffer and placeholder:
        return prompt + DIM + clip(placeholder, room) + RESET, 3
    return prompt + buffer[start:start + room], 3 + (cursor - start)


def bottom_padding(rows, filled, height):
    """Blank lines to print before the bottom area so that it lands on the LAST rows of the window.

    The bottom area used to be drawn wherever the transcript had got to, which on a fresh attach is
    the second or third row of an empty window: the input box sat near the top with the rest of the
    screen blank under it, and drifted downwards as output arrived. pi and opencode keep the box on
    the last rows at all times, and this is the arithmetic for it.

    `filled` is how many rows the transcript has already put on screen since the last clear, `height`
    how many the bottom area needs. Once the transcript has filled the window there is nothing to pad:
    the terminal's own scrolling keeps the last written row on the last row of the window, which is
    where the bottom area already is.
    """
    return max(0, rows - height - filled)


class Status:
    """What the footer says. Held apart from the drawing so the tests can set it by hand."""

    def __init__(self):
        self.connected = False
        self.model = ""
        self.engine = ""
        self.tools = 0
        self.phase = "idle"                  # idle | thinking | tool
        self.started = None                  # when the current turn began, monotonic
        self.tick = 0

    def elapsed(self):
        return 0.0 if self.started is None else time.monotonic() - self.started

    def begin(self):
        if self.phase == "idle":
            self.started = time.monotonic()
            self.tools = 0
        self.phase = "thinking"

    def end(self):
        self.phase, self.started = "idle", None


# ---- keys ----------------------------------------------------------------------------------------

CONTROL = {"\x01": "home", "\x02": "left", "\x03": "interrupt", "\x04": "eof", "\x05": "end",
           "\x06": "right", "\x08": "backspace", "\x7f": "backspace", "\x0b": "kill-end",
           "\x0c": "redraw", "\x0e": "down", "\x10": "up", "\x15": "kill-start",
           "\x17": "kill-word", "\r": "submit", "\n": "submit"}

ESCAPES = {"[A": "up", "[B": "down", "[C": "right", "[D": "left", "[H": "home", "[F": "end",
           "[1~": "home", "[4~": "end", "[7~": "home", "[8~": "end", "[3~": "delete",
           "[1;5C": "word-right", "[1;5D": "word-left", "[1;3C": "word-right", "[1;3D": "word-left",
           "[5C": "word-right", "[5D": "word-left", "OH": "home", "OF": "end",
           "b": "word-left", "f": "word-right", "\x7f": "kill-word"}

WINDOWS_KEYS = {"H": "up", "P": "down", "K": "left", "M": "right", "G": "home", "O": "end",
                "S": "delete", "s": "word-left", "t": "word-right"}

MAX_ESCAPE = 6         # longest sequence above; anything longer is not one and must not be buffered


def decode(buffer, final=False):
    """A chunk of raw input as keys, plus whatever is left of a partial sequence.

    Keys are `(name, value)`; a run of printable characters comes back as one `("text", "...")`, which
    is what makes a paste one edit rather than four hundred redraws. `final=True` means no more bytes
    are coming, so a lone Esc is Esc rather than the start of an arrow key — a distinction that cannot
    be made from the bytes, only from the silence after them.
    """
    keys, text, i, n = [], [], 0, len(buffer)

    def flush():
        if text:
            keys.append(("text", "".join(text)))
            del text[:]

    while i < n:
        ch = buffer[i]
        if ch == "\033":
            rest = buffer[i + 1:]
            match = next((rest[:k] for k in range(1, min(len(rest), MAX_ESCAPE) + 1)
                          if rest[:k] in ESCAPES), None)
            if match is not None:
                flush()
                keys.append((ESCAPES[match], None))
                i += 1 + len(match)
                continue
            if not final and len(rest) < MAX_ESCAPE:
                break                                  # might still be the start of a sequence
            flush()
            if rest[:1] in ("[", "O"):
                # An unknown sequence is dropped up to its final byte: typing its letters into the
                # buffer is worse than ignoring a key nobody pressed on purpose.
                k = 1
                while k < len(rest) and not ("\x40" <= rest[k] <= "\x7e"):
                    k += 1
                i = n if k >= len(rest) else i + k + 2
                continue
            keys.append(("esc", None))
            i += 1
        elif ch in CONTROL:
            flush()
            keys.append((CONTROL[ch], None))
            i += 1
        elif ch.isprintable():
            text.append(ch)
            i += 1
        else:
            i += 1                                     # a control character nothing is bound to
    flush()
    return keys, buffer[i:] if i < n else ""


def windows_key(code):
    """The second half of a two-part Windows key. Unmapped ones are ignored rather than typed."""
    return (WINDOWS_KEYS.get(code) or "none", None)


def word_left(text, cursor):
    i = cursor
    while i > 0 and not text[i - 1].isalnum():
        i -= 1
    while i > 0 and text[i - 1].isalnum():
        i -= 1
    return i


def word_right(text, cursor):
    i, n = cursor, len(text)
    while i < n and not text[i].isalnum():
        i += 1
    while i < n and text[i].isalnum():
        i += 1
    return i


class Editor:
    """One line of text, a cursor in it, and a history. No terminal: `apply` returns what happened and
    the caller draws it, which is what makes the whole key table testable in one process."""

    def __init__(self, history=None):
        self.text = ""
        self.cursor = 0
        self.history = history if history is not None else []
        self.at = len(self.history)          # where Up/Down is; == len means "not in the history"
        self.draft = ""                      # what was typed before Up went back, kept for Down

    def set(self, text):
        self.text, self.cursor = text, len(text)

    def apply(self, key):
        """One key. Returns None, or an action for the caller: submit / quit / interrupt / redraw."""
        name, value = key
        if name == "text":
            # A paste arrives as one key, and its newlines become spaces rather than sends: an
            # instruction pasted out of a document is one instruction, and half of it submitted on
            # its own is a turn nobody asked for.
            value = value.replace("\t", " ").replace("\r", " ").replace("\n", " ")
            self.text = self.text[:self.cursor] + value + self.text[self.cursor:]
            self.cursor += len(value)
            return None
        if name == "submit":
            return "submit"
        if name == "eof":
            return "quit" if not self.text else None
        if name == "interrupt":
            if not self.text:
                return "interrupt"
            self.set("")
        elif name == "redraw":
            return "redraw"
        elif name == "esc":
            self.set("")
        elif name == "left":
            self.cursor = max(0, self.cursor - 1)
        elif name == "right":
            self.cursor = min(len(self.text), self.cursor + 1)
        elif name == "word-left":
            self.cursor = word_left(self.text, self.cursor)
        elif name == "word-right":
            self.cursor = word_right(self.text, self.cursor)
        elif name == "home":
            self.cursor = 0
        elif name == "end":
            self.cursor = len(self.text)
        elif name == "backspace":
            if self.cursor:
                self.text = self.text[:self.cursor - 1] + self.text[self.cursor:]
                self.cursor -= 1
        elif name == "delete":
            self.text = self.text[:self.cursor] + self.text[self.cursor + 1:]
        elif name == "kill-start":
            self.text, self.cursor = self.text[self.cursor:], 0
        elif name == "kill-end":
            self.text = self.text[:self.cursor]
        elif name == "kill-word":
            start = word_left(self.text, self.cursor)
            self.text = self.text[:start] + self.text[self.cursor:]
            self.cursor = start
        elif name == "up":
            # HISTORY FROM THE EDGE, AND THEN ALL THE WAY. Up in the middle of a line means "the start
            # of this line" more often than it means "the line before this one", so the first press
            # goes there and the second, now on the edge, recalls. Once you are walking back through
            # the history both keys keep walking, wherever the cursor ended up.
            browsing = self.at < len(self.history)
            if not (browsing or not self.text or self.cursor == 0):
                self.cursor = 0
            elif self.history and self.at > 0:
                if not browsing:
                    self.draft = self.text
                self.at -= 1
                self.set(self.history[self.at])
                self.cursor = 0
        elif name == "down":
            if self.at < len(self.history):
                self.at += 1
                self.set(self.history[self.at] if self.at < len(self.history) else self.draft)
            else:
                self.cursor = len(self.text)
        return None

    def take(self):
        """The finished line, cleared out of the editor and pushed onto the history."""
        text = self.text.strip()
        self.text, self.cursor, self.draft = "", 0, ""
        if text and (not self.history or self.history[-1] != text):
            self.history.append(text)
        self.at = len(self.history)
        return text


# ---- the screen ----------------------------------------------------------------------------------

class Screen:
    """One terminal, written to from three threads: the socket reader, the key loop and the ticker.

    Everything goes through the lock and the same two steps — erase the bottom area, write, draw it
    again. Only the bottom area is ever rewritten and the transcript above it is append-only, which is
    what keeps this a few hundred lines rather than a diffing renderer. On a stream that is not a
    terminal there is no bottom area and `log` is a plain print.

    Raw mode turns off output post-processing, so every line written here starts with a carriage
    return: after a newline the cursor keeps the column it was in.
    """

    def __init__(self, out=None, status=None, editor=None):
        self.out = out or sys.stdout
        self.status = status or Status()
        self.editor = editor or Editor()
        self.running = None                  # (name, phrase, started) while a tool is in flight
        self.placeholder = ""
        self.interactive = bool(getattr(self.out, "isatty", lambda: False)())
        self._lock = threading.RLock()
        self._drawn = 0                      # bottom-area rows currently on screen
        self._at = 0                         # which of them the cursor sits on
        self._filled = 0                     # transcript rows on screen since the last clear
        self._rows = 0                       # window height at the last draw, to notice it growing

    def size(self):
        """(columns, rows), re-read on every draw. Windows has no resize signal."""
        columns, rows = shutil.get_terminal_size((100, 30))
        return max(40, columns), max(6, rows)

    def width(self):
        return self.size()[0]

    def _erase(self):
        if not self._drawn:
            return
        parts = ["\r"]
        if self._drawn - 1 > self._at:
            parts.append(CSI + "%dB" % (self._drawn - 1 - self._at))
        parts += [CSI + "2K" + CSI + "A"] * (self._drawn - 1)
        parts.append(CSI + "2K")
        self.out.write("".join(parts))
        self._drawn = self._at = 0

    def _draw(self):
        if not self.interactive:
            return
        width, rows = self.size()
        lines = []
        if self.running:
            name, phrase, started = self.running
            lines += tool_rows("run", name, phrase, "", time.monotonic() - started, width,
                               self.status.tick)
        lines.append(DIM + " " + "─" * (width - 2) + RESET)
        text, column = input_line(width, self.editor.text, self.editor.cursor, self.placeholder)
        at = len(lines)
        lines.append(text)
        lines.append(status_line(width, self.status))

        # A WINDOW THAT GREW HAS ROOM UNDER THE BOX. The rows it gained are below everything already
        # drawn, so without padding them the input floats up the screen by however many were added.
        if self._rows and rows > self._rows:
            self._filled = max(0, self._filled - (rows - self._rows))
        self._rows = rows
        pad = bottom_padding(rows, self._filled, len(lines))
        if pad:
            self.out.write("\r\n" * pad)
            self._filled += pad

        self.out.write("\r" + "\r\n".join(line + CSI + "K" for line in lines))
        if len(lines) - 1 > at:
            self.out.write(CSI + "%dA" % (len(lines) - 1 - at))
        self.out.write("\r" + (CSI + "%dC" % column if column else ""))
        self._drawn, self._at = len(lines), at

    def refresh(self):
        """Redraw the bottom area only. The ticker calls this; nothing else needs to."""
        with self._lock:
            if self.interactive:
                self._erase()
                self._draw()
                self.out.flush()

    def log(self, lines):
        """Append to the transcript. A string is one line; a list is a block."""
        with self._lock:
            self._erase()
            for line in ([lines] if isinstance(lines, str) else lines):
                self.out.write("\r" + CSI + "2K" + line + "\r\n" if self.interactive
                               else line + "\n")
                self._filled += 1
            self._draw()
            self.out.flush()

    def clear(self):
        with self._lock:
            self._drawn = 0
            self._filled = 0
            self.out.write(CSI + "2J" + CSI + "H")
            self._draw()
            self.out.flush()

    def close(self):
        """Leave the cursor on a clean line below whatever was on screen."""
        with self._lock:
            self._erase()
            self.out.write("\r\n")
            self.out.flush()


# ---- the protocol, as a transcript ----------------------------------------------------------------

class Farewell(Exception):
    """The agent said the run is over. Unwinds the reader thread; the main loop then exits."""


HELP = [
    "  Enter send · Ctrl+C clears the line, then stops the turn, then quits · Ctrl+D quits",
    "  Left/Right move · Ctrl+A/E or Home/End the ends · Ctrl+Left/Right by word",
    "  Ctrl+W delete a word · Ctrl+U/K cut to an end · Ctrl+L redraw",
    "  Up/Down history, from the start or the end of the line",
    "  /help /tools /clear /quit are handled here; /stop interrupts the running turn",
    "  anything else is an instruction, in English, recorded verbatim in the run trace",
]


class Ui:
    """One protocol event, as lines on the screen. Mirrors `agent/console.py: render` on the other
    side — the same turn reads the same way here as it does through `cli.py`."""

    def __init__(self, screen):
        self.screen = screen
        self.status = screen.status
        self.tools = []
        self.actions = 0

    def note(self, text, colour=DIM):
        self.screen.log([" " * GUTTER + colour + one + RESET
                         for one in wrap(text, self.screen.width() - GUTTER - 1)])

    def echo(self, text):
        """What you typed, in the transcript. The bottom area is transient, so without this the line
        vanishes when it is sent and the turn under it answers a question nobody can see."""
        self.screen.log([""] + [
            " " + BLUE + ("›" if i == 0 else " ") + RESET + " " + BOLD + one + RESET
            for i, one in enumerate(wrap(text, self.screen.width() - 3))])

    def show(self, msg):
        kind = msg.get("type")
        data = msg.get("data") or {}
        width = self.screen.width()

        if kind == "console.hello":
            self.tools = data.get("tools") or []
            self.actions = data.get("actions", 0)
            self.status.connected = True
            self.status.model = data.get("model", "?")
            self.status.engine = "engine " + str(data.get("engine", "?"))
            self.screen.log([
                "",
                " %sattached%s  %s%s%s  %d actions · %d tools · engine %s"
                % (GREEN, RESET, BOLD, self.status.model, RESET, self.actions,
                   len(self.tools), data.get("engine", "?")),
                " " + DIM + clip("  ".join(self.tools), width - 2) + RESET,
                " " + DIM + "type an instruction in English; /help for keys" + RESET])
        elif kind == "agent.status":
            self._status(data, width)
        elif kind == "gate.verdict":
            # Deliberately after the reply and visibly separate from it. This is the geometry's
            # account of what happened; the reply above is the model's. They are allowed to disagree,
            # and when they do this one is right.
            passed = data.get("status") == "pass"
            mark = (GREEN + "✓ verified") if passed else (RED + "✗ NOT verified")
            self.screen.log(" " * GUTTER + mark + RESET + " " + GREY
                            + clip(data.get("detail") or "", width - GUTTER - 16) + RESET)
        elif kind == "agent.reply":
            self._reply(data, width)
        elif kind == "console.bye":
            # The run is over. Not a lost connection — the agent says this only when Unity has
            # actually stopped. Handled by leaving: a prompt in front of a scene that no longer
            # exists is a window somebody has to notice and close.
            self.screen.running = None
            self.status.end()
            self.note(data.get("reason") or "the run ended", YELLOW)
            raise Farewell()

    def _status(self, data, width):
        state, detail = data.get("state"), data.get("detail") or ""
        if state == "thinking":
            self.status.begin()
            self.screen.refresh()
        elif state == "steered":
            self.note("folded in: " + detail, GREY)
        elif state == "said":
            # What the model said on its way to the next call. Dim, because the reply is the thing to
            # read and this is the reasoning that got there.
            self.screen.log([" " * GUTTER + GREY + ("⏺" if i == 0 else " ") + " " + one + RESET
                             for i, one in enumerate(wrap(detail, width - GUTTER - 3))])
        elif state == "tool":
            # Both the start of a call and a running call saying what it is doing now arrive here
            # (`Ev.TOOL_STARTED` and `Ev.TOOL_PROGRESS` render the same shape). The same name means
            # the same call, so the phrase is replaced and the clock left alone — restarting it would
            # make a three-second walk look like three fast calls.
            self.status.begin()
            self.status.phase = "tool"
            same = self.screen.running and self.screen.running[0] == detail
            self.screen.running = (detail, data.get("call") or "",
                                   self.screen.running[2] if same else time.monotonic())
            self.screen.refresh()
        elif state in ("tool_done", "tool_failed"):
            # The running row leaves the bottom area and the finished one lands in the transcript in
            # one redraw, which is what makes a call one line rather than two.
            self.status.tools += 1
            self.screen.running = None
            self.status.phase = "thinking" if self.status.started is not None else "idle"
            self.screen.log(tool_rows("done" if state == "tool_done" else "fail", detail,
                                      data.get("call") or "", data.get("result") or "",
                                      data.get("seconds"), width))

    def _reply(self, data, width):
        self.screen.running = None
        if data.get("error") or data.get("cancelled"):
            self.status.end()
            self.note(data.get("error") or "interrupted",
                      RED if data.get("error") else YELLOW)
            return
        lines = []
        if data.get("text"):
            lines = [""] + markdown_lines(data["text"], width)
        # ONE NUMBER FOR THE AGENT, AND THE WAITING SHOWN SEPARATELY. The turn's wall clock is mostly
        # the character walking on a turn that walks anywhere — three seconds during which nothing
        # could have gone faster — so quoting it made a quick decision behind a long animation read as
        # a slow agent. The headline is what the agent spent; the wait is named beside it rather than
        # dropped, because a reader who sees only the small number will wonder where the time went.
        calls = data.get("tool_calls", 0)
        deciding = data.get("deciding_s")
        parts = ["%d tool%s" % (calls, "" if calls == 1 else "s"),
                 "%.1fs deciding" % (data.get("seconds", 0.0) or 0.0 if deciding is None
                                     else deciding)]
        if (data.get("engine_wait_s") or 0.0) >= 0.05:
            parts.append("+%.1fs waiting on motion" % data["engine_wait_s"])
        if data.get("generated"):
            parts.append("%d generated" % data["generated"])
        lines.append(" " * GUTTER + DIM + " · ".join(parts) + RESET)
        self.status.end()
        self.screen.log(lines)


# ---- the socket ----------------------------------------------------------------------------------

def reader(sock, ui, stop):
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
                    ui.show(json.loads(raw.decode("utf-8")))
                except ValueError:
                    ui.note("(unreadable message from the agent)", RED)
    except Farewell:
        said_goodbye = True
    except OSError:
        pass
    finally:
        ui.status.connected = False
        ui.status.end()
        ui.screen.running = None
        if not stop.is_set() and not said_goodbye:
            ui.note("the agent service closed the connection", RED)
        stop.set()


def protocol_version():
    """The version this console speaks, read from the contract instead of repeated here.

    THE BUG THIS CLOSES COST THREE SESSIONS. This file said `{"v": 3}` and the contract went to 4, so
    every line typed here was dropped by the console channel as malformed — logged into a file nobody
    was reading, invisible in this window — and it looked exactly like an intermittent hang in the
    model, which is what got investigated. The service answers a mismatch out loud now, and this reads
    the number rather than remembering it; either alone would have been enough, which is why both are
    here. Reading the constant out of the source is the fallback for a copy of this file living
    somewhere else. If neither works the version goes out as None, which the service refuses: a guess
    that is wrong again is worse than a refusal that says so. Grep for the constant, not the import.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        if here not in sys.path:
            sys.path.insert(0, here)
        from agent.protocol import PROTOCOL_VERSION
        return PROTOCOL_VERSION
    except Exception:                                # noqa: BLE001 — a console must still start
        pass
    try:
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


# ---- raw keys ------------------------------------------------------------------------------------

def keys_posix(stop, on_key):
    """Raw mode through termios, polled so the window can be closed by the agent as well as by you.
    50 ms is below what a typist can perceive, and the poll is also what tells a lone Esc from the
    start of an arrow key."""
    import select
    import termios
    import tty
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    tty.setraw(fd)
    pending = ""
    try:
        while not stop.is_set():
            ready, _, _ = select.select([fd], [], [], 0.05)
            if not ready:
                if pending:
                    keys, pending = decode(pending, final=True)
                    for key in keys:
                        on_key(key)
                continue
            data = os.read(fd, 4096)
            if not data:
                return on_key(("eof", None))
            pending += data.decode("utf-8", "replace")
            keys, pending = decode(pending)
            for key in keys:
                on_key(key)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def keys_windows(stop, on_key):
    """The same keys through `msvcrt`, the standard library's raw console on Windows.

    POLLED RATHER THAN BLOCKING, for the reason the old editor was: `getwch` cannot be woken, so a
    window at the prompt could not be closed by anything but the person in front of it — including by
    the agent saying the scene had stopped.
    """
    import msvcrt
    while not stop.is_set():
        if not msvcrt.kbhit():
            time.sleep(0.02)
            continue
        chunk = ""
        while msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch not in ("\x00", "\xe0"):               # not a two-part key: arrows, Home, Ctrl+←
                chunk += ch
                continue
            for key in decode(chunk, final=True)[0]:
                on_key(key)
            chunk = ""
            on_key(windows_key(msvcrt.getwch() if msvcrt.kbhit() else ""))
        for key in decode(chunk, final=True)[0]:
            on_key(key)


# ---- the app -------------------------------------------------------------------------------------

class App:
    """The key loop's half of the console: what a key does, and what a slash command means here."""

    def __init__(self, sock, screen, ui, stop):
        self.sock = sock
        self.screen = screen
        self.ui = ui
        self.stop = stop
        self.editor = screen.editor
        self.leaving = 0.0                   # when Ctrl+C was last pressed on an empty line

    def key(self, key):
        action = self.editor.apply(key)
        if action == "submit":
            text = self.editor.take()
            self.screen.refresh()
            if text:
                self.submit(text)
        elif action == "quit":
            self.stop.set()
        elif action == "interrupt":
            self.interrupt()
        elif action == "redraw":
            self.screen.clear()
        else:
            self.screen.refresh()

    def interrupt(self):
        """Ctrl+C, in three steps: the line, then the turn, then the window — so the key that stops a
        runaway turn is never one press away from closing the terminal by accident."""
        if self.ui.status.phase != "idle":
            self.ui.note("interrupting", YELLOW)
            self.send("/stop")
        elif time.monotonic() - self.leaving < 2.0:
            self.stop.set()
        else:
            self.leaving = time.monotonic()
            self.ui.note("Ctrl+C again to quit — the agent keeps running either way")

    def submit(self, text):
        width = self.screen.width()
        if text in ("/quit", "/exit"):
            self.stop.set()
        elif text == "/help":
            self.screen.log([""] + [DIM + line + RESET for line in HELP])
        elif text == "/clear":
            self.screen.clear()
        elif text == "/tools":
            self.screen.log([""] + [" " * GUTTER + GREY + one + RESET
                                    for one in wrap("  ".join(self.ui.tools) or "none yet",
                                                    width - GUTTER - 1)]
                            + [" " * GUTTER + DIM + "%d actions in the library" % self.ui.actions
                               + RESET])
        else:
            # Everything else goes up the socket, including /stop and /interrupt, which the service
            # handles in `console.route` — the terminal must not grow a second opinion about them.
            if not text.startswith("/"):
                self.ui.echo(text)
            self.send(text)

    def send(self, text):
        try:
            send(self.sock, text)
        except OSError:
            self.ui.note("could not send — the agent service is gone", RED)
            self.stop.set()


def ticker(screen, stop):
    """Turn the spinner and count the seconds. Nothing else drives a redraw on its own."""
    while not stop.wait(0.1):
        if screen.running or screen.status.phase != "idle":
            screen.status.tick += 1
            screen.refresh()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Attach a terminal to the animation agent.")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args(argv)

    enable_vt()
    try:
        # The glyphs this file writes are all in Windows Terminal's font; the phrases the AGENT sends
        # are not this file's to choose. On a console that cannot encode one of them, a status line
        # would raise inside the reader thread and the window would go quiet with no reason given.
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

    screen = Screen()
    screen.placeholder = "an instruction, in English · /help for keys"
    ui = Ui(screen)

    sock = socket.socket()
    try:
        sock.connect((args.host, args.port))
    except OSError as e:
        print("%scannot reach the agent service at %s:%d%s\n  %s\n  start it in WSL with: "
              "python cli.py --engine --headless" % (RED, args.host, args.port, RESET, e))
        return 1
    screen.status.connected = True

    stop = threading.Event()
    app = App(sock, screen, ui, stop)
    threading.Thread(target=reader, args=(sock, ui, stop), daemon=True).start()
    threading.Thread(target=ticker, args=(screen, stop), daemon=True).start()
    screen.log(DIM + " closing this window leaves the agent running; stopping play mode in Unity "
                     "closes both" + RESET)
    screen.refresh()

    try:
        (keys_windows if os.name == "nt" else keys_posix)(stop, app.key)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        screen.close()
        try:
            sock.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
