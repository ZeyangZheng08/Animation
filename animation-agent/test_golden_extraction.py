#!/usr/bin/env python3
"""
test_golden_extraction.py — golden re-extraction regression for the MotionKB KINEMATIC pipeline
(HANDOFF.md §8 module E-now; ADR 0007). Stdlib only — no pip, no Unity.

The KINEMATIC block in the accepted store was produced by `metrics.channel_blocks` over the saved
per-frame pose dumps in `agent/animation_knowledge_base/raw/<id>.json`. This test RE-RUNS that exact
computation from the same frozen `raw` dumps and asserts the result still reproduces the KINEMATIC
fields (`kind`, `state_label`, `motion_magnitude`, `raw_measurement`, `mean_pose`, and the root's
`mean_body_height` / `mean_body_tilt_deg`) in each accepted `<id>.json`.

It is the regression guard for `metrics.py` + `config.py` (divisors/threshold/bone-map): any drift in
a formula or a constant that is not a deliberate `metric_formula_version` bump will flip this red. The
SEMANTIC 5-tuple is human-owned (ADR 0002) and intentionally NOT checked here.

Usage:  python test_golden_extraction.py
Exit:   0 if every accepted file's KINEMATIC reproduces from raw/; non-zero otherwise (per-file isolated).
"""
import sys, os, glob, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # config, metrics, paths, unity_sampler
import config as C            # noqa: E402
import metrics               # noqa: E402
import paths                 # noqa: E402
import unity_sampler         # noqa: E402

KB_DIR = paths.KB_DIR                                            # see paths.py / MOTIONKB_DIR

# Every key the KINEMATIC half can carry. `mean_pose` is the anatomical channels', the two carriage
# means are the root's, so a key missing from BOTH the record and the recomputation is not a
# difference — a key missing from only one is.
KINEMATIC_KEYS = ("kind", "state_label", "motion_magnitude", "raw_measurement", "mean_pose",
                  "mean_body_height", "mean_body_tilt_deg")
EPS = 1e-9


def _num_eq(a, b):
    return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= EPS


def _cmp(path, expected, got, errors):
    if isinstance(expected, dict):
        for k in set(expected) | set(got if isinstance(got, dict) else {}):
            if not isinstance(got, dict) or k not in got:
                errors.append(f"{path}.{k}: missing in recomputed"); continue
            if k not in expected:
                errors.append(f"{path}.{k}: extra in recomputed"); continue
            _cmp(f"{path}.{k}", expected[k], got[k], errors)
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if not _num_eq(expected, got):
            errors.append(f"{path}: accepted={expected!r} != recomputed={got!r}")
    elif expected != got:
        errors.append(f"{path}: accepted={expected!r} != recomputed={got!r}")


def accepted_files():
    return paths.accepted_files()


def main():
    paths.require_kb()
    files = accepted_files()
    if not files:
        print("FATAL: no accepted MotionKB files found"); return 1
    passed = failed = 0
    print(f"golden re-extraction regression — {len(files)} accepted file(s)\n")
    for f in files:
        short = paths.rel(f)
        try:
            doc = json.load(open(f, encoding="utf-8"))
            clip = doc["source_clip"]["clip_name"]               # MEASURE pipeline keys raw by clip name
            raw = unity_sampler.read_raw(clip)                   # frozen sampled poses (keyed by clip name)
            recomputed = metrics.channel_blocks(raw)             # re-run the REAL kinematic pipeline
            errors = []
            for ch in C.STATE_CHANNELS:
                acc = doc["channels"][ch]
                rec = recomputed[ch]
                for k in KINEMATIC_KEYS:
                    if k not in acc and k not in rec:
                        continue
                    _cmp(f"channels.{ch}.{k}", acc.get(k), rec.get(k), errors)
        except FileNotFoundError as e:
            print(f"  FAIL  {short}\n          - missing raw dump: {e}"); failed += 1; continue
        except Exception as e:
            print(f"  FAIL  {short}\n          - {type(e).__name__}: {e}"); failed += 1; continue
        if errors:
            failed += 1
            print(f"  FAIL  {short}")
            for e in errors[:8]:
                print(f"          - {e}")
        else:
            passed += 1
            print(f"  PASS  {short}")
    print(f"\n{passed} passed / {failed} failed"
          + ("" if failed else "  (KINEMATIC reproduces from frozen raw via metrics.py — pipeline stable)"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
