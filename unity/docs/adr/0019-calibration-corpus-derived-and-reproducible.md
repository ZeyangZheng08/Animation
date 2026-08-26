# 0019 — Calibration is corpus-derived, project-free, and offline-reproducible

Status: Accepted (2026-08-22). Refits every calibration constant on the full frozen Mixamo corpus,
replaces the project-clip rest baseline with one selected from the corpus by measurement alone, and
bumps the metric formula v2.3.0 → v2.4.0. All 2454 records re-measured from their frozen `raw`
dumps; no semantic field, no raw dump, and no `state_label` changed — the raw signals and the
static thresholds are untouched, so what moved is magnitudes and the posture half only.

> **Superseded in part by [0021](0021-kinematic-facts-not-classifications.md) (2026-08-25).**
> Everything here about the VARIATION half stands: corpus-only population, offline reproducibility
> from frozen `raw/`, and the p99 → 0.85 divisor rule. What does not is the posture half — `REST_POSE`,
> `POSTURE_DIVISOR` and `NEUTRAL` are deleted along with the triple they scaled, and
> `calibrate_posture.py` with them. Formula v3.0.0 stores the mean pose itself, so there is no
> origin-relative scale left to fit.

## Context

Two constraints govern how the KB is built, and both were being violated by leftovers from earlier
formula versions:

1. **Unity-Humanoid unified.** Every number is measured in Unity's normalised Humanoid space
   (`HumanPose.muscles` + `bodyPosition`/`bodyRotation`), independent of the sampled avatar
   (ADR 0011, re-verified across three rigs on 2026-08-22). The eight nursing clips are KB
   *content*; nothing about how the KB measures may depend on them or on any other project asset.
2. **Reproducible.** Every constant must be a pure function of the frozen dumps in `raw/` plus a
   stated rule, re-derivable offline by a script in the repo — no engine, no hand-picked inputs, no
   name matching.

What violated them:

- **`DIVISOR` was fitted on a 150-clip random sample** (ADR 0010), a cost compromise from when each
  calibration clip was a live Unity round trip. With every dump frozen on disk the sample had no
  reason to exist — and it was measurably wrong: p99 estimated from 150 draws is the ~1.5th largest
  value, and against the full 2446-dump population the sample sat 41% low on `root_vert`, 27% low
  on `root_trans`, 15% high on `head`. 1.9% of root readings saturated at 1.0.
- **`REST_POSE` was the accepted `idle`** — one project clip of unknown provenance, and an outlier:
  its left-leg stance alone made ~1000 corpus leg channels read `displaced` that a Mixamo rest
  cluster calls `neutral` (swapping baselines flips 2968 readings displaced→neutral against 182 the
  other way — the bias is one-directional). `POSTURE_DIVISOR` was additionally fitted over all 2454
  dumps, mixing the 8 nursing clips into the calibration population.
- **`STATIC_MUSCLE` was justified by the project `idle`'s own readings** (0.0047 torso, 0.0109
  busiest channel).
- The root posture field was named `body_height_offset_m` — the `_m` claims metres, and the value
  is in normalised humanoid units (ADR 0011).

## Decision

**Population.** Every constant is fitted over the 2446 `mx_*` dumps only. The 8 nursing clips stay
in the KB as content but contribute nothing to calibration — corpus membership is the `mx_` clip
prefix, not a name list.

**`DIVISOR`** (variation): refit over the full population, same rule — the corpus p99 of the raw
signal normalises to 0.85. `calibrate_divisors.py --reuse <KB>/raw --prefix mx_`, offline.
Saturation falls to 0.1–0.6% per group.

| group | v2.3.0 | v2.4.0 | shift |
|---|---|---|---|
| torso | 0.3174 | 0.2969 | −6.5% |
| head | 0.5809 | 0.4963 | −14.6% |
| arm | 0.6914 | 0.6864 | −0.7% |
| leg | 0.4296 | 0.4468 | +4.0% |
| hand | 0.7327 | 0.7546 | +3.0% |
| root_trans | 1.5637 | 1.9782 | +26.5% |
| root_vert | 1.3009 | 1.8396 | +41.4% |
| root_heading | 142.1 | 131.86 | −7.2% |

**`REST_POSE`**: the per-DOF **median** of the mean poses of the 265 corpus clips that *measure* as
at-rest — every anatomical channel's raw variation < 0.20 and mean body tilt < 7°. Selection is by
measurement alone: no name matching, no project clip, reproducible from `raw/` by
`calibrate_posture.py`. The median makes the baseline robust to stragglers in the selection, and
its sensitivity to the two thresholds is *measured, not asserted* — the script's sensitivity
section reruns the whole derivation at var < 0.15/0.25 and tilt < 5°/10°:

| selection | n | Δbody_y | Δtilt | divisor drift | posture labels flipped |
|---|---|---|---|---|---|
| var<0.15 tilt<7° | 160 | 0.0025 | 0.11° | 2.7% | 2.39% |
| var<0.25 tilt<7° | 356 | 0.0008 | 0.27° | 0.8% | 1.31% |
| var<0.20 tilt<5° | 191 | 0.0034 | 0.63° | 1.6% | 2.37% |
| var<0.20 tilt<10° | 343 | 0.0032 | 0.74° | 1.6% | 2.09% |

Rest-vs-moving separation (max anatomical posture offset of known rest clips vs known holds) is
3.0×, against 1.7× for the naive "average everything named Idle" alternative — 265 Idle-named clips
include seated, crouched and falling idles, and averaging them yields a pose 18.8° tilted that is
nobody's rest.

**`POSTURE_DIVISOR` / `NEUTRAL`**: refit against the new baseline over the same 2446, same rules
(p99 → 0.85; neutral at 0.30 × divisor). Ground-truth anchors all survive: `mx_Arms_Raised`'s
raised-and-held arms 0.88–0.89 (displaced, variation-static), `mx_Boxing_Idle`'s guard fists 0.99,
`mx_Crouch_Idle`'s legs 0.65–0.67 and root height 0.39, `mx_Agony_Holding_The_Head`'s cradled head
0.3678 vs threshold 0.2274 — while `mx_Standing_Idle` reads neutral everywhere and the project
`Idle`'s legs (0.15–0.23) become neutral, which is the ~1000-false-positives defect fixed.

**`STATIC_MUSCLE` stays 0.02, re-justified against the corpus.** The corpus's channel readings are
continuous through [0.01, 0.03] — no threshold there is "natural", so 0.02 is a convention with
measured margins: it sits 20× above the frozen-pose population (2393 of 19568 readings ≤ 0.001,
990 exactly 0.0) and ~10× below the moving median (~0.19), and moving it ±50% relabels only
1.3%/3.1% of readings. Since neither the raw signals nor any static threshold changed, **every
`state_label` in the store is bit-identical across the bump** — verified by re-measurement.

**Field rename**: `body_height_offset_m` → `body_height_offset` (root `posture_measurement`). The
schema's `rawMeasurement` vocabulary drops the three dead v2.0.0 names (`gait_foot_yrange_m`,
`trans_horiz_stddev_m`, `heading_signed_stddev_deg` — 0 records carried them) and names the five
fields the extractor actually emits.

## Consequences

- **Calibration inputs and KB content are now disjoint by construction.** Adding or re-authoring a
  nursing clip cannot move any number; refreshing the corpus is a re-run of two offline scripts.
- **The whole calibration is one reproducible pipeline**:
  `calibrate_divisors.py --reuse raw/ --prefix mx_` → paste → `calibrate_posture.py` → paste →
  `ingest_corpus.py measure` + `recalibrate_measured.py`. No step needs Unity; the golden test
  re-derives the accepted records' MEASURED from `raw/` and tracks the result.
- **Magnitudes are not comparable across the bump** (root magnitudes drop ~30%, posture magnitudes
  shift with the new baseline); anything that cached v2.3.0 numbers must re-read. `state_label`s
  are unchanged.
- The thresholds 0.20 / 7° / 0.30-of-scale / 0.02 remain conventions — but each now carries a
  measured sensitivity or margin in `config.py`, which is what makes a convention defensible.

## Amendment — 2026-08-24, formula v2.4.1: duration is a selection criterion

The rest selection above admits any clip whose eight anatomical channels stay under 0.20 and whose
mean tilt stays under 7°. Auditing the selected set by name — which the decision above never did,
because it only reported the count — showed two kinds of member that cannot testify about rest:

- **Clips too short for their own motion to appear.** Variation is measured *over time*, so a clip
  that ends before its motion develops reads low on every channel. `mx_Female_Run_Backward` is 17
  frames and reads 0.1400–0.1766 on all four limbs — `dynamic` on its own record — yet cleared the
  0.20 ceiling and voted on the rest pose. So did `mx_Hit_Reaction` (13 frames, left arm 0.1560)
  and `mx_High_Right_Boxing_Pivot` (29 frames, right leg 0.1261).
- **Single-pose assets.** 46 of the 265 were 2-frame authored poses — `mx_Arms_Behind_Back`,
  `mx_Knees_Up_Holding_Object`, `mx_Leaping_Forward_Arms_Extended_Backwards`. These are poses a
  human posed, not observations of a person standing at rest, and several are not rest poses at
  all.

**Decision.** A rest observation must also run at least `--min-frames` = 30 (1 s at 30 fps). The
criterion is still a measured quantity, and it states the condition under which the variation
signal means anything: a clip must be long enough for movement to show up if there is any.

**Cost, measured.** The set goes 265 → 205; `body_y` does not move at all (0.955386), tilt moves
3.7298° → 3.4728°, no muscle DOF moves more than 0.0923, `POSTURE_DIVISOR` drifts ≤ 1%, and 1.60%
of the store's posture labels change. Variation and every static threshold are untouched, so
`state_label` is again bit-identical across the bump.

**The floor is itself perturbed in the sensitivity section**, alongside the two thresholds — the
grid is now seven rows, `(var, tilt, min_frames)`, and no row moves more than 2.30% of labels:

| selection | n | Δbody_y | Δtilt | divisor drift | posture labels flipped |
|---|---|---|---|---|---|
| var<0.15 tilt<7° f≥30 | 113 | 0.0023 | 0.27° | 2.5% | 2.23% |
| var<0.25 tilt<7° f≥30 | 283 | 0.0005 | 0.31° | 0.7% | 1.32% |
| var<0.20 tilt<5° f≥30 | 156 | 0.0034 | 0.39° | 1.8% | 1.44% |
| var<0.20 tilt<10° f≥30 | 264 | 0.0030 | 0.66° | 1.6% | 1.69% |
| var<0.20 tilt<7° f≥15 | 228 | 0.0012 | 0.09° | 0.4% | 0.97% |
| var<0.20 tilt<7° f≥60 | 120 | 0.0034 | 0.02° | 2.2% | 1.44% |
| var<0.20 tilt<7° f≥90 | 72 | 0.0023 | 0.30° | 2.4% | 2.30% |

**`posture_calibration.md` now lists the 205 selected clips by name**, with frame count, max
channel variation and mean tilt. The count alone was what let the defect stand; a roster is what
lets the selection be checked rather than trusted — by a reader, and by whoever re-runs the fit on
a different corpus.
