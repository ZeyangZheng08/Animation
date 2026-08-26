"""
metrics.py — the v2 per-channel motion metrics, computed in pure Python from sampled poses.

Input `raw` (one clip), as produced by the Unity sampler (unity_sampler.py):
    {
      "frames": int,
      "bones":  { "<HumanBodyBones name>": [[x,y,z], ... per frame] },   # ROOT-LOCAL (root motion factored out)
      "root_pos": [[x,y,z], ... per frame],   # world (for the root translation signal)
      "root_fwd": [[x,y,z], ... per frame],   # world forward (for the root heading signal)
    }

Signals (mirror the validated 2026-06-18 calibration):
  torso  = max over frames of angle(Chest-Hips, +up)              [deg]  (rest~0, a held lean is engagement)
  head   = range over frames of angle(Head-Neck, Neck-Chest)      [deg]  (range removes the ~19deg rest pedestal)
  arm    = mean over arm bones of stddev(bone - Hips)             [m]    (hips-relative)
  leg    = mean over leg bones of stddev(bone)                    [m]    (world==local; clips in-place)
  hand   = range over frames of mean-finger-curl                  [deg]  (curl = sum of the two knuckle bends)
  root   = max(gait/Dg, trans/Dt, heading/Dh); gait = mean foot Y-range; trans/heading from root_pos/root_fwd

Stdlib only — no numpy — to match agent/motionkb/validate_motionkb.py's zero-dependency ethos.
"""
import math
import config as C


# ---- tiny vector helpers (lists of 3 floats) ----
def _sub(a, b):  return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _dot(a, b):  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _norm(a):    return math.sqrt(_dot(a, a))

def _angle_deg(a, b):
    na, nb = _norm(a), _norm(b)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    c = max(-1.0, min(1.0, _dot(a, b) / (na * nb)))
    return math.degrees(math.acos(c))

def _stddev_vec(points):
    """RMS distance of a list of 3D points from their mean (a spread metric, in meters)."""
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    mz = sum(p[2] for p in points) / n
    s = sum((p[0]-mx)**2 + (p[1]-my)**2 + (p[2]-mz)**2 for p in points)
    return math.sqrt(s / n)

def _stddev_scalar(xs):
    n = len(xs)
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / n)

def _range(xs):
    return max(xs) - min(xs)

# ---------------------------------------------------------------------------------------------
# Unity's normalised Humanoid pose
#
# Everything measured from bone POSITIONS carries the sampled avatar's proportions with it: the same
# clip read on nurse_avatar and on X Bot differs by -18.3% at the torso and +16.5% at root gait,
# because a 26% longer forearm sweeps 26% more metres for the same shoulder rotation. That makes the
# numbers a statement about the body as much as about the motion.
#
# HumanPose is the representation Unity already normalises every rigged avatar into, on BOTH halves.
# A muscle is one degree of freedom expressed against that avatar's own joint limit. bodyPosition is
# expressed in that same normalised frame -- it is NOT metres and does NOT scale with the body, which
# is easy to misread and was misstated here until 2026-08-22. Measured across nurse_avatar, X Bot and
# Y Bot on one clip: mean |difference| of 0.0001 across 95 muscles and 0.00002 on bodyPosition;
# re-verified on six clips (standing, walking, crouch, arms raised, CPR, free fall) against rigs whose
# real hip heights span 15.6% -- muscles identical to 6 decimals, bodyPosition to ~1e-5. Every
# MEASURED field, the root included, is therefore body-independent.
#
# It also measures a better thing. nurse_cpr_30's head reads 0.0856 in metres and 0.0000 in muscles,
# and the muscle answer is the true one: across all 540 frames the neck and head joints rotate by
# less than 0.0003 of their range. The head is carried by a leaning torso, not moved by its own
# joints, and "which body part does this action drive" is a question about joints.
#
# HumanBodyBones index -> channel. `muscle_bone` in the dump gives the owning bone per muscle, so the
# grouping is read off the engine rather than restated here as name matching that could drift.
MUSCLE_BONE_CHANNEL = {
    7: C.TORSO, 8: C.TORSO, 54: C.TORSO,                              # Spine, Chest, UpperChest
    9: C.HEAD, 10: C.HEAD,                                            # Neck, Head
    1: C.LEFT_LEG, 3: C.LEFT_LEG, 5: C.LEFT_LEG, 19: C.LEFT_LEG,      # UpperLeg, LowerLeg, Foot, Toes
    2: C.RIGHT_LEG, 4: C.RIGHT_LEG, 6: C.RIGHT_LEG, 20: C.RIGHT_LEG,
    11: C.LEFT_ARM, 13: C.LEFT_ARM, 15: C.LEFT_ARM, 17: C.LEFT_ARM,   # Shoulder, UpperArm, LowerArm, Hand
    12: C.RIGHT_ARM, 14: C.RIGHT_ARM, 16: C.RIGHT_ARM, 18: C.RIGHT_ARM,
}
for _b in range(24, 39):
    MUSCLE_BONE_CHANNEL[_b] = C.LEFT_HAND      # 15 left finger bones
for _b in range(39, 54):
    MUSCLE_BONE_CHANNEL[_b] = C.RIGHT_HAND     # 15 right finger bones

# LeftEye, RightEye, Jaw. This corpus carries no facial animation, and the Mixamo rigs do not even
# have these bones -- 52 mapped bones against nurse_avatar's 55. Including them would add channels
# that are identically zero on every clip.
MUSCLE_BONES_EXCLUDED = {21, 22, 23}


def _channel_muscles(raw):
    out = {}
    for m, bone in enumerate(raw.get("muscle_bone") or []):
        if bone in MUSCLE_BONES_EXCLUDED:
            continue
        ch = MUSCLE_BONE_CHANNEL.get(bone)
        if ch:
            out.setdefault(ch, []).append(m)
    return out


def _quat_fwd(q):
    """The +Z axis of a quaternion (x, y, z, w), as a vector."""
    x, y, z, w = q
    return (2.0 * (x * z + w * y), 2.0 * (y * z - w * x), 1.0 - 2.0 * (x * x + y * y))


def _quat_up(q):
    """The +Y axis of a quaternion (x, y, z, w), as a vector."""
    x, y, z, w = q
    return (2.0 * (x * y - w * z), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z + w * x))


def posture_offsets(raw, base):
    """{channel: offset} of this clip's MEAN pose from the reference pose — POSTURE, not motion.

    stddev-over-time answers "does this joint move"; it cannot see a HOLD. An arm raised and kept
    raised has the same stddev as an arm hanging at rest: zero. Measured on the store before this
    signal existed: mx_Agony_Holding_The_Head (3.7 s, the head cradled in both hands) read 0.0000
    on the head channel, and 157 records were static on every channel — ~30 of them real-duration
    held poses, invisible to any "which clips use the left arm" query. The offset of the mean pose
    from the reference is the orthogonal half: it sees what a channel HOLDS, while stddev sees what
    it MOVES.

    Anatomical channels: RMS over the channel's muscle DOF of (mean over frames − baseline), in
    muscle units — dimensionless and body-independent, the same space as the variation signal.
    Root: |mean bodyPosition.y − baseline| in normalised humanoid units, not metres (lying or
    crouching reads low) and |mean tilt
    of the body's up axis − baseline| in degrees (lying reads ~90) — the carriage of the body
    itself, which no anatomical muscle shows: a corpse pose lies with straight legs and a straight
    spine, muscle-identical to standing at rest.

    `base` is {"muscles": [per-muscle mean], "body_y": normalised units, "tilt_deg": degrees};
    config.REFERENCE_POSE holds the store's, and since v2.5.0 it is Unity's, not this store's —
    every muscle at 0 (the centre of its HumanTrait range), bodyPosition.y 1.0, bodyRotation
    identity. Nothing derives it, so "displaced" means away from the HUMANOID reference, not away
    from a relaxed human stance (ADR 0020). Requires a muscle dump (sampler >= 2026-08-20).
    """
    muscles = raw["muscles"]
    N = raw["frames"]
    bm = base["muscles"]
    if len(muscles[0]) != len(bm):
        raise ValueError("muscle count %d != baseline %d — different sampler/avatar generation"
                         % (len(muscles[0]), len(bm)))
    out = {}
    for ch, idxs in _channel_muscles(raw).items():
        offs = [sum(muscles[f][m] for f in range(N)) / N - bm[m] for m in idxs]
        out[ch] = math.sqrt(sum(x * x for x in offs) / len(offs)) if offs else 0.0
    out["root_height"] = abs(sum(p[1] for p in raw["body_pos"]) / N - base["body_y"])
    tilt = sum(_angle_deg(_quat_up(q), (0.0, 1.0, 0.0)) for q in raw["body_rot"]) / N
    out["root_tilt"] = abs(tilt - base["tilt_deg"])
    return out


def _signed_yaw(f0, fi):
    """Signed angle (deg) between two ground-projected forward vectors about +Y (wrap-safe)."""
    a = (f0[0], 0.0, f0[2]); b = (fi[0], 0.0, fi[2])
    na, nb = _norm(a), _norm(b)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    a = (a[0]/na, 0.0, a[2]/na); b = (b[0]/nb, 0.0, b[2]/nb)
    c = max(-1.0, min(1.0, _dot(a, b)))
    ang = math.degrees(math.acos(c))
    cross_y = a[2] * b[0] - a[0] * b[2]   # y component of a x b
    return ang if cross_y >= 0 else -ang


def _mean_finger_curl(frame_idx, bones, wrist, fingers):
    w = bones[wrist][frame_idx]
    acc = 0.0
    for f in fingers:
        p = bones[f[0]][frame_idx]; i = bones[f[1]][frame_idx]; d = bones[f[2]][frame_idx]
        acc += _angle_deg(_sub(p, w), _sub(i, p)) + _angle_deg(_sub(i, p), _sub(d, i))
    return acc / len(fingers)


def compute_raw_signals(raw):
    """Return {channel: raw_value(float)} for the 9 channels from one clip's sampled poses."""
    bones = raw["bones"]
    N = raw["frames"]
    hips = bones[C.HIPS]; chest = bones[C.CHEST]; neck = bones[C.NECK]; head = bones[C.HEAD_BONE]

    torso = max(_angle_deg(_sub(chest[k], hips[k]), (0.0, 1.0, 0.0)) for k in range(N))
    head_r = _range([_angle_deg(_sub(head[k], neck[k]), _sub(neck[k], chest[k])) for k in range(N)])

    def grp_std(bone_names, hips_relative):
        acc = 0.0
        for bn in bone_names:
            pts = bones[bn]
            if hips_relative:
                pts = [_sub(pts[k], hips[k]) for k in range(N)]
            acc += _stddev_vec(pts)
        return acc / len(bone_names)

    l_arm = grp_std(C.ARM_BONES[C.LEFT_ARM], True)
    r_arm = grp_std(C.ARM_BONES[C.RIGHT_ARM], True)
    l_leg = grp_std(C.LEG_BONES[C.LEFT_LEG], False)
    r_leg = grp_std(C.LEG_BONES[C.RIGHT_LEG], False)

    l_hand = _range([_mean_finger_curl(k, bones, C.WRIST[C.LEFT_HAND], C.FINGER_BONES[C.LEFT_HAND]) for k in range(N)])
    r_hand = _range([_mean_finger_curl(k, bones, C.WRIST[C.RIGHT_HAND], C.FINGER_BONES[C.RIGHT_HAND]) for k in range(N)])

    # root: gait (foot vertical oscillation), translation, heading
    gait = (_range([bones[C.LEFT_FOOT][k][1] for k in range(N)]) +
            _range([bones[C.RIGHT_FOOT][k][1] for k in range(N)])) / 2.0
    # TRANSLATION AND TURNING COME FROM THE HIPS, NOT THE ROOT TRANSFORM.
    #
    # The corpus is imported with Root Transform Position and Rotation baked into the pose, so Unity
    # applies one constant offset for the whole clip and `inst.transform` never moves. Measured:
    # root_pos was identical on every frame of all 150 corpus clips sampled for ADR 0010, which made
    # both of these signals 0.0 for a walk that covers 1.125 m of ground. Read off the root they say
    # nothing; the travel is in the body, so that is where they are read.
    #
    # Bone positions are recorded root-local and a standard deviation is translation-invariant, so
    # this is the real excursion, not an artefact of where the root happens to sit. `net displacement`
    # deliberately is NOT the signal: a capoeira ginga returns to where it started (net 0.000 m) while
    # covering 1.5 m of ground, and it is the covering that makes it locomotion.
    # ---- Unity-normalised signals, when the dump carries a HumanPose (sampler >= 2026-08-20) -----
    muscles = raw.get("muscles")
    if muscles:
        cm = _channel_muscles(raw)
        out_m = {}
        for ch, idxs in cm.items():
            # RMS across the channel's degrees of freedom of each one's stddev over time.
            # RMS rather than the mean so a channel is not diluted by its own size -- a hand has 20
            # DOF against the torso's 9, and a mean would make the hand read lower for the same
            # amount of movement. Not the max either, which would let one twitchy DOF speak for the
            # whole channel.
            sds = [_stddev_scalar([muscles[f][m] for f in range(N)]) for m in idxs]
            out_m[ch] = math.sqrt(sum(x * x for x in sds) / len(sds)) if sds else 0.0
        bp = raw["body_pos"]
        bx = sum(p[0] for p in bp) / N
        bz = sum(p[2] for p in bp) / N
        out_m["_root_trans"] = math.sqrt(sum((p[0]-bx)**2 + (p[2]-bz)**2 for p in bp) / N)
        out_m["_root_vert"] = max(p[1] for p in bp) - min(p[1] for p in bp)
        bfwd = [_quat_fwd(q) for q in raw["body_rot"]]
        out_m["_root_heading"] = _stddev_scalar([_signed_yaw(bfwd[0], f) for f in bfwd])
        # The orthogonal half: what each channel HOLDS, not what it moves (see posture_offsets).
        po = posture_offsets(raw, C.REFERENCE_POSE)
        for ch in cm:
            out_m["_posture_" + ch] = po[ch]
        out_m["_posture_root_height"] = po["root_height"]
        out_m["_posture_root_tilt"] = po["root_tilt"]
        return out_m

    # ---- legacy: metre-space signals, for dumps taken before muscles were sampled ---------------
    hips_pos = (raw.get("bones") or {}).get(C.HIPS) or raw["root_pos"]
    mx = sum(p[0] for p in hips_pos) / N; mz = sum(p[2] for p in hips_pos) / N
    root_trans = math.sqrt(sum((p[0]-mx)**2 + (p[2]-mz)**2 for p in hips_pos) / N)
    hips_rot = (raw.get("bone_rot") or {}).get(C.HIPS)
    if hips_rot:
        hf = [_quat_fwd(q) for q in hips_rot]
        root_heading = _stddev_scalar([_signed_yaw(hf[0], hf[k]) for k in range(N)])
    else:
        # Dumps predating the 2026-08-06 rotation pass have no bone_rot; fall back rather than fail,
        # and accept that such a dump reports no turning.
        fwd = raw["root_fwd"]
        root_heading = _stddev_scalar([_signed_yaw(fwd[0], fwd[k]) for k in range(N)])

    return {
        C.TORSO: torso, C.HEAD: head_r,
        C.LEFT_ARM: l_arm, C.RIGHT_ARM: r_arm,
        C.LEFT_LEG: l_leg, C.RIGHT_LEG: r_leg,
        C.LEFT_HAND: l_hand, C.RIGHT_HAND: r_hand,
        "_root_gait": gait, "_root_trans": root_trans, "_root_heading": root_heading,
    }


def _clamp01(x):
    return max(0.0, min(1.0, x))


def channel_blocks(raw):
    """Full measured channel blocks for one clip: variation (state_label/motion_magnitude/
    raw_measurement) AND posture (posture_label/posture_magnitude/posture_measurement).

    Two orthogonal facts per channel, because either alone misreads half the store. Variation
    (stddev over time) cannot see a hold — an arm raised and kept raised reads 0.0000, same as an
    arm at rest. Posture (mean-pose offset from Unity's Humanoid reference) cannot see cyclic
    motion — a walking arm swings AROUND its mean carriage and reads that carriage, not the swing.
    Since v2.5.0 the reference is the engine's, so posture answers "how far from the Humanoid
    reference does this channel sit", which is not the same as "how unusual is this posture": a
    person standing relaxed reads high on arms, knees and fingers, because those sit near the ends
    of their ranges when upright (ADR 0020).
    """
    sig = compute_raw_signals(raw)
    if "_posture_" + C.TORSO not in sig:
        raise ValueError("no posture signals: this dump predates muscle sampling (2026-08-20) — "
                         "re-sample the clip")
    out = {}

    def fk_or_hand(channel, kind, signal_name, div_key, thr_key):
        rawv = sig[channel]
        div = C.DIVISOR[div_key]
        prawv = sig["_posture_" + channel]
        pdiv = C.POSTURE_DIVISOR[div_key]
        out[channel] = {
            "kind": kind,
            "state_label": "dynamic" if rawv >= C.STATIC[thr_key] else "static",
            "motion_magnitude": round(_clamp01(rawv / div), 4),
            "raw_measurement": {"signal": signal_name, "raw_value": round(rawv, 5), "divisor": div},
            "posture_label": "displaced" if prawv >= C.NEUTRAL[thr_key] else "neutral",
            "posture_magnitude": round(_clamp01(prawv / pdiv), 4),
            "posture_measurement": {"signal": PSIG, "raw_value": round(prawv, 5), "divisor": pdiv},
        }

    # One signal name for every anatomical channel, because it is now literally the same measurement
    # everywhere: the RMS over that channel's Humanoid degrees of freedom of each one's stddev in time.
    SIG = "muscle_dof_stddev_rms"
    # And its posture twin: the RMS over the same degrees of freedom of the MEAN pose's offset from
    # the reference (config.REFERENCE_POSE — Unity's Humanoid reference pose, muscle 0 on every DOF).
    PSIG = "muscle_dof_mean_offset_rms"
    fk_or_hand(C.TORSO, "fk_part", SIG, C.TORSO, C.TORSO)
    fk_or_hand(C.HEAD, "fk_part", SIG, C.HEAD, C.HEAD)
    fk_or_hand(C.LEFT_ARM, "fk_part", SIG, "arm", "arm")
    fk_or_hand(C.RIGHT_ARM, "fk_part", SIG, "arm", "arm")
    fk_or_hand(C.LEFT_LEG, "fk_part", SIG, "leg", "leg")
    fk_or_hand(C.RIGHT_LEG, "fk_part", SIG, "leg", "leg")
    fk_or_hand(C.LEFT_HAND, "hand", SIG, "hand", "hand")
    fk_or_hand(C.RIGHT_HAND, "hand", SIG, "hand", "hand")

    t, v, h = sig["_root_trans"], sig["_root_vert"], sig["_root_heading"]
    root_mag = _clamp01(max(t / C.DIVISOR["root_trans"],
                            v / C.DIVISOR["root_vert"],
                            h / C.DIVISOR["root_heading"]))
    # Dynamic if the body went anywhere at all -- moved, rose or turned. Foot lift is deliberately
    # not part of this: an in-place walk's body does not travel, and whether its legs are stepping
    # is what the leg channels say.
    root_dynamic = (t >= C.STATIC["root_trans"] or v >= C.STATIC["root_vert"]
                    or h >= C.STATIC["root_heading"])
    # Root posture is the CARRIAGE of the body: height offset (lying/crouching reads low) and tilt
    # of the body's up axis (lying reads ~90 deg). No anatomical muscle shows either — a corpse pose
    # lies with straight legs and a straight spine, muscle-identical to standing at rest.
    ph, pt = sig["_posture_root_height"], sig["_posture_root_tilt"]
    root_pmag = _clamp01(max(ph / C.POSTURE_DIVISOR["root_height"],
                             pt / C.POSTURE_DIVISOR["root_tilt"]))
    root_displaced = ph >= C.NEUTRAL["root_height"] or pt >= C.NEUTRAL["root_tilt"]
    out[C.ROOT] = {
        "kind": "root",
        "state_label": "dynamic" if root_dynamic else "static",
        "motion_magnitude": round(root_mag, 4),
        "raw_measurement": {
            "signal": "max(trans/%s, vert/%s, heading/%s)" % (
                C.DIVISOR["root_trans"], C.DIVISOR["root_vert"], C.DIVISOR["root_heading"]),
            "body_trans_horiz_stddev": round(t, 5),
            "body_vert_range": round(v, 5),
            "body_heading_stddev_deg": round(h, 3),
        },
        "posture_label": "displaced" if root_displaced else "neutral",
        "posture_magnitude": round(root_pmag, 4),
        "posture_measurement": {
            "signal": "max(height_offset/%s, tilt_offset/%s)" % (
                C.POSTURE_DIVISOR["root_height"], C.POSTURE_DIVISOR["root_tilt"]),
            "body_height_offset": round(ph, 5),
            "body_tilt_offset_deg": round(pt, 3),
        },
    }
    return out
