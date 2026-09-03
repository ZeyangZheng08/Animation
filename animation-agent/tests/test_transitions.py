"""Seam search. The arithmetic is checked on synthetic clips; the two anchored numbers come from the
real corpus and exist to catch a silent change in what the metric means."""
import math
import os

import pytest

import config
from agent import transitions as T
from agent.kbindex import KBIndex
from tests import corpus as C

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

def rec_with(loop=False, **channels):
    """A v4 record stub: per channel a measured `raw_measurement.raw_value` and nothing else.

    v3 wrote `role` and `contact` here and `payload_window` read both. Neither survives ADR 0022 --
    what a clip touches is a fact about the scene it is played in -- so what marks a payload is now
    measured: a one-shot whose HANDS move is a one-shot doing something with them.
    """
    return {"loop": loop,
            "channels": {name: {"raw_measurement": {"raw_value": value}}
                         for name, value in channels.items()}}


def test_a_one_shot_with_still_hands_is_freely_trimmable():
    """Nothing is being held, so no span of it is the point. `left_hand` reads exactly 0.0 on
    `grab_bottle` in the real store, so this is the corpus's own shape, not an invented one."""
    assert T.payload_window(rec_with(right_arm=0.4, right_hand=0.0), 40) is None


def test_a_one_shot_whose_hands_move_protects_its_middle():
    """`grab_bottle` reads 0.286 on the right hand and `giving_pills` 0.779 on the left. Cutting into
    that span removes the grasp and the action stops being itself."""
    assert T.payload_window(rec_with(right_hand=0.3), 40) == (5, 34)


def test_the_threshold_is_the_stores_own_static_constant():
    """Not a number chosen here. A hand below `STATIC_MUSCLE` is one the store already calls static,
    so nothing new is calibrated to decide what a payload is."""
    assert T.payload_window(rec_with(right_hand=config.STATIC_MUSCLE), 40) is None
    assert T.payload_window(rec_with(right_hand=config.STATIC_MUSCLE * 1.1), 40) == (5, 34)


def test_a_busy_channel_that_is_not_a_hand_protects_nothing():
    """A torso that moves is most of the corpus; it is what the clip does, not what the clip is FOR.
    Only the hands mark a payload, which is the same coarseness the v3 rule had for the same reason:
    there is no per-frame contact track to be finer with."""
    assert T.payload_window(rec_with(torso=0.9, left_leg=0.9, right_hand=0.0), 40) is None


def test_a_loop_has_no_payload_however_busy_its_hands_are():
    """Every frame of a cycle is as good as any other, so the loop test comes first and nothing below
    it can overrule it."""
    assert T.payload_window(rec_with(loop=True, right_hand=0.9), 40) is None


def test_search_range_skips_the_payload():
    rec = rec_with(right_hand=0.3)
    c = clip("c", {"Hips": [IDENT] * 40})
    allowed = T._search_range(c, rec, tail=True)
    lo, hi = T.payload_window(rec, 40)
    assert allowed and all(k < lo or k > hi for k in allowed)


def test_search_range_is_never_empty_even_when_the_payload_swallows_it():
    """A clip whose protected span covers the whole search window still has to be joinable somewhere."""
    rec = rec_with(right_hand=0.3)
    c = clip("c", {"Hips": [IDENT] * 8})
    assert T._search_range(c, rec, tail=True) != []
    assert T._search_range(c, rec, tail=False) != []


# ---- what is standing on what ----------------------------------------------------------------

def test_a_standing_clip_carries_its_weight_through_both_legs(corpus):
    """v3 read `contact == "ground"` per channel; v4 records do not say what a channel touches, so the
    question is answered from the measured CARRIAGE instead.

    Real records rather than two hand-written stubs: the carriage is read from the posture sidecar
    now, which is keyed by action_id, so a stub with no id has no posture to read and raises rather
    than defaulting -- which is the behaviour `posture_of` is meant to have."""
    kb, _ = corpus
    standing = kb.record(C.WALK)
    seated = kb.record(C.SEATED)

    assert T.support_channels(standing, standing) == {"root", "torso", "left_leg", "right_leg"}
    assert T.support_channels(seated, seated) == {"root", "torso"}
    # Both ends of a seam are consulted: a support being given up is still being stood on for part of
    # the blend, so a stand-to-sit holds the legs across the whole of it.
    assert T.support_channels(standing, seated) == {"root", "torso", "left_leg", "right_leg"}


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
def corpus(tmp_path_factory):
    """A twelve-record store and its clips.

    NOT THE WHOLE LIBRARY, and `load_clips` refuses it outright: 2446 parsed dumps are several
    gigabytes and do not fit in a process, which is why `LOAD_CLIPS_MAX_ACTIONS` exists. The records
    copied here are REAL, so their dumps, their segments and their posture entries all answer as they
    do in production -- what the temporary store changes is how many of them there are.
    """
    if not os.path.isdir(T.paths.RAW_DIR):
        pytest.skip("no raw dumps available")
    kb = KBIndex.load(actions_dir=C.copy_store(tmp_path_factory.mktemp("kb")))
    return kb, T.load_clips(kb)


@pytest.fixture(scope="module")
def looping(corpus):
    """The same clips, with the walk marked as a loop.

    NO CORPUS RECORD DECLARES `loop`: all 2446 read false, because Mixamo's exporter says nothing
    about it and nobody has gone through them. The scheduler's looping behaviour is real and worth
    testing, so the flag is set here — which is exactly what `unity_execute` does when a plan names a
    clip the runtime treats as locomotion.
    """
    kb, clips = corpus
    return kb, dict(clips, **{C.WALK: T.load_clip(C.WALK, loop=True)})


def test_the_two_nearest_actions_need_a_one_frame_blend(corpus):
    """A stand-up finishing on its feet joined to a sit-down starting on its feet: the closest pair
    in the fixture, at about 3.5 degrees. If this stops being a direct cut the metric changed
    meaning, not the corpus.

    It is also the case the end/start posture rule exists for. Read by dominant posture both clips
    are `seated`, and a rule that compared those would call this a posture change -- when what the
    seam actually joins is standing to standing."""
    kb, clips = corpus
    seam = T.find_seam(C.STAND_UP, C.SIT_DOWN, kb, clips)
    assert seam.cost_deg < 5.0
    assert seam.cls == T.CLASS_DIRECT
    assert seam.blend_frames == 1


def test_a_walk_into_a_seated_clip_is_reported_as_a_posture_change(corpus):
    """The one case a crossfade cannot serve: standing to seated, with no clip covering the change."""
    kb, clips = corpus
    seam = T.find_seam(C.WALK, C.SEATED, kb, clips)
    assert seam.cls == T.CLASS_POSTURE_CHANGE
    assert seam.notes and "no clip in the library" in seam.notes[0]


def test_a_clip_that_crosses_a_posture_is_judged_at_its_ends_not_its_middle(corpus):
    """A stand-up is dominantly seated and ends standing, so it joins a walk by an ordinary blend.
    Reading the dominant posture would refuse exactly the clips a posture change needs."""
    kb, clips = corpus
    assert T.find_seam(C.STAND_UP, C.WALK, kb, clips).cls != T.CLASS_POSTURE_CHANGE
    assert T.find_seam(C.WALK, C.SIT_DOWN, kb, clips).cls != T.CLASS_POSTURE_CHANGE


def test_other_is_not_a_posture_to_be_refused_over(corpus):
    """`other` is the posture analysis's fallback for a configuration its rules cannot place, and 1077
    of 2446 clips carry it. Treating a difference involving it as a mismatch would refuse most of what
    the library can perform, over a claim nothing made."""
    kb, clips = corpus
    assert T.find_seam(C.OTHER, C.WALK, kb, clips).cls != T.CLASS_POSTURE_CHANGE
    assert T.find_seam(C.WALK, C.OTHER, kb, clips).cls != T.CLASS_POSTURE_CHANGE


def test_every_ordered_pair_of_a_small_store_gets_a_seam(corpus):
    kb, clips = corpus
    seams = T.build_table(kb)
    n = len(clips)
    assert len(seams) == n * (n - 1)
    assert all(s.from_action != s.to_action for s in seams)


def test_building_every_pair_is_refused_at_corpus_scale():
    """n(n-1) is 5,981,970 seams and about seven CPU-hours over the real library. Refused with the
    arithmetic in the message rather than run: a builder that simply took hours would be indis-
    tinguishable from one that had hung."""
    kb = KBIndex.load()
    assert len(kb.actions) > T.BUILD_TABLE_MAX_ACTIONS
    with pytest.raises(ValueError) as excinfo:
        T.build_table(kb)
    assert "ordered pairs" in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        T.load_clips(kb)
    assert "every dump in memory" in str(excinfo.value)


# ---- scheduling ------------------------------------------------------------------------------

def test_a_single_action_schedules_as_one_step_starting_at_zero(corpus):
    """One action is a one-step sequence, which is what keeps the executor on a single code path."""
    kb, clips = corpus
    steps = T.schedule([C.CHEST], kb, clips)
    assert len(steps) == 1
    assert steps[0].start_at_s == 0.0
    assert steps[0].blend_in_s == 0.0
    assert steps[0].clip_start_frame == 0


def test_the_second_step_enters_at_the_seam_and_fades_in(corpus):
    kb, clips = corpus
    seam = T.find_seam(C.STAND_UP, C.SIT_DOWN, kb, clips)
    steps = T.schedule([C.STAND_UP, C.SIT_DOWN], kb, clips)
    assert len(steps) == 2
    assert steps[1].clip_start_frame == seam.to_frame
    assert steps[1].blend_in_s == pytest.approx(seam.blend_frames / 30.0, abs=1e-6)
    assert steps[1].start_at_s > 0


def test_steps_run_back_to_back_with_no_gap(corpus):
    kb, clips = corpus
    steps = T.schedule([C.IDLE, C.WALK, C.GRAB], kb, clips)
    for a, b in zip(steps, steps[1:]):
        assert b.start_at_s == pytest.approx(a.start_at_s + a.duration_s, abs=1e-6)


def test_a_walk_gets_a_whole_cycle_before_it_hands_over(looping):
    """The walk is 41 frames. Cutting at the seam alone would show a third of a step and call it a
    walk."""
    kb, clips = looping
    steps = T.schedule([C.WALK, C.GRAB], kb, clips)
    assert steps[0].loop is True
    assert steps[0].duration_s >= clips[C.WALK].frames / 30.0


def test_a_trailing_loop_has_no_end(looping):
    """Nothing follows it, so it plays until something else stops it — not for one clip length."""
    kb, clips = looping
    steps = T.schedule([C.GRAB, C.WALK], kb, clips)
    assert steps[-1].action_id == C.WALK
    assert steps[-1].duration_s is None


def test_scheduling_refuses_a_posture_change_rather_than_crossfading_it(corpus):
    """Exactly the lie the seam class exists to prevent: a blend between a stance and a sit
    interpolates through a pose nobody performs."""
    kb, clips = corpus
    with pytest.raises(ValueError) as excinfo:
        T.schedule([C.WALK, C.SEATED], kb, clips)
    assert "posture change" in str(excinfo.value)


# ---- generated transitions -------------------------------------------------------------------

def test_a_posture_change_can_be_generated_when_asked_for_explicitly(corpus):
    """The flag is not a way to silence the objection — it says the caller found something to sit on
    and accepts that these frames are made, not retrieved."""
    kb, clips = corpus
    steps = T.schedule([C.WALK, C.SEATED], kb, clips, generate_posture_changes=True)
    generated = steps[1].generated
    assert generated is not None
    assert generated["kind"] == T.CLASS_POSTURE_CHANGE
    assert generated["from_action"] == C.WALK and generated["to_action"] == C.SEATED


def test_the_generated_target_is_the_next_clips_own_first_played_frame(corpus):
    """Not a guess and not the seat height: where the hips must end up is where the clip that plays
    next has them, because that is the pose the transition has to arrive at."""
    kb, clips = corpus
    steps = T.schedule([C.WALK, C.SEATED], kb, clips, generate_posture_changes=True)
    entered_at = steps[1].clip_start_frame
    assert steps[1].generated["target_hip_height_m"] == pytest.approx(
        T.hip_height(clips[C.SEATED], entered_at), abs=1e-4)


def test_the_generated_duration_follows_the_stated_descent_rate(corpus):
    kb, clips = corpus
    generated = T.schedule([C.WALK, C.SEATED], kb, clips,
                           generate_posture_changes=True)[1].generated
    assert generated["duration_s"] == pytest.approx(
        max(0.3, generated["hip_travel_m"] / T.POSTURE_CHANGE_RATE_M_PER_S), abs=1e-3)


def test_a_generated_transition_lasts_as_long_as_the_descent_not_the_angular_rule(corpus):
    """The crossfade IS the generated transition here, so the descent sets its length. Leaving the
    angular-rate blend in place would fade the clips over 0.3 s while the hips took nearly a second."""
    kb, clips = corpus
    steps = T.schedule([C.WALK, C.SEATED], kb, clips, generate_posture_changes=True)
    # 1e-4 because `generated` carries the wire-rounded value and blend_in_s carries the raw one.
    assert steps[1].blend_in_s == pytest.approx(steps[1].generated["duration_s"], abs=1e-4)


def test_a_descent_into_a_seat_travels_about_the_measured_hip_drop(corpus):
    """Standing hips sit near 0.86-0.90 m and a seated clip's near 0.48 m, so the descent is roughly
    0.4 m. A number far off that means the hip track or the seam moved, not that the rate changed."""
    kb, clips = corpus
    generated = T.schedule([C.WALK, C.SEATED], kb, clips,
                           generate_posture_changes=True)[1].generated
    assert 0.3 < generated["hip_travel_m"] < 0.5


def test_an_ordinary_seam_generates_nothing(corpus):
    kb, clips = corpus
    steps = T.schedule([C.STAND_UP, C.SIT_DOWN], kb, clips, generate_posture_changes=True)
    assert all(s.generated is None for s in steps)


# ---- per-channel seams -------------------------------------------------------------------------

def _ramps(kb, clips, order, generate=False):
    steps = T.schedule(order, kb, clips, generate_posture_changes=generate)
    return steps[1].blend_in_s, steps[1].channel_blends


def test_the_opening_step_crosses_no_seam_and_so_schedules_no_channels(corpus):
    kb, clips = corpus
    steps = T.schedule([C.WALK, C.SEATED], kb, clips, generate_posture_changes=True)
    assert steps[0].channel_blends == []


def test_every_channel_arrives(corpus):
    """The engine layers the incoming step over the outgoing one per channel, so a channel nobody
    schedules keeps the OUTGOING pose for the rest of the plan -- a head still walking while the rest
    of her types. Missing one is not a smaller blend, it is a permanently wrong body part."""
    kb, clips = corpus
    _, groups = _ramps(kb, clips, [C.WALK, C.SEATED], generate=True)
    covered = {channel for group in groups for channel in group["channels"]}
    assert covered == set(T.CHANNEL_BONES)


def test_channels_finish_together(corpus):
    """End-alignment is the mechanism. Everything is in place at the same instant; what differs is
    when each channel starts, which is what makes the upper body arrive late."""
    kb, clips = corpus
    for order, generate in (([C.WALK, C.SEATED], True), ([C.IDLE, C.WALK], False)):
        window, groups = _ramps(kb, clips, order, generate)
        for group in groups:
            assert group["offset_s"] + group["blend_in_s"] == pytest.approx(window, abs=1e-3)


def test_a_channel_with_less_to_do_starts_later(corpus):
    """Measured on this pair: the left hand is 114 degrees from where it has to be, the right arm 47
    and the head 38. So the hand sets off at once and the head goes last, and nothing said so -- it
    falls out of the distances."""
    kb, clips = corpus
    _, groups = _ramps(kb, clips, [C.WALK, C.SEATED], generate=True)
    start = {channel: group["offset_s"] for group in groups for channel in group["channels"]}
    assert start["head"] > start["right_arm"] > start["left_hand"]


def test_the_channels_holding_her_up_cross_the_whole_seam(corpus):
    """A leg cannot arrive late: the weight is on it the whole way down. Derived from the measured
    carriage rather than listed here -- see `support_channels` -- so a corpus that grows a second
    seated action does not need this test rewritten."""
    kb, clips = corpus
    window, groups = _ramps(kb, clips, [C.WALK, C.SEATED], generate=True)
    start = {channel: group["offset_s"] for group in groups for channel in group["channels"]}
    for channel in ("left_leg", "right_leg", "root", "torso"):
        assert start[channel] == pytest.approx(0.0, abs=1e-6)


def test_every_channel_sweeps_at_one_rate(corpus):
    """The point of scaling each width by that channel's own travel: one angular rate across the whole
    body, instead of the busiest joint snapping and the quietest crawling. Fingers are exempt and
    support channels are given more time than their travel needs, so neither is checked here."""
    kb, clips = corpus
    seam = T.find_seam(C.WALK, C.SIT_DOWN, kb, clips)
    window, groups = _ramps(kb, clips, [C.WALK, C.SIT_DOWN])
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
    seam = T.find_seam(C.IDLE, C.WALK, kb, clips)
    assert seam.pace_deg() > seam.cost_deg
    assert seam.blend_frames == T.blend_frames_for(seam.pace_deg(), clips[C.IDLE].fps)


def test_a_hard_cut_is_one_group_of_everything(corpus):
    """A zero-length window has nothing to stagger, and the engine reads a group of zero length as the
    cut it is."""
    kb, clips = corpus
    seam = T.find_seam(C.WALK, C.SEATED, kb, clips)
    groups = T.channel_ramps(seam, 0.0)
    assert len(groups) == 1
    assert set(groups[0]["channels"]) == set(T.CHANNEL_BONES)
    assert groups[0]["offset_s"] == 0.0 and groups[0]["blend_in_s"] == 0.0


def test_an_opener_that_is_only_a_departure_point_leaves_at_once(corpus):
    """An opening step she is already standing in names a POSE, not a performance, and the schedule
    should not spend its clip on it.

    Both ways of getting this wrong were measured on real turns. `walking` as the opener marched her on
    the spot for a stride before she sat, because a looping step is given a whole cycle first. Swapping
    it for the stance made that seconds of standing still, because the idle runs 6 s. Entering ON the
    seam frame costs
    neither: that frame is the closest standing pose to the seated clip's opening, which is the only
    thing the step was ever for.
    """
    kb, clips = corpus
    order = [C.IDLE, C.SEATED]
    padded = T.schedule(order, kb, clips, generate_posture_changes=True)
    departure = T.schedule(order, kb, clips, generate_posture_changes=True, open_at_seam=True)

    assert departure[1].start_at_s < 0.1, "the sit should begin at once"
    assert padded[1].start_at_s > 5.0, "playing the opener out is what it used to wait for"

    # Entered on the frame it hands over from, so nothing of the opener plays and the descent's start
    # pose is a frame that actually happened.
    seam = T.find_seam(C.IDLE, C.SEATED, kb, clips)
    assert departure[0].clip_start_frame == seam.from_frame
    assert padded[0].clip_start_frame == 0


def test_the_generated_descent_is_unchanged_by_where_the_opener_entered(corpus):
    """The hips still travel from the outgoing seam frame to the incoming one; only the waiting went."""
    kb, clips = corpus
    order = [C.IDLE, C.SEATED]
    padded = T.schedule(order, kb, clips, generate_posture_changes=True)
    departure = T.schedule(order, kb, clips, generate_posture_changes=True, open_at_seam=True)

    for key in ("start_hip_height_m", "target_hip_height_m", "duration_s"):
        assert departure[1].generated[key] == padded[1].generated[key]


def test_a_walk_that_is_really_walking_still_gets_its_whole_stride(corpus):
    """The cycle rule is not gone. It is what stops a locomotion step showing a third of a stride and
    calling it a walk; it just does not apply to a step nobody is watching."""
    kb, clips = corpus
    padded = T.schedule([C.WALK, C.SEATED], kb, clips, generate_posture_changes=True)
    assert padded[1].start_at_s > clips[C.WALK].frames / float(clips[C.WALK].fps or 30) * 0.9


# --------------------------------------------------------------------------------------------------
# Content identity and the caches keyed on it.
#
# THERE IS NO SEAM TABLE AND NO DIRECTORY FINGERPRINT ANY MORE, and the tests for both are gone with
# them. The table covered every ordered pair of an eight-action store; over 2446 that is 5.98 million
# pairs and about seven CPU-hours, so seams are computed when asked for. The fingerprint that kept
# the table honest hashed every dump in `raw` -- 52 s per process once the corpus landed, to prove
# that 2446 files the table never opened had not changed.
#
# What replaces both is one SHA-256 per file actually read, carried on the Clip, and a bounded LRU
# keyed on those digests. Same guarantee, paid per clip that is used instead of per clip that exists.

def test_a_clip_carries_the_digest_of_the_bytes_it_was_parsed_from(tmp_path):
    """The digest is taken on the way past: the file has to be read to be parsed, so it is read as
    bytes, hashed, and then decoded. One pass, and the answer is about the content rather than about
    a name or an mtime."""
    import hashlib

    import paths
    from agent import transitions as T

    raw = tmp_path / "_raw"
    raw.mkdir()
    body = {"clip": "one", "frames": 2, "frame_rate": 30, "bone_rot": {"Hips": [IDENT, IDENT]},
            "bones": {}}
    paths.write_json(str(raw / "one.json"), body)
    clip = T.load_clip("one", raw_dir=str(raw))
    on_disk = open(str(raw / "one.json"), "rb").read()
    assert clip.digest == hashlib.sha256(on_disk).hexdigest()


def test_a_reseampled_dump_gets_a_different_digest(tmp_path):
    """The whole point: a dump that changed cannot answer with the seam its old contents earned."""
    import paths
    from agent import transitions as T

    raw = tmp_path / "_raw"
    raw.mkdir()
    body = {"clip": "one", "frames": 2, "frame_rate": 30, "bone_rot": {"Hips": [IDENT, IDENT]},
            "bones": {}}
    paths.write_json(str(raw / "one.json"), body)
    before = T.load_clip("one", raw_dir=str(raw)).digest
    paths.write_json(str(raw / "one.json"), dict(body, frames=2, length=9.0))
    assert T.load_clip("one", raw_dir=str(raw)).digest != before


def test_asking_for_the_same_seam_twice_returns_the_same_object(corpus):
    """A bounded LRU rather than a table. The second ask has to be free, or `motion_transition`
    re-searches every frame pair each time an agent revisits a pairing."""
    kb, clips = corpus
    T.forget_raw()
    first = T.find_seam(C.WALK, C.IDLE, kb, clips)
    assert T.find_seam(C.WALK, C.IDLE, kb, clips) is first


def test_the_seam_cache_is_keyed_on_the_dumps_and_the_algorithm_version(corpus):
    """Keyed on `(from, to, version, digest_from, digest_to)`. `(from, to)` alone would survive a
    re-sample and answer about a dump that no longer exists, which is the failure the directory
    fingerprint existed to prevent -- at the cost of hashing the whole corpus."""
    kb, clips = corpus
    T.forget_raw()
    seam = T.find_seam(C.WALK, C.IDLE, kb, clips)
    key = (C.WALK, C.IDLE, T.SEAM_ALGORITHM_VERSION,
           clips[C.WALK].digest, clips[C.IDLE].digest)
    assert T._SEAM_CACHE[key] is seam

    # A version bump retires every stored answer without anyone having to clear anything.
    stale = (C.WALK, C.IDLE, "0.0.0", clips[C.WALK].digest, clips[C.IDLE].digest)
    assert stale not in T._SEAM_CACHE


def test_a_clip_with_no_digest_is_computed_rather_than_cached(corpus):
    """A Clip a test built by hand carries no digest, so there is nothing honest to key on. Computing
    is the safe answer; keying on the name alone would put a hand-made clip's seam where a real one's
    belongs."""
    kb, clips = corpus
    bare = clip(C.IDLE, {"Hips": [IDENT, IDENT]})
    assert bare.digest is None
    seam = T.find_seam(C.WALK, C.IDLE, kb, dict(clips, **{C.IDLE: bare}))
    assert T.find_seam(C.WALK, C.IDLE, kb, dict(clips, **{C.IDLE: bare})) is not seam


def test_both_caches_are_bounded(corpus):
    """Unbounded was right for eight clips and is a slow leak over 2446: a search-heavy turn touches
    dozens of dumps at about 600 KB of JSON each."""
    assert T.CLIP_CACHE_MAX < 100
    assert T.SEAM_CACHE_MAX < 100000
    kb, clips = corpus
    T.forget_raw()
    for a in sorted(clips):
        T.load_clip((kb.record(a)["source_clip"])["clip_name"])
    assert len(T._CLIP_CACHE) <= T.CLIP_CACHE_MAX


def test_forgetting_raw_drops_both(corpus):
    """The sampler calls it after writing a dump. A seam cache keyed on content could not answer
    staleley anyway, but the entries it would leave behind are about clips that no longer exist."""
    kb, clips = corpus
    T.find_seam(C.WALK, C.IDLE, kb, clips)
    assert T._SEAM_CACHE
    T.forget_raw()
    assert not T._SEAM_CACHE and not T._CLIP_CACHE
