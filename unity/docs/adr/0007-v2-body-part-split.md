# 0007 — v2 body-part split (9 channels) + Python-side reproducible extractor

Status: Accepted (2026-06-18). Supersedes ADR 0003 (the v1 6-part split). ADR 0002 (measured vs
semantic) remains binding and is reinforced here.

## Context
The v1 split (`head, chest, left_arm, right_arm, legs, feet`) merged both legs, folded hands into arms,
and had no root channel or orthogonal IK layer. A 4-lens adversarial review of the v2 draft spec
(measurement soundness, engine neutrality, schema/migration, research positioning) returned 10 blockers;
the taxonomy intent was sound but the draft's metrics, engine-mapping claims, and positioning needed
rework before any code was written.

## Decision
**Taxonomy.** 9 channels = 8 anatomical (PARTITION set, partitioned by `composability.locks/free`) + 1
`root` (locomotion-owned, measured-only, NOT partitioned):
`root, torso, head, left_arm, right_arm, left_leg, right_leg, left_hand, right_hand`.
- Laterality split kept for legs AND hands — a GENERAL design decision (current corpus is all in-place,
  so per-leg/root are forward-declared for future locomotion clips, not validated on the 8 clips now).
- Clavicle (shoulder) AND wrist belong to the ARM channel in ALL engines (Unity needs per-transform
  masking, since `AvatarMaskBodyPart.LeftArm` excludes the clavicle); HAND = fingers only.
- Foot+toes fold into the LEG channel; foot ground-contact lives in the orthogonal `ik_goals` layer.

**Why 9, not finer (e.g. Li et al.'s 16-part low-level scheme).** The channel set is a SEMANTIC /
retrieval / measurement / masking grouping; its granularity is pinned to the portable, engine-neutral
COARSE mask, not to a per-joint control vocabulary. Verified against the Unity API on 2026-06-23
([`AvatarMaskBodyPart`](https://docs.unity3d.com/ScriptReference/AvatarMaskBodyPart.html),
[`AvatarMask`](https://docs.unity3d.com/ScriptReference/AvatarMask.html)): the enum's 9 anatomical values —
`Root, Body, Head, LeftLeg, RightLeg, LeftArm, RightArm, LeftFingers, RightFingers` — map 1:1 onto these 9
channels, and its 4 IK entries (`Left/RightFootIK`, `Left/RightHandIK`) independently corroborate keeping IK
in the orthogonal `ik_goals` layer rather than inside an FK channel. Finer-than-part precision is NOT a
reason to add channels: AvatarMask ALSO exposes a per-transform (per-bone) mask (`transformCount`,
`SetTransformActive`, `GetTransformPath`, …), so "use only the forearm" is a per-transform mask WITHIN the
arm channel (already relied on for the clavicle), and "place the wrist/hand on a target" is an `ik_goals`
entry. Neither promotes a joint (elbow/wrist/knee) to a top-level channel — joint-level masking is an
in-channel MECHANISM, decoupled from the taxonomy. (Precision note: 9 is the coarse *body-part* mask
granularity, NOT the finest maskable unit — via the transform mask the finest unit is the individual bone.)

**Architecture (engine-decoupled).** The extractor is a PURE PYTHON program under the separate `animation-agent` repo
(`config.py` channels/bonemap/divisors/thresholds · `metrics.py` formulas · `extract.py` assembly/merge ·
`unity_sampler.py`). Python owns ALL knowledge (the body-part partition, the metric, normalization, JSON
assembly, semantic-preserving merge, run-log). Unity is touched ONLY to sample muscle clips (they have no
transform paths and must be sampled in-engine): a generic pose-sampler — generated from Python's config,
holding no KB knowledge — runs via the Unity MCP `execute_code` bridge and writes per-frame root-local
bone positions to disk; Python reads them. This matches the project thesis (the multi-agent system is
Python, decoupled from the engine); Unity MCP is used only where genuinely needed.

**Measured metrics (frozen 2026-06-18 calibration; each divisor maps its most-active reference clip to
~0.85, each static threshold sits above that channel's idle noise floor — calibration is reproducible via
`unity_sampler` + `metrics`):**
| channel | signal | divisor (ref clip) | static thr |
|---|---|---|---|
| torso | max torso-lean deg (rest~0; a held lean is engagement → MAX) | 54 (check_pulse 45.9) | 5 deg |
| head | head-vs-spine RANGE deg (removes the ~19 deg rest pedestal → RANGE not MAX) | 15.4 (giving_pills 13.1) | 4 deg |
| arms | mean bone hips-rel pos stddev m | 0.137 (check_pulse 0.1163) | 0.015 m |
| legs | mean bone world pos stddev m (in-place ⇒ world==local) | 0.247 (walking 0.2100) | 0.015 m |
| hands | mean finger CURL-angle range deg (not 15-bone position mean — that dilutes grasp to ~0) | 129 (giving_pills 110) | 5 deg |
| root | max(gait/0.317, trans/0.30, heading/60); gait = foot Y-oscillation (the measured locomotion signal) | gait: walking 0.2694 | gait 0.10 m |

Sampling: N = clamp(round(dur·fps), 2, 600) — native rate, never decimated (cpr=540 frames sampled in
full); each frame read in ROOT-LOCAL frame so the in-place world==local assumption is enforced, not assumed.

**Semantic stays human-owned.** The program writes MEASURED only. The 5-tuple
`{role, motion_type, contact, constraint, target}` + `composability` are SEMANTIC; the first v2 build
migrates copy-able semantic content from v1 (descriptions, ik_goals, a best-effort composability remap)
and leaves the 5-tuple as PENDING (`null`, flagged in `extraction.field_origin.semantic_pending`). The
program never fabricates semantics — this is what keeps "grow the KB" from re-introducing the LLM
positioning unreliability the project is positioned against.

## Consequences
+ Hands as a first-class channel with the curl-angle metric cleanly separates grasp/articulation with
  correct laterality (validated: typing both hands, grab_bottle/check_pulse/bvm right-only, giving_pills
  left-dominant; idle/cpr ~0). Walking is now distinguishable from idle AT THE MEASURED LAYER via gait.
+ The program is engine-decoupled and reproducible; the same generic sampler is callable by the future
  Python multi-agent server over the same MCP bridge.
+ A clean v1→v2 migration + a v2 contract (`motionkb.v2.schema.json` + `engine_mask_map.json` +
  `validate_motionkb.py` v2). 8/8 candidates pass.
- The clavicle's "arm" home costs Unity a per-transform mask (its body-part enum disagrees). A graded
  torso/root seam feather is a UE5/SMPL-X capability but a Unity/Blender limitation (binary masks).
- Per-leg + root are forward-declared (exercised by ~0 current clips). The 5-tuple + composability need a
  human authoring pass before candidate→accepted promotion.

## Positioning (honesty fix from the review)
Li et al. (2025) defines TWO body-part schemes, and v2 relates to each differently:
- **(a) Their 6-part BPQ rubric** (Head, Torso, Left/Right Arm, Left/Right Leg) is a human EVALUATION
  metric (Good / Partially-Good / Bad / Not-Relevant). This is the PRIOR ART v2 takes and EXTENDS — with
  hands + root + an orthogonal IK layer for the manipulation-heavy nursing domain (NOT "literature
  consensus"). The 4-valued `role` is a semantic DESIGN CONTRIBUTION (only `free`≈BPQ "Not Relevant").
- **(b) Their 16-part low-level scheme** (Head, Torso, L/R UpperArm, L/R Elbow, L/R Wrist, L/R UpperLeg,
  L/R Knee, L/R Ankle, L/R Toes) is the LLM's discrete-position GENERATION interface: each part gets a
  textual position enum (e.g. elbow `straight`/`90`/`fully`) mapped by a fixed rule-table to SMPL joint
  rotations, then scored by BPPA. That is precisely the granularity at which the paper SHOWS LLMs fail
  (BPPA 50–75 %; even high-BPPA poses yield broken animations), and it has NO finger/hand channel. v2
  deliberately does NOT adopt it as channel structure — numerics come from MEASURING real clips, not from
  an LLM specifying joint positions. The 16-part scheme is reused only as a Phase-2 BPPA-style EVALUATION
  axis (retrieval vs. LLM-generation baseline), never as the measured taxonomy.

The measured-not-generated thesis holds for all NUMERICS; categorical semantics are semantic/agent-grounded
and tagged in `field_origin` (no numeric field is ever model-produced).
