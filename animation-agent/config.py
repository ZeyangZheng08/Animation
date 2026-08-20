"""
config.py — the canonical, ENGINE-NEUTRAL knowledge of the MotionKB v2 extractor.

This module is the single source of truth for the body-part split and the measurement
normalization. It is pure Python and Unity-independent: Unity is touched ONLY to sample muscle
clips (see unity_sampler.py), and even the bone names here are plain strings that the generic
Unity sampler resolves — the partition and metric live in Python, by design (the multi-agent
system is decoupled from the engine).

9 channels = 8 anatomical (PARTITION set, partitioned by composability.locks/free) + 1 root
(locomotion-owned, measured-only, NOT partitioned). v2 split locked 2026-06-18:
  - laterality split kept for legs AND hands (general design; forward-declared for future clips)
  - clavicle (shoulder) AND wrist both belong to the ARM channel in all engines; HAND = fingers only
  - foot+toes fold into the LEG channel; foot ground-contact lives in the orthogonal IK layer

Divisors/thresholds are FROZEN from the 2026-06-18 in-engine calibration (reproducible via
unity_sampler + metrics; see docs/specs/motionkb-v2-spec.md §2 and docs/adr/0007). Each divisor maps
its most-active reference clip to ~0.85; each static threshold sits in the wide gap above that
channel's idle/static cluster. Bumping any value here is a metric_formula_version change.

All identifiers/comments are English (all-English-artifacts rule).
"""

SCHEMA_VERSION   = "motionkb/v2"
FORMULA_VERSION  = "v2.2.0"   # v2.2.0: measured in Unity's normalised Humanoid space (ADR 0011)
BONE_MAP_VERSION = "v2.0.0"
EXTRACTOR_VERSION = "2.0.0"
CALIBRATION_AVATAR = "nurse_avatar.fbx"

# The avatar the EVIDENCE FRAMES are drawn on — deliberately not the one above.
#
# Measurement and rendering are the two halves ADR 0008 keeps apart: the sampler produces the
# numbers, the frames are what a VLM reads to propose the categorical labels. They may therefore use
# different bodies, and they should.
#
# Rendering on nurse_avatar put every clip in clinical scrubs, so a cartwheel and a sword swing were
# both previewed on something that looks like a nurse. This library is general motion — locomotion,
# sport, combat, dance — and a costume in shot is exactly the kind of cue that pulls a label toward
# a reading the movement does not support. Y Bot is Mixamo's featureless mannequin: no clothing, no
# props, nothing to read into.
#
# Measurement stays on nurse_avatar because the DIVISOR table is fitted in metres against its limb
# lengths (ADR 0010). Swapping the sampled avatar shifts every raw signal — measured on one clip,
# nurse_avatar vs X Bot differs by -18.3% at the torso and +16.5% at root_gait — so it would
# invalidate the calibration for no benefit the frames do not already give.
RENDER_AVATAR = "Y Bot.fbx"

# ---- Sampling rule (fixes v1's fixed-30 undersampling): N = clamp(round(dur*fps), MIN, MAX) ----
# MAX=600 > the longest current native frame count (cpr=540), so no clip is sub-native; a max-based
# signal (torso lean) is not decimation-robust, hence native rate.
SAMPLE_MIN, SAMPLE_MAX = 2, 600

# ---- Channel vocabulary (single source of truth; engine_mask_map.json mirrors this) ----
ROOT       = "root"
TORSO      = "torso"
HEAD       = "head"
LEFT_ARM   = "left_arm"
RIGHT_ARM  = "right_arm"
LEFT_LEG   = "left_leg"
RIGHT_LEG  = "right_leg"
LEFT_HAND  = "left_hand"
RIGHT_HAND = "right_hand"

STATE_CHANNELS     = [ROOT, TORSO, HEAD, LEFT_ARM, RIGHT_ARM, LEFT_LEG, RIGHT_LEG, LEFT_HAND, RIGHT_HAND]  # 9 (measured + state_label)
PARTITION_CHANNELS = [TORSO, HEAD, LEFT_ARM, RIGHT_ARM, LEFT_LEG, RIGHT_LEG, LEFT_HAND, RIGHT_HAND]        # 8 (locks/free)

# ---- Channel -> Unity HumanBodyBones names (the sampler resolves these; Python owns the grouping) ----
ARM_BONES = {
    LEFT_ARM:  ["LeftShoulder", "LeftUpperArm", "LeftLowerArm", "LeftHand"],
    RIGHT_ARM: ["RightShoulder", "RightUpperArm", "RightLowerArm", "RightHand"],
}
LEG_BONES = {
    LEFT_LEG:  ["LeftUpperLeg", "LeftLowerLeg", "LeftFoot", "LeftToes"],
    RIGHT_LEG: ["RightUpperLeg", "RightLowerLeg", "RightFoot", "RightToes"],
}
# Hand: 5 fingers x {Proximal, Intermediate, Distal} per side; metric is finger curl, rooted at the wrist.
FINGER_BONES = {
    LEFT_HAND: [
        ["LeftThumbProximal",  "LeftThumbIntermediate",  "LeftThumbDistal"],
        ["LeftIndexProximal",  "LeftIndexIntermediate",  "LeftIndexDistal"],
        ["LeftMiddleProximal", "LeftMiddleIntermediate", "LeftMiddleDistal"],
        ["LeftRingProximal",   "LeftRingIntermediate",   "LeftRingDistal"],
        ["LeftLittleProximal", "LeftLittleIntermediate", "LeftLittleDistal"],
    ],
    RIGHT_HAND: [
        ["RightThumbProximal",  "RightThumbIntermediate",  "RightThumbDistal"],
        ["RightIndexProximal",  "RightIndexIntermediate",  "RightIndexDistal"],
        ["RightMiddleProximal", "RightMiddleIntermediate", "RightMiddleDistal"],
        ["RightRingProximal",   "RightRingIntermediate",   "RightRingDistal"],
        ["RightLittleProximal", "RightLittleIntermediate", "RightLittleDistal"],
    ],
}
WRIST = {LEFT_HAND: "LeftHand", RIGHT_HAND: "RightHand"}
# Angle/posture helper bones.
HIPS, CHEST, NECK, HEAD_BONE = "Hips", "Chest", "Neck", "Head"
LEFT_FOOT, RIGHT_FOOT = "LeftFoot", "RightFoot"


def all_sample_bones():
    """Union of every bone the metrics need — the bone list Python sends to the Unity sampler."""
    s = set([HIPS, CHEST, NECK, HEAD_BONE, LEFT_FOOT, RIGHT_FOOT])
    for g in ARM_BONES.values():
        s.update(g)
    for g in LEG_BONES.values():
        s.update(g)
    for side in FINGER_BONES.values():
        for finger in side:
            s.update(finger)
    s.update(WRIST.values())
    return sorted(s)


# ---- Frozen normalization (divisor: raw/divisor=magnitude clamped [0,1]; provenance = ref clip) ----
# Fitted on the corpus, in Unity's normalised Humanoid space.
#
# The signals these divide are muscle values now, not metres (ADR 0011): each is one joint's rotation
# expressed against that avatar's own limit, so it is dimensionless and body-independent. Fitted over
# 150 randomly sampled Mixamo clips (calibrate_divisors.py --scratch ~/calib_muscle, seed 0) so that
# the corpus 99th percentile normalises to 0.85, leaving 0-0.7% saturation.
DIVISOR = {
    TORSO: 0.3174,          # per-DOF stddev RMS;  corpus p99 0.2698 -> 0.85
    HEAD: 0.5809,           #                      corpus p99 0.4937 -> 0.85
    "arm": 0.6914,          #                      corpus p99 0.5877 -> 0.85
    "leg": 0.4296,          #                      corpus p99 0.3652 -> 0.85
    "hand": 0.7327,         #                      corpus p99 0.6228 -> 0.85
    # The root channel answers where the BODY went, from HumanPose.bodyPosition / bodyRotation, which
    # Unity scales by the avatar's size. There is no foot-gait term any more: foot lift was a
    # metre-space proxy for "is this locomotion", and that question belongs to the leg channels --
    # an in-place walk is still a walk, and the scene moves the character (validate_motionkb.py).
    "root_trans": 1.5637,   # horizontal stddev;   corpus p99 1.3291 -> 0.85
    "root_vert": 1.3009,    # vertical range;      corpus p99 1.1058 -> 0.85
    "root_heading": 142.1,  # signed-yaw stddev deg; corpus p99 120.79 -> 0.85
}

# Static threshold on the RAW signal. ONE constant now, which the previous scheme could not have.
#
# Under v2.1.0 each channel needed its own threshold because the signals were dimensionally
# incommensurable -- degrees for the torso, metres for an arm, degrees of finger curl for a hand.
# Muscle values are all the same kind of number: one joint's rotation as a fraction of its own
# limit. So "this joint barely moves" is one number across every channel.
#
# 0.02 is roughly twice the largest channel reading of the store's own reference for standing still:
# `idle` measures 0.0047 at the torso and 0.0109 at its busiest channel. Heading keeps a separate
# value because it is the one signal still in degrees.
STATIC_MUSCLE = 0.02
STATIC = {
    TORSO: STATIC_MUSCLE,
    HEAD: STATIC_MUSCLE,
    "arm": STATIC_MUSCLE,
    "leg": STATIC_MUSCLE,
    "hand": STATIC_MUSCLE,
    "root_trans": STATIC_MUSCLE,
    "root_vert": STATIC_MUSCLE,
    "root_heading": 2.0,   # degrees
}
