"""Declaration and dispatch. Mostly about what an invented argument costs.

A turn is iterations times a round trip and the tools are sub-millisecond, so the only lever on how
long a decision takes is how many times the model has to be asked again. An argument it made up used
to be one of those times.
"""
import pytest

from agent.tools.registry import ToolFailure, ToolFatal, ToolRegistry

SCHEMA = {"type": "object", "properties": {"where": {"type": "string"}},
          "required": ["where"], "additionalProperties": False}


def registry_with(handler, parameters=None):
    registry = ToolRegistry()
    registry.add("go", "go somewhere", parameters or SCHEMA, handler)
    return registry


@pytest.mark.asyncio
async def test_an_invented_argument_does_not_cost_a_round_trip():
    seen = {}

    def go(where):
        seen["where"] = where
        return {"arrived": where}

    out = await registry_with(go).dispatch("go", {"where": "chair", "then_wait": True})
    assert out["success"] is True
    assert out["arrived"] == "chair"
    assert seen["where"] == "chair"
    assert out["ignored_arguments"] == ["then_wait"]
    assert "then_wait" in out["note"]


@pytest.mark.asyncio
async def test_dropping_an_argument_is_never_silent():
    """The whole trade is that the model finds out in the result instead of in a failed call. If the
    note ever stops being emitted, this becomes a tool quietly doing something other than it was
    asked, which is worse than the failure it replaced."""
    out = await registry_with(lambda where: {"ok": True}).dispatch(
        "go", {"where": "chair", "speed": 3})
    assert "note" in out and "ignored_arguments" in out


@pytest.mark.asyncio
async def test_a_near_miss_is_named_rather_than_guessed():
    out = await registry_with(lambda where: {"ok": True}).dispatch("go", {"wher": "chair"})
    # Still a failure -- `where` is required and was not supplied under a name the tool knows. What
    # changes is that the model is told which real parameter the typo was close to.
    assert out["success"] is False
    assert "wher" in out["note"] and "where" in out["note"]


@pytest.mark.asyncio
async def test_a_missing_required_argument_still_fails():
    out = await registry_with(lambda where: {"ok": True}).dispatch("go", {})
    assert out["success"] is False
    assert "bad arguments" in out["error"]


@pytest.mark.asyncio
async def test_a_tool_that_breaks_inside_is_not_blamed_on_the_arguments():
    """A TypeError raised INSIDE a handler is the tool's defect, and it used to be reported with the
    same words as a wrong parameter. Measured on a live turn: a str/int comparison in the walk poll
    loop came back as "bad arguments for plan_motion", so the model rewrote arguments that were
    already right and sent byte-identical ones twice. The turn still survives; only the blame moves.
    """
    def broken(where):
        return {"ok": "yes" >= 0}

    out = await registry_with(broken).dispatch("go", {"where": "there"})
    assert out["success"] is False
    assert "bad arguments" not in out["error"]
    assert "failed internally" in out["error"] and "TypeError" in out["error"]
    assert "not in the arguments" in out["hint"]


@pytest.mark.asyncio
async def test_a_handler_that_takes_anything_keeps_everything():
    def go(**kwargs):
        return {"got": sorted(kwargs)}

    out = await registry_with(go).dispatch("go", {"where": "chair", "extra": 1})
    assert out["got"] == ["extra", "where"]
    assert "ignored_arguments" not in out


@pytest.mark.asyncio
async def test_a_tool_that_could_not_do_the_thing_is_still_a_result():
    def go(where):
        raise ToolFailure("no such place", hint="call scene_find first")

    out = await registry_with(go).dispatch("go", {"where": "moon"})
    assert out["success"] is False
    assert out["error"] == "no such place"
    assert out["hint"] == "call scene_find first"


@pytest.mark.asyncio
async def test_a_tool_that_cannot_run_at_all_ends_the_turn():
    with pytest.raises(ToolFatal):
        await registry_with(lambda where: "not a dict").dispatch("go", {"where": "chair"})


def test_a_declaration_that_would_let_the_model_invent_silently_is_refused():
    """Leniency at dispatch is not a reason to relax the declaration: strict is what stops most of
    these arriving, and lenient is what makes the rest cheap."""
    with pytest.raises(ValueError):
        ToolRegistry().add("go", "go", {"type": "object", "properties": {}}, lambda: {})
