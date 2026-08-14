"""The KB tools: the projection budget, the two renames, and the error discipline."""
import pytest

from agent.kbindex import KBIndex
from agent.tools import ToolRegistry
from agent.tools import kb as kb_tools

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def kb():
    return KBIndex.load()


@pytest.fixture
def registry(kb):
    return kb_tools.register(ToolRegistry(), kb)


async def test_declarations_use_the_flat_realtime_shape(registry):
    """Chat Completions nests these under a "function" key. Getting it wrong yields a session that
    accepts the update and then never calls a tool, with no error."""
    for decl in registry.declarations():
        assert decl["type"] == "function"
        assert isinstance(decl["name"], str) and decl["name"]
        assert "function" not in decl
        assert decl["parameters"]["additionalProperties"] is False


async def test_search_returns_compact_hits(registry):
    out = await registry.dispatch("kb_search", {"query": "performs chest compressions on the patient"})
    assert out["success"]
    assert out["results"][0]["action_id"] == "cpr"
    hit = out["results"][0]
    assert set(hit) == {"action_id", "display_name", "score", "matched", "kind", "posture",
                        "duration_s", "loop", "drives", "touches"}
    assert hit["drives"] == ["torso", "left_hand", "right_hand"]
    assert hit["touches"] == ["patient_chest"]


async def test_search_reports_diagnostics_instead_of_a_verdict(registry):
    """No tuned cutoff — the model gets the evidence and decides. See kb.py's docstring."""
    out = await registry.dispatch("kb_search", {"query": "presses a button on the cardiac monitor"})
    assert out["query_coverage"] < 0.5      # most of the request has no words in the corpus
    assert "top_margin" in out


async def test_filters_narrow_the_corpus(registry):
    seated = await registry.dispatch("kb_search", {"query": "anything", "posture": "seated"})
    assert [r["action_id"] for r in seated["results"]] == ["typing"]

    bases = await registry.dispatch("kb_search", {"query": "anything", "kind": "base"})
    assert sorted(r["action_id"] for r in bases["results"]) == ["idle", "typing", "walking"]

    legs = await registry.dispatch("kb_search", {
        "query": "anything", "drives_channel": {"channel": "left_leg", "role": "primary"}})
    assert [r["action_id"] for r in legs["results"]] == ["walking"]


async def test_filter_with_no_survivors_says_so_rather_than_returning_junk(registry):
    out = await registry.dispatch("kb_search", {"query": "walking", "posture": "seated",
                                                "kind": "overlay"})
    assert out["results"] == []
    assert "relax" in out["note"]


async def test_contact_is_normalized_to_match_ik_goals(registry):
    """channels.*.contact is 'object:pills' but ik_goals[].contact_object is 'pills'. Same referent,
    two spellings — reconciled here so the model never has to know."""
    out = await registry.dispatch("kb_get_action", {"action_id": "giving_pills",
                                                    "include": ["channels", "ik_goals"]})
    assert out["channels"]["right_hand"]["contact"] == {"kind": "object", "name": "pills"}
    assert out["channels"]["left_leg"]["contact"] == {"kind": "ground"}
    assert {g["contact_object"] for g in out["ik_goals"]} == {"pills"}


async def test_the_two_constraint_vocabularies_are_renamed_apart(registry):
    """channels.*.constraint and ik_goals[].constraint share a name and mean unrelated things."""
    out = await registry.dispatch("kb_get_action", {"action_id": "grab_bottle",
                                                    "include": ["channels", "ik_goals"]})
    assert out["reach_requirement"]["right_hand"] == "must-reach"
    assert out["ik_goals"][0]["solver"] == "TwoBoneIKConstraint"
    assert "constraint" not in out
    assert all("constraint" not in g for g in out["ik_goals"])


async def test_defective_composability_fields_never_reach_the_model(registry):
    """A schema omission is enforceable; a prompt instruction is not."""
    out = await registry.dispatch("kb_get_action", {"action_id": "grab_bottle",
                                                    "include": ["channels", "ik_goals", "summary"]})
    blob = repr(out)
    for field in ("can_overlay_on", "locks", "seam_owner", "extraction", "guid", "file_id"):
        assert field not in blob, field


async def test_projection_defaults_to_channels_only(registry):
    out = await registry.dispatch("kb_get_action", {"action_id": "walking"})
    assert set(out) <= {"action_id", "display_name", "channels", "reach_requirement", "success"}
    assert "tags" not in out
    assert len(out["channels"]) == 9


async def test_channel_subset_is_honoured(registry):
    out = await registry.dispatch("kb_get_action", {"action_id": "walking",
                                                    "channels": ["left_leg", "right_leg"]})
    assert sorted(out["channels"]) == ["left_leg", "right_leg"]


async def test_unknown_action_is_a_recoverable_failure_not_a_crash(registry):
    """The model can fix this itself, so the turn must survive it."""
    out = await registry.dispatch("kb_get_action", {"action_id": "moonwalk"})
    assert out["success"] is False
    assert "moonwalk" in out["error"]
    assert "kb_search" in out["hint"]


async def test_bad_arguments_are_reported_back_not_raised(registry):
    out = await registry.dispatch("kb_search", {"nonsense": 1})
    assert out["success"] is False
    assert "bad arguments" in out["error"]

    out = await registry.dispatch("no_such_tool", {})
    assert out["success"] is False
    assert "kb_search" in out["hint"]


# ---- kb_pose: the tools that compute, rather than fetch ---------------------------------------

async def test_pose_reports_typing_as_seated_at_its_very_first_frame(registry):
    """The question that started this: does Typing begin standing? Hips near 0.46 m, not 0.90 m.

    Unanswerable with `read`, and that is why this is a tool: the per-frame data is one line of about
    two megabytes, so there is no window of it a model can usefully be shown.
    """
    out = await registry.dispatch("kb_pose", {"action_id": "typing", "at": "start"})
    assert out["success"] and out["frame"] == 0
    assert out["hips_height_m"] < 0.6
    assert out["posture"] == "seated"


async def test_pose_reports_idle_as_standing(registry):
    out = await registry.dispatch("kb_pose", {"action_id": "idle", "at": "middle"})
    assert out["hips_height_m"] > 0.8 and out["both_feet_planted"] is True


async def test_pose_rejects_an_unknown_action(registry):
    out = await registry.dispatch("kb_pose", {"action_id": "sit_down", "at": "start"})
    assert out["success"] is False and "sit_down" in out["error"]


# ---- kb_transition ---------------------------------------------------------------------------

async def test_transition_reports_a_joinable_pair_as_joinable(registry):
    out = await registry.dispatch("kb_transition", {"from_action": "check_pulse",
                                                    "to_action": "giving_pills"})
    assert out["success"] and out["joinable_by_blending"] is True
    assert out["blend_frames"] >= 1


async def test_transition_refuses_to_pretend_a_posture_change_is_a_blend(registry):
    out = await registry.dispatch("kb_transition", {"from_action": "walking", "to_action": "typing"})
    assert out["class"] == "posture_change"
    assert out["joinable_by_blending"] is False


async def test_a_posture_change_says_how_to_make_it_rather_than_what_is_missing(registry):
    """Measured: told the frames were ones "neither clip contains", the model read a true statement as
    a refusal, answered that the library could not do it, and stopped -- with the generator one call
    away. Accurate and unusable is still unusable."""
    out = await registry.dispatch("kb_transition", {"from_action": "walking", "to_action": "typing"})
    assert out["can_be_generated"] is True
    assert "GENERATED for you" in out["how"]
    assert "base=walking" in out["how"] and "base: typing" in out["how"]
    assert "sit_on" in out["how"]


async def test_transition_rejects_a_self_transition(registry):
    out = await registry.dispatch("kb_transition", {"from_action": "idle", "to_action": "idle"})
    assert out["success"] is False


async def test_the_narrow_arm_withholds_the_measuring_tools(kb):
    """The comparison arm is a fixed membership, not whatever happens to be in this module."""
    from agent.tools import ToolRegistry as R
    narrow = kb_tools.register(R(), kb, measuring=False)
    assert narrow.names() == ["kb_search", "kb_get_action"]
