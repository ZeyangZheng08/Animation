#!/usr/bin/env python3
"""
build_segments.py — regenerate the derived per-channel segment table, and report what it found.

The table is a CACHE: `agent/segments.py` computes the same answer live from `raw`, deleting the file
costs a rebuild and nothing else, and nothing in the motionkb/v4 contract references it. What it buys
is that a plan does not re-derive nine channels' windows for every layer it times.

O(N), NOT O(N^2). One pass over the 2446 accepted actions, one dump at a time. The seam table's
quadratic is what made a precomputed table impossible at this size (see agent/transitions.py); this one
asks a question about each clip ON ITS OWN, so the corpus costs 2446 answers rather than 5.98 million.

ONE DUMP AT A TIME IS THE POINT. The corpus's dumps are 1.4 GB of JSON; loading them all and then
computing, which is what `transitions.load_clips` does for a small store, does not fit in a process. So
each worker reads one dump, answers about it, and drops it — `memo=False` keeps them out of the clip
cache, which exists for the handful a plan revisits, not for a sweep that visits each clip once.

    python build_segments.py                      # write the table + the summary
    python build_segments.py --report-only        # print, write nothing
    python build_segments.py --only mx_Walking_Forward --only mx_Cross_Jumps
                                                  # per-channel detail for named actions, writes nothing
"""
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor

import paths
from agent import segments as S
from agent import transitions as T

# Enough to keep the DrvFs reads overlapping without holding many parsed dumps at once.
WORKERS = 8


def _corpus():
    """[(action_id, clip_name, loop, {channel: state_label})] for every accepted record."""
    out = []
    for path, doc, err in paths.read_records(paths.accepted_files()):
        if err:
            raise SystemExit("cannot read %s: %s" % (paths.rel(path), err))
        clip_name = (doc.get("source_clip") or {}).get("clip_name")
        if not clip_name:
            continue
        states = {name: (ch or {}).get("state_label")
                  for name, ch in (doc.get("channels") or {}).items()}
        out.append((doc["action_id"], clip_name, bool(doc.get("loop")), states))
    return sorted(out)


def _one(row):
    action_id, clip_name, loop, _ = row
    try:
        clip = T.load_clip(clip_name, loop=loop, memo=False)
        return (action_id, S.for_action(clip), clip.digest, None)
    except Exception as e:
        return (action_id, None, None, "%s: %s" % (type(e).__name__, e))


def build(rows, workers=WORKERS, progress=None):
    table, digests, failures, done = {}, {}, [], 0
    with ThreadPoolExecutor(max(1, min(workers, len(rows) or 1))) as pool:
        for action_id, segs, digest, err in pool.map(_one, rows):
            done += 1
            if err:
                failures.append((action_id, err))
            else:
                table[action_id] = segs
                digests[action_id] = digest
            if progress and done % 200 == 0:
                progress(done, len(rows))
    return table, digests, failures


def _detail(action_id, segs, states):
    """The per-channel row-per-channel view. Printed for named actions only: 2446 actions x 9 channels
    is 22 000 lines, which is a file rather than a report."""
    print("\n%s" % action_id)
    print("  %-11s %-8s %9s %8s %9s %8s %7s"
          % ("channel", "state", "travel", "peak/s", "window", "cycle", "keeps"))
    for seg in segs:
        keeps = seg["frames"] / float(seg["clip_frames"])
        cycle = ("%df @%.2f" % (seg["cycle_frames"], seg["cycle_residual_deg"])
                 if seg["cycle_frames"] else "-")
        print("  %-11s %-8s %9.1f %8.1f %9s %8s %6.0f%%"
              % (seg["channel"], states.get(seg["channel"]) or "-", seg["travel_deg"],
                 seg["peak_deg_per_s"],
                 "%d-%d/%d" % (seg["start_frame"], seg["end_frame"], seg["clip_frames"]),
                 cycle, keeps * 100))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-only", action="store_true", help="print, write nothing")
    ap.add_argument("--only", action="append", default=None,
                    help="per-channel detail for this action_id (repeatable); writes nothing")
    ap.add_argument("--out", default=None)
    ap.add_argument("--jobs", type=int, default=WORKERS)
    args = ap.parse_args(argv)

    paths.require_kb()
    rows = _corpus()
    if args.only:
        wanted = set(args.only)
        rows = [r for r in rows if r[0] in wanted]
        missing = wanted - {r[0] for r in rows}
        if missing:
            print("not accepted actions: %s" % ", ".join(sorted(missing)))
            return 2
    states_by_action = {r[0]: r[3] for r in rows}

    print("%d action(s), %d channels each" % (len(rows), len(S.CHANNEL_BONES)))
    table, digests, failures = build(rows, workers=args.jobs,
                                     progress=lambda d, n: print("  %d/%d" % (d, n)))
    for action_id, err in failures:
        print("  FAIL  %s: %s" % (action_id, err))

    if args.only:
        for action_id in sorted(table):
            _detail(action_id, table[action_id], states_by_action[action_id])
        return 1 if failures else 0

    # THE TWO THINGS THIS TABLE IS FOR, separated, because they are worth very different amounts and
    # one combined number would hide which of them carried it.
    cyclic = [(a, seg) for a in sorted(table) for seg in table[a] if seg["cycle_frames"]]
    trimmed = [(a, seg, seg["frames"] / float(seg["clip_frames"]))
               for a in sorted(table) for seg in table[a]
               if not seg["cycle_frames"] and seg["frames"] < seg["clip_frames"] * 0.95]
    actions_with_cycle = len({a for a, _ in cyclic})
    actions_with_trim = len({a for a, _, _ in trimmed})

    print("\nrepeating channels -- assembly takes ONE repetition instead of the whole clip:")
    print("   %d channel(s) across %d action(s)" % (len(cyclic), actions_with_cycle))
    for action_id, seg in sorted(cyclic, key=lambda r: -r[1]["clip_frames"] / float(r[1]["frames"]))[:10]:
        print("   %-52s %-11s %d frames of %d  (%.0fx shorter, ends %.2f deg apart)"
              % (action_id, seg["channel"], seg["frames"], seg["clip_frames"],
                 seg["clip_frames"] / float(seg["frames"]), seg["loop_gap_deg"]))

    print("\nchannels with dead ends worth trimming (window keeps under 95%):")
    print("   %d channel(s) across %d action(s)" % (len(trimmed), actions_with_trim))
    for action_id, seg, keeps in sorted(trimmed, key=lambda r: r[2])[:10]:
        print("   %-52s %-11s keeps %3.0f%%  (head %d, tail %d frames)"
              % (action_id, seg["channel"], keeps * 100, seg["start_frame"],
                 seg["clip_frames"] - seg["end_frame"]))

    if not args.report_only:
        path = S.write_table(table, path=args.out, digests=digests)
        print("\nwrote %s (%d actions)" % (paths.rel(path), len(table)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
