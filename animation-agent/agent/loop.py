"""
loop.py — the ReAct loop, shaped after Codex's submission/event queues.

THE ONE STRUCTURAL IDEA. There are two independently scheduled tasks: a submission loop that is NEVER
blocked by a running turn, and one task per turn. They share state through the Session object, not
through a channel. That is what makes "type new text while it is thinking" work — the submission loop is
always free to accept input, whatever the turn is doing.

New text while a turn is running does NOT abort it and does NOT splice into the in-flight response. It
is appended to `pending_input`, which the turn drains at the TOP of its next iteration. Mid-stream
injection would build a plan from half of one intent and half of another; worse, on the Realtime API it
desynchronizes the conversation item list. Explicit interrupt is a separate op, and only it cancels.

WHY THE LOOP CONTINUES AFTER A FAILED TOOL. A tool that could not do the thing returns
`{"success": false, ...}` as an ordinary result, so the model sees the failure and can correct inside the
same turn. That is not error handling bolted on — it is the mechanism the geometric gate will use to
send a rejection back as something the agent can act on. Only `ToolFatal` ends a turn.

BUDGETS ARE NOT OPTIONAL. `max_iterations` and `max_tool_calls` bound a turn. A latency-tuned mini model
that loops between two tools is a real failure mode, and an unbounded loop against a paid API is the kind
of bug you discover on the invoice.
"""
import asyncio
import json
import logging
import time

from . import digest
from .llm.base import LlmError, TextDelta, ToolCall, TurnDone
from .tools import ToolFatal

log = logging.getLogger("agent.loop")

# Raised from 8/24. Those were set when a turn meant "find one action and play it" against two KB
# tools. A turn now can mean find the actions, check how they join, find somewhere to sit, measure
# where it is, walk there, turn around, plan the sequence, and check the result -- against thirteen
# tools. The first full run of that hit both ceilings mid-task. These are still ceilings, not budgets
# to spend: a turn that needs 40 calls is a turn that went wrong, and the report says which.
MAX_ITERATIONS = 14
MAX_TOOL_CALLS = 40

# WHEN A REPEATED FAILURE STOPS BEING A RETRY. A tool that failed is meant to be tried again inside the
# turn -- that is why a failure comes back as a result rather than an exception -- but the same call
# with the same arguments failing with the same message is not a retry, it is the loop. Measured: 14
# consecutive `motion_search` calls, every one refused for the same reason, ending on the iteration
# budget with nothing said to the user about why.
#
# Two counts, because there are two shapes. Identical calls are the tight loop; a tool that keeps
# failing the same way while the arguments wander is the wider one, and it burns a turn just as
# thoroughly.
REPEAT_STEER_AFTER = 2
REPEAT_STOP_AFTER = 3
SAME_ERROR_STOP_AFTER = 5


class Op:
    USER_TEXT = "user_text"
    INTERRUPT = "interrupt"
    SHUTDOWN = "shutdown"

    __slots__ = ("kind", "text")

    def __init__(self, kind, text=None):
        self.kind = kind
        self.text = text


class Ev:
    """What the UI sees. Deliberately a flat set — the CLI renders these and nothing else."""

    TURN_STARTED = "turn.started"
    STEERED = "turn.steered"          # new input folded into a running turn
    TEXT = "assistant.text"
    TOOL_STARTED = "tool.started"
    TOOL_PROGRESS = "tool.progress"   # a running tool saying what it is doing, before it is finished
    TOOL_FINISHED = "tool.finished"
    TURN_COMPLETE = "turn.complete"
    VERDICT = "verify.done"           # a check that could only be answered after the reply
    ERROR = "error"


class RepeatGuard:
    """Counts failures that are the same failure, and says what to do about it.

    DETERMINISTIC, AND OUTSIDE THE MODEL. The model is the thing that is looping; asking it to notice
    is asking the broken part to diagnose itself. So the loop counts, and what it counts is the pair
    that makes a repeat a repeat: the call (name plus arguments, canonically spelled) and the message
    that came back. Different arguments or a different message start a new count, because either one
    is the model actually trying something.

    ONE TURN, ONE GUARD. Counts do not survive a turn: a call that failed twice an hour ago tells you
    nothing about the request being made now.
    """

    def __init__(self, steer_after=REPEAT_STEER_AFTER, stop_after=REPEAT_STOP_AFTER,
                 same_error_after=SAME_ERROR_STOP_AFTER):
        self.steer_after = steer_after
        self.stop_after = stop_after
        self.same_error_after = same_error_after
        self.identical = {}
        self.by_error = {}
        self.steered = set()

    @staticmethod
    def key(name, arguments, error):
        """(tool, canonical arguments, error text). `sort_keys` because two calls that differ only in
        the order the model happened to serialise them in are the same call."""
        try:
            spelled = json.dumps(arguments, sort_keys=True, default=str)
        except (TypeError, ValueError):
            spelled = repr(arguments)
        return (name, spelled, error or "")

    def record(self, name, arguments, error):
        key = self.key(name, arguments, error)
        self.identical[key] = self.identical.get(key, 0) + 1
        loose = (name, error or "")
        self.by_error[loose] = self.by_error.get(loose, 0) + 1
        return key

    def stop(self):
        """The turn is over, and this is what to say. None while it is worth carrying on."""
        for (name, _, error), count in self.identical.items():
            if count >= self.stop_after:
                return "(stopped: %s failed the same way %d times: %s)" % (name, count, error)
        for (name, error), count in self.by_error.items():
            if count >= self.same_error_after:
                return "(stopped: %s failed the same way %d times: %s)" % (name, count, error)
        return None

    def steer(self):
        """The nudge to send before the next request, once. None when there is nothing to say."""
        for key, count in self.identical.items():
            if count == self.steer_after and key not in self.steered:
                self.steered.add(key)
                return ("The last two calls were identical and failed the same way: %s. Change the "
                        "arguments or take another route; do not repeat the call." % key[2])
        return None


class Session:
    def __init__(self, backend, registry, instructions,
                 max_iterations=MAX_ITERATIONS, max_tool_calls=MAX_TOOL_CALLS):
        self.backend = backend
        self.registry = registry
        self.instructions = instructions
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls

        self.submissions = asyncio.Queue()
        self.pending_input = []
        self.turn = None
        self._handlers = []
        self._submission_task = None
        self.last_turn = None          # TurnReport of the most recent turn

    # ---- lifecycle -------------------------------------------------------------------------

    async def start(self):
        await self.backend.connect(self.instructions, self.registry.declarations())
        declared = getattr(self.backend, "declared_tool_names", None)
        if declared is not None and set(declared) != set(self.registry.names()):
            # Presents otherwise as a model that simply never calls a tool.
            log.warning("server accepted tools %s but we declared %s", declared, self.registry.names())
        self._submission_task = asyncio.ensure_future(self.submission_loop())
        return self

    async def close(self):
        if self._submission_task is not None:
            self._submission_task.cancel()
            self._submission_task = None
        if self.turn is not None:
            self.turn.cancel()
            self.turn = None
        await self.backend.close()

    def on_event(self, handler):
        self._handlers.append(handler)
        return handler

    def _emit(self, kind, **data):
        for handler in self._handlers:
            handler(kind, data)

    def notice(self, message):
        """Say something to whatever is displaying this session, with no turn behind it.

        For the input guards: a line that is refused before it can become a turn still has to reach
        the person who typed it, and there is no TurnReport to carry it.
        """
        self._emit(Ev.ERROR, message=message)

    # ---- submissions -----------------------------------------------------------------------

    async def submit(self, op):
        await self.submissions.put(op)

    async def submit_text(self, text):
        await self.submit(Op(Op.USER_TEXT, text))

    async def submission_loop(self):
        """Never blocked by a running turn — that is the whole point of the split."""
        while True:
            op = await self.submissions.get()
            if op.kind == Op.SHUTDOWN:
                return
            if op.kind == Op.INTERRUPT:
                await self._interrupt()
            elif op.kind == Op.USER_TEXT:
                self._route_text(op.text)

    def _route_text(self, text):
        if self.turn is None or self.turn.done():
            self.turn = asyncio.ensure_future(self.run_turn(text))
        else:
            self.pending_input.append(text)
            self._emit(Ev.STEERED, text=text)

    async def _interrupt(self):
        if self.turn is not None and not self.turn.done():
            try:
                await self.backend.cancel()
            except LlmError:
                pass
            self.turn.cancel()

    async def wait_idle(self):
        """Block until no turn is running. For scripted runs and the eval, not for the CLI."""
        while self.turn is not None and not self.turn.done():
            await asyncio.wait([self.turn])

    # ---- one turn --------------------------------------------------------------------------

    async def run_turn(self, text):
        started = time.monotonic()
        report = TurnReport(text)
        guard = RepeatGuard()
        self._emit(Ev.TURN_STARTED, text=text)

        try:
            await self.backend.send_user_text(text)
            for iteration in range(self.max_iterations):
                # Drain steering at the TOP of the iteration, never mid-response.
                while self.pending_input:
                    extra = self.pending_input.pop(0)
                    report.steered.append(extra)
                    await self.backend.send_user_text(extra)

                # SAID BEFORE THE NEXT REQUEST, so the model sees it where it sees everything else.
                # Not counted as steering: nobody typed it, and a turn report that listed it among
                # the user's own words would misdescribe the turn.
                nudge = guard.steer()
                if nudge:
                    await self.backend.send_user_text(nudge)

                await self.backend.request_response()
                done = await self._await_response(report)
                report.iterations = iteration + 1

                # The model's own account of what it is about to do. A turn that spends four
                # iterations makes four decisions and only the last of them reaches the reply, so the
                # ones in between are shown as they happen. Whole, not per fragment: a delta is a few
                # characters and no console can show one usefully.
                if done.tool_calls and done.text:
                    self._emit(Ev.TEXT, text=done.text)

                if not done.tool_calls:
                    if self.pending_input:
                        continue           # answered, but new input arrived — keep going
                    report.text = done.text
                    break

                if report.tool_calls + len(done.tool_calls) > self.max_tool_calls:
                    report.text = "(stopped: tool-call budget exhausted)"
                    report.exhausted = True
                    break

                for call in done.tool_calls:
                    await self._run_tool(call, report, guard)

                # A LOOP ENDS WITH A REPLY, NOT WITH A BUDGET. Left to the ceilings, a turn spent
                # repeating one refused call ended on "(stopped: iteration budget exhausted)", which
                # says the turn was long and not what went wrong. This says what went wrong.
                stopped = guard.stop()
                if stopped:
                    report.text = stopped
                    report.exhausted = True
                    self._emit(Ev.TEXT, text=stopped)
                    break
            else:
                report.text = "(stopped: iteration budget exhausted)"
                report.exhausted = True

        except asyncio.CancelledError:
            report.cancelled = True
            self._emit(Ev.TURN_COMPLETE, report=report)
            raise
        except (LlmError, ToolFatal) as e:
            report.error = str(e)
            self._emit(Ev.ERROR, message=str(e))

        report.seconds = time.monotonic() - started
        self.last_turn = report
        self._emit(Ev.TURN_COMPLETE, report=report)
        return report

    async def _await_response(self, report):
        async for event in self.backend.events():
            if isinstance(event, TextDelta):
                continue                    # the whole message arrives with TurnDone; see run_turn
            elif isinstance(event, ToolCall):
                continue                    # the authoritative list arrives with TurnDone
            elif isinstance(event, TurnDone):
                return event
        raise LlmError("model stream ended without completing a response")

    async def _run_tool(self, call, report, guard=None):
        try:
            arguments = json.loads(call.arguments or "{}")
        except ValueError:
            arguments = call.arguments

        # WHAT IT IS ASKING FOR, NOT JUST WHICH TOOL. A column of bare tool names says the agent is
        # alive and nothing else; four `unity_query` calls in a row look identical whether they are
        # narrowing on a chair or re-asking a question already answered. Composed here rather than in
        # a renderer so the stdin session and an attached terminal cannot describe a turn differently.
        called = digest.describe(call.name, arguments)
        started = time.monotonic()
        self._emit(Ev.TOOL_STARTED, name=call.name, arguments=call.arguments, call=called)

        # WHAT IT IS DOING WHILE IT DOES IT. A tool result arrives when the tool is finished, so the
        # three seconds `unity_execute` spends watching her cross the room were three seconds of nothing
        # on screen. The tool says so itself rather than a renderer guessing from arguments, which is
        # opencode's split between a tool's `output` (for the model) and its `metadata` (for the UI).
        def say(text):
            self._emit(Ev.TOOL_PROGRESS, name=call.name, call=called, detail=text)

        result = await self.registry.dispatch(call.name, call.arguments, say=say)
        seconds = time.monotonic() - started

        # Popped, not read through: `_display` exists for the person watching and every token of it
        # would otherwise be re-sent to the model on each remaining round trip of the turn.
        shown = result.pop("_display", None) or {}
        waited = float(shown.get("engine_wait_s") or 0.0)

        # Neither API can put a picture in a tool result, so a tool that produces one returns it under
        # `images` and it is lifted out here: the result stays a small JSON object, and the picture
        # follows as a user message. Leaving it in place would send a megabyte of base64 as the text of
        # a function_call_output, which is both useless to the model and expensive.
        images = result.pop("images", None)

        report.tool_calls += 1
        report.trace.append({"tool": call.name, "arguments": arguments,
                             # When this landed, measured from the start of the turn. The interaction
                             # target is on the DECISION -- how long until something moves -- and the
                             # total turn time cannot answer that, because it also contains whatever
                             # the model said afterwards.
                             "at_s": round(time.monotonic() - report.started_at, 2),
                             "success": result.get("success"),
                             "error": result.get("error"),
                             # Whether frames were actually generated is the one thing a reply can
                             # claim without it being true, so the trace records it rather than
                             # leaving the answer as the only account of what happened.
                             "generated": len(result.get("generated_transitions") or []) or None,
                             # From the RESULT, not from the arguments. `unity_execute` and
                             # `unity_validate` take identical arguments and differ only in what they
                             # do, so nothing about a call says whether anything moved -- only the
                             # answer does.
                             "committed": result.get("committed") or None,
                             # HOW FAR SHE ACTUALLY TRAVELLED, for the same reason `generated` is
                             # here. A plan that walks her across the room and one committed where she
                             # already stood are the same call with the same arguments -- naming a
                             # seat is naming a destination, so the walk need never appear in what the
                             # model wrote. Without this the trace cannot tell the two apart, and a
                             # demo about walking would rest on the reply saying it walked.
                             "walked_m": (result.get("walked") or {}).get("path_length_m"),
                             # Seconds of this call that were the character moving rather than the
                             # agent deciding. Measured inside the tool at the point it waits, so it
                             # is what was actually spent, not an estimate from the total.
                             "engine_wait_s": round(waited, 2) or None,
                             "images": len(images or [])})
        self._emit(Ev.TOOL_FINISHED, name=call.name, success=result.get("success"),
                   error=result.get("error"), images=len(images or []),
                   call=called, result=digest.summarise(call.name, result),
                   seconds=round(seconds, 2), engine_wait_s=round(waited, 2))
        await self.backend.submit_tool_result(call.call_id, result)
        if images:
            try:
                await self.backend.send_user_images(images)
            except NotImplementedError:
                pass                     # a backend that cannot see loses the picture, not the turn

        if guard is not None and not result.get("success"):
            guard.record(call.name, arguments, result.get("error"))

        verify = result.get("verify")
        if verify and verify.get("status") == "scheduled":
            self._verify_later(verify)

    def _verify_later(self, verify):
        """Run a check whose answer does not exist yet, without holding the reply for it.

        A generated sit is only measurable once the descent has finished, which is seconds after the
        plan is committed. Holding the turn until then would put the physical length of the animation
        inside the interaction latency, and the target is on the decision, not on the motion. So the
        turn ends, and the verdict arrives afterwards on its own.

        A FAILED verdict is submitted as ordinary input rather than pushed into the finished turn:
        `_route_text` already queues behind a running turn and starts a new one when idle, so a
        correction can never interleave with a turn in flight or with what the user types next.

        Nothing here decides WHAT to check. The tool result names the tool and its arguments, so the
        loop stays free of any knowledge about motion.
        """
        async def run():
            try:
                result = await self.registry.dispatch(verify["tool"],
                                                      json.dumps(verify.get("arguments") or {}))
            except asyncio.CancelledError:
                raise
            except Exception as e:                        # noqa: BLE001 — see below
                # Broad on purpose. This runs detached, so an exception here has nobody to propagate
                # to: it would end up as "task exception was never retrieved" in a log nobody reads,
                # and the verification would silently not have happened. A check that could not run is
                # reported as a check that did not pass.
                log.warning("deferred %s did not complete: %s", verify.get("tool"), e)
                result = {"success": False, "error": "the check could not run: %s" % e}
            ok = bool(result.get("success"))
            self._emit(Ev.VERDICT, name=verify["tool"], success=ok,
                       detail=result.get("error") or verify.get("confirms", ""),
                       result=result)
            if not ok and verify.get("on_failure"):
                await self.submit_text(verify["on_failure"] + " " + (result.get("error") or ""))

        asyncio.ensure_future(run())


class TurnReport:
    """Everything the eval and the CLI need to know about one turn."""

    def __init__(self, prompt):
        self.prompt = prompt
        self.text = ""
        self.started_at = time.monotonic()
        self.trace = []
        self.steered = []
        self.tool_calls = 0
        self.iterations = 0
        self.seconds = 0.0
        self.exhausted = False
        self.cancelled = False
        self.error = None

    @property
    def ok(self):
        return self.error is None and not self.cancelled

    def tools_used(self):
        return [step["tool"] for step in self.trace]

    def generated(self):
        """How many transitions this turn really generated. A reply can claim one without it being
        true — measured: the prompt used to instruct the model to say so unconditionally — so anything
        that reports a turn should report this next to the text rather than trusting it."""
        return sum(step.get("generated") or 0 for step in self.trace)

    def engine_wait_s(self):
        """Seconds this turn spent watching the character move, summed off the calls that waited."""
        return sum(step.get("engine_wait_s") or 0.0 for step in self.trace)

    def decision_seconds(self):
        """The turn's time MINUS the part that was the character moving.

        This is the number to quote for how long the agent took. A turn that walks her across the room
        contains three seconds during which nothing could have gone faster: the walk is the walk, and
        the agent is blocked on the engine, not thinking. Reporting only the total makes a fast
        decision behind a long animation read as a slow agent, which is the same confusion `motion_at`
        exists to avoid at the other end.

        Bucketed rather than estimated, after codex's RuntimeMetricsSummary: each bucket is measured
        where it is spent -- inside the tool, at the poll -- so this subtracts a real number.
        """
        return max(0.0, self.seconds - self.engine_wait_s())

    def motion_at(self):
        """Seconds from the request to the moment something actually started moving, or None if
        nothing did.

        THIS IS THE NUMBER THE LATENCY TARGET IS ABOUT, and the turn's total is not. A turn also
        contains the reply, and — for a generated motion — nothing else: the landing is measured
        afterwards, on its own. Reporting only the total makes a fast decision followed by a long
        animation look like a slow agent.

        Read off what the tool did rather than what it was asked to do. `unity_execute` and
        `unity_validate` take identical arguments, so nothing in a call says whether anything moved.
        """
        for step in self.trace:
            if step.get("tool") == "unity_execute" and step.get("success") and step.get("committed"):
                return step.get("at_s")
        return None

    def as_dict(self):
        return {"prompt": self.prompt, "text": self.text, "trace": self.trace,
                "steered": self.steered, "tool_calls": self.tool_calls,
                "iterations": self.iterations, "seconds": round(self.seconds, 2),
                "deciding_s": round(self.decision_seconds(), 2),
                "engine_wait_s": round(self.engine_wait_s(), 2),
                "generated": self.generated(), "motion_at_s": self.motion_at(),
                "exhausted": self.exhausted, "cancelled": self.cancelled, "error": self.error}

    def __repr__(self):
        return "TurnReport(%d tools, %d iters, %.1fs)" % (
            self.tool_calls, self.iterations, self.seconds)
