"""Channel arbitration, against the eval set's own ground truth.

WHAT THESE STOPPED BEING ABLE TO ASSERT. Through v3 the partition was DERIVED from a `role` label on
every channel, and the tests here checked that the derivation reproduced `retrieval_eval_set.json`
without anybody typing the answer into a test. motionkb/v4 deletes `role` (ADR 0022): which part of a
clip matters is a fact about the task, not about the clip, so the assignment now ARRIVES from the
agent's plan.

So the subject changed and the fixture did not. The ground-truth channel sets are still what these
run on -- they are now the INPUT rather than the expected output, and what is checked is that a given
assignment is carried through unaltered, that the root follows the legs, that a channel two parts name
is halved, and that the assignments a plan cannot mean are refused by name. The eval file is still the
source of the channel lists, so a case that changes there changes here.
"""
import json

import pytest

import paths
from agent import assemble as A
from agent.kbindex import ANATOMICAL, KBIndex


@pytest.fixture(scope="module")
def kb():
    return KBIndex.load()


@pytest.fixture(scope="module")
def eval_cases():
    with open(paths.EVAL_SET, encoding="utf-8") as f:
        return json.load(f)["cases"]


def decompose_cases(cases):
    return [c for c in cases if c["expected"]["type"] == "decompose"]


def ground_truth(case):
    """The case's parts as the plan would write them: {action_id: [anatomical channels]}.

    `root` is stripped because it is never assigned by a plan -- it follows the legs, which is the
    thing `test_root_goes_to_whichever_part_owns_the_legs` is about.
    """
    return {part["action_id"]: [c for c in part["channels"] if c != "root"]
            for part in case["expected"]["parts"]}


def test_the_eval_set_still_has_the_two_decompose_cases(eval_cases):
    ids = [c["id"] for c in decompose_cases(eval_cases)]
    assert ids == ["dc-walk-carry", "dc-givepills-gaze"]


def test_a_given_partition_is_carried_through_unaltered(kb, eval_cases):
    """The plan says who drives what; this module must hand back exactly that and nothing else.

    It used to be a test that the RULE reproduced these sets. There is no rule left to test -- the
    sets are the input now -- so what is worth checking is that nothing is quietly added, dropped or
    reassigned on the way through, and that everything nobody named comes back as free.
    """
    for case in decompose_cases(eval_cases):
        expected = case["expected"]
        assembly = A.decompose(ground_truth(case), kb)

        for part in expected["parts"]:
            want = sorted(c for c in part["channels"] if c != "root")
            got = sorted(c for c in assembly.channels_of(part["action_id"]) if c != "root")
            assert got == want, "%s / %s" % (case["id"], part["action_id"])

        assert sorted(assembly.free_channels) == sorted(expected["free_channels"]), case["id"]
        assert not assembly.conflicts, case["id"]


def test_a_partition_that_leaves_a_part_out_is_rejected_rather_than_guessed(kb):
    """The one thing v4 may not do is invent the channel list it stopped deriving.

    An overlay with no channels is not "an overlay over everything": an empty mask plays FULL BODY at
    full weight in the engine, so accepting one would replace the whole body with the overlay, and
    dropping it would answer a request nobody made.
    """
    with pytest.raises(ValueError, match="names no channels"):
        A.arbitrate("walking", ["grab_bottle"], kb)
    with pytest.raises(ValueError, match="names no channels"):
        A.arbitrate("walking", [{"action_id": "grab_bottle", "channels": []}], kb)
    with pytest.raises(ValueError, match="do not exist"):
        A.arbitrate("walking", [("grab_bottle", ["elbow"])], kb)
    with pytest.raises(ValueError, match="do not exist"):
        A.arbitrate("walking", [("grab_bottle", ["right_arm"])], kb, base_channels=["elbow"])


def test_both_ways_of_writing_an_overlay_mean_the_same_thing(kb):
    """The plan schema emits dicts; the eval arm and the probes write pairs. One normalisation, so a
    partition cannot depend on which caller built it."""
    pairs = A.arbitrate("walking", [("grab_bottle", ["right_arm", "right_hand"])], kb)
    dicts = A.arbitrate("walking",
                        [{"action_id": "grab_bottle", "channels": ["right_hand", "right_arm"]}], kb)
    assert pairs.layers == dicts.layers
    assert pairs.free_channels == dicts.free_channels


def test_root_goes_to_whichever_part_owns_the_legs(kb, eval_cases):
    """Ground truth names root only in dc-walk-carry; it is asserted separately from the partition.

    Not to the part whose root channel is dynamic: since ADR 0011 the root counts turning, so four of
    the eight actions read dynamic and the in-place walk reads the lowest of them. Where a body goes
    is decided by what its legs did."""
    walk_carry = next(c for c in eval_cases if c["id"] == "dc-walk-carry")
    assembly = A.decompose(ground_truth(walk_carry), kb)
    assert assembly.root_owner == "walking"
    assert "root" in assembly.channels_of("walking")
    # The rule it replaced would refuse this partition: grab_bottle's root is dynamic too (0.0467 to
    # the walk's 0.0382), on 6.6 degrees of yaw while reaching for the bottle.
    assert kb.channels("grab_bottle")["root"]["state"] == "dynamic"
    assert not assembly.conflicts

    gaze = next(c for c in eval_cases if c["id"] == "dc-givepills-gaze")
    assembly = A.decompose(ground_truth(gaze), kb)
    assert assembly.root_owner == "giving_pills"   # it drives its own legs, as the base

    # Legs nobody claims leave the root with the base rather than inventing an owner. Neither of
    # these two is asked for the lower body, so the base keeps it.
    assembly = A.arbitrate("bvm", [("check_pulse", ["left_arm", "right_arm"])], kb)
    assert "left_leg" in assembly.free_channels and "right_leg" in assembly.free_channels
    assert assembly.root_owner == "bvm" and not assembly.conflicts

    # A base that claims nothing still holds the root, because it is layer 0 and it is what the legs
    # are playing. This is the shape every lone-overlay plan takes.
    assembly = A.arbitrate("idle", [("grab_bottle", ["right_arm", "right_hand"])], kb)
    assert assembly.base == "idle" and assembly.root_owner == "idle"


def test_two_parts_driving_one_leg_each_is_a_conflict_not_half_a_root(kb):
    """Root is never split, so a lower body the plan cut down the middle has no owner to give it to.
    Reported rather than resolved: which of the two decides where she goes is a real question."""
    assembly = A.arbitrate("walking", [("giving_pills", ["right_leg"])], kb,
                           base_channels=["left_leg"])
    assert not assembly.ok
    root = next(c for c in assembly.conflicts if c.channel == "root")
    assert set(root.action_ids) == {"walking", "giving_pills"}
    assert root.reason == "driving one leg each"


def test_the_base_claims_nothing_unless_it_says_so(kb):
    """Claiming is not playing. Layer 0 animates the whole body whatever this list holds, so an empty
    `base_channels` is not a base that has stopped moving -- it is a base that will not contend."""
    loose = A.arbitrate("walking", [("grab_bottle", ["right_arm"])], kb)
    assert loose.channels_of("grab_bottle") == ["right_arm"]
    assert loose.shared == []

    reserved = A.arbitrate("walking", [("grab_bottle", ["right_arm"])], kb,
                           base_channels=["right_arm"])
    assert [m.channel for m in reserved.shared] == ["right_arm"]


def test_a_single_part_is_its_own_base(kb):
    """A lone action is not an overlay looking for something to sit on. v3 slipped `idle` underneath
    and then promoted it away again when it turned out to claim nothing; there is nothing to promote
    now, because the plan named one part and that part is the whole motion."""
    channels = ["torso", "left_arm", "right_arm", "left_hand", "right_hand"]
    assembly = A.decompose({"cpr": channels}, kb)
    assert assembly.base == "cpr"
    assert sorted(c for c in assembly.channels_of("cpr") if c != "root") == sorted(channels)
    assert assembly.root_owner == "cpr"


def test_the_base_can_be_named_rather_than_inferred(kb):
    """Whichever part drives a leg is the default because that is what sets the stance, but a caller
    that knows better says so. Under v3 this was read off `composability.base_or_overlay`, a stored
    label v4 deletes -- whether a clip is foundational or grafted on is a fact about the combination."""
    parts = {"walking": ["left_leg", "right_leg"], "grab_bottle": ["right_arm", "right_hand"]}
    assert A.decompose(parts, kb).base == "walking"
    assert A.decompose(parts, kb, base="grab_bottle").base == "grab_bottle"


def test_every_channel_is_owned_at_most_once(kb):
    for parts in ({"walking": ["left_leg", "right_leg"],
                   "grab_bottle": ["right_arm", "right_hand"]},
                  {"giving_pills": ["torso", "left_arm", "right_arm"]},
                  {"idle": [], "grab_bottle": ["right_arm", "right_hand"]},
                  {"walking": ["left_leg", "right_leg"], "check_pulse": ["left_arm", "right_arm"]}):
        assembly = A.decompose(parts, kb)
        owned = [c for _, chans in assembly.layers for c in chans]
        assert len(owned) == len(set(owned)), parts
        assert set(assembly.free_channels).isdisjoint(owned), parts
        assert set(assembly.free_channels) | set(owned) >= set(ANATOMICAL), parts


def test_an_action_cannot_fight_itself_for_a_body_part(kb):
    """The same action named twice asks for one layer twice, and the second request says nothing the
    first did not. Measured on a live turn: `typing` sent twice came back as "typing and typing fight
    over left_arm" -- true, about nothing."""
    assembly = A.arbitrate("idle", [("cpr", ["torso", "left_arm"]), ("cpr", ["right_arm"])], kb)
    assert assembly.ok
    assert [aid for aid, _ in assembly.layers].count("cpr") == 1
    assert sorted(assembly.channels_of("cpr")) == ["left_arm", "right_arm", "torso"]


# ---- one channel, two sources -------------------------------------------------------------------

def test_a_contested_channel_is_mixed_rather_than_won(kb):
    """The module exists for this. Two parts asked for one body part get half of it each, and the
    request is answered rather than half-discarded."""
    assembly = A.arbitrate("giving_pills", [("walking", ["left_leg", "right_leg"])], kb,
                           base_channels=["left_leg", "right_leg"])
    assert assembly.ok
    mixed = {m.channel: dict(m.shares) for m in assembly.shared}
    assert set(mixed) == {"left_leg", "right_leg"}
    for channel, shares in mixed.items():
        assert shares["walking"] == pytest.approx(0.5), channel
        assert shares["giving_pills"] == pytest.approx(0.5), channel
        assert sum(shares.values()) == pytest.approx(1.0), channel


def test_the_shares_are_equal_because_nothing_is_left_to_rank_them_by(kb):
    """v3 normalised ROLE_PRIORITY and got 0.6/0.4 for primary against support. That number was
    defensible exactly as long as the ranking behind it existed; v4 deletes `role` (ADR 0022), and
    inventing a replacement weight here would be the plan emitting a motion numeric by proxy. Half
    each is the honest reading of "the agent asked for both of these here", and it stays half each
    however many parts contend."""
    two = A.arbitrate("giving_pills", [("walking", ["left_leg"])], kb, base_channels=["left_leg"])
    assert sorted(dict(two.shared[0].shares).values()) == [pytest.approx(0.5), pytest.approx(0.5)]

    three = A.arbitrate("giving_pills", [("walking", ["left_leg"]), ("cpr", ["left_leg"])], kb,
                        base_channels=["left_leg"])
    shares = dict(three.shared[0].shares)
    assert len(shares) == 3
    assert all(s == pytest.approx(1 / 3.0) for s in shares.values())


def test_a_pinned_channel_is_refused_rather_than_halved(kb):
    """Half a hand shaped for a pill bottle and half shaped for a patient's chest grips neither, and
    an IK constraint then drags the wrist of a pose that was never a grip. A hand is a shape, not an
    axis. So where the plan pins an effector -- through `carry` or `ik_bindings` -- a contested
    channel is named back to the caller instead of blended."""
    assembly = A.arbitrate("cpr", [("giving_pills", ["right_hand"])], kb,
                           base_channels=["right_hand"], pinned_channels=["right_hand"])
    assert not assembly.ok
    assert not [m for m in assembly.shared if m.channel == "right_hand"]
    right = next(c for c in assembly.conflicts if c.channel == "right_hand")
    assert set(right.action_ids) == {"cpr", "giving_pills"}
    assert right.reason == "driving a channel the plan pins to an object"
    assert "right_hand" in right.why() and "holds neither" in right.why()


def test_an_unpinned_hand_is_mixable_like_anything_else(kb):
    """The refusal above is about the PIN, not about the hand. Nothing in a v4 record says what a hand
    holds, so a plan that pins nothing has asked for two motions on one hand and gets both."""
    assembly = A.arbitrate("cpr", [("giving_pills", ["right_hand"])], kb,
                           base_channels=["right_hand"])
    assert assembly.ok
    assert [m.channel for m in assembly.shared] == ["right_hand"]


def test_root_is_never_mixed(kb):
    """Two root motions added together are not a motion, so the channel goes whole to one part or to
    nobody -- including when the legs that decide it are themselves shared."""
    for base, overlays, claimed in (
            ("giving_pills", [("walking", ["left_leg", "right_leg"])], ["left_leg", "right_leg"]),
            ("cpr", [("walking", ["left_leg", "right_leg"])], ["left_leg", "right_leg"]),
            ("check_pulse", [("grab_bottle", ["right_arm"])], ["right_arm"])):
        assembly = A.arbitrate(base, overlays, kb, base_channels=claimed)
        assert "root" not in {m.channel for m in assembly.shared}
        assert assembly.root_owner is not None


def test_an_action_that_only_holds_a_share_still_gets_a_layer(kb):
    """grab_bottle owns nothing outright here -- it holds half of right_arm and nothing else. Dropping
    it from `layers` is how a mix silently becomes a winner-take-all, one layer short and looking
    correct."""
    assembly = A.arbitrate("check_pulse", [("grab_bottle", ["right_arm"])], kb,
                           base_channels=["right_arm"])
    assert assembly.channels_of("grab_bottle") == []
    assert "grab_bottle" in [aid for aid, _ in assembly.layers]
    assert assembly.share_of("grab_bottle", "right_arm") == pytest.approx(0.5)
    assert assembly.share_of("grab_bottle", "left_leg") == 0.0


def test_mixing_leaves_the_eval_ground_truth_alone(kb, eval_cases):
    """Neither decompose case contests a channel, so neither may acquire a mix. This is the guard on
    the whole mechanism: `layers` stays an ownership partition and the eval keeps scoring it by set
    equality."""
    for case in decompose_cases(eval_cases):
        assembly = A.decompose(ground_truth(case), kb)
        assert assembly.shared == [], case["id"]


def test_a_mix_is_reported_as_a_decomposition(kb):
    """One body part driven by two retrieved clips at once is the strongest decomposition there is, so
    the verdict says so rather than leaving it to be inferred from the partition."""
    assembly = A.arbitrate("giving_pills", [("walking", ["left_leg", "right_leg"])], kb,
                           base_channels=["left_leg", "right_leg"])
    answer = A.verdict(assembly)
    assert answer["type"] == A.DECOMPOSE
    assert {m["channel"] for m in answer["shared"]} == {"left_leg", "right_leg"}


def test_a_base_that_claims_nothing_is_not_part_of_the_composition(kb):
    """Naming a posture-setting action under a lone overlay is how a single overlay is played at all:
    something has to hold the rest of the body up. It contributed no body part, so scoring it as a
    composition would penalise a plan for being more correct than the ground truth, which calls
    `giving_pills` alone a full match. Read off the claims rather than off the id -- v3 hardcoded
    `"idle"` here, which stopped being true the moment any other action was used as a stance."""
    assembly = A.arbitrate("idle", [("grab_bottle", ["right_arm", "right_hand"])], kb)
    assert A.verdict(assembly) == {"type": A.FULL_MATCH, "action_id": "grab_bottle"}

    # Any standing action can play that part, and the answer has to be the same one.
    assembly = A.arbitrate("walking", [("grab_bottle", ["right_arm", "right_hand"])], kb)
    assert A.verdict(assembly) == {"type": A.FULL_MATCH, "action_id": "grab_bottle"}


def test_a_gaze_is_a_decomposition_that_still_names_the_action_it_freed_the_head_from(kb):
    """`dc-givepills-gaze`: one retrieved clip plus a head solved by IK rather than retrieved. That
    is the FK-retrieval / IK-goal split, so the ground truth calls it a decompose even though only
    one action was fetched.

    And the part has to be NAMED. A base that claims nothing is normally dropped from `contributing`
    -- a posture-holder under a lone overlay is not a second motion -- but when the gaze is the only
    reason this is a decomposition at all, dropping it leaves "decomposed into nothing", which is not
    what happened: the action is there, with its head freed."""
    assembly = A.arbitrate("giving_pills", [], kb)
    answer = A.verdict(assembly, gaze_at="obj:MonitorVitals")
    assert answer["type"] == A.DECOMPOSE
    assert [p["action_id"] for p in answer["parts"]] == ["giving_pills"]
    assert "head" in answer["free_channels"]
