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
| [0012](0012-accepted-store-in-its-own-directory.md) | The accepted records move to `kb/actions/`, so membership is the directory rather than a denylist of the shared files pasted into seven consumers; no record changed | Accepted; two-store half superseded by [0016](0016-one-store-status-is-the-membership-test.md) |
| [0013](0013-root-follows-the-legs.md) | The assembler gives the root channel to whichever part owns the legs, not to the part whose root reads `dynamic` — since 0011 four of eight do, and the in-place walk reads lowest of them | Accepted |
| [0014](0014-corpus-enters-measured-only.md) | The 2446-clip Mixamo corpus enters the KB MEASURED-only through its own bulk path; `status: candidate` is what licenses a null semantic half, so the gate can tell unlabelled from malformed | Accepted |
| [0015](0015-evidence-frames-chosen-for-coverage.md) | The frames a labeller is shown are chosen to cover the clip's pose range (k-center in muscle space), not to sit inside an "action window" — which put all three pictures inside a held pose and showed one pose three times | Accepted |
| [0016](0016-one-store-status-is-the-membership-test.md) | `candidate/` merges into `actions/`: one store, `status` is the membership test, promotion is a rename. Selecting the accepted subset reads `manifest.json`; walking the whole store reads concurrently (68 s → 6 s). Supersedes the two-store half of 0012 | Accepted |
| [0017](0017-knowledge-base-and-its-build-artifacts.md) | `agent/kb/` becomes `agent/animation_knowledge_base/` and what only the build produced moves to `agent/motionkb_build/` — split by who READS it, so the frozen evidence the runtime reads stays and the reports, archive and retired contract leave the agent's search workspace entirely | Accepted |
| [0018](0018-posture-joins-the-measured-block.md) | Every channel's MEASURED half gains a posture triple (`posture_label`/`posture_magnitude`/`posture_measurement`) — variation says what a channel MOVES, posture says what it HOLDS, so held poses stop being invisible; formula v2.2.0 → v2.3.0, all 2454 re-measured from frozen `raw` | Accepted |
| [0019](0019-calibration-corpus-derived-and-reproducible.md) | Every calibration constant refit on the full 2446-dump Mixamo corpus, offline from frozen `raw/`; REST_POSE = the median pose of the clips that MEASURE as at-rest (no name matching, no project clip) with threshold sensitivity measured; `body_height_offset_m` loses its lying `_m`; formula v2.3.0 → v2.4.0, `state_label`s bit-identical. Amended 2026-08-24 (v2.4.1): a rest observation must also run ≥ 1 s, which drops the 60 short clips and single-pose assets that had been voting on the rest pose | Accepted; REST_POSE half superseded by [0020](0020-posture-origin-is-unitys.md) |
| [0020](0020-posture-origin-is-unitys.md) | The posture origin becomes Unity's Humanoid reference pose — every muscle 0, `bodyPosition.y` 1.0, `bodyRotation` identity — instead of a rest pose fitted from the corpus: the engine fixes where zero is, the corpus only fixes scale, and idle clips stop defining the standard. Formula v2.4.1 → v2.5.0; 30.99% of posture labels move and `displaced` now means "away from the Humanoid reference", not "away from a relaxed stance"; `state_label` bit-identical; the fitted origin stays reproducible as an ablation | Accepted |

New ADRs: copy the format, take the next number, set Status, never edit an Accepted record's
decision in place — supersede it with a new ADR that references the old one.
