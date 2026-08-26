#!/usr/bin/env python3
"""
calibrate_posture.py — fit the POSTURE half of the measured block from the store's own raw dumps.

WHY. The variation signal (muscle_dof_stddev_rms) measures whether a joint MOVES; it cannot see a
HOLD. An arm raised and kept raised has the same stddev over time as an arm hanging at rest: zero.
Measured on the store before this signal existed: mx_Arms_Raised read 0.0000 on both arms,
mx_Agony_Holding_The_Head (3.7 s of cradling the head) read 0.0000 on the head channel, and
mx_Armed_Villain_Holding_A_Hostage_From_Behind was static on all 8 anatomical channels for 5 s.
157 records were static on every channel — ~30 of them real-duration held poses — so "raise the
left arm and keep it there" was indistinguishable from doing nothing. The offset of the clip's MEAN
pose from a reference pose is the orthogonal half: it sees what a channel HOLDS, while stddev sees
what it MOVES.

ORIGIN (v2.5.0, ADR 0020). The reference pose is Unity's, not the corpus's. HumanPose.muscles are
normalised to [-1, 1] over each degree of freedom's HumanTrait range, and 0 is that range's CENTRE
— a definition that ships with the engine, identical on every rig, with no estimation error and no
free parameter. The root half is Unity's too: bodyPosition.y = 1.0 and bodyRotation = identity, the
Humanoid reference pose, verified across the project's rigs (X Bot 1.000000, Y Bot 0.999999,
Fat_man 0.999963, patient_avatar 0.999963, all at tilt 0.0000 — the humanScale normalisation puts
the reference hips at 1.0 by construction, across body shapes as different as X Bot and Fat_man;
rigs that read otherwise, like nurse_avatar at 0.984676 / 1.51 deg, deviate because their imported
bind pose is not a clean T-pose, which is a property of the asset, not of the standard).

WHAT THE CORPUS STILL DECIDES. Scale only: POSTURE_DIVISOR per group (the chosen percentile of the
corpus offsets normalises to 0.85, the same rule the variation divisors are fitted by) and the
neutral/displaced threshold at 0.30 x divisor. Unity fixes where zero is; the corpus fixes how much
counts as a lot. Idle clips are ordinary content and take no part in defining the standard.

WHAT THIS REPLACED, AND WHY IT IS STILL HERE. Through v2.4.1 the origin was a rest pose FITTED from
the corpus: the per-DOF median of the clips whose 8 anatomical channels all varied by less than
--var-thresh, whose mean body tilt was under --tilt-thresh, and which ran at least --min-frames
(duration was a criterion because variation is measured over time, so a clip too short for its
motion to develop cannot testify that it is at rest). That path is kept behind --baseline fitted,
because the comparison between a definitional origin and a fitted one is an ablation the write-up
needs to be able to reproduce from one command. It is no longer the production origin.

POPULATION. Fitting runs over the --prefix corpus only (mx_, 2446 dumps). The 8 nursing clips are
KB content, not calibration inputs: the calibration standard is Mixamo.

WHAT THIS DOES. Reads every corpus dump in <KB>/raw (no engine, no KB write), computes the
per-channel posture offsets against the origin, and reports the distribution per divisor group —
then proposes a POSTURE_DIVISOR per group, NEUTRAL at 0.30 x divisor, and threshold/ablation
context. Nothing is written into the knowledge base. Applying the result is a separate, deliberate
act: paste the emitted constants into config.py, bump metric_formula_version, then re-run
`ingest_corpus.py measure` and `recalibrate_measured.py`, and the golden test tracks the new values
automatically.

    python3 calibrate_posture.py
    python3 calibrate_posture.py --report              # also write the report under motionkb_build
    python3 calibrate_posture.py --baseline fitted     # the pre-v2.5.0 origin, as an ablation
"""
import argparse
import json
import math
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as C          # noqa: E402
import metrics              # noqa: E402
import paths                # noqa: E402

# Divisor groups, mirroring calibrate_divisors.py / metrics.channel_blocks: left/right share a
# divisor (an arm is an arm), root height and tilt are their own dimensions (normalised units,
# degrees).
GROUPS = {
    C.TORSO: (C.TORSO,),
    C.HEAD:  (C.HEAD,),
    "arm":   (C.LEFT_ARM, C.RIGHT_ARM),
    "leg":   (C.LEFT_LEG, C.RIGHT_LEG),
    "hand":  (C.LEFT_HAND, C.RIGHT_HAND),
    "root_height": ("root_height",),
    "root_tilt":   ("root_tilt",),
}
ANATOMICAL = [C.TORSO, C.HEAD, C.LEFT_ARM, C.RIGHT_ARM, C.LEFT_LEG, C.RIGHT_LEG,
              C.LEFT_HAND, C.RIGHT_HAND]
ALL_SIGNALS = ANATOMICAL + ["root_height", "root_tilt"]  # fit signals; labels use LABEL_KEYS
# The neutral/displaced threshold, as a fraction of each group's divisor — one constant in
# NORMALISED space, like STATIC_MUSCLE is one constant in muscle space (see config.NEUTRAL).
NEUTRAL_FRAC = 0.30
# Clips whose posture is known from inspection — printed as a sanity table so the fitted numbers can
# be read against ground truth (rest head on nurse_cpr_30, cradled head on mx_Agony_...).
CONTEXT_CLIPS = ["Idle", "nurse_cpr_30", "mx_Standing_Idle", "mx_Breathing_Idle", "mx_Arms_Raised",
                 "mx_Agony_Holding_The_Head", "mx_Armed_Villain_Holding_A_Hostage_From_Behind",
                 "mx_Legs_Crossed_Arms_Raised", "mx_Boxing_Idle", "mx_Crouch_Idle"]
# Threshold candidates for the neutral/displaced boundary, printed as context: muscle offsets are
# dimensionless, root height is normalised units, root tilt degrees.
MUSCLE_CANDS = [0.02, 0.04, 0.06, 0.08, 0.10, 0.15]
HEIGHT_CANDS = [0.02, 0.05, 0.10, 0.20, 0.30]
TILT_CANDS = [2.0, 5.0, 10.0, 20.0, 30.0]
# --baseline fitted only: the selection thresholds the sensitivity section perturbs the defaults
# toward, as (var_thresh, tilt_thresh, min_frames) — one row per criterion moved off its default.
SENSITIVITY_GRID = [(0.15, 7.0, 30), (0.25, 7.0, 30), (0.20, 5.0, 30), (0.20, 10.0, 30),
                    (0.20, 7.0, 15), (0.20, 7.0, 60), (0.20, 7.0, 90)]
# Unity's Humanoid reference pose, in the same three quantities a dump records. Not fitted, not
# sampled, not corpus-derived: muscle 0 is the centre of each DOF's HumanTrait range, and the
# reference bodyPosition/bodyRotation are what humanScale normalisation puts a T-posed rig at.
UNITY_REF_BODY_Y = 1.0
UNITY_REF_TILT_DEG = 0.0


def percentile(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def clip_stats(raw):
    """(mean pose, per-channel variation) for one dump — everything selection and fitting need.

    The pose carries the dump's frame count alongside the averages because the fitted-baseline
    ablation needs it: there, duration is a selection criterion, not a property of the pose itself.

    The mean pose is UNROUNDED so offsets computed from it are bit-identical to
    metrics.posture_offsets, which averages the frames itself; rounding happens once, on the
    baseline that gets pasted into config.py.
    """
    muscles = raw["muscles"]
    N = raw["frames"]
    M = len(muscles[0])
    s1 = [0.0] * M
    s2 = [0.0] * M
    for row in muscles:
        for i, v in enumerate(row):
            s1[i] += v
            s2[i] += v * v
    mean = [a / N for a in s1]
    sd = [math.sqrt(max(0.0, s2[i] / N - mean[i] ** 2)) for i in range(M)]
    var = {}
    for ch, idxs in metrics._channel_muscles(raw).items():
        var[ch] = math.sqrt(sum(sd[m] ** 2 for m in idxs) / len(idxs)) if idxs else 0.0
    pose = {
        "muscles": mean,
        "frames": N,
        "body_y": sum(p[1] for p in raw["body_pos"]) / N,
        "tilt_deg": sum(metrics._angle_deg(metrics._quat_up(q), (0.0, 1.0, 0.0))
                        for q in raw["body_rot"]) / N,
    }
    return pose, var


def unity_reference(n_muscles):
    """Unity's Humanoid reference pose — the production origin since v2.5.0.

    Every number here comes from the engine's definition of Humanoid space, not from the store:
    muscle 0 is the centre of each degree of freedom's HumanTrait range, and a T-posed rig
    normalises to bodyPosition.y 1.0 with bodyRotation identity. Nothing to fit, nothing to select.
    """
    return {"muscles": [0.0] * n_muscles,
            "body_y": UNITY_REF_BODY_Y,
            "tilt_deg": UNITY_REF_TILT_DEG}


def select_rest(stats, var_thresh, tilt_thresh, min_frames):
    """--baseline fitted only: the at-rest observations — long enough to testify, all 8 anatomical
    channels below var_thresh, AND upright. See the module docstring for why duration is one of the
    criteria, and why this whole path is now an ablation rather than the production origin."""
    return sorted(n for n, (pose, var) in stats.items()
                  if pose["frames"] >= min_frames
                  and max(var[c] for c in ANATOMICAL) < var_thresh
                  and pose["tilt_deg"] < tilt_thresh)


def median_baseline(stats, names):
    """Per-DOF median over the selected clips' mean poses, rounded to what config.py freezes."""
    poses = [stats[n][0] for n in names]
    M = len(poses[0]["muscles"])
    return {
        "muscles": [round(statistics.median(p["muscles"][i] for p in poses), 6) for i in range(M)],
        "body_y": round(statistics.median(p["body_y"] for p in poses), 6),
        "tilt_deg": round(statistics.median(p["tilt_deg"] for p in poses), 4),
    }


def offsets_from_mean(pose, base, chmap):
    """metrics.posture_offsets, computed from a cached mean pose instead of re-reading the dump."""
    bm = base["muscles"]
    out = {}
    for ch, idxs in chmap.items():
        offs = [pose["muscles"][m] - bm[m] for m in idxs]
        out[ch] = math.sqrt(sum(x * x for x in offs) / len(offs)) if offs else 0.0
    out["root_height"] = abs(pose["body_y"] - base["body_y"])
    out["root_tilt"] = abs(pose["tilt_deg"] - base["tilt_deg"])
    return out


def fit(stats, base, chmap, pctl, reads):
    """(per-clip offsets, per-signal distributions, divisor per group, neutral per group)."""
    offsets = {n: offsets_from_mean(pose, base, chmap) for n, (pose, _) in stats.items()}
    per_signal = {}
    for off in offsets.values():
        for k, v in off.items():
            per_signal.setdefault(k, []).append(v)
    divisor, neutral = {}, {}
    for g, members in GROUPS.items():
        xs = [x for m in members for x in per_signal.get(m, [])]
        pv = percentile(xs, pctl)
        divisor[g] = round(pv / reads, 4) if pv else None
        neutral[g] = round(divisor[g] * NEUTRAL_FRAC, 4) if divisor[g] else None
    return offsets, per_signal, divisor, neutral


def group_of(signal):
    for g, members in GROUPS.items():
        if signal in members:
            return g
    raise KeyError(signal)


LABEL_KEYS = ANATOMICAL + [C.ROOT]


def labels(offsets, neutral):
    """{clip: {channel: displaced?}} — the 9 posture labels a record carries. Root is ONE label,
    displaced when height OR tilt crosses its threshold, exactly as metrics.channel_blocks does."""
    out = {}
    for n, off in offsets.items():
        row = {c: off[c] >= neutral[group_of(c)] for c in ANATOMICAL}
        row[C.ROOT] = (off["root_height"] >= neutral["root_height"]
                       or off["root_tilt"] >= neutral["root_tilt"])
        out[n] = row
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prefix", default="mx_",
                    help="the calibration corpus: dumps whose name starts with this "
                         "(default: %(default)s; the nursing clips are content, not calibration)")
    ap.add_argument("--baseline", choices=("unity", "fitted"), default="unity",
                    help="posture origin: 'unity' is the production one — Unity's Humanoid "
                         "reference pose, definitional and parameter-free; 'fitted' reproduces the "
                         "pre-v2.5.0 origin (the corpus rest set's median) as an ablation "
                         "(default: %(default)s)")
    ap.add_argument("--var-thresh", type=float, default=0.20,
                    help="--baseline fitted: every anatomical channel's raw variation below this "
                         "(default: %(default)s)")
    ap.add_argument("--tilt-thresh", type=float, default=7.0,
                    help="--baseline fitted: mean body tilt below this many degrees "
                         "(default: %(default)s)")
    ap.add_argument("--min-frames", type=int, default=30,
                    help="--baseline fitted: at least this many frames, so the clip is long enough "
                         "for motion to show up if there is any (default: %(default)s = 1 s)")
    ap.add_argument("--percentile", type=float, default=99.0)
    ap.add_argument("--reads", type=float, default=0.85,
                    help="what the chosen percentile should normalise to (default: %(default)s)")
    ap.add_argument("--limit", type=int, default=0, help="dev: only the first N dumps")
    ap.add_argument("--report", action="store_true",
                    help="also write motionkb_build/reports/posture_calibration.md")
    args = ap.parse_args(argv)

    paths.require_kb()
    files = sorted(f for f in os.listdir(paths.RAW_DIR)
                   if f.endswith(".json") and f.startswith(args.prefix))
    if args.limit:
        files = files[:args.limit]
    print("reading %d corpus dumps (prefix %r) from %s ..."
          % (len(files), args.prefix, paths.rel(paths.RAW_DIR)))

    chmap = {}
    skipped = []

    def one(fname):
        name = fname[:-5]
        try:
            with open(os.path.join(paths.RAW_DIR, fname), encoding="utf-8") as f:
                raw = json.load(f)
            if not raw.get("muscles"):
                return (name, None, "no muscles (pre-2026-08-20 dump)")
            if not chmap:
                chmap.update(metrics._channel_muscles(raw))
            return (name, clip_stats(raw), None)
        except Exception as e:
            return (name, None, "%s: %s" % (type(e).__name__, e))

    with ThreadPoolExecutor(16) as pool:
        rows = list(pool.map(one, files))
    skipped = [(n, e) for n, _, e in rows if e]
    stats = {n: st for n, st, e in rows if not e}
    print("computed %d, skipped %d\n" % (len(stats), len(skipped)))
    for n, e in skipped[:10]:
        print("  skipped %s — %s" % (n, e))
    if not stats:
        raise SystemExit("no usable dumps")

    n_muscles = len(next(iter(stats.values()))[0]["muscles"])
    # The fitted origin is computed either way: as the production origin under --baseline fitted,
    # and as the ablation it is compared against under --baseline unity.
    rest = select_rest(stats, args.var_thresh, args.tilt_thresh, args.min_frames)
    if not rest:
        raise SystemExit("no clip passed the rest selection — thresholds too tight?")
    fitted_base = median_baseline(stats, rest)
    unity_base = unity_reference(n_muscles)
    base = fitted_base if args.baseline == "fitted" else unity_base

    offsets, per_signal, divisor, neutral = fit(stats, base, chmap, args.percentile, args.reads)
    primary_labels = labels(offsets, neutral)

    if args.baseline == "fitted":
        head = ("# posture calibration — %d dumps (prefix `%s`), ABLATION origin = median of %d "
                "rest clips (var < %g, tilt < %g deg, >= %d frames)"
                % (len(stats), args.prefix, len(rest), args.var_thresh, args.tilt_thresh,
                   args.min_frames))
    else:
        head = ("# posture calibration — %d dumps (prefix `%s`), origin = Unity's Humanoid "
                "reference pose" % (len(stats), args.prefix))
    lines = [head, ""]

    def emit(s=""):
        print(s)
        lines.append(s)

    if args.baseline == "fitted":
        emit("origin: median pose of %d selected rest clips (body_y %.4f, tilt %.2f deg) — fitted "
             "from the corpus by measurement alone. This is the pre-v2.5.0 origin, kept as an "
             "ablation; the store's production origin is Unity's reference pose."
             % (len(rest), fitted_base["body_y"], fitted_base["tilt_deg"]))
    else:
        emit("origin: Unity's Humanoid reference pose — all %d muscles at 0 (the centre of each "
             "DOF's HumanTrait range), bodyPosition.y %g, bodyRotation identity (tilt %g deg). "
             "Definitional: nothing here is fitted, selected or sampled, so the corpus decides "
             "scale only." % (n_muscles, UNITY_REF_BODY_Y, UNITY_REF_TILT_DEG))
    emit("")

    # ---- distribution + proposed divisor per group ----
    emit("%-12s %6s %8s %8s %8s %8s %8s   %s" %
         ("group", "n", "p50", "p90", "p95", "p99", "max", "divisor (p%g -> %g)" %
          (args.percentile, args.reads)))
    for g, members in GROUPS.items():
        xs = [x for m in members for x in per_signal.get(m, [])]
        emit("%-12s %6d %8.4f %8.4f %8.4f %8.4f %8.4f   %s" %
             (g, len(xs), percentile(xs, 50), percentile(xs, 90), percentile(xs, 95),
              percentile(xs, args.percentile), max(xs), divisor[g]))

    # ---- threshold context: what fraction of channel readings sit below each candidate ----
    emit("")
    emit("## neutral/displaced threshold context (fraction of readings below the candidate)")
    musc = []
    for ch in ANATOMICAL:
        musc.extend(per_signal.get(ch, []))
    for cands, xs, unit in ((MUSCLE_CANDS, musc, "muscle"),
                            (HEIGHT_CANDS, per_signal.get("root_height", []), "root_height"),
                            (TILT_CANDS, per_signal.get("root_tilt", []), "root_tilt deg")):
        emit("  %-14s " % unit +
             "  ".join("<%g: %.1f%%" % (c, 100.0 * sum(1 for x in xs if x < c) / len(xs))
                       for c in cands))

    # ---- static-threshold context: the quiet subset is the population STATIC_MUSCLE answers to ----
    emit("")
    emit("## variation inside the corpus's quiet subset (context for config.STATIC_MUSCLE = %g). "
         "The subset is the" % C.STATIC_MUSCLE)
    emit("## v2.4.1 rest selection (%d clips); since v2.5.0 it defines no origin, it is just a "
         "population of" % len(rest))
    emit("## clips that hold still, which is what a static threshold has to be read against.")
    rv = [stats[n][1][c] for n in rest for c in ANATOMICAL]
    emit("  %d channel readings: p50 %.4f  p90 %.4f  p95 %.4f  p99 %.4f  max %.4f"
         % (len(rv), percentile(rv, 50), percentile(rv, 90), percentile(rv, 95),
            percentile(rv, 99), max(rv)))
    below = 100.0 * sum(1 for x in rv if x < C.STATIC_MUSCLE) / len(rv)
    emit("  %.1f%% of those readings sit below STATIC_MUSCLE (a clip can hold still and still "
         "sway; static means it does not move at all)" % below)

    # ---- the payoff: held poses invisible to variation (static under STATIC_MUSCLE) ----
    emit("")
    emit("## static-variation channels whose posture offset exceeds the candidate (the holds the")
    emit("## variation signal cannot see) — channel readings / clips with at least one")
    for cand in MUSCLE_CANDS:
        n_ch, clips = 0, set()
        for name, (pose, var) in stats.items():
            for ch in ANATOMICAL:
                if var.get(ch, 1.0) < C.STATIC_MUSCLE and offsets[name].get(ch, 0.0) >= cand:
                    n_ch += 1
                    clips.add(name)
        emit("  >=%g: %5d channel readings in %4d clips" % (cand, n_ch, len(clips)))

    n_labels = len(stats) * len(LABEL_KEYS)
    if args.baseline == "fitted":
        # ---- sensitivity: how much does a FITTED origin move when its selection thresholds do ----
        emit("")
        emit("## sensitivity of the fitted origin to its selection thresholds — the reason this")
        emit("## origin could use round numbers, and the parameters v2.5.0 removed entirely")
        emit("%-28s %5s %12s %10s %10s %12s %s" %
             ("selection", "n", "maxDOFdelta", "d_body_y", "d_tilt", "div drift",
              "label flips (of %d)" % n_labels))
        for vt, tt, mf in SENSITIVITY_GRID:
            alt_rest = select_rest(stats, vt, tt, mf)
            if not alt_rest:
                emit("%-28s %5s" % ("var<%g tilt<%g frames>=%d" % (vt, tt, mf), "0"))
                continue
            alt_base = median_baseline(stats, alt_rest)
            _, _, alt_div, alt_neu = fit(stats, alt_base, chmap, args.percentile, args.reads)
            alt_labels = labels({n: offsets_from_mean(pose, alt_base, chmap)
                                 for n, (pose, _) in stats.items()}, alt_neu)
            max_dof = max(abs(a - b) for a, b in zip(alt_base["muscles"], base["muscles"]))
            drift = max(abs(alt_div[g] - divisor[g]) / divisor[g] for g in GROUPS if divisor[g])
            flips = sum(1 for n in stats for s in LABEL_KEYS
                        if alt_labels[n][s] != primary_labels[n][s])
            emit("%-28s %5d %12.4f %10.4f %10.3f %11.1f%% %8d (%.2f%%)" %
                 ("var<%g tilt<%g frames>=%d" % (vt, tt, mf), len(alt_rest), max_dof,
                  abs(alt_base["body_y"] - base["body_y"]),
                  abs(alt_base["tilt_deg"] - base["tilt_deg"]), 100.0 * drift,
                  flips, 100.0 * flips / n_labels))

        # ---- the rest set itself: what this origin was actually voted on ----
        emit("")
        emit("## the %d selected rest clips (the origin is their per-DOF median) — listed so the"
             % len(rest))
        emit("## selection can be checked by eye, not taken on the count alone")
        emit("%-56s %7s %9s %8s" % ("clip", "frames", "max var", "tilt"))
        for n in rest:
            pose, var = stats[n]
            emit("%-56s %7d %9.4f %7.2f"
                 % (n, pose["frames"], max(var[c] for c in ANATOMICAL), pose["tilt_deg"]))
    else:
        # ---- ablation: the fitted origin this one replaced ----
        emit("")
        emit("## ablation — the corpus-fitted origin used through v2.4.1, against this one. There")
        emit("## is no sensitivity table any more because the production origin has no parameters")
        emit("## to perturb: run --baseline fitted to reproduce that origin and its own analysis.")
        _, _, alt_div, alt_neu = fit(stats, fitted_base, chmap, args.percentile, args.reads)
        alt_labels = labels({n: offsets_from_mean(pose, fitted_base, chmap)
                             for n, (pose, _) in stats.items()}, alt_neu)
        max_dof = max(abs(v) for v in fitted_base["muscles"])
        drift = max(abs(alt_div[g] - divisor[g]) / divisor[g] for g in GROUPS if divisor[g])
        flips = sum(1 for n in stats for s in LABEL_KEYS
                    if alt_labels[n][s] != primary_labels[n][s])
        emit("  fitted origin: median of %d rest clips (var<%g, tilt<%g deg, >=%d frames)"
             % (len(rest), args.var_thresh, args.tilt_thresh, args.min_frames))
        emit("  distance from Unity's reference: max |DOF| %.4f, rms %.4f, d_body_y %.4f, "
             "d_tilt %.3f deg"
             % (max_dof,
                math.sqrt(sum(v * v for v in fitted_base["muscles"]) / n_muscles),
                abs(fitted_base["body_y"] - UNITY_REF_BODY_Y),
                abs(fitted_base["tilt_deg"] - UNITY_REF_TILT_DEG)))
        emit("  divisor drift %.1f%%, %d of %d posture labels differ (%.2f%%)"
             % (100.0 * drift, flips, n_labels, 100.0 * flips / n_labels))
        emit("  how much of a reading is signal rather than a common pedestal (sd/mean per group):")
        _, alt_per, _, _ = fit(stats, fitted_base, chmap, args.percentile, args.reads)
        emit("  %-12s %10s %10s" % ("group", "unity", "fitted"))
        for g, members in GROUPS.items():
            if g.startswith("root"):
                continue
            a = [x for m in members for x in per_signal.get(m, [])]
            b = [x for m in members for x in alt_per.get(m, [])]
            emit("  %-12s %10.3f %10.3f"
                 % (g, statistics.pstdev(a) / statistics.fmean(a),
                    statistics.pstdev(b) / statistics.fmean(b)))

    # ---- known-clip sanity table ----
    emit("")
    emit("## context clips (posture offset per channel; * = variation-static)")
    emit("%-48s %s" % ("clip", "  ".join("%9s" % c[:9] for c in ANATOMICAL + ["root_height", "root_tilt"])))
    for n in CONTEXT_CLIPS:
        p = os.path.join(paths.RAW_DIR, n + ".json")
        if not os.path.exists(p):
            emit("%-48s (no dump)" % n)
            continue
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        po = metrics.posture_offsets(raw, base)
        pose, va = clip_stats(raw)
        cells = []
        for c in ANATOMICAL:
            cells.append("%8.4f%s" % (po.get(c, 0.0), "*" if va.get(c, 1.0) < C.STATIC_MUSCLE else " "))
        cells.append("%9.4f" % po["root_height"])
        cells.append("%9.2f" % po["root_tilt"])
        emit("%-48s %s" % (n, " ".join(cells)))

    # ---- paste-ready constants ----
    emit("")
    emit("## paste into config.py")
    if args.baseline == "fitted":
        emit("REFERENCE_POSE = {   # ABLATION ONLY — not the store's origin since v2.5.0")
        ms = fitted_base["muscles"]
        emit("    # Median mean-pose of the %d corpus clips selected as at-rest by measurement" % len(rest))
        emit("    # alone (>= %d frames, every anatomical channel's raw variation < %g, mean body"
             % (args.min_frames, args.var_thresh))
        emit("    # tilt < %g deg — no name matching)." % args.tilt_thresh)
        emit('    "muscles": [')
        for i in range(0, len(ms), 8):
            emit("        " + ", ".join("%g" % v for v in ms[i:i + 8]) + ",")
        emit("    ],")
        emit('    "body_y": %g,' % fitted_base["body_y"])
        emit('    "tilt_deg": %g,' % fitted_base["tilt_deg"])
        emit("}")
    else:
        emit("# REFERENCE_POSE is not emitted here: it is Unity's, not this script's. All %d"
             % n_muscles)
        emit("# muscles at 0, body_y %g, tilt_deg %g — see config.REFERENCE_POSE."
             % (UNITY_REF_BODY_Y, UNITY_REF_TILT_DEG))
    emit("POSTURE_DIVISOR = {")
    for g, dv in divisor.items():
        key = ("C.TORSO" if g == C.TORSO else "C.HEAD" if g == C.HEAD else '"%s"' % g)
        emit("    %s: %s,   # corpus p%g %.4f -> %g" %
             (key, dv, args.percentile,
              percentile([x for m in GROUPS[g] for x in per_signal.get(m, [])], args.percentile),
              args.reads))
    emit("}")
    emit("NEUTRAL = {")
    for g, nv in neutral.items():
        key = ("C.TORSO" if g == C.TORSO else "C.HEAD" if g == C.HEAD else '"%s"' % g)
        emit("    %s: %s,   # %g x divisor" % (key, nv, NEUTRAL_FRAC))
    emit("}")

    if args.report:
        name = ("posture_calibration.md" if args.baseline == "unity"
                else "posture_calibration_fitted_ablation.md")
        out = os.path.join(paths.REPORTS_DIR, name)
        paths.write_text(out, "\n".join(lines) + "\n")
        print("\n-> %s" % paths.rel(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
