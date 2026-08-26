# 0018 — Posture joins the measured block

Status: Accepted (2026-08-21). Extends the MEASURED half of every channel with a posture triple —
`posture_label` (`neutral` | `displaced`), `posture_magnitude` (0..1), `posture_measurement` — and
bumps the metric formula v2.2.0 → v2.3.0. All 2454 records re-measured from their frozen `raw`
dumps; no semantic field, no raw dump, and no variation number changed.

## Context

The measured state of a channel was one signal: `muscle_dof_stddev_rms`, the variation of that
channel's Humanoid degrees of freedom over time (ADR 0011). Variation answers "does this joint
MOVE". It cannot see a HOLD — an arm raised and kept raised has the same stddev as an arm hanging
at rest: zero. Measured on the store:

- `mx_Agony_Holding_The_Head`, 3.7 s of cradling the head, read **0.0000** on the head channel.
- `mx_Armed_Villain_Holding_A_Hostage_From_Behind`, 5.0 s, read static on all 8 anatomical
  channels — by the store's account, nothing happened for five seconds.
- 157 records were static on every channel; ~30 of them are real-duration held poses, not 2-frame
  pose assets.

So "raise the left arm and keep it there" — exactly the kind of clip part-wise retrieval must find —
was indistinguishable from doing nothing. The root channel had the same blindness one level up:
lying is muscle-identical to standing (straight legs, straight spine); the difference is the
carriage of the body, which no anatomical muscle shows.

What is deliberately NOT the fix: recording carried motion. When a torso leans and the head rides
along, the head's joints do not move, and composing that torso onto another base re-creates the
carry through forward kinematics at bake time. `nurse_cpr_30`'s head correctly measures 0 — and the
new signal confirms it independently (posture offset 0.0061, genuinely at rest). What was missing
was the other axis entirely: not what a channel moves, but what it holds.

## Decision

One new signal per channel, orthogonal to variation: the offset of the clip's MEAN pose from a rest
baseline.

1. **Anatomical channels** — `muscle_dof_mean_offset_rms`: RMS over the channel's muscle DOF of
   (mean over frames − baseline), in the same normalised muscle space as variation, so it stays
   dimensionless and body-independent.
2. **Baseline** — `config.REST_POSE`, the accepted `idle`'s mean HumanPose over its frozen dump.
   Derived by `calibrate_posture.py`, frozen as a constant so every record is measured against the
   same rest and the golden test reproduces bit-for-bit.
3. **Root** — carriage, not muscles: `body_height_offset_m` (|mean bodyPosition.y − rest|; crouching
   reads 0.33–0.41, hanging/lying 0.4+) and `body_tilt_offset_deg` (mean tilt of the body's up axis;
   push-up/floor poses 41–71, lying ~90).
4. **Divisors** — corpus p99 → 0.85, fitted over all 2454 dumps, the same rule the variation
   divisors follow. Report: `motionkb_build/reports/posture_calibration.md`.
5. **Threshold** — `displaced` at 0.30 × the group's divisor: one constant in NORMALISED space.
   A single raw threshold (the STATIC_MUSCLE move) does not transfer here, because posture at rest
   is not near-zero across clips: two genuinely relaxed standings differ in natural carriage, and
   the scatter differs by family (torso ~0.05–0.18, hands ~0.29–0.38). 0.30-of-scale is anchored on
   measured ground truth: below it, `mx_Breathing_Idle` and `Walk_N`'s swing-around-rest limbs and
   `nurse_cpr_30`'s at-rest head; above it, every inspected deliberate hold (boxing guard arms
   0.58–0.61, bow-aim head 0.78–0.81, crouch legs 0.72–0.84, walking's fists 1.21–1.40, the cradled
   head 0.365).

The two labels now span four readable states: static + neutral = rest; **static + displaced = a
hold**; dynamic + neutral = motion around rest carriage (locomotion limbs); dynamic + displaced =
working in a displaced posture (CPR's compressing arms).

## Consequences

- 3395 channel readings in 1181 clips are variation-static with a posture offset ≥ 0.1 — the holds
  that were invisible. `mx_Arms_Raised` arms: 0.0000 variation, 0.89/0.91 posture.
- Contract: the triple is REQUIRED on all 9 channels (schema + CHANGELOG); `field_origin.measured`
  and the golden regression cover it; `extraction.motion_metric` derives the description from
  config, as before.
- Validator: the `role=primary but static` nudge now fires only at rest posture (a displaced hold
  visibly occupies its channel); a static-but-displaced channel listed in `composability.free`
  warns — a hold occupies a channel exactly as motion does.
- `kbindex.channels()` exposes `posture_label` beside `state` — verbatim, not shortened to
  `posture`, because `composability.posture` (standing | seated) already owns that key in
  search hits, and one key with two disjoint vocabularies is the constraint-vs-constraint
  trap over again; `propose`'s measured summary hands both to
  the VLM as given facts.
- Division of labour, sharpened: MEASURED owns whether a channel moves AND what it holds; the VLM
  is not a backstop for measurement. SEMANTIC adds what no joint number can produce — what the
  action is, why a channel moves, what it touches — so retrieval has meaning to search, and the
  one-way check stays: numbers refute descriptions, never the reverse.
- `derived/` tables are untouched: they fingerprint against `raw`, and `raw` did not change.
