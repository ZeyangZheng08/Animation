# 0021 — Kinematic facts, not classifications

Status: Accepted (2026-08-25). Renames the MEASURED half of every record to **KINEMATIC**, deletes
the posture triple in favour of the mean pose itself, and bumps the contract: schema `motionkb/v2` →
`motionkb/v3`, `metric_formula_version` v2.5.0 → **v3.0.0**, `extractor_version` 2.0.0 → **3.0.0**.
All 2454 records re-measured from their frozen `raw` dumps. Supersedes the posture-classification
parts of [0018](0018-posture-joins-the-measured-block.md),
[0019](0019-calibration-corpus-derived-and-reproducible.md) and
[0020](0020-posture-origin-is-unitys.md); the variation half of each stands unchanged.

## Context

Variation — `muscle_dof_stddev_rms`, how much a channel's degrees of freedom move over time — cannot
see a hold. `mx_Arms_Raised` reads **0.0000** on both arms: by the variation signal alone, an arm
held over the head and an arm hanging at the side are the same reading. ADR 0018 added a second
signal for exactly that, and it was the right thing to add. What it added was not.

The signal it added was a **distance**: take the clip's mean pose, subtract a reference pose, RMS the
residual to one scalar per channel, divide by a fitted divisor, and threshold the result into
`neutral` | `displaced`. Three of those five steps are choices, and the store has now paid for each
of them.

- **The reference moved twice.** v2.3.0 used the project's accepted `idle` — one clip, one body, one
  person's stance; its left-leg stance alone made ~1000 corpus leg channels read `displaced`. v2.4.0
  fitted a pose from the 265 corpus clips that measure as at-rest. v2.5.0 (ADR 0020) deleted that
  for Unity's Humanoid reference, because the fitted pose was an estimate with selection parameters
  and *there is no one relaxed stance* to estimate.
- **The last move cost most of the signal's meaning, and ADR 0020 said so.** Nobody stands at the
  centre of their joint ranges, so `mx_Standing_Idle` reads **0.47 displaced** on the left arm while
  `mx_Boxing_Idle`'s raised guard reads **0.29 neutral** — a raised guard sits closer to the centre
  of the shoulder's range than a relaxed arm does. 31% of all posture labels flipped across that
  bump, and the fraction of channel readings labelled `displaced` went from 52.5% to 74.4%. Corpus
  spread as sd/mean fell on every group, most sharply on legs (0.519 → 0.321) and hands
  (0.521 → 0.253): distance from a point nobody occupies carries a large component common to every
  clip.
- **The label was a statement about the origin as much as about the clip.** ADR 0020 accepted that
  as a known cost and told consumers not to read `posture_label` alone. A field the contract has to
  warn you not to read is not a field, and the sequence of three origins is not bad luck: the
  question "is this pose displaced?" has no answer that does not first fix an arbitrary "displaced
  from what".
- **And the reduction threw the pose away.** Whatever origin was chosen, RMS-ing 20 hand DOFs into
  one number discards which fingers are curled; `mx_Arms_Raised`'s left arm reduced to `0.89`, and
  0.89 does not say the arm is overhead. The store held 22086 of these scalars and could not
  reconstruct one pose from them.

Meanwhile the thing the scalar was computed FROM is exact, cheap, already in the frozen dumps, and
answers the question the signal was added for.

## Decision

**Store the mean pose. Classify nothing.**

1. **`mean_pose`, per anatomical channel.** The per-frame mean of each of the channel's Unity
   Humanoid muscle degrees of freedom, as a JSON object keyed by the engine's own DOF names, written
   in ascending Unity muscle index within the channel — self-describing, text-diffable, and in a
   fixed order so two records line up. Same normalised muscle space as variation, so it stays
   dimensionless and body-independent.
2. **The root stores its carriage the same way**: `mean_body_height` (mean `HumanPose.bodyPosition.y`,
   normalised humanoid units, not metres) and `mean_body_tilt_deg` (mean angle of `bodyRotation`'s up
   axis from world up). Both are the clip's own means, not offsets from a reference carriage.
   `body_height_offset` and `body_tilt_offset_deg` are deleted. Mean XZ and mean heading are
   deliberately NOT stored: they are properties of where the clip was authored rather than of the
   motion, and how far the body travels is already the root's variation signal.
3. **No origin, no divisor, no threshold.** `POSTURE_DIVISOR`, `NEUTRAL` (0.30 × divisor) and
   `REFERENCE_POSE` are deleted from `config.py`, and `calibrate_posture.py` is deleted with them.
   Muscle 0 and `bodyPosition.y` = 1.0 survive as what ADR 0011 always used them for — **the
   mathematical origin of the shared coordinate system** — and carry no reading of "rest",
   "standard" or "neutral" anywhere in the code or the docs.
4. **The half is called KINEMATIC.** `field_origin.measured` → `field_origin.kinematic` in every
   record and the schema; `extract._apply_measured` → `_apply_kinematic`; `recalibrate_measured.py`
   → `recalibrate_kinematic.py`; `propose._measured_summary` → `_kinematic_summary`; the docs say
   KINEMATIC where they named the block. This follows the 2026-07-01 `AUTHORED` → `SEMANTIC` rename
   exactly, and for the same reason: the old name had drifted from what the half holds. "MEASURED"
   was accurate about provenance and silent about content, and it invited exactly the mistake this
   ADR is undoing — a label produced by a program reads as measured even when what was measured is
   the program's own threshold. KINEMATIC says what the numbers are: joint motion and joint pose.
   `muscle_dof_stddev_rms` and the whole SEMANTIC side keep their names; so do the
   `vlm_proposed` / `vlm_accepted` / `human_accepted` provenance tiers.
5. **`schema_version` becomes `motionkb/v3`.** Posture landed inside `motionkb/v2` because it only
   added fields. This deletes fields, adds fields and renames a `field_origin` tier — a consumer
   written against v2 breaks on it — so the id moves. `motionkb.v2.schema.json` stays on disk as the
   historical contract, as `motionkb.v1.schema.json` did before it.

### What did NOT change

The variation half, in full: `muscle_dof_stddev_rms`, the v2.4.0 `DIVISOR` fit, `STATIC_MUSCLE` =
0.02, `state_label`, `motion_magnitude`, `raw_measurement`, and the root's
trans/vert/heading signals. **Verified bit-identical across all 2454 records** — 0 differing values
on `state_label`, `motion_magnitude`, `raw_measurement`, `kind`, `duration` or `frame_rate` in the
re-measurement diff. Also untouched: the frozen `raw` dumps, the whole SEMANTIC half, and
`calibrate_divisors.py` with its report. The `derived/` tables fingerprint against `raw`, which did
not change, so a rebuild reproduces them byte for byte apart from one line of prose in each `_meta`
naming the contract version.

## Consequences

**A held pose is now legible instead of merely flagged.** `mx_Arms_Raised`'s left arm is
`state: static`, `motion_magnitude: 0.0` — and `Left Shoulder Down-Up: 1.39`, `Left Arm Twist
In-Out: 1.15`. The two idles ADR 0020 could not order now need no ordering: `mx_Standing_Idle` reads
`Left Shoulder Down-Up: -0.75, Left Arm Down-Up: -0.54`, `mx_Boxing_Idle` reads `-0.55 / -0.19`, and
a consumer that wants a distance between them can take one against whatever reference its own task
defines. The store no longer decides that for it.

**Two validator checks are gone, not re-expressed.** Both branched on `posture_label`: the
`role=primary but static` nudge fired only at rest posture, and a static-but-displaced channel
listed in `composability.free` warned. Neither survives without the label, and neither was replaced
by a distance threshold — inventing one in the validator would put back exactly the arbitrary choice
this removes. The nudge is the clearer loss to be honest about: without a pose gate it fires on
every held pose in the store, which is the case the pose signal exists to make visible. Every
static/dynamic semantic-consistency check stays (`manipulate` / `reach` / `cyclic-locomotion` on a
static channel, `hold-static` on a dynamic one, and the rest).

**Consumers get the vector as a plain given fact.** `kbindex.channels()` hands `mean_pose` over
whole, rounded to 2 decimals for the projection (the record carries 5), plus the root's two means;
`propose`'s summary gives the VLM the same, on a second line per channel. Neither derives a label,
because there is none to derive.

**Records grew 26%** — `actions/` goes from 20.5 MB to 25.9 MB for the 95 extra numbers per record.
The corpus vocabulary a `grep` sees grew with it, from 104 distinct words to 164, all of them the
fixed DOF names; the tripwire in `tests/test_tools_files.py` was moved to 200 with that reasoning
recorded, and `glob`-before-`grep` guidance is unaffected because the added words are identical in
every record.

**Magnitudes from v2.5.0 do not carry over, and neither does the vocabulary.** Anything holding a
`posture_label` or `posture_magnitude` must re-read; `metric_formula_version` on every record says
which formula produced it. The two calibration reports for the retired formula move to
`motionkb_build/archive/` rather than being deleted, so ADR 0018–0020 can still be checked against
the output that produced them.

**Gates, after the re-measure:** validate 2454/2454 against `motionkb.v3.schema.json`, golden
re-extraction 8/8 reproducing KINEMATIC from frozen `raw`, manifest in sync, agent-repo suite
349/349, retrieval eval 7/12 — unchanged, as expected: no arm of it ever read a posture label.
