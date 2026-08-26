# 0010 — Normalisation divisors refitted on the corpus, and root translation measured from the hips; `metric_formula_version` v2.1.0

Status: Accepted (2026-08-20). Amends the DIVISOR table set under ADR 0007 / ADR 0003. ADR 0002
(measured vs semantic) is the constraint this migration was executed under and remains binding.

## Context

Every divisor in `config.DIVISOR` was chosen so that one nominated nurse clip normalised to 0.85 —
`check_pulse` for torso and arms, `giving_pills` for head and hands, `walking` for legs and root
gait. That was a reasonable calibration for a store of eight small-amplitude bedside actions.

On 2026-08-20 a 2446-clip Mixamo corpus was imported (see `Dataset/tools/README.md`). Sampling 150
of it at random (`calibrate_divisors.py`, seed 0) showed the calibration was exhausted:

| group | current divisor | corpus p50 | p90 | p99 | max | readings clamped at 1.0 |
| --- | --- | --- | --- | --- | --- | --- |
| torso | 54.0 | 20.90 | 80.85 | 164.72 | 174.73 | 12.7% |
| head | 15.4 | 11.34 | 31.03 | 45.71 | 64.24 | **39.3%** |
| arm | 0.137 | 0.112 | 0.278 | 0.505 | 0.552 | **40.0%** |
| leg | 0.247 | 0.129 | 0.800 | 1.366 | 1.877 | **40.0%** |
| hand | 129.0 | 15.76 | 93.84 | 141.16 | 152.74 | 3.3% |
| root_gait | 0.317 | 0.080 | 0.603 | 1.552 | 1.653 | 19.3% |

The clearest single case is not an exotic one: `mx_Female_Walk_Forward`, an ordinary walk, read 1.0
on both legs, where the KB's own `walking` — an *in-place* walk — reads 0.85. The leg divisor was
fitted to a clip that does not travel, so any clip with a real stride exceeds it.

A field that saturates on two fifths of the corpus cannot discriminate, and discrimination is the
whole job of `motion_magnitude`: it is what tells a reader that a walk and a cartwheel differ at the
legs. Left alone, the corpus would enter the store with that column carrying almost no information.

## Decision

Refit each divisor so the corpus **99th percentile** normalises to 0.85, leaving 0–0.7% saturation:

| group | v2.0.0 | v2.1.0 |
| --- | --- | --- |
| torso | 54.0 | 193.8 |
| head | 15.4 | 53.8 |
| arm | 0.137 | 0.595 |
| leg | 0.247 | 1.607 |
| hand | 129.0 | 166.1 |
| root_gait | 0.317 | 1.826 |
| root_trans | 0.30 | 1.551 |
| root_heading | 60.0 | 149.7 |

`metric_formula_version` goes to **v2.1.0**. `bone_map_version` and `extractor_version` are unchanged —
no bone map and no sampling rule moved. The root formula's INPUT changed (root transform → hips),
which is why this is a formula-version bump and not merely a constants change.

### Root translation and turning are measured from the hips

`root_trans` and `root_heading` could not be fitted at first, because they were reading nothing:
`root_pos` was byte-identical on every frame of all 150 sampled clips, so both signals were 0.0
across the entire sample — a walk covering 1.125 m of ground reported zero translation. The corpus
is imported with Root Transform Position and Rotation baked into the pose, so Unity applies one
constant offset for the whole clip and `inst.transform` never moves.

The travel is not missing, only misfiled: bone positions are recorded root-local, and the hips
trajectory carries it. Measured on the frozen dumps:

| clip | hips horizontal stddev | net displacement | ground covered | `root_pos` stddev |
| --- | --- | --- | --- | --- |
| `mx_Reaction_To_Getting_Clipped…` | 0.672 | 2.106 m | 2.270 m | 0.0000 |
| `mx_Capoeira_Idle` | 0.524 | 0.000 m | 1.513 m | 0.0000 |
| `mx_Female_Walk_Forward` | 0.340 | 1.125 m | 1.126 m | 0.0000 |
| `Walk_N` (in-place) | 0.016 | 0.000 m | 0.042 m | 0.0000 |
| `nurse_cpr_30` | 0.003 | 0.000 m | 0.008 m | 0.0057 |

So `metrics.compute_raw_signals` now takes `root_trans` from the hips trajectory and `root_heading`
from the hips' signed yaw (`bone_rot`, present since 2026-08-06; dumps without it fall back to the
old root reading rather than failing). A standard deviation is translation-invariant, so measuring
in root-local space gives the true excursion.

Net displacement is deliberately NOT the signal. A capoeira ginga returns to where it started — net
0.000 m — while covering 1.5 m of ground, and it is the covering that makes it locomotion.

Both divisors were then fitted on the same 150-clip sample. Both had been badly wrong once they
measured anything: at 0.30 the old translation divisor saturated on **32%** of the corpus.

The `root` channel now separates what it is supposed to separate:

| clip | root magnitude before | after |
| --- | --- | --- |
| `mx_Reaction_To_Getting_Clipped…` (2.1 m) | 0.107 | **0.738** |
| `mx_Capoeira_Idle` (1.5 m covered) | 0.088 | **0.338** |
| `mx_Female_Walk_Forward` (1.1 m) | 0.060 | **0.219** |
| `mx_Old_Man_Standing_Idle` | 0.005 | 0.016 |
| nurse clips (in place) | 0.000–0.035 | 0.001–0.089 |

Before this, a walk and a stand were indistinguishable at the root.

### What this does NOT change

**`state_label` is unaffected.** Static/dynamic is decided by `config.STATIC`, applied to the RAW
physical signal, never to the normalised value. The migration script asserted this per channel and
reported zero flips across all eight records.

**No SEMANTIC field moved.** Verified field by field against `HEAD` after the migration: zero
differences in `display_name`, `overall_intent`, `tags`, `mask_coverage`, `composability`,
`ik_goals`, `source_clip`, `controller_*`, `status`, or any `channels.*.{role, motion_type, contact,
constraint, target, motion_description}` — and `extraction.vlm_proposal` / `verified_by` /
`verified_at` / `verified_against_screenshots` are carried through unchanged. A formula migration is
not a re-authoring, and the VLM proposals that produced the semantics still stand.

## Consequences

- **The nurse actions read lower.** `walking` legs 0.850 → 0.131; `check_pulse` torso 0.849 → 0.237;
  `giving_pills` hands 0.853 → 0.663. Their ordering relative to each other is unchanged. On a scale
  that has to hold cartwheels and sword work, a bedside pulse check *is* a small movement, and the
  new numbers say so. Anything that reads `motion_magnitude` as "large means important" was reading
  it wrong; it means "large relative to the corpus".
- **Magnitudes are not comparable across the `kb/v2` tag.** Records at v2.0.0 and v2.1.0 use
  different scales. The tag still restores a coherent store; it does not restore a comparable one.
- **`motion_metric` is now derived from `config.DIVISOR`** instead of being a hand-typed string
  (`extract.py::_build_extraction`). It had already drifted from being retyped once; a record's own
  account of how its numbers were produced must not be able to disagree with the code that produced
  them.
- **Migration is re-runnable.** `recalibrate_measured.py` rewrites only the MEASURED block of the
  accepted store from the frozen `raw` dumps, and `test_golden_extraction.py` recomputes from those
  same dumps — so the golden values track the new formula automatically rather than needing a
  separate re-freeze. Both passed 8/8 after the change.
- **The fit is reproducible.** `calibrate_divisors.py --limit 150 --seed 0` re-draws the same sample;
  `--reuse` recomputes the fit from saved dumps with no engine. Neither writes into the KB.

## Alternatives considered

**Leave v2.0.0 alone.** Zero churn, and the accepted records keep their frozen numbers. Rejected:
40% of arm and leg readings in the corpus are 1.0, and the saturation is worst exactly where the
corpus is richest — locomotion and whole-body action.

**Fit at p90 instead of p99.** More spread through the middle of the range, at roughly 10%
saturation. Rejected: saturation is the failure this ADR exists to remove, and p99 already leaves
the nurse actions clearly separated from each other.

**Two scales — one for clinical actions, one for the general corpus.** Rejected: `motion_magnitude`
is a single comparable column or it is not useful for retrieval at all, and a per-subcorpus scale
would make the arbitration rules depend on which subcorpus a clip came from.
