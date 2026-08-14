# 0005 — Version, rollback, and decisions via git; no separate DB / hash store / ledger

Status: Accepted (2026-06-18)

## Context
The KB is ~8 small JSON files in a single-author repo that has never been pushed. Production-style data
versioning (object store, content-hash reconciliation, append-only promotion ledger) was considered and
rejected as over-engineering for this scale.

## Decision
git IS the change detector, decision log, and rollback mechanism. A KB version = a `git tag kb/<ver>`
plus a human `CHANGELOG.md`; rollback = `git checkout kb/<ver> -- agent/kb/`. Candidate vs
accepted is a `status` field + a `candidate/` directory; promotion is a `git mv` + status flip whose
COMMIT MESSAGE is the decision record. A `kb_manifest.json` indexes identity/provenance (action → file →
kb_version → extractor_git_sha) but stores NO per-entry content hash. If a content hash is ever needed,
compute it on demand in the validator — do not store-and-reconcile it as a standing invariant.

## Consequences
+ No bespoke infrastructure duplicating git; out-of-band edits surface in `git status`.
+ Promotions are dated, authored, diffable, and tamper-evident for free.
- Revisit only if multiple writers or an automated promotion gate appear (then a machine-readable
  decision trail may be warranted).
