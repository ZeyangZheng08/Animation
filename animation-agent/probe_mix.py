#!/usr/bin/env python3
"""probe_mix.py — one body part driven by two clips at once, and what the graph actually held.

The plan names `giving_pills` and `walking` on the same legs, so both are asked for there and neither
can simply win. Winner-take-all threw one of them away; the shares are half each, and this is where
that stops being an arithmetic claim. It sends the plan down the real path and then asks the engine,
not the plan, what the mixer ended up holding.

WHO DECIDES THE CONTENTION CHANGED, AND THE ARITHMETIC WITH IT. Under v3 the pair contested the legs
by themselves -- `giving_pills` labelled them `support`, `walking` labelled them `primary`, and
normalising ROLE_PRIORITY gave 0.6 and 0.4. motionkb/v4 deletes `role` (ADR 0022), so a contested
channel is one the PLAN named twice, and the shares are equal because nothing is left to rank them
by. What this probe measures is unchanged: whether a fractional weight survives the trip to the
graph.

THE PRIMITIVE WAS CHECKED FIRST, SEPARATELY. Whether a masked layer interpolates at a fractional weight
at all is a question about Unity, not about this pipeline, so it was answered on its own before any of
this was built: `walking` under `nurse_cpr_30` masked to the right arm, sampled at weights 0, 0.5 and 1,
gave a right elbow 34.21 degrees from one end and 35.01 from the other across a 69.14 degree span — a
detour of 0.08 degrees off the geodesic, i.e. a genuine midpoint — while the left elbow, outside the
mask, moved 0.00. What is left for this probe is the part that pipeline can get wrong: does the share
survive the trip.

IT GOES THROUGH THE TOOL, NOT AROUND IT. probe_sit.py hand-builds its payload and carries a note about
what that cost — a probe whose payload has drifted from the tool's has stopped being evidence about the
tool. So this dispatches `plan_motion` itself.

Run with Unity in play mode, and with nothing else serving the runtime channel: this is the server, so
the auto-started service has to be off (Tools > Animation Agent > Open Terminal On Play).
"""
import argparse
import asyncio
import sys

from agent import assemble as A
from agent import gates as G
from agent import protocol as P
from agent.engine import DEFAULT_HOST, DEFAULT_PORT, EngineLink
from agent.kbindex import KBIndex
from agent.tools import ToolRegistry
from agent.tools import kb as kb_tools
from agent.tools import scene as scene_tools

BASE = "giving_pills"
OVERLAY = "walking"
# The body part both halves of the plan are asked for. The legs, because that is the pair this probe
# was written about: a walk's stride and a hand-over's brace, on one set of legs.
CONTESTED = ["left_leg", "right_leg"]


def expected(kb, base, overlay, channels):
    """What the arbitration says, worked out here so the engine's answer has something to be wrong
    against. Derived inside the probe from the same function the tool uses, deliberately: a probe
    with its own copy of the rule cannot catch the rule changing."""
    assembly = A.arbitrate(base, [(overlay, channels)], kb, base_channels=channels)
    return {mix.channel: dict(mix.overlay_weights(assembly.base)).get(overlay)
            for mix in assembly.shared}


async def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--overlay", default=OVERLAY)
    ap.add_argument("--channels", nargs="+", default=CONTESTED,
                    help="the body parts BOTH halves of the plan ask for. Since v4 a contested "
                         "channel is one the plan named twice; nothing in a record contests "
                         "anything by itself.")
    # Named, because there are three of them now and the tools refuse to guess. That refusal is
    # correct and this is what it costs: a probe cannot leave the character implicit any more.
    ap.add_argument("--character", default="Jill")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--wait", type=int, default=120)
    args = ap.parse_args(argv)

    kb = KBIndex.load()
    want = expected(kb, args.base, args.overlay, args.channels)
    if not want:
        print("%s under %s contests no channel, so there is no mix to measure"
              % (args.overlay, args.base))
        return 1

    async with EngineLink(args.host, args.port) as link:
        print("serving ws://%s:%d — enter play mode in Unity" % (args.host, args.port), flush=True)
        hello = await link.wait_ready(timeout=args.wait)

        registry = kb_tools.register(ToolRegistry(), kb)
        scene_tools.register(registry, link, kb)

        # The id behind the name, for the one call below that goes straight to the engine.
        names = dict((hello or {}).get("character_names") or {})
        character = next((cid for cid, name in names.items()
                          if name.lower() == args.character.lower()), args.character)

        plan = await registry.dispatch("plan_motion", {
            "base": args.base, "base_channels": list(args.channels),
            "overlays": [{"action_id": args.overlay, "channels": list(args.channels)}],
            "character": args.character, "mode": "commit"})
        if plan.get("success") is False:
            print("the plan was refused: %s" % plan.get("error"))
            return 1

        # From `derived`, which is the assembly the tool actually sent. `sequence` was the wrong place
        # to look: it only appears for a plan of more than one step, and it carries the timeline rather
        # than the layers -- so reading it made a working mix print nothing at all.
        derived = plan.get("derived")
        derived = derived[0] if isinstance(derived, list) else (derived or {})
        print("\nplanned")
        for mix in derived.get("shared") or []:
            print("  %-10s %s" % (mix["channel"],
                                  " + ".join("%.2f %s" % (s["share"], s["action_id"])
                                             for s in mix["shares"])))

        # Give the graph a moment to be built and played before reading weights off it.
        await asyncio.sleep(0.5)
        # THE GATE DIRECTLY, THROUGH THE SAME PROJECTION THE TOOL USES. `check_motion` raises on any
        # failed check, and this plan has one that has nothing to do with mixing: `giving_pills`
        # animates both hands against the pill bottle, so standing anywhere else fails
        # `contact_reached` however well the legs are composed. Reading `composed` off that raised
        # result gave an empty list, and the probe reported "the share did not survive the trip" about
        # a mix that was live in the graph. Two different problems; both are printed, neither hides the
        # other. The PLAN still goes through the tool, which is the part that has to be the real path.
        _, report = G.summarise(await link.call(P.T.GATE_RUN, {"character": character}))
        held = report.get("composed") or []
        failures = report.get("failures") or []

        print("\nheld by the graph")
        if not held:
            print("  nothing. The mixer is carrying one source per channel, so the share did not "
                  "survive the trip — that is the failure this probe exists to catch.")
            return 1
        ok = True
        seen = set()
        for entry in held:
            got = float(entry.get("share"))
            for channel in entry.get("channels") or []:
                target = want.get(channel)
                agrees = target is not None and abs(got - target) < 0.01
                ok = ok and agrees
                seen.add(channel)
                print("  %-10s %.3f held, %s asked for%s"
                      % (channel, got, "%.3f" % target if target is not None else "nothing",
                         "" if agrees else "   <-- DISAGREES"))
        missing = sorted(set(want) - seen)
        if missing:
            ok = False
            print("  never reported: %s   <-- planned as shared and the graph is not holding it"
                  % ", ".join(missing))

        for metric in report.get("observations") or []:
            if metric.get("check") == "foot_skate":
                print("\nfoot skate %.3f m/s — reported, not judged. Two clips on one pair of legs is "
                      "exactly where this would show up." % float(metric["measured"]))

        if failures:
            print("\ngeometric checks that failed, which are about WHERE SHE IS and not about the mix:")
            for failure in failures:
                print("  %-24s %s" % (failure["check"], failure["problem"]))
            print("  This plan is committed where she happens to be standing. %s animates its hands "
                  "against an object, so they miss it from anywhere else — expected here, and not "
                  "evidence about the weights above." % args.base)

        print("\n%s" % ("the share the arbitration derived is the share the mixer is holding."
                        if ok else "the graph is not holding what was asked for; read the two "
                                   "columns above."))
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
