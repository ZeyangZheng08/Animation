"""Seam search. The arithmetic is checked on synthetic clips; the two anchored numbers come from the
real corpus and exist to catch a silent change in what the metric means."""
import math
import os

import pytest

from agent import transitions as T
from agent.kbindex import KBIndex

IDENT = [0.0, 0.0, 0.0, 1.0]


def q_y(deg):
    """Quaternion for a rotation of `deg` about Y, xyzw."""
    h = math.radians(deg) / 2.0
    return [0.0, math.sin(h), 0.0, math.cos(h)]


def make_raw(name, per_frame, frames=None, fps=30, feet=None):
    """`per_frame` maps bone -> list of quaternions."""
    n = frames or len(next(iter(per_frame.values())))
    bones = {}
    if feet:
        bones["LeftFoot"] = [[0.0, y, 0.0] for y in feet]
        bones["RightFoot"] = [[0.0, y, 0.0] for y in feet]
    return {"clip": name, "frames": n, "frame_rate": fps, "bone_rot": per_frame, "bones": bones}


def clip(name, per_frame, loop=False, **kw):
    return T.Clip(make_raw(name, per_frame, **kw), loop=loop)


# ---- quaternion distance ---------------------------------------------------------------------

def test_identical_rotations_are_zero_degrees_apart():
    assert T.quat_angle_deg(IDENT, IDENT) == pytest.approx(0.0, abs=1e-9)


def test_angle_matches_the_rotation_it_encodes():
    assert T.quat_angle_deg(IDENT, q_y(90)) == pytest.approx(90.0, abs=1e-6)
    assert T.quat_angle_deg(q_y(30), q_y(75)) == pytest.approx(45.0, abs=1e-6)


def test_negated_quaternion_is_the_same_rotation():
    """q and -q differ in the dump but describe one orientation; a signed dot product would report 180
    degrees of difference between a pose and itself and poison every seam."""
    q = q_y(40)
    assert T.quat_angle_deg(q, [-c for c in q]) == pytest.approx(0.0, abs=1e-6)


def test_pose_distance_averages_over_bones_and_ignores_unshared_ones():
    a = clip("a", {"Hips": [IDENT], "Chest": [IDENT], "OnlyInA": [q_y(90)]})
    b = clip("b", {"Hips": [q_y(20)], "Chest": [q_y(40)]})
    assert T.pose_distance(a, 0, b, 0) == pytest.approx(30.0, abs=1e-6)


def test_fingers_are_excluded_from_the_pose_metric():
    a = clip("a", {"Hips": [IDENT], "LeftThumbProximal": [IDENT]})
    assert a.pose_bones == ["Hips"]


# ---- blend length and class ------------------------------------------------------------------

def test_blend_length_follows_the_stated_angular_rate():
    # 180 deg/s at 30 fps = 6 deg/frame.
    assert T.blend_frames_for(0.0, 30) == 0
    assert T.blend_frames_for(6.0, 30) == 1
    assert T.blend_frames_for(6.1, 30) == 2
    assert T.blend_frames_for(42.0, 30) == 7


def test_class_needs_no_tuned_threshold():
    assert T.classify(1, "standing", "standing") == T.CLASS_DIRECT
    assert T.classify(2, "standing", "standing") == T.CLASS_BLEND


def test_posture_change_outranks_the_numbers():
    """Even a one-frame blend is not a stance change; the categorical fact wins."""
    assert T.classify(1, "standing", "seated") == T.CLASS_POSTURE_CHANGE
    assert T.classify(99, "seated", "seated") == T.CLASS_BLEND


def test_unknown_posture_does_not_invent_a_change():
    assert T.classify(3, None, "seated") == T.CLASS_BLEND


# ---- trim budget -----------------------------------------------------------------------------

def test_a_loop_has_no_payload_to_protect():
    assert T.payload_window({"loop": True, "channels": {}}, 30) is None


def test_a_one_shot_without_object_contact_is_freely_trimmable():
    rec = {"loop": False, "channels": {"right_arm": {"role": "primary", "contact": "none"}}}
    assert T.payload_window(rec, 40) is None


def test_a_one_shot_touching_an_object_protects_its_middle():
    rec = {"loop": False, "channels": {"right_hand": {"role": "primary", "contact": "object:pills"}}}
    assert T.payload_window(rec, 40) == (5, 34)


def test_contact_on_a_non_primary_channel_does_not_protect_anything():
    """A stabilizer leaning on something is not the point of the action."""
    rec = {"loop": False, "channels": {"torso": {"role": "stabilizer", "contact": "object:bed"}}}
    assert T.payload_window(rec, 40) is None


def test_search_range_skips_the_payload():
    rec = {"loop": False, "channels": {"right_hand": {"role": "primary", "contact": "object:pills"}}}
    c = clip("c", {"Hips": [IDENT] * 40})
    allowed = T._search_range(c, rec, tail=True)
    lo, hi = T.payload_window(rec, 40)
    assert allowed and all(k < lo or k > hi for k in allowed)


def test_search_range_is_never_empty_even_when_the_payload_swallows_it():
    """A clip whose protected span covers the whole search window still has to be joinable somewhere."""
    rec = {"loop": False, "channels": {"right_hand": {"role": "primary", "contact": "object:x"}}}
    c = clip("c", {"Hips": [IDENT] * 8})
    assert T._search_range(c, rec, tail=True) != []
    assert T._search_range(c, rec, tail=False) != []


# ---- foot preference -------------------------------------------------------------------------

def test_a_locomotion_seam_prefers_a_planted_frame():
    """Two frames are equally good on pose; one has a foot in the air. The planted one must win."""
    rots = {"Hips": [IDENT, IDENT]}
    walk = clip("walk", rots, loop=True, feet=[0.05, 0.35])
    assert T._foot_penalty(walk, 0) == pytest.approx(0.0)
    assert T._foot_penalty(walk, 1) > 0.0


def test_a_non_looping_clip_pays_no_foot_penalty():
    """The term exists to stop a walk mid-swing; a one-shot's feet are wherever the action put them."""
    still = clip("still", {"Hips": [IDENT, IDENT]}, loop=False, feet=[0.05, 0.35])
    assert T._foot_penalty(still, 1) == pytest.approx(0.0)


# ---- against the real corpus -----------------------------------------------------------------

@pytest.fixture(scope="module")
def corpus():
    if not os.path.isdir(T.paths.RAW_DIR):
        pytest.skip("no _raw dumps available")
    kb = KBIndex.load()
    return kb, T.load_clips(kb)


def test_the_two_nearest_actions_need_a_one_frame_blend(corpus):
    """check_pulse and giving_pills share a standing-at-the-bedside pose. If this stops being ~2 deg the
    metric changed meaning, not the corpus."""
    kb, clips = corpus
    seam = T.find_seam("check_pulse", "giving_pills", kb, clips)
    assert seam.cost_deg < 5.0
    assert seam.cls == T.CLASS_DIRECT
    assert seam.blend_frames == 1


def test_walking_into_typing_is_reported_as_a_posture_change(corpus):
    """The one case a crossfade cannot serve: standing to seated, with no clip covering the change."""
    kb, clips = corpus
    seam = T.find_seam("walking", "typing", kb, clips)
    assert seam.cls == T.CLASS_POSTURE_CHANGE
    assert seam.notes and "no clip in the library" in seam.notes[0]


def test_every_ordered_pair_gets_a_seam(corpus):
    kb, clips = corpus
    seams = T.build_table(kb)
    n = len(clips)
    assert len(seams) == n * (n - 1)
    assert all(s.from_action != s.to_action for s in seams)


def test_the_table_survives_a_write_and_read(corpus, tmp_path):
    kb, _ = corpus
    seams = T.build_table(kb)
    path = str(tmp_path / "transitions.json")
    T.write_table(seams, path=path)
    table = T.read_table(path)
    assert len(table) == len(seams)
    one = seams[0]
    assert table[(one.from_action, one.to_action)]["blend_frames"] == one.blend_frames


# ---- scheduling ------------------------------------------------------------------------------

def test_a_single_action_schedules_as_one_step_starting_at_zero(corpus):
    """One action is a one-step sequence, which is what keeps the executor on a single code path."""
    kb, clips = corpus
    steps = T.schedule(["cpr"], kb, clips)
    assert len(steps) == 1
    assert steps[0].start_at_s == 0.0
    assert steps[0].blend_in_s == 0.0
    assert steps[0].clip_start_frame == 0


def test_the_second_step_enters_at_the_seam_and_fades_in(corpus):
    kb, clips = corpus
    seam = T.find_seam("check_pulse", "giving_pills", kb, clips)
    steps = T.schedule(["check_pulse", "giving_pills"], kb, clips)
    assert len(steps) == 2
    assert steps[1].clip_start_frame == seam.to_frame
    assert steps[1].blend_in_s == pytest.approx(seam.blend_frames / 30.0, abs=1e-6)
    assert steps[1].start_at_s > 0


def test_steps_run_back_to_back_with_no_gap(corpus):
    kb, clips = corpus
    steps = T.schedule(["check_pulse", "giving_pills", "grab_bottle"], kb, clips)
    for a, b in zip(steps, steps[1:]):
        assert b.start_at_s == pytest.approx(a.start_at_s + a.duration_s, abs=1e-6)


def test_a_walk_gets_a_whole_cycle_before_it_hands_over(corpus):
    """Walk_N is 29 frames. Cutting at the seam alone would show a third of a step and call it a walk."""
    kb, clips = corpus
    steps = T.schedule(["walking", "giving_pills"], kb, clips)
    assert steps[0].loop is True
    assert steps[0].duration_s >= clips["walking"].frames / 30.0


def test_a_trailing_loop_has_no_end(corpus):
    """Nothing follows it, so it plays until something else stops it — not for one clip length."""
    kb, clips = corpus
    steps = T.schedule(["giving_pills", "walking"], kb, clips)
    assert steps[-1].action_id == "walking"
    assert steps[-1].duration_s is None


def test_scheduling_refuses_a_posture_change_rather_than_crossfading_it(corpus):
    """Exactly the lie the seam class exists to prevent: a blend between a stance and a sit
    interpolates through a pose nobody performs."""
    kb, clips = corpus
    with pytest.raises(ValueError) as excinfo:
        T.schedule(["walking", "typing"], kb, clips)
    assert "posture change" in str(excinfo.value)


# ---- generated transitions -------------------------------------------------------------------

def test_a_posture_change_can_be_generated_when_asked_for_explicitly(corpus):
    """The flag is not a way to silence the objection — it says the caller found something to sit on
    and accepts that these frames are made, not retrieved."""
    kb, clips = corpus
    steps = T.schedule(["walking", "typing"], kb, clips, generate_posture_changes=True)
    generated = steps[1].generated
    assert generated is not None
    assert generated["kind"] == T.CLASS_POSTURE_CHANGE
    assert generated["from_action"] == "walking" and generated["to_action"] == "typing"


def test_the_generated_target_is_the_next_clips_own_first_played_frame(corpus):
    """Not a guess and not the seat height: where the hips must end up is where the clip that plays
    next has them, because that is the pose the transition has to arrive at."""
    kb, clips = corpus
    steps = T.schedule(["walking", "typing"], kb, clips, generate_posture_changes=True)
    entered_at = steps[1].clip_start_frame
    assert steps[1].generated["target_hip_height_m"] == pytest.approx(
        T.hip_height(clips["typing"], entered_at), abs=1e-4)


def test_the_generated_duration_follows_the_stated_descent_rate(corpus):
    kb, clips = corpus
    generated = T.schedule(["walking", "typing"], kb, clips,
                           generate_posture_changes=True)[1].generated
    assert generated["duration_s"] == pytest.approx(
        max(0.3, generated["hip_travel_m"] / T.POSTURE_CHANGE_RATE_M_PER_S), abs=1e-3)


def test_a_generated_transition_lasts_as_long_as_the_descent_not_the_angular_rule(corpus):
    """The crossfade IS the generated transition here, so the descent sets its length. Leaving the
    angular-rate blend in place would fade the clips over 0.3 s while the hips took nearly a second."""
    kb, clips = corpus
    steps = T.schedule(["walking", "typing"], kb, clips, generate_posture_changes=True)
    # 1e-4 because `generated` carries the wire-rounded value and blend_in_s carries the raw one.
    assert steps[1].blend_in_s == pytest.approx(steps[1].generated["duration_s"], abs=1e-4)


def test_walking_down_to_typing_travels_about_the_measured_hip_drop(corpus):
    """Standing hips sit near 0.86-0.90 m and typing's near 0.466 m, so the descent is roughly 0.4 m.
    A number far off that means the hip track or the seam moved, not that the rate changed."""
    kb, clips = corpus
    generated = T.schedule(["walking", "typing"], kb, clips,
                           generate_posture_changes=True)[1].generated
    assert 0.3 < generated["hip_travel_m"] < 0.5


def test_an_ordinary_seam_generates_nothing(corpus):
    kb, clips = corpus
    steps = T.schedule(["check_pulse", "giving_pills"], kb, clips, generate_posture_changes=True)
    assert all(s.generated is None for s in steps)


def test_a_table_built_from_other_raw_data_is_refused_not_returned(corpus, tmp_path):
    """A cache that cannot notice its inputs moved would answer confidently about a corpus that no
    longer exists. Returning None makes the caller recompute."""
    kb, _ = corpus
    path = str(tmp_path / "transitions.json")
    T.write_table(T.build_table(kb), path=path)
    assert T.read_table(path) is not None

    doc = T.paths.read_json(path)
    doc["_meta"]["raw_fingerprint"] = "0000000000000000"
    T.paths.write_json(path, doc)
    assert T.read_table(path) is None
    assert T.read_table(path, check_fingerprint=False) is not None


# ---- per-channel seams -------------------------------------------------------------------------

def _ramps(kb, clips, order, generate=False):
    steps = T.schedule(order, kb, clips, generate_posture_changes=generate)
    return steps[1].blend_in_s, steps[1].channel_blends


def test_the_opening_step_crosses_no_seam_and_so_schedules_no_channels(corpus):
    kb, clips = corpus
    steps = T.schedule(["walking", "typing"], kb, clips, generate_posture_changes=True)
    assert steps[0].channel_blends == []


def test_every_channel_arrives(corpus):
    """The engine layers the incoming step over the outgoing one per channel, so a channel nobody
    schedules keeps the OUTGOING pose for the rest of the plan -- a head still walking while the rest
    of her types. Missing one is not a smaller blend, it is a permanently wrong body part."""
    kb, clips = corpus
    _, groups = _ramps(kb, clips, ["walking", "typing"], generate=True)
    covered = {channel for group in groups for channel in group["channels"]}
    assert covered == set(T.CHANNEL_BONES)


def test_channels_finish_together(corpus):
    """End-alignment is the mechanism. Everything is in place at the same instant; what differs is
    when each channel starts, which is what makes the upper body arrive late."""
    kb, clips = corpus
    for order, generate in ((["walking", "typing"], True), (["idle", "walking"], False)):
        window, groups = _ramps(kb, clips, order, generate)
        for group in groups:
            assert group["offset_s"] + group["blend_in_s"] == pytest.approx(window, abs=1e-3)


def test_a_channel_with_less_to_do_starts_later(corpus):
    """`walking` and `typing` are ~90 degrees apart at the right arm and ~7 at the head. The head is
    therefore the last thing to move, and nothing said so -- it falls out of the distances."""
    kb, clips = corpus
    _, groups = _ramps(kb, clips, ["walking", "typing"], generate=True)
    start = {channel: group["offset_s"] for group in groups for channel in group["channels"]}
    assert start["head"] > start["left_arm"] > start["right_leg"]


def test_the_channels_holding_her_up_cross_the_whole_seam(corpus):
    """A leg cannot arrive late: the weight is on it the whole way down. Read off `contact == ground`
    rather than listed, so it stays true if an action ever puts a hand on the floor."""
    kb, clips = corpus
    window, groups = _ramps(kb, clips, ["walking", "typing"], generate=True)
    start = {channel: group["offset_s"] for group in groups for channel in group["channels"]}
    for channel in ("left_leg", "right_leg", "root", "torso"):
        assert start[channel] == pytest.approx(0.0, abs=1e-6)


def test_every_channel_sweeps_at_one_rate(corpus):
    """The point of scaling each width by that channel's own travel: one angular rate across the whole
    body, instead of the busiest joint snapping and the quietest crawling. Fingers are exempt and
    support channels are given more time than their travel needs, so neither is checked here."""
    kb, clips = corpus
    seam = T.find_seam("walking", "giving_pills", kb, clips)
    window, groups = _ramps(kb, clips, ["walking", "giving_pills"])
    rates = []
    for group in groups:
        for channel in group["channels"]:
            if (channel in T.FINGER_CHANNELS or channel in seam.support
                    or group["blend_in_s"] <= 0):
                continue
            rates.append(seam.channel_cost_deg[channel] / group["blend_in_s"])
    assert len(rates) >= 2
    assert max(rates) == pytest.approx(min(rates), rel=0.05)
    assert max(rates) <= T.MAX_BLEND_RATE_DEG_PER_S * 1.05


def test_a_seam_is_as_long_as_its_busiest_channel_needs(corpus):
    """Not as long as the average needs. The average is the right number for choosing WHERE to cut and
    the wrong one for how long the cut takes -- 26 degrees of arm in three frames is 260 deg/s against
    a stated ceiling of 180."""
    kb, clips = corpus
    seam = T.find_seam("idle", "walking", kb, clips)
    assert seam.pace_deg() > seam.cost_deg
    assert seam.blend_frames == T.blend_frames_for(seam.pace_deg(), clips["idle"].fps)


def test_a_hard_cut_is_one_group_of_everything(corpus):
    """A zero-length window has nothing to stagger, and the engine reads a group of zero length as the
    cut it is."""
    kb, clips = corpus
    seam = T.find_seam("walking", "typing", kb, clips)
    groups = T.channel_ramps(seam, 0.0)
    assert len(groups) == 1
    assert set(groups[0]["channels"]) == set(T.CHANNEL_BONES)
    assert groups[0]["offset_s"] == 0.0 and groups[0]["blend_in_s"] == 0.0


def test_an_opener_that_is_only_a_departure_point_leaves_at_once(corpus):
    """An opening step she is already standing in names a POSE, not a performance, and the schedule
    should not spend its clip on it.

    Both ways of getting this wrong were measured on real turns. `walking` as the opener marched her on
    the spot for a stride before she sat, because a looping step is given a whole cycle first. Swapping
    it for `idle` made that eight seconds, because idle is 8.4 s long. Entering ON the seam frame costs
    neither: that frame is the closest standing pose to the seated clip's opening, which is the only
    thing the step was ever for.
    """
    kb, clips = corpus
    order = ["idle", "typing"]
    padded = T.schedule(order, kb, clips, generate_posture_changes=True)
    departure = T.schedule(order, kb, clips, generate_posture_changes=True, open_at_seam=True)

    assert departure[1].start_at_s < 0.1, "the sit should begin at once"
    assert padded[1].start_at_s > 8.0, "an idle cycle is what it used to wait for"

    # Entered on the frame it hands over from, so nothing of the opener plays and the descent's start
    # pose is a frame that actually happened.
    seam = T.find_seam("idle", "typing", kb, clips)
    assert departure[0].clip_start_frame == seam.from_frame
    assert padded[0].clip_start_frame == 0


def test_the_generated_descent_is_unchanged_by_where_the_opener_entered(corpus):
    """The hips still travel from the outgoing seam frame to the incoming one; only the waiting went."""
    kb, clips = corpus
    order = ["idle", "typing"]
    padded = T.schedule(order, kb, clips, generate_posture_changes=True)
    departure = T.schedule(order, kb, clips, generate_posture_changes=True, open_at_seam=True)

    for key in ("start_hip_height_m", "target_hip_height_m", "duration_s"):
        assert departure[1].generated[key] == padded[1].generated[key]


def test_a_walk_that_is_really_walking_still_gets_its_whole_stride(corpus):
    """The cycle rule is not gone. It is what stops a locomotion step showing a third of a stride and
    calling it a walk; it just does not apply to a step nobody is watching."""
    kb, clips = corpus
    padded = T.schedule(["walking", "typing"], kb, clips, generate_posture_changes=True)
    assert padded[1].start_at_s > clips["walking"].frames / float(clips["walking"].fps or 30) * 0.9
