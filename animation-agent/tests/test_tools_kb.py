"""The five motion tools: what a search can be steered by, what the analysis tools measure, and the
isolation the whole split exists to produce.

WHAT A SMALL LIBRARY BUYS. Every test here runs against twelve real records copied into a temporary
`actions/` directory (see `tests/corpus.py`). The ids are real, so the raw dumps, the segment table
and the posture sidecar all answer about them exactly as they do in production — what the temporary
store changes is how MANY documents the index holds. That matters because a ranking over twelve
documents is predictable and a ranking over 2446 is a fact about whatever else happens to be in the
library, and a test that asserted the second would be asserting the corpus rather than the tool.

THE ISOLATION TESTS RUN AGAINST THE REAL ONE, on purpose. "No nursing motion is reachable" is a claim
about the library the agent actually gets, and a fixture cannot make it true.
"""
import json

import pytest

from agent import segments as S
from agent.kbindex import KBIndex
from agent.tools import ToolRegistry
from agent.tools import files as file_tools
from agent.tools import kb as kb_tools
from tests import corpus as C

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def small_kb(tmp_path_factory):
    return KBIndex.load(actions_dir=C.copy_store(tmp_path_factory.mktemp("kb")))


@pytest.fixture
def registry(small_kb):
    return kb_tools.register(ToolRegistry(), small_kb)


@pytest.fixture(scope="module")
def full_registry():
    """The real library, for the claims that are about the real library."""
    registry = kb_tools.register(ToolRegistry(), KBIndex.load())
    file_tools.register(registry)
    return registry


async def call(registry, name, **kwargs):
    out = await registry.dispatch(name, kwargs)
    assert out.get("success") is not False, out
    return out


async def fails(registry, name, **kwargs):
    out = await registry.dispatch(name, kwargs)
    assert out.get("success") is False, out
    return out


# ---- the surface -----------------------------------------------------------------------------

async def test_declarations_use_the_flat_realtime_shape(registry):
    """Chat Completions nests these under a "function" key. Getting it wrong yields a session that
    accepts the update and then never calls a tool, with no error."""
    for decl in registry.declarations():
        assert decl["type"] == "function"
        assert isinstance(decl["name"], str) and decl["name"]
        assert "function" not in decl
        assert decl["parameters"]["additionalProperties"] is False


async def test_the_five_motion_tools_are_the_five(registry):
    assert registry.names() == ["motion_search", "motion_channels", "motion_timing",
                                "motion_compose", "motion_transition"]


async def test_the_narrow_arm_withholds_the_measuring_tools(small_kb):
    """The comparison arm is the surface as it stood before per-frame measurement was exposed at all.
    It only means something if its membership stays fixed while these modules are reorganised."""
    narrow = kb_tools.register(ToolRegistry(), small_kb, measuring=False)
    assert narrow.names() == ["motion_search", "motion_channels"]


# ---- motion_search ---------------------------------------------------------------------------

async def test_search_returns_what_a_choice_is_made_on(registry):
    """`moves` is what `drives` became, and the rename is the point: `drives` was the channels whose
    `role` was `primary`, a preview of a partition the record no longer holds. What a hit can honestly
    offer is the parts it ANIMATES, which is the pool an overlay's channel list draws from."""
    out = await call(registry, "motion_search", query="walking forward", top_k=3)
    assert out["corpus_size"] == len(C.SMALL_STORE)
    top = out["results"][0]
    assert top["action_id"] == C.WALK
    assert set(top) == {"action_id", "description", "score", "matched_evidence", "posture",
                        "start_posture", "end_posture", "duration_s", "moves"}
    assert "left_leg" in top["moves"] and "right_leg" in top["moves"]
    assert top["posture"] == "standing"


async def test_a_hit_carries_both_ends_not_just_the_dominant_posture(registry):
    """Whether two clips can follow one another is decided by where one finishes and the next begins.
    A result offering only the dominant reading makes that a second round trip per candidate — and
    describes a clip that CHANGES posture as something it never is: since posture algorithm 2.0.0 a
    stand-up is dominantly `other`, because most of its frames are the rise itself, which is neither
    end. The two ends are the fact a plan needs and the dominant reading cannot carry."""
    out = await call(registry, "motion_search", query="standing up from a seat", top_k=12)
    by_id = {r["action_id"]: r for r in out["results"]}
    assert by_id[C.STAND_UP]["posture"] == "other"
    assert by_id[C.STAND_UP]["start_posture"] == "seated"
    assert by_id[C.STAND_UP]["end_posture"] == "standing"


async def test_the_corpus_prefix_is_not_a_word(registry):
    """Every one of the 2446 ids begins `mx_`, so the token carries no meaning in either direction: a
    query containing it would match everything, and `query_coverage` would count it as a word the
    library has."""
    out = await call(registry, "motion_search", query="mx", top_k=3)
    assert out["query_coverage"] is None, "a query of nothing but the prefix has no content words"


async def test_coverage_says_how_much_of_the_request_the_library_has_words_for(registry):
    """The honest no-match signal. BM25 always ranks something first, so a query about pressing a
    button still returns a top hit; coverage says how much of what was asked for is in the vocabulary
    at all."""
    known = await call(registry, "motion_search", query="walking forward", top_k=1)
    absent = await call(registry, "motion_search",
                        query="xylophone glockenspiel harpsichord", top_k=1)
    assert known["query_coverage"] == 1.0
    assert absent["query_coverage"] == 0.0
    assert absent["results"], "a low score is still a ranking, not an empty answer"


async def test_posture_keeps_only_what_the_clip_mostly_is(registry):
    """`posture` filters on the dominant reading, which is what a clip mostly IS. A stand-up is
    mostly the rise — neither seated nor standing — and lands under `other` with the kneel; where a
    plan needs its two ENDS it asks `transition` instead, which the next case covers."""
    for posture, expected in (("seated", {C.SEATED, C.SIT_DOWN}),
                              ("floor", {C.FLOOR, C.CHEST, C.FALL}),
                              ("other", {C.OTHER, C.STAND_UP})):
        out = await call(registry, "motion_search", query="", posture=posture, top_k=20)
        assert {r["action_id"] for r in out["results"]} == expected, posture


async def test_transition_keeps_only_what_the_clip_crosses(registry):
    """The query a posture change needs, and the one `posture` cannot express: a clip that stands up
    out of a chair is dominantly one or the other and matches neither honestly."""
    out = await call(registry, "motion_search", query="", top_k=20,
                     transition={"from_posture": "standing", "to_posture": "seated"})
    assert {r["action_id"] for r in out["results"]} == {C.SIT_DOWN}

    out = await call(registry, "motion_search", query="", top_k=20,
                     transition={"from_posture": "seated", "to_posture": "standing"})
    assert {r["action_id"] for r in out["results"]} == {C.STAND_UP}


async def test_posture_and_transition_intersect(registry):
    """Both are filters over the same posture sidecar, and filters narrow together.

    They used to be refused as a pair, which cost a measured turn: the model sent both fourteen times
    running and the turn ended on the iteration budget. `mx_Standing_To_Sitting_Transition` is
    dominantly seated AND crosses standing to seated, so it is what the intersection keeps.
    """
    out = await call(registry, "motion_search", query="", top_k=20, posture="seated",
                     transition={"from_posture": "standing", "to_posture": "seated"})
    assert {r["action_id"] for r in out["results"]} == {C.SIT_DOWN}


async def test_a_posture_that_contradicts_the_transition_is_dropped_not_intersected(registry):
    """A clip cannot be dominantly on the floor and also finish a sit-down, and the honest answer to
    that pair is not an empty result: it is that `posture` was a guess about something unguessable.
    The transition is what was asked for, so the transition is what is served — and the reply says
    the other half was dropped."""
    out = await call(registry, "motion_search", query="", top_k=20, posture="floor",
                     transition={"from_posture": "standing", "to_posture": "seated"})
    assert {r["action_id"] for r in out["results"]} == {C.SIT_DOWN}
    assert out["posture_ignored"].startswith("transition given")


async def test_exclude_is_how_a_second_search_says_not_these(registry):
    """Rephrasing reorders the whole ranking and hands the rejected clips back in a different order.
    Naming them is how "not these" is said once."""
    first = await call(registry, "motion_search", query="walking forward", top_k=3)
    rejected = [r["action_id"] for r in first["results"][:2]]
    second = await call(registry, "motion_search", query="walking forward", top_k=3,
                        exclude=rejected)
    assert not (set(r["action_id"] for r in second["results"]) & set(rejected))
    assert second["results"], "excluding two of twelve leaves ten to rank"


async def test_an_exclude_the_library_does_not_hold_is_ignored_and_said(registry):
    """"Not this" about something that is in no record excludes nothing, so it is not a question.
    Measured: the model filled the parameter with `["placeholder"]` on every call of a turn."""
    out = await call(registry, "motion_search", query="walking forward", top_k=3,
                     exclude=["placeholder", C.WALK])
    assert out["ignored_exclude"] == ["placeholder"]
    assert C.WALK not in {r["action_id"] for r in out["results"]}, "the real id still excludes"


async def test_an_empty_list_means_the_parameter_was_left_out(registry):
    """Models fill every optional parameter they are shown. An empty list has to read as absence, or
    a search narrows itself to nothing on arguments nobody meant."""
    filtered = await call(registry, "motion_search", query="walking forward", top_k=5,
                          exclude=[], moves_channels=[], transition={})
    plain = await call(registry, "motion_search", query="walking forward", top_k=5)
    assert [r["action_id"] for r in filtered["results"]] == [r["action_id"] for r in plain["results"]]
    assert "ignored_exclude" not in filtered


async def test_moves_channels_keeps_only_what_actually_animates_them(registry):
    out = await call(registry, "motion_search", query="", moves_channels=["left_leg", "right_leg"],
                     top_k=20)
    ids = {r["action_id"] for r in out["results"]}
    assert C.WALK in ids
    assert C.POSE not in ids, "a two-frame pose animates nothing"
    assert C.IDLE not in ids, "a stance holds its legs still"


async def test_a_filter_nothing_satisfies_says_so_rather_than_answering_empty(registry):
    out = await call(registry, "motion_search", query="walking", posture="floor",
                     moves_channels=["left_hand"], top_k=5)
    if not out["results"]:
        assert "relax them" in out["note"]


async def test_top_k_reaches_twenty(registry):
    """8 was the cap over an eight-action library, where it meant "all of them". Over 2446 a search
    that can only see eight candidates cannot be steered by reading them."""
    schema = next(d for d in registry.declarations() if d["name"] == "motion_search")
    assert schema["parameters"]["properties"]["top_k"]["maximum"] == 20


# ---- motion_channels -------------------------------------------------------------------------

async def test_channels_returns_all_nine_with_what_each_part_does(registry):
    out = await call(registry, "motion_channels", action_id=C.WALK)
    assert out["action_id"] == C.WALK and out["description"]
    assert set(out["channels"]) == {"root", "torso", "head", "left_arm", "right_arm",
                                    "left_leg", "right_leg", "left_hand", "right_hand"}
    for name, block in out["channels"].items():
        assert set(block) == {"state", "motion_description"}, name
    assert out["channels"]["left_leg"]["state"] == "dynamic"
    assert out["channels"]["left_leg"]["motion_description"]


async def test_an_unknown_action_is_a_failure_the_model_can_act_on(registry):
    out = await fails(registry, "motion_channels", action_id="mx_No_Such_Clip")
    assert "unknown action_id" in out["error"] and "motion_search" in out["hint"]


# ---- motion_timing ---------------------------------------------------------------------------

async def test_timing_says_when_each_part_moves_and_what_the_body_is_doing(registry):
    out = await call(registry, "motion_timing", action_id=C.SIT_DOWN)
    assert out["duration_s"] and out["frame_rate"]
    span = out["channels"]["left_leg"]["active_span"]
    assert 0 <= span["start_frame"] < span["end_frame"]
    assert span["seconds"] == pytest.approx(
        (span["end_frame"] - span["start_frame"]) / out["frame_rate"], abs=0.05)
    assert out["channels"]["left_leg"]["repeatable"] is False
    assert out["channels"]["left_leg"]["cycle_frames"] is None


async def test_timing_carries_the_posture_structure_from_the_sidecar(registry):
    """The five fields `build_posture.py` writes. A clip that changes posture partway is the case the
    dominant reading cannot express, and this is where an agent finds out it does.

    THE CROSSING IS ASSERTED, NOT ITS SHAPE. This used to require a DIRECT standing->seated boundary,
    which was asserting how coarse the old rule was: since 2.0.0 the middle of a descent is `other`,
    because she is neither standing on her feet nor yet supported by the seat, and that is what
    `other` is for. What the tool has to carry is the same either way — where the clip starts, where
    it ends, and every boundary announced in order.
    """
    out = await call(registry, "motion_timing", action_id=C.SIT_DOWN)
    assert out["start_posture"] == "standing" and out["end_posture"] == "seated"
    assert out["dominant_posture"] == "seated"
    postures = [seg["posture"] for seg in out["posture_segments"]]
    assert postures[0] == "standing" and postures[-1] == "seated"
    assert "seated" not in postures[:1] and "standing" not in postures[1:]
    # One transition per seam, each naming the two states it joins and the frame the new one starts.
    assert len(out["posture_transitions"]) == len(out["posture_segments"]) - 1
    for index, change in enumerate(out["posture_transitions"]):
        assert change["from"] == postures[index] and change["to"] == postures[index + 1]
        assert change["at_frame"] == out["posture_segments"][index + 1]["start_frame"]


async def test_the_posture_segments_cover_the_clip_with_no_gap(registry):
    for action_id in (C.WALK, C.SIT_DOWN, C.FALL, C.FLOOR):
        out = await call(registry, "motion_timing", action_id=action_id)
        segments = out["posture_segments"]
        assert segments[0]["start_frame"] == 0
        for before, after in zip(segments, segments[1:]):
            assert after["start_frame"] == before["end_frame"] + 1, action_id


async def test_a_repeating_clip_is_reported_as_repeatable(registry):
    """`temporal_intent: repeat` is only honest where a period was measured."""
    out = await call(registry, "motion_timing", action_id=C.CYCLIC)
    repeating = [name for name, block in out["channels"].items() if block["repeatable"]]
    assert repeating
    for name in repeating:
        assert out["channels"][name]["cycle_frames"] > 0


async def test_timing_sends_no_numbers_in(registry):
    """Read-only, and the parameter list says so: one identifier and nothing else. There is nowhere
    for a frame index to enter."""
    schema = next(d for d in registry.declarations() if d["name"] == "motion_timing")
    assert list(schema["parameters"]["properties"]) == ["action_id"]


# ---- motion_compose --------------------------------------------------------------------------

async def test_compose_resolves_a_split_without_an_engine(registry):
    out = await call(registry, "motion_compose", base=C.WALK,
                     overlays=[{"action_id": C.GRAB, "channels": ["right_arm", "right_hand"]}])
    assert out["base"] == C.WALK
    partition = {e["action_id"]: e["channels"] for e in out["partition"]}
    assert partition[C.GRAB] == ["right_arm", "right_hand"]
    assert out["root_owner"] == C.WALK
    assert out["conflicts"] == [] and out["shared"] == []
    assert "left_leg" in out["free_channels"]


async def test_a_channel_two_overlays_name_is_halved(registry):
    out = await call(registry, "motion_compose", base=C.IDLE,
                     overlays=[{"action_id": C.WALK, "channels": ["right_arm"]},
                               {"action_id": C.GRAB, "channels": ["right_arm"]}])
    shared = {m["channel"]: {s["action_id"]: s["share"] for s in m["shares"]}
              for m in out["shared"]}
    assert shared["right_arm"][C.WALK] == pytest.approx(0.5)
    assert shared["right_arm"][C.GRAB] == pytest.approx(0.5)


async def test_a_pinned_hand_turns_a_share_into_a_conflict(registry):
    """A hand bound to something in the scene cannot be averaged out of two grips: it would hold
    neither. Reported here, without an engine, so the arrangement can be fixed before it is sent."""
    args = {"base": C.IDLE,
            "overlays": [{"action_id": C.WALK, "channels": ["right_hand"]},
                         {"action_id": C.GRAB, "channels": ["right_hand"]}]}
    loose = await call(registry, "motion_compose", **args)
    assert [m["channel"] for m in loose["shared"]] == ["right_hand"] and not loose["conflicts"]

    pinned = await call(registry, "motion_compose", pinned=["right_hand"], **args)
    assert pinned["shared"] == []
    assert [c["channel"] for c in pinned["conflicts"]] == ["right_hand"]
    assert "bound to something in the scene" in pinned["conflicts"][0]["why"]


@pytest.mark.parametrize("intent,loops", [("once", False), ("repeat", True)])
async def test_temporal_intent_decides_whether_the_window_repeats(registry, intent, loops):
    """The measurement decides WHICH frames; the intent decides whether they repeat. That one bit is
    a fact about the task, and it is the only thing the agent supplies here."""
    out = await call(registry, "motion_compose", base=C.IDLE,
                     overlays=[{"action_id": C.CHEST, "channels": ["right_arm", "right_hand"],
                                "temporal_intent": intent}])
    entry = next(e for e in out["schedule"] if e["action_id"] == C.CHEST)
    assert entry["temporal_intent"] == intent
    assert entry["frame_window"]["loop"] is loops


async def test_continuous_drops_the_window_and_plays_the_whole_clip(registry):
    out = await call(registry, "motion_compose", base=C.IDLE,
                     overlays=[{"action_id": C.CHEST, "channels": ["right_arm"],
                                "temporal_intent": "continuous"}])
    entry = next(e for e in out["schedule"] if e["action_id"] == C.CHEST)
    assert entry["frame_window"] is None


async def test_repeating_a_window_that_does_not_rejoin_says_what_it_costs(registry):
    """Honoured and reported. A request to keep something going is a real request, and silently not
    repeating would leave the agent describing a motion that stopped."""
    out = await call(registry, "motion_compose", base=C.IDLE,
                     overlays=[{"action_id": C.GRAB, "channels": ["right_arm"],
                                "temporal_intent": "repeat"}])
    window = next(e for e in out["schedule"] if e["action_id"] == C.GRAB)["frame_window"]
    assert window["loop"] is True
    assert "jump" in window["why"]


async def test_the_window_compose_reports_is_the_one_the_measurement_gives(registry):
    """Same table `unity_execute` reads, so a composition that resolves here is the one that will be
    sent. A preview of the plan rather than a model of it."""
    out = await call(registry, "motion_compose", base=C.IDLE,
                     overlays=[{"action_id": C.GRAB, "channels": ["right_arm", "right_hand"]}])
    window = next(e for e in out["schedule"] if e["action_id"] == C.GRAB)["frame_window"]
    measured = S.window_for(S.read_table()[C.GRAB], ["right_arm", "right_hand"])
    assert window["start_frame"] == measured["start_frame"]
    assert window["end_frame"] == measured["end_frame"]


async def test_two_postures_at_once_are_reported_rather_than_resolved(registry):
    """Overlays are simultaneous and two postures cannot be. The fix is a different arrangement rather
    than a different action, and finding that out without an engine round trip is what this is for."""
    out = await call(registry, "motion_compose", base=C.WALK,
                     overlays=[{"action_id": C.SEATED, "channels": ["right_arm"]}])
    assert out["posture_conflict"]["postures"][C.SEATED] == "seated"
    assert "`then`" in out["posture_conflict"]["hint"]


async def test_an_overlay_that_names_no_channels_is_refused_by_name(registry):
    """Not defaulted. An empty mask plays FULL BODY at full weight in the engine, so accepting one
    would replace the whole body with the overlay."""
    out = await fails(registry, "motion_compose", base=C.WALK,
                      overlays=[{"action_id": C.GRAB, "channels": []}])
    assert "names no channels" in out["error"]


async def test_compose_takes_no_numbers(registry):
    """Numbers come OUT — a frame window, a share — and never in. Every parameter is an identifier,
    an enum or a list of them, so the invariant is structural rather than aspirational."""
    schema = next(d for d in registry.declarations() if d["name"] == "motion_compose")

    def leaves(node):
        if node.get("type") in ("object",) or "properties" in node:
            for child in (node.get("properties") or {}).values():
                for leaf in leaves(child):
                    yield leaf
        elif node.get("type") == "array":
            for leaf in leaves(node.get("items") or {}):
                yield leaf
        else:
            yield node

    for leaf in leaves(schema["parameters"]):
        assert leaf.get("type") == "string", leaf


# ---- motion_transition -----------------------------------------------------------------------

async def test_a_same_posture_pair_answers_with_the_seam(registry):
    out = await call(registry, "motion_transition", from_action=C.WALK, to_action=C.IDLE)
    assert out["joinable_by_blending"] is True
    assert out["from_end_posture"] == "standing" and out["to_start_posture"] == "standing"
    assert out["blend_frames"] >= 0 and out["cost_deg"] > 0
    assert out["class"] in ("direct", "blend")


async def test_a_posture_change_names_the_change_rather_than_the_absence(registry):
    """Answering "these do not join" is true and reads as a refusal: measured on the eight-action
    library, the model called this, concluded the motion was impossible and stopped. There are two
    ways forward and both are named."""
    out = await call(registry, "motion_transition", from_action=C.WALK, to_action=C.SEATED)
    assert out["joinable_by_blending"] is False
    assert out["required_transition"] == {"from_posture": "standing", "to_posture": "seated"}
    assert out["synthesis_available"] is True
    assert "motion_search" in out["how"] and "via" in out["how"]
    assert "routes" not in out, "the library is 2446 clips; it is not enumerated here"


async def test_a_change_with_no_generator_says_so(registry):
    """The executor descends onto a support and rises off one. There is no generator for lying down,
    and claiming otherwise would send an agent to `then` for frames that will not be made."""
    out = await call(registry, "motion_transition", from_action=C.WALK, to_action=C.FLOOR)
    assert out["required_transition"] == {"from_posture": "standing", "to_posture": "floor"}
    assert out["synthesis_available"] is False


async def test_via_costs_each_candidate_at_both_joins_and_ranks_them(registry):
    """Geometry orders; the agent chooses. Which clip MEANS the right thing is a question about
    meaning, and a rank is not an answer to it."""
    out = await call(registry, "motion_transition", from_action=C.WALK, to_action=C.SEATED,
                     via=[C.SIT_DOWN, C.STAND_UP])
    routes = out["routes"]
    assert [r["via"] for r in routes] == sorted(
        [r["via"] for r in routes], key=lambda v: next(x["total_cost_deg"] for x in routes
                                                       if x["via"] == v))
    for rank, route in enumerate(routes, 1):
        assert route["geometric_rank"] == rank
        assert route["total_cost_deg"] == pytest.approx(
            route["entry_cost_deg"] + route["exit_cost_deg"], abs=1e-6)
        assert route["start_posture"] and route["end_posture"]
    assert "MEANS the wrong thing" in out["note"]


async def test_the_right_bridge_is_the_one_that_joins_cleanly_at_both_ends(registry):
    """`mx_Standing_To_Sitting_Transition` starts standing and ends seated, so a walk joins its head
    and a seated clip joins its tail: two ordinary blends and nothing generated. The stand-up crosses
    the other way and cannot serve, however its numbers come out."""
    out = await call(registry, "motion_transition", from_action=C.WALK, to_action=C.SEATED,
                     via=[C.SIT_DOWN, C.STAND_UP])
    clean = {r["via"]: r["joins_cleanly"] for r in out["routes"]}
    assert clean[C.SIT_DOWN] is True
    assert clean[C.STAND_UP] is False


async def test_an_action_does_not_transition_to_itself(registry):
    out = await fails(registry, "motion_transition", from_action=C.WALK, to_action=C.WALK)
    assert "does not transition to itself" in out["error"]


async def test_a_via_cannot_be_one_of_the_two_ends(registry):
    out = await fails(registry, "motion_transition", from_action=C.WALK, to_action=C.SEATED,
                      via=[C.WALK])
    assert "cannot be one of the two ends" in out["error"]


# ---- isolation: the eight nursing records are unreachable --------------------------------------

async def test_searching_for_a_nursing_motion_returns_only_corpus_clips(full_registry):
    """The claim the whole split exists to make. `cpr` was one of the eight accepted actions and
    "chest compressions" was the query written to find it; the library the agent gets now holds 2446
    Mixamo clips and none of the eight."""
    out = await call(full_registry, "motion_search", query="chest compressions", top_k=20)
    assert out["corpus_size"] == 2446
    found = C.has_nursing_content(json.dumps(out))
    assert not found, "a nursing token reached a search result: %s" % found
    assert out["results"], "the corpus still answers the query, with its own clips"
    assert all(r["action_id"].startswith("mx_") for r in out["results"])


@pytest.mark.parametrize("query", ["bag valve mask", "checking a pulse at the wrist",
                                   "giving pills to a patient", "typing at a nurse station"])
async def test_no_nursing_query_reaches_a_nursing_clip(full_registry, query):
    out = await call(full_registry, "motion_search", query=query, top_k=20)
    assert not C.has_nursing_content(json.dumps(out)), query


async def test_the_source_mount_holds_the_corpus_and_nothing_else(full_registry):
    """`glob("source/**")` is the other half: the assets mount used to be all of `Assets/Animations`,
    which holds the nursing FBX and `.anim` beside the corpus."""
    out = await full_registry.dispatch("glob", {"pattern": "source/**"})
    if out.get("success") is False:
        pytest.skip("source assets not mounted")
    for pattern in ("source/**", "source/*", "source/**/*"):
        found = await call(full_registry, "glob", pattern=pattern)
        for path in found["paths"]:
            assert not C.has_nursing_content(path), path


async def test_the_eight_action_ids_are_gone_from_the_library(full_registry):
    for action_id in ("walking", "typing", "idle", "cpr", "bvm", "check_pulse", "giving_pills",
                      "grab_bottle"):
        out = await fails(full_registry, "motion_channels", action_id=action_id)
        assert "unknown action_id" in out["error"]


async def test_a_transition_search_drops_posture_and_says_so(registry):
    """A transition clip's dominant posture is not predictable — of the two clips in the real library
    that go standing to seated, one is dominantly standing and the other dominantly seated. Measured
    on a live turn: the model asked for both, the intersection removed
    `mx_Standing_To_Sitting_Transition`, and it concluded from the one clip left that nothing in the
    library could sit somebody on a chair."""
    out = await call(registry, "motion_search", query="", top_k=20, posture="standing",
                     transition={"from_posture": "standing", "to_posture": "seated"})
    assert out["posture_ignored"].startswith("transition given")
    assert {r["action_id"] for r in out["results"]} == {C.SIT_DOWN}, \
        "the sit-down is dominantly seated and a `posture: standing` filter would have dropped it"


async def test_posture_alone_is_still_applied(registry):
    """The drop is only when the two are given together; `posture` on its own is untouched."""
    out = await call(registry, "motion_search", query="", top_k=20, posture="seated")
    assert "posture_ignored" not in out
    assert {r["action_id"] for r in out["results"]} == {C.SEATED, C.SIT_DOWN}
