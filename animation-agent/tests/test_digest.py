"""One tool call, as the line a person reads.

The rule under test is that nothing is invented. Every phrase `digest` produces has to be assembled
out of keys the tools genuinely return, because a summary that guessed would be worse than no summary
— it would be believed. So the assertions here are mostly about what it does NOT say.

The tool names are the thirteen the model sees. `digest` dispatches on them by name, so a rename that
missed this file would leave the display falling through to its generic branch: still a line, and no
longer the right one.
"""
from agent import digest


def test_a_call_says_what_it_is_asking_for():
    assert digest.describe("motion_search", {"query": "sit and type"}) == '"sit and type"'
    assert digest.describe("motion_transition", {"from_action": "mx_Walking_Forward",
                                                 "to_action": "mx_Standing_Idle"}) \
        == "mx_Walking_Forward → mx_Standing_Idle"
    assert digest.describe("unity_locomotion", {"destination": "anchor:Computer",
                                                "face": "obj:Laptop"}) \
        == "anchor:Computer · face obj:Laptop"
    assert digest.describe("unity_execute", {"base": "mx_Walking", "then": [{"base": "mx_Typing"}],
                                             "sit_on": "obj:Chair"}) \
        == "mx_Walking → mx_Typing · sit on obj:Chair"


def test_a_route_through_a_transition_clip_is_in_the_line():
    """A `via` is a step the agent chose and a step that plays, so a line that omitted it would
    describe a two-step plan that was three."""
    # Short ids on purpose: `_clip` cuts at WIDTH, and this is a test about which parts reach the
    # line rather than about where it truncates.
    assert digest.describe("motion_transition",
                           {"from_action": "mx_Walking", "to_action": "mx_Typing",
                            "via": ["mx_Sit_To_Stand"]}) \
        == "mx_Walking → mx_Typing via mx_Sit_To_Stand"
    assert digest.describe("unity_execute",
                           {"base": "mx_Walking",
                            "then": [{"via": ["mx_Sit_To_Stand"], "base": "mx_Typing"}]}) \
        == "mx_Walking → mx_Sit_To_Stand → mx_Typing"


def test_one_tool_two_questions_reads_as_two_lines():
    """`unity_query` merged the search and the relation, so the display has to dispatch on which one
    arrived. Without that, finding a chair and asking whether she can reach it look identical."""
    assert digest.describe("unity_query", {"query": "", "limit": 10}) == "the whole room"
    assert digest.describe("unity_query", {"query": "chair"}) == "chair"
    assert digest.describe("unity_query", {"object_ids": ["obj:Chair", "obj:Laptop"]}) \
        == "obj:Chair, obj:Laptop"
    assert digest.summarise("unity_query", {"results": [{"label": "Chair"},
                                                        {"id": "anchor:Bedside"}],
                                            "count": 2}) == "Chair, anchor:Bedside"
    assert digest.summarise("unity_query", {"results": [], "count": 0}) == "nothing matched"
    assert digest.summarise("unity_query", {"objects": [{"exists": True, "needs_walking": True},
                                                        {"exists": True}]}) == "1 of 2 within reach"


def test_a_result_says_what_came_back():
    assert digest.summarise("motion_search", {"results": [{"action_id": "mx_Typing"},
                                                          {"action_id": "mx_Standing_Idle"}]}) \
        == "mx_Typing, mx_Standing_Idle"
    assert digest.summarise("unity_locomotion", {"arrived": True, "path_length_m": 2.44}) \
        == "arrived, walked 2.4 m"
    assert digest.summarise("unity_execute", {"committed": True, "sequence": [1, 2],
                                              "generated_transitions": [1]}) \
        == "committed · 2 steps · 1 generated"


def test_the_analysis_tools_report_what_they_measured():
    assert digest.summarise("motion_timing",
                            {"dominant_posture": "seated",
                             "channels": {"left_arm": {"moving": True},
                                          "root": {"moving": False}}}) == "seated, 1 channel moving"
    assert digest.summarise("motion_compose", {"partition": [1, 2], "conflicts": []}) == "2 layers"
    assert digest.summarise("motion_compose",
                            {"partition": [1, 2],
                             "conflicts": [{"channel": "right_hand"}]}) == "1 conflict"
    assert digest.summarise("motion_transition", {"class": "blend"}) == "blend"
    assert digest.summarise("motion_transition",
                            {"required_transition": {"from_posture": "standing",
                                                     "to_posture": "seated"}}) \
        == "standing -> seated needed"
    assert digest.summarise("motion_transition",
                            {"routes": [{"via": "mx_Standing_To_Sitting_Transition"}]}) \
        == "best via mx_Standing_To_Sitting_Transition"


def test_a_checked_plan_and_a_played_one_do_not_read_alike():
    """`unity_validate` and `unity_execute` take identical arguments, so the line has to come off the
    RESULT. A validation that read as a commit is how "I checked it" becomes "she did it"."""
    assert digest.summarise("unity_validate", {"committed": False, "sequence": [1, 2]}) \
        == "checked, not played · 2 steps"
    assert digest.summarise("unity_execute", {"committed": True}) == "committed"


def test_a_substituted_opener_is_in_the_line():
    """It is a change to what was committed, so it belongs where a person watching can see it — not
    only in the tool result the model reads."""
    assert "opened on mx_Standing_Idle" in digest.summarise(
        "unity_execute", {"committed": True,
                          "opened_on": {"asked_for": "mx_Walking_Forward",
                                        "played": "mx_Standing_Idle"}})


def test_a_failure_is_the_error_and_nothing_else():
    out = digest.summarise("unity_execute",
                           {"success": False,
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
    for name in ("unity_query", "motion_search", "motion_channels", "motion_timing",
                 "motion_compose", "motion_transition", "unity_locomotion", "unity_execute",
                 "unity_validate", "unity_measure"):
        out = digest.summarise(name, {})
        assert isinstance(out, str)
    assert digest.summarise("unity_locomotion", {}) == "not there yet"
    assert digest.describe("motion_search", "not json at all") == "not json at all"


def test_a_long_phrase_is_clipped_rather_than_wrapped():
    long_query = "a very long description of a motion " * 4
    out = digest.describe("motion_search", {"query": long_query})
    assert len(out) <= digest.WIDTH
    assert out.endswith("…")
