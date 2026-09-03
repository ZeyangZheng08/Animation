#!/usr/bin/env python3
"""
calibrate_divisors.py — fit config.DIVISOR from a sample of the corpus, without touching the KB.

WHY. Every divisor in config.DIVISOR was set so that one chosen nurse clip reads 0.85, and the nurse
clips are small-amplitude bedside actions. Measured against the Mixamo corpus that calibration is
already exhausted: an ordinary `mx_Female_Walk_Forward` reads 1.0 on both legs where the KB's own
`walking` (an in-place walk) reads 0.85, and across a first sample of 7 corpus clips 11 of 56
anatomical channel readings clamped to exactly 1.0. A field that saturates is a field that cannot
discriminate, which is the opposite of what retrieval and arbitration need from it.

WHAT THIS DOES. Samples N clips over the Unity MCP bridge, runs metrics.compute_raw_signals on each
dump, and reports the distribution of the RAW physical signal per divisor group — then proposes a
divisor per group at a chosen percentile. Nothing is written into the knowledge base: no register,
no record, no raw/*.json. Dumps live in a scratch directory and the report is printed.

Applying the result is a separate, deliberate act: it bumps metric_formula_version, rewrites the
KINEMATIC block of every accepted record, and invalidates the frozen values in
test_golden_extraction.py.

    python3 calibrate_divisors.py --limit 150
    python3 calibrate_divisors.py --limit 150 --percentile 99 --json out.json

The canonical v2.4.0 fit needs no engine at all: every dump is already frozen in raw/, so
    python3 calibrate_divisors.py --reuse <KB>/raw --prefix mx_
fits over the full Mixamo corpus (2446 dumps) and excludes the 8 nursing clips — KB content is
one thing, the calibration population is another, and the calibration population is Mixamo only.
"""
import argparse
import json
import os
import random
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as C          # noqa: E402
import metrics              # noqa: E402
import unity_sampler        # noqa: E402

# Which raw signal feeds which divisor. Mirrors metrics.channel_blocks; kept explicit so a change
# there shows up here as a KeyError rather than as a silently mis-fitted divisor.
GROUPS = {
    C.TORSO:   ("torso",),
    C.HEAD:    ("head",),
    "arm":     ("left_arm", "right_arm"),
    "leg":     ("left_leg", "right_leg"),
    "hand":    ("left_hand", "right_hand"),
}
# root_gait is gone: foot lift was a metre-space proxy for "is this locomotion", and that question
# now belongs to the leg channels. The root channel answers where the BODY went, which is what
# HumanPose.bodyPosition/bodyRotation measure -- and in an avatar-independent way.
ROOT_GROUPS = {
    "root_trans":   "_root_trans",
    "root_vert":    "_root_vert",
    "root_heading": "_root_heading",
}


def percentile(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def corpus_clip_names(catalog_dir, prefix, limit, seed):
    """Clip names straight off disk — the corpus FBX file stems, which the postprocessor made equal
    to the clip name. No Unity round trip and no KB read needed to build the list."""
    names = sorted(os.path.splitext(f)[0] for f in os.listdir(catalog_dir)
                   if f.lower().endswith(".fbx") and f.startswith(prefix))
    if limit and limit < len(names):
        random.Random(seed).shuffle(names)
        names = sorted(names[:limit])
    return names


def sample_one(clip_name, host, port, instance, scratch):
    """guid+file_id by name, then one sampler call. Returns the parsed dump or raises."""
    ok, found, _ = unity_sampler.run_csharp_over_http(
        unity_sampler.build_find_clip_csharp(clip_name), host=host, port=port, instance=instance)
    if not ok:
        raise RuntimeError("find failed: %s" % found[:120])
    rows = [r for r in (found or "").strip().splitlines() if "|" in r]
    if len(rows) != 1:
        raise RuntimeError("%d matches for %r" % (len(rows), clip_name))
    path, guid, fid = rows[0].split("|")
    ok, dump, _ = unity_sampler.run_csharp_over_http(
        unity_sampler.build_sampler_csharp({"id": clip_name, "guid": guid, "file_id": int(fid)}),
        host=host, port=port, instance=instance)
    if not ok or dump.startswith("ERROR:"):
        raise RuntimeError("sample failed: %s" % dump[:120])
    scratch_file = os.path.join(scratch, clip_name + ".json")
    with open(scratch_file, "w", encoding="utf-8") as fh:
        fh.write(dump)
    return json.loads(dump)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default="/mnt/d/Research/AI_agent/Animation_agent/Animation"
                                        "/Assets/Animations/Mixamo30")
    ap.add_argument("--prefix", default="mx_")
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--seed", type=int, default=0, help="fixed so the sample is reproducible")
    ap.add_argument("--percentile", type=float, default=99.0,
                    help="the raw value that should read 0.85 after division (default: %(default)s)")
    ap.add_argument("--reads", type=float, default=0.85,
                    help="what the chosen percentile should normalize to (default: %(default)s)")
    ap.add_argument("--host", default=unity_sampler.DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=unity_sampler.DEFAULT_PORT)
    ap.add_argument("--instance")
    ap.add_argument("--scratch", help="where the per-clip dumps go. Default is a temp dir, but WSL "
                                      "clears /tmp when the distro restarts and a half-finished "
                                      "sampling run is worth keeping -- point this somewhere durable "
                                      "for a long run.")
    ap.add_argument("--reuse", help="compute from dumps already in this directory; no Unity calls. "
                                    "A sampling run that is interrupted leaves its dumps behind, and "
                                    "the fit does not need the engine once they exist.")
    ap.add_argument("--json", help="write the fitted divisors and the distribution here")
    args = ap.parse_args(argv)

    reuse = bool(args.reuse)
    if reuse:
        scratch = args.reuse
        names = sorted(os.path.splitext(f)[0] for f in os.listdir(scratch)
                       if f.endswith(".json") and f.startswith(args.prefix))
        print("reusing %d dumps from %s (prefix %r, no Unity calls)"
              % (len(names), scratch, args.prefix))
    else:
        if not unity_sampler.bridge_healthy(args.host, args.port):
            raise SystemExit("Unity MCP bridge not reachable at %s:%d" % (args.host, args.port))
        names = corpus_clip_names(args.corpus, args.prefix, args.limit, args.seed)
        print("sampling %d of the corpus (seed %d) ..." % (len(names), args.seed))
        if args.scratch:
            scratch = args.scratch
            if not os.path.isdir(scratch):
                os.makedirs(scratch)
        else:
            scratch = tempfile.mkdtemp(prefix="divisor_calib_")
        # Already-sampled clips are skipped, so an interrupted run resumes by re-issuing the command.
        done = set(os.path.splitext(f)[0] for f in os.listdir(scratch) if f.endswith(".json"))
        if done:
            names = [n for n in names if n not in done]
            print("%d already sampled in %s; %d left" % (len(done), scratch, len(names)))
        print("scratch dumps: %s   (nothing is written into the KB)" % scratch)

    raws = {g: [] for g in GROUPS}
    raws.update({g: [] for g in ROOT_GROUPS})
    ok = failed = 0
    for i, name in enumerate(names, 1):
        try:
            if reuse:
                with open(os.path.join(scratch, name + ".json"), encoding="utf-8") as fh:
                    dump = json.load(fh)
            else:
                dump = sample_one(name, args.host, args.port, args.instance, scratch)
            sig = metrics.compute_raw_signals(dump)
        except Exception as e:
            failed += 1
            print("  [%d/%d] %-52s FAILED %s" % (i, len(names), name[:52], str(e)[:60]))
            continue
        ok += 1
        for group, channels in GROUPS.items():
            for ch in channels:
                v = sig.get(ch)
                if isinstance(v, (int, float)):
                    raws[group].append(float(v))
        for group, key in ROOT_GROUPS.items():
            v = sig.get(key)
            if isinstance(v, (int, float)):
                raws[group].append(float(v))
        if i % 10 == 0 or i == len(names):
            print("  [%d/%d] %d ok / %d failed" % (i, len(names), ok, failed))

    print("\n%-14s %6s %9s %9s %9s %9s %9s | %9s %9s %7s" %
          ("group", "n", "p50", "p90", "p99", "max", "current", "fitted", "sat.now", "sat.new"))
    out = {"sample_size": ok, "percentile": args.percentile, "reads": args.reads, "groups": {}}
    for group in list(GROUPS) + list(ROOT_GROUPS):
        xs = raws[group]
        if not xs:
            continue
        # A group being fitted for the first time has no divisor yet; report it rather than crash.
        cur = C.DIVISOR.get(group)
        pv = percentile(xs, args.percentile)
        fitted = (pv / args.reads) if pv else (cur or 0.0)
        sat_now = (sum(1 for x in xs if x / cur >= 1.0) * 100.0 / len(xs)) if cur else float("nan")
        sat_new = sum(1 for x in xs if x / fitted >= 1.0) * 100.0 / len(xs)
        print("%-14s %6d %9.4f %9.4f %9.4f %9.4f %9.4f | %9.4f %8.1f%% %6.1f%%" %
              (group, len(xs), percentile(xs, 50), percentile(xs, 90), pv, max(xs),
               cur if cur else float("nan"), fitted, sat_now, sat_new))
        out["groups"][group] = {"n": len(xs), "current_divisor": cur, "fitted_divisor": round(fitted, 4),
                                "p50": percentile(xs, 50), "p90": percentile(xs, 90),
                                "p99": percentile(xs, 99), "max": max(xs),
                                "saturation_now_pct": round(sat_now, 2),
                                "saturation_fitted_pct": round(sat_new, 2)}
    print("\n%d clips sampled, %d failed. Nothing written to the KB." % (ok, failed))
    print("Applying these means: bump metric_formula_version, re-extract every accepted record,")
    print("re-freeze test_golden_extraction.py, and record the change in a new ADR.")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)
        print("wrote %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
