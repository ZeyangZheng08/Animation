"""Channel arbitration, checked against the eval set's own ground truth.

The point of these is that the partition rule is DERIVED, not asserted. If someone later changes what a
base or an overlay claims, these fail against `retrieval_eval_set.json` rather than against a number
somebody typed into a test.
"""
import json
import os

import pytest

import paths
from agent import assemble as A
from agent.kbindex import ANATOMICAL, KBIndex, ROLE_PRIORITY


@pytest.fixture(scope="module")
def kb():
    return KBIndex.load()


@pytest.fixture(scope="module")
def eval_cases():
    with open(os.path.join(paths.KB_DIR, "retrieval_eval_set.json"), encoding="utf-8") as f:
        return json.load(f)["cases"]


def decompose_cases(cases):
    return [c for c in cases if c["expected"]["type"] == "decompose"]


def test_the_eval_set_still_has_the_two_decompose_cases(eval_cases):
    ids = [c["id"] for c in decompose_cases(eval_cases)]
    assert ids == ["dc-walk-carry", "dc-givepills-gaze"]


def test_arbitration_reproduces_the_ground_truth_partition(kb, eval_cases):
    for case in decompose_cases(eval_cases):
        expected = case["expected"]
        assembly = A.decompose([p["action_id"] for p in expected["parts"]], kb)

        for part in expected["parts"]:
            want = sorted(c for c in part["channels"] if c != "root")
            got = sorted(c for c in assembly.channels_of(part["action_id"]) if c != "root")
            assert got == want, "%s / %s" % (case["id"], part["action_id"])

        assert sorted(assembly.free_channels) == sorted(expected["free_channels"]), case["id"]
        assert not assembly.conflicts, case["id"]


def test_root_goes_to_whichever_part_owns_the_legs(kb, eval_cases):
    """Ground truth names root only in dc-walk-carry; it is asserted separately from the partition.

    Not to the part whose root channel is dynamic: since ADR 0011 the root counts turning, so four of
    the eight actions read dynamic and the in-place walk reads the lowest of them. Where a body goes
    is decided by what its legs did."""
    walk_carry = next(c for c in eval_cases if c["id"] == "dc-walk-carry")
    assembly = A.decompose([p["action_id"] for p in walk_carry["expected"]["parts"]], kb)
    assert assembly.root_owner == "walking"
    assert "root" in assembly.channels_of("walking")
    # The rule it replaced would refuse this partition: grab_bottle's root is dynamic too (0.0467 to
    # the walk's 0.0382), on 6.6 degrees of yaw while reaching for the bottle.
    assert kb.channels("grab_bottle")["root"]["state"] == "dynamic"
    assert not assembly.conflicts

    gaze = next(c for c in eval_cases if c["id"] == "dc-givepills-gaze")
    assembly = A.decompose([p["action_id"] for p in gaze["expected"]["parts"]], kb)
    assert assembly.root_owner == "giving_pills"   # it owns its own legs, as the base

    # Legs nobody claims leave the root with the base rather than inventing an owner. Neither of
    # these two touches the lower body, and neither is promoted away, so the base keeps it.
    assembly = A.arbitrate("bvm", ["check_pulse"], kb)
    assert "left_leg" in assembly.free_channels and "right_leg" in assembly.free_channels
    assert assembly.root_owner == "bvm" and not assembly.conflicts

    # `idle` claims nothing anywhere, so arbitrate promotes it away and grab_bottle becomes the
    # base of its own motion. The root still lands on the base, which is now the former overlay.
    assembly = A.decompose(["idle", "grab_bottle"], kb)
    assert assembly.base == "grab_bottle" and assembly.root_owner == "grab_bottle"


def test_both_halves_of_the_claim_rule_are_load_bearing(kb):
    """Each half is falsified by the other case — this is why the rule is asymmetric."""
    grab = kb.channels("grab_bottle")
    give = kb.channels("giving_pills")

    # An overlay claiming `support` too would take grab_bottle's torso, which the ground truth leaves free
    assert grab["torso"]["role"] == "support"
    # A base claiming only `primary` would drop giving_pills' torso and legs, which the ground truth owns
    assert give["torso"]["role"] == "support"
    assert give["left_leg"]["role"] == "support"


def test_stabilizer_channels_are_claimed_by_nobody(kb):
    """This is what makes a carry able to override a walking arm swing."""
    walking = kb.channels("walking")
    assert walking["right_arm"]["role"] == "stabilizer"
    assembly = A.decompose(["walking", "grab_bottle"], kb)
    assert "right_arm" in assembly.channels_of("grab_bottle")
    assert "right_arm" not in assembly.channels_of("walking")


def test_equal_claims_are_reported_not_silently_resolved(kb):
    """giving_pills and cpr both drive both hands as `primary`, each holding something different.

    The hand goes whole to one of them -- half a grip is no grip -- and the other loses its OBJECT
    rather than the whole plan. Reported either way: what must never happen is the pills quietly
    ceasing to exist.
    """
    assembly = A.arbitrate("idle", ["giving_pills", "cpr"], kb)
    assert assembly.ok
    dropped = {d.channel: d for d in assembly.dropped}
    assert {"left_hand", "right_hand"} <= set(dropped)
    for drop in dropped.values():
        assert {drop.action_id, drop.kept_action_id} == {"giving_pills", "cpr"}
        assert drop.object and drop.kept_object and drop.object != drop.kept_object


def test_a_grip_the_request_named_is_the_one_that_is_kept(kb):
    """The only thing that outranks role priority here. Asked to hold the pills, the hand that holds
    the pills is the one grounded, and cpr's chest contact is what comes off."""
    assembly = A.arbitrate("cpr", ["giving_pills"], kb, named_objects=["pills"])
    assert assembly.ok
    right = next(d for d in assembly.dropped if d.channel == "right_hand")
    assert right.action_id == "cpr" and right.object == "patient_chest"
    assert right.kept_action_id == "giving_pills" and right.kept_object == "pills"
    assert "right_hand" in assembly.channels_of("giving_pills")


def test_naming_both_objects_is_still_a_conflict(kb):
    """Dropping either would be doing subtraction on the caller's behalf: it asked for both by name.
    So this one stays a refusal, and names the two things it is about."""
    assembly = A.arbitrate("cpr", ["giving_pills"], kb, named_objects=["pills", "patient_chest"])
    assert not assembly.ok
    right = next(c for c in assembly.conflicts if c.channel == "right_hand")
    assert set(right.action_ids) == {"cpr", "giving_pills"}
    assert "patient_chest" in right.detail and "pills" in right.detail


def test_a_lone_overlay_is_promoted_rather_than_stacked_on_idle(kb):
    """Slipping idle underneath would claim nothing (it is `free` everywhere) while looking like it
    worked, so a sole overlay becomes the base and claims its support channels too."""
    assembly = A.decompose(["cpr"], kb)
    assert assembly.base == "cpr"
    assert "torso" in assembly.channels_of("cpr")      # primary
    assert "left_leg" in assembly.channels_of("cpr")   # support — only a base claims these


def test_two_bases_are_rejected(kb):
    with pytest.raises(ValueError, match="more than one base"):
        A.decompose(["walking", "idle"], kb)


def test_every_channel_is_owned_at_most_once(kb):
    for parts in (["walking", "grab_bottle"], ["giving_pills"], ["idle", "grab_bottle"],
                  ["walking", "check_pulse"]):
        assembly = A.decompose(parts, kb)
        owned = [c for _, chans in assembly.layers for c in chans]
        assert len(owned) == len(set(owned)), parts
        assert set(assembly.free_channels).isdisjoint(owned), parts
        assert set(assembly.free_channels) | set(owned) >= set(ANATOMICAL), parts


# ---- one channel, two sources -------------------------------------------------------------------

def test_a_contested_channel_is_mixed_rather_than_won(kb):
    """The change this module exists for. walking drives the legs as `primary` and giving_pills braces
    them as `support`; the brace used to be discarded outright because primary outranks support."""
    assembly = A.arbitrate("giving_pills", ["walking"], kb)
    assert assembly.ok
    mixed = {m.channel: dict(m.shares) for m in assembly.shared}
    assert set(mixed) == {"left_leg", "right_leg"}
    for channel, shares in mixed.items():
        assert shares["walking"] == pytest.approx(0.6), channel
        assert shares["giving_pills"] == pytest.approx(0.4), channel
        assert sum(shares.values()) == pytest.approx(1.0), channel


def test_the_shares_are_the_role_table_and_not_a_number_someone_chose(kb):
    """0.6/0.4 is ROLE_PRIORITY primary(3) against support(2), normalised. Asserted against the table
    rather than against the literals, so changing the table fails here instead of drifting."""
    assembly = A.arbitrate("giving_pills", ["walking"], kb)
    shares = dict(assembly.shared[0].shares)
    primary, support = ROLE_PRIORITY["primary"], ROLE_PRIORITY["support"]
    assert shares["walking"] == pytest.approx(primary / float(primary + support))
    assert shares["giving_pills"] == pytest.approx(support / float(primary + support))


def test_two_equal_claims_split_the_channel_evenly(kb):
    """check_pulse and grab_bottle are both `primary` on right_arm and neither holds anything THERE --
    both grips are on right_hand, which is a different channel and stays a conflict."""
    assembly = A.arbitrate("check_pulse", ["grab_bottle"], kb)
    arm = [m for m in assembly.shared if m.channel == "right_arm"]
    assert len(arm) == 1
    assert sorted(dict(arm[0].shares).values()) == [pytest.approx(0.5), pytest.approx(0.5)]


def test_two_grips_on_one_channel_are_never_mixed(kb):
    """Half a hand on a patient's chest and half on a pill bottle satisfies neither grip, so a
    contested hand is never SHARED however it is resolved. A hand is a shape, not an axis -- which is
    why the mix that arms, torso and legs get stops at the wrist."""
    assembly = A.arbitrate("cpr", ["giving_pills"], kb)
    assert not [m for m in assembly.shared if m.channel in ("left_hand", "right_hand")]
    for channel in ("left_hand", "right_hand"):
        drivers = [aid for aid, chans in assembly.layers if channel in chans]
        assert len(drivers) == 1


def test_the_base_keeps_a_contested_hand_when_nothing_was_named(kb):
    """No request, no role difference: both are `primary` and both hold something. The base takes it,
    which is the same tiebreak a mix uses and for the same reason -- the base sets the context."""
    assembly = A.arbitrate("cpr", ["giving_pills"], kb)
    assert "right_hand" in assembly.channels_of("cpr")
    right = next(d for d in assembly.dropped if d.channel == "right_hand")
    assert right.action_id == "giving_pills" and right.object == "pills"


def test_a_single_grip_takes_the_channel_whole(kb):
    """A contact is a hard constraint and a share is not, so a channel one side grips and the other
    does not is not a mix -- it goes to the side with something to hold."""
    assembly = A.arbitrate("walking", ["grab_bottle"], kb)
    assert kb.channels("grab_bottle")["right_hand"]["contact"].startswith("object:")
    assert "right_hand" in assembly.channels_of("grab_bottle")
    assert not [m for m in assembly.shared if m.channel == "right_hand"]


def test_root_is_never_mixed(kb):
    """Two root motions added together are not a motion. Root is decided by `state == "dynamic"`, which
    is a different question from the role partition and stays that way."""
    for base, overlays in (("giving_pills", ["walking"]), ("cpr", ["walking"]),
                           ("check_pulse", ["grab_bottle"])):
        assembly = A.arbitrate(base, overlays, kb)
        assert "root" not in {m.channel for m in assembly.shared}


def test_an_action_that_only_holds_a_share_still_gets_a_layer(kb):
    """grab_bottle owns nothing outright against check_pulse -- it holds 0.5 of right_arm and loses
    right_hand to the conflict. Dropping it from `layers` is how a mix silently becomes a winner."""
    assembly = A.arbitrate("check_pulse", ["grab_bottle"], kb)
    assert assembly.channels_of("grab_bottle") == []
    assert "grab_bottle" in [aid for aid, _ in assembly.layers]
    assert assembly.share_of("grab_bottle", "right_arm") == pytest.approx(0.5)
    assert assembly.share_of("grab_bottle", "left_leg") == 0.0


def test_mixing_leaves_the_eval_ground_truth_alone(kb, eval_cases):
    """Neither decompose case contests a channel, so neither may acquire a mix. This is the guard on
    the whole change: `layers` stays an ownership partition and the eval keeps scoring it by set
    equality."""
    for case in eval_cases:
        expected = case["expected"]
        if expected["type"] != "decompose":
            continue
        assembly = A.decompose([p["action_id"] for p in expected["parts"]], kb)
        assert assembly.shared == [], case["id"]


def test_a_mix_is_reported_as_a_decomposition(kb):
    """One body part driven by two retrieved clips at once is the strongest decomposition there is, so
    the verdict says so rather than leaving it to be inferred from the partition."""
    assembly = A.arbitrate("giving_pills", ["walking"], kb)
    answer = A.verdict(assembly)
    assert answer["type"] == A.DECOMPOSE
    assert {m["channel"] for m in answer["shared"]} == {"left_leg", "right_leg"}
