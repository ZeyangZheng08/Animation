# 0002 — Separate MEASURED from SEMANTIC fields

Status: Accepted (2026-06-18)

**Terminology (2026-07-01):** 'AUTHORED' was renamed to 'SEMANTIC' for accuracy — the half is agent-neutral (mostly VLM-proposed / program-derived, not human-authored); no behavior change.

## Context
Each action JSON mixes numbers measured in-engine (magnitudes, duration, root motion) with
human-semantic, screenshot-verified semantics (descriptions, tags, targets, composability,
mask_coverage). The KB must be able to "grow" (re-extract / add actions) without destroying the
semantic work, and without re-introducing the LLM positioning/description unreliability this project
is explicitly positioned against (numerics come from retrieval/measurement, not generation).

## Decision
The extractor only ever (re)writes MEASURED fields — `motion_magnitude`, `duration`, `frame_rate`,
`loop`, `has_root_motion`, `root_displacement_m`, and the `state_label` / `is_static` threshold. It
NEVER clobbers SEMANTIC fields — `motion_description`, `overall_intent`, `tags`, `target`,
`interaction_object`, `faces_target`, `ik_goal`, `composability`, `controller_*`, `source_clip.*`, and
notably `mask_coverage` (it encodes design intent — which mask region the action drives — not a
measured quantity). A `field_origin` map records the classification per file (module D).

## Consequences
+ Re-extraction is idempotent and safe; growing the KB never rewrites human descriptions.
+ Numbers are reproducible from the source clip; prose stays human-owned and verified.
- The extractor must read-merge existing files, not overwrite them wholesale.
