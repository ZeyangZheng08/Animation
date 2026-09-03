"""The ids in `tests/corpus.py` still have the properties they were chosen for.

WHY THIS EXISTS. Every id in that file is a bet about a record: that `WALK` moves both legs, that
`SEATED` is seated at both ends, that `POSE` animates nothing. If the corpus changes under one of
them, the tests that read the property fail somewhere else entirely — a channel partition test
failing because a clip stopped moving its root is a puzzle, and this is the same fact as a sentence.

It is also where the constants are documented against reality rather than against a comment.
"""
import pytest

from agent import segments as S
from agent.kbindex import KBIndex, posture_of, posture_span_of
from tests import corpus as C


@pytest.fixture(scope="module")
def kb():
    return KBIndex.load()


def test_every_named_id_is_in_the_library(kb):
    missing = [a for a in set(C.SMALL_STORE + C.STANDING) if a not in kb.actions]
    assert not missing, "tests/corpus.py names records the library does not hold: %s" % missing


def test_the_walk_is_a_gait_cycle_and_the_pose_is_not(kb):
    """The pair is the point. `mx_Walking` is the obvious id to reach for and animates nothing, which
    is why the primitive check measures rather than trusting the name."""
    walk = kb.record(C.WALK)
    channels = walk["channels"]
    assert walk["extraction"]["sampled_frames"] >= 20
    for part in ("root", "left_leg", "right_leg"):
        assert channels[part]["state_label"] == "dynamic"
    assert posture_of(walk) == "standing"

    pose = kb.record(C.POSE)
    assert pose["extraction"]["sampled_frames"] == 2
    assert all(ch["state_label"] == "static" for ch in pose["channels"].values())


def test_the_idle_is_still_enough_to_stand_in(kb):
    idle = kb.record(C.IDLE)
    assert posture_of(idle) == "standing"
    assert idle["channels"]["root"]["state_label"] == "static"
    busiest = max(ch.get("motion_magnitude") or 0.0 for ch in idle["channels"].values())
    assert busiest < 0.15


@pytest.mark.parametrize("action_id,posture", [("SEATED", "seated"), ("FLOOR", "floor"),
                                               ("OTHER", "other"), ("WALK", "standing")])
def test_one_clip_per_coarse_posture(kb, action_id, posture):
    assert posture_of(kb.record(getattr(C, action_id))) == posture


@pytest.mark.parametrize("action_id,span", [("SIT_DOWN", ("standing", "seated")),
                                            ("STAND_UP", ("seated", "standing")),
                                            ("FALL", ("standing", "floor")),
                                            ("SEATED", ("seated", "seated"))])
def test_the_crossing_clips_cross(kb, action_id, span):
    assert posture_span_of(kb.record(getattr(C, action_id))) == span


def test_the_cyclic_clip_has_a_measured_period():
    """`temporal_intent: repeat` is only honest where a period was measured, and one clip in the
    fixture has to have one for that to be testable at all. Most Mixamo clips do not: a walk IS one
    cycle, so the period search finds nothing to repeat inside it."""
    table = S.read_table() or {}
    periods = [seg["channel"] for seg in table.get(C.CYCLIC) or [] if seg["cycle_frames"]]
    assert periods, "%s no longer has a repeating channel" % C.CYCLIC


def test_everything_named_standing_is_standing(kb):
    """A test that combines two actions needs both in one posture, or it is testing the posture gate.
    This is the list those substitutions draw from."""
    for action_id in C.STANDING:
        assert posture_of(kb.record(action_id)) == "standing", action_id
        assert posture_span_of(kb.record(action_id)) == ("standing", "standing"), action_id


def test_the_work_clip_uses_both_hands(kb):
    """The `base that DOES something` role: a plan whose base is not a walk and not a stance."""
    channels = kb.record(C.WORK)["channels"]
    for part in ("left_arm", "right_arm", "left_hand", "right_hand"):
        assert channels[part]["state_label"] == "dynamic", part


def test_the_grab_reaches_with_one_hand_over_a_moving_body(kb):
    """What the partition tests need from it: a dynamic root, so root ownership is a real question,
    and one hand that moves without the other."""
    channels = kb.record(C.GRAB)["channels"]
    assert channels["root"]["state_label"] == "dynamic"
    assert channels["right_hand"]["state_label"] == "dynamic"
    assert channels["left_hand"]["state_label"] == "static"
