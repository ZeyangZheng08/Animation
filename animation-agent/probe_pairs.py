#!/usr/bin/env python3
"""probe_pairs.py — every ordered pair of actions, and whether the agent can get from one to the other.

"Any action, then any other action" is a claim about 56 pairs, not about the one that was demonstrated.
This walks all of them and says which work, and for each that does not, why — a coverage number with a
reason attached to every hole, rather than a demo that happened to pick a working pair.

TWO DIFFERENT QUESTIONS, AND THE CHEAP ONE WAS NEVER THE BLOCKED ONE. Read the numbers accordingly.

SCHEDULING (the default, hermetic) is pure Python over the frozen `raw` dumps: find the seam, decide
whether the join needs frames no clip contains, derive the hip travel for them. It answers 56/56, and it
answered 56/56 BEFORE any of the posture work — `schedule` derives the hip travel as an absolute value
and has always been willing to plan a pair in either direction. So this number is a guard against the
seam table regressing, not evidence that the agent can perform all 56. Do not quote it as coverage.

COMMITTING (`--engine`, needs play mode) is where the asymmetry actually was: the executor refused any
plan that started in a posture the character was not in, in one direction only, so half the crossings
were unreachable however well they scheduled. That refusal is now one symmetric rule, and this arm is
what shows it. Slower, and it moves the character 56 times.
"""
import argparse
import asyncio
import itertools
import sys

from agent import kbindex as KI
from agent import protocol as P
from agent import transitions as T
from agent.engine import DEFAULT_HOST, DEFAULT_PORT, EngineLink
from agent.kbindex import KBIndex


def posture_of(kb, action_id):
    """Standing or seated, binned from the clip's measured carriage.

    It used to read `composability.posture`, a label a VLM proposed and a human accepted; v4 deletes
    the whole block (ADR 0022). Delegated to `kbindex` rather than reimplemented, so this probe and
    the tools cannot come to disagree about which action is the seated one."""
    return KI.posture_of(kb.record(action_id))


def schedule_pair(kb, clips, a, b):
    """Try to plan a -> b. Returns (ok, note) where note says what happened either way."""
    try:
        timeline = T.schedule([a, b], kb, clips, generate_posture_changes=True, open_at_seam=True)
    except ValueError as e:
        return False, str(e)
    except (IOError, OSError, KeyError) as e:
        return False, "no per-frame data: %s" % e

    second = timeline[1]
    if second.generated:
        made = second.generated
        return True, "generated %s, hips %.3f -> %.3f m over %.2f s" % (
            made["kind"], made["start_hip_height_m"], made["target_hip_height_m"], made["duration_s"])
    return True, "%s seam, blends over %.2f s, enters on frame %d" % (
        second.seam_class, second.blend_in_s, second.clip_start_frame)


async def commit_pairs(kb, pairs, host, port, wait, who, seat):
    """The second question: will the engine play what was scheduled. Needs play mode.

    A pair that ends seated needs somewhere to sit, or it is refused for a reason that has nothing to
    do with the posture machinery -- so the seat is passed on every pair and ignored by the ones that
    do not sit. And each pair leaves her in the posture its second action is in, which is where the
    next pair departs from: that is the point, since a run that reset to standing between pairs would
    never once start from seated and would pass without testing the direction that was blocked.
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
        for index, (a, b) in enumerate(pairs):
            request = {"base": a, "then": [{"base": b}], "character": who, "mode": "commit"}
            if posture_of(kb, a) == "seated" or posture_of(kb, b) == "seated":
                request["sit_on"] = seat
            out = await registry.dispatch("plan_motion", request)
            ok = out.get("success") is not False
            results[(a, b)] = (ok, out.get("error") or "committed")
            print("  %3d/%d  %-4s %-14s -> %-14s %s"
                  % (index + 1, len(pairs), "" if ok else "FAIL", a, b,
                     "" if ok else (out.get("error") or "")), flush=True)
            # Let a generated posture change finish before the next pair departs from it.
            await asyncio.sleep(2.0 if posture_of(kb, b) != posture_of(kb, a) else 0.2)
    return results


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", action="store_true",
                    help="also commit each pair against a running executor (needs play mode)")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--wait", type=int, default=120)
    ap.add_argument("--verbose", action="store_true", help="print every pair, not only the failures")
    ap.add_argument("--character", default="Jill", help="who to drive; there are three of them now")
    ap.add_argument("--seat", default="obj:Chair", help="what the seated pairs sit on")
    args = ap.parse_args(argv)

    kb = KBIndex.load()
    clips = T.load_clips(kb)
    ids = sorted(clips)
    pairs = [(a, b) for a, b in itertools.permutations(ids, 2)]

    scheduled = {pair: schedule_pair(kb, clips, *pair) for pair in pairs}
    crossings = [pair for pair in pairs if posture_of(kb, pair[0]) != posture_of(kb, pair[1])]

    print("== %d ordered pairs over %d actions ==\n" % (len(pairs), len(ids)))
    for pair in pairs:
        ok, note = scheduled[pair]
        if ok and not args.verbose:
            continue
        print("  %-4s %-14s -> %-14s %s" % ("" if ok else "FAIL", pair[0], pair[1], note))

    good = sum(1 for ok, _ in scheduled.values() if ok)
    made = sum(1 for pair in pairs if scheduled[pair][0] and "generated" in scheduled[pair][1])
    print("\n  scheduled          %d/%d" % (good, len(pairs)))
    print("  of those, generated %d  (every pair that crosses a posture: %d)" % (made, len(crossings)))
    if good < len(pairs):
        print("\n  A failure here is a pair the agent cannot get between at all. Each one above "
              "carries its reason;\n  none of them may be left as 'not tried'.")

    if not args.engine:
        print("\n  SCHEDULING ONLY, and this arm answered 56/56 before the posture work too -- it is a "
              "guard on the\n  seam table, not a coverage figure. What was blocked was the engine "
              "refusing to START from a\n  posture it was not in. `--engine` is the arm that measures "
              "that; run it in play mode.")
        return 0 if good == len(pairs) else 1

    committed = asyncio.run(commit_pairs(kb, [p for p in pairs if scheduled[p][0]],
                                         args.host, args.port, args.wait,
                                         args.character, args.seat))
    print("\n== committed ==\n")
    for pair, (ok, note) in sorted(committed.items()):
        if ok and not args.verbose:
            continue
        print("  %-4s %-14s -> %-14s %s" % ("" if ok else "FAIL", pair[0], pair[1], note))
    played = sum(1 for ok, _ in committed.values() if ok)
    print("\n  committed          %d/%d" % (played, len(committed)))
    return 0 if good == len(pairs) and played == len(committed) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
