# motionkb schema — CHANGELOG

All notable changes to the `motionkb` schema. The schema id is the contract between the extractor and the
Python RAG (Phase 2); bump deliberately and record every change here.

## motionkb/v2 — 2026-08-21 (metric formula v2.3.0: posture)
Every channel's MEASURED block gains a posture triple, orthogonal to the variation triple:
`posture_label` (`neutral` | `displaced`), `posture_magnitude` (0..1), `posture_measurement`.
Variation (stddev over time) cannot see a HOLD — an arm raised and kept raised read 0.0000,
identical to rest (`mx_Agony_Holding_The_Head`'s cradled head measured 0.0000; 157 records were
static on every channel, ~30 of them real-duration held poses). Posture is the offset of the clip's
MEAN pose from `config.REST_POSE`, the accepted `idle`'s mean HumanPose: anatomical channels RMS the
per-muscle offset (`muscle_dof_mean_offset_rms`); root reads the body's carriage as
`body_height_offset_m` + `body_tilt_offset_deg` (lying tilts ~90 deg and no anatomical muscle shows
it — a corpse pose is muscle-identical to standing). Divisors fitted corpus p99 → 0.85 over all
2454 dumps; `displaced` at 0.30 × divisor, anchored on measured rest clips (`mx_Breathing_Idle`,
`Walk_N`) below and inspected holds (boxing guard, bow aim, crouch, fists) above. All three fields
required on all 9 channels. Fit: `calibrate_posture.py`; report:
`motionkb_build/reports/posture_calibration.md`; ADR 0018.

> The SCHEMA has not changed since, but the ORIGIN this entry names has moved twice — the entry
> describes what shipped with v2.3.0, not the live metric. `REST_POSE` became the corpus rest set's
> median in formula v2.4.0/v2.4.1 (ADR 0019), and in v2.5.0 it was deleted for Unity's Humanoid
> reference pose — every muscle 0, `bodyPosition.y` 1.0, `bodyRotation` identity (ADR 0020). Read
> `posture_label` as "away from the Humanoid reference", not "away from a relaxed stance". The
> field named `body_height_offset_m` here is `body_height_offset` since v2.4.0. `config.py` and
> each record's `metric_formula_version` are authoritative.

## motionkb/v2 — 2026-07-01 update (ik_goals made a DERIVED, engine-neutral field)
Non-breaking (backward-compatible; the accepted store still validates). `ik_goals` is no longer orphaned
"migrated-once-from-v1" data — it is now **DERIVED by the `propose` step** from the semantic 5-tuple (a
hand/foot channel with `contact=object:<obj>` and `constraint ∈ {must-reach, must-maintain}` yields one
goal; provenance `field_origin.derived`), so the whole KB is regenerable end-to-end.
- **`ik_goals[].target` is now nullable** (`type: ["string","null"]`, was a required non-empty string). The
  concrete scene anchor is engine-specific (a Unity NurseIKHelper group, an Unreal socket, …) and is
  deferred to Phase-2 grounding / the per-engine adapter — it is NOT stored in the engine-neutral KB.
  `contact_object` is the durable, engine-neutral 'what to reach'.
- **Gate rule broadened:** an ik_goal now requires `constraint ∈ {must-reach, must-maintain}` (was
  `must-reach` only) — an effector pinned to an object needs IK whether it reaches-to or holds-at it. This
  makes ik_goals robust to the VLM's reach/maintain reinterpretation. Enforced by `validate_motionkb.py` (animation-agent repo).

## motionkb/v2 — 2026-06-18
Breaking re-split of the body-part taxonomy (ADR 0007, supersedes ADR 0003) + a Python-side, engine-
decoupled extractor (the separate `animation-agent` repo). **Promoted to the root ACCEPTED store on 2026-06-24** (after the
ADR 0008 VLM-proposal pass filled + verified the SEMANTIC 5-tuple, `status: accepted`); the former v1
store is retired but preserved at git tag `kb/v1` (`motionkb.v1.schema.json` kept as its contract).
Highlights of the v2 schema:
- **9 channels** replace the 6 `body_parts`: `channels` = 8 anatomical (PARTITION set, partitioned by
  `composability.locks/free`) + 1 `root` (measured-only, NOT partitioned). Legs and hands are now split
  left/right; hands are a first-class channel (fingers only); clavicle+wrist live in the arm channel.
- **Per-channel measured block** `{kind, state_label, motion_magnitude, raw_measurement{signal,…}}`;
  `is_static` dropped (redundant with `state_label`). `root` is measured-only (no semantic 5-tuple).
- **Semantic 5-tuple** per anatomical channel: `role` / `motion_type` / `contact` / `constraint` /
  `target` (+ `motion_description`). Seeded `null` (PENDING) on first build — never machine-authored.
- **Orthogonal `ik_goals`** array (effector ∈ {left_hand,right_hand,left_foot,right_foot}); FK mask and
  IK goal are separate. `composability` gains `seam_owner{torso,root}`.
- **`extraction`** requires `sampling_rule`, `bone_map_version`, `metric_formula_version`,
  `extractor_version`, `avatar`, real `extracted_at`, `field_origin`; `extractor_lang` added.
- Shared **`engine_mask_map.json`** is the single channel-vocabulary source of truth (Unity/UE5/Blender/
  SMPL-X). Sampling is native-rate (no 30-cap); divisors/thresholds are calibration-derived, not eyeballed.

Cross-field invariants (locks/free partition of the 8, overlay lock-disjointness, posture compatibility,
ik effector→channel resolution, channel-vocabulary agreement with `engine_mask_map.json`) are enforced by
`validate_motionkb.py` (animation-agent repo) (now v2). 8/8 candidates pass.

### 2026-08-21 — the semantic half became nullable (ADR 0014)
`action_id`, `display_name` and `overall_intent` are now `["string", "null"]`, and `tags` lost
`minItems: 1`. A widening, so every previously valid document is still valid. It exists because a
record whose MEASURED half is complete and whose SEMANTIC half has not been proposed yet is a real
state of the pipeline — it is literally what `extract.py register` writes — and requiring those four
in the schema made that state indistinguishable from a malformed record. The requirement moved to
`validate_motionkb.py`, attached to `status`: anything other than an explicit `candidate` must have
all four. Fail-closed, so a record with no `status` at all is still held to the full bar.

## motionkb/v1 — 2026-06-18
Initial machine-checkable schema, pinned to the stable shape of the 8 existing action files
(`idle`, `walking`, `typing`, `giving_pills`, `cpr`, `grab_bottle`, `check_pulse`, `bvm`). Highlights:
- Top level: 18 required fields; `additionalProperties: false`.
- `trigger_param` nullable (e.g. `idle`); `source_clip.file_id` is an integer and may be negative
  (`X Bot@Typing.fbx` = `-203655887218126122`); the `.anim` assets use `7400000`, the 5 `nurse_*.fbx`
  overlays share `1827226128182048838` — `file_id` is rig/importer-specific, not a Unity invariant.
- `body_parts`: exactly the 6 parts; each a BodyPartFact; `ik_goal` is `null` or
  `{ effector (left_hand|right_hand), target, constraint }`.
- `composability.posture` optional (`standing` default; only `typing` sets `seated`).
- `extraction`: requires the current 5 fields; the module-D provenance fields (`extractor_git_sha`,
  `metric_formula_version`, `raw_measurements`, `field_origin`, …) are optional / additive.
- `status` (`candidate` | `accepted` | `deprecated`) optional, pre-wired for module-C versioning.

Cross-field invariants (is_static ↔ state_label mirror, locks/free partition of the 6 parts, overlay
lock-disjointness) and `guid → asset` resolution are NOT expressible in JSON Schema — they are enforced
by `validate_motionkb.py` (animation-agent repo) (+ the Unity `MotionKBValidator` for guid). See `../../../HANDOFF.md`
§8 (module A).
