"""The ReAct loop. The cases that matter are steering, failure survival, and the budgets."""
import asyncio

import pytest

from agent.kbindex import KBIndex
from agent.loop import Ev, Op, Session
from agent.tools import ToolFailure, ToolRegistry
from agent.tools import kb as kb_tools
from tests.scripted_llm import ScriptedBackend, calls, says

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def kb():
    return KBIndex.load()


def make(script, kb, hold=None, **kwargs):
    registry = kb_tools.register(ToolRegistry(), kb)
    return Session(ScriptedBackend(script, hold=hold), registry, "test instructions", **kwargs)


async def test_a_turn_runs_tools_then_answers(kb):
    session = make([calls(("motion_search", {"query": "chest compressions"})),
                    says("cpr")], kb)
    await session.start()
    report = await session.run_turn("press on the chest")

    assert report.ok
    assert report.text == "cpr"
    assert report.tools_used() == ["motion_search"]
    assert report.iterations == 2
    await session.close()


async def test_new_text_mid_turn_is_folded_in_at_the_next_iteration(kb):
    """The whole point of the submission/turn split: input arriving during a turn neither aborts it nor
    splices into the in-flight response — it is drained at the TOP of the next iteration."""
    hold = asyncio.Event()
    session = make([calls(("motion_search", {"query": "walk"})),
                    calls(("motion_search", {"query": "carry"})),
                    says("walking + grab_bottle")], kb, hold=hold)
    await session.start()

    turn = asyncio.ensure_future(session.run_turn("walk across the room"))
    await asyncio.sleep(0.02)                   # the turn is now parked inside its first response
    assert session.backend.user_messages == ["walk across the room"]

    session.pending_input.append("actually carry the bottle too")
    hold.set()
    report = await turn

    assert report.steered == ["actually carry the bottle too"]
    # It reached the model as a user message, in the same turn, after the first response completed.
    assert session.backend.user_messages == ["walk across the room", "actually carry the bottle too"]
    assert report.iterations >= 2
    await session.close()


async def test_submission_loop_routes_to_a_new_turn_when_idle(kb):
    session = make([says("ok")], kb)
    await session.start()
    await session.submit_text("hello")
    await asyncio.sleep(0.05)
    await session.wait_idle()

    assert session.last_turn.text == "ok"
    await session.close()


async def test_submission_loop_steers_instead_of_starting_a_second_turn(kb):
    hold = asyncio.Event()
    session = make([calls(("motion_search", {"query": "a"})), says("done")], kb, hold=hold)
    await session.start()

    session.turn = asyncio.ensure_future(session.run_turn("first"))
    await asyncio.sleep(0.02)                   # parked in the first response
    await session.submit_text("second")
    await asyncio.sleep(0.02)                   # submission loop routes it while the turn is live
    hold.set()
    await session.wait_idle()

    assert "second" in session.backend.user_messages
    assert session.last_turn.steered == ["second"]     # one turn, not two
    await session.close()


async def test_a_failed_tool_does_not_end_the_turn(kb):
    """This is the mechanism a rejected geometric gate will use, so it has to be the default path."""
    session = make([calls(("motion_channels", {"action_id": "moonwalk"})),
                    calls(("motion_search", {"query": "walk"})),
                    says("walking")], kb)
    await session.start()
    report = await session.run_turn("do a moonwalk")

    assert report.ok
    assert report.text == "walking"
    assert report.trace[0]["success"] is False
    assert "moonwalk" in report.trace[0]["error"]
    # The failure was handed back to the model as an ordinary result.
    assert session.backend.tool_results[0][1]["success"] is False
    await session.close()


async def test_iteration_budget_stops_a_tool_loop(kb):
    """A latency-tuned mini model ping-ponging between two tools is a real failure mode, and an
    unbounded loop against a paid API is a bug you find on the invoice."""
    session = make([calls(("motion_search", {"query": "x"}))] * 20, kb, max_iterations=3)
    await session.start()
    report = await session.run_turn("loop forever")

    assert report.exhausted
    assert report.iterations == 3
    assert "iteration budget" in report.text
    await session.close()


async def test_tool_call_budget_stops_a_wide_fan_out(kb):
    session = make([calls(*[("motion_search", {"query": "x"})] * 5)] * 10, kb, max_tool_calls=6)
    await session.start()
    report = await session.run_turn("call everything")

    assert report.exhausted
    assert report.tool_calls <= 6
    assert "tool-call budget" in report.text
    await session.close()


async def test_interrupt_cancels_the_turn_and_the_response(kb):
    session = make([says("too late")], kb, hold=asyncio.Event())   # never released
    await session.start()
    session.turn = asyncio.ensure_future(session.run_turn("start something long"))
    await asyncio.sleep(0.02)

    await session.submit(Op(Op.INTERRUPT))
    await asyncio.sleep(0.05)

    assert session.backend.cancelled == 1
    assert session.turn.cancelled() or session.turn.done()
    await session.close()


async def test_events_reach_the_ui_in_order(kb):
    session = make([calls(("motion_search", {"query": "walk"})), says("walking")], kb)
    await session.start()
    seen = []
    session.on_event(lambda kind, data: seen.append(kind))
    await session.run_turn("walk")

    assert seen[0] == Ev.TURN_STARTED
    assert Ev.TOOL_STARTED in seen and Ev.TOOL_FINISHED in seen
    assert seen[-1] == Ev.TURN_COMPLETE
    assert seen.index(Ev.TOOL_STARTED) < seen.index(Ev.TOOL_FINISHED)
    await session.close()


async def test_images_go_out_as_a_message_and_stay_out_of_the_tool_result(kb):
    """A megabyte of base64 inside a function_call_output is useless to the model and expensive, so the
    loop moves it to a user message. Both halves are checked: gone from one, present in the other.

    `read` is what produces these now — a rendered frame is a file with an image extension, not a
    separate kind of access — so this invariant covers every picture the agent can look at.
    """
    reg = ToolRegistry()
    reg.add("fake_frames", "returns a picture",
            {"type": "object", "additionalProperties": False, "properties": {}},
            lambda: {"path": "kb/frames/Typing/front_f17.png",
                     "images": [{"data_uri": "data:image/png;base64,AAAA", "caption": "a frame"}]})

    backend = ScriptedBackend([calls(("fake_frames", {})), says("looked at it")])
    session = Session(backend, reg, "test instructions")
    await session.start()
    report = await session.run_turn("look")
    await session.close()

    assert report.tool_calls == 1
    _, result = backend.tool_results[0]
    assert "images" not in result
    assert len(backend.user_images) == 1
    assert backend.user_images[0]["caption"] == "a frame"


async def test_a_scheduled_check_runs_after_the_turn_and_does_not_delay_it(kb):
    """A generated sit is not measurable until the descent has run, seconds after the plan is
    committed. The reply must not wait for that, and the check must still happen."""
    ran = asyncio.Event()
    reg = ToolRegistry()
    reg.add("fake_plan", "commits a plan",
            {"type": "object", "additionalProperties": False, "properties": {}},
            lambda: {"committed": True,
                     "verify": {"status": "scheduled", "tool": "fake_check", "arguments": {},
                                "confirms": "the pelvis landed on obj:Chair"}})

    async def check():
        ran.set()
        return {"landed": True}

    reg.add("fake_check", "measures the landing",
            {"type": "object", "additionalProperties": False, "properties": {}}, check)

    seen = []
    session = Session(ScriptedBackend([calls(("fake_plan", {})), says("sitting down")]), reg, "t")
    session.on_event(lambda kind, data: seen.append((kind, data)))
    await session.start()
    report = await session.run_turn("sit down and type")

    assert report.text == "sitting down"
    assert report.tools_used() == ["fake_plan"]      # the check is NOT one of the turn's iterations
    await asyncio.wait_for(ran.wait(), timeout=2)
    for _ in range(10):                              # let the emit land
        if any(kind == Ev.VERDICT for kind, _ in seen):
            break
        await asyncio.sleep(0.01)
    verdict = [data for kind, data in seen if kind == Ev.VERDICT]
    assert verdict and verdict[0]["success"] is True
    assert verdict[0]["detail"] == "the pelvis landed on obj:Chair"
    await session.close()


async def test_a_failed_scheduled_check_comes_back_as_a_new_turn(kb):
    """The correction goes through the ordinary input path — `submit_text`, the same one the user's
    keyboard uses — so it queues behind whatever is running instead of interleaving with it. Asserted
    on what the model actually received, not on the queue: reading the queue in the test would race
    the submission loop that is supposed to drain it."""
    reg = ToolRegistry()
    reg.add("fake_plan", "commits a plan",
            {"type": "object", "additionalProperties": False, "properties": {}},
            lambda: {"verify": {"status": "scheduled", "tool": "fake_check", "arguments": {},
                                "on_failure": "the sit you just committed did not land:"}})

    async def check():
        raise ToolFailure("the pelvis ended up 1.4 m outside the footprint of obj:Chair")

    reg.add("fake_check", "measures the landing",
            {"type": "object", "additionalProperties": False, "properties": {}}, check)

    backend = ScriptedBackend([calls(("fake_plan", {})), says("sitting down"), says("let me fix that")])
    session = Session(backend, reg, "t")
    await session.start()
    await session.run_turn("sit down and type")

    for _ in range(100):
        if len(backend.user_messages) > 1:
            break
        await asyncio.sleep(0.01)
    assert len(backend.user_messages) == 2, "the failed check never reached the model"
    correction = backend.user_messages[1]
    assert correction.startswith("the sit you just committed did not land:")
    assert "1.4 m outside the footprint" in correction
    await session.close()


async def test_a_check_that_cannot_run_is_reported_as_not_passed(kb):
    """The watcher is detached, so an exception in it has nobody to propagate to — it would surface as
    an unretrieved task exception and the verification would silently not have happened. Not running
    and not passing have to look the same from outside."""
    reg = ToolRegistry()
    reg.add("fake_plan", "commits a plan",
            {"type": "object", "additionalProperties": False, "properties": {}},
            lambda: {"verify": {"status": "scheduled", "tool": "fake_check", "arguments": {}}})

    async def check():
        raise RuntimeError("the engine went away")

    reg.add("fake_check", "measures the landing",
            {"type": "object", "additionalProperties": False, "properties": {}}, check)

    seen = []
    session = Session(ScriptedBackend([calls(("fake_plan", {})), says("sitting down")]), reg, "t")
    session.on_event(lambda kind, data: seen.append((kind, data)))
    await session.start()
    await session.run_turn("sit down and type")

    for _ in range(100):
        if any(kind == Ev.VERDICT for kind, _ in seen):
            break
        await asyncio.sleep(0.01)
    verdict = [data for kind, data in seen if kind == Ev.VERDICT]
    assert verdict and verdict[0]["success"] is False
    assert "the engine went away" in verdict[0]["detail"]
    await session.close()


def _plan_registry():
    """A stand-in for the pair of plan tools, answering the one field the trace reads.

    `unity_execute` and `unity_validate` take IDENTICAL arguments, so nothing about a call says
    whether anything moved. That is the whole reason the trace reads `committed` off the result: the
    field this fake returns is the field the real tools return, and it is the only difference between
    them that a reader can see.
    """
    registry = ToolRegistry()
    schema = {"type": "object", "properties": {"base": {"type": "string"}},
              "required": ["base"], "additionalProperties": False}

    async def unity_execute(base):
        return {"committed": True, "base": base}

    async def unity_validate(base):
        return {"committed": False, "base": base}

    registry.add("unity_execute", "play it", schema, unity_execute)
    registry.add("unity_validate", "check it", dict(schema), unity_validate)
    return registry


async def test_the_decision_is_timed_by_what_the_tool_did_not_by_what_it_was_asked(kb):
    """Two tools, identical arguments. Reading the ARGUMENTS cannot tell a committed motion from a
    checked one, and when the plan tool had a defaulted `mode` that is exactly what went wrong --
    measured on a real turn that committed a walk-and-sit and came back motion_at_s=None."""
    session = Session(ScriptedBackend([calls(("unity_execute", {"base": "mx_Typing"})),
                                       says("sitting down")]), _plan_registry(), "test")
    await session.start()
    report = await session.run_turn("sit down and type")

    assert report.motion_at() is not None
    assert report.as_dict()["motion_at_s"] == report.trace[0]["at_s"]
    await session.close()


async def test_a_validation_is_not_a_moment_anything_moved(kb):
    session = Session(ScriptedBackend([calls(("unity_validate", {"base": "mx_Typing"})),
                                       says("here is what it would do")]), _plan_registry(), "test")
    await session.start()
    report = await session.run_turn("what would sitting down look like")

    assert report.motion_at() is None
    await session.close()


async def test_a_tool_event_carries_enough_to_read_the_turn(kb):
    """A column of bare tool names says the agent is alive and nothing else. Four `unity_query` calls in
    a row look identical whether they are narrowing on a chair or re-asking a question already
    answered. Composed in the loop rather than in a renderer, so the stdin session and an attached
    terminal cannot describe the same turn differently."""
    session = make([calls(("motion_search", {"query": "sit and type"})), says("typing it is")], kb)
    await session.start()
    events = []
    session.on_event(lambda kind, data: events.append((kind, data)))
    await session.run_turn("sit and type")

    started = [d for k, d in events if k == Ev.TOOL_STARTED][0]
    finished = [d for k, d in events if k == Ev.TOOL_FINISHED][0]

    assert started["call"] == '"sit and type"', "the call has to say what it asked for"
    assert finished["call"] == started["call"]
    assert finished["result"], "a successful call used to show as its own name and nothing else"
    assert finished["seconds"] >= 0.0
    await session.close()


async def test_what_the_model_says_between_calls_is_shown(kb):
    """A turn that spends four iterations makes four decisions and only the last of them reaches the
    reply. Emitted whole rather than per fragment: a delta is a few characters and no console can show
    one usefully."""
    session = make([calls(("motion_search", {"query": "walk"}), text="Looking for a walk cycle."),
                    says("walking")], kb)
    await session.start()
    said = []
    session.on_event(lambda kind, data: said.append(data["text"]) if kind == Ev.TEXT else None)
    await session.run_turn("walk")

    assert said == ["Looking for a walk cycle."]
    await session.close()


# ---- what a turn spent, split by what it went on ------------------------------------------------

async def test_waiting_on_the_engine_is_not_counted_as_deciding(kb):
    """The number a person reads. A turn that walks her across the room contains seconds during which
    nothing could have gone faster, and rolling them into the total made a quick decision behind a long
    animation read as a slow agent."""
    registry = kb_tools.register(ToolRegistry(), kb)

    async def slow_walk():
        # Exactly what the real walk poll does: sleep, then declare the sleep as engine time.
        await asyncio.sleep(0.3)
        registry.progress.waited(0.3)
        return {"arrived": True}

    registry.add("fake_walk", "walk somewhere",
                 {"type": "object", "properties": {}, "additionalProperties": False}, slow_walk)
    session = Session(ScriptedBackend([calls(("fake_walk", {})), says("she is there")]),
                      registry, "test instructions")
    await session.start()
    report = await session.run_turn("walk over there")

    assert report.engine_wait_s() == pytest.approx(0.3, abs=0.01)
    assert report.seconds >= 0.3
    assert report.decision_seconds() < report.seconds
    assert report.decision_seconds() == pytest.approx(report.seconds - 0.3, abs=0.01)
    await session.close()


async def test_a_turn_that_moves_nothing_spends_it_all_deciding(kb):
    """The other half of the same claim. Nothing waited on the engine, so nothing is subtracted and
    the headline number is the whole turn."""
    session = make([calls(("motion_search", {"query": "walk"})), says("walking")], kb)
    await session.start()
    report = await session.run_turn("walk")

    assert report.engine_wait_s() == 0.0
    assert report.decision_seconds() == pytest.approx(report.seconds)
    await session.close()


async def test_the_wait_is_recorded_per_call_not_only_in_the_total(kb):
    """A turn is several calls and only some of them wait. The trace has to say which, or the split
    cannot be checked against anything afterwards."""
    registry = kb_tools.register(ToolRegistry(), kb)

    async def slow_walk():
        await asyncio.sleep(0.2)
        registry.progress.waited(0.2)
        return {"arrived": True}

    registry.add("fake_walk", "walk somewhere",
                 {"type": "object", "properties": {}, "additionalProperties": False}, slow_walk)
    session = Session(ScriptedBackend([calls(("motion_search", {"query": "walk"})),
                                       calls(("fake_walk", {})), says("done")]),
                      registry, "test instructions")
    await session.start()
    report = await session.run_turn("go")

    waits = {step["tool"]: step.get("engine_wait_s") for step in report.trace}
    assert waits["motion_search"] is None, "a call that waited on nothing must not claim it did"
    assert waits["fake_walk"] == pytest.approx(0.2, abs=0.01)
    await session.close()


async def test_a_running_tool_can_say_what_it_is_doing(kb):
    """A tool result arrives when the tool is finished, so a three-second walk was three seconds of
    nothing on screen. This is the channel that fixes it -- opencode's Context.metadata()."""
    registry = kb_tools.register(ToolRegistry(), kb)

    async def slow_walk():
        registry.progress("walking 2.8 m")
        await asyncio.sleep(0.05)
        registry.progress("walking, 1.1 m to go")
        return {"arrived": True}

    registry.add("fake_walk", "walk somewhere",
                 {"type": "object", "properties": {}, "additionalProperties": False}, slow_walk)
    session = Session(ScriptedBackend([calls(("fake_walk", {})), says("there")]),
                      registry, "test instructions")
    await session.start()
    said = []
    session.on_event(lambda kind, data: said.append(data["detail"])
                     if kind == Ev.TOOL_PROGRESS else None)
    await session.run_turn("walk")

    assert said == ["walking 2.8 m", "walking, 1.1 m to go"]
    await session.close()


async def test_the_display_block_never_reaches_the_model(kb):
    """`_display` is for the person watching. A turn is round trips, and every token of display copy in
    a tool result is paid for again on each one."""
    registry = kb_tools.register(ToolRegistry(), kb)

    async def slow_walk():
        registry.progress("walking 2.8 m")
        registry.progress.waited(0.1)
        return {"arrived": True}

    registry.add("fake_walk", "walk somewhere",
                 {"type": "object", "properties": {}, "additionalProperties": False}, slow_walk)
    backend = ScriptedBackend([calls(("fake_walk", {})), says("there")])
    session = Session(backend, registry, "test instructions")
    await session.start()
    await session.run_turn("walk")

    submitted = [str(r) for r in getattr(backend, "tool_results", [])]
    assert submitted, "the scripted backend did not record what was submitted"
    assert not any("_display" in r for r in submitted)
    assert not any("engine_wait_s" in r for r in submitted)
    await session.close()
