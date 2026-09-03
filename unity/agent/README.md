# `agent/` — the MotionKB

All that remains of the agent side inside this repository. The pipeline that produces this knowledge base
and the runtime service that consumes it moved to a separate repository on 2026-08-05:

```
~/Research/animation_agent     on WSL (Ubuntu 24.04, ext4) — engine-independent Python
```

```
agent/
├── animation_knowledge_base/   the MotionKB itself — everything a CONSUMER reads: 2446 records,
│                               the frozen pose dumps and render frames they were derived from, the
│                               derived tables, the manifest, the contract
├── nursing_assets/             FROZEN, READ BY NOTHING: the 8 hand-authored nursing actions and the
│                               evidence they were built from, held out for an evaluation that does
│                               not exist yet (ADR 0023)
├── legacy/eval_8_actions/      the retired twelve-case retrieval eval set, archived beside them
└── motionkb_build/             everything that exists only because it was BUILT: run reports, the
                                corpus enumeration, the archive of superseded records and contracts
```

Split by who reads them, not by how they were made ([ADR 0017](../docs/adr/0017-knowledge-base-and-its-build-artifacts.md)):
nothing at runtime opens the second, and the agent's search workspace does not mount it.

## Why this half stayed

The KB cannot be regenerated without Unity. `raw/` comes from in-engine `AnimationMode` sampling of
Humanoid muscle clips, `frames/` from in-engine rendering, and every entry records a `source_clip`
(`guid` + `file_id`) that only means anything inside *this* Unity project. It also grows only when a new
clip is imported here.

So it is a derivative of this project's animation assets and is versioned with them. Adding an action is
then **one atomic commit** holding both the FBX and its KB entry, and a guid can never drift out of sync
with the store that records it. The pipeline has no such tie — it is ordinary engine-independent Python —
so it left.

`kb/` and `motionkb/` had both moved here out of `Assets/` on the same day, before the split; keeping the
KB under `Assets/` would have had Unity import it as project assets and pull it into player builds.

## How the agent side reaches it

Through one configured path — see `paths.py` in the agent repo:

```sh
export MOTIONKB_DIR=/mnt/d/Research/AI_agent/Animation_agent/Animation/agent/animation_knowledge_base
```

The runtime service loads the accepted store into memory at startup, BM25 index included — all 2446
records in about 0.55 s, taken through `manifest.json` rather than by walking the directory (`KBIndex.load`
→ `paths.accepted_files`; walking it cost 4.13 s). So retrieval never touches the disk; only `frames`
JPEGs are read on demand as visual evidence. A search over the loaded index costs about 18 ms. Start-up
also reads `derived/posture.json` and refuses to run without one that matches the current algorithm
version and covers every record ([ADR 0024](../docs/adr/0024-kinematic-posture-states-and-seat-alignment.md)).
It treats the KB as **read-only**. The only writer is the offline pipeline.

Everything written here goes through `paths.write_text` / `write_json` / `write_bytes`: UTF-8 without BOM,
LF, atomic. Pose dumps are written back verbatim rather than re-serialized, so re-sampling unchanged data
produces a zero-line diff and `git status` stays a working drift detector. **git runs on the Windows side
only** — the agent side reaches this tree over DrvFs and must not manage it.

## What it holds

| Input | Role |
|---|---|
| `actions/*.json` (2446 records, all accepted; `schema_version: motionkb/v4`) | 9 channels x the kinematic block, plus two description fields: `action_description` and each anatomical channel's `motion_description`. Every record carries both since 2026-08-27, when `qwen3.8-27b` described the corpus on HPC; all 2446 were accepted on 2026-09-02 under their own clip names, so a record is `<clip_name>.json` and `action_id == clip_name` ([ADR 0023](../docs/adr/0023-the-general-library-is-the-knowledge-base.md)) |
| `manifest.json` | corpus index; pin the KB by its `kb_version` |
| `engine_mask_map.json` | engine-neutral channel vocabulary (Unity / UE5 / Blender / SMPL-X) |
| `raw/<clip>.json` | frozen per-frame pose dumps — the golden regression's input |
| `frames/<clip>/*.jpg` | render frames, read at retrieval time as open-ended visual evidence (lfs). 24 per clip: the eight-view ring (`front, front_right, right, back_right, back, back_left, left, front_left`, turning toward the figure's own right) at three pose-coverage times — 16 for a one-frame Mixamo pose asset, which has only two moments to sample. All 2446 (57,680 files, 3.5 GB) are gitignored as regenerable derivatives, like their dumps; the nursing eight's rings are tracked, in `nursing_assets/frames/` |
| `derived/posture.json` | the posture sidecar: four coarse states per clip over time, and how far each clip travels. A hard start-up dependency, and the fifth gate |
| `derived/segments.json` | per action and channel, the frames that channel is actually moving in |
| `motionkb_build/reports/kb_state.md` | last `guid → AnimationClip` resolution result |
| `legacy/eval_8_actions/retrieval_eval_set.json` | the retired eval set (full_match / decompose / no_match) — it names the eight nursing actions and does not run |

The kinematic/semantic split and its provenance tiers (ADR 0002, ADR 0008) are what keep this auditable;
record which `kb_version` a run consumed. Since
[ADR 0022](../docs/adr/0022-the-kb-describes-the-agent-decides.md) the semantic half is those two
description fields and nothing else: a record says what the action looks like and how each body part
moves, while composition, contact, IK and channel ownership are decided at runtime by the agent, which is
the side that has the task and the scene. The kinematic half was untouched by that change — no number
moved.

## Gates

All five live in the agent repo and run from there: `./check_kb.sh`. Four need no engine — schema,
channel vocabulary and description completeness (2446/2446); golden re-extraction from frozen `raw`
(a fixed 16-clip subset, not a sample); manifest sync; and `build_posture.py --check`, which recomputes
the posture sidecar from the dumps and compares, so a hand-edited one is caught too. The fifth,
`validate_guids.py`, resolves each `source_clip.guid` to a real `AnimationClip` by driving the
`AssetDatabase` over the Unity MCP bridge, and writes `motionkb_build/reports/kb_state.md` — a
deterministic 40-clip sample by default, with `--all` for the run to make after a reimport.

That last one used to be `Assets/Editor/MotionKB/MotionKBValidator.cs`, an in-editor tool. It was deleted
on 2026-08-05 and reimplemented as generated C# posted from Python — the same pattern
`build_find_clip_csharp` and `build_resolve_controller_csharp` already used. **No agent code remains inside
the Unity project.**

See the root `README.md` for the research statement and `HANDOFF.md` for the engineering handoff.
