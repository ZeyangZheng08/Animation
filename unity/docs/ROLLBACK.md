# ROLLBACK — literal revert recipes

One page of "how do I undo X" for this repo. Rollback here is git + asset reassignment, by design
(see `adr/0005-git-as-version-and-ledger.md`); there is no separate rollback service.

> Since 2026-08-05 the validators live in the separate **`animation-agent`** repository (WSL). Run every
> `python …` line below from there with `MOTIONKB_DIR` pointing at this repo's `agent/animation_knowledge_base/`; run every
> `git …` line here, on the Windows side. The KB itself is still versioned in **this** repository.

## Roll back the whole MotionKB to a named version
Each contract version has a tag. Make new ones annotated, with a message that names the snapshot and
spells out its own checkout command — `kb/v1`, `kb/v3` and `kb/v4` do, and `kb/v2` is lightweight only
because somebody typed `git tag kb/v2`. See `../HANDOFF.md` §8.2 module C.

    git checkout kb/v4 -- agent/animation_knowledge_base/
    python validate_motionkb.py        # confirm the restored set is valid

**`kb/v4` is the current store; `kb/v3` is the one step back.** Those two are the only tags that
command works against. `kb/v1` and `kb/v2` predate the 2026-08-05 move (ADR 0017) and hold the KB at
`Assets/MotionKB/`, not `agent/animation_knowledge_base/`, so against those two it matches nothing
and exits 0 — a silent no-op, not a rollback. Use the old path and move the result:

    git checkout kb/v2 -- Assets/MotionKB/
    git mv Assets/MotionKB agent/animation_knowledge_base        # or move the files and delete the stray .meta

Check first, always: `git ls-tree --name-only <tag>` tells you which layout that tag has.

**Rolling back to `kb/v3` is a different contract, not just older data.** That snapshot is
`motionkb/v3`: every anatomical channel carries the SEMANTIC 5-tuple (`role` / `motion_type` /
`contact` / `constraint` / `target`), and the top level carries `display_name`, `tags`,
`mask_coverage`, `ik_goals`, `composability` and `overall_intent` where v4 has `action_description`
(ADR 0022). Restoring it means restoring `motionkb.v3.schema.json` as the validator's target, a
`config.py` at `EXTRACTOR_VERSION` 3.1.0, a validator that still has `validate_semantic_consistency`,
and a RUNTIME that derives the channel partition from `role` instead of reading it off the plan —
all of which live in the `animation-agent` repository, so a KB-only checkout leaves the two halves
disagreeing in a way that fails loudly at the schema and silently at the assembler. Roll the pipeline
and the runtime back to the matching commit as well, or do not roll back past `kb/v4`.

**Rolling back to `kb/v2` is a third contract again**: `posture_label` / `posture_magnitude` /
`posture_measurement` per channel and no `mean_pose`, `field_origin` naming its tier `measured`, and
a `config.py` that still holds `REFERENCE_POSE` / `POSTURE_DIVISOR` / `NEUTRAL`. `kb/v1` is a fourth
(6 body parts, not 9 channels).

List versions: `git tag --list 'kb/*'` — `kb/v1` (the retired 6-part store), `kb/v2` (the 9-channel
store with the posture triple), `kb/v3` (`mean_pose`, KINEMATIC, formula v3.0.0, the full SEMANTIC
half) and `kb/v4` (the current one: descriptions only, formula still v3.0.0 — no number differs from
v3). `manifest.json`'s `rollback_tag` names the tag matching the store as it stands; the human
changelog is `agent/animation_knowledge_base/schema/CHANGELOG.md`.

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

A breaking schema change should be a new id (`motionkb/v4` was the last one) + a converter, not an
in-place edit — see `adr/0001-data-contract-first.md` and
`agent/animation_knowledge_base/schema/CHANGELOG.md`. The converter for v3 → v4 is
`extract.py migrate` in the `animation-agent` repository; it is idempotent and reads no pose dump,
because v4 moved no number. The retired contracts are kept: v1 under `agent/motionkb_build/archive/`,
v2 and v3 beside the live one in `agent/animation_knowledge_base/schema/`.

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
