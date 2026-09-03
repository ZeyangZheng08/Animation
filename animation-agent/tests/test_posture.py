"""The posture rules, as arithmetic.

`build_posture.py` is the one module in this pipeline whose output nobody can eyeball: it says what a
body is doing at every frame of 2446 clips, and it is read by the executor to decide whether a
character may be walked off a chair. `audit_posture.py` checks the RULES against clips a human can
label by eye; this checks the FUNCTIONS underneath them against numbers a human can work out on
paper — a de Leva centre of mass over a skeleton with three bones in it, a base of support that is a
rectangle, and one frame of each branch of the rule.

Nothing here reads the corpus. The two cases that do are marked and skipped when the dumps are not on
this machine, since they are untracked (ADR 0014).
"""
import math
import os

import pytest

import build_posture as P
import paths
from tests import corpus as C


# ---- a skeleton somebody can check by hand ---------------------------------------------------

def flat(value=(0.0, 0.0, 0.0)):
    """Every bone the segment table names, all at one point."""
    return {b: tuple(value) for b in P.required_bones()}


def seated_frame():
    """A body sitting on something: trunk upright, thigh horizontal, shank vertical, feet forward.

    Every number below is chosen so the angles come out at right angles and can be read off the
    picture: the trunk points straight up, the femur points straight forward, the shank points
    straight down.
    """
    frame = flat()
    frame.update({
        "Hips": (0.0, 1.0, 0.0), "Chest": (0.0, 1.4, 0.0),
        "Neck": (0.0, 1.5, 0.0), "Head": (0.0, 1.7, 0.0),
    })
    for side, x in (("Left", -0.1), ("Right", 0.1)):
        frame.update({
            side + "UpperLeg": (x, 1.0, 0.0),
            side + "LowerLeg": (x, 1.0, 0.4),
            side + "Foot": (x, 0.6, 0.4),
            side + "Toes": (x, 0.6, 0.6),
            side + "UpperArm": (x, 1.4, 0.0),
            side + "LowerArm": (x, 1.2, 0.0),
            side + "Hand": (x, 1.0, 0.0),
        })
    return frame


# ---- centre_of_mass --------------------------------------------------------------------------

def test_the_mass_fractions_sum_to_one():
    """de Leva's table is a partition of the body. If it did not sum to 1 the COM would be a
    weighted sum rather than a weighted mean, and every height would be wrong by the shortfall."""
    total = sum(mass for _, _, mass, _ in P.DE_LEVA_SEGMENTS)
    assert total == pytest.approx(1.0, abs=1e-9)


def test_a_body_at_one_point_has_its_centre_of_mass_there():
    """The invariant that follows from the fractions summing to 1, and the cheapest way to catch a
    typo in the table."""
    assert P.centre_of_mass(flat((1.0, 2.0, 3.0))) == pytest.approx((1.0, 2.0, 3.0))


def test_the_centre_of_mass_moves_with_the_body():
    """It is a position, not a shape: translate every bone and it translates with them."""
    frame = seated_frame()
    moved = {b: (p[0] + 5.0, p[1] - 2.0, p[2] + 0.5) for b, p in frame.items()}
    a, b = P.centre_of_mass(frame), P.centre_of_mass(moved)
    assert (b[0] - a[0], b[1] - a[1], b[2] - a[2]) == pytest.approx((5.0, -2.0, 0.5))


def test_one_segment_off_the_origin_puts_the_mass_where_the_table_says():
    """Hand-computable: everything at the origin except the neck two metres up. The trunk then runs
    hips-to-neck and carries 0.4346 of the mass at 0.486 of its length; the head segment is a point
    at the neck and carries 0.0694 of it. Nothing else has moved.

        0.4346 * (0.486 * 2)  +  0.0694 * 2  =  0.4224312 + 0.1388  =  0.5612312
    """
    frame = flat()
    frame["Neck"] = (0.0, 2.0, 0.0)
    frame["Head"] = (0.0, 2.0, 0.0)
    assert P.centre_of_mass(frame)[1] == pytest.approx(0.5612312, abs=1e-9)
    assert P.centre_of_mass(frame)[0] == pytest.approx(0.0)


# ---- base of support and the hull --------------------------------------------------------------

def feet_at(ankle_z=0.0, toe_z=0.2, half_width=0.1):
    """A frame whose feet are a plain rectangle, so the hull arithmetic can be read off the page."""
    frame = flat()
    for side, x in (("Left", -half_width), ("Right", half_width)):
        frame[side + "Foot"] = (x, 0.0, ankle_z)
        frame[side + "Toes"] = (x, 0.0, toe_z)
    return frame


def test_the_base_of_support_reaches_behind_the_ankles():
    """Six points: an ankle, a toe and a heel per side, the heel `HEEL_BEHIND_ANKLE_M` behind the
    ankle along the foot's own axis. Without it the base would end at the ankle and every standing
    frame would read as balanced on its toes."""
    points = P.base_of_support(feet_at())
    assert len(points) == 6
    assert sorted(round(z, 4) for _, z in points) == [-0.07, -0.07, 0.0, 0.0, 0.2, 0.2]
    assert sorted(round(x, 4) for x, _ in points) == [-0.1, -0.1, -0.1, 0.1, 0.1, 0.1]


def test_a_foot_with_no_axis_contributes_its_ankle_rather_than_a_guess():
    frame = feet_at()
    for side in ("Left", "Right"):
        frame[side + "Toes"] = frame[side + "Foot"]
    points = P.base_of_support(frame)
    assert {(round(x, 4), round(z, 4)) for x, z in points} == {(-0.1, 0.0), (0.1, 0.0)}


def test_the_hull_of_the_feet_is_the_rectangle_they_span():
    hull = P.convex_hull(P.base_of_support(feet_at()))
    assert set(hull) == {(-0.1, -0.07), (0.1, -0.07), (0.1, 0.2), (-0.1, 0.2)}


def test_inside_the_hull_is_negative_and_outside_is_positive():
    """The rectangle runs x -0.1..0.1 and z -0.07..0.2. The origin is 0.07 from its back edge and
    0.1 from each side, so the nearest edge is the back one."""
    points = P.base_of_support(feet_at())
    assert P.signed_distance_to_hull((0.0, 0.0), points) == pytest.approx(-0.07, abs=1e-9)
    assert P.signed_distance_to_hull((0.0, -0.17), points) == pytest.approx(0.10, abs=1e-9)
    assert P.signed_distance_to_hull((0.3, 0.0), points) == pytest.approx(0.20, abs=1e-9)


def test_a_degenerate_base_answers_as_a_point_or_a_line():
    """One foot point, or a row of them, is a base of support with no area. Answering with a
    distance to it is the correct reading; raising would refuse to describe a clip over one frame."""
    assert P.signed_distance_to_hull((0.0, 0.0), [(0.0, 0.3)]) == pytest.approx(0.3)
    assert P.signed_distance_to_hull((0.0, 0.0), [(-1.0, 0.5), (1.0, 0.5)]) == pytest.approx(0.5)


def test_the_com_behind_the_heels_is_measured_from_the_rearmost_one():
    """Winter's supported case, as one signed number along the axis a seat is on."""
    frame = feet_at()
    assert P.heels_behind(frame, (0.0, 0.7, -0.17)) == pytest.approx(0.10, abs=1e-9)
    assert P.heels_behind(frame, (0.0, 0.7, 0.10)) == pytest.approx(-0.17, abs=1e-9)


# ---- features --------------------------------------------------------------------------------

def test_the_hand_built_frame_reads_as_right_angles():
    """Trunk straight up, femur straight forward, shank straight down: 0, 90, 90, 90 by
    construction, so a sign error anywhere in the angle code shows up here as 90 degrees out."""
    h, theta_trunk, theta_thigh, hip_incl, knee_incl, behind, outside = \
        P.frame_features(seated_frame(), 0.62, 1.0)
    assert theta_trunk == pytest.approx(0.0, abs=1e-6)
    assert theta_thigh == pytest.approx(90.0, abs=1e-6)
    assert hip_incl == pytest.approx(90.0, abs=1e-6)
    assert knee_incl == pytest.approx(90.0, abs=1e-6)
    assert h == pytest.approx(0.62, abs=1e-9)
    # The feet are forward of the hips, so the mass is behind them and inside nothing.
    assert behind > P.COM_BEHIND_BOS_M
    assert outside > 0.0


def test_height_is_a_fraction_of_the_upright_reference():
    """Guerra's normalisation: the same body against a taller upright reference reads lower. The
    height is Unity's own `bodyPosition.y`, which the balance test does not touch."""
    frame = seated_frame()
    assert P.frame_features(frame, 0.62, 0.62)[0] == pytest.approx(1.0)
    assert P.frame_features(frame, 0.62, 1.24)[0] == pytest.approx(0.5)


def test_a_dump_missing_a_bone_the_table_names_is_refused():
    """A COM computed from a partial body is not a COM, and a posture derived from one would be a
    number with nothing behind it."""
    with pytest.raises(ValueError) as e:
        P.features({"frames": 1, "body_pos": [(0.0, 1.0, 0.0)],
                    "bones": {"Hips": [(0.0, 0.0, 0.0)]}}, 1.0)
    assert "missing bone" in str(e.value)


# ---- label_frame -------------------------------------------------------------------------------

def frame(h=0.65, trunk=5.0, thigh=90.0, hip=90.0, knee=90.0, behind=0.20, outside=0.2):
    return (h, trunk, thigh, hip, knee, behind, outside)


def test_a_seated_frame_is_seated():
    assert P.label_frame(frame()) == "seated"


def test_a_stance_with_the_mass_over_the_feet_is_not_seated():
    """The clause that does the work. Same height, same hip and knee as a sit — this is a fighting
    stance, and the only thing that separates them is where the load goes."""
    assert P.label_frame(frame(behind=-0.10)) == "other"


def test_the_mass_has_to_be_clear_of_the_heels_by_the_threshold():
    """Straddling a chair backwards reads just short of it, at about -0.02 m, and is `other` on
    purpose: the feet are astride the seat and carrying the body."""
    assert P.label_frame(frame(behind=P.COM_BEHIND_BOS_M + 0.001)) == "seated"
    assert P.label_frame(frame(behind=P.COM_BEHIND_BOS_M)) == "other"
    assert P.label_frame(frame(behind=-0.023)) == "other"


def test_the_seated_ranges_are_the_workstation_standards_widened():
    for hip in (P.HIP_SEATED[0], 90.0, P.HIP_SEATED[1]):
        assert P.label_frame(frame(hip=hip)) == "seated", hip
    assert P.label_frame(frame(hip=P.HIP_SEATED[0] - 1)) == "other"
    assert P.label_frame(frame(hip=P.HIP_SEATED[1] + 1)) == "other"
    for knee in (P.KNEE_SEATED[0], 90.0, P.KNEE_SEATED[1]):
        assert P.label_frame(frame(knee=knee)) == "seated", knee
    assert P.label_frame(frame(knee=P.KNEE_SEATED[0] - 1)) == "other"
    assert P.label_frame(frame(knee=P.KNEE_SEATED[1] + 1)) == "other"


def test_a_body_folded_flat_is_not_sitting():
    """ISO 11226 stops calling a trunk upright past 60 degrees, and the floor rule takes it first
    once the body is low as well."""
    assert P.label_frame(frame(trunk=P.THETA_TRUNK_HORIZONTAL + 1, h=P.H_LOW - 0.01)) == "floor"
    assert P.label_frame(frame(trunk=P.THETA_TRUNK_HORIZONTAL + 1, h=P.H_LOW + 0.01)) == "other"


def test_the_floor_rule_comes_first():
    assert P.label_frame(frame(h=P.H_FLOOR - 0.01)) == "floor"


def test_standing_is_a_high_carriage_on_vertical_thighs():
    assert P.label_frame(frame(h=1.0, thigh=5.0, hip=175.0, knee=175.0, behind=-0.1)) == "standing"
    assert P.label_frame(frame(h=P.H_AIRBORNE + 0.01, thigh=5.0, hip=175.0, knee=175.0,
                               behind=-0.1)) == "other"
    assert P.label_frame(frame(h=1.0, thigh=P.THETA_THIGH_UPRIGHT + 1, hip=140.0, knee=150.0,
                               behind=-0.1)) == "other"


# ---- against the corpus, where the dumps are on this machine -----------------------------------

def dump_for(action_id):
    record = paths.read_json(os.path.join(paths.ACTIONS_DIR, action_id + ".json"))
    name = (record.get("source_clip") or {}).get("clip_name")
    path = os.path.join(paths.RAW_DIR, name + ".json")
    if not os.path.exists(path):
        pytest.skip("no frozen dump for %s on this machine" % action_id)
    return paths.read_json(path)


def test_the_upright_reference_reads_as_upright():
    """`mx_Standing_Idle` normalises itself to 1.0 by construction, which is what makes every height
    threshold a fraction of standing height rather than an absolute number."""
    raw = dump_for(P.UPRIGHT_REFERENCE_ACTION)
    upright = P.upright_body_height(raw)
    assert 0.5 < upright < 1.5, "an upright bodyPosition height on this avatar"
    heights = [f[0] for f in P.features(raw, upright)]
    assert sum(heights) / len(heights) == pytest.approx(1.0, abs=1e-9)
    assert all(P.H_STANDING <= h <= P.H_AIRBORNE for h in heights)


def test_a_real_sit_puts_the_mass_behind_the_feet_and_a_real_stance_does_not():
    """The measurement the rule turns on, on two clips from the corpus: sitting down ends with the
    COM well behind the heels, and a fighting stance ends with it over them."""
    upright = P.upright_body_height(dump_for(P.UPRIGHT_REFERENCE_ACTION))
    sit = P.features(dump_for(C.SIT_DOWN), upright)[-1]
    stance = P.features(dump_for("mx_Engaged_In_A_Fist_Fight_2"), upright)[-1]
    assert sit[5] > P.COM_BEHIND_BOS_M
    assert stance[5] < 0.0
    assert P.label_frame(sit) == "seated"
    assert P.label_frame(stance) != "seated"


def behind_by(action_id, com_of):
    """Mean over the last eight frames of (rear heel z - COM z), for whichever COM is passed."""
    raw = dump_for(action_id)
    frames = P._bone_frames(raw)[-8:]
    body = raw["body_pos"][-8:]
    values = [P.heels_behind(f, com_of(f, b)) for f, b in zip(frames, body)]
    return sum(values) / len(values)


def test_unitys_body_position_is_kept_for_height_and_not_for_balance():
    """The measurement that settled which quantity does what.

    `HumanPose.bodyPosition` and the de Leva COM agree vertically and disagree horizontally, in both
    directions and by up to 0.22 m. Sitting still in a chair reads 0.008 m behind the rear heel by
    bodyPosition — inside the noise — and 0.160 m by de Leva. Crouching behind cover reads +0.09 m by
    bodyPosition, i.e. BEHIND the feet, which would make a crouch a sit; de Leva puts it over them.
    So the height is Unity's and the balance test is de Leva's.
    """
    unity = lambda frame, body: (0.0, 0.0, body[2])
    de_leva = lambda frame, body: P.centre_of_mass(frame)

    # THE DECISIVE CASE: a crouch. Unity's body position puts the mass BEHIND the feet, which is the
    # supported case and would make a crouch a sit; de Leva puts it over them, which is what a crouch
    # is. The two disagree in SIGN, so no threshold could rescue the first.
    assert behind_by("mx_Crouch_Cover_To_Cover", unity) > 0.0
    assert behind_by("mx_Crouch_Cover_To_Cover", de_leva) < 0.0

    # And on a real sit they agree in sign and disagree by twenty-fold: 0.008 m by bodyPosition,
    # which is inside the noise of a rig, against 0.160 m.
    by_unity = behind_by("mx_Sitting_Still_In_A_Chair", unity)
    by_de_leva = behind_by("mx_Sitting_Still_In_A_Chair", de_leva)
    assert by_de_leva > 0.1 > by_unity
    assert by_de_leva > 10 * by_unity


def test_the_two_heights_track_each_other_which_is_why_unitys_is_kept():
    """Vertically there is nothing to choose between them, so the engine's own number stays."""
    raw = dump_for(P.UPRIGHT_REFERENCE_ACTION)
    upright_body = P.upright_body_height(raw)
    upright_com = sum(P.centre_of_mass(f)[1] for f in P._bone_frames(raw)) / raw["frames"]
    for action_id in (P.UPRIGHT_REFERENCE_ACTION, C.SEATED, C.SIT_DOWN):
        clip = dump_for(action_id)
        frames, body = P._bone_frames(clip), clip["body_pos"]
        worst = max(abs(b[1] / upright_body - P.centre_of_mass(f)[1] / upright_com)
                    for f, b in zip(frames, body))
        assert worst < 0.03, "%s: the two heights differ by %.3f" % (action_id, worst)
