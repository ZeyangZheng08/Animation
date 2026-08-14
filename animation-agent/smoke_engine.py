#!/usr/bin/env python3
"""
smoke_engine.py — drive the real Unity executor over the runtime channel, without a model.

Deliberately no LLM. This isolates the engine half: if a plan fails here, the cause is in the executor
or the protocol, not in what a model chose. The agent arm has its own eval.

It serves the channel, waits for Unity to connect, and then walks the demo's three cases:
  1. full match          -- one layer, whole body
  2. layered composition -- walking legs + grab_bottle right arm and hand (dc-walk-carry)
  3. scene grounding     -- the same, with the right hand bound to a real object and a gaze target

Usage:  python smoke_engine.py            (start this, then enter play mode in Unity)
"""
import argparse
import asyncio
import time

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


async def main(host, port, wait):
    kb = KBIndex.load()
    async with EngineLink(host, port) as link:
        print("serving ws://%s:%d — enter play mode in Unity (waiting up to %ds)" % (host, port, wait))
        hello = await link.wait_ready(timeout=wait)
        print("connected: scene=%s characters=%s registry=%s objects protocol=v%s"
              % (hello.get("scene"), hello.get("characters"), hello.get("objects"),
                 hello.get("protocol")))

        character = (hello.get("characters") or ["chr:CPRNurse"])[0]

        anchors = await link.call(P.T.SCENE_ANCHORS, {})
        print("\n== scene anchors (%d)" % len(anchors["anchors"]))
        print("   " + ", ".join(a["name"] for a in anchors["anchors"][:8]))

        bottle = await link.call(P.T.SCENE_FIND, {"alias": "aspirin_bottle"})
        print("\n== scene_find(alias='aspirin_bottle')")
        for o in bottle["objects"]:
            print("   %s  %s  near=%s" % (o["id"], o["name"], o["near"]))
        bottle_id = bottle["objects"][0]["id"] if bottle["objects"] else None

        # Seating, and where it is. A generated sit has to land on something real, so the chair being
        # findable AND measurable is a precondition for the whole posture-change path.
        chairs = await link.call(P.T.SCENE_FIND, {"category": "seating"})
        print("\n== scene_find(category='seating')")
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
        print("\n== scene_find fallback for an unannotated object")
        print("   %d hit(s); first: %s" % (len(raw["objects"]),
                                           raw["objects"][0] if raw["objects"] else "none"))

        monitor = await link.call(P.T.SCENE_FIND, {"name_contains": "Monitor"})
        monitor_id = monitor["objects"][0]["id"] if monitor["objects"] else None
        print("   monitor -> %s" % monitor_id)

        # 1. full match
        full = A.arbitrate("cpr", [], kb)
        await show(link, "full match: cpr", {
            "character": character, "layers": layers_of(full, kb), "mode": "commit"})
        await asyncio.sleep(2.5)

        # 2. layered composition — the dc-walk-carry partition
        carry = A.arbitrate("walking", ["grab_bottle"], kb)
        await show(link, "composed: walking + grab_bottle (dc-walk-carry)", {
            "character": character, "layers": layers_of(carry, kb, hold=("grab_bottle",)),
            "free_channels": carry.free_channels, "mode": "commit"})
        await asyncio.sleep(2.5)

        # 3. the same, grounded on real objects
        if bottle_id:
            await show(link, "grounded: carry the real bottle, gaze at the monitor", {
                "character": character, "layers": layers_of(carry, kb, hold=("grab_bottle",)),
                "free_channels": carry.free_channels,
                "ik": [{"effector": "right_hand", "object_id": bottle_id}],
                "carry": [{"object_id": bottle_id, "hand": "right_hand"}],
                "gaze_at": monitor_id, "mode": "commit"})
            await asyncio.sleep(3.0)

        # 4. dc-givepills-gaze
        gaze = A.arbitrate("giving_pills", [], kb)
        await show(link, "grounded: giving_pills with head gaze (dc-givepills-gaze)", {
            "character": character, "layers": layers_of(gaze, kb),
            "free_channels": gaze.free_channels,
            "gaze_at": monitor_id, "mode": "commit"})
        await asyncio.sleep(3.0)
        await gate(link, character, "after a plan with no hand binding")

        # 5. SEQUENCES — the v2 time axis. Three pairs chosen from the seam table so the three classes
        #    are all exercised, and so the numbers on screen can be checked against build_transitions.
        from agent import transitions as T
        clips = T.load_clips(kb)
        for order, why in [(["check_pulse", "giving_pills"], "direct seam, 2.05 deg, 1-frame blend"),
                           (["cpr", "bvm"], "blend seam, 32.9 deg — the pair idle hurts most"),
                           (["walking", "giving_pills"], "a loop handing over to a one-shot")]:
            steps = steps_of(order, kb, clips)
            await show_sequence(link, "sequence: %s  (%s)" % (" -> ".join(order), why),
                                character, steps)
            total = sum(s["duration_s"] or 3.0 for s in steps)
            await asyncio.sleep(min(total + 0.5, 8.0))
        await gate(link, character, "after a sequence")

        # 6. the one sequence that must be refused rather than blended
        try:
            steps_of(["walking", "typing"], kb, clips)
            print("\n!! walking -> typing was scheduled; it should have been refused")
        except ValueError as e:
            print("\n== refused, correctly: %s" % e)

        # 7. a binding the geometry cannot honour: reach for something across the room.
        #    The point is to see the gate REJECT, not to see it agree with everything.
        if monitor_id:
            grab = A.arbitrate("idle", ["grab_bottle"], kb)
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
    asyncio.run(main(ap.parse_args().host, ap.parse_args().port, ap.parse_args().wait))
