#!/usr/bin/env python3
"""
recalibrate_kinematic.py — rewrite the KINEMATIC block of every accepted record in place.

Run this once after a deliberate `metric_formula_version` bump. It re-runs
`metrics.channel_blocks` over each record's frozen `raw` dump and writes back only the KINEMATIC
fields (`kind`, `state_label`, `motion_magnitude`, `raw_measurement`, `mean_pose`, and the root's
`mean_body_height` / `mean_body_tilt_deg`) plus the `extraction` block — exactly the split ADR 0002
draws. SEMANTIC fields, `composability`, `ik_goals`, `source_clip`, `controller_*` and `status` are
read and written back untouched.

Why this is not `extract.py assemble`: assemble skips accepted records outright, because re-measuring
the eight the KB is built from should never be a side effect of bringing in a new clip. This is the
deliberate path — it rewrites the KINEMATIC block and nothing else, and says what it would change
before it changes it.

    python3 recalibrate_kinematic.py --dry-run    # show every magnitude that would change
    python3 recalibrate_kinematic.py              # write

Afterwards run `./check_kb.sh`: the golden test recomputes KINEMATIC from the same `raw` and so
tracks the new values automatically, and the validator re-checks the contract.
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as C            # noqa: E402
import metrics                # noqa: E402
import paths                  # noqa: E402
import unity_sampler          # noqa: E402
import extract as E           # noqa: E402


def accepted_files():
    """(path, record) for the accepted records — the ones whose KINEMATIC half is frozen as golden."""
    out = []
    for p, doc, err in paths.read_records(paths.accepted_files()):
        if err:
            raise SystemExit("cannot read %s: %s" % (p, err))
        out.append((p, doc))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report the change, write nothing")
    args = ap.parse_args(argv)

    records = accepted_files()
    if not records:
        raise SystemExit("no accepted records under %s" % paths.ACTIONS_DIR)
    print("formula version in config: %s" % C.FORMULA_VERSION)
    print("%d accepted record(s)%s\n" % (len(records), "  [DRY RUN]" if args.dry_run else ""))

    header = "%-14s %-22s %8s %8s   %s" % ("action", "channel", "before", "after", "raw (unchanged)")
    changed = failed = 0
    for path, doc in records:
        aid = doc.get("action_id") or os.path.basename(path)
        clip = (doc.get("source_clip") or {}).get("clip_name")
        try:
            raw = unity_sampler.read_raw(clip)
        except Exception as e:
            print("%-14s SKIPPED — no raw for clip %r (%s)" % (aid, clip, e))
            failed += 1
            continue
        blocks = metrics.channel_blocks(raw)

        before = {c: (doc.get("channels", {}).get(c) or {}).get("motion_magnitude")
                  for c in C.STATE_CHANNELS}
        state_before = {c: (doc.get("channels", {}).get(c) or {}).get("state_label")
                        for c in C.STATE_CHANNELS}
        E._apply_kinematic(doc, blocks)
        doc["duration"] = round(raw["length"], 3)
        doc["frame_rate"] = raw["frame_rate"]
        doc["extraction"] = _merge_extraction(doc, E._build_extraction(raw))

        rows, flipped = [], []
        for c in C.STATE_CHANNELS:
            aft = doc["channels"][c]["motion_magnitude"]
            if before[c] != aft:
                rows.append("%-14s %-22s %8s %8s   %s"
                            % (aid, c, before[c], aft,
                               doc["channels"][c]["raw_measurement"].get("raw_value", "-")))
            if state_before[c] is not None and state_before[c] != doc["channels"][c]["state_label"]:
                flipped.append("%s %s: %s -> %s" % (aid, c, state_before[c],
                                                    doc["channels"][c]["state_label"]))
        if rows:
            print(header if changed == 0 else "")
            print("\n".join(rows))
            changed += 1
        if flipped:
            # A divisor-only migration must never reach here: STATIC applies to the raw signal.
            # A migration that changes the SIGNAL (ADR 0011 moved measurement into muscle space)
            # legitimately does, so this is a list to review, not an alarm -- every entry should be
            # explainable, and one that is not is the signal to stop.
            print("  ** state_label changed - review each of these:")
            for f in flipped:
                print("     " + f)
        if not args.dry_run:
            E._atomic_write(path, doc)

    print("\n%d record(s) updated%s, %d skipped." %
          (changed, " (dry run — nothing written)" if args.dry_run else "", failed))
    if not args.dry_run:
        print("Next: ./check_kb.sh")
    return 1 if failed else 0


def _merge_extraction(doc, fresh):
    """Keep the provenance the migration has no business rewriting.

    `_build_extraction` returns a whole block, but a formula migration is not a re-authoring: the
    VLM proposal that produced the semantics, and who verified it, still stand.
    """
    old = doc.get("extraction") or {}
    for k in ("vlm_proposal", "verified_by", "verified_at", "verified_against_screenshots"):
        if k in old:
            fresh[k] = old[k]
    return fresh


if __name__ == "__main__":
    sys.exit(main())
