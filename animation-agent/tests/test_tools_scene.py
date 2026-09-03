"""Scene and plan tools against the fake engine.

The load-bearing assertion is that the plan tool has nowhere for a number to ENTER, and that the scene
tools have nowhere for one to LEAVE. `unity_query` answers which thing; `unity_query` answers what it
is to her right now. Neither emits a coordinate, a distance or a surface height — those still exist and
are still measured, they simply stop passing through the model on their way to the solver that uses
them.
"""
import pytest

import math

from agent import protocol as P
from agent import kbindex as KI
from agent.tools.scene import standing_point_for
from agent import segments as S
from agent import transitions as T
from agent.engine import EngineLink
from agent.kbindex import KBIndex
from agent.tools import ToolRegistry
from agent.tools import kb as kb_tools
from agent.tools import scene as scene_tools
from tests import corpus as C
from tests.fake_engine import FakeEngine, FakeEngineError

pytestmark = pytest.mark.asyncio

OBJECTS = {
    "obj:AspirinBottle": {"id": "obj:AspirinBottle", "name": "Aspirin Bottle",
                          "category": "consumable", "aliases": ["aspirin_bottle", "pills"],
                          "on": "obj:MedCabinet", "held_by": None, "reachable": False},
    "obj:MonitorVitals": {"id": "obj:MonitorVitals", "name": "Monitor Vitals",
                          "category": "device", "aliases": [], "held_by": None, "reachable": True},
    # The other end of half this library. `cpr`, `check_pulse` and `bvm` all reach for a person, and
    # until a plan could ask to hold two things at once nothing needed to resolve which person.
    # The nurse the tools drive. She is not in the object registry -- the executor answers for her out
    # of its own list -- but she comes back from a search, which is how her id becomes findable at all.
    "chr:CPRNurse": {"id": "chr:CPRNurse", "name": "CPRNurse", "category": "character",
                     "aliases": [], "held_by": None, "reachable": True, "drivable": True},
    "obj:Patient": {"id": "obj:Patient", "name": "Patient", "category": "character",
                    "aliases": ["patient_chest", "patient_wrist", "patient"], "held_by": None,
                    "reachable": True, "carriable": False},
    "obj:Chair": {"id": "obj:Chair", "name": "Chair", "category": "seating",
                  "aliases": ["chair", "seat", "stool"], "held_by": None, "reachable": True,
                  "has_usable_surface": True, "carriable": False},
    # The scene carries four authored transforms saying where each hand and each elbow goes on this
    # laptop, which is what `two_handed_anchors` reports. A bottle has no such thing.
    "obj:Laptop": {"id": "obj:Laptop", "name": "Laptop", "category": "device",
                   "aliases": ["keyboard", "laptop", "computer"], "held_by": None,
                   "reachable": True, "has_usable_surface": True, "carriable": False,
                   "two_handed_anchors": True},
}


# GameObjects nobody annotated. The real registry is about 30 hand-reviewed entries out of 600, so a
# name outside it has to come back as "there, but nothing is known about it" rather than as absence.
RAW_SCENE = ["Curtain Rail", "IV Stand"]

# Where the fake scene puts everything it is asked about. One position for every object, which is
# enough for the tests that read one -- the standing-point arithmetic is checked against its own
# inputs rather than against a scene layout, in tests/test_seat_alignment.py.
SCENE_POSITION = (1.0, 0.5, -2.0)

# What the engine says she is doing when asked. Mutated by the tests that care; the default is the
# state every other test was written against, where nothing is playing.
STANDING = {"playing": None, "going": False}

# Whether the destination is somewhere she has to walk to. Flipped by the tests about what a plan
# opens on: a walk of zero length plays nothing, so the plan after it departs from whatever she is
# already doing rather than from a walk cycle she never performed.
ROUTE = {"already_there": False}

# The carry half of dc-walk-carry, written the way a plan writes it. Since v4 the agent names the
# channels an overlay drives and the knowledge base no longer derives them (ADR 0022), so every test
# that composes something has to say the split -- and this is the same list the eval ground truth in
# `retrieval_eval_set.json` holds for that case.
CARRY = {"action_id": C.GRAB, "channels": ["right_arm", "right_hand"]}
LEGS = ["left_leg", "right_leg"]


def committed(submitted):
    """The plans that actually played. Since v4 every commit is preceded by the same plan sent as
    `validate` — that pair IS the fence — so a test about what played filters to this side of it."""
    return [s for s in submitted if isinstance(s, dict) and s.get("mode") == "commit"]


def checked(submitted):
    """And the other side: the plans that ran on the hidden copy before anything moved."""
    return [s for s in submitted if isinstance(s, dict) and s.get("mode") == "validate"]


def same_plan(a, b):
    """Whether two requests carry the same compiled plan. `mode` is the one word that differs by
    design, and `at` is where the hidden copy was stood — the projected arrival of a walk that has
    not happened yet, which by definition has no counterpart on the commit."""
    keys = ("mode", "at")
    return ({k: v for k, v in a.items() if k not in keys}
            == {k: v for k, v in b.items() if k not in keys})


def fenced(submitted):
    """Each committed plan paired with the check that preceded it, as (probe, played). Raises if any
    commit has no matching check before it — which is the whole invariant."""
    pairs = []
    for index, entry in enumerate(submitted):
        if not isinstance(entry, dict) or entry.get("mode") != "commit":
            continue
        probe = next((p for p in reversed(submitted[:index])
                      if isinstance(p, dict) and p.get("mode") == "validate"
                      and same_plan(p, entry)), None)
        assert probe is not None, "a plan played without being checked first: %r" % (entry,)
        pairs.append((probe, entry))
    return pairs


# What the executor answers a `validate` with when the plan is sound: the same metric shapes the
# runtime gate reports, measured on a hidden duplicate before anything visible has moved. A stand-in
# that simply passed everything would hide the fence rather than model it, so the tests that are about
# a refusal install `refusing_handlers` below instead.
def passing_verdict(params):
    return {"status": "pass",
            "checked": ["ground_penetration", "foot_skate", "contact_reached:right_hand"],
            "samples": 61, "seconds_simulated": 2.0, "failures": [], "metrics": []}


def handlers(record, moves=None, verdict=passing_verdict):
    def find(params):
        alias = params.get("alias")
        name = (params.get("name_contains") or "").lower()
        category = params.get("category")
        out = [o for o in OBJECTS.values()
               if (not alias or alias in o["aliases"])
               and (not name or name in o["name"].lower())
               and (not category or category == o["category"])]
        # The engine's own last resort: a bare name that the annotated registry does not know is
        # looked up against raw GameObject names. Modelled here because the note the agent attaches to
        # those hits — nothing is known beyond that it exists — is the thing under test.
        if not out and name and not alias and not category:
            out = [{"id": "scene:" + raw.replace(" ", ""), "name": raw, "source": "scene"}
                   for raw in RAW_SCENE if name in raw.lower()]
        return {"objects": out}

    def describe(params):
        obj = OBJECTS.get(params["object_id"])
        if obj is None:
            raise FakeEngineError(P.E.NOT_FOUND, "no object %s" % params["object_id"])
        return obj

    def assemble(params):
        record.append(params)
        # v4: the same compiled plan arrives twice, once to be checked out of sight and once to play.
        # Both are recorded, because "was it checked before it played" is a fact about the order of
        # these calls and the tests assert on it.
        if params.get("mode") == "validate":
            return verdict(params)
        return {"plan_id": "pl_1", "accepted": True, "start_play_latency_ms": 31}

    def position(params):
        out = []
        for object_id in params["object_ids"]:
            obj = OBJECTS.get(object_id)
            if obj is None:
                out.append({"object_id": object_id, "found": False})
                continue
            item = {"object_id": object_id, "found": True, "position": list(SCENE_POSITION)}
            if object_id == "obj:Chair":
                item["surface_height_m"] = 0.4054
            if object_id == "obj:Laptop":
                # The real one sits on a desk at 1.16 m. It has a surface, which is exactly why it was
                # accepted as somewhere to sit.
                item["surface_height_m"] = 1.163
            if params.get("relative_to"):
                item["from_character"] = {"character": params["relative_to"], "distance_m": 3.4,
                                          "height_above_floor_m": 0.5, "bearing": "ahead",
                                          "within_arms_reach": False, "needs_walking": True}
            out.append(item)
        return {"objects": out}

    def locomote(params):
        if moves is not None:
            moves.append(params)
        if params.get("preview"):
            # Where the walk WOULD put her. Answers and moves nothing, which is the whole reason the
            # message exists: the motion that follows the walk is judged at this point before she
            # takes a step towards it.
            #
            # `point:x,z` IS A DESTINATION LIKE ANY OTHER, and the engine resolves it the same way
            # (SceneQueryService.ResolvePoint). It is the one form no model ever writes: the planner
            # computes it, from a transition clip's own travel and the seat's position, so that the
            # clip finishes with the hips on the seat.
            if str(params.get("to") or "").startswith("point:"):
                x, z = [float(v) for v in params["to"][len("point:"):].split(",")]
                return {"preview": True, "reachable": True, "arrived": False,
                        "path_length_m": 1.08, "eta_s": 0.72,
                        "arrival": [x, 0.0, z], "facing_deg": 42.0}
            if params.get("to") not in OBJECTS:
                return {"preview": True, "reachable": False,
                        "why": "nothing called %s" % params.get("to")}
            if ROUTE["already_there"]:
                return {"preview": True, "reachable": True, "arrived": True,
                        "path_length_m": 0.0, "eta_s": 0.0,
                        "arrival": [0.0, 0.0, 0.0], "facing_deg": 42.0}
            return {"preview": True, "reachable": True, "arrived": False,
                    "path_length_m": 1.08, "eta_s": 0.72,
                    "arrival": [0.9, 0.0, -1.7], "facing_deg": 42.0}
        if params.get("face_only"):
            return dict({"arrived": True, "turning": False, "remaining_m": 0.0}, **STANDING)
        if params.get("query"):
            # `playing` is what the real executor reports for the step carrying the most weight. A
            # plan whose opening step is only there to be departed from opens on this instead of on
            # a walk cycle she is not walking.
            return dict({"arrived": True, "turning": False, "remaining_m": 0.0}, **STANDING)
        if str(params.get("to") or "").startswith("point:"):
            return {"going": True, "arrived": False, "path_length_m": 1.08, "eta_s": 0.72,
                    "remaining_m": 1.08}
        if params.get("to") not in OBJECTS:
            raise FakeEngineError(P.E.NOT_FOUND, "nothing called %s" % params.get("to"))
        if ROUTE["already_there"]:
            return {"going": False, "arrived": True, "path_length_m": 0.0, "eta_s": 0.0,
                    "remaining_m": 0.0}
        return {"going": True, "arrived": False, "path_length_m": 1.08, "eta_s": 0.72,
                "remaining_m": 1.08}

    return {P.T.SCENE_FIND: find, P.T.SCENE_DESCRIBE: describe,
            P.T.SCENE_ANCHORS: lambda p: {"anchors": ["Bedside", "MonitorStation"]},
            P.T.SCENE_POSITION: position,
            P.T.MOTION_LOCOMOTE: locomote,
            P.T.MOTION_ASSEMBLE: assemble}


@pytest.fixture(scope="module")
def kb():
    return KBIndex.load()


@pytest.fixture
async def wired(unused_tcp_port, kb):
    submitted = []
    async with EngineLink("127.0.0.1", unused_tcp_port, request_timeout=2.0) as link:
        # The hello names its characters, as the real executor's does. Tools default to the only one.
        async with FakeEngine("ws://127.0.0.1:%d" % unused_tcp_port, handlers(submitted),
                              hello={"scene": "TestScene", "characters": ["chr:CPRNurse"],
                                     "character_names": {"chr:CPRNurse": "Jill"}}):
            await link.wait_ready(timeout=2)
            registry = kb_tools.register(ToolRegistry(), kb)
            scene_tools.register(registry, link, kb)
            yield registry, submitted


async def test_a_character_is_found_by_the_name_a_person_says(wired):
    """The regression this exists for, measured on a live turn: asked to drive Jill, the model
    searched the scene, got back CPRNurse / EKGNurse / AirwayNurse, and replied that there was no
    character called Jill -- about a scene she was standing in. `_who` had always accepted the name;
    the search result was where there was nowhere to learn it.

    The spoken name lives in the executor's handshake, not in the object registry, so it is merged in
    here. All three spellings resolve, which is what `_who` already did with whatever it was given.
    """
    registry, _ = wired
    out = await registry.dispatch("unity_query", {"query": "nurse"})
    hit = [r for r in out["results"] if r["id"] == "chr:CPRNurse"]
    assert hit, "the character has to come back from a search at all"
    assert hit[0]["label"] == "Jill", "the label is the name a person says"
    assert "CPRNurse" in hit[0]["aliases"], "and the scene's own spelling stays reachable"

    listed = await registry.dispatch("unity_query", {"query": ""})
    assert "Jill" in repr(listed), "a bare listing has to name her too"


async def test_alias_bridges_the_motion_library_to_the_scene(wired):
    """The KB says a motion touches `aspirin_bottle`; the scene has an object called `Aspirin Bottle`.
    The alias is what joins them, and it is why a motion can be grounded at all."""
    registry, _ = wired
    out = await registry.dispatch("unity_query", {"query": "aspirin_bottle"})
    assert out["results"][0]["id"] == "obj:AspirinBottle"
    assert "aspirin_bottle" in out["results"][0]["aliases"]


async def test_a_word_for_the_thing_finds_it_by_name(wired):
    registry, _ = wired
    out = await registry.dispatch("unity_query", {"query": "chair"})
    assert [r["id"] for r in out["results"]] == ["obj:Chair"]


async def test_a_word_for_the_thing_finds_it_by_alias(wired):
    """"Stool" is not this object's name; it is one of the names it answers to. The search that only
    matched labels sent an agent looking for furniture five times and it concluded there was no seat."""
    registry, _ = wired
    out = await registry.dispatch("unity_query", {"query": "stool"})
    assert [r["id"] for r in out["results"]] == ["obj:Chair"]


async def test_a_place_is_searched_for_like_a_thing(wired):
    """An anchor IS an entity here, so it comes back from the same call. It used to need a tool of its
    own, which made "walk to the bedside" a two-call question whose first call had no prompt."""
    registry, _ = wired
    out = await registry.dispatch("unity_query", {"query": "bedside"})
    assert [r["id"] for r in out["results"]] == ["anchor:Bedside"]


async def test_a_bare_search_lists_the_whole_room(wired):
    """The cheapest correct answer to "is there a chair". Absence is read off this list rather than
    inferred from repeated misses."""
    registry, _ = wired
    out = await registry.dispatch("unity_query", {"query": ""})
    ids = {r["id"] for r in out["results"]}
    assert {"obj:Chair", "obj:Laptop", "obj:Patient"} <= ids
    assert {"anchor:Bedside", "anchor:MonitorStation"} <= ids
    assert out["count"] == len(ids)
    assert "annotated" in out["note"]


async def test_search_returns_identity_and_nothing_else(wired):
    """The whole point of the collapse. Category, carriability, per-hand anchors, surface heights and
    metres are facts the deterministic backend consumes; handing them to the model only invited it to
    plan around capabilities the executor validates anyway."""
    registry, _ = wired
    out = await registry.dispatch("unity_query", {"query": ""})
    for hit in out["results"]:
        assert set(hit) == {"id", "label", "aliases"}
    text = repr(out).lower()
    for banned in ("category", "carriable", "two_handed", "surface", "position", "distance",
                   "bounds", "reachable"):
        assert banned not in text


async def test_an_unannotated_object_comes_back_without_invented_aliases(wired):
    """It is there and nothing else is known about it. Saying so is what stops a plan being built on
    an affordance nobody authored."""
    registry, _ = wired
    out = await registry.dispatch("unity_query", {"query": "curtain"})
    assert out["results"][0]["id"] == "scene:CurtainRail"
    assert out["results"][0]["aliases"] == []
    assert "raw name" in out["note"]


async def test_nothing_by_that_name_is_said_plainly(wired):
    registry, _ = wired
    out = await registry.dispatch("unity_query", {"query": "defibrillator"})
    assert out["success"] and out["results"] == [] and out["count"] == 0
    assert 'query=""' in out["note"]


async def test_query_answers_the_relation_not_the_geometry(wired):
    registry, _ = wired
    out = await registry.dispatch("unity_query", {"object_ids": ["obj:Chair"],
                                                  "relative_to": "chr:CPRNurse"})
    assert out["success"]
    item = out["objects"][0]
    assert item == {"id": "obj:Chair", "exists": True, "within_arms_reach": False,
                    "needs_walking": True, "held_by": None}
    assert out["relative_to"] == "chr:CPRNurse"


async def test_query_defaults_to_the_only_character(wired):
    """Same reason every other tool resolves a character rather than demanding one: there is no
    ambiguity to protect where there is one person."""
    registry, _ = wired
    out = await registry.dispatch("unity_query", {"object_ids": ["obj:Chair"]})
    assert out["objects"][0]["needs_walking"] is True


async def test_query_carries_no_numbers(wired):
    registry, _ = wired
    out = await registry.dispatch("unity_query", {"object_ids": ["obj:Chair", "obj:Laptop"]})
    text = repr(out).lower()
    for banned in ("position", "distance", "surface", "bearing", "height", "metre", "meter"):
        assert banned not in text


async def test_query_says_which_ids_do_not_exist(wired):
    registry, _ = wired
    out = await registry.dispatch("unity_query", {"object_ids": ["obj:Nope"]})
    assert out["success"]
    assert out["objects"][0] == {"id": "obj:Nope", "exists": False}
    assert "unity_query" in out["note"]


async def test_the_channel_split_the_plan_names_reaches_the_engine(wired):
    """The split is the AGENT's since v4 (ADR 0022), so what this checks is carriage, not derivation:
    the parts the plan named come out the other side masked to exactly those channels, the root goes
    with the legs, and everything nobody asked for is reported free rather than quietly claimed.

    The channel lists are dc-walk-carry's, out of `retrieval_eval_set.json`."""
    registry, submitted = wired
    out = await registry.dispatch("unity_execute", {
        "character": "chr:CPRNurse", "base": C.WALK,
        "base_channels": ["left_leg", "right_leg"],
        "overlays": [{"action_id": C.GRAB, "channels": ["right_arm", "right_hand"]}],
        "ik_bindings": [{"effector": "right_hand", "object_id": "obj:AspirinBottle"}],
        "carry": [{"object_id": "obj:AspirinBottle", "hand": "right_hand"}]})

    assert out["success"]
    # v2 wraps every plan in `steps`; a single action is a one-step sequence, so there is one shape on
    # the wire rather than a flat form for the common case and a nested one for the interesting case.
    assert len(submitted[0]["steps"]) == 1
    layers = {layer["action_id"]: layer for layer in submitted[0]["steps"][0]["layers"]}
    assert sorted(c for c in layers[C.WALK]["channels"] if c != "root") == ["left_leg", "right_leg"]
    assert sorted(layers[C.GRAB]["channels"]) == ["right_arm", "right_hand"]
    assert layers[C.WALK]["owns_root"] is True
    assert sorted(submitted[0]["free_channels"]) == ["head", "left_arm", "left_hand", "torso"]


async def test_clip_ids_reach_the_engine_but_never_the_model(wired):
    """The executor needs a guid; the model has no use for one and pays tokens to see it."""
    registry, submitted = wired
    out = await registry.dispatch("unity_execute", {"character": "c", "base": C.CYCLIC})
    assert submitted[0]["steps"][0]["layers"][0]["clip"]["guid"]
    assert "guid" not in repr(out)


async def test_the_plan_tool_has_nowhere_for_a_number_to_enter(wired):
    """Structural enforcement of "the model emits no motion numerics" — check the schema, not the model."""
    registry, _ = wired
    spec = next(d for d in registry.declarations() if d["name"] == "unity_execute")

    def leaves(node):
        if node.get("type") == "object":
            for child in (node.get("properties") or {}).values():
                yield from leaves(child)
        elif node.get("type") == "array":
            yield from leaves(node.get("items") or {})
        else:
            yield node

    types = {leaf.get("type") for leaf in leaves(spec["parameters"])}
    assert types <= {"string", "boolean"}, types


async def test_a_seat_still_lands_on_a_measured_surface(wired):
    """The height did not stop existing when it stopped being shown. The deterministic side still
    reads it — this is the number the generated descent aims the pelvis at — and the model never sees
    it. That split IS the architecture, and it is what makes removing the numbers from the tool
    surface a simplification rather than a loss."""
    registry, submitted = wired
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "then": [{"base": C.SEATED}], "sit_on": "obj:Chair"})
    assert out["success"]
    generated = [s for s in submitted[-1]["steps"] if s.get("generated")]
    assert generated[0]["generated"]["support_surface_m"] == pytest.approx(0.4054)

    # And it is no longer something the model can ASK for. `unity_execute` still echoes the descent it
    # generated, numbers and all — a separate surface, and out of this change's scope. What is closed
    # here is the route by which a height was fetched and then reasoned with.
    asked = await registry.dispatch("unity_query", {"object_ids": ["obj:Chair"]})
    assert "0.4054" not in repr(asked)
    searched = await registry.dispatch("unity_query", {"query": "chair"})
    assert "0.4054" not in repr(searched)


async def test_move_to_waits_until_she_has_arrived(wired):
    """"She is at the desk" is the precondition the next call depends on. Returning before it is true
    just moves the waiting into the model."""
    registry, _ = wired
    out = await registry.dispatch("unity_locomotion", {"character": "chr:CPRNurse",
                                              "destination": "obj:Chair"})
    assert out["success"] and out["arrived"] is True
    assert out["path_length_m"] == pytest.approx(1.08)


async def test_move_to_reports_a_destination_that_does_not_exist(wired):
    registry, _ = wired
    out = await registry.dispatch("unity_locomotion", {"character": "chr:CPRNurse", "destination": "obj:Nope"})
    assert out["success"] is False


async def test_a_seated_action_with_nothing_to_sit_on_is_refused(wired):
    """The false success this was written for: an agent that could not find the chair planned `typing`
    on its own, the character sat in mid-air, every geometric check passed because nothing had claimed
    a support, and the agent reported success."""
    registry, submitted = wired
    before = len(submitted)
    out = await registry.dispatch("unity_execute", {"character": "chr:CPRNurse", "base": C.SEATED})
    assert out["success"] is False
    assert "nothing was named to sit on" in out["error"]
    assert "unity_query" in out["hint"]
    assert len(submitted) == before, "it should be refused before the engine is asked to play anything"


async def test_a_seated_action_names_its_support_so_the_gate_has_something_to_judge(wired):
    registry, submitted = wired
    out = await registry.dispatch("unity_execute", {"character": "chr:CPRNurse", "base": C.SEATED,
                                                  "sit_on": "obj:Chair"})
    assert out["success"]
    assert submitted[-1]["steps"][0]["expect_support"]["object_id"] == "obj:Chair"


async def test_the_generated_descent_is_reachable_from_the_tool_the_model_is_given(wired):
    """THE PATH THE AGENT HAS TO BE ABLE TO TAKE. Naming both actions in one call with a seat is what
    makes the standing-to-seated frames; the generator was verified by a deterministic probe long
    before any agent reached it, and reaching it is a separate claim from having it."""
    registry, submitted = wired
    out = await registry.dispatch("unity_execute", {
        "character": "chr:CPRNurse", "base": C.WALK, "then": [{"base": C.SEATED}],
        "sit_on": "obj:Chair"})

    assert out["success"], out.get("error")
    generated = out["generated_transitions"]
    assert len(generated) == 1
    assert generated[0]["kind"] == "posture_change"
    assert generated[0]["support_object_id"] == "obj:Chair"
    assert generated[0]["target_hip_height_m"] == pytest.approx(0.4054, abs=0.15)
    # and it reached the engine, which is what actually plays it
    assert any(s.get("generated") for s in submitted[-1]["steps"])


async def test_the_gate_reports_what_the_plan_binds_and_nothing_it_invents(wired):
    """WHERE A GRIP IS NAMED CHANGED, and so did what this gate can say.

    It used to read `channels.*.contact` off the knowledge base -- `typing` recorded both hands on
    `object:keyboard` -- and report a hand the CLIP claimed as already grounded. v4 records do not say
    what a hand holds (ADR 0022), because that is a fact about the scene. So the gate's source is the
    plan, and what it reports is what the plan pinned: nothing, when nothing was pinned.
    """
    registry, _ = wired
    bare = await registry.dispatch("unity_execute", {"base": C.SEATED, "sit_on": "obj:Chair"})
    assert bare["success"]
    gate = next(g for g in bare["gates"] if g["id"] == "contact_bindings")
    assert gate["status"] == "pass"
    assert gate["detail"] == "nothing is bound to the scene"
    assert gate["plan_contacts"] == []

    bound = await registry.dispatch("unity_execute", {
        "base": C.SEATED, "sit_on": "obj:Chair",
        "ik_bindings": [{"effector": "left_hand", "object_id": "obj:Laptop"}]})
    assert bound["success"]
    gate = next(g for g in bound["gates"] if g["id"] == "contact_bindings")
    assert gate["plan_contacts"] == [{"effector": "left_hand", "object_id": "obj:Laptop"}]
    assert "left_hand -> obj:Laptop" in gate["detail"]


async def test_two_hands_land_on_an_object_that_says_where_both_of_them_go(wired):
    """The scene knows this and nothing else does: the laptop carries LaptopHandLeft/Right and their
    elbow hints, the demo path engages them and the hands land 0.000 m from it, and an agent binding
    one hand and leaving the other gets one hand on the keyboard and one hovering a fifth of a metre
    under it.

    v3 read the pair out of the knowledge base -- `typing` declared both hands on `keyboard`, so both
    were bound without the agent saying anything. The record no longer says that (ADR 0022), so the
    agent names ONE binding and the OBJECT supplies the second. The rule is still the object's and
    still not the model's."""
    registry, submitted = wired
    out = await registry.dispatch("unity_execute", {
        "base": C.SEATED, "sit_on": "obj:Chair",
        "ik_bindings": [{"effector": "left_hand", "object_id": "obj:Laptop"}]})
    assert out["success"]
    assert {b["effector"]: b["object_id"] for b in submitted[-1]["ik"]} == {
        "left_hand": "obj:Laptop", "right_hand": "obj:Laptop"}
    assert [g["effector"] for g in out["paired_hands"]] == ["right_hand"]
    assert all("says where both hands go" in g["because"] for g in out["paired_hands"])


async def test_an_object_with_one_grab_point_pairs_nothing(wired):
    """A bottle says where a hand goes only in the sense of being somewhere. Aiming two at it pulls both
    wrists onto the same point, so where the object cannot say, the hand the plan named is the only one
    bound and the clip keeps the other."""
    registry, submitted = wired
    out = await registry.dispatch("unity_execute", {
        "base": C.GRAB,
        "ik_bindings": [{"effector": "right_hand", "object_id": "obj:AspirinBottle"}]})
    assert out["success"]
    assert [b["effector"] for b in submitted[-1]["ik"]] == ["right_hand"]
    assert "paired_hands" not in out


async def test_a_bound_contact_is_measured_as_well_as_bound(wired):
    """Binding and judging are separate. The hands are put on the object's own per-hand anchors, and
    the result is still measured, because reaching the anchors is not the same as the motion looking
    right: `typing` types 0.70 m above the floor and 0.33 m in front of the root, this laptop's deck is
    near 0.90 m, and where an object carries no anchors nothing is bound at all."""
    registry, submitted = wired
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "then": [{"base": C.SEATED}], "sit_on": "obj:Chair",
        "ik_bindings": [{"effector": "left_hand", "object_id": "obj:Laptop"}]})
    assert out["success"]
    expect = {c["effector"]: c for c in submitted[-1]["expect_contact"]}
    assert set(expect) == {"left_hand", "right_hand"}
    assert all(c["object_id"] == "obj:Laptop" for c in expect.values())
    # Due when the posture change FINISHES, not when the step starts. The descent is still running
    # through the opening of the seated step, and the worst contact error on this plan landed mid-way
    # down at 0.12 m with the hands correct before and after.
    typing = next(s for s in out["sequence"] if s["action_id"] == C.SEATED)
    descent = out["generated_transitions"][0]["duration_s"]
    assert expect["left_hand"]["due_at_s"] == pytest.approx(typing["starts_at_s"] + descent)


async def test_a_seat_named_without_its_prefix_still_resolves(wired):
    """Measured, and it cost the whole task: the model wrote `sit_on: "Chair"` where the id is
    `obj:Chair`, got "no object 'Chair' to sit on" about a room with a chair in it, tried again with
    the same spelling, and gave up saying the library could not do it."""
    registry, submitted = wired
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "then": [{"base": C.SEATED}], "sit_on": "Chair"})
    assert out["success"], out.get("error")
    assert out["generated_transitions"][0]["support_object_id"] == "obj:Chair"


async def test_an_ambiguous_name_is_still_refused(wired):
    """Two matches is a real question, and answering it by taking the first would be a guess."""
    registry, _ = wired
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "then": [{"base": C.SEATED}], "sit_on": "a"})
    assert out["success"] is False
    assert "to sit on" in out["error"]


async def test_walking_to_somewhere_plays_the_walk(wired):
    """Displacement and animation are separate mechanisms here, and running only the first slid the
    character across the room in whatever pose she was in. Nobody asked for a slide; the walk cycle
    had no one to start it."""
    registry, submitted = wired
    out = await registry.dispatch("unity_locomotion", {"character": "chr:CPRNurse", "destination": "obj:Chair"})
    assert out["success"] and out["arrived"] is True
    played = [s["steps"][0]["action_id"] for s in committed(submitted)]
    assert played == [C.WALK, C.IDLE], "walk while going, stop walking once there"
    # And each of them was run on the hidden copy first: two plays, two checks, in that order.
    assert [s["steps"][0]["action_id"] for s in checked(submitted)] == [C.WALK, C.IDLE]
    assert [s["mode"] for s in submitted] == ["validate", "commit", "validate", "commit"]


async def test_every_step_tells_the_engine_what_posture_it_is_in(wired):
    """The engine has no knowledge base, so the posture has to travel with the step. It is what lets
    the executor know she ends up seated -- and therefore keep the generated pose across the next graph
    rebuild, and refuse to walk her off the chair."""
    registry, submitted = wired
    await registry.dispatch("unity_execute", {
        "character": "chr:CPRNurse", "base": C.WALK, "then": [{"base": C.SEATED}],
        "sit_on": "obj:Chair"})
    assert [s["posture"] for s in submitted[-1]["steps"]] == ["standing", "seated"]


async def test_a_committed_generated_plan_schedules_its_own_verification(wired):
    """The landing is not measurable when the plan returns -- the descent has not run yet -- and waiting
    for it would put the length of the animation inside the answer. So the check is scheduled, and the
    result says plainly that nothing is known yet."""
    registry, _ = wired
    out = await registry.dispatch("unity_execute", {
        "character": "chr:CPRNurse", "base": C.WALK, "then": [{"base": C.SEATED}],
        "sit_on": "obj:Chair", "mode": "commit"})

    assert out["success"]
    verify = out["verify"]
    assert verify["status"] == "scheduled"
    assert verify["tool"] == "unity_measure"
    assert verify["arguments"] == {"character": "chr:CPRNurse"}
    assert "obj:Chair" in verify["confirms"]
    assert "Say she is sitting down" in verify["note"]


async def test_validating_promises_nothing_and_plays_nothing(wired):
    """Nothing is playing, so there is nothing to measure and nothing to promise."""
    registry, submitted = wired
    out = await registry.dispatch("unity_validate", {
        "character": "chr:CPRNurse", "base": C.WALK, "then": [{"base": C.SEATED}],
        "sit_on": "obj:Chair"})
    assert out["success"] and out["committed"] is False
    assert "verify" not in out
    assert not committed(submitted), "a validation must not commit anything"
    assert checked(submitted), "and it must still run the same check"


async def test_the_two_plan_tools_derive_the_same_plan(wired):
    """The difference is where the function stops, and nowhere else. Two tools whose plans could
    differ would make a passed validation evidence about something other than what will play."""
    registry, submitted = wired
    args = {"base": C.WALK, "base_channels": LEGS, "overlays": [CARRY]}
    await registry.dispatch("unity_validate", dict(args))
    validated = checked(submitted)[-1]
    submitted.clear()
    await registry.dispatch("unity_execute", dict(args))
    assert same_plan(validated, committed(submitted)[-1])


async def test_there_is_no_mode_for_the_model_to_get_wrong(wired):
    """`mode` was a parameter with a default, and both mistakes were measured: one turn spent an
    iteration on a dry run and another on the identical commit, another invented `commit: true` and
    lost a third to the error. Two tools cannot be confused for one flag."""
    registry, submitted = wired
    for name in ("unity_execute", "unity_validate"):
        schema = next(d for d in registry.declarations() if d["name"] == name)
        assert "mode" not in schema["parameters"]["properties"]
    await registry.dispatch("unity_execute", {"base": C.CYCLIC})
    assert submitted[-1]["mode"] == "commit"


async def test_the_only_character_does_not_have_to_be_named(wired):
    """Measured: `unity_locomotion` with character "nurse" against a scene whose only character is
    "chr:CPRNurse". The id bought nothing and cost a round trip whenever the model wrote something
    reasonable-looking instead."""
    registry, submitted = wired
    out = await registry.dispatch("unity_locomotion", {"destination": "obj:Chair"})
    assert out["success"]
    assert submitted[-1]["character"] == "chr:CPRNurse"


async def test_a_lone_seated_action_says_it_is_a_cut(wired):
    """The gates measure where she ends up, and she ends up correctly on the seat — nothing in them can
    see that she got there in one frame. The result has to say so itself."""
    registry, _ = wired
    out = await registry.dispatch("unity_execute", {"character": "chr:CPRNurse", "base": C.SEATED,
                                                  "sit_on": "obj:Chair"})
    assert out["success"]
    cut = next(g for g in out["gates"] if g["id"] == "transition_present")
    assert cut["status"] == "warn"
    assert "then" in cut["hint"]
    assert "generated_transitions" not in out, "nothing was generated, so nothing may claim it was"


async def test_a_sequence_without_a_posture_change_generates_nothing(wired):
    registry, _ = wired
    out = await registry.dispatch("unity_execute", {"character": "chr:CPRNurse", "base": C.GIVING,
                                                  "then": [{"base": C.WORK}]})
    assert out["success"]
    assert "generated_transitions" not in out
    assert [s["action_id"] for s in out["sequence"]] == [C.GIVING, C.WORK]


async def test_a_standing_action_needs_no_seat(wired):
    registry, _ = wired
    out = await registry.dispatch("unity_execute", {"character": "chr:CPRNurse", "base": C.CYCLIC})
    assert out["success"]


async def test_two_hands_aimed_at_one_object_are_warned_about_not_refused(wired):
    """The shape that once pulled both of typing's wrists onto a single grab point -- measured, right
    hand 0.000 m and left 0.065 m, which is clasping rather than typing. A warn rather than a refusal
    because an object CAN say where both hands go, and when it does that is exactly what should
    happen; the object is asked, in `_pair_bound_hands`."""
    registry, submitted = wired
    out = await registry.dispatch("unity_execute", {
        "base": C.GRAB,
        "ik_bindings": [{"effector": "left_hand", "object_id": "obj:AspirinBottle"},
                        {"effector": "right_hand", "object_id": "obj:AspirinBottle"}]})
    assert out["success"], out.get("error")
    gate = next(g for g in out["gates"] if g["id"] == "contact_bindings")
    assert gate["status"] == "warn"
    assert "Bind one hand" in gate["hint"]
    assert submitted           # and it really was sent, rather than refused quietly


async def test_mixing_postures_fails_the_cheap_gate_before_any_round_trip(wired):
    registry, submitted = wired
    out = await registry.dispatch("unity_execute", {
        "character": "c", "base": C.SEATED,
        "overlays": [{"action_id": C.GRAB, "channels": ["right_arm", "right_hand"]}]})
    assert out["success"] is False
    assert "seated" in out["error"] and "standing" in out["error"]
    assert not submitted


async def test_scene_tools_degrade_when_the_engine_is_absent(kb, unused_tcp_port):
    """The demo must still answer from the motion library when Unity is not running."""
    async with EngineLink("127.0.0.1", unused_tcp_port, request_timeout=0.5) as link:
        registry = scene_tools.register(kb_tools.register(ToolRegistry(), kb), link, kb)
        out = await registry.dispatch("unity_query", {"query": "monitor"})
        assert out["success"] is False
        assert "not connected" in out["error"]
        assert "motion library" in out["hint"]


# ---- there is no longer a clause to fill in wrongly ---------------------------------------------

async def test_there_is_nothing_left_to_guess_at(wired):
    """The defect that hid a chair in plain sight, closed structurally rather than defended against.

    A model fills in every field a schema offers, so `reachable_by: {"character": ""}` used to arrive
    on a search that never meant to constrain anything. Engine-side a blank character resolved to the
    driven character — right for a tool that needs an actor, catastrophic as a filter: it turned an
    unconstrained search into "within arm's reach right now", and the chair was across the room. Ten
    calls in one turn, every one empty, and the agent concluded the room had no chair. The fix was a
    guard; this is the same defect with the field removed instead.
    """
    from agent.tools.scene import UNITY_QUERY_PARAMS

    assert set(UNITY_QUERY_PARAMS["properties"]) == {"query", "limit", "object_ids", "relative_to"}
    assert UNITY_QUERY_PARAMS["additionalProperties"] is False

    registry, _ = wired
    # A word that means nothing here is a miss, not a filter that silently empties the room.
    out = await registry.dispatch("unity_query", {"query": "seating"})
    assert out["success"] and out["count"] == 0
    assert 'query=""' in out["note"]


async def test_a_surface_she_would_end_up_under_is_refused_as_a_seat(wired):
    """Measured on a real turn: the model passed the laptop as `sit_on`. It has a surface, so nothing
    objected; the descent ran to the hip height `typing` opens on and left the pelvis 0.70 m beneath a
    deck it was reported to be sitting on -- inside the footprint, so the gate's containment check
    passed as well. Both numbers exist before anything plays."""
    registry, _ = wired
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "then": [{"base": C.SEATED, "sit_on": "obj:Laptop"}]})

    assert out["success"] is False
    assert "underneath it" in out["error"]
    assert "unity_query" in out["hint"]


async def test_a_real_seat_is_still_a_seat(wired):
    registry, _ = wired
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "then": [{"base": C.SEATED, "sit_on": "obj:Chair"}]})

    assert out["success"] is True
    assert out["generated_transitions"][0]["support_object_id"] == "obj:Chair"


async def test_every_object_the_model_names_is_looked_up_not_just_the_seat(wired):
    """Measured: a plan bound both hands to `Laptop` and named `obj:Chair` as the seat. The seat
    resolved and the bindings did not, so the clip played unbound and the gate reported hands 0.19 m
    from the keyboard. Which fields carry a prefix is not something a model gets right one at a time."""
    registry, submitted = wired
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "then": [{"base": C.SEATED}], "sit_on": "Chair",
        "ik_bindings": [{"effector": "left_hand", "object_id": "Laptop"},
                        {"effector": "right_hand", "object_id": "Laptop"}],
        "gaze_at": "Laptop"})

    assert out["success"] is True
    sent = submitted[-1]
    assert {b["object_id"] for b in sent["ik"]} == {"obj:Laptop"}
    assert sent["gaze_at"] == "obj:Laptop"


async def test_a_carried_object_is_looked_up_too(wired):
    registry, submitted = wired
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "overlays": [CARRY],
        "carry": [{"object_id": "Aspirin Bottle", "hand": "right_hand"}]})

    assert out["success"] is True
    assert submitted[-1]["carry"][0]["object_id"] == "obj:AspirinBottle"


async def test_a_walk_that_only_opens_a_sit_becomes_what_she_is_already_doing(wired):
    """Measured on a real turn: unity_locomotion walked her to the workstation and left her idle, then the
    committed plan opened on `walking` again -- so she marched on the spot in front of the desk for a
    whole loop cycle before sitting down. The opening step of a posture change exists to be departed
    FROM; `idle` serves as well and is what she is actually doing."""
    registry, submitted = wired
    STANDING["playing"] = C.IDLE
    # SHE IS ALREADY THERE, which is the situation the docstring describes and which the stand-in used
    # to model by accident. `sit_on` names somewhere to walk, so the plan previews a route; a route of
    # zero length plays no walk, and the opener therefore has to come from what she is doing rather
    # than from the walk that is not going to happen.
    ROUTE["already_there"] = True
    try:
        out = await registry.dispatch("unity_execute", {
            "base": C.WALK, "then": [{"base": C.SEATED}], "sit_on": "obj:Chair"})
    finally:
        STANDING["playing"] = None
        ROUTE["already_there"] = False

    assert out["success"] is True
    assert out["opened_on"] == {"asked_for": C.WALK, "played": C.IDLE,
                                "why": "she was not walking anywhere; %s is what she was already "
                                       "doing" % C.IDLE}
    assert submitted[-1]["steps"][0]["action_id"] == C.IDLE
    assert [g["id"] for g in out["gates"] if g["id"] == "opening_step"], \
        "the substitution has to be visible where the model reads structural facts"


async def test_a_bare_walk_while_she_is_stationary_is_refused(wired):
    """Measured on a real turn: unity_locomotion walked her to the patient, and the model then committed
    `walking` on its own -- so she arrived and kept striding on the spot indefinitely, while reporting
    that she had walked there. That report was true and was not what the scene showed."""
    registry, _ = wired
    out = await registry.dispatch("unity_execute", {"base": C.WALK})

    assert out["success"] is False
    assert "march her on the spot" in out["error"]
    assert "unity_locomotion" in out["hint"]


async def test_a_walk_with_an_overlay_is_still_planned(wired):
    """Only the BARE case is refused. `walking` under an overlay is a composed motion whose base
    carries the posture -- walking while grabbing a bottle -- and taking that away would remove a
    capability over a plan the model may yet follow with a unity_locomotion."""
    registry, submitted = wired
    out = await registry.dispatch("unity_execute", {"base": C.WALK, "overlays": [CARRY]})

    assert out["success"] is True
    assert submitted[-1]["steps"][0]["action_id"] == C.WALK


async def test_a_walk_she_is_actually_walking_is_planned(wired):
    """And so is one committed while the navigation agent is genuinely under way."""
    registry, submitted = wired
    STANDING["going"] = True
    try:
        out = await registry.dispatch("unity_execute", {"base": C.WALK})
    finally:
        STANDING["going"] = False

    assert out["success"] is True
    assert submitted[-1]["steps"][0]["action_id"] == C.WALK


async def test_a_walk_stays_a_walk_while_she_really_is_walking(wired):
    registry, submitted = wired
    try:
        # unity_locomotion with then_wait off leaves her under way, so the plan committed now genuinely does
        # open on a walk and replacing it would be the wrong correction.
        await registry.dispatch("unity_locomotion", {"destination": "obj:Chair", "then_wait": False})
        STANDING["playing"] = C.WALK
        STANDING["going"] = True
        out = await registry.dispatch("unity_execute", {
            "base": C.WALK, "then": [{"base": C.SEATED}], "sit_on": "obj:Chair"})
    finally:
        STANDING["playing"] = None
        STANDING["going"] = False

    assert out["success"] is True
    assert "opened_on" not in out
    assert submitted[-1]["steps"][0]["action_id"] == C.WALK


async def test_a_hand_binding_waits_for_the_step_that_reaches(wired):
    """A binding must not engage during the walk that gets her there: measured, the walk played with
    her arms stretched back toward the desk, and no geometric check saw it, because every one of them
    is about where she ends UP.

    WHICH step it belongs to used to be read off the knowledge base -- `typing` recorded both hands
    on a keyboard and `walking` recorded them free, so the step that touched something identified
    itself. v4 records say no such thing (ADR 0022), so the plan is asked instead: a step whose
    layers explicitly drive this hand, and failing that the LAST step, because a plan with several
    steps that pins a hand is pinning it to what it walks towards."""
    registry, submitted = wired
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "then": [{"base": C.SEATED}], "sit_on": "obj:Chair",
        "ik_bindings": [{"effector": "left_hand", "object_id": "obj:Laptop"}]})

    assert out["success"] is True
    sent = submitted[-1]
    assert len(sent["ik"]) == 2, "the laptop says where both hands go, so both are bound"
    for binding in sent["ik"]:
        assert binding["at_s"] > 0.0, "a binding for the typing step cannot start during the walk"

    # And it is the same moment the gate waits for, not a second one computed alongside it.
    due = {c["effector"]: c["due_at_s"] for c in sent["expect_contact"]}
    for binding in sent["ik"]:
        assert binding["at_s"] == pytest.approx(due[binding["effector"]])


async def test_a_one_step_plan_binds_from_the_first_frame(wired):
    """Nothing precedes the reach, so there is nothing to wait for. This is the shape every binding
    had before they were timed, and it has to be unchanged."""
    registry, submitted = wired
    out = await registry.dispatch("unity_execute", {
        "base": C.GRAB, "ik_bindings": [{"effector": "right_hand",
                                                "object_id": "obj:AspirinBottle"}]})

    assert out["success"] is True
    assert submitted[-1]["ik"][0]["at_s"] == 0.0
    assert submitted[-1]["gaze_at_s"] == 0.0


@pytest.fixture
async def walked(unused_tcp_port, kb):
    """Same wiring, plus every locomote request, so a derived facing can be asserted rather than
    inferred from the character having ended up somewhere."""
    submitted, moves = [], []
    async with EngineLink("127.0.0.1", unused_tcp_port, request_timeout=2.0) as link:
        async with FakeEngine("ws://127.0.0.1:%d" % unused_tcp_port, handlers(submitted, moves),
                              hello={"scene": "TestScene", "characters": ["chr:CPRNurse"]}):
            await link.wait_ready(timeout=2)
            registry = kb_tools.register(ToolRegistry(), kb)
            scene_tools.register(registry, link, kb)
            yield registry, submitted, moves


async def test_walking_to_the_seat_leaves_no_standstill_in_between(walked):
    """The walk and the sit used to be two tool calls with a model round trip between them -- measured
    at 0.85 s -- and unity_locomotion ended by parking her in idle, so the plan that followed departed from a
    standstill: walk, stop, stand, sit. One call keeps the walk under her until the descent commits."""
    registry, submitted, _ = walked
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "walk_to": "obj:Chair", "then": [{"base": C.SEATED}],
        "sit_on": "obj:Chair"})

    assert out["success"] is True
    played = [s["steps"][0]["action_id"] for s in committed(submitted)]
    assert C.IDLE not in played, "she must not be parked in idle between arriving and sitting"
    assert played == [C.WALK, C.WALK], "the travel clip, then the plan that opens on it"
    assert [s["action_id"] for s in submitted[-1]["steps"]] == [C.WALK, C.SEATED]
    assert out["walked"]["path_length_m"] == 1.08 and out["walked"]["arrived"] is True


async def test_the_facing_comes_from_what_the_plan_aims_at(walked):
    """A character faces the thing the motion she is about to perform interacts with, and the seat
    does not get a vote -- which is how she came to sit with her back to the desk when the facing was
    taken from the chair. THE RULE IS UNCHANGED; where it reads the answer is not.

    v3 read it off the knowledge base: `typing` spelled both hands `contact: object:keyboard` and the
    registry's alias list joined `keyboard` to `obj:Laptop`. A v4 record says how a hand moves, not
    what it is on (ADR 0022), so the answer comes from the plan -- which names the object once, and
    names it as a scene id, so there is no alias round trip and no ambiguity to refuse."""
    registry, _, moves = walked
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "walk_to": "obj:Chair", "then": [{"base": C.SEATED}],
        "sit_on": "obj:Chair",
        "ik_bindings": [{"effector": "left_hand", "object_id": "obj:Laptop"}]})

    assert out["success"] is True
    assert out["walked"]["facing"] == "obj:Laptop"
    assert out["walked"]["facing_from"] == "left_hand is bound to it"
    assert [m["face_only"] for m in moves if m.get("face_only")] == ["obj:Laptop"]


async def test_a_gaze_decides_the_facing_only_when_nothing_is_held(walked):
    """Hands before gaze, in a fixed order, so the same plan resolves the same way twice. Looking at
    a monitor while working on a patient should not turn the body away from the patient."""
    registry, _, moves = walked
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "walk_to": "obj:Chair", "then": [{"base": C.SEATED}],
        "sit_on": "obj:Chair", "gaze_at": "obj:MonitorVitals"})

    assert out["success"] is True
    assert out["walked"]["facing"] == "obj:MonitorVitals"
    assert out["walked"]["facing_from"] == "she is looking at it"


async def test_a_plan_that_aims_at_nothing_leaves_the_facing_to_the_route(walked):
    """Also correct: there is nothing it is aimed at. `walking` and `idle` on their own take this
    branch, where v3 would have gone looking through the KB's contacts for a target."""
    registry, _, moves = walked
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "overlays": [CARRY], "walk_to": "obj:Patient"})

    assert out["success"] is True
    assert "facing" not in out["walked"]
    assert not [m for m in moves if m.get("face_only")]


async def test_an_overlay_plays_on_top_of_the_walk_that_gets_her_there(walked):
    """"Walk over holding the bottle out" is the one shape of composition this corpus can express, and
    it used to wait for her to arrive before playing anything but the walk. She is not doing it WHILE
    walking if it starts when the walking stops.

    The first thing sent to the engine -- before a single poll of the journey -- has to carry both.
    """
    registry, submitted, _ = walked
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "overlays": [CARRY], "walk_to": "obj:Patient"})

    assert out["success"] is True
    departure = submitted[0]["steps"][0]
    assert departure["action_id"] == C.WALK
    assert [l["action_id"] for l in departure["layers"]] == [C.WALK, C.GRAB]
    assert out["walked"]["while_walking"] == [CARRY]


async def test_the_overlay_carries_on_after_the_walk_ends(walked):
    """The walk is over; the overlay is not. Committing `walking` again would stride her on the spot,
    so what is left is the same overlay over a stance. What must not happen is the reach ending
    because the crossing did."""
    registry, submitted, _ = walked
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "overlays": [CARRY], "walk_to": "obj:Patient"})

    assert out["success"] is True
    # `idle` STAYS UNDERNEATH now, where v3 promoted it away. It claims no channel -- the plan gave
    # it none -- so it drives nothing the overlay wanted; it plays layer 0 full-body and holds up the
    # rest of her while the reach carries on over the top. The arm is unchanged either way; what
    # changed is that a base claiming nothing is no longer a special case to be probed for.
    layers = {l["action_id"]: l for l in submitted[-1]["steps"][0]["layers"]}
    assert set(layers) == {C.IDLE, C.GRAB}
    assert layers[C.IDLE]["channels"] == ["root"] and layers[C.IDLE]["source"] == "base"
    assert layers[C.GRAB]["channels"] == ["right_arm", "right_hand"]
    assert out["played_while_walking"]["overlays"] == [CARRY]


async def test_a_walk_to_nowhere_plays_nothing(walked):
    """A destination that cannot be reached is a refusal, not a motion committed where she stands."""
    registry, submitted, _ = walked
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "walk_to": "obj:Nowhere", "then": [{"base": C.SEATED}],
        "sit_on": "obj:Chair"})

    assert out["success"] is False
    assert not submitted, "nothing should have been played"


async def test_the_walk_enters_on_the_frame_the_seam_picked(walked, kb):
    """A commit cannot crossfade -- the composer hard-sets the opening step to full weight and the
    outgoing graph is gone -- so where the clip STARTS is the only lever. The seam search already
    answers it, and every step but the opening one already enters that way."""
    registry, submitted, _ = walked
    STANDING["playing"] = C.IDLE
    try:
        out = await registry.dispatch("unity_locomotion", {"destination": "obj:Chair"})
    finally:
        STANDING["playing"] = None

    assert out["success"] is True
    travel = submitted[0]["steps"][0]
    assert travel["action_id"] == C.WALK
    seam = T.find_seam(C.IDLE, C.WALK, kb, {a: T.load_clip(a) for a in (C.IDLE, C.WALK)})
    assert travel["clip_start_frame"] == seam.to_frame
    assert travel["clip_start_frame"] > 0, "the seam picked a frame, not the start of the clip"


async def test_naming_a_seat_is_naming_somewhere_to_walk(walked):
    """Sitting on something means being at it. Left to the model this went wrong both ways on real
    turns -- one committed the sit while she was still crossing the room, the rest walked with unity_locomotion
    and sat from the standstill it leaves her in."""
    registry, submitted, moves = walked
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "then": [{"base": C.SEATED}], "sit_on": "obj:Chair"})

    assert out["success"] is True
    assert out["walked"]["destination"] == "obj:Chair", "no walk_to was passed; the seat is the place"
    assert [s["steps"][0]["action_id"] for s in committed(submitted)] == [C.WALK, C.WALK]
    # Twice, and they have to agree: the route is previewed at the stopping distance the walk will
    # then use, or the hidden copy is checked somewhere the real one never stands.
    assert [m["stop_within_m"] for m in moves if "stop_within_m" in m] == [0.08, 0.08], \
        "right at the seat, previewed and then walked"


async def test_an_action_cannot_fight_itself(wired):
    """Measured on a live turn: the model sent `typing` twice in one call and got back "these actions
    fight over the same body parts: left_arm (typing and typing)" -- true, about nothing."""
    registry, _ = wired
    out = await registry.dispatch("unity_execute", {
        "base": C.IDLE,
        "overlays": [{"action_id": C.CYCLIC, "channels": ["torso", "left_arm"]},
                     {"action_id": C.CYCLIC, "channels": ["right_arm"]}]})
    assert out["success"] is True, out.get("error")
    # The repeat is merged, not layered twice: the second entry says nothing the first did not, so
    # what comes out is one cpr driving the union of the two channel lists. Over an idle that claims
    # nothing, that is cpr itself rather than a composition of cpr with cpr.
    assert out["retrieval"] == {"type": "full_match", "action_id": C.CYCLIC}
    assert sorted(out["derived"]["layers"][-1]["channels"]) == ["left_arm", "right_arm", "torso"]
    driving = [layer["action_id"] for layer in out["derived"]["layers"]]
    assert driving.count(C.CYCLIC) == 1, "the repeat asked for the same layer twice"


async def test_a_plan_records_which_branch_the_library_answered_on(wired):
    """A clip that covered the whole request and a motion composed out of several are different
    claims about the system. Only the eval could tell them apart before: a live turn that assembled a
    motion existing in no clip left nothing behind saying it had."""
    registry, _ = wired

    whole = await registry.dispatch("unity_execute", {"base": C.CYCLIC})
    assert whole["retrieval"] == {"type": "full_match", "action_id": C.CYCLIC}

    composed = await registry.dispatch("unity_execute", {
        "base": C.WALK, "base_channels": LEGS,
        "overlays": [{"action_id": C.WORK,
                      "channels": ["torso", "left_arm", "right_arm", "left_hand", "right_hand"]}]})
    assert composed["retrieval"]["type"] == "decompose"
    by_action = {p["action_id"]: set(p["channels"]) for p in composed["retrieval"]["parts"]}
    assert set(by_action) == {C.WALK, C.WORK}
    assert "root" in by_action[C.WALK], "the stepping belongs to the walk"
    assert {"left_arm", "right_arm"} <= by_action[C.WORK]


async def test_the_verdict_is_the_one_the_eval_scores(wired, kb):
    """One function, called from both places. Two would be two definitions of what decomposing means,
    and the score would stop being a statement about what the live path does."""
    from agent import assemble as A

    registry, _ = wired
    overlays = [{"action_id": C.WORK, "channels": ["torso", "left_arm", "right_arm"]}]
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "base_channels": LEGS, "overlays": overlays})
    assert out["retrieval"] == A.verdict(
        A.arbitrate(C.WALK, overlays, kb, base_channels=LEGS))


# ---- a channel with two sources, all the way to the wire ----------------------------------------
#
# A MIX IS NOW SOMETHING THE PLAN ASKS FOR. Under v3 it fell out of the role table -- walking took the
# legs as `primary` and giving_pills braced them as `support`, and the two labels met on one channel.
# v4 has no labels to meet (ADR 0022), so a contested channel is one the plan named twice: the base
# reserved it with `base_channels` and an overlay asked for it anyway. The engine-side half of the
# mechanism -- its own layer, its own weight, its own aligned entry frame -- is unchanged, and that is
# what these are about.

MIXED_LEGS = {"base": C.WORK, "base_channels": LEGS,
              "overlays": [{"action_id": C.WALK, "channels": LEGS}],
              "mode": "commit"}


async def test_a_mix_reaches_the_engine_as_its_own_layer(wired):
    """The end of the chain. Both parts were asked for on the legs, so what leaves here is a layer
    masked to those legs at half weight -- not a leg one of them simply won."""
    registry, submitted = wired
    await registry.dispatch("unity_execute", dict(MIXED_LEGS))
    layers = submitted[-1]["steps"][0]["layers"]
    mixed = [l for l in layers if l.get("source") == "mix"]
    assert len(mixed) == 1
    assert mixed[0]["action_id"] == C.WALK
    assert sorted(mixed[0]["channels"]) == ["left_leg", "right_leg"]
    # 0.5, not the 0.6 the normalised role table used to give. There is nothing left to rank the two
    # claims by, and a weight invented here would be the plan emitting a motion numeric by proxy.
    assert mixed[0]["weight"] == pytest.approx(0.5)


async def test_the_owner_of_a_mixed_channel_does_not_also_mask_it(wired):
    """A layer masked to a channel at full weight IS winner-take-all. The mix's owner holds the legs
    in the ownership partition and must not carry them into its own mask, or the mix never happens."""
    registry, submitted = wired
    await registry.dispatch("unity_execute", dict(MIXED_LEGS))
    layers = submitted[-1]["steps"][0]["layers"]
    assert not [l for l in layers if l.get("source") == "overlay" and l["action_id"] == C.WALK], \
        "walking drives the legs only through the mix, so it may not also be masked to them"
    assert [l["action_id"] for l in layers if l.get("source") == "mix"] == [C.WALK]
    assert all(l.get("weight", 1.0) == 1.0 for l in layers if l.get("source") != "mix")


async def test_a_mixed_layer_enters_on_its_aligned_frame(wired):
    """Averaging two poses half a stride apart puts the legs where neither clip put them, so the
    overlay enters where the channels already agree rather than at frame 0."""
    registry, submitted = wired
    await registry.dispatch("unity_execute", dict(MIXED_LEGS))
    mixed = [l for l in submitted[-1]["steps"][0]["layers"] if l.get("source") == "mix"][0]
    assert mixed["clip_start_frame"] > 0
    # Reported so a mix that had to average two distant poses is visible as such.
    assert mixed["entry_apart_deg"] is not None


async def test_both_mixed_channels_of_one_clip_share_a_phase(wired):
    """Two legs at two phases is two legs stepping independently. Asked separately they want frames
    11 and 1; a clip is one performance, so they get one frame between them."""
    registry, submitted = wired
    await registry.dispatch("unity_execute", dict(MIXED_LEGS))
    mixed = [l for l in submitted[-1]["steps"][0]["layers"] if l.get("source") == "mix"]
    assert len({l["clip_start_frame"] for l in mixed}) == 1


async def test_a_plan_with_no_contested_channel_carries_no_weights(wired):
    """dc-walk-carry's shape. Nothing here is contested, so nothing may acquire a weight or a mix --
    this is the guard that mixing did not change every plan that came before it."""
    registry, submitted = wired
    await registry.dispatch("unity_execute", {"base": C.WALK, "base_channels": LEGS,
                                            "overlays": [CARRY], "mode": "commit"})
    layers = submitted[-1]["steps"][0]["layers"]
    assert not [l for l in layers if l.get("source") == "mix"]
    assert all("weight" not in l for l in layers)


async def test_the_base_is_never_cut_and_an_overlay_is(wired, kb):
    """Same plan, the other half of what it now carries. The overlay opens and closes with frames its
    arm is not moving in and contributes only the middle, while the base -- which sets the posture
    everything else hangs on -- keeps every frame it has.

    A base trimmed to the frames its legs happen to be moving in is a posture that stops halfway
    through the plan."""
    registry, submitted = wired
    overlay = {"action_id": C.WORK, "channels": ["right_arm", "right_hand"]}
    await registry.dispatch("unity_execute", {"base": C.WALK, "base_channels": LEGS,
                                              "overlays": [overlay]})
    layers = {l["action_id"]: l for l in submitted[-1]["steps"][0]["layers"]}
    assert "clip_end_frame" not in layers[C.WALK]

    window = S.window_for(S.read_table()[C.WORK], ["right_arm", "right_hand"])
    assert layers[C.WORK]["clip_start_frame"] == window["start_frame"] > 0
    assert layers[C.WORK]["clip_end_frame"] == window["end_frame"] < kb.record(
        C.WORK)["extraction"]["sampled_frames"]
    # Not a repetition, so reaching the end holds the final pose rather than snapping back.
    assert layers[C.WORK]["loop_in_window"] is False


async def test_a_repeating_overlay_contributes_one_repetition(wired):
    """The overlay's legs repeat every 22 frames over a clip of 110, and the measurement is what
    picks the 22 — an overlay grafted onto something else contributes one repetition rather than five.

    WHETHER IT THEN LOOPS IS A SEPARATE QUESTION, and `temporal_intent` is where it is answered. The
    window is one repetition either way; whether the base outlives it is a fact about the task, which
    is why the default plays it once and `repeat` is a thing the agent says. See the test below."""
    registry, submitted = wired
    await registry.dispatch("unity_execute", {
        "base": C.WORK, "base_channels": ["right_arm", "right_hand"],
        "overlays": [{"action_id": C.CYCLIC, "channels": LEGS}]})
    layers = {l["action_id"]: l for l in submitted[-1]["steps"][0]["layers"]}
    repeating = layers[C.CYCLIC]
    assert (repeating["clip_start_frame"], repeating["clip_end_frame"]) == (0, 22)
    assert "repetition" in repeating["window_why"]


async def test_temporal_intent_decides_whether_that_repetition_loops(wired):
    """The measurement says WHICH frames; `temporal_intent` says whether they repeat, which is a fact
    about the task rather than about the clip. It is the one bit the agent supplies here."""
    registry, submitted = wired
    for intent, loops in (("once", False), ("repeat", True)):
        submitted.clear()
        await registry.dispatch("unity_execute", {
            "base": C.WORK, "base_channels": ["right_arm", "right_hand"],
            "overlays": [{"action_id": C.CYCLIC, "channels": LEGS,
                          "temporal_intent": intent}]})
        layer = {l["action_id"]: l for l in submitted[-1]["steps"][0]["layers"]}[C.CYCLIC]
        assert layer["temporal_intent"] == intent
        assert layer["loop_in_window"] is loops


async def test_a_continuous_overlay_keeps_the_whole_clip(wired):
    """The window is dropped rather than narrowed: an overlay that IS the point of the motion, rather
    than a gesture inside it, plays for as long as it has."""
    registry, submitted = wired
    await registry.dispatch("unity_execute", {
        "base": C.WORK, "base_channels": ["right_arm", "right_hand"],
        "overlays": [{"action_id": C.CYCLIC, "channels": LEGS,
                      "temporal_intent": "continuous"}]})
    layer = {l["action_id"]: l for l in submitted[-1]["steps"][0]["layers"]}[C.CYCLIC]
    assert "clip_end_frame" not in layer and "clip_start_frame" not in layer


async def test_only_a_via_that_travels_drives_the_transform(wired):
    """`apply_root_motion` is protocol v5, and it is the narrowest field on the wire: the composer
    discards root motion for everything else, which is what keeps a walk cycle from covering the
    ground twice under a NavMeshAgent that is already moving her.

    A BRIDGE THE AGENT CHOSE IS THE EXCEPTION. A sit-down clip travels 0.446 m backwards onto the
    seat; discarding that leaves the feet sliding through a step they are visibly taking and the hips
    finishing in front of the chair. So the flag goes on the `via` step and on nothing else -- not on
    the base, not on the steps the agent named directly, and not on a via that stays put."""
    registry, submitted = wired
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "sit_on": "obj:Chair",
        "then": [{"via": [C.SIT_DOWN], "base": C.SEATED}]})
    assert out["success"] is True, out

    steps = committed(submitted)[-1]["steps"]
    by_action = {s["action_id"]: s for s in steps}
    assert list(by_action) == [C.WALK, C.SIT_DOWN, C.SEATED], "the via plays as a step of its own"

    assert by_action[C.SIT_DOWN].get("apply_root_motion") is True
    for layer in by_action[C.SIT_DOWN]["layers"]:
        assert layer.get("apply_root_motion") is True

    for action_id in (C.WALK, C.SEATED):
        assert not by_action[action_id].get("apply_root_motion"), action_id
        for layer in by_action[action_id]["layers"]:
            assert not layer.get("apply_root_motion"), action_id


async def test_a_retrieved_sit_generates_nothing(wired):
    """The point of naming a `via` at all. The frames between standing and seated exist in a clip, so
    they are played rather than made, and the result says so by carrying no generated transition."""
    registry, submitted = wired
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "sit_on": "obj:Chair",
        "then": [{"via": [C.SIT_DOWN], "base": C.SEATED}]})
    assert out["success"] is True
    assert "generated_transitions" not in out
    assert not any(s.get("generated") for s in committed(submitted)[-1]["steps"])


async def test_a_sit_down_walks_to_a_computed_point_rather_than_to_the_chair(walked):
    """Naming a seat used to mean walking to the seat. With a clip that steps backwards into it, the
    destination is the point that clip's own travel ends AT the seat from -- and she faces away from
    it, because that is how a person sits down.

    The numbers are checked against `standing_point_for`, not written down here: the chair's position
    comes back from the scene and the displacement off the sidecar, so a test that restated either
    would be testing its own copy of them."""
    registry, submitted, moves = walked
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "sit_on": "obj:Chair",
        "then": [{"via": [C.SIT_DOWN], "base": C.SEATED}]})
    assert out["success"] is True, out

    destinations = [m.get("to") for m in moves if m.get("to")]
    assert any(str(d).startswith("point:") for d in destinations), destinations
    assert "obj:Chair" not in destinations, "the chair is where she ENDS, not where she walks to"

    # The fake scene answers every object at the same position, so the standing point is the clip's
    # own travel away from it -- whichever direction the approach resolves to.
    seat = SCENE_POSITION
    travel = KI.root_travel_of(KBIndex.load().record(C.SIT_DOWN))[:2]
    walked_to = next(d for d in destinations if str(d).startswith("point:"))
    got_x, got_z = [float(v) for v in walked_to[len("point:"):].split(",")]
    assert math.hypot(got_x - seat[0], got_z - seat[2]) == pytest.approx(
        math.hypot(travel[0], travel[1]), abs=1e-3)


async def test_a_pinned_hand_two_actions_drive_is_refused_by_name(wired):
    """Asked to carry the bottle in that hand AND to drive it from two clips, there is nothing left
    to decide: half a hand shaped for a bottle and half shaped for a chest grips neither, and the IK
    constraint then drags the wrist of a pose that was never a grip. A hand is a shape, not an axis,
    so this is the one contested channel that is refused rather than halved.

    v3 got here a different way: both records DECLARED a contact, one hand could serve only one of
    them, and the loser had its object detached and reported as a `dropped_grip`. A v4 record
    declares nothing (ADR 0022) -- what a hand holds is named once, by the plan -- so there is no
    second grip left to drop, and the refusal names the body part and the pair instead of two
    objects."""
    registry, _ = wired
    out = await registry.dispatch("unity_execute", {
        "base": C.CYCLIC, "base_channels": ["right_hand"], "mode": "commit",
        "overlays": [{"action_id": C.GRAB, "channels": ["right_arm", "right_hand"]}],
        "carry": [{"object_id": "obj:AspirinBottle", "hand": "right_hand"}]})
    assert out["success"] is False
    assert "right_hand" in out["error"]
    assert "%s and %s" % (C.CYCLIC, C.GRAB) in out["error"]
    assert "holds neither" in out["error"]
    assert "one after the other with `then`" in out["hint"]


async def test_the_same_pair_is_mixed_when_the_plan_pins_nothing(wired):
    """The refusal above is about the PIN, not about the hand. Drop the carry and the two claims are
    an ordinary contested channel, which is halved and played -- because nothing in the record says
    either clip is holding anything."""
    registry, submitted = wired
    out = await registry.dispatch("unity_execute", {
        "base": C.CYCLIC, "base_channels": ["right_hand"], "mode": "commit",
        "overlays": [{"action_id": C.GRAB, "channels": ["right_arm", "right_hand"]}]})
    assert out["success"] is True, out.get("error")
    mixed = [l for l in submitted[-1]["steps"][0]["layers"] if l.get("source") == "mix"]
    assert [l["channels"] for l in mixed] == [["right_hand"]]


# ---- getting up, which is the same machinery as sitting down with the ends swapped ---------------

@pytest.fixture
async def seated(unused_tcp_port, kb):
    """An engine that reports her sitting on the chair, and stops reporting it once a plan that ends
    standing has been committed. `on_navmesh` follows, because that is what the executor does: the
    navigation agent comes back at the end of the rise and not before."""
    timeline = []
    base = handlers(timeline)
    inner = base[P.T.MOTION_LOCOMOTE]

    def stood_up():
        return any(entry.get("steps") and entry["steps"][-1].get("posture") == "standing"
                   for entry in timeline if isinstance(entry, dict))

    def locomote(params):
        out = inner(params)
        if params.get("query"):
            up = stood_up()
            out["posture"] = "standing" if up else "seated"
            out["sitting_on"] = None if up else "obj:Chair"
            out["on_navmesh"] = up
            out["playing"] = None if up else C.SEATED
        else:
            timeline.append({"locomote": params})
        return out

    base[P.T.MOTION_LOCOMOTE] = locomote
    async with EngineLink("127.0.0.1", unused_tcp_port, request_timeout=2.0) as link:
        async with FakeEngine("ws://127.0.0.1:%d" % unused_tcp_port, base,
                              hello={"scene": "TestScene", "characters": ["chr:CPRNurse"]}):
            await link.wait_ready(timeout=2)
            registry = kb_tools.register(ToolRegistry(), kb)
            scene_tools.register(registry, link, kb)
            yield registry, timeline


async def test_she_is_stood_up_before_she_is_walked(seated):
    """The order is the whole problem. Re-enabling the navigation agent warps her to the nearest
    walkable point, which is not under the chair, so travelling first teleports her off the seat in a
    seated pose. The rise has to be committed and landed first."""
    registry, timeline = seated
    out = await registry.dispatch("unity_execute", {"base": C.WALK, "walk_to": "obj:Chair",
                                                  "then": [{"base": C.GIVING}],
                                                  "mode": "commit"})
    assert out.get("success") is not False, out
    plans = [i for i, e in enumerate(timeline) if e.get("steps")]
    walks = [i for i, e in enumerate(timeline) if e.get("locomote")]
    assert plans and walks
    assert plans[0] < walks[0], "she was walked before she was on her feet"


async def test_getting_up_generates_its_frames(seated):
    """No clip covers it in either direction -- the corpus has one seated action, so every route
    between sitting and standing crosses the same gap. The rise is the seated action she is in, into
    the one that was asked for, with the frames between them made."""
    registry, timeline = seated
    await registry.dispatch("unity_execute", {"base": C.WALK, "walk_to": "obj:Chair",
                                            "then": [{"base": C.GIVING}],
                                            "mode": "commit"})
    rise = [e for e in timeline if e.get("steps")][0]["steps"]
    assert [s["action_id"] for s in rise] == [C.SEATED, C.WALK]
    assert rise[0]["posture"] == "seated" and rise[1]["posture"] == "standing"
    assert rise[1].get("generated"), "the posture change was scheduled as an ordinary blend"
    assert rise[1]["generated"]["support_object_id"] == "obj:Chair"


async def test_the_rise_travels_upward(seated):
    """Sitting down and standing up are the same generated descent with the ends swapped, so the one
    thing that must differ is the direction the hips go."""
    registry, timeline = seated
    await registry.dispatch("unity_execute", {"base": C.WALK, "walk_to": "obj:Chair",
                                            "then": [{"base": C.GIVING}],
                                            "mode": "commit"})
    made = [e for e in timeline if e.get("steps")][0]["steps"][1]["generated"]
    assert made["target_hip_height_m"] > made["start_hip_height_m"]


async def test_getting_up_is_reported_because_nobody_asked_for_it(seated):
    """The model asked for what came after. A reply that does not mention getting up describes a
    character who was already on her feet."""
    registry, _ = seated
    out = await registry.dispatch("unity_execute", {"base": C.WALK, "walk_to": "obj:Chair",
                                                  "then": [{"base": C.GIVING}],
                                                  "mode": "commit"})
    assert out["stood_up"]["landed"] is True
    assert out["stood_up"]["order"] == [C.SEATED, C.WALK]


async def test_a_standing_character_is_left_alone(wired):
    """Every plan written before this one. One query, no extra plan, nothing changed."""
    registry, submitted = wired
    out = await registry.dispatch("unity_execute", {"base": C.WALK, "walk_to": "obj:Chair",
                                                  "then": [{"base": C.GIVING}],
                                                  "mode": "commit"})
    # Asserted to have SUCCEEDED first. Without this the rest passes on a refusal — nothing was
    # committed, so "no rise was committed" is vacuously true and the test proves nothing.
    assert out.get("success") is not False, out
    assert "stood_up" not in out
    plans = [entry for entry in submitted if entry.get("steps")]
    assert plans, "nothing was committed, so this test would pass however broken the code was"
    assert all(entry["steps"][0]["action_id"] != C.SEATED for entry in plans)


# ---- three nurses, told apart by name ------------------------------------------------------------

THREE = {"scene": "TestScene",
         "characters": ["chr:CPRNurse", "chr:EKGNurse", "chr:AirwayNurse"],
         "character_names": {"chr:CPRNurse": "Jill", "chr:EKGNurse": "Dana",
                             "chr:AirwayNurse": "Kate"}}


@pytest.fixture
async def crowded(unused_tcp_port, kb):
    """Three drivable characters, as the scene has once Jill, Dana and Kate are all wired."""
    submitted = []
    async with EngineLink("127.0.0.1", unused_tcp_port, request_timeout=2.0) as link:
        async with FakeEngine("ws://127.0.0.1:%d" % unused_tcp_port, handlers(submitted),
                              hello=THREE):
            await link.wait_ready(timeout=2)
            registry = kb_tools.register(ToolRegistry(), kb)
            scene_tools.register(registry, link, kb)
            yield registry, submitted


@pytest.mark.parametrize("asked,expected", [
    ("Dana", "chr:EKGNurse"),          # her name, which is what an instruction says
    ("dana", "chr:EKGNurse"),          # and however it was capitalised
    ("chr:AirwayNurse", "chr:AirwayNurse"),   # the id, which is what the protocol says
    ("CPRNurse", "chr:CPRNurse"),      # the scene object, which is what the KB and logs say
])
async def test_a_nurse_can_be_named_three_ways(crowded, asked, expected):
    """One character, three spellings, and only the middle one used to be accepted -- so the natural
    instruction failed on the thing it was most specific about."""
    registry, submitted = crowded
    await registry.dispatch("unity_execute", {"base": C.CYCLIC, "character": asked, "mode": "commit"})
    assert submitted[-1]["character"] == expected


async def test_naming_nobody_is_a_question_not_a_guess(crowded):
    """With one character, whatever was asked for was the only answer. With three, picking one would
    send the instruction to the wrong person silently."""
    registry, _ = crowded
    out = await registry.dispatch("unity_execute", {"base": C.CYCLIC, "mode": "commit"})
    assert out["success"] is False
    assert "which one" in out["error"]
    for name in ("Jill", "Dana", "Kate"):
        assert name in out["hint"]


async def test_an_unknown_name_lists_who_is_actually_here(crowded):
    registry, _ = crowded
    out = await registry.dispatch("unity_execute", {"base": C.CYCLIC, "character": "Maria",
                                                  "mode": "commit"})
    assert out["success"] is False
    assert "Maria" in out["error"]
    assert "Jill" in out["hint"] and "Dana" in out["hint"] and "Kate" in out["hint"]


async def test_one_character_still_answers_to_anything(wired):
    """The behaviour that removed a measured round trip: with nobody else to confuse her with, a wrong
    name is not a question."""
    registry, submitted = wired
    await registry.dispatch("unity_execute", {"base": C.CYCLIC, "character": "nurse", "mode": "commit"})
    assert submitted[-1]["character"] == "chr:CPRNurse"


# ---- nothing visible happens until the plan has been checked -------------------------------------

def refusing_handlers(record, moves=None, reason="pelvis_outside_support", metric="seated_on_support"):
    """An engine whose pre-execution check says no. The failure is structured — which metric, on what,
    and why — because "it failed" leaves the model rewriting arguments at random."""
    def verdict(params):
        return {"status": "fail", "checked": [metric], "samples": 61, "seconds_simulated": 2.0,
                "failures": [{"metric": metric, "object_id": "obj:Chair", "reason": reason}],
                "metrics": []}
    return handlers(record, moves, verdict=verdict)


@pytest.fixture
async def refusing(unused_tcp_port, kb):
    submitted, moves = [], []
    async with EngineLink("127.0.0.1", unused_tcp_port, request_timeout=2.0) as link:
        async with FakeEngine("ws://127.0.0.1:%d" % unused_tcp_port,
                              refusing_handlers(submitted, moves),
                              hello={"scene": "TestScene", "characters": ["chr:CPRNurse"]}):
            await link.wait_ready(timeout=2)
            registry = kb_tools.register(ToolRegistry(), kb)
            scene_tools.register(registry, link, kb)
            yield registry, submitted, moves


async def test_a_plan_that_fails_the_check_never_plays(refusing):
    """The rule this whole path exists for. A candidate that does not work must not reach the visible
    character at all — not as a pose, and not as a walk across the room to find out."""
    registry, submitted, moves = refusing
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "then": [{"base": C.SEATED}], "sit_on": "obj:Chair"})

    assert out["success"] is False
    assert committed(submitted) == [], "nothing may play once the check has failed"
    assert checked(submitted), "and the check has to have actually run"
    assert not [m for m in moves if not m.get("preview") and not m.get("query")], \
        "she must not have been walked anywhere either"


async def test_a_refusal_names_what_to_change(refusing):
    """Not "it failed". Which check, on what, and which of the four things to change — the motion, the
    target, the composition, the route. A failure that does not point at one of those sends the model
    rewriting arguments that were already right."""
    registry, _, _ = refusing
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "then": [{"base": C.SEATED}], "sit_on": "obj:Chair"})

    assert "seated_on_support" in out["error"] and "obj:Chair" in out["error"]
    assert "nothing was played and nothing moved" in out["hint"]
    assert "sit does not land on the seat" in out["hint"]


async def test_the_plan_that_was_checked_is_the_plan_that_plays(wired):
    """One compile, two sends, the same bytes. Deriving the plan again between the check and the play
    would reintroduce exactly the gap this closes: the verdict would be about a plan that no longer
    describes what is about to happen."""
    registry, submitted = wired
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "then": [{"base": C.SEATED}], "sit_on": "obj:Chair"})

    assert out["success"]
    pairs = fenced(submitted)
    assert len(pairs) == len(committed(submitted)) == 2, "the walk and the plan, each checked"
    assert out["validated"]["status"] == "pass"


async def test_the_check_runs_where_the_walk_will_leave_her(walked):
    """A motion checked where she stands is a motion checked in the wrong place: a sit judged from
    across the room lands nowhere, correctly, about a plan that would have worked. So the route is
    projected first and the hidden copy is stood at the arrival."""
    registry, submitted, moves = walked
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "walk_to": "obj:Chair", "then": [{"base": C.SEATED}],
        "sit_on": "obj:Chair"})

    assert out["success"]
    assert [m for m in moves if m.get("preview")], "the route has to be previewed, not walked"
    probe = fenced(submitted)[-1][0]
    assert probe["at"] == {"position": [0.9, 0.0, -1.7], "facing_deg": 42.0}


async def test_the_route_is_previewed_before_she_takes_a_step(walked):
    """The preview comes first and the walk comes after the verdict. Reading the order off the
    engine's own record, because "before" is the entire claim."""
    registry, submitted, moves = walked
    await registry.dispatch("unity_execute", {
        "base": C.WALK, "walk_to": "obj:Chair", "then": [{"base": C.SEATED}],
        "sit_on": "obj:Chair"})

    kinds = [("preview" if m.get("preview") else "walk") for m in moves
             if m.get("to") is not None]
    assert kinds and kinds[0] == "preview"
    assert "walk" in kinds


async def test_somewhere_she_cannot_get_to_is_refused_before_she_moves(walked):
    """This used to be found by walking at it until a timeout, which is a failed plan the viewer
    watches. The route is computed first now, so an unreachable destination costs nothing visible."""
    registry, submitted, moves = walked
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "walk_to": "obj:Nope", "then": [{"base": C.SEATED}],
        "sit_on": "obj:Chair"})

    assert out["success"] is False
    assert "cannot get to" in out["error"] and "nothing has moved" in out["hint"]
    assert committed(submitted) == []
    assert not [m for m in moves if not m.get("preview") and not m.get("query")]


async def test_the_walk_itself_is_checked_before_it_starts(walked):
    """"Walk over holding the bottle out" is a composed motion that plays DURING the crossing, so it
    is a plan in its own right and it is checked in its own right — before the first step, not after
    the last."""
    registry, submitted, moves = walked
    out = await registry.dispatch("unity_execute", {
        "base": C.WALK, "overlays": [CARRY], "walk_to": "obj:Chair"})

    assert out["success"]
    probes = checked(submitted)
    assert probes, "the walk-with-overlay plan has to be checked"
    assert probes[0]["steps"][0]["action_id"] == C.WALK
    assert {layer["action_id"] for layer in probes[0]["steps"][0]["layers"]} == {C.WALK,
                                                                                C.GRAB}
    # And it was checked before she set off, not re-checked from inside the walk.
    assert [s["steps"][0]["action_id"] for s in committed(submitted)].count(C.WALK) == 1


async def test_getting_up_goes_through_the_same_fence(seated):
    """The rise is the one plan this file decides on by itself — the model asked for what came after —
    so a rise that lands badly is a failure nobody would have predicted from the request."""
    registry, timeline = seated
    out = await registry.dispatch("unity_execute", {"base": C.IDLE})

    assert out["success"]
    plans = [e for e in timeline if isinstance(e, dict) and e.get("steps")]
    rise = [e for e in plans if [s["action_id"] for s in e["steps"]] == [C.SEATED, C.IDLE]]
    assert [e["mode"] for e in rise] == ["validate", "commit"], \
        "the rise nobody asked for is checked like everything else"
