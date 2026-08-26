# ROLLBACK — literal revert recipes

One page of "how do I undo X" for this repo. Rollback here is git + asset reassignment, by design
(see `adr/0005-git-as-version-and-ledger.md`); there is no separate rollback service.

> Since 2026-08-05 the validators live in the separate **`animation-agent`** repository (WSL). Run every
> `python …` line below from there with `MOTIONKB_DIR` pointing at this repo's `agent/animation_knowledge_base/`; run every
> `git …` line here, on the Windows side. The KB itself is still versioned in **this** repository.

## Roll back the whole MotionKB to a named version
Once a version tag exists (`git tag kb/<ver>`; see `../HANDOFF.md` §8.2 module C):

    git checkout kb/<ver> -- agent/animation_knowledge_base/
    python validate_motionkb.py        # confirm the restored set is valid

**The existing `kb/v1` and `kb/v2` tags predate the 2026-08-05 move and hold the KB at
`Assets/MotionKB/`, not `agent/animation_knowledge_base/`.** Against those two tags the command above matches nothing and
exits 0 — a silent no-op, not a rollback. Use the old path and move the result:

    git checkout kb/v2 -- Assets/MotionKB/
    git mv Assets/MotionKB agent/animation_knowledge_base        # or move the files and delete the stray .meta

Check first, always: `git ls-tree --name-only <tag>` tells you which layout that tag has.

List versions: `git tag --list 'kb/*'` (currently `kb/v1` — the retired 6-part store — and `kb/v2`). The
human changelog is `agent/animation_knowledge_base/schema/CHANGELOG.md`.

## Discard a bad re-extraction (before acceptance)
Since ADR 0016 there is one store: re-extraction writes the record in place at
`agent/animation_knowledge_base/actions/<clip_name>.json`, and `extract.py assemble` skips anything already `accepted`, so an
accepted record is not what a re-extraction touches. To drop the work,
`git checkout -- agent/animation_knowledge_base/actions/<clip_name>.json`.

Accepting a record RENAMES it (`<clip_name>.json` -> `<action_id>.json`), so undoing an acceptance is a
two-file operation:

    git checkout -- agent/animation_knowledge_base/actions/            # discard everything uncommitted in the store
    python gen_kb_manifest.py                    # the manifest indexes the accepted subset — regenerate it

## Revert a single accepted action file

    git checkout -- agent/animation_knowledge_base/actions/<id>.json          # discard uncommitted edits
    git checkout <commit> -- agent/animation_knowledge_base/actions/<id>.json # or revert to a past commit
    python gen_kb_manifest.py                           # only if status or provenance changed

Then re-run `python validate_motionkb.py`.

## Roll back the schema

    git checkout <commit> -- agent/motionkb_build/archive/motionkb.v1.schema.json

A breaking schema change should be a new id (`motionkb/v2`) + a converter, not an in-place edit —
see `adr/0001-data-contract-first.md` and `agent/animation_knowledge_base/schema/CHANGELOG.md`.

## Revert a de-clipped bed mesh (Cover / Sheet / Mattress)
The blanket / sheet / mattress use edited source meshes (`*_declipped.mesh`). To revert a layer: in
`EmergencyRoom.unity`, reassign that SkinnedMeshRenderer's `sharedMesh` back to the matching
`hospital_bed.fbx` sub-mesh (`Cover` / `Sheet` / `mattress`) and delete the `_declipped.mesh` asset.
Full context: `../HANDOFF.md` §0 (the 2026-06-17 notes).

## Restore the earlier patient pose (P3, flat / unresponsive)
A full backup scene exists at the repo root: `EmergencyRoom.P3backup.unity`. Open it, or copy the
patient/bed transforms from it. The current scene is P0 (awake/reclining); see `../HANDOFF.md` §5.

## Find what actually drifted
Single-author repo, so git is the drift detector (no stored content hashes — ADR 0005):

    git status
    git diff --ignore-all-space --numstat     # real edits, ignoring CRLF / LFS noise
