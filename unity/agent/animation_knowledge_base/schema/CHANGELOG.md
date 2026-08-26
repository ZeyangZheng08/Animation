# motionkb schema — CHANGELOG

All notable changes to the `motionkb` schema. The schema id is the contract between the extractor and the
Python RAG (Phase 2); bump deliberately and record every change here.

## motionkb/v3 — 2026-08-26 (amended: the channel `kind` field is removed)
**Not a new contract id.** `kind` carried no information a consumer could not already read, so it is
deleted from every channel block rather than kept as a field every writer has to fill correctly.

- **Removed** from all 9 channels: `kind` (`fk_part` | `hand` on the eight anatomical channels,
  `root` on the root). `channels` is a name-keyed object, and the schema already dispatches a
  channel's structure on that key — the root's block is validated as the root because it is stored
  under `"root"`, not because it declares itself one. The `hand` value was the channel name
  `left_hand` / `right_hand` restated, and nothing computed anything from either value: the one
  consumer, the validator's `motion_type=manipulate` nudge, now tests the channel name directly.
- **No number changed.** Variation, `mean_pose`, the root's carriage means, `raw_measurement`,
  the SEMANTIC half, `composability`, `ik_goals` and the frozen `raw` dumps are untouched;
  `metric_formula_version` stays `v3.0.0` and `extractor_version` goes to `3.1.0`, which is the only
  other line the store rewrite moves.
- **A leftover `kind` is now an error, not a warning.** Both channel definitions are
  `additionalProperties: false`, so a record written by an older extractor fails validation instead
  of passing with a field the contract no longer describes.
- `engine_mask_map.json` keeps its own per-channel `kind`: that file is a different contract
  (`motionkb-engine-map/v1`) describing each channel's masking primitive per engine, and its value is
  read by people, not by the record schema.

## motionkb/v3 — 2026-08-25 (metric formula v3.0.0: the mean pose is stored, not classified)
**Breaking.** In one line: **v2 was variation plus a scalar distance-to-a-reference posture; v3 is
variation plus the actual mean pose vector.** A consumer written against v2 does not read a v3
record.

- **Removed** from every channel: `posture_label` (`neutral` | `displaced`), `posture_magnitude`,
  `posture_measurement`; and from the root's measurement block, `body_height_offset` /
  `body_tilt_offset_deg`. They were one number per channel — the RMS distance of the clip's mean pose
  from a reference pose, divided by a fitted divisor and thresholded. The reference moved three
  times (accepted `idle` → a pose fitted from the corpus → Unity's Humanoid reference) because there
  is no non-arbitrary answer to "displaced from what", and the reduction to a scalar could not say
  which fingers were curled.
- **Added**, required on the 8 anatomical channels: **`mean_pose`** — an object mapping each of that
  channel's Unity Humanoid muscle DOF names to that DOF's per-frame mean, in the same normalised
  muscle space as variation. Keys are WRITTEN in ascending Unity muscle index within the channel, so
  records stay diffable and the vector explains itself. Required on the root instead:
  **`mean_body_height`** (mean `HumanPose.bodyPosition.y`, normalised humanoid units) and
  **`mean_body_tilt_deg`** (mean angle of `bodyRotation`'s up axis from world up). Mean XZ and mean
  heading are deliberately absent: both describe where the clip was authored, and how far the body
  travels is already the root's variation signal.
- **Renamed**: the `extraction.field_origin` tier `measured` → `kinematic`, on every record and in
  the schema. The half is called KINEMATIC now — the old name described provenance and said nothing
  about content, which is what let a thresholded label read as a measurement (ADR 0021, following the
  2026-07-01 `authored` → `semantic` precedent). `muscle_dof_stddev_rms`, the SEMANTIC side, and the
  `vlm_proposed` / `vlm_accepted` / `human_accepted` provenance values are unchanged.
- **Unchanged**: the variation triple (`state_label`, `motion_magnitude`, `raw_measurement`), the
  v2.4.0 `DIVISOR` fit, `STATIC_MUSCLE` = 0.02, the root's trans/vert/heading signals, the frozen
  `raw` dumps, `derived/`, and the whole SEMANTIC half. Verified bit-identical across all 2454
  re-measured records.
- **Origin**: Unity's muscle 0 and `bodyPosition.y` = 1.0 remain the coordinate system every number
  lives in (ADR 0011) and carry no reading of rest, standard or neutral anywhere. `REFERENCE_POSE`,
  `POSTURE_DIVISOR` and `NEUTRAL` are deleted from `config.py`; `calibrate_posture.py` is deleted and
  its reports archived under `motionkb_build/archive/`.
- **Validator**: `validate_motionkb.py` targets `motionkb.v3.schema.json` and drops the two checks
  that branched on `posture_label` (the `role=primary but static` nudge and the
  static-but-displaced-in-`composability.free` warning), with no replacement threshold. Its schema
  interpreter grew `minProperties` and `additionalProperties`-as-a-schema for `mean_pose`.
- `motionkb.v2.schema.json` is kept on disk as the historical contract, as `motionkb.v1.schema.json`
  was before it. ADR: [0021](../../../docs/adr/0021-kinematic-facts-not-classifications.md).

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
