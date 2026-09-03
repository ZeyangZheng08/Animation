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


def _wait_for_unity(coro_timeout, where):
    """A readable failure when nothing connects, instead of a bare TimeoutError traceback.

    These scripts are the SERVER on the runtime channel, so "no engine" is the ordinary outcome of
    running one with Unity closed — and a traceback through asyncio.timeouts says nothing about what
    to do. Two things go wrong here and they need different answers: nobody entered play mode, or
    something else is already holding the port (a service `terminal.ps1` left running is the usual
    one), in which case this process never got to listen at all.
    """
    raise SystemExit(
        "no engine connected on %s within the wait.\n"
        "  * Unity has to be in PLAY mode: this script is the server and the executor dials in.\n"
        "  * Nothing else may hold the port. `ss -ltn | grep %s` finds a service left running by\n"
        "    terminal.ps1; stop it, or turn off Tools > Animation Agent > Open Terminal On Play."
        % (where, where.rsplit(":", 1)[-1]))


# The clips this smoke drives. The eight nursing actions it used to name left the knowledge base
# (agent/nursing_assets/). `SIT_DOWN` goes in `then[].via`: the library holds a real clip for the
# posture change now, so the descent is RETRIEVED rather than generated, which is the path the
# whole three-family split exists to make reachable.
WALK = "mx_Walking_Forward"
SIT_DOWN = "mx_Standing_To_Sitting_Transition"
# `mx_Aim_Pistol_While_Sitting` until posture algorithm 2.0.0, which decides a seat by where the mass
# goes rather than by joint angles: that clip draws its feet back under the seat, so its centre of
# mass sits only 0.024-0.061 m behind the heels against a 0.04 m margin and it now reads `other` for
# most of its frames. A smoke needs a clip the rule is not marginal about.
SEATED = "mx_Sitting_Still_In_A_Chair"
STAND_UP = "mx_Sitting_To_Standing_2"
# What the sit-down clip displaces the hips by, from the posture sidecar's `root_travel`. Restated
# here rather than read, so the assertion below is against a number somebody wrote down.
SIT_DOWN_TRAVEL_M = 0.446
IDLE = "mx_Standing_Idle"


def _round(value):
    return "%.4f" % value if isinstance(value, float) else str(value)


def show(label, value):
    print("   %-12s %s" % (label, json.dumps(value, default=str)))


async def main(host, port, wait):
    kb = KBIndex.load()
    async with EngineLink(host, port) as link:
        print("serving ws://%s:%d — enter play mode in Unity (waiting up to %ds)" % (host, port, wait))
        try:
            hello = await link.wait_ready(timeout=wait)
        except (asyncio.TimeoutError, TimeoutError):
            _wait_for_unity(None, "%s:%d" % (host, port))
        print("connected: scene=%s characters=%s protocol=v%s"
              % (hello.get("scene"), hello.get("characters"), hello.get("protocol")))
        character = (hello.get("characters") or ["chr:CPRNurse"])[0]
        who = {"character": character}

        registry = kb_tools.register(ToolRegistry(), kb)
        scene_tools.register(registry, link, kb)

        print('\n== unity_query(query=""), the whole room')
        out = await registry.dispatch("unity_query", {"query": ""})
        print("   %d entities" % out.get("count"))
        for hit in (out.get("results") or [])[:8]:
            print("     %-22s %-22s %s" % (hit["id"], hit["label"], ",".join(hit["aliases"])))

        print("\n== unity_query('chair')")
        out = await registry.dispatch("unity_query", {"query": "chair"})
        show("results", out.get("results"))
        seat = (out.get("results") or [{}])[0].get("id")
        if not seat:
            print("   no seat in this scene; the sit cases below are skipped")

        print("\n== unity_query(object_ids=...), the relation and nothing else")
        show("objects", (await registry.dispatch(
            "unity_query", {"object_ids": [seat] if seat else ["obj:Patient"],
                            "relative_to": who.get("character")})).get("objects"))

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
            "steps": [{"action_id": WALK,
                       "layers": [{"action_id": WALK, "channels": [], "source": "base",
                                   "owns_root": True, "hold_final_pose": False,
                                   "clip": {"guid": kb.record(WALK)["source_clip"]["guid"],
                                            "clip_name": kb.record(WALK)["source_clip"]["clip_name"]}}],
                       "start_at_s": 0.0, "blend_in_s": 0.0, "clip_start_frame": 0,
                       "duration_s": None, "loop": True, "posture": "standing",
                       "frame_rate": kb.record(WALK).get("frame_rate") or 30}],
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
            print("\n== unity_execute: walk over, sit down through a real transition clip, settle (preview -> check -> walk -> commit)")
            t0 = time.perf_counter()
            out = await registry.dispatch("unity_execute", dict(
                who, base=WALK, then=[{"via": [SIT_DOWN], "base": SEATED}], sit_on=seat))
            print("   %.2f s total" % (time.perf_counter() - t0))
            show("success", out.get("success"))
            if not out.get("success"):
                show("error", out.get("error"))
                show("hint", out.get("hint"))
            else:
                show("validated", out.get("validated"))
                show("walked", out.get("walked"))
                show("generated", out.get("generated_transitions"))

                # HOW MUCH OF THE SIT-DOWN'S OWN TRAVEL REACHED THE TRANSFORM. The clip moves the hips
                # 0.446 m; the composer consumes its root motion while it is at least half
                # established, so what lands should be close to that and not to zero. Asserted rather
                # than printed: a plan can pass every geometric check while the character stood still
                # and the seat happened to be where she already was.
                applied = (out.get("validated") or {}).get("root_motion_applied_m")
                travel = SIT_DOWN_TRAVEL_M
                if applied is not None:
                    print("   %-12s %.4f m applied of the clip's own %.4f m (%.0f%%)"
                          % ("root_motion", applied, travel, 100.0 * applied / travel))
                    assert applied > 0.8 * travel, (
                        "only %.4f m of %s's %.4f m reached the transform; the handover is eating it"
                        % (applied, SIT_DOWN, travel))

        if seat:
            print("\n== unity_measure: the RUNTIME gate on the same motion")
            out = await registry.dispatch("unity_measure", who)
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

            # ---- and back up again, through the clip that does it -------------------------------
            #
            # THE MIRROR OF THE SIT, and the reason `standing_point_for` handles both directions: a
            # stand-up travels FORWARD off the seat by about the same 0.44 m the sit-down came in by.
            # Where she ends up is therefore the standing point she sat down from, and the distance
            # between the two is the round-trip error of the whole mechanism.
            print("\n== unity_execute: stand back up through %s" % STAND_UP)
            # `sit_on` still names the chair: she is ON it, and the seated step at the head of this
            # plan has to say what it is sitting on for the same reason the sit did.
            out = await registry.dispatch("unity_execute", dict(
                who, base=SEATED, sit_on=seat, then=[{"via": [STAND_UP], "base": IDLE}]))
            show("success", out.get("success"))
            if out.get("error"):
                show("error", out.get("error"))
            show("stood_up", out.get("stood_up"))
            seq = out.get("sequence")
            if seq:
                show("sequence", [s["action_id"] for s in seq])

        print("\n== a plan that cannot work: sit on something with no seat in it")
        out = await registry.dispatch("unity_execute",
                                      dict(who, base=SEATED, sit_on="obj:Patient"))
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
