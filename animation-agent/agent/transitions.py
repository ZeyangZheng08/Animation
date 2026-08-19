"""
transitions.py — where two actions can be joined, how far apart they are there, and how long the
blend has to be.

The knowledge base describes each action in isolation. A transition is a RELATION between two of them,
and nothing in the v2 contract holds one. Rather than bump the contract, this derives the relation from
the frozen `_raw` dumps: the per-frame bone rotations added on 2026-08-05 are exactly what a seam search
needs, and the result is a regenerable sidecar, not a new field on any record.

Three things it answers, in the model's vocabulary:

  WHERE   the best frame pair (i in A, j in B) to cut, inside a trim budget that refuses to cut through
          an action's payload.
  HOW FAR apart the two poses are there, in degrees.
  HOW LONG the blend has to be, derived from a stated angular-rate assumption rather than tuned.

DELIBERATELY NO TUNED THRESHOLD. The class of a pair falls out of the blend length and the postures;
there is no "seam cost above X is infeasible" constant, because the corpus has 8 clips and any cutoff
fitted to it would be noise wearing a number. A same-posture pair always gets a blend, and whether it
looks acceptable is the geometric gate's verdict, measured on the frames that actually played.
"""
import json
import math
import os

import config
import paths

# Angular rate a joint is allowed to sweep during a blend. A blend that moves the average joint faster
# than this reads as a snap. This is an ASSUMPTION, stated here so the gate's velocity-continuity
# measurement can falsify it — it was not fitted to any evaluation set.
MAX_BLEND_RATE_DEG_PER_S = 180.0

# Which sampled bones make up each channel. Grouped here from the extractor's own lists rather than
# restated, so a channel cannot come to mean one thing to the pipeline and another to the seam search.
CHANNEL_BONES = dict(
    [("root", [config.HIPS]),
     ("torso", [config.CHEST]),
     ("head", [config.NECK, config.HEAD_BONE])]
    + list(config.ARM_BONES.items())
    + list(config.LEG_BONES.items())
    + [(side, [bone for finger in fingers for bone in finger])
       for side, fingers in config.FINGER_BONES.items()])

# The two channels the whole-body pose metric already leaves out, for the reason given on `pose_bones`:
# 30 of the 50 sampled bones are finger joints. They keep their own ramp — a hand that has to open still
# gets the time to open — but they do not get to decide how long a seam is.
FINGER_CHANNELS = ("left_hand", "right_hand")

# Between any ground contact and the root. Fixed by the humanoid rig: the feet hang off the legs, the
# legs off the hips, the hips are the root. See `support_channels` for why that matters.
CARRIES_WEIGHT_ALWAYS = ("root", "torso")

# Fraction of each clip the seam search may look at: the tail of A, the head of B. Wide enough to find
# a compatible moment, narrow enough that a "transition" is still recognisably a join of the two.
SEARCH_FRACTION = 0.40

# Feet within this of their own minimum over the clip count as planted, for the locomotion seam term.
PLANTED_BAND_M = 0.03

# Weight on the foot-contact term when the source is locomotion. In degrees, so it trades directly
# against pose distance: stopping a walk mid-swing costs about as much as 12 degrees of pose error.
FOOT_PENALTY_DEG = 12.0

CLASS_DIRECT = "direct"
CLASS_BLEND = "blend"
CLASS_POSTURE_CHANGE = "posture_change"


# ---- raw access ------------------------------------------------------------------------------

class Clip(object):
    """One `_raw` dump, with just enough structure for a seam search."""

    __slots__ = ("clip_name", "frames", "fps", "rot", "bones", "pose_bones", "foot_y", "loop")

    def __init__(self, raw, loop=False):
        self.clip_name = raw["clip"]
        self.frames = raw["frames"]
        self.fps = raw.get("frame_rate") or 30
        self.rot = raw.get("bone_rot") or {}
        self.bones = raw.get("bones") or {}
        self.loop = loop
        # Fingers are excluded from the pose metric: 30 of the 50 sampled bones are finger joints, so
        # including them lets a hand pose outvote the whole body, and the asset's fingers are known to
        # self-intersect in curled poses anyway.
        self.pose_bones = sorted(b for b in self.rot if not _is_finger(b))
        self.foot_y = _foot_heights(self.bones)


_FINGER_TOKENS = ("Thumb", "Index", "Middle", "Ring", "Little")


def _is_finger(bone):
    return any(tok in bone for tok in _FINGER_TOKENS)


def _foot_heights(bones):
    """Per-frame max(left, right) foot height, or None if the feet were not sampled. Used to prefer a
    seam where a walking cycle has both feet down."""
    left, right = bones.get("LeftFoot"), bones.get("RightFoot")
    if not left or not right:
        return None
    return [max(left[i][1], right[i][1]) for i in range(min(len(left), len(right)))]


# (path, loop) -> Clip, for the default corpus only. See load_clip and forget_raw.
_CLIP_CACHE = {}


def forget_raw():
    """Drop everything memoised from the default `_raw`. The sampler calls this after writing a dump.

    Nothing else needs it: `_raw` is frozen for every process that only reads the KB, and the one writer
    is `unity_sampler.write_raw`, which runs in the offline pipeline.
    """
    _CLIP_CACHE.clear()
    _FINGERPRINT_CACHE.pop(paths.RAW_DIR, None)


def load_clip(clip_name, loop=False, raw_dir=None):
    """One `_raw` dump as a Clip.

    MEMOISED FOR THE DEFAULT CORPUS. These dumps are about 900 KB each and get loaded by every seam
    search, every segment build and every plan that windows a layer. On a local disk re-reading them is
    cheap; the KB normally sits on a Windows worktree over DrvFs, where one open costs about 19 ms, and
    across a test suite that was two seconds of pure re-reading. A Clip is a read-only value object
    (`__slots__`, built once in `__init__`, never written to afterwards), so sharing one is safe.

    Pass `raw_dir` and the read is unconditional -- that is the corpus a caller brought itself, and this
    module has no idea when it changes.
    """
    frozen = raw_dir is None
    path = os.path.join(raw_dir or paths.RAW_DIR, clip_name + ".json")
    key = (path, bool(loop))
    if frozen:
        hit = _CLIP_CACHE.get(key)
        if hit is not None:
            return hit
    with open(path, encoding="utf-8") as fh:
        clip = Clip(json.load(fh), loop=loop)
    if frozen:
        _CLIP_CACHE[key] = clip
    return clip


# ---- pose distance ---------------------------------------------------------------------------

def quat_angle_deg(q1, q2):
    """Geodesic angle between two unit quaternions, in degrees. abs() on the dot product because q and
    -q are the same rotation and the raw dumps do not normalise the sign."""
    d = abs(q1[0] * q2[0] + q1[1] * q2[1] + q1[2] * q2[2] + q1[3] * q2[3])
    if d > 1.0:
        d = 1.0
    return 2.0 * math.degrees(math.acos(d))


def pose_distance(a, i, b, j, bones=None):
    """Mean per-bone rotation difference between frame i of clip a and frame j of clip b, in degrees.

    Root-local, so this compares POSE and is blind to where the character is standing — which is what a
    seam needs, since the corpus is in-place and world position comes from the NavMesh agent.
    """
    names = bones if bones is not None else [n for n in a.pose_bones if n in b.rot]
    if not names:
        raise ValueError("no shared bones between %s and %s" % (a.clip_name, b.clip_name))
    total = 0.0
    for name in names:
        total += quat_angle_deg(a.rot[name][i], b.rot[name][j])
    return total / len(names)


def channel_costs(a, i, b, j):
    """The same distance, per channel. None for a channel whose bones were not sampled in both clips.

    This is the whole basis for timing a seam by body part. The average is a summary and it hides the
    thing that matters: joined at their best frames, `walking` and `typing` are 48 degrees apart on
    average, 90 at the busiest arm and 5 at the torso. One blend length for all of them either snaps the
    arm or drags the torso, and which of the two you get depends on a number neither channel chose.
    """
    out = {}
    for channel, bones in CHANNEL_BONES.items():
        shared = [n for n in bones if n in a.rot and n in b.rot]
        out[channel] = pose_distance(a, i, b, j, shared) if shared else None
    return out


def support_channels(rec_a, rec_b):
    """Channels holding the body up across a seam, which therefore cannot arrive late.

    Everything else may be timed by how far it has to travel, because nothing rests on it. A leg cannot:
    the weight is on it continuously, and a leg that reaches its seated pose only at the end of a descent
    has spent that descent straightening under a pelvis that was already on its way down. That is the
    pose nobody performs, and it is what the first version of the generated sit produced.

    Read off `contact`, not listed here: a channel touching the ground is load-bearing whichever action
    it belongs to, and the two ends of a seam are both consulted because a contact that is being taken up
    or given up is still being stood on for part of the blend. Everything between that contact and the
    root goes with it — for a foot that is the leg it belongs to plus the hips and spine, and for a hand
    on the ground it is that hand's arm.
    """
    out = set(CARRIES_WEIGHT_ALWAYS)
    for rec in (rec_a, rec_b):
        for channel, spec in (rec.get("channels") or {}).items():
            if (spec or {}).get("contact") != "ground":
                continue
            out.add(channel)
            if channel in FINGER_CHANNELS:
                out.add(channel.replace("_hand", "_arm"))
    return out


# ---- trim budget -----------------------------------------------------------------------------

def payload_window(record, frames):
    """The frames a seam must not cut into: the span where a `primary` channel is touching an object.

    Cutting the tail off `grab_bottle` removes the grasp and the action stops being itself. A loop has
    no payload in this sense — every frame of a walk cycle is as good as any other — so it returns None
    and the whole clip is available.

    The knowledge base has no per-frame contact track, so this is deliberately coarse: an action with an
    object contact on a primary channel protects its whole middle, keeping only the outer eighths
    trimmable. That is a conservative budget, and being conservative here costs a slightly worse seam
    while being wrong the other way costs the action's meaning.
    """
    if record.get("loop"):
        return None
    channels = record.get("channels") or {}
    contacts = [ch for ch in channels.values()
                if ch.get("role") == "primary" and str(ch.get("contact", "none")).startswith("object:")]
    if not contacts:
        return None
    margin = max(1, int(frames * 0.125))
    return (margin, frames - 1 - margin)


def _search_range(clip, record, tail):
    """Frame indices a seam may use: the tail of the outgoing clip, the head of the incoming one."""
    n = clip.frames
    if n <= 1:
        return [0]
    span = max(1, int(round(n * SEARCH_FRACTION)))
    window = range(n - span, n) if tail else range(span)
    payload = payload_window(record, n)
    if payload is None:
        return list(window)
    lo, hi = payload
    allowed = [k for k in window if k < lo or k > hi]
    # Never return empty: a clip whose payload swallows the whole search window still has to be joinable
    # somewhere, and its own first/last frame is the least destructive place.
    return allowed or [n - 1 if tail else 0]


# ---- seam ------------------------------------------------------------------------------------

class Seam(object):
    __slots__ = ("from_action", "to_action", "from_frame", "to_frame",
                 "cost_deg", "blend_frames", "cls", "notes", "channel_cost_deg", "support")

    def __init__(self, from_action, to_action, from_frame, to_frame, cost_deg, blend_frames, cls, notes,
                 channel_cost_deg=None, support=None):
        self.from_action = from_action
        self.to_action = to_action
        self.from_frame = from_frame
        self.to_frame = to_frame
        self.cost_deg = cost_deg
        self.blend_frames = blend_frames
        self.cls = cls
        self.notes = notes
        self.channel_cost_deg = channel_cost_deg or {}
        self.support = tuple(sorted(support or ()))

    def pace_deg(self):
        """The travel that sets this seam's length: the busiest channel that is allowed to set it.

        Fingers are excluded for the same reason they are excluded from the pose metric — `idle` and
        `walking` are 92 degrees apart at the fingers and 26 at the busiest arm, and letting a hand pose
        decide would stretch the entry into a walk to half a second to accommodate a curl nobody watches.
        """
        real = [c for ch, c in self.channel_cost_deg.items()
                if c is not None and ch not in FINGER_CHANNELS]
        return max(real) if real else self.cost_deg

    def as_dict(self):
        return {"from": self.from_action, "to": self.to_action,
                "from_frame": self.from_frame, "to_frame": self.to_frame,
                "cost_deg": round(self.cost_deg, 3), "blend_frames": self.blend_frames,
                "class": self.cls, "notes": self.notes,
                "channel_cost_deg": {k: (None if v is None else round(v, 3))
                                     for k, v in sorted(self.channel_cost_deg.items())},
                "support_channels": list(self.support)}


def blend_frames_for(cost_deg, fps):
    """How many frames a channel needs to travel `cost_deg` without exceeding MAX_BLEND_RATE_DEG_PER_S.

    Called with the BUSIEST channel's travel, not the average. It used to be called with the average,
    which is not a rate limit on anything: joining `idle` to `walking` costs 18 degrees averaged over the
    body and 26 at the busiest arm, so the three frames that bought produced 260 degrees a second through
    that arm against a stated ceiling of 180. The average is the right number for choosing WHERE to cut
    and the wrong one for deciding how long the cut takes.
    """
    return int(math.ceil(cost_deg / MAX_BLEND_RATE_DEG_PER_S * fps))


def classify(blend_frames, posture_from, posture_to):
    """No tuned constant here on purpose — see the module docstring.

    A posture change is categorical (the knowledge base says one action is seated and the other is not)
    and outranks the numbers, because no crossfade between a stance and a sit is a stance change: it
    needs frames that neither clip contains.
    """
    if posture_from and posture_to and posture_from != posture_to:
        return CLASS_POSTURE_CHANGE
    return CLASS_DIRECT if blend_frames <= 1 else CLASS_BLEND


def _foot_penalty(clip, frame):
    """Degrees of penalty for cutting a locomotion clip while a foot is off the ground."""
    if not clip.loop or not clip.foot_y or frame >= len(clip.foot_y):
        return 0.0
    lowest = min(clip.foot_y)
    lift = clip.foot_y[frame] - lowest
    if lift <= PLANTED_BAND_M:
        return 0.0
    span = max(1e-6, max(clip.foot_y) - lowest)
    return FOOT_PENALTY_DEG * min(1.0, (lift - PLANTED_BAND_M) / span)


def find_seam(from_id, to_id, kb, clips):
    """Best join between two actions. `clips` maps action_id -> Clip."""
    rec_a, rec_b = kb.actions[from_id], kb.actions[to_id]
    a, b = clips[from_id], clips[to_id]
    shared = [n for n in a.pose_bones if n in b.rot]

    from_frames = _search_range(a, rec_a, tail=True)
    to_frames = _search_range(b, rec_b, tail=False)

    best = None
    for i in from_frames:
        penalty = _foot_penalty(a, i)
        for j in to_frames:
            score = pose_distance(a, i, b, j, shared) + penalty
            if best is None or score < best[0]:
                best = (score, i, j)
    _, i, j = best

    cost = pose_distance(a, i, b, j, shared)
    per_channel = channel_costs(a, i, b, j)
    fps = a.fps or 30
    seam = Seam(from_id, to_id, i, j, cost, 0, None, [], per_channel,
                support_channels(rec_a, rec_b))
    frames = blend_frames_for(seam.pace_deg(), fps)
    posture_a = (rec_a.get("composability") or {}).get("posture")
    posture_b = (rec_b.get("composability") or {}).get("posture")
    cls = classify(frames, posture_a, posture_b)

    notes = []
    if cls == CLASS_POSTURE_CHANGE:
        notes.append("%s is %s, %s is %s; no clip in the library covers the change between them"
                     % (from_id, posture_a, to_id, posture_b))
    if _foot_penalty(a, i) > 0:
        notes.append("cut while a foot is off the ground; no fully planted frame was available")
    if payload_window(rec_a, a.frames) is not None and i != a.frames - 1:
        notes.append("trimmed %d frame(s) off the end of %s, outside its payload" % (a.frames - 1 - i, from_id))
    if payload_window(rec_b, b.frames) is not None and j != 0:
        notes.append("skipped %d opening frame(s) of %s, outside its payload" % (j, to_id))
    seam.blend_frames = frames
    seam.cls = cls
    seam.notes = notes
    return seam


# ---- mixing: two sources on one channel, at the same time ------------------------------------

def mix_entry_frame(a, a_frame, b, channels, rec_b=None):
    """Which frame of `b` to enter on so that, on the channels it MIXES with `a`, it starts closest to
    where `a` already is. Returns (frame, worst_disagreement_deg).

    A seam joins two clips in TIME; this joins them in SPACE, on channels both of them drive at once.
    Same measurement either way — the per-channel rotation distance — so the answer comes from the same
    place rather than from a second idea about what "close" means.

    IT MATTERS MORE HERE THAN AT A SEAM. A seam is a handover, and a bad one shows as a snap that is
    over in a few frames. A mix is a weighted average held for the whole step, and averaging two poses
    that are far apart does not read as either of them — 0.4 of a stance and 0.6 of a walk stride, half
    a period out, puts the legs somewhere neither clip ever put them. Measured on the pair this exists
    for, `giving_pills` under `walking`: entering the walk at its own frame 0 leaves the left leg 49.5
    degrees away from where the base has it, and the aligned frame leaves it 12.6, against a worst case
    over the cycle of 63.7.

    ONE PHASE PER CLIP, NOT PER CHANNEL, AND THAT IS THE POINT OF PASSING A LIST. Asked separately, the
    same pair wants frame 11 for the left leg and frame 1 for the right — and honouring both would mean
    playing one walk cycle at two phases, which is two legs stepping independently of each other. A clip
    is one performance and its channels are coupled through it; splitting that is a worse error than the
    misalignment it fixes. So the frame is the one that minimises the WORST channel, not the sum: a mean
    would let a well-matched leg pay for a badly-matched one.

    ENTRY ONLY, AND THAT IS A REAL LIMIT. The two clips then advance at their own rates and drift apart;
    `walking` is 29 frames and nothing else in the corpus is, so a mix that runs for seconds ends up
    wherever the two periods take it. Aligning entry removes the worst of it and nothing more. Sustained
    alignment is time warping — resampling one clip onto the other's phase — and that is not this. The
    drift is left for the gate to measure rather than hidden behind a number here.

    Returns (0, None) when no channel has bones sampled in both clips, which is the same "nothing to go
    on" answer `channel_costs` gives.
    """
    if isinstance(channels, str):
        channels = [channels]
    bones = {c: [n for n in CHANNEL_BONES.get(c, []) if n in a.rot and n in b.rot] for c in channels}
    bones = {c: names for c, names in bones.items() if names}
    if not bones or a_frame >= a.frames:
        return 0, None

    window = _search_range(b, rec_b, tail=False) if rec_b is not None else list(range(b.frames))
    best, best_at = None, window[0]
    for j in window:
        worst = max(pose_distance(a, a_frame, b, j, names) for names in bones.values())
        if best is None or worst < best:
            best, best_at = worst, j
    return best_at, best


# ---- the table -------------------------------------------------------------------------------

def load_clips(kb, raw_dir=None):
    """Clip objects for every accepted action, keyed by action_id (the `_raw` files are keyed by clip
    name, which is not the same string — that mismatch is a standing trap in this repo)."""
    out = {}
    for action_id, rec in kb.actions.items():
        clip_name = (rec.get("source_clip") or {}).get("clip_name")
        if not clip_name:
            continue
        out[action_id] = load_clip(clip_name, loop=bool(rec.get("loop")), raw_dir=raw_dir)
    return out


def build_table(kb, raw_dir=None):
    """Every ordered pair. 8 actions = 56 seams, each a small search — under a second in total."""
    clips = load_clips(kb, raw_dir=raw_dir)
    ids = sorted(clips)
    seams = []
    for from_id in ids:
        for to_id in ids:
            if from_id != to_id:
                seams.append(find_seam(from_id, to_id, kb, clips))
    return seams


def channel_ramps(seam, window_s):
    """How each channel crosses one seam: when it starts, inside the window, and how long it takes.

    END-ALIGNED, and that is the entire mechanism. Every channel finishes at the same moment — when the
    incoming action is fully established — so a channel with less ground to cover starts later, and
    arrives late without anyone having decided that it should. The upper body coming in after the legs
    is not a rule written down anywhere here; it is what falls out of the upper body having less to do.

    And because each width is the window scaled by that channel's share of the busiest travel, every
    channel sweeps at the SAME angular rate, the one the window was sized for. A single weight cannot do
    that: it moves the busiest channel and the quietest one over the same seconds, so one snaps at
    several times the stated ceiling while the other crawls. Measured on this corpus, that ratio runs
    from 2.9 to 5.2.

    Support channels take the whole window whatever their travel — see `support_channels`. So do the
    fingers, and for them the rate guarantee genuinely does not hold: they were left out of the window
    sizing, so a curl larger than the busiest arm's travel is still swept in the arm's time. That is the
    status quo made visible rather than a regression, and it is the price of not letting a hand pose
    stretch every seam in the corpus.

    Channels sharing a width share a group, because they will share one mask and one weight in the
    engine. A hard cut collapses to a single group of everything at zero length, which is what a `direct`
    seam is.
    """
    pace = seam.pace_deg()
    groups = {}
    for channel in sorted(CHANNEL_BONES):
        cost = seam.channel_cost_deg.get(channel)
        if channel in seam.support or cost is None or pace <= 0:
            share = 1.0
        else:
            share = min(1.0, cost / pace)
        groups.setdefault(round(window_s * share, 4), []).append(channel)
    return [{"channels": channels, "offset_s": round(max(0.0, window_s - width), 4),
             "blend_in_s": width}
            for width, channels in sorted(groups.items(), reverse=True)]


# ---- scheduling ------------------------------------------------------------------------------

class Step(object):
    """One entry on the timeline the executor plays.

    Every field is a time or a frame, and every one is computed here rather than in the engine. The
    executor's job is to play a schedule, not to decide one — the same split that keeps channel
    arbitration on this side.
    """

    __slots__ = ("action_id", "start_at_s", "blend_in_s", "clip_start_frame", "duration_s", "loop",
                 "seam_class", "generated", "channel_blends")

    def __init__(self, action_id, start_at_s, blend_in_s, clip_start_frame, duration_s, loop,
                 seam_class, generated=None, channel_blends=None):
        self.action_id = action_id
        self.start_at_s = start_at_s
        self.blend_in_s = blend_in_s
        self.clip_start_frame = clip_start_frame
        self.duration_s = duration_s
        self.loop = loop
        self.seam_class = seam_class
        self.generated = generated
        # Empty on the opening step, which enters over no seam. `blend_in_s` stays the length of the
        # whole handover and remains what a reader should quote; these are how it is spent per channel.
        self.channel_blends = channel_blends or []

    def as_dict(self):
        out = {"action_id": self.action_id,
               "start_at_s": round(self.start_at_s, 4),
               "blend_in_s": round(self.blend_in_s, 4),
               "clip_start_frame": self.clip_start_frame,
               "duration_s": None if self.duration_s is None else round(self.duration_s, 4),
               "loop": self.loop,
               "seam_class": self.seam_class,
               "channel_blends": self.channel_blends}
        if self.generated:
            out["generated"] = self.generated
        return out


# How fast a body may be lowered or raised under its own control, in metres per second. A stated
# assumption, not a fitted constant: it sets how long a generated sit takes (0.44 m of hip drop at this
# rate is 0.88 s), and the gate's tracking error is what can falsify it.
POSTURE_CHANGE_RATE_M_PER_S = 0.5


def hip_height(clip, frame):
    """Height of the Hips bone above the floor at one frame, or None if it was not sampled."""
    track = clip.bones.get("Hips")
    if not track or frame >= len(track):
        return None
    return track[frame][1]


def bone_at(clip, bone, frame):
    """A sampled bone position at one frame, root-local (the corpus is in-place, so this is also the
    character-space position). None if that bone was not sampled."""
    track = clip.bones.get(bone)
    if not track or frame >= len(track):
        return None
    return list(track[frame])


def schedule(action_ids, kb, clips, min_loop_cycles=1, generate_posture_changes=False,
             open_at_seam=False):
    """Turn an ordered list of actions into a timeline.

    Each step enters at its seam's `to_frame` and hands over at the next seam's `from_frame`; the
    crossfade sits at that boundary, incoming rising while the outgoing keeps playing and falls. A
    non-looping clip that runs out mid-fade holds its final pose, which is what an
    AnimationClipPlayable does anyway and reads correctly.

    A looping step gets at least `min_loop_cycles` whole cycles before it hands over, because walking
    is 29 frames and cutting at the seam alone would show a third of a step and call it a walk.

    `open_at_seam` says the FIRST step is only there to be departed from: she is already standing
    where this happens, and that step names a pose rather than a performance. It then ENTERS on its
    seam frame and hands over immediately, instead of playing up to that frame and, for a looping
    clip, a whole cycle first. Measured: `walking` is 0.97 s, so an opener she was not walking marched
    her on the spot for a stride before she sat; `idle` is 8.4 s, so opening on that instead left her
    standing motionless for eight seconds. Neither of those is watched — what the step supplies is the
    pose the next one departs from, which is exactly the frame the seam search picked. It also makes
    the generated descent honest: its start hip height is read off that frame, and that frame is now
    one that actually plays.

    A posture change raises unless `generate_posture_changes` is set. That flag is not a way to silence
    the objection — it says the caller has found something to sit on and accepts that the frames will be
    generated rather than retrieved. Left off, scheduling one as an ordinary crossfade would be exactly
    the lie the seam class exists to prevent.
    """
    if len(action_ids) < 1:
        raise ValueError("a sequence needs at least one action")

    seams = [find_seam(a, b, kb, clips) for a, b in zip(action_ids, action_ids[1:])]
    if not generate_posture_changes:
        bad = [s for s in seams if s.cls == CLASS_POSTURE_CHANGE]
        if bad:
            raise ValueError("%s -> %s is a posture change; it cannot be scheduled as a blend"
                             % (bad[0].from_action, bad[0].to_action))

    steps, t = [], 0.0
    clip_start = 0
    for index, action_id in enumerate(action_ids):
        clip = clips[action_id]
        fps = clip.fps or 30
        blend_in = 0.0 if index == 0 else seams[index - 1].blend_frames / float(fps)
        departure_pose = index == 0 and open_at_seam and seams
        if index > 0:
            clip_start = seams[index - 1].to_frame
        elif departure_pose:
            clip_start = seams[0].from_frame

        if index < len(seams):
            span = seams[index].from_frame - clip_start
            if departure_pose:
                span = 0            # entered on the frame it hands over from; nothing left to play
            elif clip.loop:
                # Whole cycles first, then on to the seam frame.
                span += min_loop_cycles * clip.frames
            elif span <= 0:
                # The seam sits at or before where this step entered: nothing of it would play. Give it
                # the rest of the clip rather than a zero-length step nobody can see.
                span = clip.frames - 1 - clip_start
            duration = max(1, span) / float(fps)
        else:
            duration = None if clip.loop else (clip.frames - 1 - clip_start) / float(fps)

        seam_class = seams[index - 1].cls if index > 0 else None
        generated = None
        if seam_class == CLASS_POSTURE_CHANGE:
            # THE TARGET IS NOT GUESSED. Where the hips must end up is the hip height of this step's own
            # first played frame, because this step is what plays next. The duration follows from how
            # far they have to travel at a stated rate, so both numbers are derived from measured data
            # and neither is available for a model to invent.
            from_clip = clips[action_ids[index - 1]]
            start_hip = hip_height(from_clip, seams[index - 1].from_frame)
            target_hip = hip_height(clip, clip_start)
            if start_hip is None or target_hip is None:
                raise ValueError("no hip track for %s -> %s; cannot generate the transition"
                                 % (action_ids[index - 1], action_id))
            travel = abs(start_hip - target_hip)
            seconds = max(0.3, travel / POSTURE_CHANGE_RATE_M_PER_S)
            generated = {
                "kind": CLASS_POSTURE_CHANGE,
                "from_action": action_ids[index - 1],
                "to_action": action_id,
                "start_hip_height_m": round(start_hip, 4),
                "target_hip_height_m": round(target_hip, 4),
                "hip_travel_m": round(travel, 4),
                "duration_s": round(seconds, 4),
                "note": "no clip in the library covers this; these frames are generated",
            }
            # WHERE THE FEET SHOULD END UP, and again not a guess: the incoming clip's own first played
            # frame has them. Pinning them where the outgoing motion left them makes the result a squat
            # in a walking stride rather than a sit with the feet under the knees -- which is exactly
            # what the first version produced, and what a screenshot showed and no metric did.
            left = bone_at(clip, "LeftFoot", clip_start)
            right = bone_at(clip, "RightFoot", clip_start)
            if left and right:
                generated["foot_targets"] = {
                    "left": [round(v, 4) for v in left],
                    "right": [round(v, 4) for v in right],
                    "space": "character_local",
                }
            # The crossfade IS the generated transition, so it lasts as long as the descent rather than
            # as long as the angular-rate rule would have made it.
            blend_in = seconds

        # Computed last, off whatever `blend_in` finally is: a generated posture change overrides the
        # window above, and the per-channel split has to be a split of the window that will actually be
        # played rather than of the one the rate rule proposed.
        ramps = channel_ramps(seams[index - 1], blend_in) if index > 0 else []

        steps.append(Step(action_id, t, blend_in, clip_start, duration, bool(clip.loop),
                          seam_class, generated, ramps))
        if duration is None:
            break
        t += duration
    return steps


TABLE_PATH = os.path.join(paths.KB_DIR, "_derived", "transitions.json")


# raw_dir -> (stat signature, digest). See raw_fingerprint.
_FINGERPRINT_CACHE = {}


def raw_fingerprint(raw_dir=None):
    """Content hash of every `_raw` dump the table was derived from.

    A cache with no way to notice its inputs moved is worse than no cache: it answers confidently with
    the old corpus. Re-sample one clip and this changes, so a stale table announces itself instead of
    being believed.

    WHY THIS IS MEMOISED. Every `read_table` calls it, and it hashes 7 MB of JSON. That is low tens of
    milliseconds on a local disk -- the cost the original version budgeted for -- but the KB normally
    lives on a Windows worktree reached over DrvFs, where the same read is about 270 ms. Paid once per
    tool registry and once per table, across a test suite it was most of a nine-second gap between
    running against the mounted KB and against a local copy of it.

    So it is memoised, and the DEFAULT corpus is memoised without touching the filesystem at all. That
    is not an assumption about disks, it is the write discipline: the only writer of `_raw` is
    `unity_sampler.write_raw`, running in the offline pipeline, and it calls `forget_raw`. Every other
    process treats the KB as read-only, so within one of them `_raw` cannot move. A stat-per-file check
    would cost 8 x 19 ms here and prove something already guaranteed.

    Pass `raw_dir` explicitly -- as the builders and the tests do -- and the memo is verified against
    each file's (name, size, mtime) instead, because that corpus is the caller's and may well change.
    """
    import hashlib
    frozen = raw_dir is None
    raw_dir = raw_dir or paths.RAW_DIR

    if frozen:
        cached = _FINGERPRINT_CACHE.get(raw_dir)
        if cached is not None:
            return cached[1]
        names, signature = None, None
    else:
        names = sorted(n for n in os.listdir(raw_dir) if n.endswith(".json"))
        signature = tuple((n, s.st_size, s.st_mtime_ns) for n, s in
                          ((n, os.stat(os.path.join(raw_dir, n))) for n in names))
        cached = _FINGERPRINT_CACHE.get(raw_dir)
        if cached is not None and cached[0] == signature:
            return cached[1]

    if names is None:
        names = sorted(n for n in os.listdir(raw_dir) if n.endswith(".json"))
    h = hashlib.sha256()
    for name in names:
        h.update(name.encode("utf-8"))
        with open(os.path.join(raw_dir, name), "rb") as fh:
            h.update(fh.read())
    digest = h.hexdigest()[:16]
    _FINGERPRINT_CACHE[raw_dir] = (signature, digest)
    return digest


def write_table(seams, path=None, extra=None, raw_dir=None):
    """Write the cache. DERIVED, not contract: `_raw` is untouched, and deleting this file costs a
    rebuild, not information."""
    doc = {
        "_meta": {
            "kind": "derived",
            "derived_from": "_raw bone_rot (root_local, xyzw)",
            "regenerate": "python build_transitions.py",
            "raw_fingerprint": raw_fingerprint(raw_dir),
            "max_blend_rate_deg_per_s": MAX_BLEND_RATE_DEG_PER_S,
            "search_fraction": SEARCH_FRACTION,
            "note": "Regenerable sidecar. Not part of the motionkb/v2 contract; no record references it.",
        },
        "seams": [s.as_dict() for s in seams],
    }
    if extra:
        doc["_meta"].update(extra)
    return paths.write_json(path or TABLE_PATH, doc)


# TABLE_PATH -> ((size, mtime_ns, raw fingerprint), table). See read_table.
_TABLE_CACHE = {}


def read_table(path=None, check_fingerprint=True, raw_dir=None):
    """Cached seams keyed by (from, to), or None if there is no usable cache.

    Returns None rather than stale data when `_raw` has moved underneath it, so the caller recomputes
    instead of quietly answering about a corpus that no longer exists.

    WHY THE DEFAULT PATH IS MEMOISED. Every tool registry reads this table once, and a test suite builds
    a registry per test, so the same file was opened a few hundred times. On a local disk that is
    nothing; the KB normally lives on a Windows worktree reached over DrvFs, where one open costs about
    9 ms and one stat about 1.4 ms, and it became seconds. The memo is keyed on the file's (size, mtime)
    together with the `_raw` fingerprint, so it expires for exactly the reasons the fingerprint exists.
    Only the default path is cached: pass `path` explicitly -- as the builders and the tests do -- and
    the read is unconditional. The cached table is SHARED; copy it before mutating.
    """
    memo = path is None and raw_dir is None and check_fingerprint
    path = path or TABLE_PATH
    if not os.path.exists(path):
        return None
    fingerprint = raw_fingerprint(raw_dir) if check_fingerprint else None
    key = None
    if memo:
        st = os.stat(path)
        key = (st.st_size, st.st_mtime_ns, fingerprint)
        hit = _TABLE_CACHE.get(path)
        if hit is not None and hit[0] == key:
            return hit[1]
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if check_fingerprint:
        stored = (doc.get("_meta") or {}).get("raw_fingerprint")
        if stored and stored != fingerprint:
            return None
    table = {(s["from"], s["to"]): s for s in doc.get("seams", [])}
    if memo:
        _TABLE_CACHE[path] = (key, table)
    return table
