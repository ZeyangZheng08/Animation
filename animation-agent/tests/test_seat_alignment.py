"""Placing a character so that a retrieved posture transition finishes on the seat.

THE PROBLEM, IN ONE MEASUREMENT. `mx_Standing_To_Sitting_Transition` moves the hips 0.446 m BACKWARD
over its 67 frames, because that is how sitting down works: you step back and lower yourself onto
what is behind you. Played from where a walk stops -- in front of the chair, facing it -- it finishes
with the hips 0.446 m in front of the seat. The generated descent that preceded it hid this by
driving the hips onto the seat directly rather than playing anything, so the failure only appeared
once real transition clips became reachable.

WHAT IS TESTED HERE. The arithmetic that undoes it, as a pure function; that the sidecar carries the
displacement it reads; that the wire says which steps drive the transform; and that the protocol's
three copies of the version agree, since applying root motion is what the bump is for.
"""
import json
import math
import re

import pytest

import build_posture
import paths
import terminal
from agent import protocol as P
from agent.kbindex import KBIndex, root_travel_of
from agent.tools.scene import bearing_deg, heading_error_deg, standing_point_for
from tests import corpus as C


@pytest.fixture(scope="module")
def kb():
    return KBIndex.load()


def apply_travel(stand, facing_deg, travel):
    """Where the hips end up: the standing point plus the clip's own displacement, turned into the
    direction she is facing. The inverse of what `standing_point_for` solves."""
    rad = math.radians(facing_deg)
    cos, sin = math.cos(rad), math.sin(rad)
    return (stand[0] + travel[0] * cos + travel[1] * sin,
            stand[1] - travel[0] * sin + travel[1] * cos)


# ---- the arithmetic --------------------------------------------------------------------------

@pytest.mark.parametrize("seat,approach", [
    ((0.0, 0.0), (0.0, 3.0)),          # approached from +Z
    ((0.0, 0.0), (0.0, -3.0)),         # from -Z
    ((0.0, 0.0), (2.5, 0.0)),          # from +X
    ((-1.4, 5.2), (0.9, 2.0)),         # a real-ish chair, approached diagonally
    ((3.0, -2.0), (3.0, -7.0)),
])
def test_the_standing_point_puts_the_hips_on_the_seat(seat, approach):
    """The whole claim, checked by running the clip forward again: stand there, face that way, apply
    the travel, and the hips land on the seat. Exactly, not approximately -- this is arithmetic."""
    travel = (0.0364, -0.4446)
    x, z, facing = standing_point_for(seat, approach, travel)
    landed = apply_travel((x, z), facing, travel)
    assert landed[0] == pytest.approx(seat[0], abs=1e-6)
    assert landed[1] == pytest.approx(seat[1], abs=1e-6)


def test_she_stands_with_her_back_to_the_seat():
    """Sitting down means putting your back to the chair. Facing it and playing a clip that steps
    backwards walks her away from it."""
    seat, approach = (0.0, 0.0), (0.0, 4.0)
    x, z, facing = standing_point_for(seat, approach, (0.0364, -0.4446))
    # She stands between the seat and where she came from...
    assert 0.0 < z < 4.0
    # ...and faces along +Z, which is away from the seat and back the way she came.
    assert facing == pytest.approx(0.0, abs=1e-6)


def test_the_standing_point_is_the_clips_own_travel_away_from_the_seat():
    """Not a fixed offset. A clip that steps back further stands her back further, and one that does
    not travel stands her on the seat -- which is why a non-travelling via is left alone."""
    seat, approach = (0.0, 0.0), (0.0, 3.0)
    near = standing_point_for(seat, approach, (0.0, -0.20))
    far = standing_point_for(seat, approach, (0.0, -0.90))
    assert near[1] == pytest.approx(0.20, abs=1e-6)
    assert far[1] == pytest.approx(0.90, abs=1e-6)


def test_a_seat_she_is_already_standing_on_still_gets_an_answer():
    """A degenerate approach has no direction in it. Normalising zero would be a NaN standing point,
    which reaches the engine as a destination it cannot parse."""
    x, z, facing = standing_point_for((1.0, 2.0), (1.0, 2.0), (0.0, -0.45))
    assert not math.isnan(x) and not math.isnan(z) and not math.isnan(facing)


# ---- what the sidecar carries ------------------------------------------------------------------

# ---- the heading, which is the half the approach cannot decide -------------------------------

def test_a_given_heading_is_used_and_the_approach_is_ignored():
    """A sit-down does not turn her: the heading she stands in is the heading she is seated in. So
    what she is about to work at decides it, and the route she took to get there does not.

    Same arithmetic either way -- the hips still land on the seat, from a different side of it."""
    travel = (0.0364, -0.4446)
    seat, approach = (0.0, 0.0), (0.0, 3.0)
    x, z, facing = standing_point_for(seat, approach, travel, facing_deg=90.0)
    assert facing == pytest.approx(90.0)
    landed = apply_travel((x, z), facing, travel)
    assert landed[0] == pytest.approx(seat[0], abs=1e-6)
    assert landed[1] == pytest.approx(seat[1], abs=1e-6)
    # +X of the seat, because that is where somebody facing +X has to start to end up on it.
    assert x > seat[0] and abs(z - seat[1]) < 0.05


def test_no_heading_falls_back_to_the_approach():
    """The rung under it, unchanged: she puts her back to the seat, along the line she came in on."""
    travel = (0.0364, -0.4446)
    _, _, with_none = standing_point_for((0.0, 0.0), (0.0, 3.0), travel, facing_deg=None)
    _, _, without = standing_point_for((0.0, 0.0), (0.0, 3.0), travel)
    assert with_none == pytest.approx(without)
    assert without == pytest.approx(0.0), "approached from +Z, she faces +Z"


def test_a_bearing_is_the_engines_own_convention():
    """0 is +Z and 90 is +X, so the number goes onto the wire as `facing_deg` without conversion."""
    assert bearing_deg((0.0, 0.0), (0.0, 5.0)) == pytest.approx(0.0)
    assert bearing_deg((0.0, 0.0), (5.0, 0.0)) == pytest.approx(90.0)
    assert bearing_deg((0.0, 0.0), (0.0, -5.0)) == pytest.approx(180.0)
    assert bearing_deg((2.0, 2.0), (2.0, 2.0)) == 0.0, "no direction in a point on top of itself"


def test_a_heading_error_takes_the_short_way_round():
    assert heading_error_deg(350.0, 10.0) == pytest.approx(20.0)
    assert heading_error_deg(10.0, 350.0) == pytest.approx(-20.0)
    assert abs(heading_error_deg(0.0, 180.0)) == pytest.approx(180.0)


def test_the_sidecar_measures_how_far_each_clip_travels(kb):
    dx, dz, yaw = root_travel_of(kb.record(C.SIT_DOWN))
    assert dz < -0.3, "a sit-down steps backwards; measured 0.4446 m on this clip"
    assert math.hypot(dx, dz) == pytest.approx(0.446, abs=0.01)
    assert abs(yaw) < 90.0, "it steps back, it does not turn round"


def test_a_stand_up_travels_the_other_way(kb):
    """The mirror, and the reason the compiler handles both: the same displacement read forwards
    gives the point she ends up on after rising, which is where a following walk departs from."""
    down = root_travel_of(kb.record(C.SIT_DOWN))
    up = root_travel_of(kb.record(C.STAND_UP))
    assert down[1] < 0 < up[1]


def test_a_clip_that_stays_put_says_so(kb):
    """Most of the library does not travel, and a plan that turned the navigation agent off for two
    millimetres would be paying the whole cost of root motion for nothing."""
    for action_id in (C.IDLE, C.SEATED):
        dx, dz, _ = root_travel_of(kb.record(action_id))
        assert math.hypot(dx, dz) < 0.05, action_id


def test_every_record_has_a_travel_measurement():
    """The sidecar covers the store or the runtime refuses to start; `root_travel` is part of that
    coverage now, so a partial rebuild is a failure rather than a field that is sometimes there."""
    doc = paths.read_json(paths.KB_DIR + "/derived/posture.json")
    entries = doc["actions"]
    missing = [a for a, e in entries.items() if "root_travel" not in e]
    assert not missing, "%d entries carry no root_travel (first: %s)" % (len(missing), missing[:3])
    # AGAINST THE MODULE, NOT A LITERAL. Pinning the string here made every deliberate algorithm bump
    # fail as a version mismatch and say nothing about the rule that moved. The invariant that
    # matters is the one `KBIndex.load` enforces: the sidecar on disk was built by this checkout.
    assert doc["_meta"]["version"] == build_posture.POSTURE_ALGORITHM_VERSION


# ---- the protocol ------------------------------------------------------------------------------

def test_the_three_copies_of_the_protocol_version_agree():
    """A mismatch is fatal on the first message, deliberately, so the three have to be bumped
    together. `terminal.py` READS the constant rather than restating it, and this checks that the
    read still lands -- it has a regex fallback for when the import is not available."""
    cs = open(_protocol_cs(), encoding="utf-8").read()
    declared = re.search(r"public const int Version\s*=\s*(\d+)\s*;", cs)
    assert declared, "no version constant in Protocol.cs"
    assert int(declared.group(1)) == P.PROTOCOL_VERSION
    assert terminal.PROTOCOL_VERSION == P.PROTOCOL_VERSION


def test_the_version_is_at_least_five_because_root_motion_is_optional():
    """v5 added `apply_root_motion`. An executor from before it drops the field, discards the root
    motion and plays a sit-down on the spot while reporting success -- which is the one failure shape
    the fatal version check exists to prevent."""
    assert P.PROTOCOL_VERSION >= 5


def _protocol_cs():
    import os
    return os.path.join(os.path.dirname(paths.KB_DIR), "..", "Assets", "Scripts", "AgentRuntime",
                        "Protocol.cs")
