"""
registry.py — tool declaration and dispatch.

The wire shape is the OpenAI Realtime one, which is FLAT:

    {"type": "function", "name": ..., "description": ..., "parameters": {...json schema...}}

not the Chat Completions shape, where the same fields are nested under a `"function"` key. Mixing the
two produces a session that accepts the update and then never calls a tool, with no error — so the
declaration is built in exactly one place, here.
"""
import asyncio
import difflib
import inspect
import json


class ToolFailure(Exception):
    """The tool ran and could not do the thing. Becomes a model-visible failed result, not a crash."""

    def __init__(self, message, hint=None):
        super().__init__(message)
        self.message = message
        self.hint = hint


class ToolFatal(Exception):
    """The tool cannot run at all. Ends the turn."""


def _accepted_names(handler):
    """Keyword names the handler will take, or None if it takes **kwargs and will take anything."""
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        return None
    names = set()
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            return None
        if parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                              inspect.Parameter.KEYWORD_ONLY):
            names.add(parameter.name)
    return names


def _dropped_note(result, spec, ignored):
    """Say what was dropped, on every path out. Attached to failures as well as successes, because a
    misspelled required parameter is both — the tool never saw it, so it also never ran."""
    if not ignored:
        return result
    result["ignored_arguments"] = ignored
    note = "%s does not take %s; it ran without %s" % (
        spec.name, ", ".join(ignored), "them" if len(ignored) > 1 else "it")
    for key in ignored:
        near = difflib.get_close_matches(key, sorted(spec.accepted), n=1, cutoff=0.7)
        if near:
            note += ". '%s' looks like '%s' — if that is what you meant, call it again with that name" \
                    % (key, near[0])
    result["note"] = note
    return result


class ToolSpec:
    __slots__ = ("name", "description", "parameters", "handler", "accepted")

    def __init__(self, name, description, parameters, handler):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
        self.accepted = _accepted_names(handler)

    def declaration(self):
        return {"type": "function", "name": self.name,
                "description": self.description, "parameters": self.parameters}


class Progress:
    """What a running tool can say about itself, and what it spent its time on.

    TWO THINGS A RESULT CANNOT CARRY. A tool result arrives when the tool is finished, so a call that
    takes three seconds is three seconds of nothing on screen — and the walk inside `unity_execute` is
    exactly that. And a result says what happened, not how long the parts of it took, so a turn that
    spent four seconds watching a character cross a room is indistinguishable from one that spent four
    seconds thinking. Shaped after opencode's `Tool.Context.metadata()`, which tools call DURING
    execution, and codex's `RuntimeMetricsSummary`, which buckets a turn's time by what it went on
    rather than reporting one wall clock.

    `waited` is the bucket that matters here: seconds spent watching the engine move the character.
    It is time the agent could not have gone faster through, it is not deciding, and rolling it into
    the turn total makes a fast decision behind a long animation read as a slow agent.
    """

    __slots__ = ("_say", "engine_wait_s", "last")

    def __init__(self, say=None):
        self._say = say
        self.engine_wait_s = 0.0
        self.last = None

    def __call__(self, text):
        """Say what is happening now. Best-effort by construction: a display that cannot be reached
        must never take down the work it is describing."""
        self.last = text
        if self._say is None:
            return
        try:
            self._say(text)
        except Exception:                    # noqa: BLE001 — see above
            self._say = None

    def waited(self, seconds):
        """Seconds spent waiting on the engine, not on a decision."""
        self.engine_wait_s += max(0.0, seconds)


class ToolRegistry:
    def __init__(self):
        self._tools = {}
        # The call in flight. Tools reach it through `registry.progress`, which is available to them
        # because `register()` closes over the registry — no extra parameter on every handler, and no
        # signature for a model to see.
        self.progress = Progress()

    def add(self, name, description, parameters, handler):
        if name in self._tools:
            raise ValueError("duplicate tool: %s" % name)
        if parameters.get("additionalProperties") is not False:
            # Without this a mini model will happily invent parameters and the failure is silent.
            raise ValueError("tool %s: parameters must set additionalProperties=false" % name)
        self._tools[name] = ToolSpec(name, description, parameters, handler)
        return self

    def tool(self, name, description, parameters):
        def decorate(fn):
            self.add(name, description, parameters, fn)
            return fn
        return decorate

    def declarations(self):
        return [t.declaration() for t in self._tools.values()]

    def names(self):
        return list(self._tools)

    async def dispatch(self, name, arguments, say=None):
        """Run a tool and return the result dict the model will see.

        `say` is called with a line of text while the tool is still running — see Progress. What the
        tool reports through it, and the seconds it spent waiting on the engine, come back under
        `_display`, which is stripped before the result reaches the model.

        Never raises ToolFailure — it is caught and shaped into `{"success": false, ...}` so the turn
        survives. ToolFatal propagates.

        DECLARED STRICTLY, RUN LENIENTLY. The declaration keeps `additionalProperties: false`, because
        that is what stops a model inventing parameters in the first place. But when one arrives anyway
        the tool runs without it and says which key it dropped, instead of failing. The reason is the
        clock: a turn is iterations times a round trip, tools are sub-millisecond, so the only way to
        make a decision faster is to make fewer round trips — and an invented key used to cost a whole
        one for a call that would have worked. Measured on the walk-and-sit run, `then_wait` and an
        `object_id` on the plan tool each burned an iteration on their own.

        Nothing is hidden by this. The dropped keys are named in the result, a near miss gets its
        correction suggested, and what the tool actually did is in the same result for the model to read.
        """
        spec = self._tools.get(name)
        if spec is None:
            return {"success": False, "error": "no such tool: %s" % name,
                    "hint": "available tools: %s" % ", ".join(self._tools)}

        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except ValueError as e:
                return {"success": False, "error": "arguments were not valid JSON: %s" % e}
        if not isinstance(arguments, dict):
            return {"success": False, "error": "arguments must be a JSON object"}

        ignored = []
        if spec.accepted is not None:
            ignored = sorted(key for key in arguments if key not in spec.accepted)
            if ignored:
                arguments = {k: v for k, v in arguments.items() if k in spec.accepted}

        # BAD ARGUMENTS ARE DECIDED BEFORE THE CALL, NOT INFERRED FROM WHAT IT RAISED. Binding the
        # signature separates the model's mistake from ours: a wrong or missing parameter fails here,
        # and a TypeError from INSIDE the tool is the tool's own, not something the model can fix.
        #
        # The two used to share one `except TypeError`. Measured, that turned a str/int comparison deep
        # in the walk poll loop into "bad arguments for unity_execute", so the model rewrote arguments
        # that were correct and got the same error again -- twice, with byte-identical parameters. An
        # error message that blames the wrong side does not just fail to help, it actively misdirects.
        try:
            bound = inspect.signature(spec.handler).bind(**arguments)
        except TypeError as e:
            # The case the near-miss hint exists for: a misspelled required parameter arrives here as
            # both a dropped key and a missing one.
            return self._display(_dropped_note(
                {"success": False, "error": "bad arguments for %s: %s" % (name, e)}, spec, ignored))

        self.progress = Progress(say)
        try:
            result = spec.handler(*bound.args, **bound.kwargs)
            if inspect.isawaitable(result):
                result = await result
        except ToolFailure as e:
            out = {"success": False, "error": e.message}
            if e.hint:
                out["hint"] = e.hint
            return self._display(_dropped_note(out, spec, ignored))
        except (ToolFatal, asyncio.CancelledError):
            raise
        except Exception as e:                          # noqa: BLE001
            # The tool broke. Reported as a failure so the turn survives and the trace keeps the
            # reason, but named as OUR fault -- there is nothing for the model to correct, and telling
            # it otherwise is what sent it round the loop rewriting good arguments.
            return self._display(_dropped_note(
                {"success": False,
                 "error": "%s failed internally: %s: %s" % (name, type(e).__name__, e),
                 "hint": "this is a defect in the tool, not in the arguments. Try a different "
                         "approach; repeating the same call will not help."},
                spec, ignored))

        if not isinstance(result, dict):
            raise ToolFatal("tool %s returned %s, not a dict" % (name, type(result).__name__))
        result.setdefault("success", True)
        return self._display(_dropped_note(result, spec, ignored))

    def _display(self, result):
        """Attach what this call spent and what it said, under a key nothing sends to the model.

        `_display` leads with an underscore for the same reason a private attribute does, and `loop.py`
        pops it before the result is submitted. Keeping it out of the model's context is not tidiness:
        a turn is round trips, and every token of display copy in a tool result is paid for on each one.
        """
        if self.progress.engine_wait_s > 0 or self.progress.last:
            result["_display"] = {"engine_wait_s": round(self.progress.engine_wait_s, 2),
                                  "said": self.progress.last}
        return result
