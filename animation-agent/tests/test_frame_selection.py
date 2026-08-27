"""Which frames the labeller is shown.

The claim under test is not "the selector returns three numbers" but "the three pictures represent the
clip". That is one measurable quantity -- the RADIUS: how far the worst-covered frame of the clip is
from the nearest chosen frame, in the same normalised muscle space the channel metric uses. Every test
here is about that number, plus the naming that has to survive two frames landing close together in
time now that they are chosen by pose.
"""
import glob
import json
import math
import os

import pytest

import paths
import propose
import unity_sampler as US

K = US.K_FRAMES
OLD_FRACS = [0.30, 0.55, 0.80]


# ------------------------------------------------------------------------------------- helpers

def radius(M, idx):
    """Distance from the worst-covered frame to its nearest chosen frame."""
    return max(min(US._pose_distance(M[i], M[j]) for j in idx) for i in range(len(M)))


def as_indices(fracs, n):
    return sorted({int(round(f * (n - 1))) for f in fracs})


def old_action_window_fracs(raw):
    """The selector this replaced, kept here so the regression bar is measured against the code that
    was actually running and not against its fixed-fraction fallback. It found the frames where the
    busiest effector's distance from the Hips was in the top 40% of its range -- the "action window" --
    and spread three frames at 15/50/85% of it."""
    b = raw.get("bones") or {}
    hips = b.get("Hips")
    seqs = [x for x in b.values() if x]
    nfr = min([len(x) for x in seqs] + [len(hips)]) if (hips and seqs) else 0
    best = None
    for bone in ("LeftHand", "RightHand", "LeftFoot", "RightFoot"):
        ser = b.get(bone)
        if not ser or nfr < 2:
            continue
        d = [math.sqrt(sum((ser[i][k] - hips[i][k]) ** 2 for k in range(3))) for i in range(nfr)]
        mean = sum(d) / nfr
        var = sum((v - mean) ** 2 for v in d) / nfr
        if best is None or var > best[0]:
            best = (var, d)
    if best is None or nfr < 3:
        return list(OLD_FRACS)
    ser = best[1]
    lo_v, hi_v = min(ser), max(ser)
    if hi_v - lo_v < 0.02:
        lo, hi = 0, nfr - 1
    else:
        thr = lo_v + 0.60 * (hi_v - lo_v)
        active = [i for i in range(nfr) if ser[i] >= thr]
        lo, hi = active[0], active[-1]
    span = hi - lo
    return sorted(round((lo + int(round(span * f))) / (nfr - 1), 3) for f in (0.15, 0.50, 0.85))


def synth(*segments):
    """A muscle series from (n_frames, value) segments; each frame is 95 DOF all at `value`."""
    M = []
    for n, v in segments:
        M.extend([[float(v)] * 95 for _ in range(n)])
    return {"muscles": M}


def ramp_then_hold(ramp=13, hold=63, out=10):
    """The shape that broke the old selector: a short move into a pose, then a long hold, then a move
    back out. `check_pulse` is exactly this, and it is what most held nursing actions look like."""
    M = [[i / float(ramp)] * 95 for i in range(ramp)]          # 0 -> 1
    M += [[1.0] * 95 for _ in range(hold)]                     # held
    M += [[1.0 - i / float(out)] * 95 for i in range(out)]     # 1 -> 0
    return {"muscles": M}


# ------------------------------------------------------------------------------- the contract

def test_returns_k_distinct_sorted_fractions_in_range():
    fracs = US.select_fracs(ramp_then_hold())
    assert len(fracs) == K
    assert fracs == sorted(fracs)
    assert len(set(fracs)) == K
    assert all(0.0 <= f <= 1.0 for f in fracs)


def test_is_deterministic():
    raw = ramp_then_hold()
    assert US.select_fracs(raw) == US.select_fracs(raw) == US.select_fracs(ramp_then_hold())


def test_falls_back_to_fixed_fractions_without_a_usable_dump():
    """`render` may run before `sample`. Falling back beats refusing to render at all."""
    assert US.select_fracs({}) == US.RENDER_FRACS
    assert US.select_fracs({"muscles": []}) == US.RENDER_FRACS
    assert US.select_fracs({"muscles": [[0.0] * 95]}) == US.RENDER_FRACS


def test_a_clip_shorter_than_k_returns_every_frame_it_has():
    raw = synth((2, 0.0))
    assert US.select_frame_indices(raw) == [0, 1]
    assert US.select_fracs(raw) == [0.0, 1.0]


# ------------------------------------------------------------- the defect this replaced (synthetic)

def test_a_held_pose_is_not_shown_three_times():
    """THE BUG. The old selector found the "action window" -- the engaged part -- and put all three
    frames inside it, so a clip that ramps in, holds, and ramps out was labelled from three copies of
    the hold. Nothing about how the pose is reached was ever shown."""
    raw = ramp_then_hold()
    M = raw["muscles"]
    new = US.select_frame_indices(raw)
    held = [i for i, f in enumerate(M) if f[0] == 1.0]

    assert not set(new) <= set(held), "all three frames landed inside the hold again"
    assert radius(M, new) < radius(M, as_indices(OLD_FRACS, len(M)))


def test_frames_spread_over_a_cycle_rather_than_aliasing_with_it():
    """Fixed fractions alias against a periodic clip: 15/50/85% of four identical cycles can be the same
    phase three times. Choosing for coverage cannot, because a repeated phase covers nothing new."""
    cycle = [[math.sin(2 * math.pi * t / 20.0)] * 95 for t in range(20)]
    raw = {"muscles": cycle * 4}
    M = raw["muscles"]
    new = US.select_frame_indices(raw)
    phases = {i % 20 for i in new}
    assert len(phases) == K, "two chosen frames are the same phase of the cycle"
    assert radius(M, new) <= radius(M, as_indices(OLD_FRACS, len(M)))


def test_a_still_clip_is_covered_by_anything_and_does_not_crash():
    raw = synth((40, 0.5))
    idx = US.select_frame_indices(raw)
    assert len(idx) == K
    assert radius(raw["muscles"], idx) == 0.0


# ------------------------------------------------------------------ against the real accepted clips

def accepted_dumps():
    """(action_id, dump path) for the accepted records. The store holds every record whatever its
    status (ADR 0016), and the 2446 unlabelled ones have no action_id to name a case after -- the bar
    below is about the eight clips the KB was built from."""
    if not os.path.isdir(paths.RAW_DIR) or not os.path.exists(paths.MANIFEST):
        return []
    names = []
    for p, doc, err in paths.read_records(paths.accepted_files()):
        if err:
            continue
        clip = (doc.get("source_clip") or {}).get("clip_name")
        raw = os.path.join(paths.RAW_DIR, "%s.json" % clip)
        if clip and os.path.exists(raw):
            names.append((doc["action_id"], raw))
    return sorted(names)


@pytest.mark.parametrize("action_id,raw_path", accepted_dumps() or [("<no kb>", None)])
def test_every_accepted_clip_is_covered_at_least_as_well_as_before(action_id, raw_path):
    """The regression bar, on real data: the new selection may not be worse than the old one on ANY of
    the eight clips the KB is built from. Measured when this landed, it is better on all eight."""
    if raw_path is None:
        pytest.skip("knowledge base not available")
    with open(raw_path, encoding="utf-8") as fh:
        raw = json.load(fh)
    M = raw["muscles"]
    new = US.select_frame_indices(raw)
    old = as_indices(old_action_window_fracs(raw), len(M))
    assert radius(M, new) <= radius(M, old) + 1e-9, (
        "%s: coverage got worse (%.4f -> %.4f)" % (action_id, radius(M, old), radius(M, new)))


def test_check_pulse_no_longer_shows_one_pose_three_times():
    """The clip that made this worth fixing, named rather than left to the parametrised sweep: 2.9 s,
    13 frames of reaching and 63 of holding. The old frames sat at 21/50/80%, all inside the hold."""
    p = os.path.join(paths.RAW_DIR, "nurse_check_pulse.json")
    if not os.path.exists(p):
        pytest.skip("knowledge base not available")
    with open(p, encoding="utf-8") as fh:
        raw = json.load(fh)
    M = raw["muscles"]
    old = as_indices(old_action_window_fracs(raw), len(M))
    assert old == [18, 43, 68], "the old selector no longer reproduces the frames it shipped"
    assert radius(M, US.select_frame_indices(raw)) < 0.5 * radius(M, old)


# ----------------------------------------------------------------------------------- the view ring

def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def flat(v):
    """The camera direction with the look-down component removed, renormalised -- the azimuth alone."""
    m = math.hypot(v[0], v[2]) or 1.0
    return (v[0] / m, 0.0, v[2] / m)


def test_every_clip_gets_the_whole_ring():
    """The point of the change: eight angles, always, with no per-action decision left to get wrong."""
    for fwd in (None, [[0, 0, 1]], [[1, 0, 0]], [[-0.6, 0, 0.8]] * 5):
        ring = US.view_ring(fwd)
        assert [n for n, _ in ring] == list(US.VIEW_RING_NAMES)
    assert [n for n, _ in US.RENDER_VIEWS] == list(US.VIEW_RING_NAMES)


def test_the_ring_is_45_degrees_apart_all_the_way_round():
    ring = US.view_ring([[0.3, 0, -0.9]] * 4)
    az = [flat(d) for _, d in ring]
    for i in range(len(az)):
        cos = dot(az[i], az[(i + 1) % len(az)])
        assert abs(cos - math.cos(math.radians(45))) < 1e-6, "step %d is not 45 degrees" % i


def test_front_is_the_face_and_right_is_the_avatars_right():
    """Named from the AVATAR's frame, not the world's: `front` is wherever the clip faces, and the ring
    turns toward the avatar's own right. Shot with a facing of -Z, the avatar's right is -X."""
    ring = dict(US.view_ring([[0, 0, -1]] * 3))
    assert dot(flat(ring["front"]), (0, 0, -1)) > 0.999
    assert dot(flat(ring["back"]), (0, 0, 1)) > 0.999
    assert dot(flat(ring["right"]), (-1, 0, 0)) > 0.999
    assert dot(flat(ring["left"]), (1, 0, 0)) > 0.999


def test_one_elevation_for_all_eight():
    """A view that looks down harder than its neighbours makes the same pose read as a different one."""
    ring = US.view_ring([[0.7, 0, 0.7]] * 2)
    ys = {round(d[1] / math.hypot(math.hypot(d[0], d[1]), d[2]), 9) for _, d in ring}
    assert len(ys) == 1
    assert US.VIEW_ELEVATION > 0   # looking DOWN at the figure, not up from the floor


def test_view_batches_cover_the_ring_and_stay_inside_the_image_budget():
    """24 images in one response is ~4 MB of base64 against an 8 MB ceiling -- it fits, with no room for
    a heavier pose. Batching by view is free because the framing is computed from the times, not the
    angles, so every batch frames the clip identically."""
    ring = US.view_ring()
    for fracs in ([0.3, 0.55, 0.8], [0.0, 0.02, 0.06, 0.12], [0.5]):
        batches = US.view_batches(ring, fracs)
        assert [v for b in batches for v in b] == ring          # every view, once, in ring order
        for b in batches:
            assert len(b) * len(fracs) <= US.IMAGES_PER_CALL


# ------------------------------------------------------------------------------- frame filenames

def test_rendered_frame_names_carry_the_ordinal():
    """Two frames chosen by pose can be one frame apart in a 600-frame clip, which is the same whole
    percent -- and the render snippet names files by percent, so one would overwrite the other."""
    cs = US.build_render_csharp({"id": "x", "guid": "g", "file_id": 1},
                                views=[("front", (0, 0, 1))], fracs=[0.5001, 0.5002, 0.9])
    assert 'VN[vi]+"_t"+fi+"_f"' in cs


def test_frames_are_encoded_as_jpeg():
    """PNG at 1024 cost ~270 KB a frame, which is what kept the ring down to two views."""
    cs = US.build_render_csharp({"id": "x", "guid": "g", "file_id": 1},
                                views=US.RENDER_VIEWS, fracs=[0.5])
    assert "EncodeToJPG(tex, JQ)" in cs and "int JQ=%d;" % US.JPEG_QUALITY in cs
    assert "EncodeToPNG" not in cs
    assert '".jpg"' in cs


def test_split_frame_name_reads_both_the_new_and_the_old_form():
    assert propose.split_frame_name("/k/frames/c/back_right_t0_f7.jpg") == ("back_right", "7")
    assert propose.split_frame_name("/k/frames/c/front_left_3q_t0_f7.png") == ("front_left_3q", "7")
    assert propose.split_frame_name("/k/frames/c/side_right_t2_f100.png") == ("side_right", "100")
    assert propose.split_frame_name("/k/frames/c/front_left_3q_f21.png") == ("front_left_3q", "21")


def test_frames_sort_in_time_order_within_a_view():
    """Each attached frame is labelled with its percentage through the clip, so the manifest has to
    read in time order within one angle. Sorted lexicographically, "_f21" precedes "_f5" -- which never
    bit while every fraction was >= 15%, and would now that frame 0 gets chosen."""
    new = sorted(["front_t0_f0.jpg", "front_t1_f7.jpg", "front_t2_f15.jpg"])
    assert [propose.split_frame_name(f)[1] for f in new] == ["0", "7", "15"]
    old = sorted(["front_f0.png", "front_f7.png", "front_f15.png"])
    assert [propose.split_frame_name(f)[1] for f in old] == ["0", "15", "7"]


def test_attached_frames_go_round_the_ring_not_down_the_alphabet():
    """Sorted by name the eight views interleave -- back, back_left, back_right, front, ... -- so
    neighbouring angles land far apart. Ring order keeps one angle's moments together in the attached
    sequence; the prompt states no reading order, so nothing it says depends on this."""
    names = ["%s_t%d_f%d.jpg" % (v, i, i * 30)
             for v in sorted(US.VIEW_RING_NAMES) for i in range(3)]
    ordered = sorted(names, key=propose.frame_sort_key)
    assert [propose.split_frame_name(f)[0] for f in ordered[::3]] == list(US.VIEW_RING_NAMES)
    assert [propose.split_frame_name(f)[1] for f in ordered[:3]] == ["0", "30", "60"]


def test_provenance_records_the_views_not_the_frame_names():
    cand = {}
    propose._stamp_provenance(cand, ["a/front_t0_f0.jpg", "a/front_t1_f9.jpg",
                                     "a/back_left_t0_f0.jpg"], True)
    assert cand["extraction"]["vlm_proposal"]["render_views"] == ["back_left", "front"]
