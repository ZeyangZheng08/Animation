# 0014 — The Mixamo corpus enters the KB measured-only, and the contract says so

Status: Accepted (2026-08-21). Adds a bulk ingestion path beside the curated one, and makes
"MEASURED complete, SEMANTIC not yet proposed" a state the schema can hold and the gate can check.
Supersedes nothing; ADR 0002 (measured/semantic separation) and ADR 0008 (a VLM proposes the
semantic 5-tuple) are what it implements at corpus scale. Where the records LAND has since changed:
[0016](0016-one-store-status-is-the-membership-test.md) merged `candidate/` into `actions/`, so read
every `candidate/<clip>.json` below as `actions/<clip>.json`. Nothing else here is affected — this
record is about what enters the KB and what licenses a null semantic half, not about which directory
holds it.

## Context

The knowledge base held eight actions, each hand-curated: registered by name, sampled, measured,
rendered, labelled by a VLM, reviewed. 2446 Mixamo clips are now imported into Unity under
`Assets/Animations/Mixamo30/`, and they are meant to become the retrieval corpus.

Two things stood in the way, and neither was about volume for its own sake.

**The contract could not express a half-finished record.** `_new_source_stub` — what `register`
writes — seeds `action_id`, `display_name` and `overall_intent` null and `tags` empty, because those
are semantic and ADR 0002 forbids machine-authoring them. The schema required all four as non-null
strings. So the very shape the pipeline produces was a schema violation, and validating it reported
four errors that meant "not labelled yet" in the vocabulary of "malformed". At eight records, worked
around by hand. At 2446 it is the whole report: 9784 errors and about 24,000 warning lines, none of
which is a defect.

The warnings came from the same place. `soft_warnings` compares `mask_coverage` and
`composability.free` against the measured `state_label` — both sides semantic declarations, both
still placeholders on a fresh stub (`mask_coverage` all false, all eight channels free). On an
unlabelled record it does not detect a disagreement, it detects the placeholder.

**The curated commands do not scale, for reasons of shape rather than size.** `register` resolves
one clip by name through `build_find_clip_csharp`, which loads every asset under `Assets/Animations`
and scans it. That directory now holds 2446 FBX, so registering the corpus one clip at a time is
2446 full sweeps of 2446 files. `assemble` writes a candidate for every source entry it can see,
including the eight accepted ones, re-staging records that are finished. And neither has a resume
rule, which a 70-minute run of engine calls needs.

## Decision

**A record may be MEASURED-complete and SEMANTIC-pending, and `status` is what says which.**

The schema makes `action_id`, `display_name` and `overall_intent` nullable and drops `minItems: 1`
from `tags`. The requirement does not disappear — it moves to `validate_motionkb.py`, attached to the
claim it belongs to:

```python
if data.get("status") != "candidate":
    for field in ("action_id", "display_name", "overall_intent"):
        if data.get(field) is None:
            errors.append(f"{field} is null, which only a status='candidate' record may be")
    if not (data.get("tags") or []):
        errors.append("tags is empty, which only a status='candidate' record may be")
```

Fail-closed: only an explicit `candidate` is exempt, so a record with no `status` at all is still
held to the full bar. The semantic half is required to **accept** a record, not to **hold** one.

`soft_warnings` gets the gate `validate_semantic_consistency` already had — inert until some channel
carries a non-null `role`. A placeholder is not a disagreement.

`validate_motionkb.py -q` prints failures and the summary only. At 2454 files a PASS line each is not
a report.

**Bulk ingestion is its own path: `ingest_corpus.py`, four verbs.**

```
index      one engine call enumerates the whole directory  -> motionkb_build/reports/corpus_index.tsv
register   pure Python, one candidate stub per indexed clip
sample     one engine call per clip, skips clips that have a dump  -> raw/<clip>.json
measure    pure Python, raw -> the MEASURED block of each stub
```

The population is the index, not "every source entry the store can see", so the eight accepted
records are never re-staged. `sample` resumes by re-running. `index` refuses outright if two clips
share a name, because clip name is what `raw/<clip>.json` and `candidate/<clip>.json` are keyed by
and a collision would silently overwrite a dump.

**MEASURED is computed by the same code as the curated path.** `ingest_corpus.py` imports
`extract._apply_measured` and `extract._build_extraction` rather than reimplementing them. Two paths
into one store must not become two dialects of one contract; the way to guarantee that is for the
block to have one definition, not two that agree today.

**The corpus pose dumps are not tracked in git.** `.gitignore` gains `raw/mx_*.json`, mirroring the
rule already written there for the FBX corpus itself.

## Consequences

**The gate covers the corpus instead of drowning in it.** 2454 files, 0 failures, and a failure now
means a defect. Proven fail-closed both ways: nulling the semantic half of an accepted record fails,
a record with no `status` and a null `overall_intent` fails, the same nulls under
`status: candidate` pass.

**The run: 2434 clips sampled in 69.9 minutes, 0 failures, 2446 measured, 0 errors.** The gate reads
2454 passed / 0 failed. `raw` is 1.5 GB.

**The divisors hold on the corpus, which was the open question.** They were fitted at p99 -> 0.85 over
150 Mixamo clips (ADR 0010, refitted in muscle space by ADR 0011) precisely because the nurse-clip
calibration saturated on ordinary locomotion. Measured now across all 2446:

| channel | p50 | p90 | p99 | clamped at 1.0 | dynamic |
| --- | --- | --- | --- | --- | --- |
| root | 0.2421 | 0.6841 | 1.0000 | 74 (3.0%) | 88.1% |
| torso | 0.2287 | 0.5216 | 0.7951 | 1 (0.0%) | 85.6% |
| head | 0.2377 | 0.5051 | 0.7262 | 0 | 89.1% |
| left_arm / right_arm | 0.27 / 0.28 | 0.59 / 0.61 | 0.84 / 0.85 | 7 / 2 | 90.7% / 91.0% |
| left_leg / right_leg | 0.39 / 0.40 | 0.67 / 0.70 | 0.87 / 0.90 | 3 / 5 | 87.1% / 87.2% |
| left_hand / right_hand | 0.13 / 0.15 | 0.56 / 0.57 | 0.86 / 0.89 | 5 / 5 | 62.0% / 63.3% |

The eight anatomical channels land their p99 within 0.05 of the intended 0.85 and clamp on 0.0-0.3% of
clips, so the field still discriminates where it matters. The root clamps on 3%, which is expected of a
`max()` over three signals rather than a single one, and is the channel least used for retrieval.

**`loop` is false on all 2446, and that is a declaration rather than a measurement.** It comes from
`ModelImporterClipAnimation.loopTime`, which the corpus import never set; a pose dump cannot show it.
So the field accurately reports that nobody has declared these clips loopable, and says nothing about
which of them plainly cycle — a walk in this corpus reads `loop: false`. `transitions.load_clip` takes
`loop` from the record, so seam search will treat every corpus clip as one-shot until it is decided.
`motionkb_build/reports/corpus_ingest.md` states this rather than leaving it to be found.

**156 records measure fully static, of which 127 are the 2-frame pose assets.** Those two counts were
one until the corpus produced a counterexample: `mx_Arms_Supporting` is 2 frames whose poses differ, so
its right leg measures 0.0638 and dynamic. Short is not still, and the remaining 29 are full-length
clips that move less than the 0.02 threshold on every channel. The report carries both counts.

**2446 records are retrievable on MEASURED alone.** Duration, frame rate, loop, and the 9-channel
`state_label` / `motion_magnitude` / `raw_measurement` block are complete for every clip. What is
absent is every categorical label: `action_id`, `role`, `motion_type`, `contact`, `constraint`,
`target`, `composability`, `tags`. So the corpus can answer "which clips have dynamic legs and a
static torso" and cannot yet answer "which clips are walking" — retrieval by measurement, not by
meaning. Filling that is the next pass and deliberately not part of this one.

**`raw` for the corpus is ~1.4 GB and lives only on this machine.** The dumps are JSON at about
5.6 KB per sampled frame, and the corpus sums to roughly 247,000 frames. Tracking them would add
some 600 MB packed to a repository whose LFS objects already total 3.2 GB and which therefore cannot
be pushed (ADR 0005). The FBX corpus they derive from is untracked for that same reason, so tracking
the dumps would preserve only half of a reproduction chain that is already broken at its source.
When a clip is promoted into `actions/`, its FBX is `git mv`-ed into a tracked folder and its dump is
added in the same commit — the existing invariant, unchanged.

The candidate records themselves ARE tracked: ~6 KB each, about 15 MB total, and they are the
knowledge base's content rather than an intermediate.

**The derived tables stopped hashing the whole of `raw`, because they never read the whole of it.**
`transitions.raw_fingerprint` is what tells a cached seam or segment table that its inputs moved, and
every `read_table` calls it. It hashed the bytes of every file in `raw` — fine at 16 MB, and measured
at **52 s per process** once the corpus took the directory to 1.4 GB, on a path `agent/tools/kb.py` and
`agent/tools/scene.py` both use. Its own first sentence already named the right set: "every `raw` dump
**the table was derived from**", which is the accepted store's clips and not the directory. It now reads
those names off `actions/`, which costs 0.56 s and needs no rule about `mx_` prefixes — promote a corpus
clip and its dump joins the fingerprint at the moment it starts mattering. An explicitly passed
`raw_dir` is still hashed whole: that corpus is the caller's and nothing here knows which part of it
they meant. Both derived tables were regenerated so their stored fingerprints match.

**`grep` stopped reading `raw`, and stopped telling the model the corpus is small.** The
model-facing file tools mount the whole KB directory, deliberately — scoped wide enough that a miss is
evidence rather than a search that fell short. `raw` is inside that mount, so every `grep` read it: at
0.53 GB, 9.3 s measured, and about 24 s at full size. It buys nothing. The only words in a 600 KB dump
are the fifty bone names and the ninety-five muscle names, each once; everything else is per-frame
floats. `grep` now skips any path with a `raw` segment, reports how many dumps it did not read, and
searches them anyway when a caller names a path inside `raw/`. `glob` and `read` are unchanged, so
"has this clip been sampled" stays answerable. The exclusion is keyed on the DIRECTORY, not on the
`mx_` prefix or on size: what makes a dump unsearchable is what it holds, which was equally true of the
eight original dumps — it just did not cost anything to be wrong about.

The same call carried a claim that the corpus removed. On no match it said *"The corpus is small, so
absence here is real."* That sentence exists for a good reason — a miss the model cannot trust is a
miss it rephrases around until it hits the iteration limit — but its premise stopped being true at 2454
records. It now reports the count of files actually searched and the two things that would widen the
search, which is the same guarantee resting on something checkable.

**The eight accepted records are untouched.** They were not re-staged, re-sampled or re-measured;
the golden re-extraction regression still recomputes their MEASURED from their own frozen dumps and
still matches. `manifest.json` covers the accepted store only and is unaffected.

## Alternatives considered

**A conditional schema (`if status == accepted then required: [...]`).** The honest JSON Schema
expression of this rule, and it keeps the whole contract in the contract file. Rejected because
`validate_motionkb.py` implements a deliberate subset of JSON Schema — `type / const / enum /
required / properties / additionalProperties / items / $ref` — and `if/then` is a substantially
larger evaluator than the rule is worth. The invariants half of the validator is where cross-field
rules already live (locks/free partition, overlay disjointness, ik effector resolution); this is one
more of those.

**Ingest into a third directory, `corpus/`, beside `actions/` and `candidate/`.** Attractive because
2446 records in `candidate/` reads like a review queue nobody could ever work through. Rejected
because it would split a homogeneous population on volume alone: every one of these records is in
exactly the state `candidate/` names — measured, not accepted — and they will all leave it the same
way, through one semantic pass. ADR 0012 put membership in the directory; adding a directory that
means the same thing as an existing one takes it back out.

**Add `--corpus` flags to `extract.py`.** Fewer files, and it keeps one entry point. Rejected
because the two paths disagree about what the population is (the index vs. the union of both stores),
about resumption, and about whether the run ends in a semantic proposal. Threading both through one
set of verbs makes each read as a special case of the other.

**Drop the 128 single-pose clips.** Mixamo ships pose assets — `mx_Arms_Down`, `mx_Arms_Behind_Back`
— that resolve to 2 frames. They measure honestly: every channel reads 0.0 and static. Rejected
because a pose is a legitimate thing to retrieve and a silent filter is exactly the kind of quiet
truncation that later reads as full coverage. They are ingested and counted in
`motionkb_build/reports/corpus_ingest.md`.
