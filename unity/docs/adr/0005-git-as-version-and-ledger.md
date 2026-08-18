# 0005 — Version, rollback, and decisions via git; no separate DB / hash store / ledger

Status: Accepted (2026-06-18), amended (2026-08-18) — a source-only mirror is now published, which
changes the premise below but not the decision. Read the amendment at the end.

## Context
The KB is ~8 small JSON files in a single-author repo that had never been pushed (see the 2026-08-18
amendment: it still has not been, but a derived source-only mirror has). Production-style data
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

## Amendment (2026-08-18) — a published mirror exists; git is still the ledger

The Context above rested on "never pushed". That is no longer literally true, so the premise is worth
restating rather than leaving a reader to catch it.

Neither working repository has been pushed, and neither can be: their history is full of LFS pointers,
so any push would move 3.2 GB of objects onto GitHub — which the user ruled out. What is published is a
third repository, `~/Research/pub-code`, carrying branch `code` of `github.com/ZeyangZheng08/Animation`:
source only, no 3D assets, no LFS, its own short history unrelated to either working repo's. It is built
by copying files out of the two working trees, one way, by `sync.sh`.

**The decision is unchanged, and the mirror does not become a second source of truth.** It is downstream
of the ledger, not part of it: nothing is authored there, `sync.sh` reads committed content only
(`git show HEAD:path`), and an edit made in the mirror is overwritten on the next run. A KB version is
still a tag in the Unity repo; rollback still runs there.

Two consequences worth stating plainly:

- The mirror is **not** an offsite backup. It carries no assets, no LFS objects, and none of either
  working repo's history. Losing disk `F:` still loses the KB's provenance. The single-machine risk the
  original decision accepted is exactly as large as it was.
- `git status` in the working repos remains the drift detector. The mirror adds a second place where
  drift can appear, and `sync.sh` reports it — files that vanished upstream, and upstream files that are
  new and not covered by `.pubignore`.
