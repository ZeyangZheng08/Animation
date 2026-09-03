#!/usr/bin/env python3
"""
smoke_engine.py — drive the real Unity executor over the runtime channel, without a model.

Deliberately no LLM. This isolates the engine half: if a plan fails here, the cause is in the executor
or the protocol, not in what a model chose. The agent arm has its own eval.

It serves the channel, waits for Unity to connect, and then walks the demo's three cases:
  1. full match          -- one layer, whole body
  2. layered composition -- a walk's legs under a one-handed reach's arm and hand
  3. scene grounding     -- the same, with the right hand bound to a real object and a gaze target

Usage:  python smoke_engine.py            (start this, then enter play mode in Unity)
"""
import argparse
import asyncio
import time


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
# (agent/nursing_assets/); every id here is a real Mixamo record.
WALK = "mx_Walking_Forward"
IDLE = "mx_Standing_Idle"
WORK = "mx_Taking_An_Item_And_Examining_It"
GRAB = "mx_Picking_Up_An_Object_With_One_Hand"
SIT_DOWN = "mx_Standing_To_Sitting_Transition"
SEATED = "mx_Aim_Pistol_While_Sitting"
LEGS = ["left_leg", "right_leg"]

from agent import protocol as P
from agent.engine import DEFAULT_HOST, DEFAULT_PORT, EngineLink
from agent import assemble as A
from agent.kbindex import KBIndex


def layers_of(assembly, kb, hold=()):
    return [{"action_id": aid, "channels": chans,
             "source": "base" if aid == assembly.base else "overlay",
             "owns_root": aid == assembly.root_owner,
             "hold_final_pose": aid in hold,
             "clip": {"guid": kb.record(aid)["source_clip"]["guid"],
                      "clip_name": kb.record(aid)["source_clip"]["clip_name"]}}
            for aid, chans in assembly.layers]


def steps_of(order, kb, clips):
    """A v2 timeline for a sequence of single-action steps, timed by the seam solver."""
    from agent import transitions as T
    timeline = T.schedule(order, kb, clips)
    steps = []
    for step in timeline:
        assembly = A.arbitrate(step.action_id, [], kb)
        entry = step.as_dict()
        entry["layers"] = layers_of(assembly, kb)
        entry["frame_rate"] = kb.record(step.action_id).get("frame_rate") or 30
        steps.append(entry)
    return steps


async def show_sequence(link, title, character, steps):
    print("\n== %s" % title)
    for s in steps:
        print("   %-14s enters at %5.2fs over %4.2fs, from frame %3d, for %s"
              % (s["action_id"], s["start_at_s"], s["blend_in_s"], s["clip_start_frame"],
                 "the rest" if s["duration_s"] is None else "%.2fs" % s["duration_s"]))
    t0 = time.perf_counter()
    data = await link.call(P.T.MOTION_ASSEMBLE,
                           {"character": character, "steps": steps, "mode": "commit"}, timeout=20)
    print("   engine built %s step(s), prepare %.1f ms, round trip %.1f ms"
          % (data.get("steps"), data.get("prepare_ms", 0.0), (time.perf_counter() - t0) * 1000))
    return data


async def show(link, title, payload):
    print("\n== %s" % title)
    t0 = time.perf_counter()
    data = await link.call(P.T.MOTION_ASSEMBLE, payload, timeout=20)
    elapsed = (time.perf_counter() - t0) * 1000
    for entry in data.get("resolved", []):
        print("   layer %-13s clip=%-18s channels=%s%s"
              % (entry["action_id"], entry["clip"], ",".join(entry["channels"]),
                 "" if entry["ok"] else "   <- NO CLIP"))
    for b in data.get("bindings", []):
        print("   %-5s %-11s -> %-22s %s"
              % (b["kind"], b["effector"], b.get("resolved_to") or b["object_id"],
                 "ok" if b["ok"] else "FAILED"))
    if "prepare_ms" in data:
        print("   prepare %.1f ms, round trip %.1f ms, frame %s"
              % (data["prepare_ms"], elapsed, data.get("frame")))
    return data


async def gate(link, character, title):
    """What the model would be told, not what the engine measured."""
    from agent import gates as G
    report = await link.call(P.T.GATE_RUN, {"character": character}, timeout=20)
    ok, payload = G.summarise(report)
    print("   gate %s: %s over %s frames" % (title, "PASS" if ok else "FAIL",
                                             payload.get("frames_measured")))
    for f in payload.get("failures", []):
        print("      FAIL %s" % f["problem"])
        print("           measured %.3f m, allowed %.3f m, worst frame %s"
              % (f["measured_m"], f["allowed_m"], f["worst_frame"]))
        print("           try: %s" % f["try"])
    for o in payload.get("observations", []):
        print("      %-24s %.4f   (%s)" % (o["check"], o["measured"], o["note"]))


async def main(host, port, wait, hold_walk=0.0):
    kb = KBIndex.load()
    async with EngineLink(host, port) as link:
        print("serving ws://%s:%d — enter play mode in Unity (waiting up to %ds)" % (host, port, wait))
        try:
            hello = await link.wait_ready(timeout=wait)
        except (asyncio.TimeoutError, TimeoutError):
            _wait_for_unity(None, "%s:%d" % (host, port))
        print("connected: scene=%s characters=%s registry=%s objects protocol=v%s"
              % (hello.get("scene"), hello.get("characters"), hello.get("objects"),
                 hello.get("protocol")))

        character = (hello.get("characters") or ["chr:CPRNurse"])[0]

        anchors = await link.call(P.T.SCENE_ANCHORS, {})
        print("\n== scene anchors (%d)" % len(anchors["anchors"]))
        print("   " + ", ".join(a["name"] for a in anchors["anchors"][:8]))

        bottle = await link.call(P.T.SCENE_FIND, {"alias": "aspirin_bottle"})
        print("\n== scene.find(alias='aspirin_bottle')   [the wire, not the tool]")
        for o in bottle["objects"]:
            print("   %s  %s  near=%s" % (o["id"], o["name"], o["near"]))
        bottle_id = bottle["objects"][0]["id"] if bottle["objects"] else None

        # Seating, and where it is. A generated sit has to land on something real, so the chair being
        # findable AND measurable is a precondition for the whole posture-change path.
        chairs = await link.call(P.T.SCENE_FIND, {"category": "seating"})
        print("\n== scene.find(category='seating')        [the wire, not the tool]")
        for o in chairs["objects"]:
            print("   %s  %s  surface=%s" % (o["id"], o["name"], o.get("has_usable_surface")))
        if chairs["objects"]:
            where = await link.call(P.T.SCENE_POSITION,
                                    {"object_ids": [chairs["objects"][0]["id"]],
                                     "relative_to": character})
            item = where["objects"][0]
            rel = item.get("from_character") or {}
            print("   seat height %.4f m   |   %.2f m %s, from %s; needs walking: %s"
                  % (item.get("surface_height_m", -1), rel.get("distance_m", -1),
                     rel.get("bearing"), rel.get("character"), rel.get("needs_walking")))

        # The scene-wide fallback: something real but never annotated.
        raw = await link.call(P.T.SCENE_FIND, {"name_contains": "Defibrilator"})
        print("\n== scene.find fallback for an unannotated object")
        print("   %d hit(s); first: %s" % (len(raw["objects"]),
                                           raw["objects"][0] if raw["objects"] else "none"))

        monitor = await link.call(P.T.SCENE_FIND, {"name_contains": "Monitor"})
        monitor_id = monitor["objects"][0]["id"] if monitor["objects"] else None
        print("   monitor -> %s" % monitor_id)

        # 0. THE DRIFT WINDOW, when asked for. Commits the bare walk cycle and holds it, so a
        #    Unity-side sampler has time to measure the hips against the character's own transform
        #    while it plays.
        #
        #    WHAT IS BEING MEASURED AND WHY IT MATTERS. Every corpus clip was imported with
        #    `lockRootPositionXZ = true`, which does not remove a clip's travel -- it welds it into
        #    the pose. `mx_Walking_Forward` covers 1.3 m over its cycle, so the hips walked away from
        #    the transform while the NavMeshAgent, which owns where the character actually is, knew
        #    nothing about it. That is the drift seen in play mode. Unbaked, the travel arrives as
        #    root motion and the composer discards it for a locomotion step, so the cycle plays in
        #    place and the hips stay over the transform.
        if hold_walk > 0:
            walk = A.arbitrate(WALK, [], kb)
            # LOOPING, so there is motion to sample for the whole window. A corpus record declares
            # `loop: false` (nothing has gone through 2446 of them), and a walk that plays once and
            # holds its last frame gives one sample of a cycle rather than a cycle.
            await show(link, "drift window: %s looped for %.1f s" % (WALK, hold_walk), {
                "character": character,
                "steps": [{"action_id": WALK, "layers": layers_of(walk, kb),
                           "start_at_s": 0.0, "blend_in_s": 0.0, "clip_start_frame": 0,
                           "duration_s": None, "loop": True, "posture": "standing",
                           "frame_rate": kb.record(WALK).get("frame_rate") or 30}],
                "free_channels": walk.free_channels,
                "ik": [], "gaze_at": None, "stand_at": None, "carry": [], "mode": "commit"})
            print("   sample the hips against the transform now (Unity side), for %.1f s" % hold_walk,
                  flush=True)
            await asyncio.sleep(hold_walk)
            await gate(link, character, "after the walk cycle held in place")
            return 0

        # 1. full match
        full = A.arbitrate(WORK, [], kb)
        await show(link, "full match: %s" % WORK, {
            "character": character, "layers": layers_of(full, kb), "mode": "commit"})
        await asyncio.sleep(2.5)

        # 2. layered composition — the walk-and-carry partition. The channel lists are the PLAN's
        #    since v4 (ADR 0022): a bare action_id is refused rather than defaulted, because an empty
        #    mask plays full body at full weight in the engine.
        carry = A.arbitrate(WALK, [(GRAB, ["right_arm", "right_hand"])], kb, base_channels=LEGS)
        await show(link, "composed: %s + %s" % (WALK, GRAB), {
            "character": character, "layers": layers_of(carry, kb, hold=(GRAB,)),
            "free_channels": carry.free_channels, "mode": "commit"})
        await asyncio.sleep(2.5)

        # 3. the same, grounded on real objects
        if bottle_id:
            await show(link, "grounded: carry the real bottle, gaze at the monitor", {
                "character": character, "layers": layers_of(carry, kb, hold=(GRAB,)),
                "free_channels": carry.free_channels,
                "ik": [{"effector": "right_hand", "object_id": bottle_id}],
                "carry": [{"object_id": bottle_id, "hand": "right_hand"}],
                "gaze_at": monitor_id, "mode": "commit"})
            await asyncio.sleep(3.0)

        # 4. a grounded one-clip plan with a head gaze
        gaze = A.arbitrate(WORK, [], kb)
        await show(link, "grounded: %s with head gaze" % WORK, {
            "character": character, "layers": layers_of(gaze, kb),
            "free_channels": gaze.free_channels,
            "gaze_at": monitor_id, "mode": "commit"})
        await asyncio.sleep(3.0)
        await gate(link, character, "after a plan with no hand binding")

        # 5. SEQUENCES — the v2 time axis. Three pairs chosen from the seam table so the three classes
        #    are all exercised, and so the numbers on screen can be checked against build_transitions.
        from agent import transitions as T
        # The named clips only. `load_clips` over the whole library is refused: 2446 parsed dumps do
        # not fit in a process.
        clips = {a: T.load_clip((kb.record(a)["source_clip"])["clip_name"],
                                loop=bool(kb.record(a).get("loop")))
                 for a in (WALK, IDLE, WORK, GRAB, SIT_DOWN, SEATED)}
        for order, why in [([GRAB, IDLE], "a short blend between two standing clips"),
                           ([WALK, WORK], "a walk handing over to a one-shot"),
                           ([WALK, SIT_DOWN, SEATED],
                            "a RETRIEVED posture change: the library holds the sit-down clip, so "
                            "nothing between these three is generated")]:
            steps = steps_of(order, kb, clips)
            await show_sequence(link, "sequence: %s  (%s)" % (" -> ".join(order), why),
                                character, steps)
            total = sum(s["duration_s"] or 3.0 for s in steps)
            await asyncio.sleep(min(total + 0.5, 8.0))
        await gate(link, character, "after a sequence")

        # 6. the one sequence that must be refused rather than blended: standing straight into
        #    seated, with nothing in between and no seat named.
        try:
            steps_of([WALK, SEATED], kb, clips)
            print("\n!! %s -> %s was scheduled; it should have been refused" % (WALK, SEATED))
        except ValueError as e:
            print("\n== refused, correctly: %s" % e)

        # 7. a binding the geometry cannot honour: reach for something across the room.
        #    The point is to see the gate REJECT, not to see it agree with everything.
        if monitor_id:
            grab = A.arbitrate(IDLE, [(GRAB, ["right_arm", "right_hand"])], kb)
            await show(link, "deliberately impossible: right hand bound to the far monitor", {
                "character": character, "layers": layers_of(grab, kb),
                "ik": [{"effector": "right_hand", "object_id": monitor_id}],
                "mode": "commit"})
            await asyncio.sleep(2.5)
            await gate(link, character, "after an out-of-reach binding")
        print("\ndone.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--wait", type=int, default=120)
    ap.add_argument("--hold-walk", type=float, default=0.0, metavar="SECONDS",
                    help="commit the bare walk cycle and hold it for this long, instead of running "
                         "the whole smoke. The window a hips-vs-transform drift measurement needs.")
    _args = ap.parse_args()
    asyncio.run(main(_args.host, _args.port, _args.wait, _args.hold_walk))
