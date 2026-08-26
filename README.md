# Source of the language-driven animation assembly project

Two halves of one system, one folder each. This branch carries **source only** — no 3D assets, no LFS.

```
unity/              the Unity project: scripts, scenes, settings, and the MotionKB
animation-agent/    the engine-independent Python: the agent, the offline pipeline, the tests
```

Read `unity/README.md` for the research statement, `unity/HANDOFF.md` for the engineering handoff, and
`animation-agent/README.md` for the agent side.

## What is here, and what is not

**Not here: every 3D asset.** Meshes, textures, materials, audio, FBX, video and the UI art are all
absent — about 3.2 GB tracked through LFS in the working repository, plus one 217 MB mesh that is not
even LFS-tracked and exceeds GitHub's 100 MB per-file limit on its own.

**So `unity/` will not open as a running project.** Two things break, and both are by design here:

- Unity resolves the scripts, the scene graph and the settings, then reports missing meshes, materials
  and avatars.
- `Packages/manifest.json` resolves the MCP bridge from `file:../../unity-mcp-research/MCPForUnity`, a
  sibling checkout that is not part of this repository. Point it somewhere real or remove the entry.

That is the intended trade: this branch exists so the *logic* can be read, reviewed and diffed
anywhere, not so the demo can be run anywhere. Running it needs the asset tree, which lives with the
working copy.

What that leaves is the part that is actually the work:

| | |
|---|---|
| `unity/Assets/Scripts/AgentRuntime/` | the 11-component runtime executor — composer, IK, gates, pose synthesis, locomotion |
| `unity/Assets/Editor/AgentRuntime/` | scene wiring and the in-editor terminal |
| `unity/Assets/Scenes/` | the scenes themselves, minus what they reference |
| `unity/Assets/Animations/` | the TEXT half only: the Animator controllers, the avatar masks and the clips. The FBX, meshes and textures they were built from are not here. Note that the agent path does not use the controllers at all — it drives a PlayableGraph directly, and these are the older demo's state machine, kept because `SCENARIO.md` describes it |
| `unity/Packages/com.unity.animation.rigging/` | kept, because it is embedded rather than resolved from the registry — the IK constraints are built on it |
| `unity/agent/animation_knowledge_base/` | the MotionKB as its consumers read it: the 8 accepted actions, the schema, the frozen per-frame pose dumps behind them, the derived seam and segment tables, and the render frames |
| `unity/agent/motionkb_build/` | what only building the KB produced: the calibration and state reports, the retired v1 schema, the earlier authoring pass kept for comparison, and the retrieval eval set |
| `unity/docs/adr/` | the architecture decision records, including where each was amended |
| `animation-agent/` | all of it — 0.8 MB of Python and tests |

The knowledge base is complete and self-contained for the 8 accepted actions: `raw/` holds the
sampled pose data every one of their measurements is derived from, so the whole offline pipeline and
every hermetic test run from this branch alone, with no engine and no assets.

The 2446-clip Mixamo corpus is **not** here. Its records are 307 MB that a change of measurement
formula rewrites in full, and its pose dumps are 1.5 GB that the working repository does not track
either. What the corpus is and how it was measured is in `unity/docs/adr/0014` and `0019`/`0020`;
`animation-agent/ingest_corpus.py` is the program that builds it.

## Running what can be run

```sh
cd animation-agent
conda env create -f environment.yml && conda activate animation-agent
export MOTIONKB_DIR="$PWD/../unity/agent/animation_knowledge_base"

python -m pytest -q          # the agent side, no editor and no API key
python run_eval.py           # retrieval scored against the seed eval set
python probe_compose.py      # which pairs of actions compose, and what only looks like it does
python build_segments.py --report-only
```

Anything with `--engine` in its help needs Unity in play mode, which needs the assets.

## Why this is a mirror, not a fork

This branch keeps its own short history, unrelated to the working repositories'. Theirs is full of LFS
pointers, so pushing any of it would pull 3.2 GB of objects onto a service this branch is specifically
avoiding using that way. The two working repositories stay the source of truth:

- the Unity project, whose git is the Windows one, because the KB is a derivative of its animation assets
- `animation-agent`, on WSL's ext4 with Linux git — engine-independent, and versioned on its own

Nothing here is edited in place. `sync.sh` rewrites every published file from whichever repository
owns it, reports anything that disappeared upstream, and lists upstream files that are new and not
covered by `.pubignore` -- so carrying a new file on this branch stays a decision rather than a
default. Run it, read what it says, then commit:

```sh
./sync.sh                 # refresh what is published; report what is new upstream
./sync.sh --adopt         # and take the new files it just listed
git add -A && git commit && git push
```

`--adopt` exists so nobody hand-copies. A new file carried across from the Windows working tree
arrives CRLF onto a branch pinned to LF, and every later run then calls it changed; `--adopt` reads
it the same way everything else here is read. Which files belong on a published branch is still a
judgement — that is what the report above is for — and this only removes the copying from it.

It reads the Unity side through `git.exe`, never through this WSL git, and takes content from the
object store rather than the Windows working tree. Both reasons are written out at the top of the
script; they are the difference between a clean run and several hundred files that only look changed.
