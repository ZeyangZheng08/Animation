# 0003 — Fixed skeletal 6-part split + objective per-part metrics, measured in-engine

Status: Accepted (2026-06-18)

## Context
The body-part decomposition must be principled and reproducible, not an LLM's guess. The clips are
Humanoid muscle curves (no transform paths), so the data cannot be read from the `.anim` text.

## Decision
The 6 parts (`head, chest, left_arm, right_arm, legs, feet`) are a FIXED mapping onto Unity
`HumanBodyBones`, applied identically to every clip via `Animator.GetBoneTransform`. Magnitudes are
MEASURED by sampling each clip at 30 even frames (`AnimationMode.SampleAnimationClip`) and computing a
per-part objective signal: legs/feet = per-bone WORLD-position stddev / 0.30 (clips are in-place, so
world == character-local — this avoids hips-relative inflation when the pelvis leans, e.g. planted feet
in `check_pulse`); arms = hips-relative stddev / 0.30; chest = max torso-lean deg / 60; head =
head-vs-torso deg / 50. The bone map and divisors are named DATA constants (`BodyPartBoneMap.cs`,
`MotionMetricConfig.cs`), not prose buried in a one-off script.

## Consequences
+ The split is deterministic and the numbers are recomputable by anyone from the recorded formula.
+ Ground-contact parts read correctly under pelvis lean (why world-position beat hips-relative).
- Sampling has float drift across Unity versions; regression uses a tolerance band, not equality.
