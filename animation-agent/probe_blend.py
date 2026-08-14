#!/usr/bin/env python3
"""probe_blend.py — did the crossfade actually run, or did the scheduler snap?

A sequence that hard-cuts and a sequence that blends both pass the geometric gate and both look like
"2 steps" in the engine's reply. The difference is whether two steps ever carried weight at the same
time. The composer records that; this drives one sequence and reads it back.

    python probe_blend.py                     # a blend seam
    python probe_blend.py --order cpr bvm
"""
import argparse
import asyncio
import sys
import time

from agent import assemble as A
from agent import protocol as P
from agent import transitions as T
from agent.engine import DEFAULT_HOST, DEFAULT_PORT, EngineLink
from agent.kbindex import KBIndex


def steps_of(order, kb, clips):
    steps = []
    for step in T.schedule(order, kb, clips):
        assembly = A.arbitrate(step.action_id, [], kb)
        entry = step.as_dict()
        entry["layers"] = [
            {"action_id": aid, "channels": chans,
             "source": "base" if aid == assembly.base else "overlay",
             "owns_root": aid == assembly.root_owner, "hold_final_pose": False,
             "clip": {"guid": kb.record(aid)["source_clip"]["guid"],
                      "clip_name": kb.record(aid)["source_clip"]["clip_name"]}}
            for aid, chans in assembly.layers]
        entry["frame_rate"] = kb.record(step.action_id).get("frame_rate") or 30
        steps.append(entry)
    return steps


async def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--order", nargs="+", default=["cpr", "bvm"])
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--wait", type=int, default=90)
    args = ap.parse_args(argv)

    kb = KBIndex.load()
    clips = T.load_clips(kb)
    steps = steps_of(args.order, kb, clips)
    seam = T.find_seam(args.order[0], args.order[1], kb, clips)

    async with EngineLink(args.host, args.port) as link:
        print("serving ws://%s:%d — enter play mode in Unity" % (args.host, args.port))
        hello = await link.wait_ready(timeout=args.wait)
        character = (hello.get("characters") or ["chr:CPRNurse"])[0]

        print("\nseam %s -> %s: %.2f deg, %d blend frame(s), class %s"
              % (args.order[0], args.order[1], seam.cost_deg, seam.blend_frames, seam.cls))
        for s in steps:
            print("   %-14s at %5.2fs, fade %.3fs, from frame %d"
                  % (s["action_id"], s["start_at_s"], s["blend_in_s"], s["clip_start_frame"]))

        await link.call(P.T.MOTION_ASSEMBLE,
                        {"character": character, "steps": steps, "mode": "commit"}, timeout=20)

        handover = steps[1]["start_at_s"]
        print("\nwaiting %.1fs for the handover ..." % handover)
        await asyncio.sleep(handover + 1.5)

        report = await link.call(P.T.GATE_RUN, {"character": character}, timeout=20)
        blend = report.get("blend") or {}
        print("\nmax steps carrying weight at once : %s" % blend.get("max_concurrent_steps"))
        print("peak weight on the quieter step   : %s" % blend.get("peak_overlap"))
        verdict = "CROSSFADED" if (blend.get("max_concurrent_steps") or 0) >= 2 else "HARD CUT"
        print("verdict: %s" % verdict)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
