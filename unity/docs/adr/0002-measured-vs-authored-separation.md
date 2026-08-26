# 0002 — Separate KINEMATIC from SEMANTIC fields

Status: Accepted (2026-06-18)

**Terminology (2026-07-01):** 'AUTHORED' was renamed to 'SEMANTIC' for accuracy — the half is agent-neutral (mostly VLM-proposed / program-derived, not human-authored); no behavior change.

**Terminology (2026-08-25, [ADR 0021](0021-kinematic-facts-not-classifications.md)):** 'MEASURED' was renamed to 'KINEMATIC' for the same kind of reason — the old name described provenance and said nothing about content, and a label produced by a program reads as measured even when what was measured is the program's own threshold. The half holds joint motion and joint pose; it is called that now. No behavior change, and the split this ADR draws is unaffected.

## Context
Each action JSON mixes numbers measured from the engine's pose sampling (magnitudes, duration, root motion) with
human-semantic, screenshot-verified semantics (descriptions, tags, targets, composability,
mask_coverage). The KB must be able to "grow" (re-extract / add actions) without destroying the
semantic work, and without re-introducing the LLM positioning/description unreliability this project
is explicitly positioned against (numerics come from retrieval/measurement, not generation).

## Decision
The extractor only ever (re)writes KINEMATIC fields — `motion_magnitude`, `duration`, `frame_rate`,
`loop`, `has_root_motion`, `root_displacement_m`, and the `state_label` / `is_static` threshold. It
NEVER clobbers SEMANTIC fields — `motion_description`, `overall_intent`, `tags`, `target`,
`interaction_object`, `faces_target`, `ik_goal`, `composability`, `controller_*`, `source_clip.*`, and
notably `mask_coverage` (it encodes design intent — which mask region the action drives — not a
measured quantity). A `field_origin` map records the classification per file (module D).

## Consequences
+ Re-extraction is idempotent and safe; growing the KB never rewrites human descriptions.
+ Numbers are reproducible from the source clip; prose stays human-owned and verified.
- The extractor must read-merge existing files, not overwrite them wholesale.
