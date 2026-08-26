# 0020 — The posture origin is Unity's, not the corpus's

Status: Accepted (2026-08-25). Supersedes the `REST_POSE` half of
[0019](0019-calibration-corpus-derived-and-reproducible.md) and its 2026-08-24 amendment; the rest
of 0019 (corpus-only population, offline reproducibility, the divisor rule, the `_m` rename) stands
unchanged. Formula v2.4.1 → v2.5.0, all 2454 records re-measured from their frozen `raw` dumps.

> **Superseded by [0021](0021-kinematic-facts-not-classifications.md) (2026-08-25), which retires the
> question rather than answering it a fourth time.** The cost recorded below — a relaxed stance
> reading `displaced` while a raised guard reads `neutral`, and discrimination falling on every group
> — is what a distance from any fixed origin costs, and formula v3.0.0 stops taking one: `mean_pose`
> stores each channel's mean pose as a vector and the store classifies nothing. What survives from
> here is the measurement of what Unity's Humanoid actually defines (muscle 0 is the centre of a DOF's
> range; the reference pose normalises to `bodyPosition.y` 1.0 at 0° tilt across every rig) — that is
> the coordinate system every KINEMATIC number still lives in. It is an origin, not a rest pose.

## Context

The posture half needs an origin: `posture_magnitude` is how far a channel's mean pose sits from
one. Where that origin came from has moved twice, each time to remove an arbitrary choice.

- **v2.3.0** took the project's accepted `idle` — one clip, one body, one person's stance. It was
  an outlier: its left-leg stance alone made ~1000 corpus leg channels read `displaced`.
- **v2.4.0/v2.4.1** replaced it with a pose *fitted from the corpus*: the per-DOF median of the 205
  clips that measure as at-rest (variation < 0.20 on all eight anatomical channels, mean tilt < 7°,
  ≥ 30 frames). No name matching, no project asset, reproducible from `raw/`.

The fitted origin was defensible on its own terms and was measured as such: perturbing its three
selection thresholds moved ≤ 2.30% of labels; split-half — the 100 locomotion clips in the set
against the 105 that are not — reproduced the pooled origin to within 3.61% and 3.92%; 200
bootstrap resamples of the 205 moved a median of 1.30% of labels.

But it was still an **estimate with parameters**, and it estimated something that has no single
correct value: *there is no one relaxed stance*. Every idle differs — seated, crouched, weight on
one leg, arms folded — so any set of "quiet" clips defines a slightly different rest pose, and the
number that comes out is a property of which clips were pooled. Sensitivity analysis bounds that
dependence; it does not remove it.

Unity's Humanoid does define a pose, exactly, and it is the space every number in MEASURED already
lives in ([0011](0011-measure-in-unity-humanoid-muscle-space.md)).

**What Unity actually defines**, measured in-engine on 2026-08-25:

- `HumanPose.muscles` are normalised to [−1, 1] over each degree of freedom's `HumanTrait` range,
  and **0 is that range's centre**. It ships with the engine, it is the same on every rig, and it
  has no estimation error.
- The Humanoid reference pose normalises to **`bodyPosition.y` = 1.0, `bodyRotation` = identity**.
  Verified across the project's rigs: X Bot 1.000000, Y Bot 0.999999, Fat_man 0.999963,
  patient_avatar 0.999963 — every one at tilt 0.0000°, across body shapes as different as X Bot and
  Fat_man, because `humanScale` puts the reference hips at 1.0 by construction. `nurse_avatar`
  reads 0.984676 / 1.51° because its imported bind pose is not a clean T-pose; that is a property
  of the asset, not of the standard.
- **The T-pose is not muscle zero.** A rig's bind pose reads rms 0.4395 (max 1.0286) on
  `nurse_avatar` and rms 0.5330 (max 1.3285) on X Bot. The T-pose is a *mapping requirement* for
  Avatar configuration, per-model; muscle zero is an abstract centre nobody is posed in.
- **Unity defines no idle.** There is no relaxed-stance pose anywhere in the Humanoid spec. Sample
  content that ships with other packages is content, exactly as arbitrary as a Mixamo idle.

## Decision

**Unity decides the coordinates and the zero. The corpus decides only how much counts as a lot.**

`REST_POSE` is deleted. `config.REFERENCE_POSE` is Unity's reference pose — all 95 muscles at 0,
`body_y` 1.0, `tilt_deg` 0.0 — and nothing derives, fits or samples it. Idle clips are ordinary
content and take no part in defining the standard.

Unchanged: the raw dump format (per-frame 95 muscles + `bodyPosition` + `bodyRotation`), the
variation signal and its divisors, `STATIC_MUSCLE` = 0.02, and the whole SEMANTIC half.

`POSTURE_DIVISOR` and `NEUTRAL` are refit against the new origin over the same 2446 `mx_` dumps
with the same rules (corpus p99 → 0.85; neutral at 0.30 × divisor):

| group | v2.4.1 | v2.5.0 |
|---|---|---|
| torso | 0.5617 | 0.5944 |
| head | 0.7595 | 0.7789 |
| arm | 1.1091 | 1.0689 |
| leg | 0.8267 | 0.6813 |
| hand | 1.4151 | 1.3878 |
| root_height | 0.9882 | 1.0224 |
| root_tilt | 100.8878 | 104.9734 |

## Cost, measured

This is not a free swap, and the write-up must not present it as one.

**`posture_label` no longer means "away from a relaxed human stance". It means "away from the
Humanoid reference".** Nobody stands at the centre of their joint ranges, so an ordinary standing
clip reads far from it on exactly the joints a person holds near an extreme when upright:
`Left Arm Down-Up` has range [−60°, 100°] and its centre is the arm held out horizontally;
`Left Lower Leg Stretch` has range [−80°, 80°] and a straight knee reads +1.008; extended fingers
read ~0.81.

The consequence is visible in the ground-truth clips and cannot be tuned away by moving the
threshold, because it is an ordering, not a level:

| clip | left arm, v2.4.1 | left arm, v2.5.0 |
|---|---|---|
| `mx_Standing_Idle` (standing still) | 0.17 neutral | **0.47 displaced** |
| `mx_Boxing_Idle` (guard raised) | 0.51 displaced | **0.29 neutral** |

A raised guard sits closer to the centre of the shoulder's range than a relaxed arm does.

**Discrimination drops**, because distance from a point nobody occupies carries a large component
common to every clip. Spread of the corpus offsets as sd/mean — how much of a reading is signal
rather than a shared pedestal:

| group | v2.5.0 (Unity) | v2.4.1 (fitted) |
|---|---|---|
| torso | 0.600 | 0.656 |
| head | 0.547 | 0.551 |
| arm | 0.370 | 0.395 |
| leg | 0.321 | 0.519 |
| hand | 0.253 | 0.521 |

**31.01% of posture labels differ** — 6849 of the store's 22086, counted from the re-measurement
diff across all 2454 records: 5623 `neutral` -> `displaced` and 1226 the other way. Over the 2446
corpus records alone the figure is 6822 of 22014 (30.99%), and the fraction of channel readings
labelled `displaced` goes from 52.5% to 74.4%. Divisor drift is 21.3%.

Deliberate extremes still read high — `mx_Crouch_Idle` head 0.4963 and root height 0.4335, the
hostage hold's clamped hands 0.9185/0.9278, `mx_Agony_Holding_The_Head`'s cradled right arm 0.9153
— so the signal is not destroyed; it is re-referenced.

**`state_label` is bit-identical across the bump** — 0 changes in the 2454-record diff, as
expected: neither the raw signals nor any static threshold moved. The SEMANTIC half and the frozen
`raw` dumps are untouched too, and all four gates pass (schema + invariants, golden re-extraction
8/8 reproducing MEASURED from frozen raw, manifest in sync, guid -> AnimationClip 8/8).

## Consequences

- **Nothing in the store's coordinate system or its origin is estimated any more.** Both come from
  the engine. `calibrate_posture.py` no longer emits an origin; it emits scale.
- **Magnitudes are not comparable across the bump.** Anything holding v2.4.1 posture numbers must
  re-read. `metric_formula_version` on every record says which.
- **A consumer asking "is this arm doing something" must not read `posture_label` alone.** Against
  this origin the honest reading of a high posture magnitude is "far from the Humanoid reference",
  and the pair (`state_label`, `posture_label`) no longer separates "holding a pose" from "standing
  relaxed" on the limbs. Retrieval and assembly consume both halves plus the SEMANTIC role; that
  the posture half alone is weaker here is a known and accepted cost.
- **The alternative stays reproducible.** `calibrate_posture.py --baseline fitted` re-derives the
  v2.4.1 origin, its selection roster and its threshold-sensitivity table, and writes them to
  `posture_calibration_fitted_ablation.md`. The ablation is one command, so the comparison in this
  ADR can be re-run rather than trusted.
