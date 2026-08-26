#!/usr/bin/env python3
"""probe_compose.py — every ordered pair played AT THE SAME TIME, and what comes out.

The companion to probe_pairs.py, which asks the other question. That one is about ORDER: can she get
from A to B. This one is about COMPOSITION: can A and B drive one body at once, and is the result two
sources or one wearing a disguise. "Assembles new actions out of the library" is a claim about 56
pairs, not about the one that was demonstrated.

FOUR OUTCOMES, AND ONLY ONE OF THEM IS A CAPABILITY:

  posture     refused before any channel is looked at. `typing` is the only seated action, so it
              composes with nothing -- 14 pairs, and no amount of channel work changes that.
  conflict    refused because the plan pins a hand two actions both drive. Nothing is pinned here,
              so this arm should report zero; it is run to prove the refusal did not simply
              disappear when it stopped being the default.
  degenerate  assembled, but only one action ends up driving anything. Every `idle` pair is here.
              Counting these as compositions is how a corpus of eight looks like a corpus of thirty,
              so they are reported separately and NOT added to the total.
  composed    two actions, both driving something. This is the number.

WHERE THE CHANNEL LISTS COME FROM, AND WHY THAT IS A STAND-IN. Since motionkb/v4 (ADR 0022) the
partition is not derivable from a record: `role` is gone, because which part of a clip matters is a
fact about the task. In a live turn the AGENT names the channels. This probe has no task and no
agent, so it uses the only honest substitute the kinematics offer -- the channels each clip actually
ANIMATES, which is the same pool `kb_search` hands the model as `moves`. That is a floor, not a
plan: an agent that names fewer channels contests fewer of them, so a pair composable here may be
composable in more ways than this counts, and a pair that is not is not composable at all.

WHAT A SHARED CHANNEL MEANS FOR THE COUNT. Six of the eight actions animate the right hand, so most
pairs have both clips reaching for a part. Nothing is dropped -- v3 detached the losing grip and
reported it, and there is no declared grip left to detach -- the part is HALVED between them and the
pair is reported as sharing it, so somebody can see which compositions are averages rather than
partitions.

    python probe_compose.py                # hermetic: the partition only, no engine
    python probe_compose.py --verbose      # every pair, not only the interesting ones
    python probe_compose.py --engine       # also commit the composed ones (needs play mode)
"""
import argparse
import asyncio
import itertools
import sys

from agent import assemble as A
from agent import kbindex as KI
from agent import segments as S
from agent import transitions as T
from agent.engine import DEFAULT_HOST, DEFAULT_PORT, EngineLink
from agent.kbindex import ANATOMICAL, KBIndex

POSTURE, CONFLICT, DEGENERATE, COMPOSED = "posture", "conflict", "degenerate", "composed"


def moves(kb, action_id):
    """The anatomical channels this clip animates. See the module docstring: this stands in for the
    channel list an agent would name, and it is the widest one that is honest."""
    channels = kb.channels(action_id)
    return [c for c in ANATOMICAL if (channels.get(c) or {}).get("state") == "dynamic"]


def driven(assembly):
    """Every action driving anything, outright or as a share. An action holding only a share owns no
    channel in `layers`, and reading ownership alone would count a real mix as degenerate."""
    out = {aid for aid, channels in assembly.layers if channels}
    out |= {aid for mix in assembly.shared for aid, _ in mix.shares}
    return sorted(out)


def classify(kb, base, overlay):
    """(outcome, assembly, note) for one ordered pair, with nothing pinned."""
    postures = KI.posture_of(kb.record(base)), KI.posture_of(kb.record(overlay))
    if postures[0] != postures[1]:
        return POSTURE, None, "%s is %s, %s is %s" % (base, postures[0], overlay, postures[1])
    wants = moves(kb, overlay)
    if not wants:
        # Refused by `normalise_overlays` rather than defaulted, so it is answered before it is asked:
        # an empty mask plays FULL BODY in the engine, and there is nothing here to mask to. `idle`
        # animates nothing at all, which is what makes every idle pair degenerate.
        return DEGENERATE, None, "%s animates no body part, so there is nothing to graft" % overlay
    assembly = A.arbitrate(base, [(overlay, wants)], kb, base_channels=moves(kb, base))
    if assembly.conflicts:
        return CONFLICT, assembly, "; ".join(c.why() for c in assembly.conflicts)
    partition = "; ".join("%s=%s" % (aid, "+".join(chans))
                          for aid, chans in assembly.layers if chans)
    shared = ", ".join(mix.channel for mix in assembly.shared)
    if shared:
        partition = "%s; shared %s" % (partition, shared) if partition else "shared " + shared
    parts = driven(assembly)
    if len(parts) < 2:
        return DEGENERATE, assembly, "only %s drives anything" % (parts[0] if parts else "nothing")
    return COMPOSED, assembly, partition


def windows(kb, segment_table, assembly):
    """Which part of each overlay's clip the assembly would take. The other half of what this probe
    is for: a composition that plays all 540 frames of `cpr` under a one-second walk is not the thing
    anybody meant by combining them."""
    out = []
    for aid, channels in assembly.layers:
        # Owned channels AND shared ones: a clip is one performance, so which frames of it are worth
        # playing is decided over everything it drives, not over the half it happens to own outright.
        channels = set(channels) | {mix.channel for mix in assembly.shared
                                    if aid in dict(mix.shares)}
        if aid == assembly.base or not channels:
            continue
        window = S.window_for(segment_table.get(aid), sorted(c for c in channels if c in ANATOMICAL))
        if window:
            out.append("%s frames %d-%d (%s)" % (aid, window["start_frame"], window["end_frame"],
                                                 window["why"]))
    return out


async def commit(kb, pairs, host, port, wait, who):
    """Will the engine actually play them. Needs play mode.

    Each pair is committed on its own and left running for a moment, because what is being checked is
    that two clips drive one body at once -- which is only visible while it plays.
    """
    from agent.tools import ToolRegistry
    from agent.tools import kb as kb_tools
    from agent.tools import scene as scene_tools

    results = {}
    async with EngineLink(host, port) as link:
        print("serving ws://%s:%d — enter play mode in Unity" % (host, port), flush=True)
        await link.wait_ready(timeout=wait)
        registry = kb_tools.register(ToolRegistry(), kb)
        scene_tools.register(registry, link, kb)
        for index, (base, overlay) in enumerate(pairs):
            # The same channel lists `classify` used, so what is committed is the partition this
            # probe just counted rather than a second one that merely looks like it.
            out = await registry.dispatch("plan_motion", {
                "base": base, "base_channels": moves(kb, base),
                "overlays": [{"action_id": overlay, "channels": moves(kb, overlay)}],
                "character": who, "mode": "commit"})
            ok = out.get("success") is not False
            results[(base, overlay)] = (ok, out.get("error") or "committed")
            print("  %3d/%d  %-4s %-14s + %-14s %s"
                  % (index + 1, len(pairs), "" if ok else "FAIL", base, overlay,
                     out.get("error") or ""), flush=True)
            await asyncio.sleep(0.6)
    return results


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", action="store_true",
                    help="also commit each composed pair against a running executor (play mode)")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--wait", type=int, default=120)
    ap.add_argument("--verbose", action="store_true", help="print every pair, not only the composed")
    ap.add_argument("--character", default="Jill", help="who to drive under --engine")
    args = ap.parse_args(argv)

    kb = KBIndex.load()
    ids = sorted(kb.actions)
    pairs = list(itertools.permutations(ids, 2))
    segment_table = S.read_table() or S.build_table(T.load_clips(kb))

    outcomes = {}
    for pair in pairs:
        outcomes[pair] = classify(kb, *pair)

    print("== %d ordered pairs over %d actions, played at the same time ==\n" % (len(pairs), len(ids)))
    for pair in pairs:
        outcome, assembly, note = outcomes[pair]
        if outcome != COMPOSED and not args.verbose:
            continue
        print("  %-10s %-14s + %-14s %s" % (outcome, pair[0], pair[1], note))
        for line in windows(kb, segment_table, assembly) if assembly else []:
            print("             takes %s" % line)

    counts = {name: sum(1 for o, _, _ in outcomes.values() if o == name)
              for name in (POSTURE, CONFLICT, DEGENERATE, COMPOSED)}
    composed = [pair for pair, (o, _, _) in outcomes.items() if o == COMPOSED]
    with_share = [pair for pair in composed if outcomes[pair][1].shared]

    print("\n  composed      %d/%d   <- the number" % (counts[COMPOSED], len(pairs)))
    print("  degenerate    %d      one action ends up driving everything; not a composition"
          % counts[DEGENERATE])
    print("  posture       %d      %s is the only seated action" % (counts[POSTURE], "typing"))
    print("  conflict      %d      nothing is pinned here, so this should be 0" % counts[CONFLICT])
    print("\n  of the composed, %d have a body part driven by both clips at half each"
          % len(with_share))
    cyclic = sorted({aid for aid, segs in segment_table.items()
                     for seg in segs if seg.get("cycle_frames")})
    print("  overlays contributing one repetition rather than a whole clip: %s"
          % (", ".join(cyclic) or "none"))

    if args.engine:
        if not composed:
            print("\nnothing composed, so nothing to commit")
            return 1
        print("\n== committing %d composed pairs ==" % len(composed))
        results = asyncio.run(commit(kb, composed, args.host, args.port, args.wait, args.character))
        played = sum(1 for ok, _ in results.values() if ok)
        print("\n  committed %d/%d" % (played, len(composed)))
        return 0 if played == len(composed) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
