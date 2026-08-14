"""
segments.py — which frames of a clip a single body channel is actually doing something in.

Assembly's smallest unit used to be A WHOLE CLIP hung on a channel. `walking + cpr` therefore meant
walking's legs under all 540 frames of chest compressions — eighteen seconds of arm, under a walk that
takes one. What the composition wanted was one compression. This module is where that window comes
from, and like the seam table it is DERIVED from the frozen `_raw` dumps rather than added to the
contract: no record references it, deleting it costs a rebuild.

TWO KINDS OF WINDOW, AND THE CORPUS DECIDES WHICH IT HAS. Both are measured, and the numbers below are
what the corpus actually returned rather than a shape assumed in advance:

  ACTIVE   the span between the first and last frame where the channel exceeds a fraction of its own
           peak speed. This is the "trim the dead ends" reading, and it is worth much less than it
           sounds: `check_pulse`, `giving_pills`, `cpr` and `walking` have NO dead frames on any
           channel. It trims about a sixth off `grab_bottle` (its hold at the end), the opening of
           `typing`'s hands, and the ends of `bvm`.

  CYCLE    for a channel whose motion repeats, one repetition. This is the one that matters, and only
           two clips have it: `cpr` repeats every 18 frames on all eight channels (0.00 deg residual
           against a 2-4 deg typical spread — thirty compressions), and `bvm`'s right hand every 88-89
           (0.2 deg against 9.3). Everything else returns the smallest lag searched, which is the
           signature of no periodicity at all: nearby frames are simply the closest ones.

WHY THE THRESHOLDS ARE WHERE THEY ARE. Each was read off the measured distribution, and the gap it sits
in is recorded so a later corpus can falsify it rather than inherit it:

  MIN_LAG                 4  below this a "period" is frame adjacency, not repetition
  CYCLE_TRAVEL_FLOOR    20   deg. The channels that turned out cyclic travel 98.6 and 241.8 deg; the
                             busiest channel that moves too little to have a cycle worth taking is
                             `bvm.head` at 4.4. A 20x gap, and the floor sits in it. Without it, a
                             channel that never moves reports a perfect cycle at every lag, because
                             every frame of it matches every other.
  CYCLE_RESIDUAL_FRAC   0.10 of the clip's own typical spread. Measured: cpr 0.000, bvm 0.088; the
                             nearest non-cyclic channel with enough travel to be a candidate is
                             `typing.torso` at 0.103. That is close, which is why the fundamental must
                             also beat MIN_LAG -- two independent reasons, and every rejection in this
                             corpus fails both.
  QUIET_FRACTION        0.10 of the channel's own peak speed. Relative, not absolute: a channel is
                             being compared against itself, so this says nothing about how fast the
                             action is.

ONE PERFORMANCE, ONE WINDOW PER ACTION -- not per channel. That decision is not made here (see
`tools/scene.py`), but it is why this returns a window per channel rather than a plan: the union across
the channels an action actually drives is the caller's to take, and giving one clip's two hands
different windows would have them living at different moments.
"""
import os

import paths
from .transitions import CHANNEL_BONES, quat_angle_deg

# Shortest lag that can count as a period. Under this, "the pose repeats every N frames" is just the
# observation that consecutive frames look alike, which is true of every clip ever authored.
MIN_LAG = 4

# A channel has to have moved this far in total before anything about WHEN it moves means anything.
# Used twice, for what is really one question -- does this channel do something measurable:
#   * below it, a "repeat" is vacuous, because a still channel matches itself at every lag;
#   * below it, the channel puts no constraint on an action's window either, because it looks the same
#     wherever it is played. That second use is what lets `bvm` hand out its right hand's 89-frame
#     squeeze instead of all 180 frames: its torso and arms are claimed as `support` but travel 0.5-3.5
#     degrees over the whole clip, so they have no opinion about which frames to take.
# See the module docstring for the gap this sits in.
CYCLE_TRAVEL_FLOOR_DEG = 20.0

# How close a repeat has to be, as a fraction of how far apart this channel's own frames typically are.
# Relative because the comparison is within one channel of one clip: an absolute degree cutoff would
# call a still channel cyclic and a broad one arrhythmic.
CYCLE_RESIDUAL_FRAC = 0.10

# Cycle length is measured to about a frame, so the fundamental is refined within this neighbourhood by
# taking the lowest residual. On `bvm` that is the difference between 88 frames at 0.82 deg and 89 at
# 0.20 -- both loop, one loops better.
CYCLE_REFINE_FRAMES = 2

# Below this fraction of its own peak speed, a channel counts as not moving, for trimming the ends.
QUIET_FRACTION = 0.10

# How many frames to sample when averaging a self-distance. The full O(n^2) is affordable here (8 clips,
# the longest 540 frames) but pointless: the answer is an average, and 40 samples fix it to well under
# the thresholds above.
SAMPLES = 40

TABLE_PATH = os.path.join(paths.KB_DIR, "_derived", "segments.json")


def _bones(clip, channel):
    return [n for n in CHANNEL_BONES.get(channel, []) if n in clip.rot]


def _distance(clip, bones, i, j):
    """Mean per-bone rotation difference between two frames OF THE SAME CLIP, in degrees."""
    return sum(quat_angle_deg(clip.rot[n][i], clip.rot[n][j]) for n in bones) / len(bones)


def _step_speeds(clip, bones):
    """Degrees per second between each pair of consecutive frames. Length is frames - 1."""
    fps = clip.fps or 30
    return [_distance(clip, bones, i, i + 1) * fps for i in range(clip.frames - 1)]


def _sampled_mean(clip, bones, lag):
    """Average distance between frames `lag` apart. This is the autocorrelation, in degrees, upside
    down: a period shows up as a minimum rather than a peak."""
    span = clip.frames - lag
    step = max(1, span // SAMPLES)
    indices = range(0, span, step)
    return sum(_distance(clip, bones, i, i + lag) for i in indices) / len(indices)


def _spread(clip, bones):
    """How far apart this channel's frames typically are, measured from the first one. The scale every
    residual below is judged against, so that a slow channel and a violent one are held to the same
    proportional standard rather than the same number of degrees."""
    step = max(1, clip.frames // SAMPLES)
    indices = range(1, clip.frames, step)
    return sum(_distance(clip, bones, 0, j) for j in indices) / len(indices)


def active_span(clip, bones):
    """(first, last_exclusive) frames where the channel is moving, by its own standard.

    Returns the whole clip when the channel never crosses its own quiet threshold, which happens when
    it does not move at all -- the peak is then noise and every frame is within a tenth of it.
    """
    speeds = _step_speeds(clip, bones)
    if not speeds:
        return 0, clip.frames
    floor = max(speeds) * QUIET_FRACTION
    moving = [i for i, v in enumerate(speeds) if v >= floor]
    if not moving:
        return 0, clip.frames
    # +2 rather than +1: `speeds[i]` is the step from frame i to i+1, so the last moving step ENDS on
    # the frame after it, and an exclusive bound is one past that.
    return moving[0], min(clip.frames, moving[-1] + 2)


def cycle_length(clip, bones, travel_deg):
    """Frames per repetition, or None when the channel does not repeat.

    Returns the FUNDAMENTAL, not a harmonic: `cpr` matches itself at 18, 36 and 54 frames and only 18
    is the compression. Taking the smallest qualifying lag is what makes that automatic.
    """
    if travel_deg < CYCLE_TRAVEL_FLOOR_DEG:
        return None, None
    horizon = clip.frames // 2
    if horizon <= MIN_LAG:
        return None, None
    spread = _spread(clip, bones)
    if spread <= 0:
        return None, None
    limit = spread * CYCLE_RESIDUAL_FRAC
    residuals = {lag: _sampled_mean(clip, bones, lag) for lag in range(MIN_LAG, horizon)}
    qualifying = sorted(lag for lag, r in residuals.items() if r <= limit)
    if not qualifying or qualifying[0] <= MIN_LAG:
        # The best lag being the shortest one searched is the signature of NO periodicity: the residual
        # is still climbing, so what was found is adjacency rather than repetition.
        return None, None
    near = [lag for lag in residuals
            if abs(lag - qualifying[0]) <= CYCLE_REFINE_FRAMES and lag >= MIN_LAG]
    best = min(near, key=lambda lag: residuals[lag])
    return best, residuals[best]


def channel_segment(clip, channel):
    """One channel's window, and the measurements behind it. None when the channel was not sampled.

    `frames` is the window to play: one repetition where there is one, the moving span otherwise. That
    choice is made here rather than by the caller so that "which frames" has a single answer per
    channel, recorded with the evidence for it.
    """
    bones = _bones(clip, channel)
    if not bones:
        return None
    speeds = _step_speeds(clip, bones)
    travel = sum(_distance(clip, bones, i, i + 1) for i in range(clip.frames - 1))
    start, end = active_span(clip, bones)
    lag, residual = cycle_length(clip, bones, travel)

    if lag is not None:
        # A cycle is taken from inside the moving span, so a clip that settles before it starts
        # repeating does not hand out its settling frames as the repetition.
        end = min(clip.frames, start + lag)
        loop_gap = _distance(clip, bones, start, end - 1)
    else:
        loop_gap = _distance(clip, bones, start, end - 1)

    return {
        "channel": channel,
        "start_frame": start,
        "end_frame": end,
        "frames": end - start,
        "clip_frames": clip.frames,
        # Whether this channel has any opinion about which frames to take. A still channel is not
        # "unimportant" -- it may well be holding a pose the action needs -- it simply looks the same
        # at every frame, so it constrains nothing. See `window_for`.
        "moving": travel >= CYCLE_TRAVEL_FLOOR_DEG,
        "travel_deg": round(travel, 2),
        "peak_deg_per_s": round(max(speeds), 2) if speeds else 0.0,
        "cycle_frames": lag,
        "cycle_residual_deg": None if residual is None else round(residual, 3),
        # How far the window's own two ends are apart. A layer that loops inside its window jumps this
        # far every time round, so it is reported rather than turned into a boolean somewhere else.
        "loop_gap_deg": round(loop_gap, 2),
    }


def for_action(clip):
    """Every sampled channel of one clip."""
    out = []
    for channel in sorted(CHANNEL_BONES):
        seg = channel_segment(clip, channel)
        if seg is not None:
            out.append(seg)
    return out


def build_table(clips):
    """{action_id: [segment, ...]} for every action with a `_raw` dump."""
    return {action_id: for_action(clip) for action_id, clip in sorted(clips.items())}


def window_for(action_segments, channels):
    """The frames ONE action contributes, given the channels it drives, or None for the whole clip.

    Returns {start_frame, end_frame, loop, why}. `loop` says whether reaching the end wraps back to the
    start: true only for a repetition, whose two ends were measured to join.

    ONE CLIP IS ONE PERFORMANCE, so this is a union and not a window per channel: its parts are coupled
    through it, and playing an action's two hands from two different moments is not that action. The
    same reasoning already keeps a mixed overlay on a single entry phase.

    STILL CHANNELS ARE NOT IN THE UNION. A channel below the travel floor looks the same at every
    frame, so including it would widen the window to the whole clip for no gain -- which is exactly
    what it did to `bvm`, whose right hand repeats every 89 frames while the three support channels it
    also claims travel under four degrees between them.

    Returns None when nothing is left to say: no segments, no moving channel, or a window that is the
    whole clip anyway. A caller that gets None sends no frame bounds at all, which is what every layer
    did before this existed.
    """
    by_channel = {seg["channel"]: seg for seg in action_segments or []}
    moving = [by_channel[c] for c in channels if c in by_channel and by_channel[c]["moving"]]
    if not moving:
        return None
    start = min(seg["start_frame"] for seg in moving)
    end = max(seg["end_frame"] for seg in moving)
    # The clip's own length, read off the segments rather than passed in. The KB records `duration` in
    # seconds, so a caller converting it back to frames would be reintroducing a rounding step that the
    # table already did once, against the dump it was measured from.
    if start <= 0 and end >= max(seg["clip_frames"] for seg in moving):
        return None
    cycles = sorted({seg["cycle_frames"] for seg in moving if seg["cycle_frames"]})
    # A REPETITION IS THE ONLY WINDOW THAT LOOPS. Every moving channel has to agree on the period and
    # the window has to be exactly that long — otherwise what is being wrapped is the moving part of a
    # one-shot gesture, and wrapping THAT snaps the arm back to where it reached from.
    repetition = (len(cycles) == 1
                  and all(seg["cycle_frames"] == cycles[0] for seg in moving)
                  and end - start == cycles[0])
    return {"start_frame": start, "end_frame": end, "loop": repetition,
            "why": ("one repetition of %d frames" % cycles[0] if repetition
                    else "the frames it is moving in")}


def write_table(table, path=None, raw_dir=None):
    """Write the sidecar. Same discipline as the seam table: derived, fingerprinted against `_raw`, and
    referenced by no record in the contract."""
    from .transitions import raw_fingerprint
    doc = {
        "_meta": {
            "kind": "derived",
            "derived_from": "_raw bone_rot (root_local, xyzw)",
            "regenerate": "python build_segments.py",
            "raw_fingerprint": raw_fingerprint(raw_dir),
            "min_lag_frames": MIN_LAG,
            "cycle_travel_floor_deg": CYCLE_TRAVEL_FLOOR_DEG,
            "cycle_residual_fraction": CYCLE_RESIDUAL_FRAC,
            "quiet_fraction": QUIET_FRACTION,
            "note": "Regenerable sidecar. Not part of the motionkb/v2 contract; no record references "
                    "it. A window is one repetition where the channel repeats, and the span between "
                    "its first and last moving frame otherwise.",
        },
        "segments": table,
    }
    return paths.write_json(path or TABLE_PATH, doc)


def read_table(path=None, check_fingerprint=True, raw_dir=None):
    """The cached table, or None when there is none or `_raw` has moved under it.

    None rather than stale data, for the reason the seam table gives: a cache that cannot notice its
    inputs changed answers confidently about a corpus that no longer exists.
    """
    from .transitions import raw_fingerprint
    path = path or TABLE_PATH
    if not os.path.exists(path):
        return None
    doc = paths.read_json(path)
    if check_fingerprint:
        stored = (doc.get("_meta") or {}).get("raw_fingerprint")
        if stored and stored != raw_fingerprint(raw_dir):
            return None
    return doc.get("segments") or None
