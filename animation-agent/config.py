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

Divisors/thresholds are FROZEN and reproducible from the frozen dumps in raw/ via calibrate_divisors.py
and calibrate_posture.py — no engine needed. Each divisor is fitted so the CORPUS p99 of its raw
signal normalises to 0.85 (ADR 0010); each threshold sits in the gap above the corpus rest cluster.
Bumping any value here is a metric_formula_version change.

The authority for what MEASURED means is, in order: ADR 0019 (corpus recalibration, v2.4.0),
ADR 0018 (posture, v2.3.0), ADR 0011 (Unity
normalised Humanoid space, v2.2.0), ADR 0010 (divisors refitted on the corpus), ADR 0007 (the
9-channel split). docs/specs/motionkb-v2-spec.md is the v2.0.0 design narrative and its §2 metric
table is three formula versions stale — read it for the rationale of the split, not for numbers.

All identifiers/comments are English (all-English-artifacts rule).
"""

SCHEMA_VERSION   = "motionkb/v2"
FORMULA_VERSION  = "v2.5.0"   # v2.5.0: the posture origin is Unity's Humanoid reference pose,
                              #         not a rest pose fitted from the corpus — the engine fixes
                              #         where zero is, the corpus only fixes scale (ADR 0020)
                              # v2.4.1: a rest observation must also run >= 1 s, so short clips
                              #         cannot pass for rest by ending before their motion shows
                              #         (ADR 0019, amended)
                              # v2.4.0: every constant refitted on the FULL frozen corpus (2446 mx_
                              #         dumps); REST_POSE = the corpus rest set's median pose, no
                              #         longer the accepted idle (ADR 0019)
                              # v2.3.0: + posture (mean-pose offset from rest) per channel (ADR 0018)
                              # v2.2.0: measured in Unity's normalised Humanoid space (ADR 0011)
BONE_MAP_VERSION = "v2.0.0"
EXTRACTOR_VERSION = "2.0.0"
# The avatar the SAMPLER runs on. Provenance only: it records which body produced the dumps in
# raw/, and it does not enter any number. See the block below RENDER_AVATAR.
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
# Measurement, unlike rendering, does not care which body it runs on. nurse_avatar stays the
# sampling host for continuity — every dump in raw/ came off it — and for no stronger reason.
#
# The -18.3%-at-the-torso figure that used to sit here was a v2.1.0 fact about METRE-space signals
# read off bone positions, and ADR 0011 retired those. What MEASURED reads today is HumanPose, which
# is Unity's normalised Humanoid space on BOTH halves: a muscle is one joint's rotation against that
# avatar's own limit, and bodyPosition is expressed in that same normalised frame — it is NOT metres
# and it does NOT scale with the body. Re-verified 2026-08-22 across three rigs whose real hip
# heights span 15.6% (nurse_avatar 0.902 m, Y Bot 0.998 m, X Bot 1.043 m), on six clips covering
# standing, walking, crouching, arms raised, CPR and free fall:
#
#     muscles      identical to 6 decimal places on every clip
#     bodyPosition agrees to ~1e-5 (worst case 1.4e-4 on nurse_cpr_30, 0.013%)
#
# So the root channel is body-independent along with the anatomical eight, and CALIBRATION_AVATAR is
# a note about provenance, not a load-bearing constant. This is what ADR 0011 decided; the paragraph
# that used to be here contradicted it.
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
# expressed against that avatar's own limit, so it is dimensionless and body-independent. Fitted
# over ALL 2446 frozen corpus dumps (calibrate_divisors.py --reuse <KB>/raw --prefix mx_ — offline,
# no engine) so that the corpus p99 normalises to 0.85, leaving 0.1-0.6% saturation. The previous
# fit used a 150-clip random sample, whose p99 sat up to 41% off the population's (root_vert); with
# every dump frozen on disk the sample has no reason to exist (ADR 0019). The 8 nursing clips are
# KB content, not calibration inputs: the calibration population is Mixamo only.
DIVISOR = {
    TORSO: 0.2969,          # per-DOF stddev RMS;  corpus p99 0.2524 -> 0.85
    HEAD: 0.4963,           #                      corpus p99 0.4219 -> 0.85
    "arm": 0.6864,          #                      corpus p99 0.5834 -> 0.85
    "leg": 0.4468,          #                      corpus p99 0.3798 -> 0.85
    "hand": 0.7546,         #                      corpus p99 0.6414 -> 0.85
    # The root channel answers where the BODY went, from HumanPose.bodyPosition / bodyRotation, which
    # are in Unity's normalised Humanoid frame — not metres, and not the sampled body's scale.
    # There is no foot-gait term any more: foot lift was a metre-space proxy for "is this
    # locomotion", and that question belongs to the leg channels --
    # an in-place walk is still a walk, and the scene moves the character (validate_motionkb.py).
    "root_trans": 1.9782,   # horizontal stddev;   corpus p99 1.6815 -> 0.85
    "root_vert": 1.8396,    # vertical range;      corpus p99 1.5636 -> 0.85
    "root_heading": 131.8575,  # signed-yaw stddev deg; corpus p99 112.079 -> 0.85
}

# Static threshold on the RAW signal. ONE constant now, which the previous scheme could not have.
#
# Under v2.1.0 each channel needed its own threshold because the signals were dimensionally
# incommensurable -- degrees for the torso, metres for an arm, degrees of finger curl for a hand.
# Muscle values are all the same kind of number: one joint's rotation as a fraction of its own
# limit. So "this joint barely moves" is one number across every channel.
#
# 0.02 is a convention with measured margins, not a fitted population split: the corpus's channel
# readings are continuous through [0.01, 0.03], so no threshold there is "natural". What the corpus
# does show (all 19568 mx_ anatomical readings): a frozen-pose spike at <= 0.001 (2393 readings,
# 990 exactly 0.0 — pose assets and locked channels), a thin valley, then sway rising smoothly.
# 0.02 sits 20x above that frozen population and ~10x below the moving median (~0.19), and moving
# it +-50% relabels little (static readings: 16.7% at 0.015, 18.0% at 0.02, 21.1% at 0.03).
# Heading keeps a separate value because it is the one signal still in degrees.
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

# ---- Posture: the orthogonal half of MEASURED (v2.3.0, ADR 0018) ----
#
# Variation (stddev over time) answers "does this joint MOVE"; it cannot see a HOLD — an arm raised
# and kept raised reads 0.0000, identical to an arm at rest. Posture is the offset of the clip's
# MEAN pose from a rest baseline: it answers "what does this channel HOLD". A held pose is
# static + displaced; locomotion is dynamic + neutral; rest is static + neutral.
#
# REFERENCE_POSE is Unity's, not this store's. HumanPose.muscles are normalised to [-1, 1] over
# each degree of freedom's HumanTrait range, and 0 is that range's CENTRE — a definition that ships
# with the engine, identical on every rig, with no estimation error and no free parameter. The root
# half is Unity's too: the Humanoid reference pose normalises to bodyPosition.y 1.0 with
# bodyRotation identity. Verified across the project's rigs on 2026-08-25: X Bot 1.000000,
# Y Bot 0.999999, Fat_man 0.999963, patient_avatar 0.999963, every one of them at tilt 0.0000 —
# humanScale puts the reference hips at 1.0 by construction, across body shapes as different as
# X Bot and Fat_man. (nurse_avatar reads 0.984676 / 1.51 deg because its imported bind pose is not
# a clean T-pose; that is a property of the asset, not of the standard, and it does not enter any
# number here.)
#
# This replaced a 95-value rest pose FITTED from the corpus (the per-DOF median of 205 clips
# selected as at-rest by measurement). That origin sat closer to how a person actually stands, but
# it was an estimate with selection parameters, and every idle differs; anchoring on the engine's
# definition removes the estimate entirely. What it costs is recorded in ADR 0020 and reproducible
# with `calibrate_posture.py --baseline fitted`: 30.99% of posture labels differ, and because
# nobody stands at the centre of their joint ranges, "displaced" now means "away from the Humanoid
# reference", not "away from a relaxed human stance" — a standing clip reads displaced on the arms,
# knees and fingers, which sit near the ends of their ranges when a person is upright and relaxed.
#
# Not derived, not fitted, not sampled: nothing recomputes this. MUSCLE_COUNT mirrors
# UnityEngine.HumanTrait.MuscleCount.
MUSCLE_COUNT = 95
REFERENCE_POSE = {
    "muscles": [0.0] * MUSCLE_COUNT,
    "body_y": 1.0,         # HumanPose.bodyPosition.y — normalised humanoid units, NOT metres
    "tilt_deg": 0.0,       # bodyRotation identity: the body's up axis is world up
}

# Same fitting rule as DIVISOR: the corpus p99 of the raw offset normalises to 0.85 (fitted over
# the 2446 mx_ dumps by calibrate_posture.py — the same Mixamo-only population as everything else;
# report in motionkb_build/reports/posture_calibration.md).
POSTURE_DIVISOR = {
    TORSO: 0.5944,          # corpus p99 0.5053 -> 0.85
    HEAD: 0.7789,           # corpus p99 0.6621 -> 0.85
    "arm": 1.0689,          # corpus p99 0.9086 -> 0.85
    "leg": 0.6813,          # corpus p99 0.5791 -> 0.85
    "hand": 1.3878,         # corpus p99 1.1797 -> 0.85
    "root_height": 1.0224,  # normalised units, not metres; corpus p99 0.8690 -> 0.85
    "root_tilt": 104.9734,  # degrees; corpus p99 89.2274 -> 0.85
}

# Neutral/displaced threshold on the RAW offset, per divisor group = 0.30 x that group's divisor —
# one constant in NORMALISED space, like STATIC_MUSCLE is one constant in muscle space.
#
# Read this against what the origin now is. Distance from the Humanoid reference is NOT near zero
# for a person standing still: muscle 0 is the centre of each joint's range, so relaxed arms
# (shoulder near one end of [-60, 100] deg), straight knees (near the end of [-80, 80]) and
# extended fingers all sit far from it. Measured against this origin, mx_Standing_Idle reads
# arms 0.47/0.44, legs 0.33/0.38, hands 0.76/0.75 — above threshold — while mx_Boxing_Idle's raised
# guard reads 0.29 on the left arm, below it. That ordering is a property of where Unity's zero is,
# not of the threshold, and it is the price ADR 0020 accepts for an origin with no estimate in it.
# 0.30-of-scale keeps the same scale-relative rule the fitted origin used, so the constant is
# comparable across the two; deliberate extremes still read high (mx_Crouch_Idle head 0.4963 and
# root height 0.4335, the hostage hold's clamped hands 0.9185/0.9278, mx_Agony_Holding_The_Head's
# cradled right arm 0.9153).
NEUTRAL = {
    TORSO: 0.1783,
    HEAD: 0.2337,
    "arm": 0.3207,
    "leg": 0.2044,
    "hand": 0.4163,
    "root_height": 0.3067,  # normalised units
    "root_tilt": 31.492,    # degrees
}
