# MotionKB — Body-Part-Level Motion Knowledge Base

MotionKB is an engine-agnostic library that describes each animation clip **at the body-part level** —
one JSON file per action. It's the canonical store the project's retrieval system reads from: instead
of treating an animation as one indivisible block, a planner can ask "which clip has the legs walking
and the right hand reaching?" and reason about individual parts.

Everything here is plain JSON. Unity is used only to *sample* the clips (to find where the bones are);
all the knowledge lives in these files, outside any engine.

**All 2446 records live in one directory and all of them are `accepted`** — general-purpose Mixamo
clips, measured, rendered and described, and since 2026-09-02 the whole of the formal library
([ADR 0023](../../docs/adr/0023-the-general-library-is-the-knowledge-base.md)). `status` is still the
field that says how far a record has got, and the mechanism for a `candidate` is still here for the
next clip that arrives measured and wordless; today nothing is one.

A record is named by its key, and for every record in this store the key and the `action_id` are the
same string: `mx_Sneaking.json` holds `action_id: "mx_Sneaking"`. An `mx_*` name is already unique,
already what the raw dump and the frames directory are keyed by, and already what Unity's
`ClipLibrary` resolves, so accepting the corpus invented no names for it.

**The eight hand-authored nursing actions are not here.** They are in `../nursing_assets/`, and nothing
reads that directory — not the runtime, the BM25 index, the prompt, the agent's search workspace, the
pipeline, the gates or the tests. They are held out for a nursing evaluation that does not exist yet,
and an evaluation whose clips were ever visible to retrieval would not be held out.

```
animation_knowledge_base/          everything a CONSUMER reads
├── actions/                all 2446 records, every one accepted
├── raw/                    frozen per-frame pose dumps — every KINEMATIC number comes from these,
│                           and the seam search and the posture sidecar still read them at runtime
│                           (~1.4 GB, deliberately untracked)
├── frames/                 rendered evidence frames — every description was read off these
│                           (~3.5 GB, deliberately untracked)
├── derived/                segments.json (per channel, the frames it moves in) and posture.json
│                           (four coarse states per clip over time, plus each clip's travel), from raw/
├── manifest.json           index of the ACCEPTED records
├── engine_mask_map.json    9 channels -> Unity AvatarMask
├── schema/                 the JSON Schema contract every record is validated against
└── README.md               this file

../motionkb_build/                 everything that exists only because the KB was BUILT
├── reports/                run reports + the corpus enumeration `sample` resumes from
└── archive/                superseded records and the retired v1 contract, kept for audit

../nursing_assets/                 FROZEN, READ BY NOTHING — the 8 nursing actions and their evidence
../legacy/eval_8_actions/          the retired eval set that scored those eight; it does not run
```

**The split is by who reads it, not by how it was made**
([ADR 0017](../../docs/adr/0017-knowledge-base-and-its-build-artifacts.md)). `raw/` and `frames/` are
build outputs, but they are also the evidence behind every number and every description, and the runtime
reads them — so they are knowledge. A run report is not. Nothing at runtime opens `motionkb_build/`,
and the agent's search workspace does not mount it: a search must never return a superseded record,
and moving it out of reach is a stronger answer than skipping it by name.

`actions/` is its own directory so that membership is the path rather than a list every consumer has
to keep in sync ([ADR 0012](../../docs/adr/0012-accepted-store-in-its-own-directory.md)); there used to
be a second store, `candidate/`, and it repeated in the path what `status` already said
([ADR 0016](../../docs/adr/0016-one-store-status-is-the-membership-test.md)).

**Which are accepted is answered by `manifest.json`, not by counting files.** Opening 2446 records
to ask costs 68 seconds across the mount this KB is reached through (6 with concurrency); the manifest
indexes exactly that subset, `KBIndex.load` reads it rather than walking the directory (4.13 s → 0.52 s),
and `check_kb.sh` gate 3 is what keeps it honest.

> **Status:** 2446 accepted Mixamo clips, measured, rendered, described and named by their own clip
> names. The descriptions landed 2026-08-27, written by `qwen3.8-27b` on HPC; acceptance was
> 2026-09-02. This README is the human overview; pointers to the deeper docs (and version/rollback
> details) are at the end.

> **What this library does NOT record, and why (`motionkb/v4`, 2026-08-26).** It says nothing about
> how two actions combine, what a hand is holding, where an IK goal should pin, or which body part
> "matters" in a clip. Until v4 it said all four — every channel carried a `role`, a `contact` and a
> `constraint`, and every record carried `ik_goals` and a `composability` block. Those were deleted,
> because each of them is a claim about a COMBINATION written onto a description of one clip:
> `walking`'s arm swing is incidental when she is carrying something and is the point when she is
> not, and the clip is identical either way. The runtime agent decides them per request, with the
> task and the scene in front of it. See
> [ADR 0022](../../docs/adr/0022-the-kb-describes-the-agent-decides.md).

## Why body-part level

An avatar shouldn't be driven as whole, monolithic clips. Describing each animation part-by-part lets a
planner reason about transitions and composition. Going from `walking` to `giving_pills`, for example,
the legs stop and plant, the torso steadies and leans toward the patient, the head turns to look, the
hand shapes into a pinch, and only then does the arm perform the hand-off — five things, in different
parts, at different moments.

## The 8 nursing actions — moved out, kept here for reference

These are the eight hand-authored clips the pipeline was built on. They are **not in this store** any
more: since [ADR 0023](../../docs/adr/0023-the-general-library-is-the-knowledge-base.md) they live in
`../nursing_assets/`, read by nothing, held out for a nursing evaluation. The table stays because they
are still the project's reference performances and the scenes still play them.

| action           | what it is                        | carriage |
| ---------------- | --------------------------------- | -------- |
| `idle`         | standing at rest                  | standing |
| `walking`      | walking gait (in place)           | standing |
| `typing`       | seated, typing at the computer    | seated   |
| `giving_pills` | handing medication to the patient | standing |
| `cpr`          | chest compressions                | standing |
| `grab_bottle`  | picking up the medicine bottle    | standing |
| `check_pulse`  | checking the patient's pulse      | standing |
| `bvm`          | bag-valve-mask ventilation        | standing |

All eight are Humanoid clips (Unity's retargetable, skeleton-independent animation format) remapped onto
one shared character rig (`nurse_avatar.fbx`), and they play in place. The carriage column is not stored
— it is derived. It used to be a threshold on the root channel's measured `mean_body_height` (0.647 for
the seated clip, 0.859 or higher for every standing one); since
[ADR 0024](../../docs/adr/0024-kinematic-posture-states-and-seat-alignment.md) posture is a segmentation
over time computed from the frozen dumps, because one number per clip cannot describe a clip that stands
up. Whether an action is "a base" or "an overlay" is not recorded at
all any more: `walking` is a base under a carry and an overlay under `cpr`, so it was never a property
of the clip.

## The core idea: kinematic vs. semantic

Each action splits cleanly into two kinds of information:

- **Kinematic** — *the numbers, computed by a program from the real motion.* How much each part moves,
  whether it's static or dynamic, the average pose it sits in joint by joint, the duration, the frame
  rate. These are read straight off the animation; no human or AI ever edits them. Same clip in → same
  numbers out.
- **Semantic** — *the description, in plain language,* proposed by a **VLM** (a vision-language model
  that can look at rendered frames of the motion). One sentence for the whole action, and one for each
  body part: what it does, as a person watching would say it.

That split is the whole point: numbers come from *measuring* real clips, never from a language model
guessing them — language models are unreliable at precise body positioning, a finding this project is
built on. The model is trusted only to say what it saw.

**And the semantic side stops at describing.** It does not say which part is the important one, what a
hand is holding, or what may be layered onto what. Those are decisions about a particular use, and the
system that has the task and the scene — the runtime agent — makes them per request. A knowledge base
that answered them in advance would be a catalogue of pre-enumerated combinations, which is exactly
what this project set out not to build ([ADR 0022](../../docs/adr/0022-the-kb-describes-the-agent-decides.md)).

## The 9 body-part channels

Every action describes the body with the same 9 channels — 8 anatomical parts plus a `root`:

| channel                        | covers                                                         |
| ------------------------------ | -------------------------------------------------------------- |
| `root`                       | overall position / facing (kinematic only — no description)    |
| `torso`                      | spine / chest                                                  |
| `head`                       | neck + head                                                    |
| `left_arm` / `right_arm`   | shoulder → wrist                                              |
| `left_leg` / `right_leg`   | hip → foot                                                    |
| `left_hand` / `right_hand` | the fingers (hand pose)                                        |

The wrist counts as part of the arm and the foot as part of the leg. Splitting left/right and giving
`root` its own channel is groundwork for future walking clips, where per-leg and overall movement will
matter — today's clips all play in place.

## What an entry looks like

Open [actions/cpr.json](actions/cpr.json) for a full example. For each of the 9 channels, the **kinematic** side records
two orthogonal facts: how much that part *moves* (a 0–1 magnitude, a static-or-dynamic label, and the
physical number behind it) and what pose it *sits in* — `mean_pose`, the average over the clip of each
of that part's joint degrees of freedom, listed one by one under the engine's own names. Moving and
holding are independent: an arm raised and kept raised has a movement magnitude of zero and a mean pose
that says, joint by joint, that it is overhead. On the `root` the same idea is two numbers,
`mean_body_height` and `mean_body_tilt_deg` — the carriage of the body, which no single joint shows.

The pose is stored as it is, and **nothing here is compared with a reference or labelled**. Earlier
versions of the store reduced it to one number — how far the part sat from Unity's reference pose — and
called the result `neutral` or `displaced`. That reading turned out to be a statement about where the
reference was put as much as about the clip (a person standing relaxed reads *further* from Unity's
reference than a boxer's raised guard, because nobody stands at the centre of their joint ranges), and
one number cannot say which fingers are curled. [ADR 0021](../../docs/adr/0021-kinematic-facts-not-classifications.md)
has the history and the measurements. A consumer that wants a distance is free to take one, against
whatever reference its own task calls for.

The **semantic** side of a channel is one field: `motion_description`, a sentence saying what that part
does. At the top of the record there is one more, `action_description`, for the action as a whole. That
is all of it. (Kinematic and semantic are two ways of grouping a channel's fields, not separate sections
of the file.)

In CPR, for instance, the torso is *kinematically* dynamic — a deep forward lean — and its description
says so in words; the hands barely move, and their mean pose is the interlocked compression grip, which
the description names. What the record does NOT say is that the torso is the "primary" one or that the
hands are on `object:patient_chest`. The full list of fields and their allowed values lives in the
[schema](schema/motionkb.v4.schema.json).

Three things a v3 record carried and a v4 record does not, with where each went:

- **`ik_goals`** — an effector pinned to an object. Every record in the store had `target: null`,
  because the actual anchor is a fact about a particular scene; the field was two thirds of a decision
  with the deciding third permanently missing. The agent names the effector and the object in its plan
  now, against a scene it has queried.
- **`composability`** (`locks` / `free` / `can_overlay_on` / `base_or_overlay` / `posture` /
  `seam_owner`) — which parts an action needs exclusively and what it can layer onto. `can_overlay_on`
  was an enumerated whitelist, which is the pre-enumerated interaction template this project rejects;
  `locks`/`free` meant "occupied", not "un-overridable", which is a different thing. The agent supplies
  the channel split in its plan. `posture` survives, but as a measurement rather than a label.
- **The per-channel 5-tuple** (`role` / `motion_type` / `contact` / `constraint` / `target`) — see the
  paragraph at the top of this file, and ADR 0022 for the check that no kinematic threshold
  reconstructs `role`.

## The Mixamo corpus

Eight hand-authored nursing actions were enough to build a pipeline on and far too few to retrieve
from. So the whole of [Mixamo](https://www.mixamo.com)'s library — 2446 clips, re-downloaded at 30 fps
to match the project's rig — was imported into Unity and run through the pipeline: measured first
(2026-08-21), then photographed and described (2026-08-27), then accepted (2026-09-02) as the store
itself. It is the library now, and the eight are elsewhere.

What is in it: 2446 clips, 143 minutes of motion, median 2.2 s and longest 46.7 s. Between 85% and 91%
of them move on any given body channel; the hands are the quietest at 62%. 128 are Mixamo *pose* assets
that resolve to a single frame pair, and 156 records measure fully static — those are kept rather than
filtered, because a pose is a legitimate thing to retrieve, and counted in
[`motionkb_build/reports/corpus_ingest.md`](../motionkb_build/reports/corpus_ingest.md) so nobody has to rediscover them.

One caveat worth knowing before you use the corpus: **`loop` is `false` on every record**, including the
walks and runs. It is an importer setting, not a measurement — nobody set it when the corpus was
imported — so it records that no one has declared these clips loopable, not that they do not cycle.

What that buys is retrieval **by measurement**: *"clips whose legs are dynamic and whose torso is
static"* is a query the corpus can answer, and it will return every walk, run, jog and sidestep in the
library without anyone having named one of them. *"Clips of someone walking"* needs a description
instead — and since 2026-08-27 the corpus has those too.

Filling that half was the pass v4 made smaller than it would have been: what had to be produced for
each of the 2446 was nine sentences, not nine sentences plus forty categorical labels that have to
agree with each other. It stayed deliberately separate from measuring, for the reason the whole
kinematic / semantic split exists: the numbers come from measuring, the sentences come from looking,
and running them together is how the two get confused. The eight nursing actions had been described by
a VLM reading rendered frames (ADR 0008); doing the same for 2446 was a scale question.

**Both halves are filled now.** Every corpus clip got its eight-view ring on **2026-08-27** — 57,680
JPEGs, 3.5 GB, about four hours of engine time, not one failure — and the same day `qwen3.8-27b`, served
locally on an HPC cluster rather than through 2446 hosted-API calls, read those rings and wrote the nine
sentences into every record. Each one now carries an `extraction.vlm_proposal` block naming the model,
the frame count and the eight views. No kinematic number moved on that pass, and none moved on the
acceptance that followed it either: describing a clip is not accepting it, and accepting one is not
re-measuring it.

Why the corpus entered `status: candidate` rather than as a store of its own, and why its pose dumps
are not in git: [ADR 0014](../../docs/adr/0014-corpus-enters-measured-only.md). Why it is now the whole
of the accepted store, keyed by clip name, with the nursing eight moved out:
[ADR 0023](../../docs/adr/0023-the-general-library-is-the-knowledge-base.md).

## How an entry is produced

An entry comes together in three stages. First, the measured numbers are computed automatically from the
real clip (and the controller wiring is read straight from Unity). Then a VLM watches rendered frames of
the motion and writes the `action_description` and the eight `motion_description`s, and an automatic
check confirms it answered for every channel. Naming is separate: describing a clip does not say what to
call it, and dozens of corpus clips are walk variants that would collide on one `action_id`, so a record
keeps its `clip_name` key until a human accepts it under a name. A named record is kept in the live store
by default; a human review is optional. The commands for each stage are below.

## Running the extractor

The library is built by a small Python toolchain in the separate `animation-agent` repo, in two halves: first **measure** the
numbers, then **describe** the motion. Every step is a plain Python command; the ones that touch Unity
(`register`, `resolve-controller`, `sample`, `render`) need the editor open with the MCP server running
(HTTP, port 8080). Working files are keyed by the clip name until the very end. Run from the repo root.

> To start a **brand-new action**, first `python extract.py register <clip_name>` — it finds
> the clip by name in Unity, fills its `source_clip` (`guid` + `file_id` + `clip_name`), **and** resolves
> `controller_state` / `controller_layer` / `trigger_param` from the AnimatorController the clip is wired into
> (left blank if it isn't wired yet — run `resolve-controller <clip_name>` once you wire it). That's all the
> hand-entry there is: every description comes from the propose step below.
> (Re-running the existing 8 doesn't need this — they're already registered.)

**Measure — the numbers (program-written):**

1. `python extract.py emit-sampler` — writes a small Unity sampler script.
2. `python extract.py sample` — runs it in Unity (the one in-engine step); records per-frame
   bone positions to `agent/animation_knowledge_base/raw/<clip>.json`. (`--host`/`--port`/`--instance` if not the default.)
3. `python extract.py assemble` — computes every measured value into `actions/<clip>.json`, leaving
   the descriptions for the next half. Accepted records are skipped: their measured half is frozen
   golden, and re-measuring it is `recalibrate_kinematic.py`'s deliberate job.

**Describe — the words (a VLM proposes, kept by default; a human may review):**

4. `python extract.py render <clip>` — renders the clip from **all eight sides** (on a plain
   ground plane) to `agent/animation_knowledge_base/frames/<clip>/`. The three times are chosen to COVER the clip's range
   of pose — the frames that leave no part of the motion unrepresented — rather than spread over an
   interval (ADR 0015). The angles are not chosen at all: `view_ring` builds eight camera directions 45°
   apart around the clip's own facing (`front, front_right, right, back_right, back, back_left, left,
   front_left`, turning toward the figure's own right), all at one slight look-down, so nothing is left to
   a guess about which axis an action reads along — whatever one view hides, the opposite view shows.
   That is 8 × 3 = **24 JPEGs** a clip (16 where the clip is a single-frame pose asset). They are not a
   throwaway intermediate: besides feeding the proposal step below, the runtime agent reads them at
   retrieval time as open-ended visual evidence, to arbitrate between candidates the descriptions do not
   separate. This store's rings are gitignored — 57,680 files and 3.5 GB, regenerable from the FBX that
   are versioned beside them. The nursing eight's are **committed** via git-lfs, in
   `../nursing_assets/frames/`: eight rings is a size worth carrying, and it keeps that archive readable
   without booting the engine.
5. `python extract.py propose <clip>` — a vision-language model looks at those frames and writes nine
   sentences: the `action_description`, and one `motion_description` per anatomical channel. The reply
   comes back as nine labelled lines rather than JSON, because the corpus pass runs on a local ~27B
   model and a model that size loses a whole record to a stray fence or a missing key, where a skipped
   line costs one channel and a retry. A deterministic gate checks it answered for every channel,
   self-correcting on failure.
   The only measurement in the prompt is which parts move: `mean_pose` is a vector whose origin is a
   coordinate centre rather than a rest pose, so nothing reads off it that the eight views do not show
   better, and carriage is visible the same way — but three sampled moments cannot separate a hand held
   still from a hand that trembles, and `state_label` is the field a consumer reads beside the sentence.
   **A record that already has an `action_id` is then kept**: status flips to `accepted` and the file is
   renamed `actions/<action_id>.json` (provenance `vlm_accepted`, no human required); `--stage` holds it
   at `status: candidate` instead, and so does having no name yet. (Needs the provider's key in
   `key.env`, which is git-ignored.)
6. `python extract.py author <clip | all>` — **optional** human review: accept a staged record,
   marking it `human_accepted`. (Skip it entirely and the VLM output stands as `vlm_accepted`.)

**Migrate — a contract change that moves no number:**

- `python extract.py migrate [--dry-run]` — rewrites every record into the current schema's shape:
  renames what was renamed, deletes what the contract dropped, restamps `schema_version` /
  `extractor_version` / `extracted_at`. It reads no pose dump, so it cannot disturb a measured value.
  Idempotent. This is what took the store from `motionkb/v3` to `motionkb/v4`.

**Or, for a whole corpus at once** — `python ingest_corpus.py index | register | sample | measure | render`
runs the measure half and the render step over every clip in an asset folder (`Assets/Animations/Mixamo30`
by default) and stops there, leaving 2446 measured-and-photographed records with their semantic half
seeded null (`status` reports where the funnel stands). `index`, `sample` and `render` need Unity;
`sample` takes about an hour for the full corpus and `render` about four, and both resume if interrupted —
`sample` on whether the dump exists, `render` on whether the ring is complete — so re-running after a
failure picks up where it stopped. It never proposes, never promotes, and never touches an accepted record.

Validate any time with `python validate_motionkb.py` (add `-q` to print failures only — at 2446 files a
PASS line each is not a report; see the next section). The measure half writes
only numbers (never the words); the describe half writes only words (never the numbers). Full per-step
detail + the one rig-specific gotcha are in the engineering notes, [HANDOFF.md](../../HANDOFF.md) §8.3.

## How the data is kept correct

Every entry is checked two ways: against a strict field spec (the "contract" that lists every allowed
field and value), and by an automated check that recomputes all the measured numbers from the original
clips — so nothing can be hand-edited or silently drift without being caught.

Run these from the **agent repository** (`~/Research/animation_agent` on WSL), with `MOTIONKB_DIR` pointing here:

```
python validate_motionkb.py    # check every entry against the contract
./check_kb.sh                  # the five gates: contract + recompute-and-compare + manifest + posture + guid
```

All 2446 records pass today, every one of them against the full contract — description completeness
included, since every record here is accepted. The fourth gate, `build_posture.py --check`, recomputes
`derived/posture.json` from the frozen dumps and compares, so a hand-edited sidecar is caught as well as
a stale one; the runtime refuses to start without a current one.

## Going deeper

- **Exact field contract:** [`schema/motionkb.v4.schema.json`](schema/motionkb.v4.schema.json) (and
  [`engine_mask_map.json`](engine_mask_map.json) — how these body-part names map onto other engines' bone
  groups; a separate contract, unaffected by v4).
- **Engineering notes & extractor internals** (adding an action, the rig-specific gotchas):
  [HANDOFF.md](../../HANDOFF.md) §8.
- **Why each decision was made:** [docs/adr/](../../docs/adr/) — 0022 (the KB describes, the agent
  decides), 0007 (the 9-channel split & extractor), 0008 (the VLM proposal loop), 0002 (kinematic vs.
  semantic), 0021 (poses are stored, not classified).
- **Rollback / versions:** [docs/ROLLBACK.md](../../docs/ROLLBACK.md); the current store is tagged
  `kb/v4` (2026-08-26), and the retired versions are preserved at `kb/v3` (2026-08-25, the same
  numbers with the full SEMANTIC half), `kb/v2` (2026-06-24, the 9-channel store with the posture
  triple) and `kb/v1` (the 6-part store). Only `kb/v3` and `kb/v4` hold the KB at this path — the
  older two predate the move and keep it under `Assets/MotionKB/`.
