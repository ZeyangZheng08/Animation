#!/usr/bin/env python3
"""probe_sit.py — drive the one motion the library does not contain, and measure what came out.

walking -> typing crosses standing to seated. Nothing in the corpus covers it, no crossfade can serve,
and the frames have to be made. This sends that plan and then asks the gate what actually happened:
where the hips ended up, how well the closed loop tracked the descent, whether the feet stayed put and
whether anything went through the floor.

Run with Unity in play mode.
"""
import argparse
import asyncio
import sys

from agent import assemble as A
from agent import gates as G
from agent import protocol as P
from agent import transitions as T
from agent.engine import DEFAULT_HOST, DEFAULT_PORT, EngineLink
from agent.kbindex import KBIndex


def steps_of(order, kb, clips, seat_id=None, seat_surface=None):
    steps = []
    for step in T.schedule(order, kb, clips, generate_posture_changes=True):
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
        # Same field the real path sends. A probe whose payload has drifted from the tool's stops being
        # evidence about the tool: this one passed while leaving the executor's posture on "standing",
        # because it was the one caller not sending it.
        entry["posture"] = ((kb.record(step.action_id).get("composability") or {}).get("posture")
                            or "standing")
        if entry.get("generated") and seat_id:
            entry["generated"]["support_object_id"] = seat_id
            entry["generated"]["support_surface_m"] = seat_surface
        steps.append(entry)
    return steps


async def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--order", nargs="+", default=["walking", "typing"])
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--wait", type=int, default=90)
    ap.add_argument("--walk-there", action="store_true",
                    help="move the character to the seat before the descent, which is the fix the "
                         "gate named when the first generated sit landed in mid-air")
    ap.add_argument("--face", default=None, help="object_id to turn towards after arriving")
    args = ap.parse_args(argv)

    kb = KBIndex.load()
    clips = T.load_clips(kb)

    async with EngineLink(args.host, args.port) as link:
        print("serving ws://%s:%d — enter play mode in Unity" % (args.host, args.port))
        hello = await link.wait_ready(timeout=args.wait)
        character = (hello.get("characters") or ["chr:CPRNurse"])[0]

        seats = await link.call(P.T.SCENE_FIND, {"category": "seating"})
        if not seats["objects"]:
            print("no seating in the registry; the plan would be refused")
            return 1
        seat_id = seats["objects"][0]["id"]
        seat = (await link.call(P.T.SCENE_POSITION, {"object_ids": [seat_id],
                                                     "relative_to": character}))["objects"][0]
        rel = seat.get("from_character") or {}
        print("\nseat %s at height %.4f m, %.2f m %s of the character"
              % (seat_id, seat["surface_height_m"], rel.get("distance_m", -1), rel.get("bearing")))
        if args.walk_there:
            start = await link.call(P.T.MOTION_LOCOMOTE,
                                    {"character": character, "to": seat_id, "stop_within_m": 0.08})
            print("   walking %.2f m, eta %.2f s ..." % (start.get("path_length_m", -1),
                                                        start.get("eta_s", -1)))
            for _ in range(80):
                await asyncio.sleep(0.25)
                state = await link.call(P.T.MOTION_LOCOMOTE, {"character": character, "query": True})
                if state.get("arrived"):
                    break
            if args.face:
                await link.call(P.T.MOTION_LOCOMOTE,
                                {"character": character, "face_only": args.face})
                print("   facing %s" % args.face)
            again = (await link.call(P.T.SCENE_POSITION, {"object_ids": [seat_id],
                                                          "relative_to": character}))["objects"][0]
            print("   arrived: now %.2f m from the seat"
                  % (again.get("from_character") or {}).get("distance_m", -1))

        steps = steps_of(args.order, kb, clips, seat_id, seat.get("surface_height_m"))

        for s in steps:
            line = "   %-9s at %5.2fs, fade %.3fs, from frame %d" % (
                s["action_id"], s["start_at_s"], s["blend_in_s"], s["clip_start_frame"])
            print(line)
            g = s.get("generated")
            if g:
                print("      GENERATED %s: hips %.4f -> %.4f m (%.4f m of travel) over %.3f s"
                      % (g["kind"], g["start_hip_height_m"], g["target_hip_height_m"],
                         g["hip_travel_m"], g["duration_s"]))

        # The contacts the clips make by themselves, measured but never bound -- same payload the tool
        # sends. `typing` animates both hands against a keyboard; whether they meet the real one is
        # decided by where she ends up sitting, and nothing used to ask.
        expect = []
        for step in steps:
            start = (step.get("start_at_s") or 0.0)
            if step.get("generated"):
                start += step["generated"].get("duration_s") or 0.0
            for channel, spec in (kb.record(step["action_id"]).get("channels") or {}).items():
                contact = (spec or {}).get("contact") or ""
                if channel in ("left_hand", "right_hand") and contact.startswith("object:"):
                    hits = (await link.call(P.T.SCENE_FIND,
                                            {"alias": contact[len("object:"):]}))["objects"]
                    if len(hits) == 1:
                        expect.append({"effector": channel, "object_id": hits[0]["id"],
                                       "due_at_s": start})
        for e in expect:
            print("   contact %-11s -> %-14s due at %5.2fs"
                  % (e["effector"], e["object_id"], e["due_at_s"]))

        # Bind each declared hand to the object's OWN per-hand anchor, where it has them. Same rule the
        # tool applies: two hands may be aimed at something only when it says where both of them go.
        #
        # AND AT THE SAME MOMENT THE CONTACT FALLS DUE. A binding belongs to the step that reaches for
        # something, not to the plan -- applied from frame zero it drags her arms toward the desk for
        # the whole walk. `due_at_s` above is that moment and there is not a second one: a probe that
        # sent a different payload from the tool is how a verified path and the real path came to
        # disagree once already.
        ik = []
        for e in expect:
            found = (await link.call(P.T.SCENE_FIND, {"name_contains": e["object_id"][4:]}))["objects"]
            if found and found[0].get("two_handed_anchors"):
                ik.append({"effector": e["effector"], "object_id": e["object_id"],
                           "at_s": e["due_at_s"]})
        for b in ik:
            print("   bind    %-11s -> %-14s from %5.2fs (per-hand anchors)"
                  % (b["effector"], b["object_id"], b["at_s"]))

        await link.call(P.T.MOTION_ASSEMBLE,
                        {"character": character, "steps": steps, "expect_contact": expect,
                         "ik": ik, "mode": "commit"}, timeout=20)

        settle = steps[1]["start_at_s"] + steps[1]["blend_in_s"] + 1.0
        print("\nwaiting %.1fs for the descent to finish ..." % settle)
        await asyncio.sleep(settle)

        report = await link.call(P.T.GATE_RUN, {"character": character}, timeout=20)
        gen = report.get("generated") or {}
        blend = report.get("blend") or {}
        target = steps[1]["generated"]["target_hip_height_m"]
        hips = gen.get("hip_height_m")

        print("\n== what actually happened")
        print("   hips ended at        %.4f m   (asked for %.4f, off by %.4f)"
              % (hips, target, abs(hips - target)))
        print("   above the seat       %.4f m" % (hips - seat["surface_height_m"]))
        print("   worst tracking error %.4f m   (peak gap during the descent)"
              % gen.get("worst_tracking_error_m", -1))
        print("   crossfade            %s step(s) at once, peak overlap %.3f"
              % (blend.get("max_concurrent_steps"), blend.get("peak_overlap", 0.0)))

        ok, payload = G.summarise(report)
        print("\n   gate %s over %s frames" % ("PASS" if ok else "FAIL",
                                               payload.get("frames_measured")))
        for f in payload.get("failures", []):
            print("      FAIL %s: measured %.3f m, allowed %.3f m, worst frame %s"
                  % (f["problem"], f["measured_m"], f["allowed_m"], f["worst_frame"]))
        for o in payload.get("observations", []):
            print("      %-24s %.4f   (%s)" % (o["check"], o["measured"], o["note"]))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
