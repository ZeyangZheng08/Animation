# MotionKB v2 — Body-Part Split & Reproducible Extractor Spec

> **SUPERSEDED — read this as history, not as the contract (2026-08-26).** This is the design narrative
> for the v2 body-part split, written in 2026-06 and now several contract versions stale. The live
> contract is `agent/animation_knowledge_base/schema/motionkb.v4.schema.json`, and the field-level record
> of how it got there is `agent/animation_knowledge_base/schema/CHANGELOG.md`.
>
> Two whole sections below describe blocks that no longer exist. **§4 (`ik_goals`)** and
> **§5 (`composability`)** were deleted by
> [ADR 0022](../adr/0022-the-kb-describes-the-agent-decides.md), together with the per-channel 5-tuple of
> §3 (`role`, `motion_type`, `contact`, `constraint`, `target`), because every one of them stated a
> decision about a COMBINATION on a record that describes one clip: whether an arm swing is incidental
> depends on what the character is being asked to do, what a hand holds depends on what is in the room,
> and where an IK goal pins depends on both. A v4 record answers two questions and no others — what the
> action looks like (`action_description`, renamed from `overall_intent`) and how each body part moves
> (`channels.*.motion_description` plus the kinematic block). Composition, contact, IK and channel
> ownership are decided at runtime by the agent, which is the side holding the task and the scene.
>
> What is still worth reading here: **§0-§1** (why the body is split into nine channels, and the three
> independent constraints that split satisfies) and **§6** (the engine-neutral mask map, unaffected —
> it describes masking primitives per engine and says nothing about ownership). §2's metric has been
> replaced repeatedly; see the note below.
>
> Status: ACCEPTED & IMPLEMENTED (2026-06-18). The decision record is **ADR 0007** (supersedes ADR 0003);
> the authoritative numbers/shape now live in the implementation, not this draft. This file is kept as the
> design narrative — read ADR 0007 + the code/schema for the final values.
>
> **What changed after the 4-lens adversarial review (the draft below predates these fixes):**
> - **Architecture is PYTHON, not C#.** The extractor is the separate `animation-agent` repo (config/metrics/extract/
>   unity_sampler), engine-decoupled. Unity is touched ONLY to sample muscle clips, via a generic
>   pose-sampler generated from Python config and run over the Unity MCP bridge (§7's C# file list is
>   obsolete).
> - **Metrics calibrated, not eyeballed.** Divisors are derived from the most-active reference clip
>   (→~0.85); thresholds from the idle noise floor. `torso` uses MAX lean (rest~0); `head` uses RANGE
>   (removes its ~19° rest pedestal); hands use finger CURL-angle range (the 15-bone position mean of the
>   draft diluted grasp to ~0); a measured `gait` signal distinguishes walking from idle; sampling is
>   native-rate (the draft's 300-cap decimated cpr). See ADR 0007's metric table.
> - **Engine-neutrality corrected.** Clavicle+wrist → arm channel in all engines; toes are an optional
>   leg leaf absent in SMPL-X; IK layer inert in SMPL-X; the "1:1 Unity AvatarMaskBodyPart" claim was
>   wrong and is dropped. See `agent/animation_knowledge_base/engine_mask_map.json`.
> - **Positioning made honest.** BPQ is prior art (6-FK backbone), not "consensus"; hands/root/IK are
>   domain extensions; `role`'s 4 values are a semantic contribution (only `free`≈BPQ "Not Relevant").
>
> **The metric of §2 has been replaced twice since, and §2 is NOT what the extractor computes:**
> - **ADR 0010** (v2.1.0) refitted the divisors on the Mixamo corpus instead of one reference clip:
>   the rule is now "the corpus p99 of the raw signal normalises to 0.85", not "the most-active
>   reference clip reads 0.85".
> - **ADR 0011** (v2.2.0) moved every signal out of metres and degrees into Unity's normalised
>   Humanoid space — `HumanPose.muscles` for the 8 anatomical channels and `HumanPose.bodyPosition` /
>   `bodyRotation` for the root — which makes the numbers body-independent. §2's per-channel table
>   (torso lean in degrees, arm position stddev in metres, finger curl, foot gait) describes signals
>   that no longer exist.
> - **ADR 0018** (v2.3.0) added the orthogonal POSTURE half: every channel now carries what it HOLDS
>   (the offset of the clip's mean pose from a reference pose) beside what it MOVES. §2 has only
>   the latter, so a raised-and-held arm reads there as indistinguishable from a resting one.
>
> - **ADR 0019** (v2.4.0) refit every constant on the FULL frozen corpus (2446 mx_ dumps, offline)
>   and replaced the rest baseline: REST_POSE is now the median pose of the corpus clips that
>   measure as at-rest, selected with no name matching — no project clip is a calibration input.
>   Its 2026-08-24 amendment (v2.4.1) adds a duration floor to that selection: a clip must run at
>   least a second to count as an observation of rest.
>
> - **ADR 0020** (v2.5.0) supersedes that baseline: the posture origin is **Unity's Humanoid
>   reference pose** — every muscle at 0 (the centre of its `HumanTrait` range),
>   `bodyPosition.y` 1.0, `bodyRotation` identity — not a pose fitted from the corpus. The
>   engine fixes where zero is; the corpus fixes only how much counts as a lot. Read
>   `posture_label` as "away from the Humanoid reference", NOT as "away from a relaxed stance": a
>   person standing still reads displaced on arms, knees and hands, because those sit near the ends
>   of their ranges when upright.
>
> - **ADR 0021** (v3.0.0) retires the posture triple entirely, and with it the whole question of
>   where the origin sits. Every channel now stores `mean_pose` — the per-frame mean of each of its
>   Humanoid muscle degrees of freedom, keyed by the engine's DOF names — and the root stores
>   `mean_body_height` / `mean_body_tilt_deg`. Nothing is reduced to a scalar, divided by a fitted
>   divisor, or labelled `neutral` | `displaced`; the schema id moves to `motionkb/v3` and the half is
>   called KINEMATIC. Read the previous three bullets for how the store got here, not for what it
>   holds.
>
> Read `config.py` and ADR 0010/0011/0018/0019/0020/0021 for the live metric. §2 below is kept as the
> design narrative that motivated the channel split.
>
> All numerics in the KB are KINEMATIC — computed by the program; the semantic half stays out of the
> program's reach (ADR 0002). Since ADR 0022 that half is exactly two fields, `action_description` and
> the per-channel `motion_description`; the 5-tuple and `composability` named here are gone.

## 0. Why v2
v1 split the body into 6 parts (`head, chest, left_arm, right_arm, legs, feet`). That split:
- merged both legs into one `legs` channel (loses laterality — Uni-Inter shows baselines mis-route
  "left hand" vs "right hand"; the same applies to legs),
- folded hands into arms (loses grasp/reach distinction — critical for nursing: pinch a pill vs
  extend the arm are different functions),
- had no explicit root channel and no orthogonal IK layer (FK mask and IK goal were conflated in
  `ik_goal` living inside an arm fact).

v2 adopts a split that simultaneously satisfies three independent constraints, so it is near-canonical:
1. **Literature consensus** — Li et al. 2025 BPQ uses exactly Head/Torso/Left-Arm/Right-Arm/Left-Leg/
   Right-Leg; Fan et al. 2024 decomposes instructions into predefined body segments; CoMo / Athanasiou.
2. **Kinematic-tree branch structure** — each channel is a connected subtree rooted at a branch bone
   (pelvis→spine, neck→head, clavicle→arm, hip→leg). A mask can only cleanly act on a subtree.
3. **Unity-native mask enum** — `AvatarMaskBodyPart` = {Root, Body, Head, LeftLeg, RightLeg, LeftArm,
   RightArm, LeftFingers, RightFingers, + Foot/Hand IK}. The canonical schema maps 1:1 onto it.

## 1. Channel taxonomy (9 channels = 6 FK + 2 hands + 1 root)

| Channel | kind | Unity HumanBodyBones (the bone-map DATA) |
|---|---|---|
| `root` | root | Hips (projected object frame / heading) |
| `torso` | fk_part | Spine, Chest, UpperChest |
| `head` | fk_part | Neck, Head |
| `left_arm` | fk_part | LeftShoulder, LeftUpperArm, LeftLowerArm, LeftHand (wrist) |
| `right_arm` | fk_part | RightShoulder, RightUpperArm, RightLowerArm, RightHand (wrist) |
| `left_leg` | fk_part | LeftUpperLeg, LeftLowerLeg, LeftFoot, LeftToes |
| `right_leg` | fk_part | RightUpperLeg, RightLowerLeg, RightFoot, RightToes |
| `left_hand` | hand | Left{Thumb,Index,Middle,Ring,Little}{Proximal,Intermediate,Distal} (15) |
| `right_hand` | hand | Right{...} (15) |

Notes:
- `feet` is NOT a separate FK channel anymore — the foot/toes belong to the leg subtree (mask cleanliness).
  Foot **ground contact** is expressed in the orthogonal IK/contact layer (`ik_goals` + the leg's semantic
  `contact`/`constraint`), not as its own FK part.
- `left_arm` ends at the wrist (LeftHand bone); finger articulation is the separate `left_hand` channel.
- Rig confirmed (2026-06-18 probe): nurse_avatar.fbx is Humanoid with ALL of these bones mapped; all 8
  clips carry 40 finger + 2–8 toe + 7 root muscle curves at 30 fps.

## 2. MEASURED fields (the program writes ONLY these)

Sampling: **N = clamp(ceil(duration × frame_rate), 30, 300)** even frames via
`AnimationMode.SampleAnimationClip` on the instantiated nurse_avatar (fixes v1's fixed-30 undersampling of
long clips, e.g. cpr=18 s; capped so very long clips stay cheap). Record `sampled_frames = N`.

Per-channel objective signal → normalized `motion_magnitude` ∈ [0,1] (clamped):

| Channel | Signal | Divisor |
|---|---|---|
| `torso` | max torso-lean angle (chest-up vs world-up), deg | 60 |
| `head` | max head-vs-torso angle, deg | 50 |
| `left_arm`/`right_arm` | mean per-bone HIPS-RELATIVE world-pos stddev, m | 0.30 |
| `left_leg`/`right_leg` | mean per-bone WORLD-pos stddev, m (clips in-place ⇒ world==local; avoids hips-relative inflation under pelvis lean) | 0.30 |
| `left_hand`/`right_hand` | mean per-finger-bone WRIST-RELATIVE pos stddev, m (relative to LeftHand/RightHand ⇒ isolates finger articulation from arm transport) | 0.05 |
| `root` | max( RootT horizontal-translation stddev / 0.30 , RootQ heading stddev deg / 60 ) | (built in) |

- `state_label` = `dynamic` if `motion_magnitude ≥ STATIC_THRESHOLD` (global 0.08) else `static`;
  `is_static` mirrors it. For in-place clips `root` is ~0 ⇒ `static` (correct: walking's locomotion is
  external/NavMeshAgent, not baked; that semantic lives in SEMANTIC `motion_type=cyclic-locomotion`).
- `has_root_motion`, `root_displacement_m`, `duration`, `frame_rate`, `loop` — measured as in v1.
- `raw_measurement` per channel: `{signal, raw_value, divisor}` recorded under `extraction.raw_measurements`
  for auditability (module D).

## 3. SEMANTIC fields (human-owned; program NEVER writes — ADR 0002)

Each FK/hand channel carries the 5-tuple semantic state (the LLM-facing / RAG-bridging layer):
- `role` ∈ {primary, stabilizer, support, free} — maps to Li et al.'s relevance label (free ≈ "Not Relevant").
- `motion_type` ∈ {cyclic-locomotion, reach, hold-static, balance, gaze, manipulate}.
- `contact` ∈ {ground, "object:<name>", none}.
- `constraint` ∈ {must-maintain, must-reach, unconstrained}.
- `target` — scene entity / world frame (filled by the scene-grounding agent in Phase 2), or null.
- plus prose `motion_description` (screenshot-verified).

Migration from v1 (seed, then re-verify): v1 `motion_description/target/faces_target/interaction_object/
ik_goal` map into the 5-tuple + `ik_goals`. Migrated facts are flagged `verified_against_screenshots=false`
until re-checked. The program does NOT fabricate these.

## 4. Orthogonal IK / contact layer (`ik_goals`, top-level array)

> **Deleted in v4** ([ADR 0022](../adr/0022-the-kb-describes-the-agent-decides.md)). `target` was null on
> every record in the store because the anchor is scene-specific — two thirds of a decision with the
> deciding third permanently absent. The runtime plan binds an effector to a scene object instead.

FK mask ("which bone's rotation comes from which clip") is SEPARATE from IK goal ("which end-effector is
constrained to a world/scene target"). Unity encodes this separation too (FootIK/HandIK are distinct enum
entries). `ik_goals` is **DERIVED** from the semantic 5-tuple — each hand/foot channel whose `contact` is
`object:<obj>` and whose `constraint` pins it there (`must-reach` OR `must-maintain` — reaching-to and
holding-at both put the effector at the object) yields one goal (provenance `field_origin.derived`).
`target` (the concrete scene anchor) is **null**: the anchor is engine-specific (a Unity NurseIKHelper
group, an Unreal socket, …) and is deferred to Phase-2 grounding / the per-engine adapter, so it is NOT
stored in the engine-neutral KB — `contact_object` is the durable, engine-neutral 'what to reach'. Each entry:
```
{ effector: left_hand|right_hand|left_foot|right_foot,
  target: null,                       // deferred to Phase-2 grounding (engine-specific scene anchor)
  constraint: "TwoBoneIKConstraint",
  contact_object: <string|null>,      // DERIVED from the channel's object contact; engine-neutral
  world_space: bool }
```
Rule: "right hand reaches the patient" is an IK goal, NOT a right_arm FK mask.
"face the target" decomposes into TWO things: torso/root yaw alignment (FK/root) + head look-at (aim-IK) —
never a single quantity.

## 5. composability (over the 8 anatomical channels)

> **Deleted in v4** ([ADR 0022](../adr/0022-the-kb-describes-the-agent-decides.md)). The partition now
> arrives in the agent's plan (`overlays[].channels`, an optional `base_channels`, everything else free),
> and `posture` survives only as a MEASUREMENT — the root's `mean_body_height` binned at 0.75. The seam
> rule below is still the right description of the problem; it is solved per plan now, not per record.

`locks`/`free` PARTITION the 8 channels {torso, head, left_arm, right_arm, left_leg, right_leg, left_hand,
right_hand} (root is owned by the locomotion source, handled by the composition seam rule below).
`can_overlay_on`, `base_or_overlay`, `posture` as in v1, re-derived for the finer split.

**Composition seam rule (the v2 risk the schema must encode):** when composing (e.g. walk-legs + give-pills-
arms), `torso` and `root` are the contested seam. Default ownership: lower body + `root` → the locomotion
clip; upper body → the manipulation clip; `torso` → whichever side has the stronger orienting constraint
(a reach/gaze target). Mask blending must feather the seam (UE blend-depth / Blender weight ramp / Unity
muscle-level ramp), never a hard binary cut.

## 6. Engine-neutral mask map (CONSTANT, shared — not per-file)

Lives in `agent/animation_knowledge_base/engine_mask_map.json` (one shared reference), not duplicated per action.

| Channel | Unity AvatarMaskBodyPart | UE5 branch root | Blender bone collection | SMPL-X joints |
|---|---|---|---|---|
| root | Root | root/pelvis | root, pelvis | pelvis(0) |
| torso | Body | spine_01 | spine chain | spine1/2/3 (3,6,9) |
| head | Head | neck_01 | neck, head | neck, head (12,15) |
| left_arm | LeftArm | clavicle_l | shoulder.L→hand.L | collar/shoulder/elbow/wrist_L (13,16,18,20) |
| right_arm | RightArm | clavicle_r | shoulder.R→hand.R | …_R (14,17,19,21) |
| left_leg | LeftLeg | thigh_l | thigh.L→foot.L | hip/knee/ankle/foot_L (1,4,7,10) |
| right_leg | RightLeg | thigh_r | thigh.R→foot.R | …_R (2,5,8,11) |
| left_hand | LeftFingers | hand_l subtree | finger.L | left-hand 15 joints |
| right_hand | RightFingers | hand_r subtree | finger.R | right-hand 15 joints |

## 7. The program (reproducible — replaces hand extraction)

- `Assets/Editor/MotionKB/BodyPartBoneMap.cs` — the §1 channel→HumanBodyBones map as named DATA constants.
- `Assets/Editor/MotionKB/MotionMetricConfig.cs` — the §2 signals, divisors, sampling rule, STATIC_THRESHOLD.
- `Assets/Editor/MotionKB/MotionKBExtractor.cs` — Editor entry (`MotionKB/Extract Action…` + headless batch):
  loads a clip by guid+file_id, samples N frames on nurse_avatar, computes the 9-channel MEASURED block,
  read-merges the existing JSON preserving SEMANTIC, writes `candidate/<id>.json`, emits a run-log; per-file
  isolated, atomic write, resumable (module H).
- `Assets/Editor/MotionKB/MotionKBValidator.cs` — v2 schema + invariants + guid resolution in-editor.
- `validate_motionkb.py` (animation-agent repo) — updated to v2 (8-channel partition, IK orthogonality, root channel).
- `schema/motionkb.v2.schema.json` — the v2 contract.

Run: extractor over all 8 clips ⇒ 8 `candidate/*.json` v2 files with MEASURED filled; v1 retired but kept
via `git tag kb/v1` for rollback (ADR 0005). Semantic 5-tuple migrated + flagged, verified later by screenshot.
