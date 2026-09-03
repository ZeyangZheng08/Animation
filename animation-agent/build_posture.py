#!/usr/bin/env python3
"""
build_posture.py — Kinematic Posture State Analysis over the corpus, into `derived/posture.json`.

WHAT THIS ANSWERS. A record says how much each body part moves and what the clip means. It does not
say what the body is DOING WITH THE GROUND — standing, sitting, down on the floor — and the executor
has to know, because a plan that walks a seated character off a chair is not a plan. Through v3 that
was one label a VLM proposed; through v4 it was a single threshold on `mean_body_height` over the
whole clip. Both give one word per clip, and a clip that stands up is not one word.

So this derives a TIME STRUCTURE from the frozen `raw` dumps: which coarse state the body is in at
every frame, where those runs begin and end, and where the boundaries between them fall.

WHAT DECIDES A SEAT, AND WHY IT IS NOT AN ANGLE (2.0.0). Sitting, squatting, crouching and a wide
fighting stance share their joint angles: thighs near horizontal, a knee folded, the body low. What
separates them is not the pose, it is WHERE THE LOAD GOES.

  * Winter DA (1995), "Human balance and posture control during standing and walking", Gait &
    Posture 3:193-214. An unsupported upright posture requires the whole-body centre of mass to
    project inside the base of support formed by the feet. A squat, a crouch and a stance are all
    unsupported: the feet carry the body, so the COM stays over them. Sitting is the SUPPORTED case
    — the seat carries the load — and the COM projects BEHIND the feet, which no unsupported posture
    can hold. Hof, Gazendam and Sinke (2005), J Biomech 38:1-8, give the dynamic form of the same
    condition; the static one is enough here, because these are labels on frames rather than a
    balance controller.
  * de Leva P (1996), "Adjustments to Zatsiorsky-Seluyanov's segment inertia parameters", J Biomech
    29(9):1223-1230 — the segment mass fractions and along-segment COM locations that turn a set of
    root-local bone positions into a whole-body COM. See `DE_LEVA_SEGMENTS`.
  * ANSI/HFES 100-2007 (Human Factors Engineering of Computer Workstations) and ISO 11226:2000
    (static working postures) give seated ranges: a trunk-thigh included angle of about 90-120 deg
    (reclined to about 130) and a knee included angle of about 90-135 deg, with trunk inclination
    past 60 deg not counted as upright. See `HIP_SEATED`, `KNEE_SEATED`, `THETA_TRUNK_HORIZONTAL`.
  * Guerra BMV et al. (2020), Sensors 20(6):1602, normalise joint heights by the subject's standing
    height, because absolute positions are subject-dependent and the relations are not. Here that is
    `upright_body_height`, measured once per build off `mx_Standing_Idle`, so every height threshold
    below is a FRACTION of what this avatar reads standing.
  * Liu et al. (2017) separate the same states with deterministic rules over trunk and thigh
    orientation, and say plainly that their angle cut-offs are empirical. That is the standing rule.
  * Schenkman et al. (1990), Phys Ther 70(10):638-648: sit-to-stand is a four-phase process taking
    roughly 1.5-2 s, so `MIN_POSTURE_DURATION_S` at 0.3 s sits below one phase — short enough to
    keep every phase visible, long enough to absorb per-frame flicker.

WHAT IS MEASURED, PER FRAME (all from the dump; nothing is fitted):

    h             `body_pos.y / upright_body_height` — Unity's own `HumanPose.bodyPosition.y`, as a
                  fraction of what it reads standing. UNITY'S QUANTITY, KEPT: measured against the de
                  Leva COM it tracks it within 0.03 everywhere (0.967 against 0.935 upright, 0.64-0.69
                  against 0.61-0.66 seated), so there is nothing to gain by replacing it and a
                  well-understood engine quantity to lose. Guerra for the normalisation.
    theta_trunk   angle(Chest - Hips, +Y) — trunk inclination, 0 deg upright.
    theta_thigh   angle(LowerLeg - UpperLeg, -Y) per leg, meaned — kept for the STANDING rule.
    hip_incl      angle(Chest - Hips, LowerLeg - UpperLeg) per leg, meaned — the trunk-thigh included
                  angle the workstation standards are written in. About 180 standing, about 90 seated.
    knee_incl     angle(UpperLeg - LowerLeg, Foot - LowerLeg) per leg, minimum — 180 is a straight leg.
    bos_behind    (rearmost heel z) - xcom.z, metres. Positive means the mass projects behind the
                  heels, which is Winter's supported case and is what a seat is for.
    com_outside_bos  signed distance from the XCOM's ground projection to the convex hull of the four
                  foot points, negative inside. The general 2-D form of the same condition; reported
                  rather than ruled on, because the sitting/standing distinction is sagittal and a
                  lunge is outside its base of support sideways without being seated.
    xcom          not a feature in its own right, but what both of the above are measured on:
                  COM + v_COM / omega0, Hof et al. (2005). Equal to the COM whenever the body is
                  still, which is every held posture.

EVERY RAW QUANTITY IS STILL UNITY'S. The heights are `HumanPose.bodyPosition.y` and the positions are
`Transform.InverseTransformPoint` on the same transform the pose handler was built with, so the two
are in one frame and directly comparable. de Leva is not a second source of data: it is the WEIGHTING
applied to Unity's own bone positions to get a COM whose horizontal position can be trusted.

AND THE HORIZONTAL IS WHY. `bodyPosition` is fine as a height and unusable as a balance test.
Measured on the same frames, rear-heel z minus COM z: sitting still in a chair reads 0.008 m by
`bodyPosition` and 0.160 m by de Leva, and aiming a pistol while seated reads -0.071 m -- in FRONT of
the feet, which would make it a stance -- against 0.061 m. Crouching behind cover reads +0.092 m by
`bodyPosition`, i.e. BEHIND the feet, which would make a crouch a sit; de Leva puts it at -0.113 m,
over them, which is what a crouch is. The two disagree by up to 0.22 m along z and in both directions,
so the balance test is de Leva's and the height is Unity's.

ROOT-LOCAL METRES, ON ONE AVATAR. `bones` is `Transform.InverseTransformPoint` on the single
calibration avatar every clip in this corpus was retargeted to, so a length here is comparable across
all 2446 dumps and means nothing outside them. `h` is normalised and therefore scale-free;
`bos_behind` and `com_outside_bos` are not, and are never a distance in anybody's scene.

NO HEIGHT ABOVE THE GROUND, EITHER. `bones` is root-local, so `Foot.y` is the foot's height relative
to the character's own root and says nothing about where the scene's floor is. Support and contact are the Unity executor's to determine. `transitions.py` keeps
a planted-foot heuristic (`PLANTED_BAND_M`, measured against that clip's own lowest foot) as a
WITHIN-CLIP contact proxy; it does not claim to know the ground either.

FOUR COARSE STATES: standing / seated / floor / other, decided per frame in a FIXED ORDER.
`floor` is this project's term for a floor-level kinematic state — lying, crawling, and any other
configuration with the whole body down near the ground; it is not a standard posture name. `other`
is the conservative fallback that catches crouching, kneeling, airborne and mid-transition
configurations. Neither is an error state: a clip that is mostly `other` has been described
correctly, not skipped.

THE BOUNDARY THIS DOES NOT CROSS. `seated` here means a SEATED-LIKE BODY CONFIGURATION. Whether the
character is actually sitting ON something is a fact about a scene, and the Unity side decides it
(`seated_on_support`, contact, penetration, reachability). The two are reported separately and never
merged.

    python build_posture.py                 # (re)generate derived/posture.json
    python build_posture.py --check         # exit 1 if the sidecar is missing, stale or out of date
    python build_posture.py --resume        # keep entries already present, compute only what is missing
    python build_posture.py --only mx_Walking_Forward --report-only    # look at one clip

Stdlib only, no Unity, no pip.
"""
import argparse
import datetime
import json
import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths                                                     # noqa: E402

# Bump this whenever a threshold, a feature or a rule changes. The sidecar records it, the runtime
# refuses a sidecar that does not match, and the audit's expectations are read against it.
#
# 1.1.0 adds `root_travel` per clip. NO POSTURE RULE CHANGED: every threshold, every feature and the
# order they are applied in are byte-identical to 1.0.0, and the audit's twenty-one expectations pass
# unaltered. The version moves because the FILE's shape did, and a consumer that reads the new field
# needs a way to tell whether it is there.
#
# 2.0.0 replaces the seated rule with a mechanical one, and it is a major bump because the FEATURES
# changed: the shank angle is gone and a de Leva COM against the base of support is in. `body_pos`
# stays, as the HEIGHT -- it tracks the COM's height within 0.03 and it is Unity's own number -- and
# is not used horizontally, where it is wrong by up to 0.22 m in either direction. What went wrong with the old rule is that
# it described a POSE — low, thighs pitched, a knee folded — and a fighting stance, a squat, a duck
# behind cover, a kettlebell swing and a slide tackle all hold that pose. Eleven clips read
# standing -> seated and only two of them were sits; the agent searched for a way to sit a character
# down and was offered a spinning back kick. The fix is not a tighter angle. It is Winter's condition:
# an unsupported posture keeps the centre of mass over the feet, and a seat is what lets it go behind
# them. So the COM is computed from de Leva's segment parameters, the base of support from the feet,
# and the rule asks the mechanical question instead of the geometric one.
POSTURE_ALGORITHM_VERSION = "2.0.0"

PATH = os.path.join(paths.DERIVED_DIR, "posture.json")

# ---- the segment table ------------------------------------------------------------------------
# de Leva P (1996), "Adjustments to Zatsiorsky-Seluyanov's segment inertia parameters", J Biomech
# 29(9):1223-1230, Table 4, MALE. Each row is (mass fraction of body mass, proximal bone, distal
# bone, COM position along the segment as a fraction from the proximal end). The two-sided rows are
# listed once and applied to both sides.
#
# WHY A TABLE AND NOT `HumanPose.bodyPosition`. Unity's body position is an approximation of the
# COM that the engine documents as such, it arrives already normalised by an avatar scale nothing
# here controls, and it cannot be checked against anything. This can: the fractions sum to 1.000,
# they come from a paper with a page number, and a hand-built frame gives an answer somebody can
# work out on paper. The mass fractions below sum to 0.4346 + 0.0694 + 2 x 0.2480 = 1.0000.
DE_LEVA_TRUNK = ("Hips", "Neck", 0.4346, 0.486)
DE_LEVA_HEAD = ("Neck", "Head", 0.0694, 0.5)
DE_LEVA_SIDED = (
    ("UpperArm", "LowerArm", 0.0271, 0.5772),      # upper arm, shoulder -> elbow
    ("LowerArm", "Hand", 0.0162, 0.4574),          # forearm, elbow -> wrist
    ("Hand", "Hand", 0.0061, 0.0),                 # hand, taken at the wrist marker
    ("UpperLeg", "LowerLeg", 0.1416, 0.4095),      # thigh, hip -> knee
    ("LowerLeg", "Foot", 0.0433, 0.4459),          # shank, knee -> ankle
    ("Foot", "Toes", 0.0137, 0.4415),              # foot, ankle -> toe
)
DE_LEVA_SEGMENTS = (DE_LEVA_TRUNK, DE_LEVA_HEAD) + tuple(
    (side + a, side + b, m, f) for side in ("Left", "Right") for a, b, m, f in DE_LEVA_SIDED)

# ---- thresholds, each with a source or a measurement -------------------------------------------
# HEIGHTS ARE FRACTIONS OF `upright_body_height`, what `HumanPose.bodyPosition.y` reads on this
# avatar standing, measured once per build off `mx_Standing_Idle` (Guerra et al. 2020 normalise joint
# heights by standing height for exactly this reason). Unchanged from 1.1.0 in value, and now
# unchanged in what they divide as well: 1.x compared the same quantity against an absolute cut-off,
# and this compares it against itself upright.
H_FLOOR = 0.45                  # below this the body is down at floor level however it is arranged
H_LOW = 0.70                    # low enough that a horizontal trunk means lying, not bending over
H_SEATED = 0.80                 # a seated carriage sits below this
H_STANDING = 0.80               # a standing carriage sits at or above this
H_AIRBORNE = 1.15               # above this the body has left the ground; standing does not apply

# ISO 11226:2000 treats trunk inclination past 60 deg as not upright. Used by the floor rule (a
# horizontal trunk low down is lying) and by the seated rule (you cannot sit folded flat).
THETA_TRUNK_HORIZONTAL = 60.0

# Liu et al. (2017): standing is a high carriage on thighs that are still essentially vertical.
THETA_THIGH_UPRIGHT = 35.0

# ANSI/HFES 100-2007 and ISO 11226:2000 put seated posture at a trunk-thigh included angle of about
# 90-120 deg, reclined to about 130, and a knee included angle of about 90-135 deg. The ranges below
# are the standards' widened by the offset MEASURED on this calibration avatar: upright chair sitting
# in this corpus reads hip 77-93 and knee 64-89, because the chest vector leans forward of the trunk
# line and the thighs slope up on a seat of this height. So the floor of each range comes down by
# roughly that offset and the ceiling keeps the standards' reclined limit.
HIP_SEATED = (60.0, 130.0)
KNEE_SEATED = (50.0, 140.0)

# ZERO, AND THAT IS THE POINT. Winter's condition is "outside the base of support", not "outside it
# by a margin", and the posterior boundary of the base of support is the heel. A margin here would be
# a number with no source, and one was tried: at 0.04 m it cut through the middle of
# `mx_Aim_Pistol_While_Sitting`, whose COM sits 0.024-0.061 m behind the heel across its own frames.
# The boundary is the boundary.
COM_BEHIND_BOS_M = 0.0

# The calibration avatar's ankle-to-heel offset: how far behind the ankle joint its heel sits, along
# the foot's own axis. A property of the rig every clip in this corpus was retargeted to, stated here
# because the dumps carry no heel marker -- and the base of support has to reach the heel, or every
# standing frame reads as balanced on its toes.
HEEL_BEHIND_ANKLE_M = 0.07

# Hof, Gazendam and Sinke (2005), J Biomech 38:1-8. Balance is a condition on the EXTRAPOLATED centre
# of mass, XCOM = COM + v_COM / omega0, not on the COM itself: a body moving forward can hold its mass
# behind its feet for as long as the momentum lasts, and an inverted-pendulum model says how long.
# omega0 = sqrt(g / l) with l the pendulum length, taken here as this avatar's upright hip height
# (`pendulum_length_m`, measured per build off the reference clip, about 0.89 m, giving 1/omega0 of
# about 0.30 s).
#
# THIS IS WHAT SEPARATES A SIT FROM A DECELERATION. `mx_Change_Direction_180_Degrees_While_Running`
# plants hard to reverse, leans back, and puts its COM behind the planted foot for 23 frames -- a sit
# by the static test and obviously not one. Its COM is travelling; XCOM moves with the velocity and
# lands back over the foot. A still sit has v = 0, so XCOM is the COM and nothing changes.
GRAVITY_M_S2 = 9.81

# Schenkman et al. (1990): sit-to-stand runs four phases over 1.5-2 s, so a threshold at 0.3 s is
# below one phase — long enough to absorb per-frame flicker, short enough that no phase is hidden.
MIN_POSTURE_DURATION_S = 0.3

PARAMS = {
    "H_FLOOR": H_FLOOR,
    "H_LOW": H_LOW,
    "H_SEATED": H_SEATED,
    "H_STANDING": H_STANDING,
    "H_AIRBORNE": H_AIRBORNE,
    "THETA_TRUNK_HORIZONTAL": THETA_TRUNK_HORIZONTAL,
    "THETA_THIGH_UPRIGHT": THETA_THIGH_UPRIGHT,
    "HIP_SEATED": list(HIP_SEATED),
    "KNEE_SEATED": list(KNEE_SEATED),
    "COM_BEHIND_BOS_M": COM_BEHIND_BOS_M,
    "HEEL_BEHIND_ANKLE_M": HEEL_BEHIND_ANKLE_M,
    "GRAVITY_M_S2": GRAVITY_M_S2,
    "MIN_POSTURE_DURATION_S": MIN_POSTURE_DURATION_S,
}

# Where the upright reference comes from. One clip, named here rather than chosen at build time, so
# a rebuild on a different machine normalises by the same thing.
UPRIGHT_REFERENCE_ACTION = "mx_Standing_Idle"
# Written into _meta beside PARAMS so the file says what units it was computed in without anybody
# having to open this module. Separate from PARAMS so nothing in there is anything but a threshold.
PARAM_UNITS = {"heights": "fractions of `upright_body_height`, what HumanPose.bodyPosition.y "
                          "reads on this avatar standing (Guerra et al. 2020)",
               "angles": "degrees",
               "durations": "seconds",
               "lengths": "metres, root-local, on the single calibration avatar the whole corpus "
                          "was retargeted to. COM_BEHIND_BOS_M and HEEL_BEHIND_ANKLE_M are the "
                          "only two, and neither is a distance in any scene.",
               "upright_body_height": "HumanPose.bodyPosition.y meaned over mx_Standing_Idle. "
                                      "Every height threshold is a fraction of it.",
               "pendulum_length_m": "metres: the upright hip height over mx_Standing_Idle, which "
                                    "sets omega0 = sqrt(g / l) for Hof's extrapolated COM."}

POSTURES = ("standing", "seated", "floor", "other")

# The five bones per side plus the two trunk bones. Named here rather than taken from config.py's
# bone map because that map is the EXTRACTOR's channel grouping; this is a different question about
# the same skeleton, and coupling them would make a channel edit silently move a posture boundary.
_TRUNK = ("Hips", "Chest")
_LEG = ("UpperLeg", "LowerLeg", "Foot")

UP = (0.0, 1.0, 0.0)
DOWN = (0.0, -1.0, 0.0)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- per-frame geometry -----------------------------------------------------------------------

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _angle_deg(u, v):
    """Angle between two vectors in degrees. A degenerate (zero-length) vector returns 0.0 rather
    than raising: two coincident joints mean the dump has nothing to say about that angle, and one
    frame of one leg is not a reason to refuse to describe a clip."""
    nu = math.sqrt(u[0] * u[0] + u[1] * u[1] + u[2] * u[2])
    nv = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if nu < 1e-9 or nv < 1e-9:
        return 0.0
    d = (u[0] * v[0] + u[1] * v[1] + u[2] * v[2]) / (nu * nv)
    return math.degrees(math.acos(max(-1.0, min(1.0, d))))


def root_travel(raw):
    """How far the clip carries the PELVIS, in world metres: (dx, dz) and the yaw it turns through.

    THE NAME IS OLDER THAN THE MEANING, and the meaning is the one a seat needs. This is not the
    clip's root motion. It is the total world displacement of the hips over the clip, which is the
    root motion PLUS the change in where the pelvis sits relative to the root -- and the two are
    added here for free, because these dumps were sampled with `lockRootPositionXZ = true`, so the
    root never moved and `bones.Hips` carries the whole of it.

    MEASURED, ON `mx_Standing_To_Sitting_Transition`, AND THE THREE NUMBERS RECONCILE EXACTLY:

        root curve, first frame to last     0.3309 m   what the transform is moved by
        pelvis root-local offset, ditto     0.1152 m   she folds backwards over the root as she sits
        their sum                           0.4459 m   what this function returns (0.4461)

    WHICH ONE A PLACEMENT NEEDS. `scene.standing_point_for` solves "where must she stand so that this
    clip finishes with her ON the seat", and what has to land on the seat is the PELVIS -- that is
    what `seat_alignment` measures. So the sum is the right quantity and the root curve is not:
    placing her by the root curve alone leaves the pelvis 0.115 m short of the seat, which is more
    than twice the gate's whole tolerance.

    THE ONE RESIDUAL, STATED. Strictly the standing point should be the seat minus (root motion + the
    pelvis offset at the clip's LAST frame); this returns the difference between the last and the
    FIRST, so it is short by the pelvis offset at the first frame. At the start of a sit-down she is
    standing upright, so that offset is (0.008, -0.011) m -- 0.013 m, a quarter of the tolerance, and
    it is visible in the landing: the same plan measures 0.0104 m off the seat centre from a walking
    base and 0.0023 m from a stance. Worth knowing about; not worth a second field.

    READ OFF THE ROOT-LOCAL HIPS, WHICH IS WHERE THE COMMITTED DUMPS KEEP IT. The corpus was sampled
    while every clip was imported with `lockRootPositionXZ = true`, so the travel sits in the pose:
    `root_pos` is constant across every dump and `bones.Hips` carries the displacement. The importer
    has since been changed (4b) so that new imports put the travel in root motion instead, which is
    what stops a walk sliding the character across the floor — but the DUMPS are not resampled, and
    they do not need to be. A clip's travel is a fact about the clip; which of the two columns holds
    it is a fact about an import setting, and the rotations every other derivation reads are
    bit-identical either way (verified against a resample: `bone_rot` matches exactly).

    Yaw comes from the Hips' own rotation rather than `root_fwd`, for the same reason: rotation is
    baked into the pose in these dumps, so `root_fwd` is constant in all 2446 of them and says
    nothing.
    """
    bones = raw.get("bones") or {}
    hips = bones.get("Hips")
    if not hips or len(hips) < 2:
        return {"dx": 0.0, "dz": 0.0, "distance_m": 0.0, "yaw_deg": 0.0}
    dx = hips[-1][0] - hips[0][0]
    dz = hips[-1][2] - hips[0][2]

    yaw = 0.0
    track = (raw.get("bone_rot") or {}).get("Hips")
    if track and len(track) >= 2:
        yaw = _wrap_deg(_yaw_deg(track[-1]) - _yaw_deg(track[0]))
    return {"dx": round(dx, 4), "dz": round(dz, 4),
            "distance_m": round(math.sqrt(dx * dx + dz * dz), 4),
            "yaw_deg": round(yaw, 2)}


def _yaw_deg(q):
    """Heading of a quaternion (xyzw), in degrees about +Y. The clip's forward, flattened."""
    x, y, z, w = q
    fx = 2.0 * (x * z + w * y)
    fz = 1.0 - 2.0 * (x * x + y * y)
    return math.degrees(math.atan2(fx, fz))


def _wrap_deg(angle):
    """Into (-180, 180]. A turn of 350 degrees one way is 10 degrees the other, and a planner that
    read the first would face the character backwards."""
    while angle <= -180.0:
        angle += 360.0
    while angle > 180.0:
        angle -= 360.0
    return angle


def required_bones():
    """Every bone a dump has to carry for this to run: the two the trunk angle needs, plus every
    bone named anywhere in the de Leva table."""
    named = set(_TRUNK)
    for a, b, _, _ in DE_LEVA_SEGMENTS:
        named.add(a)
        named.add(b)
    return tuple(sorted(named))


# ---- mechanics: where the mass is, and what is under it ----------------------------------------

def centre_of_mass(frame):
    """Whole-body COM for one frame, from de Leva's segment parameters. Pure.

    `frame` is {bone_name: (x, y, z)} in root-local metres — one frame of a dump's `bones`. Each
    segment contributes its mass fraction at a point `f` of the way from its proximal bone to its
    distal one; the hand row is a point mass at the wrist, which is what a bone set without finger
    mass can honestly say. The fractions sum to 1, so this is a weighted mean and needs no
    normalisation afterwards.
    """
    x = y = z = 0.0
    for proximal, distal, mass, along in DE_LEVA_SEGMENTS:
        a, b = frame[proximal], frame[distal]
        x += mass * (a[0] + along * (b[0] - a[0]))
        y += mass * (a[1] + along * (b[1] - a[1]))
        z += mass * (a[2] + along * (b[2] - a[2]))
    return (x, y, z)


def base_of_support(frame):
    """The ground-plane points the feet stand on: (x, z) for each ankle, toe and heel. Pure.

    Six points, three per side. The heel is not a bone in these dumps, so it is placed
    `HEEL_BEHIND_ANKLE_M` behind the ankle along the foot's own axis; without it the base of support
    would end at the ankle and a standing character would read as balanced on her toes. A foot whose
    toe sits on its ankle has no axis, and contributes the ankle twice rather than a guess.
    """
    points = []
    for side in ("Left", "Right"):
        ankle, toe = frame[side + "Foot"], frame[side + "Toes"]
        points.append((ankle[0], ankle[2]))
        points.append((toe[0], toe[2]))
        dx, dz = toe[0] - ankle[0], toe[2] - ankle[2]
        span = math.hypot(dx, dz)
        if span < 1e-6:
            points.append((ankle[0], ankle[2]))
        else:
            points.append((ankle[0] - HEEL_BEHIND_ANKLE_M * dx / span,
                           ankle[2] - HEEL_BEHIND_ANKLE_M * dz / span))
    return points


def extrapolated_com(com, velocity, omega0):
    """Hof's XCOM: the COM plus its velocity divided by the pendulum's natural frequency. Pure.

    WHAT IT IS FOR. Winter's static condition asks where the mass IS; a body in motion also has
    somewhere it is GOING, and an inverted pendulum says how far: a COM travelling at v carries the
    equivalent of v / omega0 of extra reach before it has to be caught. Standing still, v is zero and
    XCOM is the COM, so nothing about a held posture changes.
    """
    return (com[0] + velocity[0] / omega0,
            com[1] + velocity[1] / omega0,
            com[2] + velocity[2] / omega0)


def com_velocities(coms, frame_rate):
    """d(COM)/dt per frame, by central difference over one frame. Pure.

    Central because a one-sided difference on a 30 fps dump lags the motion by half a frame in a
    direction that depends on which side it took, and the ends use the one-sided form because there
    is nothing on the other side of them. A single-frame clip has no velocity and answers zero.
    """
    fps = float(frame_rate or 30)
    n = len(coms)
    if n < 2:
        return [(0.0, 0.0, 0.0)] * n
    out = []
    for f in range(n):
        if f == 0:
            a, b, span = coms[0], coms[1], 1.0
        elif f == n - 1:
            a, b, span = coms[n - 2], coms[n - 1], 1.0
        else:
            a, b, span = coms[f - 1], coms[f + 1], 2.0
        out.append(((b[0] - a[0]) * fps / span,
                    (b[1] - a[1]) * fps / span,
                    (b[2] - a[2]) * fps / span))
    return out


def heels_behind(frame, com):
    """Metres the COM projects behind the REARMOST heel. Positive is Winter's supported case. Pure.

    Sagittal, because that is the axis the question is about: a seat is behind you. The 2-D form is
    `com_outside_bos` below, which is the honest general statement of the same condition and is
    reported rather than ruled on -- a lunge puts the COM outside its base of support sideways
    without anybody sitting down.
    """
    heels = []
    for side in ("Left", "Right"):
        ankle, toe = frame[side + "Foot"], frame[side + "Toes"]
        dx, dz = toe[0] - ankle[0], toe[2] - ankle[2]
        span = math.hypot(dx, dz)
        heels.append(ankle[2] if span < 1e-6 else ankle[2] - HEEL_BEHIND_ANKLE_M * dz / span)
    return min(heels) - com[2]


def convex_hull(points):
    """Monotone-chain convex hull, counter-clockwise, first point not repeated. Pure.

    Returns the input's distinct points when there are fewer than three of them, or when they are
    collinear -- both are degenerate "hulls" that `signed_distance_to_hull` handles as a segment or
    a point rather than as a polygon.
    """
    pts = sorted(set((round(x, 9), round(z, 9)) for x, z in points))
    if len(pts) < 3:
        return pts

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and _cross(out[-2], out[-1], p) <= 0:
                out.pop()
            out.append(p)
        return out

    lower, upper = half(pts), half(reversed(pts))
    hull = lower[:-1] + upper[:-1]
    return hull if len(hull) >= 3 else pts


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _point_segment_distance(p, a, b):
    dx, dz = b[0] - a[0], b[1] - a[1]
    span = dx * dx + dz * dz
    if span < 1e-18:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / span))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dz))


def signed_distance_to_hull(point, points):
    """Distance from a ground-plane point to the convex hull of `points`. NEGATIVE INSIDE. Pure.

    Winter's condition, stated as one number: the COM projection is inside the base of support when
    this is negative, and how far outside it is when it is not. A degenerate hull -- one foot point,
    or a line of them -- answers with the distance to that point or that segment, which is the
    correct reading of a base of support with no area.
    """
    hull = convex_hull(points)
    if not hull:
        return 0.0
    if len(hull) == 1:
        return math.hypot(point[0] - hull[0][0], point[1] - hull[0][1])
    if len(hull) == 2:
        return _point_segment_distance(point, hull[0], hull[1])
    inside = all(_cross(hull[i], hull[(i + 1) % len(hull)], point) >= 0 for i in range(len(hull)))
    edge = min(_point_segment_distance(point, hull[i], hull[(i + 1) % len(hull)])
               for i in range(len(hull)))
    return -edge if inside else edge


def upright_body_height(raw):
    """What `HumanPose.bodyPosition.y` reads on this avatar standing: the mean over the reference
    clip's frames.

    ONE NUMBER PER BUILD, from `UPRIGHT_REFERENCE_ACTION`, and it is what every height threshold is a
    fraction of. Guerra et al. (2020) normalise joint heights by standing height for the reason this
    needs it: a threshold has to mean the same thing on a different avatar, and an absolute number
    does not.

    UNITY'S QUANTITY, NOT DE LEVA'S, and that is measured rather than assumed: the two heights track
    each other within 0.03 across this corpus, so a de Leva COM buys nothing vertically. It is used
    horizontally, where `bodyPosition` is wrong -- see the module docstring.
    """
    body = raw.get("body_pos")
    if not body:
        raise ValueError("the upright reference dump has no body_pos")
    return sum(p[1] for p in body) / float(len(body))


def _bone_frames(raw):
    """The dump's `bones` as a list of {bone: (x, y, z)}, one entry per frame. Raises when a bone
    the segment table names is absent — a COM computed from a partial body is not a COM."""
    bones = raw.get("bones") or {}
    missing = [b for b in required_bones() if b not in bones]
    if missing:
        raise ValueError("dump is missing bone(s): %s" % ", ".join(missing))
    n = int(raw.get("frames") or 0) or min(len(bones[b]) for b in required_bones())
    n = min(n, *(len(bones[b]) for b in required_bones()))
    if n < 1:
        raise ValueError("dump has no frames")
    return [{b: bones[b][f] for b in required_bones()} for f in range(n)]


def frame_features(frame, body_height, upright_body_height_m, velocity=(0.0, 0.0, 0.0),
                   omega0=None):
    """One frame's [h, theta_trunk, theta_thigh, hip_incl, knee_incl, bos_behind,
    com_outside_bos]. Pure: `frame` is {bone: (x, y, z)}, `body_height` is that frame's
    `body_pos.y`, `velocity` is that frame's COM velocity, and nothing else is read.

    `bos_behind` and `com_outside_bos` are measured on the EXTRAPOLATED COM (Hof et al. 2005), so a
    body that is moving is judged on where its momentum is taking it. `omega0` of None means "no
    pendulum given", which is the still case and reduces XCOM to the COM.

    The two legs are reduced here, where the reduction is stated once. `theta_thigh` and `hip_incl`
    are the MEAN of the two sides — a posture is a property of the whole body, and one leg swinging
    through a walk cycle should not flip it. `knee_incl` is the MINIMUM, because the seated test asks
    whether A knee is bent and somebody sitting with one leg stretched out is still sitting.
    """
    com = centre_of_mass(frame)
    balance = com if omega0 in (None, 0) else extrapolated_com(com, velocity, omega0)
    hips, chest = frame["Hips"], frame["Chest"]
    trunk = _sub(chest, hips)
    theta_trunk = _angle_deg(trunk, UP)
    thigh = hip = 0.0
    knee = 360.0
    for side in ("Left", "Right"):
        upper = frame[side + "UpperLeg"]
        lower = frame[side + "LowerLeg"]
        foot = frame[side + "Foot"]
        femur = _sub(lower, upper)
        thigh += _angle_deg(femur, DOWN)
        hip += _angle_deg(trunk, femur)
        knee = min(knee, _angle_deg(_sub(upper, lower), _sub(foot, lower)))
    return (body_height / upright_body_height_m, theta_trunk, thigh / 2.0, hip / 2.0, knee,
            heels_behind(frame, balance),
            signed_distance_to_hull((balance[0], balance[2]), base_of_support(frame)))


def features(raw, upright_body_height_m, pendulum_length_m=None):
    """Every frame of one dump, through `frame_features`.

    The height comes from `body_pos` and everything else from `bones`. The two are sampled in one
    frame -- the pose handler is built with the instance transform and the bones are read with
    `InverseTransformPoint` on that same transform -- so they line up without any conversion.

    THE VELOCITY IS WHY THIS IS NOT A MAP OVER FRAMES. `frame_features` is pure over one frame, but
    Hof's XCOM needs d(COM)/dt, so the COM series is built first and differenced, and each frame is
    then handed its own velocity.
    """
    frames = _bone_frames(raw)
    body = raw.get("body_pos")
    if not body:
        raise ValueError("dump has no body_pos")
    n = min(len(frames), len(body))
    if n < 1:
        raise ValueError("dump has no frames")
    frames, body = frames[:n], body[:n]

    omega0 = None if not pendulum_length_m else math.sqrt(GRAVITY_M_S2 / pendulum_length_m)
    velocities = com_velocities([centre_of_mass(f) for f in frames], raw.get("frame_rate"))
    return [frame_features(frames[f], body[f][1], upright_body_height_m, velocities[f], omega0)
            for f in range(n)]


def label_frame(frame):
    """One frame's coarse state. Pure, over `frame_features`' tuple.

    THE ORDER IS THE RULE: floor, then seated, then standing, then the fallback. Each test is only
    reached because the ones above it did not fire, so `seated` never has to exclude lying and
    `standing` never has to exclude sitting.
    """
    h, theta_trunk, theta_thigh, hip_incl, knee_incl, bos_behind, _outside = frame

    # 1. FLOOR — either the body is simply down there, or the trunk is horizontal and low, which is
    #    lying and crawling. The height clause alone would miss a prone crawl on hands and knees;
    #    the trunk clause alone would catch a deep bend at standing height.
    if h < H_FLOOR or (theta_trunk > THETA_TRUNK_HORIZONTAL and h < H_LOW):
        return "floor"

    # 2. SEATED — a low carriage, held in the workstation standards' seated ranges, with the trunk
    #    upright enough to be sitting rather than folded, AND THE MASS BEHIND THE FEET.
    #
    #    THE LAST CLAUSE IS THE ONE THAT DECIDES. Everything above it is satisfied by a fighting
    #    stance, a duck behind cover and the bottom of a kettlebell swing, all of which are low with
    #    the hip and knee folded — measured, and it is why eleven clips used to read standing ->
    #    seated when two of them were sits. What none of them can do is put the mass behind the
    #    heels, because nothing is holding them up but their own feet (Winter 1995). A seat can, and
    #    that is what a seat is.
    #
    #    ON THE EXTRAPOLATED COM, NOT THE COM (Hof et al. 2005), and with NO MARGIN. The boundary of
    #    the base of support is the heel, so the test is `> 0`; a margin would be a number with no
    #    source. What keeps a hard deceleration out is not a margin but the velocity term -- a runner
    #    planting to reverse has her mass behind the foot and her momentum carrying it forward again.
    if (h < H_SEATED
            and HIP_SEATED[0] <= hip_incl <= HIP_SEATED[1]
            and KNEE_SEATED[0] <= knee_incl <= KNEE_SEATED[1]
            and theta_trunk <= THETA_TRUNK_HORIZONTAL
            and bos_behind > COM_BEHIND_BOS_M):
        return "seated"

    # 3. STANDING — carried high, on legs that are still under the body. Bending over stays standing
    #    (the thighs are vertical and the carriage stays high); a jump does not (`H_AIRBORNE`).
    if H_STANDING <= h <= H_AIRBORNE and theta_thigh < THETA_THIGH_UPRIGHT:
        return "standing"

    # 4. OTHER — crouching, kneeling, airborne, mid-transition. Conservative, not wrong.
    return "other"


# ---- time structure ---------------------------------------------------------------------------

def min_frames_for(frame_rate):
    """`MIN_POSTURE_DURATION_S` in frames, at this clip's rate. The threshold is stated in SECONDS so
    a 60 fps dump and a 30 fps dump get the same answer about the same motion."""
    return max(1, int(round(MIN_POSTURE_DURATION_S * float(frame_rate or 30))))


def _mode_filter(labels, window):
    """Median filter over a categorical signal, which is the mode over the window.

    Ties go to the frame's own label when it is among them, and otherwise to the alphabetically
    first, so the result does not depend on iteration order.
    """
    if window <= 1 or len(labels) <= 1:
        return list(labels)
    half = window // 2
    out = []
    for i in range(len(labels)):
        counts = {}
        for lab in labels[max(0, i - half):min(len(labels), i + half + 1)]:
            counts[lab] = counts.get(lab, 0) + 1
        top = max(counts.values())
        tied = sorted(k for k, v in counts.items() if v == top)
        out.append(labels[i] if labels[i] in tied else tied[0])
    return out


def _runs(labels):
    """[(label, start_frame, end_frame)] covering every frame exactly once."""
    runs = []
    start = 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[start]:
            runs.append((labels[start], start, i - 1))
            start = i
    return runs


def _absorb_short(labels, min_frames):
    """Fold every run shorter than `min_frames` into a neighbour, shortest run first.

    The mode filter removes single-frame flicker; it does not remove a five-frame excursion when the
    window is nine. This does, and it is what makes the stated guarantee true: no segment is shorter
    than `MIN_POSTURE_DURATION_S` unless it is the clip's only segment (a two-frame Mixamo pose has
    one state and no way to have a longer one).

    A short run joins the LONGER of its two neighbours, ties to the earlier one, so the choice does
    not depend on which end the scan started from.
    """
    labels = list(labels)
    while True:
        runs = _runs(labels)
        if len(runs) <= 1:
            return labels
        victim = None
        for i, (_, a, b) in enumerate(runs):
            n = b - a + 1
            if n >= min_frames:
                continue
            if victim is None or n < victim[1]:
                victim = (i, n)
        if victim is None:
            return labels
        i = victim[0]
        _, a, b = runs[i]
        before = runs[i - 1] if i > 0 else None
        after = runs[i + 1] if i + 1 < len(runs) else None
        if before is None:
            take = after
        elif after is None:
            take = before
        else:
            take = before if (before[2] - before[1]) >= (after[2] - after[1]) else after
        for f in range(a, b + 1):
            labels[f] = take[0]


def analyse(raw, upright_body_height_m, pendulum_length_m=None):
    """One dump's posture structure: the entry that goes into the sidecar."""
    frames = features(raw, upright_body_height_m, pendulum_length_m)
    min_frames = min_frames_for(raw.get("frame_rate"))
    window = min_frames if min_frames % 2 else min_frames + 1
    labels = [label_frame(f) for f in frames]
    labels = _mode_filter(labels, min(window, len(labels)))
    labels = _absorb_short(labels, min_frames)

    runs = _runs(labels)
    segments = [{"posture": lab, "start_frame": a, "end_frame": b} for lab, a, b in runs]
    # A BOUNDARY EVENT, not an interval and not a fourth posture: the frame on which the new state
    # begins. A clip that stands up has one standing/seated boundary, and the frames on either side
    # of it belong to the two states, not to the change.
    transitions = [{"from": runs[i - 1][0], "to": runs[i][0], "at_frame": runs[i][1]}
                   for i in range(1, len(runs))]

    held = {}
    for lab, a, b in runs:
        held[lab] = held.get(lab, 0) + (b - a + 1)
    dominant = max(sorted(held), key=lambda lab: held[lab])

    return {
        "start_posture": runs[0][0],
        "end_posture": runs[-1][0],
        "dominant_posture": dominant,
        "posture_segments": segments,
        "posture_transitions": transitions,
        "root_travel": root_travel(raw),
    }


# ---- the sidecar ------------------------------------------------------------------------------

def _document(entries, upright, pendulum):
    """The sidecar, minus `generated_at` — everything that is a function of the corpus and the
    algorithm, and nothing that is a function of when this ran. See `write_sidecar`."""
    return {
        "_meta": {
            "kind": "derived",
            "version": POSTURE_ALGORITHM_VERSION,
            "derived_from": "raw/<clip_name>.json: body_pos (HumanPose.bodyPosition) for height, "
                            "as a fraction of `upright_body_height`, and root-local bone positions "
                            "for the angles and for the de Leva (1996) whole-body COM the balance "
                            "test uses. Every raw quantity is Unity's; de Leva is the weighting.",
            "regenerate": "python build_posture.py",
            "params": PARAMS,
            "param_units": PARAM_UNITS,
            "upright_body_height": upright,
            "pendulum_length_m": pendulum,
            "upright_reference": UPRIGHT_REFERENCE_ACTION,
            "postures": list(POSTURES),
            "root_travel": "per clip: (dx, dz) metres and yaw_deg, the displacement between its first "
                           "and last frame, read off the root-local Hips. What a planner works "
                           "backwards from to place a character before a transition clip starts.",
            "action_count": len(entries),
            "note": "Regenerable sidecar, not part of the motionkb/v4 contract; no record references "
                    "it. `seated` is a seated-like body configuration, not a claim that the "
                    "character is sitting on anything -- the Unity executor decides that.",
        },
        "actions": entries,
    }


def write_sidecar(entries, upright, pendulum, path=None):
    """Write the sidecar, and DO NOT touch the file when only the clock has moved.

    `generated_at` is the one field that changes on every run, so writing it unconditionally would
    make `git status` report a change after every build and cost the KB its drift detector. The
    document is therefore compared without it, and the timestamp is only advanced when something
    else actually differs. Returns (path, changed).
    """
    path = path or PATH
    doc = _document(entries, upright, pendulum)
    if _on_disk_matches(path, doc):
        return path, False
    meta = doc["_meta"]
    # generated_at sits right after `version`, so the head of the file reads as provenance.
    doc["_meta"] = dict(kind=meta["kind"], version=meta["version"], generated_at=_now(),
                        **{k: v for k, v in meta.items() if k not in ("kind", "version")})
    paths.write_json(path, doc)
    return path, True


def _on_disk_matches(path, doc):
    """True when the file at `path` is this document apart from its `generated_at`."""
    if not os.path.exists(path):
        return False
    try:
        on_disk = paths.read_json(path)
    except Exception:
        return False
    if not isinstance(on_disk, dict):
        return False
    on_disk = json.loads(json.dumps(on_disk))
    (on_disk.get("_meta") or {}).pop("generated_at", None)
    return on_disk == doc


_SIDECAR = None


def read_sidecar(path=None, refresh=False):
    """{action_id: entry} from the sidecar, or a SystemExit saying how to make one.

    STRICT ON PURPOSE. A missing or superseded sidecar is not a reason to fall back to a weaker rule
    and carry on: the executor refuses to walk a seated character off a chair, and a fallback that
    quietly called everything `standing` would turn that refusal off without saying so.
    """
    global _SIDECAR
    if _SIDECAR is not None and path is None and not refresh:
        return _SIDECAR
    p = path or PATH
    if not os.path.exists(p):
        raise SystemExit(
            "no posture sidecar at %s.\n"
            "Every action's posture is read from it. Build it:  python build_posture.py"
            % paths.rel(p))
    doc = paths.read_json(p)
    version = (doc.get("_meta") or {}).get("version")
    if version != POSTURE_ALGORITHM_VERSION:
        raise SystemExit(
            "%s was built by posture algorithm %s; this code is %s.\n"
            "Rebuild it:  python build_posture.py"
            % (paths.rel(p), version, POSTURE_ALGORITHM_VERSION))
    entries = doc.get("actions") or {}
    if path is None:
        _SIDECAR = entries
    return entries


def forget_sidecar():
    """Drop the memo. The builder calls it after writing; nothing else needs it."""
    global _SIDECAR
    _SIDECAR = None


# ---- the build --------------------------------------------------------------------------------

def _corpus():
    """[(action_id, clip_name)] for every accepted record, in action_id order."""
    out = []
    for p, doc, err in paths.read_records(paths.accepted_files()):
        if err:
            raise SystemExit("cannot read %s: %s" % (paths.rel(p), err))
        aid = doc.get("action_id")
        clip = (doc.get("source_clip") or {}).get("clip_name")
        if not aid or not clip:
            raise SystemExit("%s has no action_id or no source_clip.clip_name" % paths.rel(p))
        out.append((aid, clip))
    return sorted(out)


def _one(job):
    action_id, clip_name, upright, pendulum = job
    p = os.path.join(paths.RAW_DIR, clip_name + ".json")
    try:
        with open(p, encoding="utf-8") as fh:
            raw = json.load(fh)
        return (action_id, analyse(raw, upright, pendulum), None)
    except Exception as e:
        return (action_id, None, "%s: %s" % (type(e).__name__, e))


def read_raw(clip_name):
    with open(os.path.join(paths.RAW_DIR, clip_name + ".json"), encoding="utf-8") as fh:
        return json.load(fh)


def pendulum_length(raw):
    """The inverted pendulum's length for Hof's omega0: this avatar's upright hip height, in metres.

    THE HIP, NOT THE COM. Hof's model swings the body about the ankle, and the length that sets
    omega0 is the height of the mass above it; the hip is the joint the whole trunk hangs from and is
    what the paper's own figures use. Measured once per build off the reference clip, about 0.89 m
    here, which puts 1 / omega0 at about 0.30 s.
    """
    hips = (raw.get("bones") or {}).get("Hips")
    if not hips:
        raise ValueError("the upright reference dump has no Hips track")
    return sum(p[1] for p in hips) / float(len(hips))


def upright_reference(corpus):
    """This build's `upright_body_height`, off `UPRIGHT_REFERENCE_ACTION`.

    FATAL WHEN IT IS ABSENT, rather than falling back to a constant. Every height threshold is a
    fraction of this number; a build that guessed it would produce a sidecar whose thresholds mean
    something nobody stated, and the version would not say so.
    """
    for action_id, clip_name in corpus:
        if action_id == UPRIGHT_REFERENCE_ACTION:
            raw = read_raw(clip_name)
            return upright_body_height(raw), pendulum_length(raw)
    raise SystemExit(
        "the upright reference %s is not in the accepted store, and every height threshold is a\n"
        "fraction of its COM height. Restore it, or change UPRIGHT_REFERENCE_ACTION and bump\n"
        "POSTURE_ALGORITHM_VERSION." % UPRIGHT_REFERENCE_ACTION)


def build(jobs, workers=8, progress=None):
    """{action_id: entry} plus a list of failures. Threaded because the dumps live on a DrvFs mount
    where the read, not the arithmetic, is the cost."""
    entries, failures, done = {}, [], 0
    with ThreadPoolExecutor(max(1, min(workers, len(jobs) or 1))) as pool:
        for action_id, entry, err in pool.map(_one, jobs):
            done += 1
            if err:
                failures.append((action_id, err))
            else:
                entries[action_id] = entry
            if progress and done % 200 == 0:
                progress(done, len(jobs))
    return entries, failures


def _summary(entries):
    counts = {p: 0 for p in POSTURES}
    changing = 0
    for e in entries.values():
        counts[e["dominant_posture"]] = counts.get(e["dominant_posture"], 0) + 1
        if e["posture_transitions"]:
            changing += 1
    return counts, changing


def main(argv):
    ap = argparse.ArgumentParser(description="Build derived/posture.json from the frozen raw dumps.")
    ap.add_argument("--only", action="append", default=None,
                    help="restrict to these action_ids (repeatable)")
    ap.add_argument("--resume", action="store_true",
                    help="keep entries already in the sidecar; compute only the missing ones")
    ap.add_argument("--report-only", action="store_true", help="print, write nothing")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 unless the sidecar exists, matches this version and is up to date")
    ap.add_argument("--out", default=None)
    ap.add_argument("--jobs", type=int, default=8)
    args = ap.parse_args(argv)

    paths.require_kb()
    corpus = _corpus()
    if args.only:
        wanted = set(args.only)
        corpus = [row for row in corpus if row[0] in wanted]
        missing = wanted - {a for a, _ in corpus}
        if missing:
            print("not accepted actions: %s" % ", ".join(sorted(missing)))
            return 2

    kept = {}
    if args.resume and not args.only and os.path.exists(args.out or PATH):
        try:
            kept = read_sidecar(args.out, refresh=True)
        except SystemExit as e:
            print("  (not resuming: %s)" % str(e).splitlines()[0])
            kept = {}
        corpus = [row for row in corpus if row[0] not in kept]
        print("resuming: %d already present, %d to compute" % (len(kept), len(corpus)))

    # --check is a gate line in check_kb.sh, so it says one thing: current, or stale and how to fix
    # it. The distribution and the progress ticks are for somebody watching a rebuild.
    quiet = args.check
    # ONE NUMBER, BEFORE ANYTHING IS LABELLED. Every height threshold is a fraction of it, so it is
    # read off the whole reference clip even when `--only` restricts what gets labelled.
    upright, pendulum = upright_reference(_corpus())
    if not quiet:
        print("posture algorithm %s over %d action(s)" % (POSTURE_ALGORITHM_VERSION, len(corpus)))
        print("upright body height (%s): %.4f   pendulum length: %.4f m"
              % (UPRIGHT_REFERENCE_ACTION, upright, pendulum))
    entries, failures = build(
        [(a, c, upright, pendulum) for a, c in corpus], workers=args.jobs,
        progress=None if quiet else (lambda d, n: print("  %d/%d" % (d, n))))
    for action_id, err in failures:
        print("  FAIL  %s: %s" % (action_id, err))
    entries.update({k: v for k, v in kept.items() if k not in entries})

    counts, changing = _summary(entries)
    if not quiet:
        print("\ndominant posture: " + ", ".join("%s=%d" % (p, counts.get(p, 0)) for p in POSTURES))
        print("%d of %d clips change posture at least once" % (changing, len(entries)))
    if args.only or args.report_only:
        for action_id in sorted(entries):
            e = entries[action_id]
            print("  %-52s %-8s %s" % (
                action_id, e["dominant_posture"],
                " ".join("%s[%d-%d]" % (s["posture"], s["start_frame"], s["end_frame"])
                         for s in e["posture_segments"])))

    if failures:
        return 1
    if args.check:
        path = args.out or PATH
        if not os.path.exists(path):
            print("STALE: %s does not exist (run build_posture.py)" % paths.rel(path))
            return 1
        if not _on_disk_matches(path, _document(entries, upright, pendulum)):
            print("STALE: %s is out of date (regenerate via build_posture.py)" % paths.rel(path))
            return 1
        print("%s up to date (%d actions)" % (paths.rel(path), len(entries)))
        return 0
    if args.report_only:
        return 0
    if args.only:
        print("\n--only computes a subset; refusing to write a partial sidecar. "
              "Add --report-only, or run without --only.")
        return 2

    path, changed = write_sidecar(entries, upright, pendulum, path=args.out)
    forget_sidecar()
    print("\n%s %s (%d actions)" % ("wrote" if changed else "unchanged:", paths.rel(path),
                                    len(entries)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
