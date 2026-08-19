"""One tool call, as the line a person reads.

The rule under test is that nothing is invented. Every phrase `digest` produces has to be assembled
out of keys the tools genuinely return, because a summary that guessed would be worse than no summary
— it would be believed. So the assertions here are mostly about what it does NOT say.
"""
from agent import digest


def test_a_call_says_what_it_is_asking_for():
    assert digest.describe("kb_search", {"query": "sit and type"}) == '"sit and type"'
    assert digest.describe("kb_transition", {"from_action": "walking", "to_action": "typing"}) \
        == "walking → typing"
    assert digest.describe("move_to", {"destination": "anchor:Computer", "face": "obj:Laptop"}) \
        == "anchor:Computer · face obj:Laptop"
    assert digest.describe("plan_motion", {"base": "walking", "then": [{"base": "typing"}],
                                           "sit_on": "obj:Chair"}) \
        == "walking → typing · sit on obj:Chair"


def test_an_unfiltered_room_search_says_so():
    """`scene_search` with no query is the cheapest correct answer to "is there a chair", and it is
    the call that used to be indistinguishable from a narrowed one in the display."""
    assert digest.describe("scene_search", {"limit": 10}) == "the whole room"
    assert digest.describe("scene_search", {"query": "chair"}) == "chair"
    assert digest.describe("scene_query", {"object_ids": ["obj:Chair", "obj:Laptop"]}) \
        == "obj:Chair, obj:Laptop"


def test_a_result_says_what_came_back():
    assert digest.summarise("scene_search", {"results": [{"label": "Chair"}, {"id": "anchor:Bedside"}],
                                             "count": 2}) == "Chair, anchor:Bedside"
    assert digest.summarise("scene_search", {"results": [], "count": 0}) == "nothing matched"
    assert digest.summarise("scene_query", {"objects": [{"exists": True, "needs_walking": True},
                                                        {"exists": True}]}) == "1 of 2 within reach"
    assert digest.summarise("kb_search", {"results": [{"action_id": "typing"},
                                                      {"action_id": "idle"}]}) == "typing, idle"
    assert digest.summarise("move_to", {"arrived": True, "path_length_m": 2.44}) \
        == "arrived, walked 2.4 m"
    assert digest.summarise("plan_motion", {"mode": "commit", "sequence": [1, 2],
                                            "generated_transitions": [1]}) \
        == "committed · 2 steps · 1 generated"


def test_a_substituted_opener_is_in_the_line():
    """It is a change to what was committed, so it belongs where a person watching can see it — not
    only in the tool result the model reads."""
    assert "opened on idle" in digest.summarise(
        "plan_motion", {"mode": "commit", "opened_on": {"asked_for": "walking", "played": "idle"}})


def test_a_failure_is_the_error_and_nothing_else():
    out = digest.summarise("plan_motion", {"success": False,
                                           "error": "these actions fight over the same body parts"})
    assert out == "these actions fight over the same body parts"


def test_a_tool_nobody_taught_it_about_still_gets_a_line():
    """A tool added later reads sensibly here without anyone remembering to come back. What is missing
    then is detail, not the line."""
    assert digest.describe("some_new_tool", {"object_id": "obj:Chair"}) == "obj:Chair"
    assert digest.summarise("some_new_tool", {"success": True, "note": "did the thing"}) \
        == "did the thing"
    assert digest.summarise("some_new_tool", {"success": True}) == ""


def test_nothing_is_invented_from_an_empty_result():
    for name in ("scene_search", "scene_query", "kb_search", "move_to", "plan_motion",
                 "check_motion"):
        out = digest.summarise(name, {})
        assert isinstance(out, str)
    assert digest.summarise("move_to", {}) == "not there yet"
    assert digest.describe("kb_search", "not json at all") == "not json at all"


def test_a_long_phrase_is_clipped_rather_than_wrapped():
    long_query = "a very long description of a motion " * 4
    out = digest.describe("kb_search", {"query": long_query})
    assert len(out) <= digest.WIDTH
    assert out.endswith("…")
