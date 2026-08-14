"""Scene and plan tools against the fake engine.

The load-bearing assertion is that the plan tool has nowhere for a number to ENTER. Coordinates now
come back out of scene_position, deliberately, because deciding whether to walk before sitting needs
them — but the direction that matters for the architecture is the other one: the model reads measured
numbers and never writes motion ones.
"""
import pytest

from agent import protocol as P
from agent.engine import EngineLink
from agent.kbindex import KBIndex
from agent.tools import ToolRegistry
from agent.tools import kb as kb_tools
from agent.tools import scene as scene_tools
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


# What the engine says she is doing when asked. Mutated by the tests that care; the default is the
# state every other test was written against, where nothing is playing.
STANDING = {"playing": None, "going": False}


def handlers(record, moves=None):
    def find(params):
        alias = params.get("alias")
        name = (params.get("name_contains") or "").lower()
        category = params.get("category")
        out = [o for o in OBJECTS.values()
               if (not alias or alias in o["aliases"])
               and (not name or name in o["name"].lower())
               and (not category or category == o["category"])]
        return {"objects": out}

    def describe(params):
        obj = OBJECTS.get(params["object_id"])
        if obj is None:
            raise FakeEngineError(P.E.NOT_FOUND, "no object %s" % params["object_id"])
        return obj

    def assemble(params):
        record.append(params)
        return {"plan_id": "pl_1", "accepted": True, "start_play_latency_ms": 31}

    def position(params):
        out = []
        for object_id in params["object_ids"]:
            obj = OBJECTS.get(object_id)
            if obj is None:
                out.append({"object_id": object_id, "found": False})
                continue
            item = {"object_id": object_id, "found": True, "position": [1.0, 0.5, -2.0]}
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
        if params.get("face_only"):
            return dict({"arrived": True, "turning": False, "remaining_m": 0.0}, **STANDING)
        if params.get("query"):
            # `playing` is what the real executor reports for the step carrying the most weight. A
            # plan whose opening step is only there to be departed from opens on this instead of on
            # a walk cycle she is not walking.
            return dict({"arrived": True, "turning": False, "remaining_m": 0.0}, **STANDING)
        if params.get("to") not in OBJECTS:
            raise FakeEngineError(P.E.NOT_FOUND, "nothing called %s" % params.get("to"))
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
                              hello={"scene": "TestScene", "characters": ["chr:CPRNurse"]}):
            await link.wait_ready(timeout=2)
            registry = kb_tools.register(ToolRegistry(), kb)
            scene_tools.register(registry, link, kb)
            yield registry, submitted


async def test_alias_bridges_the_motion_library_to_the_scene(wired):
    """The KB says a motion touches `aspirin_bottle`; the scene has an object called `Aspirin Bottle`.
    The alias is what joins them, and it is why a motion can be grounded at all."""
    registry, _ = wired
    out = await registry.dispatch("scene_find", {"alias": "aspirin_bottle"})
    assert out["objects"][0]["id"] == "obj:AspirinBottle"


async def test_scene_replies_carry_no_coordinates(wired):
    registry, _ = wired
    out = await registry.dispatch("scene_describe", {"object_id": "obj:AspirinBottle"})
    for banned in ("position", "rotation", "scale", "bounds", "distance", "meters"):
        assert banned not in repr(out).lower()


async def test_unknown_object_is_recoverable(wired):
    registry, _ = wired
    out = await registry.dispatch("scene_describe", {"object_id": "obj:Nope"})
    assert out["success"] is False
    assert "scene_find" in out["hint"]


async def test_plan_derives_the_partition_and_sends_it_to_the_engine(wired):
    registry, submitted = wired
    out = await registry.dispatch("plan_motion", {
        "character": "chr:CPRNurse", "base": "walking", "overlays": ["grab_bottle"],
        "ik_bindings": [{"effector": "right_hand", "object_id": "obj:AspirinBottle"}],
        "carry": [{"object_id": "obj:AspirinBottle", "hand": "right_hand"}]})

    assert out["success"]
    # v2 wraps every plan in `steps`; a single action is a one-step sequence, so there is one shape on
    # the wire rather than a flat form for the common case and a nested one for the interesting case.
    assert len(submitted[0]["steps"]) == 1
    layers = {layer["action_id"]: layer for layer in submitted[0]["steps"][0]["layers"]}
    assert sorted(c for c in layers["walking"]["channels"] if c != "root") == ["left_leg", "right_leg"]
    assert sorted(layers["grab_bottle"]["channels"]) == ["right_arm", "right_hand"]
    assert layers["walking"]["owns_root"] is True
    assert sorted(submitted[0]["free_channels"]) == ["head", "left_arm", "left_hand", "torso"]


async def test_clip_ids_reach_the_engine_but_never_the_model(wired):
    """The executor needs a guid; the model has no use for one and pays tokens to see it."""
    registry, submitted = wired
    out = await registry.dispatch("plan_motion", {"character": "c", "base": "cpr"})
    assert submitted[0]["steps"][0]["layers"][0]["clip"]["guid"]
    assert "guid" not in repr(out)


async def test_the_plan_tool_has_nowhere_for_a_number_to_enter(wired):
    """Structural enforcement of "the model emits no motion numerics" — check the schema, not the model."""
    registry, _ = wired
    spec = next(d for d in registry.declarations() if d["name"] == "plan_motion")

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


async def test_scene_position_returns_measurements_the_model_can_reason_about(wired):
    """The reversal, stated as a test: coordinates DO come back now. What matters is the direction —
    the model reads them, and the test above proves it still cannot write any."""
    registry, _ = wired
    out = await registry.dispatch("scene_position", {"object_ids": ["obj:AspirinBottle"],
                                                     "relative_to": "chr:CPRNurse"})
    assert out["success"]
    item = out["objects"][0]
    assert item["found"] is True and len(item["position"]) == 3
    assert item["from_character"]["needs_walking"] is True


async def test_scene_position_names_the_ids_it_could_not_find(wired):
    registry, _ = wired
    out = await registry.dispatch("scene_position", {"object_ids": ["obj:Nope"]})
    assert out["success"]
    assert out["objects"][0]["found"] is False
    assert "scene_find" in out["note"]


async def test_a_seat_reports_the_height_of_its_surface(wired):
    """A generated sit lands on a measured surface, not an assumed one."""
    registry, _ = wired
    out = await registry.dispatch("scene_position", {"object_ids": ["obj:Chair"]})
    assert out["objects"][0]["surface_height_m"] == pytest.approx(0.4054)


async def test_move_to_waits_until_she_has_arrived(wired):
    """"She is at the desk" is the precondition the next call depends on. Returning before it is true
    just moves the waiting into the model."""
    registry, _ = wired
    out = await registry.dispatch("move_to", {"character": "chr:CPRNurse",
                                              "destination": "obj:Chair"})
    assert out["success"] and out["arrived"] is True
    assert out["path_length_m"] == pytest.approx(1.08)


async def test_move_to_reports_a_destination_that_does_not_exist(wired):
    registry, _ = wired
    out = await registry.dispatch("move_to", {"character": "chr:CPRNurse", "destination": "obj:Nope"})
    assert out["success"] is False


async def test_a_seated_action_with_nothing_to_sit_on_is_refused(wired):
    """The false success this was written for: an agent that could not find the chair planned `typing`
    on its own, the character sat in mid-air, every geometric check passed because nothing had claimed
    a support, and the agent reported success."""
    registry, submitted = wired
    before = len(submitted)
    out = await registry.dispatch("plan_motion", {"character": "chr:CPRNurse", "base": "typing"})
    assert out["success"] is False
    assert "nothing was named to sit on" in out["error"]
    assert "scene_find" in out["hint"]
    assert len(submitted) == before, "it should be refused before the engine is asked to play anything"


async def test_a_seated_action_names_its_support_so_the_gate_has_something_to_judge(wired):
    registry, submitted = wired
    out = await registry.dispatch("plan_motion", {"character": "chr:CPRNurse", "base": "typing",
                                                  "sit_on": "obj:Chair"})
    assert out["success"]
    assert submitted[-1]["steps"][0]["expect_support"]["object_id"] == "obj:Chair"


async def test_the_generated_descent_is_reachable_from_the_tool_the_model_is_given(wired):
    """THE PATH THE AGENT HAS TO BE ABLE TO TAKE. Naming both actions in one call with a seat is what
    makes the standing-to-seated frames; the generator was verified by a deterministic probe long
    before any agent reached it, and reaching it is a separate claim from having it."""
    registry, submitted = wired
    out = await registry.dispatch("plan_motion", {
        "character": "chr:CPRNurse", "base": "walking", "then": [{"base": "typing"}],
        "sit_on": "obj:Chair"})

    assert out["success"], out.get("error")
    generated = out["generated_transitions"]
    assert len(generated) == 1
    assert generated[0]["kind"] == "posture_change"
    assert generated[0]["support_object_id"] == "obj:Chair"
    assert generated[0]["target_hip_height_m"] == pytest.approx(0.4054, abs=0.15)
    # and it reached the engine, which is what actually plays it
    assert any(s.get("generated") for s in submitted[-1]["steps"])


async def test_a_contact_the_clip_already_animates_is_not_reported_as_missing(wired):
    """The knowledge base answers this and the check was not reading it: `typing` records both hands as
    `role: primary` with `contact: object:keyboard`, so the clip animates them against a keyboard
    already. Calling that "not bound to anything in the scene" read as an instruction and the model
    started binding wrists to grab points."""
    registry, _ = wired
    out = await registry.dispatch("plan_motion", {"base": "typing", "sit_on": "obj:Chair"})
    assert out["success"]
    contact = next(g for g in out["gates"] if g["id"] == "contact_grounded")
    assert contact["status"] == "pass"
    assert "animated against its object by the clip itself" in contact["detail"]
    assert "Do not bind these hands yourself" in contact["hint"]


async def test_declared_hands_are_bound_to_the_objects_own_per_hand_anchors(wired):
    """The scene knew where each hand goes and the registry had never learned it: the laptop carries
    LaptopHandLeft/Right and their elbow hints, the demo path engages them and the hands land 0.000 m
    from it, and the agent path knew only a single grab point. `typing` declares both hands on the
    keyboard, so the binding is derivable -- from the library and the registry, not from the model."""
    registry, submitted = wired
    out = await registry.dispatch("plan_motion", {"base": "typing", "sit_on": "obj:Chair"})
    assert out["success"]
    assert {b["effector"]: b["object_id"] for b in submitted[-1]["ik"]} == {
        "left_hand": "obj:Laptop", "right_hand": "obj:Laptop"}
    assert all("says where each one goes" in g["because"] for g in out["grounded_hands"])


async def test_an_object_with_one_grab_point_binds_nothing(wired):
    """A bottle says where a hand goes only in the sense of being somewhere. Aiming two at it pulls both
    wrists onto the same point, so where the object cannot say, nothing is bound and the clip is left
    alone."""
    registry, submitted = wired
    out = await registry.dispatch("plan_motion", {"base": "grab_bottle"})
    assert out["success"]
    assert submitted[-1]["ik"] == []
    assert "grounded_hands" not in out


async def test_a_clip_carried_contact_is_measured_as_well_as_grounded(wired):
    """Grounding and judging are separate. The hands are put on the object's own per-hand anchors, and
    the result is still measured, because reaching the anchors is not the same as the motion looking
    right: `typing` types 0.70 m above the floor and 0.33 m in front of the root, this laptop's deck is
    near 0.90 m, and where an object carries no anchors nothing is bound at all."""
    registry, submitted = wired
    out = await registry.dispatch("plan_motion", {
        "base": "walking", "then": [{"base": "typing"}], "sit_on": "obj:Chair"})
    assert out["success"]
    expect = {c["effector"]: c for c in submitted[-1]["expect_contact"]}
    assert set(expect) == {"left_hand", "right_hand"}
    assert all(c["object_id"] == "obj:Laptop" for c in expect.values())
    # Due when the posture change FINISHES, not when the step starts. The descent is still running
    # through the opening of the seated step, and the worst contact error on this plan landed mid-way
    # down at 0.12 m with the hands correct before and after.
    typing = next(s for s in out["sequence"] if s["action_id"] == "typing")
    descent = out["generated_transitions"][0]["duration_s"]
    assert expect["left_hand"]["due_at_s"] == pytest.approx(typing["starts_at_s"] + descent)


async def test_a_seat_named_without_its_prefix_still_resolves(wired):
    """Measured, and it cost the whole task: the model wrote `sit_on: "Chair"` where the id is
    `obj:Chair`, got "no object 'Chair' to sit on" about a room with a chair in it, tried again with
    the same spelling, and gave up saying the library could not do it."""
    registry, submitted = wired
    out = await registry.dispatch("plan_motion", {
        "base": "walking", "then": [{"base": "typing"}], "sit_on": "Chair"})
    assert out["success"], out.get("error")
    assert out["generated_transitions"][0]["support_object_id"] == "obj:Chair"


async def test_an_ambiguous_name_is_still_refused(wired):
    """Two matches is a real question, and answering it by taking the first would be a guess."""
    registry, _ = wired
    out = await registry.dispatch("plan_motion", {
        "base": "walking", "then": [{"base": "typing"}], "sit_on": "a"})
    assert out["success"] is False
    assert "to sit on" in out["error"]


async def test_walking_to_somewhere_plays_the_walk(wired):
    """Displacement and animation are separate mechanisms here, and running only the first slid the
    character across the room in whatever pose she was in. Nobody asked for a slide; the walk cycle
    had no one to start it."""
    registry, submitted = wired
    out = await registry.dispatch("move_to", {"character": "chr:CPRNurse", "destination": "obj:Chair"})
    assert out["success"] and out["arrived"] is True
    played = [s["steps"][0]["action_id"] for s in submitted]
    assert played == ["walking", "idle"], "walk while going, stop walking once there"
    assert all(s["mode"] == "commit" for s in submitted)


async def test_every_step_tells_the_engine_what_posture_it_is_in(wired):
    """The engine has no knowledge base, so the posture has to travel with the step. It is what lets
    the executor know she ends up seated -- and therefore keep the generated pose across the next graph
    rebuild, and refuse to walk her off the chair."""
    registry, submitted = wired
    await registry.dispatch("plan_motion", {
        "character": "chr:CPRNurse", "base": "walking", "then": [{"base": "typing"}],
        "sit_on": "obj:Chair"})
    assert [s["posture"] for s in submitted[-1]["steps"]] == ["standing", "seated"]


async def test_a_committed_generated_plan_schedules_its_own_verification(wired):
    """The landing is not measurable when the plan returns -- the descent has not run yet -- and waiting
    for it would put the length of the animation inside the answer. So the check is scheduled, and the
    result says plainly that nothing is known yet."""
    registry, _ = wired
    out = await registry.dispatch("plan_motion", {
        "character": "chr:CPRNurse", "base": "walking", "then": [{"base": "typing"}],
        "sit_on": "obj:Chair", "mode": "commit"})

    assert out["success"]
    verify = out["verify"]
    assert verify["status"] == "scheduled"
    assert verify["tool"] == "check_motion"
    assert verify["arguments"] == {"character": "chr:CPRNurse"}
    assert "obj:Chair" in verify["confirms"]
    assert "do not say she is seated" in verify["note"]


async def test_a_dry_run_schedules_nothing(wired):
    """Nothing is playing, so there is nothing to measure and nothing to promise."""
    registry, _ = wired
    out = await registry.dispatch("plan_motion", {
        "character": "chr:CPRNurse", "base": "walking", "then": [{"base": "typing"}],
        "sit_on": "obj:Chair", "mode": "dry_run"})
    assert out["success"] and "generated_transitions" in out
    assert "verify" not in out


async def test_planning_plays_unless_told_otherwise(wired):
    """A dry run and then the identical commit is two round trips to play one motion, and the model
    paid them both -- then lost a third iteration inventing `commit: true` to skip the first."""
    registry, submitted = wired
    await registry.dispatch("plan_motion", {"base": "cpr"})
    assert submitted[-1]["mode"] == "commit"


async def test_the_only_character_does_not_have_to_be_named(wired):
    """Measured: `move_to` with character "nurse" against a scene whose only character is
    "chr:CPRNurse". The id bought nothing and cost a round trip whenever the model wrote something
    reasonable-looking instead."""
    registry, submitted = wired
    out = await registry.dispatch("move_to", {"destination": "obj:Chair"})
    assert out["success"]
    assert submitted[-1]["character"] == "chr:CPRNurse"


async def test_a_lone_seated_action_says_it_is_a_cut(wired):
    """The gates measure where she ends up, and she ends up correctly on the seat — nothing in them can
    see that she got there in one frame. The result has to say so itself."""
    registry, _ = wired
    out = await registry.dispatch("plan_motion", {"character": "chr:CPRNurse", "base": "typing",
                                                  "sit_on": "obj:Chair"})
    assert out["success"]
    cut = next(g for g in out["gates"] if g["id"] == "transition_present")
    assert cut["status"] == "warn"
    assert "then" in cut["hint"]
    assert "generated_transitions" not in out, "nothing was generated, so nothing may claim it was"


async def test_a_sequence_without_a_posture_change_generates_nothing(wired):
    registry, _ = wired
    out = await registry.dispatch("plan_motion", {"character": "chr:CPRNurse", "base": "check_pulse",
                                                  "then": [{"base": "giving_pills"}]})
    assert out["success"]
    assert "generated_transitions" not in out
    assert [s["action_id"] for s in out["sequence"]] == ["check_pulse", "giving_pills"]


async def test_a_standing_action_needs_no_seat(wired):
    registry, _ = wired
    out = await registry.dispatch("plan_motion", {"character": "chr:CPRNurse", "base": "cpr"})
    assert out["success"]


async def test_a_contested_hand_loses_its_object_and_says_so(wired):
    """`giving_pills` and `cpr` both grip both hands. The plan plays -- one hand keeps one of the two
    motions, the other's object is simply not attached -- and every dropped object is named, because a
    reply describing her handing over pills that were never in her hand is the failure here."""
    registry, submitted = wired
    out = await registry.dispatch("plan_motion", {
        "character": "c", "base": "idle", "overlays": ["giving_pills", "cpr"]})
    assert out["success"]
    channels = {d["channel"] for d in out["dropped_grips"]}
    assert {"left_hand", "right_hand"} <= channels
    assert submitted           # and it really was sent, rather than refused quietly


async def test_mixing_postures_fails_the_cheap_gate_before_any_round_trip(wired):
    registry, submitted = wired
    out = await registry.dispatch("plan_motion", {
        "character": "c", "base": "typing", "overlays": ["grab_bottle"]})
    assert out["success"] is False
    assert "seated" in out["error"] and "standing" in out["error"]
    assert not submitted


async def test_a_hand_contact_is_named_without_being_called_missing(wired):
    """grab_bottle's right hand touches the aspirin bottle and the clip animates it doing so. Saying
    which object is worth saying; calling it unbound was not, because the fix it implied -- an IK
    binding -- is the wrong mechanism. What grounds it is carrying the bottle or standing at it."""
    registry, _ = wired
    out = await registry.dispatch("plan_motion", {"base": "grab_bottle"})
    contact = next(g for g in out["gates"] if g["id"] == "contact_grounded")
    assert contact["status"] == "pass"
    assert "aspirin_bottle" in contact["detail"]
    assert "move_to it" in contact["hint"]
    assert out["success"]


async def test_scene_tools_degrade_when_the_engine_is_absent(kb, unused_tcp_port):
    """The demo must still answer from the motion library when Unity is not running."""
    async with EngineLink("127.0.0.1", unused_tcp_port, request_timeout=0.5) as link:
        registry = scene_tools.register(kb_tools.register(ToolRegistry(), kb), link, kb)
        out = await registry.dispatch("scene_find", {"category": "device"})
        assert out["success"] is False
        assert "not connected" in out["error"]
        assert "motion library" in out["hint"]


# ---- blank clauses are not constraints ---------------------------------------------------------

async def test_blank_clauses_are_dropped_before_they_reach_the_engine():
    """The defect that hid a chair in plain sight.

    A model fills in every field a schema offers, so `reachable_by: {"character": ""}` arrives on a
    search that never meant to constrain anything. Engine-side, a blank character id resolved to the
    driven character — which is the right default for a tool that needs an actor, and catastrophic for
    a filter: it turned an unconstrained search into "within arm's reach right now", and the chair was
    across the room. Ten scene_find calls in one turn, every one empty, and the agent concluded the
    room had no chair.
    """
    from agent.tools.scene import _asked_for

    assert _asked_for("category", "seating") is True
    assert _asked_for("category", "") is False
    assert _asked_for("category", "   ") is False
    assert _asked_for("name_contains", None) is False

    # The two clauses that carry a subject: a radius with nothing to be near, and an effector with
    # nobody to reach, are defaults rather than requests.
    assert _asked_for("near", {"object_id": "", "radius": "same_room"}) is False
    assert _asked_for("near", {"object_id": "obj:Chair", "radius": "same_room"}) is True
    assert _asked_for("reachable_by", {"character": "", "effector": "either"}) is False
    assert _asked_for("reachable_by", {"character": "chr:CPRNurse", "effector": "either"}) is True

    assert _asked_for("carry", []) is False
    assert _asked_for("limit", 10) is True


async def test_a_surface_she_would_end_up_under_is_refused_as_a_seat(wired):
    """Measured on a real turn: the model passed the laptop as `sit_on`. It has a surface, so nothing
    objected; the descent ran to the hip height `typing` opens on and left the pelvis 0.70 m beneath a
    deck it was reported to be sitting on -- inside the footprint, so the gate's containment check
    passed as well. Both numbers exist before anything plays."""
    registry, _ = wired
    out = await registry.dispatch("plan_motion", {
        "base": "walking", "then": [{"base": "typing", "sit_on": "obj:Laptop"}]})

    assert out["success"] is False
    assert "underneath it" in out["error"]
    assert "seating" in out["hint"]


async def test_a_real_seat_is_still_a_seat(wired):
    registry, _ = wired
    out = await registry.dispatch("plan_motion", {
        "base": "walking", "then": [{"base": "typing", "sit_on": "obj:Chair"}]})

    assert out["success"] is True
    assert out["generated_transitions"][0]["support_object_id"] == "obj:Chair"


async def test_every_object_the_model_names_is_looked_up_not_just_the_seat(wired):
    """Measured: a plan bound both hands to `Laptop` and named `obj:Chair` as the seat. The seat
    resolved and the bindings did not, so the clip played unbound and the gate reported hands 0.19 m
    from the keyboard. Which fields carry a prefix is not something a model gets right one at a time."""
    registry, submitted = wired
    out = await registry.dispatch("plan_motion", {
        "base": "walking", "then": [{"base": "typing"}], "sit_on": "Chair",
        "ik_bindings": [{"effector": "left_hand", "object_id": "Laptop"},
                        {"effector": "right_hand", "object_id": "Laptop"}],
        "gaze_at": "Laptop"})

    assert out["success"] is True
    sent = submitted[-1]
    assert {b["object_id"] for b in sent["ik"]} == {"obj:Laptop"}
    assert sent["gaze_at"] == "obj:Laptop"


async def test_a_carried_object_is_looked_up_too(wired):
    registry, submitted = wired
    out = await registry.dispatch("plan_motion", {
        "base": "walking", "overlays": ["grab_bottle"],
        "carry": [{"object_id": "Aspirin Bottle", "hand": "right_hand"}]})

    assert out["success"] is True
    assert submitted[-1]["carry"][0]["object_id"] == "obj:AspirinBottle"


async def test_a_walk_that_only_opens_a_sit_becomes_what_she_is_already_doing(wired):
    """Measured on a real turn: move_to walked her to the workstation and left her idle, then the
    committed plan opened on `walking` again -- so she marched on the spot in front of the desk for a
    whole loop cycle before sitting down. The opening step of a posture change exists to be departed
    FROM; `idle` serves as well and is what she is actually doing."""
    registry, submitted = wired
    STANDING["playing"] = "idle"
    try:
        out = await registry.dispatch("plan_motion", {
            "base": "walking", "then": [{"base": "typing"}], "sit_on": "obj:Chair"})
    finally:
        STANDING["playing"] = None

    assert out["success"] is True
    assert out["opened_on"] == {"asked_for": "walking", "played": "idle",
                                "why": "she was not walking anywhere; idle is what she was already "
                                       "doing"}
    assert submitted[-1]["steps"][0]["action_id"] == "idle"
    assert [g["id"] for g in out["gates"] if g["id"] == "opening_step"], \
        "the substitution has to be visible where the model reads structural facts"


async def test_a_bare_walk_while_she_is_stationary_is_refused(wired):
    """Measured on a real turn: move_to walked her to the patient, and the model then committed
    `walking` on its own -- so she arrived and kept striding on the spot indefinitely, while reporting
    that she had walked there. That report was true and was not what the scene showed."""
    registry, _ = wired
    out = await registry.dispatch("plan_motion", {"base": "walking"})

    assert out["success"] is False
    assert "march her on the spot" in out["error"]
    assert "move_to" in out["hint"]


async def test_a_walk_with_an_overlay_is_still_planned(wired):
    """Only the BARE case is refused. `walking` under an overlay is a composed motion whose base
    carries the posture -- walking while grabbing a bottle -- and taking that away would remove a
    capability over a plan the model may yet follow with a move_to."""
    registry, submitted = wired
    out = await registry.dispatch("plan_motion", {"base": "walking", "overlays": ["grab_bottle"]})

    assert out["success"] is True
    assert submitted[-1]["steps"][0]["action_id"] == "walking"


async def test_a_walk_she_is_actually_walking_is_planned(wired):
    """And so is one committed while the navigation agent is genuinely under way."""
    registry, submitted = wired
    STANDING["going"] = True
    try:
        out = await registry.dispatch("plan_motion", {"base": "walking"})
    finally:
        STANDING["going"] = False

    assert out["success"] is True
    assert submitted[-1]["steps"][0]["action_id"] == "walking"


async def test_a_walk_stays_a_walk_while_she_really_is_walking(wired):
    registry, submitted = wired
    try:
        # move_to with then_wait off leaves her under way, so the plan committed now genuinely does
        # open on a walk and replacing it would be the wrong correction.
        await registry.dispatch("move_to", {"destination": "obj:Chair", "then_wait": False})
        STANDING["playing"] = "walking"
        STANDING["going"] = True
        out = await registry.dispatch("plan_motion", {
            "base": "walking", "then": [{"base": "typing"}], "sit_on": "obj:Chair"})
    finally:
        STANDING["playing"] = None
        STANDING["going"] = False

    assert out["success"] is True
    assert "opened_on" not in out
    assert submitted[-1]["steps"][0]["action_id"] == "walking"


async def test_a_hand_binding_waits_for_the_step_that_reaches(wired):
    """The laptop's per-hand anchors are bound for her, because `typing` records both hands on a
    keyboard. They must not engage during the walk that gets her there: measured, the walk played with
    her arms stretched back toward the desk, and no geometric check saw it, because every one of them
    is about where she ends UP."""
    registry, submitted = wired
    out = await registry.dispatch("plan_motion", {
        "base": "walking", "then": [{"base": "typing"}], "sit_on": "obj:Chair"})

    assert out["success"] is True
    sent = submitted[-1]
    assert sent["ik"], "the laptop publishes a per-hand anchor, so the hands are grounded for her"
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
    out = await registry.dispatch("plan_motion", {
        "base": "grab_bottle", "ik_bindings": [{"effector": "right_hand",
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
    at 0.85 s -- and move_to ended by parking her in idle, so the plan that followed departed from a
    standstill: walk, stop, stand, sit. One call keeps the walk under her until the descent commits."""
    registry, submitted, _ = walked
    out = await registry.dispatch("plan_motion", {
        "base": "walking", "walk_to": "obj:Chair", "then": [{"base": "typing"}],
        "sit_on": "obj:Chair"})

    assert out["success"] is True
    played = [s["steps"][0]["action_id"] for s in submitted]
    assert "idle" not in played, "she must not be parked in idle between arriving and sitting"
    assert played == ["walking", "walking"], "the travel clip, then the plan that opens on it"
    assert [s["action_id"] for s in submitted[-1]["steps"]] == ["walking", "typing"]
    assert out["walked"]["path_length_m"] == 1.08 and out["walked"]["arrived"] is True


async def test_the_facing_comes_from_what_the_action_touches(walked):
    """A character faces the thing the action she is about to perform interacts with. `typing` records
    both hands as `contact: object:keyboard` and the registry aliases that to the laptop, so nothing
    is authored per seat -- and the seat does not get a vote, which is how she came to sit with her
    back to the desk when the facing was taken from the chair."""
    registry, _, moves = walked
    out = await registry.dispatch("plan_motion", {
        "base": "walking", "walk_to": "obj:Chair", "then": [{"base": "typing"}],
        "sit_on": "obj:Chair"})

    assert out["success"] is True
    assert out["walked"]["facing"] == "obj:Laptop"
    assert "typing" in out["walked"]["facing_from"]
    assert [m["face_only"] for m in moves if m.get("face_only")] == ["obj:Laptop"]


async def test_an_overlay_plays_on_top_of_the_walk_that_gets_her_there(walked):
    """"Walk over holding the bottle out" is the one shape of composition this corpus can express, and
    it used to wait for her to arrive before playing anything but the walk. She is not doing it WHILE
    walking if it starts when the walking stops.

    The first thing sent to the engine -- before a single poll of the journey -- has to carry both.
    """
    registry, submitted, _ = walked
    out = await registry.dispatch("plan_motion", {
        "base": "walking", "overlays": ["grab_bottle"], "walk_to": "obj:Patient"})

    assert out["success"] is True
    departure = submitted[0]["steps"][0]
    assert departure["action_id"] == "walking"
    assert [l["action_id"] for l in departure["layers"]] == ["walking", "grab_bottle"]
    assert out["walked"]["while_walking"] == ["grab_bottle"]


async def test_the_overlay_carries_on_after_the_walk_ends(walked):
    """The walk is over; the overlay is not. Committing `walking` again would stride her on the spot,
    so what is left is the same overlay over a stance -- `idle` claims no channel, so nothing about
    the arm changes. What must not happen is the reach ending because the crossing did."""
    registry, submitted, _ = walked
    out = await registry.dispatch("plan_motion", {
        "base": "walking", "overlays": ["grab_bottle"], "walk_to": "obj:Patient"})

    assert out["success"] is True
    # `idle` under a lone overlay is promoted away rather than layered -- it is `free` on every
    # channel, so it would sit underneath claiming nothing. What is left is the reach, standing.
    assert [l["action_id"] for l in submitted[-1]["steps"][0]["layers"]] == ["grab_bottle"]
    assert out["played_while_walking"]["overlays"] == ["grab_bottle"]


async def test_a_walk_to_nowhere_plays_nothing(walked):
    """A destination that cannot be reached is a refusal, not a motion committed where she stands."""
    registry, submitted, _ = walked
    out = await registry.dispatch("plan_motion", {
        "base": "walking", "walk_to": "obj:Nowhere", "then": [{"base": "typing"}],
        "sit_on": "obj:Chair"})

    assert out["success"] is False
    assert not submitted, "nothing should have been played"


async def test_the_walk_enters_on_the_frame_the_seam_picked(walked):
    """A commit cannot crossfade -- the composer hard-sets the opening step to full weight and the
    outgoing graph is gone -- so where the clip STARTS is the only lever. The seam search already
    answers it, and every step but the opening one already enters that way."""
    registry, submitted, _ = walked
    STANDING["playing"] = "idle"
    try:
        out = await registry.dispatch("move_to", {"destination": "obj:Chair"})
    finally:
        STANDING["playing"] = None

    assert out["success"] is True
    travel = submitted[0]["steps"][0]
    assert travel["action_id"] == "walking"
    assert travel["clip_start_frame"] == 3, "idle -> walking meet at walk frame 3"


async def test_naming_a_seat_is_naming_somewhere_to_walk(walked):
    """Sitting on something means being at it. Left to the model this went wrong both ways on real
    turns -- one committed the sit while she was still crossing the room, the rest walked with move_to
    and sat from the standstill it leaves her in."""
    registry, submitted, moves = walked
    out = await registry.dispatch("plan_motion", {
        "base": "walking", "then": [{"base": "typing"}], "sit_on": "obj:Chair"})

    assert out["success"] is True
    assert out["walked"]["destination"] == "obj:Chair", "no walk_to was passed; the seat is the place"
    assert [s["steps"][0]["action_id"] for s in submitted] == ["walking", "walking"]
    assert [m["stop_within_m"] for m in moves if "stop_within_m" in m] == [0.08], "right at the seat"


async def test_an_action_cannot_fight_itself(wired):
    """Measured on a live turn: the model sent `typing` twice in one call and got back "these actions
    fight over the same body parts: left_arm (typing and typing)" -- true, about nothing."""
    registry, _ = wired
    out = await registry.dispatch("plan_motion", {"base": "idle", "overlays": ["cpr", "cpr"]})
    assert out["success"] is True, out.get("error")
    # The repeat is dropped, not layered: one overlay over idle is how a lone overlay is played, so
    # this resolves to cpr itself rather than to a composition of cpr with cpr.
    assert out["retrieval"] == {"type": "full_match", "action_id": "cpr"}
    driving = [layer["action_id"] for layer in out["derived"]["layers"]]
    assert driving.count("cpr") == 1, "the repeat asked for the same layer twice"


async def test_a_plan_records_which_branch_the_library_answered_on(wired):
    """A clip that covered the whole request and a motion composed out of several are different
    claims about the system. Only the eval could tell them apart before: a live turn that assembled a
    motion existing in no clip left nothing behind saying it had."""
    registry, _ = wired

    whole = await registry.dispatch("plan_motion", {"base": "cpr"})
    assert whole["retrieval"] == {"type": "full_match", "action_id": "cpr"}

    composed = await registry.dispatch("plan_motion", {"base": "walking",
                                                      "overlays": ["giving_pills"]})
    assert composed["retrieval"]["type"] == "decompose"
    by_action = {p["action_id"]: set(p["channels"]) for p in composed["retrieval"]["parts"]}
    assert set(by_action) == {"walking", "giving_pills"}
    assert "root" in by_action["walking"], "the stepping belongs to the walk"
    assert {"left_arm", "right_arm"} <= by_action["giving_pills"]


async def test_the_verdict_is_the_one_the_eval_scores(wired, kb):
    """One function, called from both places. Two would be two definitions of what decomposing means,
    and the score would stop being a statement about what the live path does."""
    from agent import assemble as A

    registry, _ = wired
    out = await registry.dispatch("plan_motion", {"base": "walking", "overlays": ["giving_pills"]})
    assert out["retrieval"] == A.verdict(A.arbitrate("walking", ["giving_pills"], kb))


# ---- a channel with two sources, all the way to the wire ----------------------------------------

async def test_a_mix_reaches_the_engine_as_its_own_layer(wired):
    """The end of the chain the role table starts. walking takes the legs as `primary`, giving_pills
    braces them as `support`, and what leaves here is a layer masked to those legs at the share the
    table gave it -- not a leg the walk simply won."""
    registry, submitted = wired
    await registry.dispatch("plan_motion", {"base": "giving_pills", "overlays": ["walking"],
                                            "mode": "commit"})
    layers = submitted[-1]["steps"][0]["layers"]
    mixed = [l for l in layers if l.get("source") == "mix"]
    assert len(mixed) == 1
    assert mixed[0]["action_id"] == "walking"
    assert sorted(mixed[0]["channels"]) == ["left_leg", "right_leg"]
    assert mixed[0]["weight"] == pytest.approx(0.6)


async def test_the_owner_of_a_mixed_channel_does_not_also_mask_it(wired):
    """A layer masked to a channel at full weight IS winner-take-all. walking owns the legs in the
    ownership partition and must not carry them into its own mask, or the mix never happens."""
    registry, submitted = wired
    await registry.dispatch("plan_motion", {"base": "giving_pills", "overlays": ["walking"],
                                            "mode": "commit"})
    layers = submitted[-1]["steps"][0]["layers"]
    outright = [l for l in layers if l.get("source") == "overlay" and l["action_id"] == "walking"]
    assert outright and outright[0]["channels"] == ["root"]
    assert all(l.get("weight", 1.0) == 1.0 for l in layers if l.get("source") != "mix")


async def test_a_mixed_layer_enters_on_its_aligned_frame(wired):
    """Averaging two poses half a stride apart puts the legs where neither clip put them, so the
    overlay enters where the channels already agree rather than at frame 0."""
    registry, submitted = wired
    await registry.dispatch("plan_motion", {"base": "giving_pills", "overlays": ["walking"],
                                            "mode": "commit"})
    mixed = [l for l in submitted[-1]["steps"][0]["layers"] if l.get("source") == "mix"][0]
    assert mixed["clip_start_frame"] > 0
    # Reported so a mix that had to average two distant poses is visible as such.
    assert mixed["entry_apart_deg"] is not None


async def test_both_mixed_channels_of_one_clip_share_a_phase(wired):
    """Two legs at two phases is two legs stepping independently. Asked separately they want frames
    11 and 1; a clip is one performance, so they get one frame between them."""
    registry, submitted = wired
    await registry.dispatch("plan_motion", {"base": "giving_pills", "overlays": ["walking"],
                                            "mode": "commit"})
    mixed = [l for l in submitted[-1]["steps"][0]["layers"] if l.get("source") == "mix"]
    assert len({l["clip_start_frame"] for l in mixed}) == 1


async def test_a_plan_with_no_contested_channel_carries_no_weights(wired):
    """dc-walk-carry's shape. Nothing here is contested, so nothing may acquire a weight or a mix --
    this is the guard that mixing did not change every plan that came before it."""
    registry, submitted = wired
    await registry.dispatch("plan_motion", {"base": "walking", "overlays": ["grab_bottle"],
                                            "mode": "commit"})
    layers = submitted[-1]["steps"][0]["layers"]
    assert not [l for l in layers if l.get("source") == "mix"]
    assert all("weight" not in l for l in layers)


async def test_the_base_is_never_cut_and_an_overlay_is(wired):
    """Same plan, the other half of what it now carries. `grab_bottle` ends holding the bottle still
    for six frames; the overlay contributes the reach and leaves them, while `walking` -- the base,
    which sets the posture everything else hangs on -- keeps every frame it has."""
    registry, submitted = wired
    await registry.dispatch("plan_motion", {"base": "walking", "overlays": ["grab_bottle"],
                                            "mode": "commit"})
    layers = {l["action_id"]: l for l in submitted[-1]["steps"][0]["layers"]}
    assert "clip_end_frame" not in layers["walking"]
    assert layers["grab_bottle"]["clip_end_frame"] == 34
    # Not a repetition, so reaching the end holds the grasp rather than snapping back to the reach.
    assert layers["grab_bottle"]["loop_in_window"] is False


async def test_a_repeating_overlay_contributes_one_repetition(wired):
    """`cpr` is thirty chest compressions over eighteen seconds. Walking while doing them wants one
    compression, looping, not an arm that outlives the walk by seventeen seconds."""
    registry, submitted = wired
    await registry.dispatch("plan_motion", {"base": "walking", "overlays": ["cpr"],
                                            "mode": "commit"})
    layers = {l["action_id"]: l for l in submitted[-1]["steps"][0]["layers"]}
    cpr = layers["cpr"]
    assert (cpr["clip_start_frame"], cpr["clip_end_frame"]) == (0, 18)
    assert cpr["loop_in_window"] is True
    assert "repetition" in cpr["window_why"]


async def test_two_grips_the_request_named_are_refused_by_name(wired):
    """Asked to carry the bottle AND to press on the chest, there is nothing left to decide: the
    caller decided twice. The refusal names the two things, because "they conflict" does not tell the
    model which pair to break up."""
    registry, _ = wired
    out = await registry.dispatch("plan_motion", {
        "base": "cpr", "overlays": ["grab_bottle"], "mode": "commit",
        "carry": [{"object_id": "obj:AspirinBottle", "hand": "right_hand"}],
        "ik_bindings": [{"effector": "right_hand", "object_id": "obj:Patient"}]})
    assert out["success"] is False
    assert "right_hand" in out["error"]
    assert "patient_chest" in out["error"] and "aspirin_bottle" in out["error"]


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
            out["playing"] = None if up else "typing"
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
    out = await registry.dispatch("plan_motion", {"base": "walking", "walk_to": "obj:Chair",
                                                  "then": [{"base": "check_pulse"}],
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
    await registry.dispatch("plan_motion", {"base": "walking", "walk_to": "obj:Chair",
                                            "then": [{"base": "check_pulse"}],
                                            "mode": "commit"})
    rise = [e for e in timeline if e.get("steps")][0]["steps"]
    assert [s["action_id"] for s in rise] == ["typing", "walking"]
    assert rise[0]["posture"] == "seated" and rise[1]["posture"] == "standing"
    assert rise[1].get("generated"), "the posture change was scheduled as an ordinary blend"
    assert rise[1]["generated"]["support_object_id"] == "obj:Chair"


async def test_the_rise_travels_upward(seated):
    """Sitting down and standing up are the same generated descent with the ends swapped, so the one
    thing that must differ is the direction the hips go."""
    registry, timeline = seated
    await registry.dispatch("plan_motion", {"base": "walking", "walk_to": "obj:Chair",
                                            "then": [{"base": "check_pulse"}],
                                            "mode": "commit"})
    made = [e for e in timeline if e.get("steps")][0]["steps"][1]["generated"]
    assert made["target_hip_height_m"] > made["start_hip_height_m"]


async def test_getting_up_is_reported_because_nobody_asked_for_it(seated):
    """The model asked for what came after. A reply that does not mention getting up describes a
    character who was already on her feet."""
    registry, _ = seated
    out = await registry.dispatch("plan_motion", {"base": "walking", "walk_to": "obj:Chair",
                                                  "then": [{"base": "check_pulse"}],
                                                  "mode": "commit"})
    assert out["stood_up"]["landed"] is True
    assert out["stood_up"]["order"] == ["typing", "walking"]


async def test_a_standing_character_is_left_alone(wired):
    """Every plan written before this one. One query, no extra plan, nothing changed."""
    registry, submitted = wired
    out = await registry.dispatch("plan_motion", {"base": "walking", "walk_to": "obj:Chair",
                                                  "then": [{"base": "check_pulse"}],
                                                  "mode": "commit"})
    # Asserted to have SUCCEEDED first. Without this the rest passes on a refusal — nothing was
    # committed, so "no rise was committed" is vacuously true and the test proves nothing.
    assert out.get("success") is not False, out
    assert "stood_up" not in out
    plans = [entry for entry in submitted if entry.get("steps")]
    assert plans, "nothing was committed, so this test would pass however broken the code was"
    assert all(entry["steps"][0]["action_id"] != "typing" for entry in plans)


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
    await registry.dispatch("plan_motion", {"base": "cpr", "character": asked, "mode": "commit"})
    assert submitted[-1]["character"] == expected


async def test_naming_nobody_is_a_question_not_a_guess(crowded):
    """With one character, whatever was asked for was the only answer. With three, picking one would
    send the instruction to the wrong person silently."""
    registry, _ = crowded
    out = await registry.dispatch("plan_motion", {"base": "cpr", "mode": "commit"})
    assert out["success"] is False
    assert "which one" in out["error"]
    for name in ("Jill", "Dana", "Kate"):
        assert name in out["hint"]


async def test_an_unknown_name_lists_who_is_actually_here(crowded):
    registry, _ = crowded
    out = await registry.dispatch("plan_motion", {"base": "cpr", "character": "Maria",
                                                  "mode": "commit"})
    assert out["success"] is False
    assert "Maria" in out["error"]
    assert "Jill" in out["hint"] and "Dana" in out["hint"] and "Kate" in out["hint"]


async def test_one_character_still_answers_to_anything(wired):
    """The behaviour that removed a measured round trip: with nobody else to confuse her with, a wrong
    name is not a question."""
    registry, submitted = wired
    await registry.dispatch("plan_motion", {"base": "cpr", "character": "nurse", "mode": "commit"})
    assert submitted[-1]["character"] == "chr:CPRNurse"
