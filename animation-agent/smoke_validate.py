#!/usr/bin/env python3
"""
smoke_validate.py — the v4 fence, exercised on the real path.

Serves the runtime channel, waits for Unity to enter play mode, and then drives the ACTUAL agent
tools rather than hand-built payloads: the collapsed scene queries, a route preview that moves
nothing, a plan checked on the hidden duplicate, and a commit that only happens on a pass.

Deliberately no model. If something fails here the cause is in the executor or the protocol, not in
what a model chose.

Usage:  python smoke_validate.py        (start this, then enter play mode in Unity)
"""
import argparse
import asyncio
import json
import time

from agent import protocol as P
from agent.engine import DEFAULT_HOST, DEFAULT_PORT, EngineLink
from agent.kbindex import KBIndex
from agent.tools import ToolRegistry
from agent.tools import kb as kb_tools
from agent.tools import scene as scene_tools


def _round(value):
    return "%.4f" % value if isinstance(value, float) else str(value)


def show(label, value):
    print("   %-12s %s" % (label, json.dumps(value, default=str)))


async def main(host, port, wait):
    kb = KBIndex.load()
    async with EngineLink(host, port) as link:
        print("serving ws://%s:%d — enter play mode in Unity (waiting up to %ds)" % (host, port, wait))
        hello = await link.wait_ready(timeout=wait)
        print("connected: scene=%s characters=%s protocol=v%s"
              % (hello.get("scene"), hello.get("characters"), hello.get("protocol")))
        character = (hello.get("characters") or ["chr:CPRNurse"])[0]
        who = {"character": character}

        registry = kb_tools.register(ToolRegistry(), kb)
        scene_tools.register(registry, link, kb)

        print("\n== scene_search(), the whole room")
        out = await registry.dispatch("scene_search", {})
        print("   %d entities" % out.get("count"))
        for hit in (out.get("results") or [])[:8]:
            print("     %-22s %-22s %s" % (hit["id"], hit["label"], ",".join(hit["aliases"])))

        print("\n== scene_search('chair')")
        out = await registry.dispatch("scene_search", {"query": "chair"})
        show("results", out.get("results"))
        seat = (out.get("results") or [{}])[0].get("id")
        if not seat:
            print("   no seat in this scene; the sit cases below are skipped")

        print("\n== scene_query, the relation and nothing else")
        show("objects", (await registry.dispatch(
            "scene_query", dict(who, object_ids=[seat] if seat else ["obj:Patient"]))).get("objects"))

        if seat:
            print("\n== route preview: where the walk WOULD end (nothing moves)")
            t0 = time.perf_counter()
            preview = await link.call(P.T.MOTION_LOCOMOTE,
                                      {"character": character, "preview": True,
                                       "to": seat, "stop_within_m": 0.08})
            print("   %.2f ms" % ((time.perf_counter() - t0) * 1000))
            show("preview", preview)

        print("\n== motion.assemble mode=validate, in place, on the hidden duplicate")
        assembly_probe = {
            "character": character,
            "steps": [{"action_id": "walking",
                       "layers": [{"action_id": "walking", "channels": [], "source": "base",
                                   "owns_root": True, "hold_final_pose": False,
                                   "clip": {"guid": kb.record("walking")["source_clip"]["guid"],
                                            "clip_name": kb.record("walking")["source_clip"]["clip_name"]}}],
                       "start_at_s": 0.0, "blend_in_s": 0.0, "clip_start_frame": 0,
                       "duration_s": None, "loop": True, "posture": "standing",
                       "frame_rate": kb.record("walking").get("frame_rate") or 30}],
            "free_channels": [], "ik": [], "gaze_at": None, "stand_at": None, "carry": [],
            "mode": "validate"}
        t0 = time.perf_counter()
        verdict = await link.call(P.T.MOTION_ASSEMBLE, assembly_probe, timeout=30)
        print("   round trip %.1f ms" % ((time.perf_counter() - t0) * 1000))
        for key in ("status", "samples", "seconds_simulated", "wall_ms", "checked", "unmeasured",
                    "failures", "ground_y", "first_pose", "last_pose", "rig_spliced", "hips_range_m"):
            show(key, verdict.get(key))
        print("   --- every metric, measured vs tolerance ---")
        for m in verdict.get("metrics") or []:
            print("     %-28s %-10s tol=%-8s %s"
                  % (m["id"], _round(m["measured"]), _round(m["tolerance"]),
                     m["status"]))

        if seat:
            print("\n== plan_motion: walk over and sit down to type (preview → check → walk → commit)")
            t0 = time.perf_counter()
            out = await registry.dispatch("plan_motion", dict(
                who, base="walking", then=[{"base": "typing"}], sit_on=seat))
            print("   %.2f s total" % (time.perf_counter() - t0))
            show("success", out.get("success"))
            if not out.get("success"):
                show("error", out.get("error"))
                show("hint", out.get("hint"))
            else:
                show("validated", out.get("validated"))
                show("walked", out.get("walked"))
                show("generated", out.get("generated_transitions"))

        if seat:
            print("\n== check_motion: the RUNTIME gate on the same motion")
            out = await registry.dispatch("check_motion", who)
            show("status", out.get("status") or out.get("gates"))
            show("frames", out.get("frames_measured"))
            for o in out.get("observations") or []:
                print("     %-28s %s   (%s)"
                      % (o["check"], _round(o["measured"]), o["note"]))
            for f in out.get("failures") or []:
                print("     %-28s %s vs %s   FAIL"
                      % (f["check"], f.get("measured_m"), f.get("allowed_m")))
            if not out.get("success"):
                show("error", out.get("error"))

        print("\n== a plan that cannot work: sit on something with no seat in it")
        out = await registry.dispatch("plan_motion",
                                      dict(who, base="typing", sit_on="obj:Patient"))
        show("success", out.get("success"))
        show("error", out.get("error"))

        print("\ndone")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--wait", type=int, default=120)
    args = parser.parse_args()
    asyncio.run(main(args.host, args.port, args.wait))
