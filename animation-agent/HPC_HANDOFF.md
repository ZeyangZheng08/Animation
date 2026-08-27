# HANDOFF — the semantic pass, on HPC

For whoever picks this up here. Ten minutes on this file saves you the two hours of rediscovery it
was written from.

This directory is **not the project**. It is the slice of it that the semantic pass needs, copied
from a Windows workstation on 2026-08-27. The Unity project, the runtime service, the git history and
the 1.4 GB of pose dumps stayed behind, deliberately — see §6.

---

## 0. The job, in one paragraph

`animation_knowledge_base/actions/` holds **2454 records**, one per animation clip. Eight of them are
finished. The other **2446 are measured but wordless**: every kinematic number is filled in, and
`action_description` plus all eight `channels.*.motion_description` are `null`. Each of those clips
has an eight-view photo ring on disk. The job is to look at the pictures and write the nine missing
sentences into each record. Nothing else about a record may change.

Until that lands, the corpus is searchable by measurement (*"legs dynamic, torso static"* returns
every walk in the library) and not by meaning (*"which clips are walking"* returns nothing).

---

## 1. What is here

```
/project/Driver_in_the_loop/AI_agent/Animation/
├── Env/                          the conda env `vllm`               (pre-existing, not ours)
├── vllm/                         serving scripts + measurements      (pre-existing, not ours)
├── pipeline/                     8 files, stdlib only, no pip needed
│   ├── propose.py                the prompt, the reply parser, the completeness loop
│   ├── config.py                 the 9 channels, the bone map, the metric constants
│   ├── paths.py                  every path, derived from $MOTIONKB_DIR
│   ├── validate_motionkb.py      the gate; `validate_descriptions` is the one propose uses
│   ├── unity_sampler.py          carried for two symbols; see §6
│   ├── vlm_openai.py             reference client — the closest thing to what you must write
│   ├── vlm_anthropic.py          the same three symbols on a different provider
│   └── tests/test_propose_prompt.py    12 tests, run them, they need no server
└── animation_knowledge_base/
    ├── actions/                  2454 records (8 accepted, 2446 candidate)
    ├── frames/                   2454 directories, 57,872 JPEG
    ├── schema/                   motionkb v1..v4 + CHANGELOG; v4 is live
    ├── manifest.json             index of the accepted subset
    └── engine_mask_map.json      the engine-neutral channel vocabulary
```

One environment variable drives all of it:

```bash
export MOTIONKB_DIR=/project/Driver_in_the_loop/AI_agent/Animation/animation_knowledge_base
```

`paths.py` derives the rest. Verified here on 2026-08-27: `import propose` and `build_prompt` both
work under the login node's `python3` (3.6.8) with nothing installed. Use the `vllm` env's newer
interpreter for real work.

**A frame ring is 24 pictures, except when it is 16.** Eight camera angles 45° apart around the
figure, at three moments chosen to cover the clip's range of pose. 128 of the corpus clips are
single-frame Mixamo *pose* assets with only two moments to sample, so their ring is 8 × 2. Verified
on this copy: 2326 directories of 24, 128 of 16. Size the ring from what is on disk; do not assume 24.

---

## 2. What you must build

Two things, and nothing else. Everything upstream of them already runs.

### 2.1 `pipeline/vlm_qwen.py`

`propose.py` reaches its model through three symbols, and any module providing them is a valid
backend:

```python
MODEL                                          # str, recorded in the record's provenance
load_api_key(repo_root) -> str                 # return "EMPTY"; vLLM ignores it
describe(api_key, prompt, image_paths) -> (reply_text: str, usage: dict)
```

`describe` sends one text block followed by the images, in the order given, and returns the reply as
**text**. It does not parse. `propose.parse_reply` reads the nine labelled lines out of whatever
comes back.

Copy `vlm_openai.py` and change four things: the endpoint to `http://localhost:8000/v1/chat/completions`,
`MODEL` to `qwen3.8-27b` (the `--served-model-name`, not the HF id), drop the key handling, and drop
the gpt-5.x parameter-retry loop. Both existing clients are stdlib `urllib` on purpose, so the
pipeline needs no pip; the `vllm` env does also ship the `openai` SDK if you would rather use it.

**Turn thinking off.** `vllm/serve.sh` passes `--reasoning-parser qwen3`, so a thinking model would
return its reasoning alongside the answer, and `parse_reply` scans every line for a known label.
`vllm/describe_images.py` already disables it the right way:

```python
extra_body={"chat_template_kwargs": {"enable_thinking": False}}
```

### 2.2 A resumable batch driver

Follow `ingest_corpus.py`'s shape (it is in the agent repo, not here — the pattern is what matters):
take the population from `actions/`, do the per-clip work through `propose.propose_clip`, and
**resume on the work itself** rather than on a log. The resume test here is one line — a record whose
`action_description` is not null is done — which means an interrupted run continues instead of paying
for 2446 clips twice.

Report what you skip. A driver that silently caps or drops reads as "covered everything" when it did
not.

---

## 3. Serving settings this workload changes

Read `vllm/README_qwen38.md` first — it is a careful document and most of what is below leans on its
measurements. Three settings need to change for **this** job.

### `MM_IMAGES=24` — this one is a hard blocker

`serve.sh` defaults to `MM_IMAGES=12`. A 24-image request is **rejected**, not degraded. Start the
server with:

```bash
cd /project/Driver_in_the_loop/AI_agent/Animation/vllm
MM_IMAGES=24 MODEL_ID=Qwen/Qwen3.8-27B-FP8 MAX_SEQS=8 bash serve.sh
```

### `MAX_LEN` — 32768 fits, with less room than it looks

The frames are 1024×1024. Interpolating the README's own resolution table (1280×960 ≈ 1,191 image
tokens), each frame costs roughly **1,000 tokens**, so a 24-image request is about **24,000 prompt
tokens** before the ~400 tokens of instructions. The default `MAX_LEN=32768` holds that with room for
the reply, but not much else. If you hit a context error, `MAX_LEN=65536` is the first thing to try.
These are estimates from a table, not measurements — read `usage.prompt_tokens` off the first real
response and believe that instead.

### FP8 weights, and `MAX_SEQS=8`

The README measured FP8 weights at **37.4 tok/s single-stream against bf16's 23.7**, with greedy
output identical on 10 of 10 prompts. For a 2446-clip batch that is free speed.

`MAX_SEQS`: the README's image benchmark found throughput flattening at 8 concurrent — "the vision
encoder and the 20K-token prefill are the bottleneck, not KV" — and our prefill is larger than the
one it measured.

**Do not split the ring across requests to chase the throughput note.** The README observes that one
image per request is ~40% faster and says to pack images together "only when the task needs
cross-image reasoning". This task is exactly that case: the prompt tells the model to read all eight
angles before it places a hand, because whatever one view hides another shows. Splitting the ring
destroys the thing the eight views were rendered for.

**Rough expectation, so a slow run is recognisable as slow:** the README measured 10 images of
1920×1080 at 8 concurrent in 34.7 s per request. Ours is more images and fewer pixels each. Somewhere
around 20–40 s per clip at 8 concurrent puts 2446 clips in the **2–4 hour** range. Measure the first
twenty and extrapolate; do not trust this paragraph over a stopwatch.

---

## 4. Do this before the batch

Run the prompt over five clips picked for different shapes and **read the sentences yourself**:
a walk, a floor action, a one-handed manipulation, a single-frame pose asset (16 frames, not 24), and
something lying down. Nine sentences × 2446 clips is not a thing to discover was wrong at the end.

Two failure modes worth looking for specifically:

- **Props that are not there.** Every clip renders as one untextured mannequin on an empty floor with
  empty hands, whatever it depicts. The prompt says so. A model that writes "grips the bottle" is
  reading the clip name instead of the picture.
- **Contradicting the measurement.** The prompt states which parts move. A channel measured `static`
  described as "swings freely" is a defect in the record, because a consumer reads both fields.

The eight accepted records are your reference for register and length — read a few of them.

---

## 5. What must not happen

- **Never write a kinematic value.** `state_label`, `motion_magnitude`, `raw_measurement`, `mean_pose`,
  `mean_body_height`, `mean_body_tilt_deg` are program-measured from frozen pose dumps and are frozen
  golden. The describe half writes only words; the measure half writes only numbers. A regression test
  on the Windows side re-derives all eight accepted records from their dumps and will catch a drifted
  number, but not until this copy goes home.
- **Never accept or rename a record.** Acceptance sets `status` and renames the file to
  `<action_id>.json`. Naming is a human decision made back on the Unity side, and it is why the
  describer was stopped from proposing an `action_id` at all: dozens of these clips are walk variants
  that would collide on one. Every record here stays `status: candidate` under its `clip_name`.
- **Do not treat this copy as the source of truth.** The knowledge base is versioned in the Unity
  repository because it is a derivative of that project's animation assets — every record carries a
  `source_clip.guid` that only resolves inside that Unity project. This is a working copy.

---

## 6. Things that will look wrong and are not

- **`unity_sampler.py` is 50 KB of Unity C# generation on a machine with no Unity.** `propose.py`
  imports it for two symbols, `VIEW_RING_NAMES` and `frame_paths`. It also imports
  `agent.transitions` — but inside `write_raw`, which the semantic pass never calls, which is why the
  runtime package did not need to come along. It was carried unmodified rather than split, so this
  copy cannot drift from the repository version.
- **`raw/` is absent.** 1.4 GB of per-frame pose dumps. Nothing in the semantic pass opens one: the
  measurements the prompt uses are already in the records. If something asks for `raw/`, it is not
  part of this job.
- **The prompt states no reading order for the frames.** Each frame is labelled with its angle and its
  time in the manifest, which is the fact the model needs. An earlier version narrated the ring —
  *front, then turning toward the figure's own right* — and that sentence was deleted because it was
  also a claim about a sort order the module could not enforce on its caller. `build_prompt` now
  returns `(frames_in_ring_order, prompt)`. **Attach the list it returns**, not the one you passed in.
- **`login` node `python3` is 3.6.8 and end-of-life.** The pipeline imports fine on it. Use the `vllm`
  env for anything real.

---

## 7. Getting the results home

The descriptions are written back into `actions/*.json` in place. Only that directory changes, and it
is **21 MB**, so it goes home on its own:

```bash
rsync -avz --partial \
  /project/Driver_in_the_loop/AI_agent/Animation/animation_knowledge_base/actions/ \
  <workstation>:.../Animation/agent/animation_knowledge_base/actions/
```

Frames and schema do not travel back; they never change.

On arrival, from the agent repo: `python validate_motionkb.py` (expects 2454/2454) and
`./check_kb.sh` for the full four gates. Then the corpus is retrievable by meaning, and the
descriptions get committed in the Unity repository alongside the assets they describe.

---

## 8. Where the rest of the project is

| what | where |
|---|---|
| Unity project + the canonical knowledge base | a Windows workstation, `F:\...\Project\Animation` |
| the pipeline and the runtime service (`animation-agent`) | WSL, `~/Research/animation-agent` |
| published source mirror, both halves, no assets | `github.com/ZeyangZheng08/Animation`, branch `code` |

**This file is versioned in the agent repository, as `HPC_HANDOFF.md`.** The copy sitting next to you
is a copy: edit it and the edit is lost the next time it is refreshed. Change it there, then re-send.

The design decisions behind the contract are ADRs in the Unity repository. Three matter here:
**0022** (the knowledge base describes, the agent decides — why a record holds descriptions and no
composition fields), **0021** (kinematic facts are stored as facts, never as classifications), and
**0002** (measured and semantic are separate halves with separate writers).
