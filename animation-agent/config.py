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
FORMULA_VERSION  = "v2.0.0"
BONE_MAP_VERSION = "v2.0.0"
EXTRACTOR_VERSION = "2.0.0"
CALIBRATION_AVATAR = "nurse_avatar.fbx"

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
DIVISOR = {
    TORSO: 54.0,        # max-lean deg;      check_pulse 45.9  -> 0.85
    HEAD: 15.4,         # head range deg;    giving_pills 13.1 -> 0.85
    "arm": 0.137,       # hips-rel stddev m; check_pulse 0.1163-> 0.85
    "leg": 0.247,       # world stddev m;    walking 0.2100    -> 0.85
    "hand": 129.0,      # curl-range deg;    giving_pills 110.0-> 0.85
    "root_gait": 0.317, # foot Y-range m;    walking 0.2694    -> 0.85
    "root_trans": 0.30, # horiz root stddev m  (Phase-2, forward-declared; corpus is in-place)
    "root_heading": 60.0,  # signed heading stddev deg (Phase-2, forward-declared)
}

# Static threshold on the RAW physical signal (never one global normalized constant — the channel
# normalizations are dimensionally incommensurable). state_label = dynamic iff raw >= threshold.
STATIC = {
    TORSO: 5.0,    # idle 2.3 / static<=3.6 | dynamic>=28.8 (deg)
    HEAD: 4.0,     # idle 1.5 / static<=2.7 | dynamic>=8.0 (deg)
    "arm": 0.015,  # idle 0.004 | dynamic>=0.022 (m)
    "leg": 0.015,  # planted<=0.006 | dynamic step>=0.066 (m)
    "hand": 5.0,   # idle/cpr<=0.2 | dynamic>=19.8 (deg)
    "root_gait": 0.10,  # step<=0.063 | walking 0.2694 (m, locomotion)
}
