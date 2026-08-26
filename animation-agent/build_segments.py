#!/usr/bin/env python3
"""
build_segments.py — regenerate the derived per-channel segment table, and report what it found.

The table is a CACHE, on the same terms as the seam table: `agent/segments.py` computes the same answer
live from `raw`, deleting the file costs a rebuild and nothing else, and nothing in the motionkb/v4
contract references it.

The report is the point of running this by hand. It prints, per action and channel, how far the channel
travels, whether it repeats and how cleanly, and how much of the clip the window keeps -- so that
"assembly takes one compression rather than thirty" is a number somebody checked rather than a claim.

    python build_segments.py                # write the table + print the report
    python build_segments.py --report-only  # print, write nothing
"""
import argparse
import sys

from agent import segments as S
from agent import transitions as T
from agent.kbindex import KBIndex


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    kb = KBIndex.load()
    clips = T.load_clips(kb)
    table = S.build_table(clips)

    print("%d actions, %d channels each\n" % (len(table), len(next(iter(table.values())))))
    # `state` where this used to print `role`. The role labels are gone with motionkb/v4 (ADR 0022),
    # and nothing replaces them here: which channel MATTERS is a fact about the task, not about the
    # clip, so a report on the clip cannot carry it. What is measured is whether the part moves at
    # all, which is also the column that makes the rest of the row readable -- a static channel with
    # 0.4 degrees of travel is not a channel anything should be grafted from.
    print("%-13s %-11s %-7s %9s %8s %9s %8s %7s"
          % ("action", "channel", "state", "travel", "peak/s", "window", "cycle", "keeps"))

    cyclic, trimmed = [], []
    for action_id in sorted(table):
        channels = kb.channels(action_id)
        for seg in table[action_id]:
            state = (channels.get(seg["channel"]) or {}).get("state") or "-"
            keeps = seg["frames"] / float(seg["clip_frames"])
            cycle = ("%df @%.2f" % (seg["cycle_frames"], seg["cycle_residual_deg"])
                     if seg["cycle_frames"] else "-")
            print("%-13s %-11s %-7s %9.1f %8.1f %9s %8s %6.0f%%"
                  % (action_id, seg["channel"], state, seg["travel_deg"], seg["peak_deg_per_s"],
                     "%d-%d/%d" % (seg["start_frame"], seg["end_frame"], seg["clip_frames"]),
                     cycle, keeps * 100))
            if seg["cycle_frames"]:
                cyclic.append((action_id, seg))
            elif keeps < 0.95:
                trimmed.append((action_id, seg, keeps))
        print()

    # THE TWO THINGS THIS TABLE IS FOR, separated, because they are worth very different amounts and
    # reporting one number would hide which one carried it.
    print("repeating channels -- assembly takes ONE repetition instead of the whole clip:")
    if not cyclic:
        print("   none")
    for action_id, seg in cyclic:
        print("   %-13s %-11s %d frames of %d  (%.0fx shorter, ends %.2f deg apart)"
              % (action_id, seg["channel"], seg["frames"], seg["clip_frames"],
                 seg["clip_frames"] / float(seg["frames"]), seg["loop_gap_deg"]))

    print("\nchannels with dead ends worth trimming (window keeps under 95%):")
    if not trimmed:
        print("   none")
    for action_id, seg, keeps in sorted(trimmed, key=lambda r: r[2]):
        print("   %-13s %-11s keeps %3.0f%%  (head %d, tail %d frames)"
              % (action_id, seg["channel"], keeps * 100, seg["start_frame"],
                 seg["clip_frames"] - seg["end_frame"]))

    if not args.report_only:
        path = S.write_table(table, path=args.out)
        print("\nwrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
