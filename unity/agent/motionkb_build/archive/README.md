# archive — superseded build artifacts, kept for audit

Nothing here is read at runtime and nothing here is a fact about motion. These are artifacts of how
the knowledge base used to be built, kept so a claim made in an ADR can still be checked against the
output that produced it.

| | |
|---|---|
| `motionkb.v1.schema.json` | the v1 contract, retired 2026-06-24 when the v2 store was promoted (the live contract lives in `animation_knowledge_base/schema/`) |
| `authored_claude_backup/` | the SEMANTIC half as Claude first proposed it, before the pass that re-proposed all 8 with `gpt-5.5` (ADR 0008) |
| `posture_calibration.md` | the fit of `POSTURE_DIVISOR` and `NEUTRAL` for the posture formula of `metric_formula_version` v2.3.0 – v2.5.0 |
| `posture_calibration_fitted_ablation.md` | the corpus-fitted `REST_POSE` origin of v2.4.0/v2.4.1, re-derived as an ablation against the Unity origin of v2.5.0 (ADR 0020) |

**The two posture reports document a formula the store no longer uses.** Through v2.5.0 each channel
carried a `posture_label` / `posture_magnitude` / `posture_measurement` triple: the distance of the
channel's mean pose from an origin, normalised by a fitted divisor and thresholded into
`neutral` | `displaced`. Formula v3.0.0 deleted the triple and stores the mean pose itself instead, so
there is no origin-relative scale left to calibrate and `calibrate_posture.py` was deleted with it
(ADR 0021). The variation half is untouched, and its fit — `calibrate_divisors.py` and
`reports/divisor_calibration.md` — is live, not archived.
