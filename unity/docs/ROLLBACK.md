# ROLLBACK — literal revert recipes

One page of "how do I undo X" for this repo. Rollback here is git + asset reassignment, by design
(see `adr/0005-git-as-version-and-ledger.md`); there is no separate rollback service.

> Since 2026-08-05 the validators live in the separate **`animation-agent`** repository (WSL). Run every
> `python …` line below from there with `MOTIONKB_DIR` pointing at this repo's `agent/animation_knowledge_base/`; run every
> `git …` line here, on the Windows side. The KB itself is still versioned in **this** repository.

## Roll back the whole MotionKB to a named version
Each contract version has a tag. Make new ones annotated, with a message that names the snapshot and
spells out its own checkout command — `kb/v1` and `kb/v3` do, and `kb/v2` is lightweight only because
somebody typed `git tag kb/v2`. See `../HANDOFF.md` §8.2 module C.

    git checkout kb/v3 -- agent/animation_knowledge_base/
    python validate_motionkb.py        # confirm the restored set is valid

**`kb/v3` is the only tag that command works against.** `kb/v1` and `kb/v2` predate the 2026-08-05
move (ADR 0017) and hold the KB at `Assets/MotionKB/`, not `agent/animation_knowledge_base/`, so
against those two it matches nothing and exits 0 — a silent no-op, not a rollback. Use the old path
and move the result:

    git checkout kb/v2 -- Assets/MotionKB/
    git mv Assets/MotionKB agent/animation_knowledge_base        # or move the files and delete the stray .meta

Check first, always: `git ls-tree --name-only <tag>` tells you which layout that tag has.

**Rolling back to `kb/v2` is not just a path change — it is a different contract.** That snapshot is
`motionkb/v2`: every channel carries the `posture_label` / `posture_magnitude` / `posture_measurement`
triple and no `mean_pose`, and `field_origin` names its tier `measured`. Restoring it means restoring
`motionkb.v2.schema.json` as the validator's target and a `config.py` that still holds `REFERENCE_POSE`
/ `POSTURE_DIVISOR` / `NEUTRAL` — which live in the `animation-agent` repository, so a KB-only checkout
leaves the two halves disagreeing. Roll the pipeline back to the matching commit as well, or do not
roll back past `kb/v3`. `kb/v1` is a third contract again (6 body parts, not 9 channels).

List versions: `git tag --list 'kb/*'` — `kb/v1` (the retired 6-part store), `kb/v2` (the 9-channel
store with the posture triple) and `kb/v3` (the current one: `mean_pose`, KINEMATIC, formula v3.0.0).
`manifest.json`'s `rollback_tag` names the tag matching the store as it stands; the human changelog is
`agent/animation_knowledge_base/schema/CHANGELOG.md`.

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

A breaking schema change should be a new id (`motionkb/v3` was the last one) + a converter, not an
in-place edit — see `adr/0001-data-contract-first.md` and
`agent/animation_knowledge_base/schema/CHANGELOG.md`. The retired contracts are kept: v1 under
`agent/motionkb_build/archive/`, v2 beside the live one in `agent/animation_knowledge_base/schema/`.

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
