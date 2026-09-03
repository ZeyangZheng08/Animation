# 0023 — The general library is the knowledge base

Status: Accepted (2026-09-02). The 2446-clip Mixamo corpus is accepted as the project's only formal
MotionKB — `status: accepted`, `action_id` equal to the clip name, schema `motionkb/v4` unchanged —
and the eight hand-authored nursing actions are moved out of the store entirely, to
`agent/nursing_assets/`, where nothing reads them. Amends
[0014](0014-corpus-enters-measured-only.md), whose `candidate` status was the licence for a null
semantic half and has now been discharged, and
[0016](0016-one-store-status-is-the-membership-test.md), whose membership test is unchanged and
whose answer is now every record in the store.

## Context

The corpus was measured in August, rendered on 2026-08-27, and described the same day by
`qwen3.8-27b` reading each clip's eight-view ring. That left 2454 records in one directory: eight
accepted nursing actions and 2446 candidates that were complete in every field the schema asks for
and were held at `candidate` only because nobody had said the word.

**Two libraries in one store is not a state anything can be built on.** The runtime read the eight;
the corpus sat beside them, indexed by nothing. Every measurement the project had — retrieval scores,
composition coverage, the seam table, the transition eval — was over eight clips, and the eight were
the same eight the system was demonstrated on. That is the shape of a result nobody outside the
project can read: it does not separate a library that can be searched from a library small enough to
enumerate.

**And the eight were also the intended evaluation.** The clinical claim this project makes is that a
nursing action can be assembled from a general library, and the eight nursing clips are the reference
performances that claim would be scored against. They were, at the same time, sitting in the index
the retrieval searched. `retrieval_eval_set.json` names `walking`, `typing`, `cpr` and `grab_bottle`
as the correct answers to twelve requests — over a corpus that contained them. An evaluation whose
answers are in the library it is scoring is not held out; it is a lookup with a rubric.

Both problems have one move behind them, and neither is solved by a flag. A denylist in the index
would leave the records where the pipeline, the gates, the file tools and the tests can all still see
them, and every one of those is a path by which a held-out clip gets back into the thing holding it
out. The store has to be the general library. Physically.

## Decision

**The MotionKB is 2446 general-purpose Mixamo clips. There is no second kind of record in it.**

1. **All 2446 become `status: accepted`.** Nothing else in a record changed: not a kinematic value,
   not a description, not `extractor_version`, not `metric_formula_version`. Acceptance is the
   decision ADR 0014 deferred, taken now that the semantic half it was waiting on is written.
2. **`action_id` is the clip name.** `mx_Standing_To_Sitting_Transition` is already unique, already
   what the frozen dump and the frames directory are keyed by, and already what Unity's `ClipLibrary`
   resolves. Naming 2446 clips by hand would invent 2446 opportunities to disagree with the asset;
   the describer stopped proposing names for exactly this reason (ADR 0022), and the acceptance pass
   does not restart it.
3. **The eight nursing records leave the knowledge base** for `agent/nursing_assets/` in this
   repository — records, frozen dumps, rendered frames, and the two derived tables that covered those
   eight and only those eight. Their FBX and `.anim` stay in `Assets/Animations`, because they are
   scene assets and the scenes reference them. What changed is that the agent cannot see them.
4. **Nothing reads that directory**, and this is the claim that has to survive: not the runtime, not
   the BM25 index, not the system prompt, not the agent's `kb/` and `source/` file mounts, not the
   offline pipeline, not the five gates, not the test suite. `source/` mounts
   `Assets/Animations/Mixamo30` specifically, and not `Assets/Animations`, which also holds the
   nursing FBX and the rigs.
5. **The eval that scored them is archived, not deleted** —
   `agent/legacy/eval_8_actions/retrieval_eval_set.json` here, `legacy/eval_8_actions/run_eval.py` in
   the agent repository, each with a README saying it does not run. It is the method behind numbers
   that are already written up, and deleting it would leave those numbers with nothing behind them.
6. **A held-out nursing evaluation has still to be built, and those twelve cases are not it.** They
   were written against clips retrieval could see. What they measure is whether the channel partition
   is derived correctly; what is needed is whether the library can be searched for something it was
   never shown, with a case set written without reference to the eight.

### What a corpus this size changed about the runtime

Nothing about the schema, and a great deal about what is affordable.

- **Loading.** `KBIndex.load` walked the store directory and opened every file, 4.13 s for 2446
  records. It reads `manifest.json` and opens only what is listed: **0.52 s**. A search over the
  loaded index is ~18 ms. Retrieval never touches the disk after start-up; only `frames` JPEGs are
  read on demand.
- **Seams are searched, never tabulated.** `derived/transitions.json` was a complete table over
  eight actions — 56 ordered pairs. Over 2446 it is **5,981,970** ordered pairs and roughly seven
  CPU-hours, and almost none of them would ever be asked for. There is no table. The seam is computed
  when a pair is actually named, and cached in a bounded LRU keyed by
  `(from, to, algorithm version, SHA-256 of each dump)`, so a re-measured clip invalidates its own
  entries and nothing else. `build_transitions.py` verifies the search on a sample; it builds nothing.
- **Coverage figures are samples and say so.** `probe_pairs.py` and `probe_compose.py` take
  `--pairs` and `--seed`. A number reported over 2446 actions without those two words beside it would
  be a sweep that never ran.

### What it changed about the tools

The agent used to see five tools over a library it could enumerate, where retrieval was a lookup and
the only real decision was how to arrange two clips. It now sees **thirteen, in three families**:
Search (`motion_search`, plus `glob`/`grep`/`read` over the `kb/` and `source/` mounts), Motion
Analysis (`motion_channels`, `motion_timing`, `motion_compose`, `motion_transition`), and Unity
(`unity_query`, `unity_measure`, `unity_locomotion`, `unity_validate`, `unity_execute`).

The division is the architecture rather than a filing convenience. The four analysis tools touch no
engine: composition, timing and seam geometry are decided from frozen measurements, so a plan is
settled before anything is asked of the runtime and a wrong one costs a tool call rather than a
character crossing a room. And finding a clip in 2446 is a search whose result has to be READ before
the next question can be asked — which is why the default model moves to `gpt-5.6-terra` on
`/v1/responses`, with the realtime backend kept beside it as the comparison arm rather than deleted.

## Consequences

**The gates are five and they cover the store.** validate **2446 / 2446** against
`motionkb.v4.schema.json` including description completeness on every accepted record, golden
re-extraction **16 / 16** from frozen `raw`, `manifest.json` in sync at 2446, the posture sidecar
current at 2446 (ADR 0024), and guid resolution **40 / 0** on a deterministic sample with `--all` for
the run after a reimport. The golden set grew from 8 to 16 because the old one was the eight nursing
clips: it is now a fixed subset spanning standing, walking, sitting, both sit/stand transitions,
crouching, kneeling, bending, crawling, lying, airborne and two of the 128 single-frame pose assets.

**Every number measured over eight actions is history.** The retrieval eval's 7/12, the composition
coverage figures, the 56-pair seam table, the two-arm latency comparison — all of them were taken
over a library that no longer exists. They are kept where they were written, labelled, and they are
not evidence about the current system. Re-measuring is a separate piece of work and the held-out
nursing evaluation is the first part of it.

**The test suite needed a vocabulary.** Tests used to name `walking`, `typing` and `cpr` directly,
which made every one of them a statement about the nursing eight. `tests/corpus.py` names
clips by the property each is there FOR — `WALK`, `IDLE`, `POSE`, `SEATED`, `FLOOR`, `SIT_DOWN`,
`STAND_UP`, `CYCLIC` — so a test says what it needs rather than which clip it happened to be written
against, and `has_nursing_content` asserts the isolation directly.

**Two-frame pose assets are in the library and behave like clips.** 128 of the 2446 records are
single Mixamo poses sampled at two frames, and `mx_Walking` is one of them — which is exactly the id
somebody reaches for when they want a walk. So the runtime primitives are named options
(`--locomotion-action`, default `mx_Walking_Forward`; `--idle-action`, default `mx_Standing_Idle`)
and both are checked at start-up: the walk must be an animation, must move the root and both legs,
must be performed standing, and must loop without a visible jump. A failure is a `SystemExit` naming
the option to change, rather than a T-pose discovered at commit time.

**The store is 2446 files and one directory, and it will grow the same way.** A new clip is an FBX,
a dump, a ring, a record and a manifest entry, in one commit in this repository. Nothing about that
path is different for the two-thousand-four-hundred-and-forty-seventh.
