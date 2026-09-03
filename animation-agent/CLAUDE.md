# CLAUDE.md — animation_agent (WSL side)

Read the Unity repo's `HANDOFF.md` (§0 state, §1.1 the Windows/WSL split and this workstation's notes) and
`README.md` first; `HPC_HANDOFF.md` here covers the cluster side.

## Language
Talk to the user in **Chinese**. Code, comments, JSON, identifiers, file names and handoff documents are
**English**.

## Where things are on this machine
| what | path |
|---|---|
| this repo (pipeline + runtime service) | `~/Research/animation_agent`, conda env `animation-agent` (`~/miniforge3`) |
| Unity project + MotionKB | `/mnt/d/Research/AI_agent/Animation_agent/Animation` (Windows `D:\Research\AI_agent\Animation_agent\Animation`) |
| MotionKB | `$MOTIONKB_DIR` = `.../Animation/agent/animation_knowledge_base` (set in `~/.profile`; treat read-only) — 2446 Mixamo clips, the only formal library |
| frozen nursing assets | `.../Animation/agent/nursing_assets/` — the 8 hand-authored actions, records + dumps + frames + derived. **Nothing reads it**: not the runtime, the index, the prompt, the file tools, the pipeline, the gates or the tests. Held out for an evaluation that does not exist yet; do not wire it back in |
| retired code | `~/Research/animation_agent/legacy/eval_8_actions/` — the 8-action retrieval eval, archived with the records it scored. Kept as a record, not run |
| MCP for Unity fork | `/mnt/d/Research/AI_agent/Animation_agent/unity-mcp-research` (branch `research`, keep 9.7.2-beta.9; two uncommitted local edits are intentional) |

## Boundary rules (each was learned by breaking it — HANDOFF §1.1)
1. **The Unity repo's git is only ever Windows git:** `git.exe -C "D:/Research/AI_agent/Animation_agent/Animation" ...`.
   Never run Linux git on `/mnt/d` — it reports ~800 phantom-dirty files (CRLF + mode bits over DrvFs).
2. Read Unity-side file content with `git.exe ... show HEAD:path` when comparing bytes; the worktree is CRLF.
3. Editing files under `/mnt/d` is fine; preserve line endings. Never copy a worktree across `/mnt/*` (mode bits) — clone.
4. `/mnt/d` is 9p: ~7 s per unscoped grep, 82 s for the full suite. Scope searches to `Assets/Scripts` and
   `agent/animation_knowledge_base/actions`.
5. Select files by extension whitelist, never by subtracting `git lfs ls-files`; use `-n` for that listing.

## Driving the Unity editor (MCP for Unity, HTTP on port 8080)
- The Python server runs on **Windows**, started from the Unity window (*Window > MCP For Unity*, Connect tab,
  HTTP Local `http://127.0.0.1:8080`, Start Server). WSL reaches it through mirrored networking; the client
  entry is `.mcp.json` here (`UnityMCP` → `http://localhost:8080/mcp`) and the same at user scope.
- Before any tool call: invoke the `unity-mcp-skill` skill, read the `mcpforunity://instances` resource, then
  `set_active_instance` with the full `Animation@<hash>` (the hash changes every session).
- `execute_code` is a C# 6 method body: no `using` directives, no classes; fully qualify ambiguous names
  (`UnityEngine.Object.DestroyImmediate`); `read_console` after script edits to confirm compilation.
- MCP is the **offline** channel (KB sampling / rendering / `validate_guids.py`). The runtime channel is the
  WebSocket on 8770 where this service is the *server*; it carries typed messages only, never code.
  `PROTOCOL_VERSION` is **v5** (`apply_root_motion` on a step). Bumping it means three sites, and the third
  is the one that gets forgotten: `agent/protocol.py` is the authority, `Assets/Scripts/AgentRuntime/Protocol.cs`
  mirrors it, and `terminal.py` is standard-library-only by design so it cannot import either — it now READS
  the constant out of the source instead of carrying a literal. Grep for the constant, not for the import;
  neither `smoke_validate.py` nor `drive.py` can catch a mismatch, because both import the contract.

## Commands
```sh
conda activate animation-agent
./check_kb.sh                        # validate 2446/2446 · golden 16/16 · manifest · posture sidecar · guid sample (last needs editor + bridge)
pytest -q                            # ~110 s, all green. `tests/corpus.py` names the clips the suite stands on
python probe_pairs.py --pairs 40     # sampled: 2446 actions is 5.98M ordered pairs, never all of them
python audit_posture.py              # the posture rules against 21 hand-labelled clips; 21/21
python build_posture.py --check      # gate 4 alone: recompute the sidecar and compare, no writes
python cli.py --engine --headless    # the runtime service by hand (ws 8770, console 8771)
git.exe -C "D:/Research/AI_agent/Animation_agent/Animation" status
```
`terminal.ps1` is per-machine and intentionally uncommitted here; Unity launches it on Play.
