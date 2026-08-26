# `agent/` — the MotionKB

All that remains of the agent side inside this repository. The pipeline that produces this knowledge base
and the runtime service that consumes it moved to a separate repository on 2026-08-05:

```
~/Research/animation-agent     on WSL (Ubuntu 24.04, ext4) — engine-independent Python
```

```
agent/
├── animation_knowledge_base/   the MotionKB itself — everything a CONSUMER reads: 2454 records,
│                               the frozen pose dumps and render frames they were derived from, the
│                               derived tables, the manifest, the contract
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
export MOTIONKB_DIR=/mnt/f/Research/AI_agent/Animation/Animation_agent/Project/Animation/agent/animation_knowledge_base
```

The runtime service loads the ~1.4 MB action store into memory at startup (BM25 index included), so
retrieval never touches the disk; only `frames` PNGs are read on demand as visual evidence. It treats the
KB as **read-only**. The only writer is the offline pipeline.

Everything written here goes through `paths.write_text` / `write_json` / `write_bytes`: UTF-8 without BOM,
LF, atomic. Pose dumps are written back verbatim rather than re-serialized, so re-sampling unchanged data
produces a zero-line diff and `git status` stays a working drift detector. **git runs on the Windows side
only** — the agent side reaches this tree over DrvFs and must not manage it.

## What it holds

| Input | Role |
|---|---|
| `<action_id>.json` (8 accepted, `schema_version: motionkb/v3`) | 9 channels x {kinematic block, semantic 5-tuple}, `ik_goals`, `composability` |
| `manifest.json` | corpus index; pin the KB by its `kb_version` |
| `engine_mask_map.json` | engine-neutral channel vocabulary (Unity / UE5 / Blender / SMPL-X) |
| `raw/<clip>.json` | frozen per-frame pose dumps — the golden regression's input |
| `frames/<clip>/*.png` | render frames, read at retrieval time as open-ended visual evidence (lfs) |
| `motionkb_build/reports/kb_state.md` | last `guid → AnimationClip` resolution result |
| `retrieval_eval_set.json` | seed eval set (full_match / decompose / no_match) |

The kinematic/semantic split and its provenance tiers (ADR 0002, ADR 0008) are what keep this auditable;
record which `kb_version` a run consumed.

## Gates

All four live in the agent repo and run from there: `./check_kb.sh`. Three need no engine (schema and
cross-field invariants, golden re-extraction from frozen `raw`, manifest sync). The fourth,
`validate_guids.py`, resolves each `source_clip.guid` to a real `AnimationClip` by driving the
`AssetDatabase` over the Unity MCP bridge, and writes `motionkb_build/reports/kb_state.md`.

That last one used to be `Assets/Editor/MotionKB/MotionKBValidator.cs`, an in-editor tool. It was deleted
on 2026-08-05 and reimplemented as generated C# posted from Python — the same pattern
`build_find_clip_csharp` and `build_resolve_controller_csharp` already used. **No agent code remains inside
the Unity project.**

See the root `README.md` for the research statement and `HANDOFF.md` for the engineering handoff.
