# Architecture Decision Records (ADR)

Short, durable records of the load-bearing decisions behind this project, in the Nygard format
(Status / Context / Decision / Consequences). They make the "why" auditable and survive handoff.
See `../../HANDOFF.md` §8 for the engineering design principles and staged roadmap these decisions implement.

| # | Decision | Status |
|---|---|---|
| [0001](0001-data-contract-first.md) | The MotionKB JSON Schema is the contract; engine and RAG both code to it | Accepted |
| [0002](0002-measured-vs-authored-separation.md) | Separate MEASURED (re-extractable) from SEMANTIC (never clobbered) fields | Accepted |
| [0003](0003-skeletal-split-and-metrics.md) | Fixed 6-part skeletal split + objective per-part metrics, measured in-engine | Superseded by [0007](0007-v2-body-part-split.md) |
| [0004](0004-mask-layer-disjoint-only.md) | mask+layer co-playback is valid only for disjoint locks; true synthesis is Phase-2 | Accepted |
| [0005](0005-git-as-version-and-ledger.md) | Version / rollback / decisions via git; no separate DB, hash store, or ledger | Accepted |
| [0006](0006-peak-resilience-by-design.md) | "Peak resilience" = graceful degradation by design; QPS / online-ops out of scope | Accepted |
| [0007](0007-v2-body-part-split.md) | v2 body-part split (9 channels) + Python-side reproducible extractor; supersedes 0003 | Accepted |
| [0008](0008-vlm-proposed-authored-fields.md) | A VLM proposes the SEMANTIC 5-tuple; consistency-check-gated, human review optional (since 2026-06-25); numerics stay MEASURED | Accepted |
| [0009](0009-check-before-you-play.md) | A plan is played on a hidden duplicate and judged before the visible character moves; protocol v4 | Accepted |
| [0010](0010-divisors-refitted-on-the-corpus.md) | Divisors refitted on the 2446-clip corpus at p99, and root translation/turning measured from the hips instead of the motionless root transform; `metric_formula_version` v2.1.0; state_label and SEMANTIC untouched | Accepted |
| [0011](0011-measure-in-unity-humanoid-muscle-space.md) | MEASURED moves into Unity's normalised Humanoid space (muscles + bodyPosition), making the numbers avatar-independent; `metric_formula_version` v2.2.0; 15 state_label flips reviewed one by one | Accepted |
| [0012](0012-accepted-store-in-its-own-directory.md) | The accepted records move to `kb/actions/`, so membership is the directory rather than a denylist of the shared files pasted into seven consumers; no record changed | Accepted |
| [0013](0013-root-follows-the-legs.md) | The assembler gives the root channel to whichever part owns the legs, not to the part whose root reads `dynamic` — since 0011 four of eight do, and the in-place walk reads lowest of them | Accepted |

New ADRs: copy the format, take the next number, set Status, never edit an Accepted record's
decision in place — supersede it with a new ADR that references the old one.
