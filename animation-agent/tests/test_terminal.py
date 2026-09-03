"""The terminal, in the parts of it that can be checked without a terminal.

`terminal.py` is the one file in this repository that normally cannot be tested: it draws on a real
console with two threads writing at once, and half of what it does is only visible to a person. So the
drawing is factored into functions over values — wrapping, markdown, the tool rows, the status line,
the key decoder and the editor's transitions — and those are what is tested here. What is left
untested on purpose is `Screen`'s escape sequences, which are checked by running it.

Nothing here opens a socket, so it runs on Linux in the suite and on Windows by hand. One case
does open a pty — the padding that keeps the input box on the last rows is arithmetic about a
real window, and checking it against a StringIO would be checking the mock.
"""
import io
import os

import pytest

import terminal as T


def a_window(columns, rows):
    """`shutil.get_terminal_size` for a window of a fixed size. `os.terminal_size` because that is
    what the real one returns and `Screen` unpacks it."""
    return lambda fallback=None: os.terminal_size((columns, rows))


def plain(text):
    return T.strip_ansi(text)


# ---- text ----------------------------------------------------------------------------------------

def test_wrap_keeps_every_line_inside_the_width():
    text = "she walks to the chair and sits down on it without knocking anything over"
    for line in T.wrap(text, 20):
        assert len(line) <= 20


def test_wrap_breaks_a_word_that_cannot_fit():
    lines = T.wrap("mx_Sitting_Idle_With_A_Very_Long_Name", 12)
    assert all(len(line) <= 12 for line in lines)
    assert "".join(lines) == "mx_Sitting_Idle_With_A_Very_Long_Name"


def test_wrap_keeps_blank_lines_between_paragraphs():
    assert T.wrap("one\n\ntwo", 20) == ["one", "", "two"]


def test_clip_marks_the_cut():
    assert T.clip("abcdefgh", 4) == "abc…"
    assert T.clip("abc", 10) == "abc"
    assert T.clip("abc", 0) == ""


def test_share_keeps_both_phrases_when_they_fit():
    assert T.share("a chair", "found it", 40) == ("a chair", "found it")


def test_share_cuts_the_greedy_half_and_leaves_the_short_one_whole():
    left, right = T.share("x" * 60, "ok", 20)
    assert right == "ok"
    assert len(left) <= 20


# ---- markdown ------------------------------------------------------------------------------------

def test_markdown_renders_bullets_as_bullets():
    lines = [plain(line) for line in T.markdown_lines("- one\n- two", 60)]
    assert lines == ["  • one", "  • two"]


def test_markdown_keeps_the_number_of_a_numbered_list():
    lines = [plain(line) for line in T.markdown_lines("1. first\n2. second", 60)]
    assert lines == ["  1. first", "  2. second"]


def test_markdown_hangs_a_wrapped_bullet_under_its_text():
    lines = [plain(line) for line in T.markdown_lines("- " + "word " * 20, 40)]
    assert lines[0].startswith("  • word")
    assert lines[1].startswith("    word")


def test_markdown_styles_inline_code_and_removes_the_backticks():
    line = T.markdown_lines("play `mx_Walk_Forward` now", 60)[0]
    assert "`" not in line
    assert T.CODE in line
    assert plain(line) == "  play mx_Walk_Forward now"


def test_markdown_drops_the_fence_and_keeps_the_code():
    lines = [plain(line) for line in T.markdown_lines("see:\n```\nunity_execute\n```", 60)]
    assert lines == ["  see:", "    unity_execute"]


def test_markdown_stays_inside_the_width():
    text = "- " + "long " * 40 + "\n\nplain paragraph " * 5
    for line in T.markdown_lines(text, 50):
        assert len(plain(line)) <= 50


# ---- rows ----------------------------------------------------------------------------------------

def done(name, phrase, result, seconds, width):
    rows = T.tool_rows("done", name, phrase, result, seconds, width)
    assert len(rows) == 1
    return plain(rows[0])


def test_a_finished_call_says_what_it_asked_and_what_came_back():
    row = done("motion_search", '"sit on a chair"', "mx_Sitting_Idle +2", 0.42, 100)
    assert "motion_search" in row
    assert '"sit on a chair"' in row
    assert "mx_Sitting_Idle +2" in row
    assert row.rstrip().endswith("0.42s")
    assert len(row) <= 99


def test_a_fast_call_still_shows_a_time():
    row = done("read", "a/b.json", "ok", 0.004, 100)
    assert "0.00s" in row


def test_a_finished_call_fits_a_narrow_window():
    row = done("motion_transition", "x" * 60, "y" * 60, 12.5, 60)
    assert len(row) <= 59


def test_a_running_call_shows_a_spinner_and_the_seconds_so_far():
    row = plain(T.tool_rows("run", "unity_locomotion", "the chair", "", 1.25, 100, 0)[0])
    assert row.lstrip().startswith(T.SPIN[0])
    assert "unity_locomotion" in row
    assert "1.2s" in row


def test_the_spinner_turns_with_the_tick():
    first = plain(T.tool_rows("run", "read", "x", "", 1.0, 80, 0)[0])[2]
    second = plain(T.tool_rows("run", "read", "x", "", 1.0, 80, 1)[0])[2]
    assert first != second


def test_a_failed_call_keeps_the_whole_result_wrapped_and_red():
    rows = T.tool_rows("fail", "unity_execute", "sit on the chair",
                       "the seat is occupied and the plan was refused " * 3, 2.0, 60)
    assert len(rows) > 1
    assert T.RED in rows[0]
    assert all(T.RED in row for row in rows[1:])
    assert all(len(plain(row)) <= 59 for row in rows)
    body = " ".join(plain(row).strip() for row in rows[1:])
    assert "the seat is occupied" in body
    assert body.count("refused") == 3          # nothing was cut


# ---- the status line -----------------------------------------------------------------------------

def make_status(**kw):
    status = T.Status()
    status.connected = True
    status.model = "gpt-5.6-terra"
    status.engine = "engine connected"
    for key, value in kw.items():
        setattr(status, key, value)
    return status


def test_the_status_line_says_ready_when_nothing_is_running():
    line = plain(T.status_line(100, make_status()))
    assert "ready" in line
    assert "gpt-5.6-terra" in line
    assert "engine connected" in line


def test_the_status_line_counts_the_turn_and_its_tools():
    status = make_status(phase="thinking", tools=3)
    status.started = T.time.monotonic() - 4.2
    line = plain(T.status_line(100, status))
    assert "thinking" in line
    assert "3 tools" in line
    assert "4.2s" in line


def test_the_status_line_names_a_lost_connection():
    line = T.status_line(100, make_status(connected=False))
    assert "not connected" in plain(line)
    assert T.RED in line


def test_a_narrow_window_drops_the_model_before_the_turn():
    line = plain(T.status_line(42, make_status(phase="thinking", tools=1)))
    assert "gpt-5.6-terra" not in line
    assert "thinking" in line
    assert len(line) <= 41


# ---- the input line ------------------------------------------------------------------------------

def test_the_input_line_puts_the_cursor_where_the_cursor_is():
    text, column = T.input_line(80, "sit down", 3)
    assert plain(text) == " › sit down"
    assert column == 6


def test_the_input_line_shows_a_placeholder_only_when_it_is_empty():
    text, column = T.input_line(80, "", 0, "say something")
    assert "say something" in plain(text)
    assert column == 3
    text, _ = T.input_line(80, "a", 1, "say something")
    assert "say something" not in plain(text)


def test_a_long_line_scrolls_sideways_and_keeps_the_cursor_on_screen():
    buffer = "x" * 300
    text, column = T.input_line(60, buffer, 300)
    assert len(plain(text)) <= 60
    assert column <= 59


# ---- the key decoder -----------------------------------------------------------------------------

def test_printable_characters_arrive_as_one_key():
    keys, rest = T.decode("hello")
    assert keys == [("text", "hello")]
    assert rest == ""


def test_a_paste_with_a_newline_is_text_then_submit():
    keys, _ = T.decode("one\ntwo")
    assert keys == [("text", "one"), ("submit", None), ("text", "two")]


def test_the_arrows_decode():
    keys, _ = T.decode("\033[A\033[B\033[C\033[D")
    assert [k[0] for k in keys] == ["up", "down", "right", "left"]


def test_word_jumps_decode_from_both_the_control_and_the_alt_form():
    assert T.decode("\033[1;5D")[0] == [("word-left", None)]
    assert T.decode("\033[1;5C")[0] == [("word-right", None)]
    assert T.decode("\033b")[0] == [("word-left", None)]


def test_home_end_and_delete_decode():
    keys, _ = T.decode("\033[H\033[F\033[3~")
    assert [k[0] for k in keys] == ["home", "end", "delete"]


def test_the_control_keys_decode():
    keys, _ = T.decode("\x01\x05\x17\x03\x04\x7f\r")
    assert [k[0] for k in keys] == ["home", "end", "kill-word", "interrupt", "eof",
                                    "backspace", "submit"]


def test_a_half_arrived_escape_sequence_is_kept_for_the_next_read():
    keys, rest = T.decode("ab\033[")
    assert keys == [("text", "ab")]
    assert rest == "\033["
    keys, rest = T.decode(rest + "A")
    assert keys == [("up", None)]
    assert rest == ""


def test_a_lone_escape_is_only_escape_once_nothing_follows_it():
    assert T.decode("\033") == ([], "\033")
    assert T.decode("\033", final=True)[0] == [("esc", None)]


def test_an_unknown_sequence_does_not_type_its_letters():
    keys, _ = T.decode("\033[200~hi", final=True)
    assert keys == [("text", "hi")]


def test_a_windows_two_part_key_maps_to_the_same_names():
    assert T.windows_key("H") == ("up", None)
    assert T.windows_key("s") == ("word-left", None)
    assert T.windows_key("?") == ("none", None)


# ---- the editor ----------------------------------------------------------------------------------

def test_typing_inserts_at_the_cursor():
    editor = T.Editor()
    editor.apply(("text", "sit down"))
    editor.apply(("home", None))
    editor.apply(("text", "please "))
    assert editor.text == "please sit down"
    assert editor.cursor == 7


def test_backspace_and_delete_work_in_the_middle():
    editor = T.Editor()
    editor.apply(("text", "abcd"))
    editor.apply(("left", None))
    editor.apply(("backspace", None))
    assert (editor.text, editor.cursor) == ("abd", 2)
    editor.apply(("delete", None))
    assert editor.text == "ab"


def test_word_jumps_move_over_words_not_characters():
    editor = T.Editor()
    editor.apply(("text", "walk to the chair"))
    editor.apply(("word-left", None))
    assert editor.cursor == len("walk to the ")
    editor.apply(("word-left", None))
    assert editor.cursor == len("walk to ")
    editor.apply(("word-right", None))
    assert editor.cursor == len("walk to the")


def test_kill_word_removes_the_word_before_the_cursor():
    editor = T.Editor()
    editor.apply(("text", "walk to the chair"))
    editor.apply(("kill-word", None))
    assert editor.text == "walk to the "


def test_cutting_to_either_edge():
    editor = T.Editor()
    editor.apply(("text", "abcdef"))
    editor.apply(("left", None))
    editor.apply(("left", None))
    editor.apply(("kill-end", None))
    assert editor.text == "abcd"
    editor.apply(("kill-start", None))
    assert (editor.text, editor.cursor) == ("", 0)


def test_up_goes_to_the_start_of_the_line_first_and_to_history_second():
    editor = T.Editor(["sit on the chair"])
    editor.apply(("text", "half typed"))
    editor.apply(("up", None))
    assert editor.text == "half typed" and editor.cursor == 0
    editor.apply(("up", None))
    assert editor.text == "sit on the chair"


def test_down_comes_back_to_what_was_being_typed():
    editor = T.Editor(["one", "two"])
    editor.apply(("text", "draft"))
    editor.apply(("up", None))                 # to the start of the line
    editor.apply(("up", None))                 # "two"
    editor.apply(("up", None))                 # "one"
    assert editor.text == "one"
    editor.apply(("down", None))
    assert editor.text == "two"
    editor.apply(("down", None))
    assert editor.text == "draft"


def test_taking_the_line_records_it_once():
    editor = T.Editor()
    editor.apply(("text", "  sit down  "))
    assert editor.take() == "sit down"
    assert editor.history == ["sit down"]
    editor.apply(("text", "sit down"))
    editor.take()
    assert editor.history == ["sit down"]      # not twice in a row
    assert editor.text == "" and editor.cursor == 0


def test_a_pasted_newline_does_not_send_half_an_instruction():
    editor = T.Editor()
    editor.apply(("text", "walk over\nand sit"))
    assert editor.text == "walk over and sit"


def test_escape_clears_the_line():
    editor = T.Editor()
    editor.apply(("text", "wrong"))
    editor.apply(("esc", None))
    assert editor.text == ""


def test_ctrl_c_clears_a_line_before_it_interrupts_anything():
    editor = T.Editor()
    editor.apply(("text", "wrong"))
    assert editor.apply(("interrupt", None)) is None
    assert editor.text == ""
    assert editor.apply(("interrupt", None)) == "interrupt"


def test_ctrl_d_only_quits_on_an_empty_line():
    editor = T.Editor()
    editor.apply(("text", "x"))
    assert editor.apply(("eof", None)) is None
    editor.set("")
    assert editor.apply(("eof", None)) == "quit"


def test_enter_submits():
    assert T.Editor().apply(("submit", None)) == "submit"


# ---- a turn, end to end, on a screen that is not a terminal --------------------------------------

def turn(events):
    out = io.StringIO()
    screen = T.Screen(out=out)
    ui = T.Ui(screen)
    for event in events:
        ui.show(event)
    return ui, plain(out.getvalue())


def status(state, **data):
    return {"type": "agent.status", "data": dict(data, state=state)}


def test_a_whole_turn_reads_as_a_transcript():
    ui, text = turn([
        {"type": "console.hello", "data": {"model": "gpt-5.6-terra", "actions": 2446,
                                           "tools": ["motion_search", "unity_execute"],
                                           "engine": "connected"}},
        status("thinking", detail="sit on the chair"),
        status("tool", detail="motion_search", call='"sit"'),
        status("tool_done", detail="motion_search", call='"sit"',
               result="mx_Sitting_Idle", seconds=0.4),
        status("said", detail="I found a sitting clip"),
        status("tool_failed", detail="unity_execute", call="sit on chair_1",
               result="the seat is occupied", seconds=1.5),
        {"type": "agent.reply", "data": {"text": "She is sitting now.", "tool_calls": 2,
                                         "deciding_s": 3.1, "engine_wait_s": 2.0}},
        {"type": "gate.verdict", "data": {"status": "pass", "detail": "hips within 4 cm"}},
    ])
    assert "attached" in text and "2446 actions" in text
    assert "motion_search" in text and "mx_Sitting_Idle" in text
    assert "I found a sitting clip" in text
    assert "the seat is occupied" in text
    assert "She is sitting now." in text
    assert "2 tools · 3.1s deciding · +2.0s waiting on motion" in text
    assert "✓ verified hips within 4 cm" in text
    assert ui.status.tools == 2
    assert ui.status.phase == "idle"


def test_hello_fills_in_the_footer():
    ui, _ = turn([{"type": "console.hello",
                   "data": {"model": "m", "actions": 3, "tools": ["a"], "engine": "waiting"}}])
    assert ui.status.model == "m"
    assert ui.status.engine == "engine waiting"
    assert ui.tools == ["a"]


def test_a_running_call_lives_in_the_bottom_area_not_in_the_transcript():
    ui, text = turn([status("thinking", detail="x"),
                     status("tool", detail="unity_locomotion", call="the chair")])
    assert ui.screen.running[0] == "unity_locomotion"
    assert "unity_locomotion" not in text          # nothing appended until it finishes
    assert ui.status.phase == "tool"


def test_a_running_call_saying_more_does_not_restart_its_clock():
    ui, _ = turn([status("thinking", detail="x"),
                  status("tool", detail="unity_locomotion", call="walking"),
                  status("tool", detail="unity_locomotion", call="halfway there")])
    assert ui.screen.running[1] == "halfway there"
    started = ui.screen.running[2]
    ui.show(status("tool", detail="unity_locomotion", call="nearly"))
    assert ui.screen.running[2] == started


def test_a_finished_call_leaves_the_bottom_area():
    ui, text = turn([status("thinking", detail="x"),
                     status("tool", detail="read", call="a.json"),
                     status("tool_done", detail="read", call="a.json", result="ok", seconds=0.1)])
    assert ui.screen.running is None
    assert "read" in text and "ok" in text


def test_what_you_typed_is_echoed_as_its_own_block():
    out = io.StringIO()
    ui = T.Ui(T.Screen(out=out))
    ui.echo("walk to the chair and sit")
    assert "› walk to the chair and sit" in plain(out.getvalue())


def test_an_error_reply_is_shown_and_ends_the_turn():
    ui, text = turn([status("thinking", detail="x"),
                     {"type": "agent.reply", "data": {"text": "", "error": "the model timed out"}}])
    assert "the model timed out" in text
    assert ui.status.phase == "idle"


def test_a_cancelled_turn_says_so():
    _, text = turn([{"type": "agent.reply", "data": {"cancelled": True}}])
    assert "interrupted" in text


def test_the_farewell_ends_the_session():
    out = io.StringIO()
    ui = T.Ui(T.Screen(out=out))
    with pytest.raises(T.Farewell):
        ui.show({"type": "console.bye", "data": {"reason": "Unity left play mode"}})
    assert "Unity left play mode" in plain(out.getvalue())


def test_a_screen_that_is_not_a_terminal_draws_no_bottom_area():
    out = io.StringIO()
    screen = T.Screen(out=out)
    screen.log("hello")
    screen.refresh()
    assert out.getvalue() == "hello\n"



def test_markdown_takes_the_asterisks_off_bold_text():
    line = T.markdown_lines("the walk was **1.1 m** long", 60)[0]
    assert "*" not in line
    assert T.BOLD in line
    assert plain(line) == "  the walk was 1.1 m long"


# ---- the bottom area, pinned to the last rows -----------------------------------------------------

def test_padding_pushes_an_empty_transcript_down_to_the_last_rows():
    """A fresh attach: nothing has been printed, so the whole window above the box is padding."""
    assert T.bottom_padding(rows=30, filled=0, height=3) == 27


def test_padding_stops_once_the_transcript_fills_the_window():
    """Past that point the terminal's own scrolling keeps the last written row on the last row."""
    assert T.bottom_padding(rows=30, filled=27, height=3) == 0
    assert T.bottom_padding(rows=30, filled=400, height=3) == 0


def test_a_taller_bottom_area_takes_its_extra_row_out_of_the_padding():
    """A running tool adds a row to the box, and it comes off the top of the padding rather than
    pushing the box below the last row."""
    assert T.bottom_padding(rows=30, filled=0, height=4) == 26


def test_padding_never_goes_negative_in_a_window_too_short_for_the_box():
    assert T.bottom_padding(rows=3, filled=0, height=4) == 0


@pytest.mark.skipif(not hasattr(os, "openpty"), reason="posix only")
def test_the_bottom_area_lands_on_the_last_rows_of_a_real_pty(monkeypatch):
    """The arithmetic against a real terminal: attach on an empty 24-row window and count the rows
    the screen actually wrote. Linux only — the Windows side is checked by hand against
    `tests/fake_console.py`."""
    master, slave = os.openpty()
    monkeypatch.setattr(T.shutil, "get_terminal_size", a_window(100, 24))
    with os.fdopen(slave, "w", encoding="utf-8", newline="") as tty:
        screen = T.Screen(out=tty)
        assert screen.interactive, "a pty has to look like a terminal"
        screen.refresh()
        # 21 blank rows of padding, then the three rows of the box: the last row written is row 24.
        assert screen._filled == 21
        assert screen._drawn == 3

        screen.log("one tool call")
        assert screen._filled == 22, "the transcript grew by a row and the box did not move"
        assert T.bottom_padding(24, screen._filled, 3) == 0
    os.close(master)


def test_the_window_growing_re_pads_so_the_box_does_not_float(monkeypatch):
    """No resize signal on Windows, so the size is re-read on every draw. A window that gained rows
    gained them BELOW the box, and they have to be padded or the input sits in the middle."""
    out = io.StringIO()
    out.isatty = lambda: True
    size = [(100, 24)]
    monkeypatch.setattr(T.shutil, "get_terminal_size",
                        lambda fallback=None: os.terminal_size(size[0]))
    screen = T.Screen(out=out)
    screen.refresh()
    assert screen._filled == 21

    size[0] = (100, 40)
    screen.refresh()
    assert screen._filled == 37, "the sixteen new rows were padded, not left under the box"
