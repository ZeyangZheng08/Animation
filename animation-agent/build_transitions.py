#!/usr/bin/env python3
"""
build_transitions.py — regenerate the derived seam table, and report what it found.

The table is a CACHE. `kb_transition` computes the same answer live from `_raw`, so deleting this file
costs a rebuild and nothing else; it exists so the agent's tool call is a lookup instead of a 56-pair
search. Nothing in the motionkb/v2 contract references it.

The report is the point of running this by hand: it prints, for every ordered pair, what a direct cut
costs, what the aligned seam costs, and what routing through `idle` would cost — which is what the
Animator does today. Pairs where the idle detour is WORSE are called out, because that is the evidence
for removing it rather than an opinion about it.

    python build_transitions.py                # write the table + print the report
    python build_transitions.py --report-only  # print, write nothing
"""
import argparse
import sys

from agent import transitions as T
from agent.kbindex import KBIndex


def direct_cost(a, b, clips):
    ca, cb = clips[a], clips[b]
    shared = [n for n in ca.pose_bones if n in cb.rot]
    return T.pose_distance(ca, ca.frames - 1, cb, 0, shared)


def via_idle_cost(a, b, clips, hub="idle"):
    """Worst of the two seams on the A -> idle -> B path. A detour is only as good as its worse half."""
    if hub not in clips or a == hub or b == hub:
        return None
    return max(direct_cost(a, hub, clips), direct_cost(hub, b, clips))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    kb = KBIndex.load()
    clips = T.load_clips(kb)
    seams = T.build_table(kb)

    print("%d actions, %d ordered pairs\n" % (len(clips), len(seams)))
    print("%-14s %-14s %8s %8s %8s %7s %-16s" %
          ("from", "to", "direct", "aligned", "viaIdle", "blend", "class"))
    worse_via_idle = []
    for s in seams:
        d = direct_cost(s.from_action, s.to_action, clips)
        v = via_idle_cost(s.from_action, s.to_action, clips)
        if v is not None and v > d:
            worse_via_idle.append((s.from_action, s.to_action, d, v))
        print("%-14s %-14s %8.2f %8.2f %8s %6df %-16s" %
              (s.from_action, s.to_action, d, s.cost_deg,
               "-" if v is None else "%.2f" % v, s.blend_frames, s.cls))

    by_class = {}
    for s in seams:
        by_class[s.cls] = by_class.get(s.cls, 0) + 1
    print("\nby class: " + ", ".join("%s=%d" % kv for kv in sorted(by_class.items())))

    print("\n%d of %d pairs are WORSE through idle than cut directly:"
          % (len(worse_via_idle), len(seams)))
    for a, b, d, v in sorted(worse_via_idle, key=lambda r: r[2] - r[3])[:10]:
        print("   %-14s -> %-14s  direct %5.2f  vs viaIdle %5.2f   (+%.2f)" % (a, b, d, v, v - d))

    posture = [s for s in seams if s.cls == T.CLASS_POSTURE_CHANGE]
    if posture:
        print("\nposture changes (no clip covers these — they have to be generated):")
        for s in posture:
            print("   %-14s -> %-14s  %s" % (s.from_action, s.to_action,
                                             s.notes[0] if s.notes else ""))

    if not args.report_only:
        path = T.write_table(seams, path=args.out)
        print("\nwrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
