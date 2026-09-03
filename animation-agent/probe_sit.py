#!/usr/bin/env python3
"""probe_sit.py — drive the one motion the library does not contain, and measure what came out.

A walk into a seated clip crosses standing to seated. No crossfade can serve,
and the frames have to be made. This sends that plan and then asks the gate what actually happened:
where the hips ended up, how well the closed loop tracked the descent, whether the feet stayed put and
whether anything went through the floor.

Run with Unity in play mode.
"""
import argparse
import asyncio
import sys


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

# Runtime primitives and the corpus clips this probe drives. The eight nursing actions it used to
# name left the knowledge base (agent/nursing_assets/); `tests/corpus.py` holds the same set for
# the test suite.
WALK = "mx_Walking_Forward"
SIT_DOWN = "mx_Standing_To_Sitting_Transition"
SEATED = "mx_Aim_Pistol_While_Sitting"

from agent import assemble as A
from agent import gates as G
from agent import kbindex as KI
from agent import protocol as P
from agent import transitions as T
from agent.engine import DEFAULT_HOST, DEFAULT_PORT, EngineLink
from agent.kbindex import KBIndex

OTHER_HAND = {"left_hand": "right_hand", "right_hand": "left_hand"}


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
        entry["posture"] = KI.posture_of(kb.record(step.action_id))
        if entry.get("generated") and seat_id:
            entry["generated"]["support_object_id"] = seat_id
            entry["generated"]["support_surface_m"] = seat_surface
        steps.append(entry)
    return steps


async def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--order", nargs="+", default=[WALK, SIT_DOWN, SEATED],
                    help="the sequence to schedule. The default walks in, plays the real sit-down "
                         "clip the library holds, and settles into a seated action -- so the descent "
                         "between the walk and the sit is RETRIEVED, and only the last seam is "
                         "generated. Pass two ids to see the generated path instead.")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--wait", type=int, default=90)
    ap.add_argument("--walk-there", action="store_true",
                    help="move the character to the seat before the descent, which is the fix the "
                         "gate named when the first generated sit landed in mid-air")
    ap.add_argument("--face", default=None, help="object_id to turn towards after arriving")
    ap.add_argument("--bind", nargs="*", default=["left_hand=Laptop"], metavar="HAND=OBJECT",
                    help="what a hand is asked to reach, e.g. left_hand=Laptop. The record no "
                         "longer says (ADR 0022), so the probe names it; where the object carries "
                         "per-hand anchors the other hand is bound too. Pass --bind with nothing "
                         "after it to measure no contact at all.")
    args = ap.parse_args(argv)

    kb = KBIndex.load()
    unknown = [a for a in args.order if a not in kb.actions]
    if unknown:
        print("not in the library: %s" % ", ".join(unknown))
        return 1
    # The named clips only. `load_clips` over the whole library is refused: 2446 parsed dumps do not
    # fit in a process.
    clips = {a: T.load_clip((kb.record(a)["source_clip"])["clip_name"],
                            loop=bool(kb.record(a).get("loop")))
             for a in args.order}

    async with EngineLink(args.host, args.port) as link:
        print("serving ws://%s:%d — enter play mode in Unity" % (args.host, args.port))
        try:
            hello = await link.wait_ready(timeout=args.wait)
        except (asyncio.TimeoutError, TimeoutError):
            _wait_for_unity(None, "%s:%d" % (args.host, args.port))
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

        # WHAT SHE IS ASKED TO TOUCH, OUT OF THIS PROBE'S OWN --bind LIST.
        #
        # It used to be read off the knowledge base: a seated record declared both hands `contact:
        # object:keyboard`, and the alias joined that to the laptop. A v4 record says how a hand
        # moves, not what it is on (ADR 0022), because what a hand holds is a fact about the scene
        # and the task. In a real turn the PLAN names it; a probe has no plan, so it is named here
        # and the same list feeds the measurement and the binding.
        #
        # DUE WHEN THE LAST STEP HAS SETTLED, which is what `_binding_due` falls back to when no
        # layer explicitly drives the hand -- and no layer does here, because these steps carry no
        # overlays. A step reached through a generated posture change is not settled when it starts:
        # the descent is still running through it, and the worst contact error on this very plan
        # landed 0.12 m mid-way down with the hands correct before and after.
        def settled(step):
            start = step.get("start_at_s") or 0.0
            if step.get("generated"):
                start += step["generated"].get("duration_s") or 0.0
            return round(start, 4)

        due_at = settled(steps[-1]) if steps else 0.0
        expect, two_handed = [], {}
        for spec in args.bind:
            effector, _, name = spec.partition("=")
            effector, name = effector.strip(), name.strip()
            if effector not in OTHER_HAND or not name:
                print("   ignoring --bind %r; write it as left_hand=Laptop" % spec)
                continue
            hits = (await link.call(P.T.SCENE_FIND, {"name_contains": name}))["objects"]
            if len(hits) != 1:
                print("   --bind %s: %r matched %d objects, so nothing is bound to it"
                      % (effector, name, len(hits)))
                continue
            expect.append({"effector": effector, "object_id": hits[0]["id"], "due_at_s": due_at})
            two_handed[hits[0]["id"]] = bool(hits[0].get("two_handed_anchors"))

        # The other hand, when the OBJECT says where both of them go. Same rule the tool applies in
        # `_pair_bound_hands`, and it is the object's rule rather than the probe's: where a thing
        # carries one grab point, aiming two hands at it pulls both wrists onto the same spot.
        bound = {e["effector"] for e in expect}
        for e in list(expect):
            other = OTHER_HAND[e["effector"]]
            if other not in bound and two_handed.get(e["object_id"]):
                bound.add(other)
                expect.append({"effector": other, "object_id": e["object_id"], "due_at_s": due_at})

        for e in expect:
            print("   contact %-11s -> %-14s due at %5.2fs"
                  % (e["effector"], e["object_id"], e["due_at_s"]))

        # THE BINDINGS ARE THE SAME LIST AT THE SAME MOMENT. A binding belongs to the step that
        # reaches for something, not to the plan -- applied from frame zero it drags her arms toward
        # the desk for the whole walk. A probe that sent a different payload from the tool's is how a
        # verified path and the real path came to disagree once already.
        ik = [{"effector": e["effector"], "object_id": e["object_id"], "at_s": e["due_at_s"]}
              for e in expect]
        for b in ik:
            print("   bind    %-11s -> %-14s from %5.2fs" % (b["effector"], b["object_id"], b["at_s"]))

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
