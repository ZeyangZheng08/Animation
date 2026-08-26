# 0016 — One store; `status` is the membership test, not the directory

Status: Accepted (2026-08-21). The store moved the same day: it is
`agent/animation_knowledge_base/actions/` since [0017](0017-knowledge-base-and-its-build-artifacts.md),
and `kb_manifest.json` is `manifest.json`. Nothing decided here changed with it. Supersedes the
two-store half of
[0012](0012-accepted-store-in-its-own-directory.md) and the `candidate/` half of
[0014](0014-corpus-enters-measured-only.md). Keeps 0012's actual decision: action records live in a
directory of their own, so membership is read off the path rather than asserted with a denylist.
No record's content changed.

## Context

[0012](0012-accepted-store-in-its-own-directory.md) moved the accepted records out of the KB root into
`actions/`, and left the staging area `candidate/` beside it. Two directories, and the choice between
them was `status` written a second time: `actions/` held `status: accepted`, `candidate/` held
`status: candidate`, and nothing else was ever true.

That was cheap while `candidate/` was an empty staging area holding one clip at a time. It stopped
being cheap when [0014](0014-corpus-enters-measured-only.md) put 2446 measured-only records in it. The
store became 8 files in one directory and 2446 in another, and the split cost:

- **Two places to look for one clip.** Six call sites walked both and deduped by `clip_name`; every
  one of them had to know which store wins. `extract._source_files` documented the rule as "candidate
  overrides", `ingest._known_clip_names` as a labelled dictionary, `validate.collect_files` as
  "candidates first so a staged file is reported before the record it will replace".
- **A window where one clip had two records.** `propose` wrote `candidate/<clip>.json` while
  `actions/<action_id>.json` still stood; between those two moments the KB held two documents for one
  animation and a reader had to know which was live.
- **A directory argument at every call site that meant a status.** `paths.action_files(d)` took a
  directory precisely so the caller could pick a store, and the choice was never about where files sat.

And it did not answer the question a reader actually asks. "Where does this clip's record live" has one
right answer and it should not depend on how far the clip has got.

## Decision

**One store: `agent/animation_knowledge_base/actions/`, holding every action record whatever its status. `status` is the
membership test.**

A record is named by its key: `<clip_name>.json` while it is unlabelled, `<action_id>.json` once
PROPOSE has decided what it means. **Promotion is a rename inside one directory** — status flips to
`accepted`, and the filename follows because the record's key changed, not because it moved house.
The 2446 corpus records keep their names and the eight accepted ones keep theirs.

**Selecting the accepted subset goes through `manifest.json`.** With one store the question is
about 2454 records' contents, and the answer has to be cheap for the agent, which asks it at every
start. The manifest already indexed exactly this subset (`gen_kb_manifest.py`, HANDOFF §8 module C)
and `gen_kb_manifest.py --check` is already gate 3 of `check_kb.sh` — so `paths.accepted_files()`
reads it as the index it is. The manifest is found next to the store rather than at a fixed path, so
redirecting the store redirects its index with it. No manifest means no index yet and the store is
read and filtered; a manifest that names a file the store does not hold raises, because that is the
one direction of staleness nothing else catches.

**Walking the whole store goes through `paths.read_records()`, which reads concurrently.** The KB is
reached over DrvFs from WSL, where opening a file costs about 28 ms whatever its size. 2454 files is
14 MB and 68 s one at a time; 32 threads bring it to 6 s and more threads do not help. This is not an
optimisation, it is what makes a full-store pass usable: the gate run went from over a minute to
**15 s**, and three lookups in `extract.py` that each opened every record to find one clip now share
one pass.

**`extract.py assemble` skips accepted records.** With one store it now walks them, and writing there
would silently re-measure the eight the KB is built from. That is `recalibrate_measured.py`'s job —
deliberate, dry-runnable, MEASURED-only — and the old two-store layout enforced it by accident.

## Consequences

The KB root is one directory shorter and no consumer chooses a store any more:

| | before | after |
|---|---|---|
| directories holding records | `actions/` (8) + `candidate/` (2446) | `actions/` (2454) |
| "which store does this clip live in" | six call sites, each with its own rule | one lookup |
| one clip, two records | possible, during propose | impossible |
| full gate run (`check_kb.sh`) | over a minute | **15 s** |
| whole-store read from WSL | 68 s | 6 s |

Gates after the move: validator **2454 passed / 0 failed**, golden re-extraction **8/8**, manifest in
sync (byte-identical — the accepted records keep their relative order in a sorted store), guid→asset
**8 resolved / 0 failed** live. Agent suite **349 passed**; it was 350 because
`test_register_never_forks_a_record_that_already_exists` was parametrised over the two stores and
there is now one. That test keeps the case that matters by giving the existing record a filename that
is not its clip name — an accepted record is named after its `action_id`, so a check that went by
filename would miss exactly the collision that costs the most.

**The store no longer says at a glance how much of it is labelled.** `ls` used to answer it. Now
`manifest.json` does, `ingest_corpus.py status` prints it, and `kb_search` only ever sees the
accepted subset regardless. That is a real loss of a free signal, accepted because the signal was a
side effect of a layout that cost more than it told.

**`paths.accepted_files()` trusts a derived file.** If the manifest is stale in the direction of
listing too few, retrieval silently returns less. Gate 3 rebuilds it from the store and compares, so
the window is one gate run wide; the other direction raises.

## Alternatives considered

**Leave the two stores.** The layout was defensible when `candidate/` was a staging area. What made it
stop working was 2446 records arriving in it — a staging area that nobody will ever work through is
not a staging area, it is the store with a second name.

**Merge, and select the accepted subset by reading the store.** Correct and needs no index, but it is
6 s at every agent start even threaded, against an index read that is instant. Measured before
choosing: 68 s serial, 6 s at 32 workers, 1.25 s if the same files are read from Windows rather than
across the mount — the cost is the mount, and no amount of arranging files avoids it.

**Merge, and encode status in the filename.** A prefix or a suffix would make selection a glob. It
also puts a mutable fact in the name, so every status change is a rename, and it competes with the
naming rule that already carries meaning — a record is named by its key.

**Also hoist the boilerplate every record repeats.** `extraction.method`, `sampling_rule`,
`motion_metric`, the four version strings, `field_origin`, and the per-channel
`raw_measurement.signal`/`divisor` are byte-identical in all 2454 records: measured at **3.7 MB of the
store's 14.5 MB, 26%**. Not done. It is a `motionkb/v3` contract change for a saving in the dimension
that does not hurt — what costs is 2454 file opens, not 14 MB — and it would put facts about a record
somewhere other than in the record, which is the property that makes one file readable on its own.
