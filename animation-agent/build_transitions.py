#!/usr/bin/env python3
"""
build_transitions.py — verify the seam search on a sample of pairs. It no longer builds anything.

WHY THERE IS NOTHING LEFT TO BUILD. This used to write `derived/transitions.json`: every ordered pair
of an eight-action store, 56 seams, under a second, and a table the tool call could look up instead of
searching. The knowledge base is 2446 actions now. That is 5,981,970 ordered pairs and roughly seven
CPU-hours, to precompute answers to questions the agent will ask a few dozen of. So seams are computed
when they are asked for and kept in a bounded LRU keyed on the two dumps' content digests and
`SEAM_ALGORITHM_VERSION` (agent/transitions.py). The old table moved out with the eight records it
covered, and is frozen at agent/nursing_assets/derived/transitions.json.

WHAT IS LEFT IS THE CHECK. A search that is only ever run on demand is a search nobody looks at, so
this samples pairs, runs the real `find_seam` on them, and asserts the properties the seam search
claims for every answer it gives:

  * the cut lands inside the search window at each end, and outside either clip's payload;
  * the blend length is exactly what the stated angular-rate rule gives for the busiest channel, so
    MAX_BLEND_RATE_DEG_PER_S is a ceiling rather than a decoration;
  * the class follows from the blend length and the two postures, and nothing else;
  * asking twice returns the same object — the LRU is keyed on content, so a repeat is free.

    python build_transitions.py                      # 40 pairs, seed 0
    python build_transitions.py --pairs 200          # more of them
    python build_transitions.py --pair mx_Walking_Forward mx_Standing_Idle
Exit: 0 when every sampled pair holds every property, 1 otherwise.
"""
import argparse
import random
import sys

from agent import transitions as T
from agent import kbindex as KI
from agent.kbindex import KBIndex

DEFAULT_PAIRS = 40


def sample_pairs(action_ids, count, seed):
    """`count` distinct ordered pairs, drawn deterministically.

    Drawn from a POOL rather than from all 2446, so the run touches a few dozen dumps instead of a few
    hundred: reading the corpus is the cost here, not the search.
    """
    rng = random.Random(seed)
    pool = sorted(action_ids)
    if len(pool) > 2 * count:
        pool = sorted(rng.sample(pool, 2 * count))
    pairs, seen = [], set()
    guard = 0
    while len(pairs) < count and guard < count * 100:
        guard += 1
        a, b = rng.choice(pool), rng.choice(pool)
        if a == b or (a, b) in seen:
            continue
        seen.add((a, b))
        pairs.append((a, b))
    return pairs


def check(seam, kb, clips):
    """[complaint, ...] — the properties the seam search claims, checked on one answer."""
    bad = []
    a, b = clips[seam.from_action], clips[seam.to_action]
    rec_a, rec_b = kb.actions[seam.from_action], kb.actions[seam.to_action]

    from_allowed = T._search_range(a, rec_a, tail=True)
    to_allowed = T._search_range(b, rec_b, tail=False)
    if seam.from_frame not in from_allowed:
        bad.append("cuts %s at frame %d, outside its search window/payload budget"
                   % (seam.from_action, seam.from_frame))
    if seam.to_frame not in to_allowed:
        bad.append("enters %s at frame %d, outside its search window/payload budget"
                   % (seam.to_action, seam.to_frame))

    want = T.blend_frames_for(seam.pace_deg(), a.fps or 30)
    if seam.blend_frames != want:
        bad.append("blend is %d frames; the %.0f deg/s rule gives %d for %.2f deg at %d fps"
                   % (seam.blend_frames, T.MAX_BLEND_RATE_DEG_PER_S, want, seam.pace_deg(),
                      a.fps or 30))
    if seam.blend_frames > 0:
        rate = seam.pace_deg() / (seam.blend_frames / float(a.fps or 30))
        if rate > T.MAX_BLEND_RATE_DEG_PER_S + 1e-6:
            bad.append("busiest channel sweeps %.1f deg/s, over the stated %.0f"
                       % (rate, T.MAX_BLEND_RATE_DEG_PER_S))

    want_class = T.classify(seam.blend_frames, KI.posture_of(rec_a), KI.posture_of(rec_b))
    if seam.cls != want_class:
        bad.append("class is %s; the blend length and the two postures give %s"
                   % (seam.cls, want_class))
    return bad


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=DEFAULT_PAIRS)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--pair", nargs=2, action="append", default=None,
                    help="verify this ordered pair (repeatable); overrides sampling")
    args = ap.parse_args(argv)

    kb = KBIndex.load()
    if args.pair:
        pairs = [tuple(p) for p in args.pair]
        unknown = sorted({a for p in pairs for a in p} - set(kb.actions))
        if unknown:
            print("unknown action_id(s): %s" % ", ".join(unknown))
            return 2
    else:
        pairs = sample_pairs(kb.actions, args.pairs, args.seed)

    print("seam algorithm %s — %d of %d ordered pairs (%d actions, seed %d)\n"
          % (T.SEAM_ALGORITHM_VERSION, len(pairs), len(kb.actions) * (len(kb.actions) - 1),
             len(kb.actions), args.seed))
    print("%-44s %-44s %8s %6s %-15s" % ("from", "to", "cost", "blend", "class"))

    failures, by_class = [], {}
    for from_id, to_id in pairs:
        clips = {a: T.load_clip((kb.actions[a].get("source_clip") or {})["clip_name"],
                                loop=bool(kb.actions[a].get("loop")))
                 for a in (from_id, to_id)}
        seam = T.find_seam(from_id, to_id, kb, clips)
        again = T.find_seam(from_id, to_id, kb, clips)
        bad = check(seam, kb, clips)
        if again is not seam:
            bad.append("asking twice recomputed instead of hitting the LRU")
        by_class[seam.cls] = by_class.get(seam.cls, 0) + 1
        print("%-44s %-44s %8.2f %5df %-15s%s"
              % (from_id, to_id, seam.cost_deg, seam.blend_frames, seam.cls,
                 "  <- FAIL" if bad else ""))
        for line in bad:
            print("        - %s" % line)
        if bad:
            failures.append((from_id, to_id, bad))

    print("\nby class: " + ", ".join("%s=%d" % kv for kv in sorted(by_class.items())))
    print("%d of %d pair(s) hold every property" % (len(pairs) - len(failures), len(pairs)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
