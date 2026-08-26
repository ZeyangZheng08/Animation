# 0001 — The MotionKB JSON Schema is the data contract

Status: Accepted (2026-06-18)

## Context
`motionkb/v1` lived only as prose in `agent/animation_knowledge_base/README.md`. Nothing enforced it, and the
future Python RAG (Phase 2) would have had to reverse-engineer the shape from examples — a recipe
for silent drift between the Unity extractor (producer) and the RAG (consumer).

## Decision
The machine-checkable `agent/motionkb_build/archive/motionkb.v1.schema.json` (JSON Schema 2020-12) is the
single authoritative contract; both sides code against it. Cross-field invariants JSON Schema cannot
express are enforced by a companion validator (`validate_motionkb.py` (animation-agent repo), stdlib-only), plus
`validate_guids.py` for `guid → asset` resolution — which drives the `AssetDatabase` over the Unity MCP
bridge from the agent side (it replaced the in-editor `MotionKBValidator.cs` on 2026-08-05, so no agent
code remains inside the Unity project). The README prose is a human guide only and points at the schema file.

## Consequences
+ Malformed / incomplete files are caught mechanically (reusable, auditable, handoff-able).
+ The schema is versioned (`schema/CHANGELOG.md`); changes are deliberate and recorded.
- `guid → asset` resolution still needs Unity (`-batchmode`); the pure-JSON path can't do that layer.
