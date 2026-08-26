# 0012 — The accepted store is a directory, not a naming convention

Status: Accepted (2026-08-20); the directory it names is
`agent/animation_knowledge_base/actions/` since [0017](0017-knowledge-base-and-its-build-artifacts.md),
and 0017 also removed the last consumer still carrying the denylist this record was written to delete.
The two-store half is superseded by
[0016](0016-one-store-status-is-the-membership-test.md) (2026-08-21), which merged `candidate/` into
`actions/` and made `status` the membership test. What stands is this record's actual decision — that
records live in a directory of their own so membership is read off the path instead of asserted with a
denylist. Refines the layout ADR 0001 fixes the contract for. The schema, the field semantics and every
measured value are untouched — this record is only about where the files sit.

## Context

Until now the accepted action records were loose `*.json` files in the KB root, sharing that directory
with three files that are not action records:

| file | what it is |
| --- | --- |
| `engine_mask_map.json` | the 9-channel → Unity `AvatarMask` mapping |
| `manifest.json` | the generated corpus index |
| `retrieval_eval_set.json` | the retrieval evaluation seed |

Nothing in a path said which of the eleven files was an action record, so every consumer that walked
the store had to assert it instead. That assertion was the same three-element set, and it had been
pasted into **seven** files under **two** names:

```
extract.py                NON_ACTION_FILES
propose.py                NON_ACTION_FILES
gen_kb_manifest.py        NON_ACTION_FILES
validate_motionkb.py      NON_ACTION_FILES
test_golden_extraction.py NON_ACTION_FILES
recalibrate_measured.py   NON_ACTION_FILES
agent/kbindex.py          _NOT_ACTIONS
```

It was not in `config.py`, and the two names meant a grep for one of them found six of the seven.

The failure mode is quiet and one-directional. Adding a fourth shared file to the KB root means
editing seven places; miss one and that consumer treats the new file as an action record — the
validator reports a schema failure on a file that was never meant to be an action, or worse,
`kbindex` tries to key it by an `action_id` it does not have. Nothing warns you at the time you add
the file, because the denylist is complete for every file that already exists.

Scale makes it concrete rather than theoretical. The 2446-clip Mixamo corpus is imported and waiting;
a KB root holding thousands of loose records plus a handful of shared files is not something `ls`
can answer a question about.

## Decision

The accepted records move to `kb/actions/`, one file per action, and that directory holds action
records and nothing else.

Membership is now the directory, so there is nothing to exclude. All seven denylists are **deleted**,
not repointed, and replaced by one function in the module that already owns every KB path:

```python
# paths.py
ACTIONS_DIR = os.path.join(KB_DIR, "actions")

def action_files(d=None):
    """Every action record in `d`, sorted; the accepted store by default."""
    return sorted(glob.glob(os.path.join(d or ACTIONS_DIR, "*.json")))
```

It takes a directory so `candidate/` — which likewise holds staged records and nothing else — is read
through the same call, which is what `validate_motionkb.collect_files` needs to check both stores.

The files moved with `git mv`, so per-record history follows them.

## Consequences

**Nothing about a record changed.** Same schema version, same `action_id`, same filename, same bytes.
The four gates pass unchanged: `validate_motionkb` 8/0, `test_golden_extraction` 8/0, `gen_kb_manifest`
8 actions, `validate_guids` 8 resolved. Golden passing is the load-bearing one — it recomputes every
MEASURED value from the frozen `raw` dumps, so a move that had disturbed a record would fail it.

**Validator output reads better by accident.** Paths are reported relative to the KB root, so a line
that used to say `PASS cpr.json` now says `PASS actions/cpr.json`, and a staged candidate is
`candidate/nurse_cpr_30.json`. Which store a result came from is now visible without knowing the
convention — the same ambiguity that let the accepted store go unvalidated for several green runs
until `collect_files` was fixed.

**One model-facing behaviour changes.** The agent's file tools mount the KB root, so
`glob('*.json', path='kb')` used to return the 8 records plus the 3 shared files — a mixed answer
that looked like an answer about records. It now returns the 3 shared files, and records are
`kb/actions/*.json`. `agent/prompt.py` names the new path, and the two tests that asserted the old
result assert the new one, including a new assertion that the root glob does not reach into
`actions/` or `raw/`.

**`kbindex.load` takes `actions_dir` instead of `kb_dir`.** Every call site passes nothing, so this is
a rename of an unused parameter, but the meaning is different and the name should say so.

**The `kb/v1` and `kb/v2` tags fall further behind.** They already hold the KB at the pre-2026-08-06
`Assets/MotionKB/` path, so the documented rollback was already a no-op against them; this adds one
more difference. That is worth fixing when the tags are next revised — it is a defect in the tags,
not a reason to keep a layout.

## Alternatives considered

**Move the three shared files into an `_`-prefixed directory instead.** `kb/*.json` would then mean
exactly the accepted records, the denylists would disappear just the same, and only three paths would
move rather than eight. Rejected on what the prefix would be claiming: every other `_` directory in
the KB (`raw`, `frames`, `derived`, `_reports`) holds regenerable working files, and
`engine_mask_map.json` is a hand-maintained part of the contract while `retrieval_eval_set.json` is
the evaluation ground truth. Filing them as working files to tidy the root would mislabel two of the
three. It also leaves the KB root as the action store, which does not survive 2446 records.

**Keep the flat layout and hoist the denylist into `config.py`.** One copy instead of seven, and a
much smaller change. Rejected because it keeps the shape of the problem: membership is still asserted
by a list somebody has to remember to update, rather than read off the path. The list would just be
wrong in one place instead of seven.
