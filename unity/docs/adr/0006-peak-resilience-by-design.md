# 0006 — "Peak resilience" means graceful degradation by design, not QPS load-testing

Status: Accepted (2026-06-18). One mechanism below has changed shape: "write only to `candidate/`" is
now "write in place, and `assemble` skips accepted records", since ADR 0016 merged the two stores. The
property it was protecting — a re-extraction cannot damage an accepted record — still holds.

## Context
The required qualities include 能抗峰值 (peak-resilient). This project is a single-user, offline
clip-bake pipeline — it has no concurrent online traffic. Naively importing SRE "peak handling"
(QPS load tests, autoscaling, throughput capacity planning, distributed queues/brokers, circuit-breaker
frameworks, real-time monitoring/alerting) would be over-engineering with no traffic to justify it.

## Decision
Peak resilience is a DESIGN PROPERTY = graceful degradation + robustness under bursts (queued bakes),
pathological inputs (very long clips, a query that decomposes into many part-tasks, a huge retrieval set)
and resource spikes — achieved cheaply, in-process:
- **do-now** (in the extractor/validator batch): per-file failure isolation, resumable + checkpoint,
  idempotent + atomic writes, write only to `candidate/`.
- **Phase-2** (when the pipeline exists): bounded concurrency (one in-process `Semaphore(N)` covering
  both agent fan-out and request backpressure), per-stage timeout reusing the `stage_latency_ms` budget,
  input guards, and a degradation policy (fallback-to-cached-clip / best-full-clip / skip-bad-action).

Explicitly OUT OF SCOPE (not deferred — dropped): synthetic QPS load tests, autoscaling, throughput
capacity planning, distributed message brokers, circuit-breaker frameworks, real-time monitoring /
alerting, query-level canary / traffic routing.

## Consequences
+ A single offline job will not crash, lose completed work, or emit half-baked output under a peak.
+ Costs almost nothing — no infrastructure, just in-process primitives and disciplined batch code.
- Provides no throughput/capacity numbers; that dimension does not exist for this project by design.
