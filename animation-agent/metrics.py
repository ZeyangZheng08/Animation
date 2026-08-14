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
    rp = raw["root_pos"]
    mx = sum(p[0] for p in rp) / N; mz = sum(p[2] for p in rp) / N
    root_trans = math.sqrt(sum((p[0]-mx)**2 + (p[2]-mz)**2 for p in rp) / N)
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
    """Full measured channel blocks (kind/state_label/motion_magnitude/raw_measurement) for one clip."""
    sig = compute_raw_signals(raw)
    out = {}

    def fk_or_hand(channel, kind, signal_name, div_key, thr_key):
        rawv = sig[channel]
        div = C.DIVISOR[div_key]
        out[channel] = {
            "kind": kind,
            "state_label": "dynamic" if rawv >= C.STATIC[thr_key] else "static",
            "motion_magnitude": round(_clamp01(rawv / div), 4),
            "raw_measurement": {"signal": signal_name, "raw_value": round(rawv, 5), "divisor": div},
        }

    fk_or_hand(C.TORSO, "fk_part", "max_torso_lean_deg", C.TORSO, C.TORSO)
    fk_or_hand(C.HEAD, "fk_part", "head_vs_spine_range_deg", C.HEAD, C.HEAD)
    fk_or_hand(C.LEFT_ARM, "fk_part", "mean_bone_hips_rel_pos_stddev_m", "arm", "arm")
    fk_or_hand(C.RIGHT_ARM, "fk_part", "mean_bone_hips_rel_pos_stddev_m", "arm", "arm")
    fk_or_hand(C.LEFT_LEG, "fk_part", "mean_bone_world_pos_stddev_m", "leg", "leg")
    fk_or_hand(C.RIGHT_LEG, "fk_part", "mean_bone_world_pos_stddev_m", "leg", "leg")
    fk_or_hand(C.LEFT_HAND, "hand", "mean_finger_curl_range_deg", "hand", "hand")
    fk_or_hand(C.RIGHT_HAND, "hand", "mean_finger_curl_range_deg", "hand", "hand")

    g, t, h = sig["_root_gait"], sig["_root_trans"], sig["_root_heading"]
    root_mag = _clamp01(max(g / C.DIVISOR["root_gait"], t / C.DIVISOR["root_trans"], h / C.DIVISOR["root_heading"]))
    out[C.ROOT] = {
        "kind": "root",
        "state_label": "dynamic" if g >= C.STATIC["root_gait"] else "static",
        "motion_magnitude": round(root_mag, 4),
        "raw_measurement": {
            "signal": "max(gait/0.317, trans/0.30, heading/60)",
            "gait_foot_yrange_m": round(g, 5),
            "trans_horiz_stddev_m": round(t, 5),
            "heading_signed_stddev_deg": round(h, 3),
        },
    }
    return out
