# MotionKB — Body-Part-Level Nursing Motion Knowledge Base

MotionKB is a small, engine-agnostic library that describes each nursing animation **at the body-part
level** — one JSON file per action. It's the canonical store the project's retrieval system reads from:
instead of treating an animation as one indivisible block, a planner can ask "which clip has the legs
walking and the right hand reaching?" and reason about individual parts.

Everything here is plain JSON. **The knowledge base is `actions/` — the eight `<action_id>.json` files
in it, and nothing else.** Unity is used only to *sample* the clips (to find where the bones are); all
the knowledge lives in these files, outside any engine.

```
kb/
├── actions/          the accepted records, one file per action — THIS is the knowledge base
├── candidate/        staged records awaiting review (usually empty)
├── schema/           the JSON Schema contract every record is validated against
├── engine_mask_map.json     9 channels -> Unity AvatarMask
├── retrieval_eval_set.json  the retrieval evaluation seed
├── kb_manifest.json         generated corpus index
├── _raw/             frozen per-frame pose dumps — the only input to the MEASURED half
├── _frames/          rendered evidence frames — the only input to the SEMANTIC half
├── _derived/         generated segment / transition tables
└── _reports/         generated run reports
```

The `_`-prefixed folders are generated working files. `actions/` is its own directory so that
membership is the path rather than a list every consumer has to keep in sync — see
[ADR 0012](../../docs/adr/0012-accepted-store-in-its-own-directory.md).

> **Status:** the current library covers 8 nursing actions. This README is the human overview; pointers
> to the deeper docs (and version/rollback details) are at the end.

## Why body-part level

An avatar shouldn't be driven as whole, monolithic clips. Describing each animation part-by-part lets a
planner reason about transitions and composition. Going from `walking` to `giving_pills`, for example,
the legs stop and plant, the torso steadies and leans toward the patient, the head turns to look, the
hand shapes into a pinch, and only then does the arm perform the hand-off — five things, in different
parts, at different moments.

## The 8 actions

| action           | what it is                        | role          |
| ---------------- | --------------------------------- | ------------- |
| `idle`         | standing at rest                  | base          |
| `walking`      | walking gait (in place)           | base          |
| `typing`       | seated, typing at the computer    | base (seated) |
| `giving_pills` | handing medication to the patient | overlay       |
| `cpr`          | chest compressions                | overlay       |
| `grab_bottle`  | picking up the medicine bottle    | overlay       |
| `check_pulse`  | checking the patient's pulse      | overlay       |
| `bvm`          | bag-valve-mask ventilation        | overlay       |

All eight are Humanoid clips (Unity's retargetable, skeleton-independent animation format) remapped onto
one shared character rig (`nurse_avatar.fbx`), and they play in place. "base" vs. "overlay" is a
composition role (a foundational pose vs. something layered on top), not a Unity animation layer.

## The core idea: measured vs. semantic

Each action splits cleanly into two kinds of information:

- **Measured** — *the numbers, computed by a program from the real motion.* How much each part moves,
  whether it's static or dynamic, the duration, the frame rate. These are read straight off the
  animation; no human or AI ever edits them. Same clip in → same numbers out.
- **Semantic** — *the meaning, written by a human,* with help from a **VLM** (a vision-language model that
  can look at rendered frames of the motion and suggest labels). What role each part plays (is the torso
  the lead actor, or just steadying?), what it's doing (reaching? holding?), what it touches, what it's
  constrained to.

That split is the whole point: numbers come from *measuring* real clips, never from a language model
guessing them — language models are unreliable at precise body positioning, a finding this project is
built on. The model is trusted only for the categorical, meaning-level labels, and even those are checked
against the measured facts and confirmed by a human.

## The 9 body-part channels

Every action describes the body with the same 9 channels — 8 anatomical parts plus a `root`:

| channel                        | covers                                                         |
| ------------------------------ | -------------------------------------------------------------- |
| `root`                       | overall position / facing (measured only — no meaning labels) |
| `torso`                      | spine / chest                                                  |
| `head`                       | neck + head                                                    |
| `left_arm` / `right_arm`   | shoulder → wrist                                              |
| `left_leg` / `right_leg`   | hip → foot                                                    |
| `left_hand` / `right_hand` | the fingers (hand pose)                                        |

The wrist counts as part of the arm and the foot as part of the leg. Splitting left/right and giving
`root` its own channel is groundwork for future walking clips, where per-leg and overall movement will
matter — today's clips all play in place.

## What an entry looks like

Open [cpr.json](cpr.json) for a full example. For each of the 9 channels, the **measured** side records
how much that part moves — a 0–1 magnitude, a static-or-dynamic label, and the physical number behind it.
The **semantic** side records what it means: its role, what it's doing, what it touches, and a short
plain-language description. (These are two ways of grouping a channel's fields, not separate sections of
the file.)

In CPR, for instance, the torso is *measured* as dynamic — a deep forward lean — and *semantic* as the
primary actor driving the compressions; the hands barely move, yet are *semantic* as primary too, held
against the patient's chest. The full list of fields and their allowed values lives in the
[schema](schema/motionkb.v2.schema.json).

Two more pieces sit alongside the channels:

- **`ik_goals`** — where a hand or foot is constrained to an object, kept separate from the body-part
  description. "The right hand reaches the patient's chest" is an IK goal, not an arm-motion fact. It is
  **DERIVED** from the semantic 5-tuple (a hand/foot whose contact is `object:<obj>` and whose constraint
  pins it there — `must-reach` or `must-maintain`); the concrete scene anchor (`target`) is engine-specific
  and **deferred to Phase-2 grounding** (`target: null`) — `contact_object` is the engine-neutral 'what to reach'.
- **`composability`** — how the system will know two actions can play together: each action marks which
  body parts it needs exclusively (*locks*) and which it leaves *free* for another action to drive, plus
  which actions it can layer onto. (A part can be still yet locked if it's occupied — CPR's hands barely
  move but firmly hold the chest.)

## How an entry is produced

An entry comes together in three stages. First, the measured numbers are computed automatically from the
real clip (and the controller wiring is read straight from Unity). Then the meaning-level labels — and the
composability judgement calls — are proposed by a VLM from rendered frames of the motion, the part-ownership
(locks/free) is derived from those labels, and an automatic check confirms everything agrees with the
measured facts. By default the proposal is kept and added to the live store; a human review is optional.
The commands for each stage are below.

## Running the extractor

The library is built by a small Python toolchain in the separate `animation-agent` repo, in two halves: first **measure** the
numbers, then **author** the meaning. Every step is a plain Python command; the ones that touch Unity
(`register`, `resolve-controller`, `sample`, `render`) need the editor open with the MCP server running
(HTTP, port 8080). Working files are keyed by the clip name until the very end. Run from the repo root.

> To start a **brand-new action**, first `python extract.py register <clip_name>` — it finds
> the clip by name in Unity, fills its `source_clip` (`guid` + `file_id` + `clip_name`), **and** resolves
> `controller_state` / `controller_layer` / `trigger_param` from the AnimatorController the clip is wired into
> (left blank if it isn't wired yet — run `resolve-controller <clip_name>` once you wire it). That's all the
> hand-entry there is: `composability` and every meaning-level label come from the author step below.
> (Re-running the existing 8 doesn't need this — they're already registered.)

**Measure — the numbers (program-written):**

1. `python extract.py emit-sampler` — writes a small Unity sampler script.
2. `python extract.py sample` — runs it in Unity (the one in-engine step); records per-frame
   bone positions to `agent/kb/_raw/<clip>.json`. (`--host`/`--port`/`--instance` if not the default.)
3. `python extract.py assemble` — computes every measured value into `candidate/<clip>.json`,
   leaving the semantic fields for the next half.

**Author — the meaning (a VLM proposes, kept by default; a human may review):**

4. `python extract.py render <clip>` — renders multi-angle frames of the clip (on a plain
   ground plane) to `agent/kb/_frames/<clip>/`. These frames are **committed** (via git-lfs), not a
   throwaway intermediate: besides feeding the proposal step below, the Phase-2 agent reads them at
   retrieval time as open-ended visual evidence, to arbitrate between candidates that the closed-vocabulary
   semantic labels do not separate. Regenerating them requires a running Unity editor, so tracking them is
   what keeps the pure-Python agent side restorable without booting the engine.
5. `python extract.py propose <clip>` — a vision-language model (gpt-5.5) looks at those frames
   plus the measured facts and proposes the `action_id`, the per-part labels, the descriptions, `mask_coverage`,
   and the composability judgement calls (base-vs-overlay, posture, which bases it can layer onto); the program
   then **derives** `composability.locks`/`free`/`seam_owner` from the proposed roles. A deterministic
   consistency + composability gate checks all of it (self-correcting on failure). **By default the result is
   kept** and promoted to the accepted store as `actions/<action_id>.json` (provenance `vlm_accepted`, no human
   required); add `--stage` to hold it in `candidate/` for review instead. (Needs `OPENAI_API_KEY` in
   `key.env`, which is git-ignored.)
6. `python extract.py author <clip | all>` — **optional** human review: re-promote a staged
   candidate, marking it `human_accepted`. (Skip it entirely and the VLM output stands as `vlm_accepted`.)

Validate any time with `python validate_motionkb.py` (see the next section). The measure half writes
only numbers (never the meaning); the author half writes only meaning (never the numbers). Full per-step
detail + the one rig-specific gotcha are in the engineering notes, [HANDOFF.md](../../HANDOFF.md) §8.3.

## How the data is kept correct

Every entry is checked two ways: against a strict field spec (the "contract" that lists every allowed
field and value), and by an automated check that recomputes all the measured numbers from the original
clips — so nothing can be hand-edited or silently drift without being caught.

Run these from the **agent repository** (`~/Research/animation-agent` on WSL), with `MOTIONKB_DIR` pointing here:

```
python validate_motionkb.py    # check every entry against the contract
./check_kb.sh                  # the full gate: contract + recompute-and-compare + manifest + guid resolution
```

All 8 actions pass today.

## Going deeper

- **Exact field contract:** [`schema/motionkb.v2.schema.json`](schema/motionkb.v2.schema.json) (and
  [`engine_mask_map.json`](engine_mask_map.json) — how these body-part names map onto other engines' bone
  groups).
- **Engineering notes & extractor internals** (adding an action, the rig-specific gotchas):
  [HANDOFF.md](../../HANDOFF.md) §8.
- **Why each decision was made:** [docs/adr/](../../docs/adr/) — 0007 (the 9-channel split & extractor),
  0008 (the VLM proposal loop), 0002 (measured vs. semantic).
- **Rollback / versions:** [docs/ROLLBACK.md](../../docs/ROLLBACK.md); the current store is tagged `kb/v2`
  (finalized 2026-06-24) and the retired first version is preserved at `kb/v1`.
