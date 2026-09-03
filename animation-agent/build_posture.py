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

WHAT THE FEATURES ARE, AND WHY THOSE. The measurement set is taken from the published posture-
recognition literature, not invented here:

  * Guerra et al. (2020) recognise standing / sitting / lying from geometric relations BETWEEN body
    segments — joint angles, trunk pitch, and joint heights normalised by stature — rather than from
    absolute positions, because absolute positions are subject-dependent and the relations are not.
  * Liu et al. (2017) separate the same states with deterministic rules over trunk and thigh
    orientation, and say plainly that their angle cut-offs are empirical.
  * Schenkman et al. (1990) show that sit-to-stand is a staged dynamic process, which is why the
    output here is a segmentation over time and not a single label.

The literature decides WHAT IS MEASURED. The numbers below are FIXED OPERATIONAL THRESHOLDS: values
chosen so that these rules cut this corpus where a human would, versioned so a change is visible, and
deliberately NOT called biomechanical thresholds, because they are not measurements of anybody.

WHAT IS MEASURED, PER FRAME (all from the dump; nothing is fitted):

    h_body        `body_pos.y` — Unity's `HumanPose.bodyPosition`, the humanoid's approximate centre
                  of mass ALREADY NORMALISED by the avatar's human scale. This is the same quantity
                  the records' `mean_body_height` averages, so the two are directly comparable.
    theta_trunk   angle(Chest - Hips, +Y) — trunk inclination, 0 deg upright.
    theta_thigh   angle(LowerLeg - UpperLeg, -Y) per leg — 0 deg is a thigh hanging straight down.
    theta_shank   angle(Foot - LowerLeg, -Y) per leg — 0 deg is a vertical shank. This is what
                  separates SITTING (thigh horizontal, shank vertical) from SQUATTING (both pitched).
    phi_knee      angle(UpperLeg - LowerLeg, Foot - LowerLeg) per leg — 180 deg is a straight leg.

NO CALIBRATION STEP, and no canonical leg length. Heights come in already normalised by Unity, and
angles are scale-free by construction, so there is nothing left for a calibration to do. The papers
call this quantity normalised body height or COM height; it is not a pelvis height and is not named
one here.

NO HEIGHT ABOVE THE GROUND, EITHER. `bones` is root-local (`Transform.InverseTransformPoint`), so
`Foot.y` is the foot's height relative to the character's own root and says nothing about where the
scene's floor is. Support and contact are the Unity executor's to determine. `transitions.py` keeps
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
POSTURE_ALGORITHM_VERSION = "1.1.0"

PATH = os.path.join(paths.DERIVED_DIR, "posture.json")

# ---- fixed operational thresholds -------------------------------------------------------------
# Heights are in the same normalised humanoid units as the records' `mean_body_height`, which makes
# them checkable against the corpus without being defined by it: the corpus's seated clips read
# around 0.65 and its lowest standing clip reads 0.86.
H_FLOOR = 0.45                  # below this the body is down at floor level however it is arranged
H_LOW = 0.70                    # low enough that a horizontal trunk means lying, not bending over
THETA_TRUNK_HORIZONTAL = 60.0   # trunk inclination past which the torso reads as horizontal
H_SEATED = 0.80                 # a seated carriage sits below this
THETA_THIGH_SEATED = 45.0       # thighs pitched at least this far from vertical
THETA_SHANK_SEATED = 40.0       # ...while the shanks stay near vertical. Squatting fails this.
THETA_KNEE_FLEXED = 120.0       # at least one knee bent past this
H_STANDING = 0.80               # a standing carriage sits at or above this
THETA_THIGH_UPRIGHT = 35.0      # ...on thighs that are still essentially vertical
H_AIRBORNE = 1.15               # above this the body has left the ground; standing does not apply
MIN_POSTURE_DURATION_S = 0.3    # a state has to last this long to be a segment rather than a flicker

PARAMS = {
    "H_FLOOR": H_FLOOR,
    "H_LOW": H_LOW,
    "THETA_TRUNK_HORIZONTAL": THETA_TRUNK_HORIZONTAL,
    "H_SEATED": H_SEATED,
    "THETA_THIGH_SEATED": THETA_THIGH_SEATED,
    "THETA_SHANK_SEATED": THETA_SHANK_SEATED,
    "THETA_KNEE_FLEXED": THETA_KNEE_FLEXED,
    "H_STANDING": H_STANDING,
    "THETA_THIGH_UPRIGHT": THETA_THIGH_UPRIGHT,
    "H_AIRBORNE": H_AIRBORNE,
    "MIN_POSTURE_DURATION_S": MIN_POSTURE_DURATION_S,
}
# Written into _meta beside PARAMS so the file says what units it was computed in without anybody
# having to open this module. Separate from PARAMS so nothing in there is anything but a threshold.
PARAM_UNITS = {"heights": "normalised humanoid units (HumanPose.bodyPosition.y)",
               "angles": "degrees",
               "durations": "seconds"}

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
    """Where the clip leaves the body relative to where it picked it up: (dx, dz) in metres and the
    yaw it turns through.

    WHY THIS IS NEEDED AT ALL. A retrieved posture transition is a clip that TRAVELS.
    `mx_Standing_To_Sitting_Transition` steps 0.45 m backwards into the chair; played from wherever
    the walk happened to stop, it puts the hips 0.45 m in front of the seat. The planner has to know
    that displacement to work backwards from the seat to the point she should stand on before the
    clip starts, and this is where the number comes from.

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
    """Every bone a dump has to carry for this to run."""
    return tuple(_TRUNK) + tuple(side + b for side in ("Left", "Right") for b in _LEG)


def features(raw):
    """Per-frame [h_body, theta_trunk, theta_thigh, theta_shank, phi_knee].

    The two legs are reduced here, where the reduction is stated once: `theta_thigh` and
    `theta_shank` are the MEAN of the two sides (a posture is a property of the whole body, and one
    leg swinging through a walk cycle should not flip it), while `phi_knee` is the MINIMUM (the
    seated test asks whether A knee is bent, and a seated character with one leg extended is still
    seated).
    """
    bones = raw.get("bones") or {}
    missing = [b for b in required_bones() if b not in bones]
    if missing:
        raise ValueError("dump is missing bone(s): %s" % ", ".join(missing))
    body = raw.get("body_pos")
    if not body:
        raise ValueError("dump has no body_pos")
    n = int(raw.get("frames") or len(body))
    n = min(n, len(body), *(len(bones[b]) for b in required_bones()))
    if n < 1:
        raise ValueError("dump has no frames")

    out = []
    for f in range(n):
        hips, chest = bones["Hips"][f], bones["Chest"][f]
        theta_trunk = _angle_deg(_sub(chest, hips), UP)
        thigh = shank = 0.0
        knee = 360.0
        for side in ("Left", "Right"):
            upper = bones[side + "UpperLeg"][f]
            lower = bones[side + "LowerLeg"][f]
            foot = bones[side + "Foot"][f]
            thigh += _angle_deg(_sub(lower, upper), DOWN)
            shank += _angle_deg(_sub(foot, lower), DOWN)
            knee = min(knee, _angle_deg(_sub(upper, lower), _sub(foot, lower)))
        out.append((body[f][1], theta_trunk, thigh / 2.0, shank / 2.0, knee))
    return out


def label_frame(frame):
    """One frame's coarse state. THE ORDER IS THE RULE: floor, then seated, then standing, then the
    fallback. Each test is only reached because the ones above it did not fire, so `seated` never has
    to exclude lying and `standing` never has to exclude sitting."""
    h_body, theta_trunk, theta_thigh, theta_shank, phi_knee = frame

    # 1. FLOOR — either the body is simply down there, or the trunk is horizontal and low, which is
    #    lying and crawling. The height clause alone would miss a prone crawl on hands and knees;
    #    the trunk clause alone would catch a deep bend at standing height.
    if h_body < H_FLOOR or (theta_trunk > THETA_TRUNK_HORIZONTAL and h_body < H_LOW):
        return "floor"

    # 2. SEATED — low carriage, thighs pitched toward horizontal, shanks still near vertical, a knee
    #    folded, trunk not horizontal. The shank clause is what keeps a squat out: a squat pitches
    #    the shank forward too, fails here, and falls through to `other`.
    if (h_body < H_SEATED and theta_thigh > THETA_THIGH_SEATED and theta_shank < THETA_SHANK_SEATED
            and phi_knee < THETA_KNEE_FLEXED and theta_trunk <= THETA_TRUNK_HORIZONTAL):
        return "seated"

    # 3. STANDING — carried high, on legs that are still under the body. Bending over stays standing
    #    (the thighs are vertical and the carriage stays high); a jump does not (`H_AIRBORNE`).
    if H_STANDING <= h_body <= H_AIRBORNE and theta_thigh < THETA_THIGH_UPRIGHT:
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


def analyse(raw):
    """One dump's posture structure: the entry that goes into the sidecar."""
    frames = features(raw)
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

def _document(entries):
    """The sidecar, minus `generated_at` — everything that is a function of the corpus and the
    algorithm, and nothing that is a function of when this ran. See `write_sidecar`."""
    return {
        "_meta": {
            "kind": "derived",
            "version": POSTURE_ALGORITHM_VERSION,
            "derived_from": "raw/<clip_name>.json: body_pos (HumanPose.bodyPosition, normalised) "
                            "+ root-local bone positions",
            "regenerate": "python build_posture.py",
            "params": PARAMS,
            "param_units": PARAM_UNITS,
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


def write_sidecar(entries, path=None):
    """Write the sidecar, and DO NOT touch the file when only the clock has moved.

    `generated_at` is the one field that changes on every run, so writing it unconditionally would
    make `git status` report a change after every build and cost the KB its drift detector. The
    document is therefore compared without it, and the timestamp is only advanced when something
    else actually differs. Returns (path, changed).
    """
    path = path or PATH
    doc = _document(entries)
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
    action_id, clip_name = job
    p = os.path.join(paths.RAW_DIR, clip_name + ".json")
    try:
        with open(p, encoding="utf-8") as fh:
            raw = json.load(fh)
        return (action_id, analyse(raw), None)
    except Exception as e:
        return (action_id, None, "%s: %s" % (type(e).__name__, e))


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
    if not quiet:
        print("posture algorithm %s over %d action(s)" % (POSTURE_ALGORITHM_VERSION, len(corpus)))
    entries, failures = build(
        corpus, workers=args.jobs,
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
        if not _on_disk_matches(path, _document(entries)):
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

    path, changed = write_sidecar(entries, path=args.out)
    forget_sidecar()
    print("\n%s %s (%d actions)" % ("wrote" if changed else "unchanged:", paths.rel(path),
                                    len(entries)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
