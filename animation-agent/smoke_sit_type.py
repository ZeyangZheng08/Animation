#!/usr/bin/env python3
"""
smoke_sit_type.py — sitting down at a desk and working there, on the real path.

The cases `smoke_validate.py` does not cover: which way a sit-down leaves her facing, a seated
character switching to another seated clip without getting up, and the handover that has to keep her
pelvis on the seat while she does it. All three are PLACEMENTS rather than plans — the choice of
clips is the same either way — so this drives the tools directly and no model is involved.

WHAT THE ROOM ALLOWS, MEASURED. `EmergencyRoom` puts the chair 0.43 m from the laptop and pushes it
under the desk, and there is no walkable ground between the two: a preview of the point 0.10 m in
front of the chair towards the laptop already snaps 0.23 m away. `mx_Standing_To_Sitting_Transition`
travels 0.446 m backwards, so the standing point for sitting down FACING the laptop is inside the
desk, and no clip in the library sits down in less than 0.234 m. She therefore sits facing the way
she came and turns on the seat, which is what a person does at a chair that is already pushed in --
and asking for the impossible version is refused by name, which is case 3 below.

    1. walk over and sit down through `mx_Standing_To_Sitting_Transition` into a seated idle,
       nothing bound. A seated idle rather than the typing clip, so that case 2 is a switch to a
       DIFFERENT seated action rather than to the one already playing.
    2. in place, still on the same chair: type on the laptop with both hands bound to it. Expects no
       walk, no rise, a turn on the seat towards the laptop, and a pelvis that stays on it.
    3. the same sit-down WITH the laptop bound, which the room cannot do. Expects the refusal to name
       the distance rather than quietly sitting her backwards.
    4. stand back up through `mx_Sitting_To_Standing_2` and walk. Two things at once: that she is on
       the navigation mesh, and that the walk after a retrieved rise actually arrives -- the rise
       hands the agent back on its own clock, and before it did the first walk after one was refused
       with "no complete route".

Usage:  python smoke_sit_type.py        (start this, then enter play mode in Unity)
"""
import argparse
import asyncio
import json
import time

from agent import gates as G
from agent import protocol as P
from agent.engine import DEFAULT_HOST, DEFAULT_PORT, EngineLink
from agent.kbindex import KBIndex
from agent.tools import ToolRegistry
from agent.tools import kb as kb_tools
from agent.tools import scene as scene_tools

IDLE = "mx_Standing_Idle"
SIT_DOWN = "mx_Standing_To_Sitting_Transition"
SEATED = "mx_Sitting_Still_In_A_Chair"
TYPING = "mx_Sitting_At_A_Computer_And_Typing"
STAND_UP = "mx_Sitting_To_Standing_2"

# What each expectation is worth. `work_target_bearing_deg` is the number this whole path exists to
# get right and no gate measures it: the seat checks pass just as well with her back to the desk.
WORK_BEARING_MAX_DEG = 30.0
SEAT_ALIGNMENT_MAX_M = 0.05


def show(label, value):
    print("   %-24s %s" % (label, json.dumps(value, default=str)))


def metric(report, name):
    """One metric out of a raw `gate.run` report, or None.

    THE RAW REPORT RATHER THAN `unity_measure`, and only here. What the tool hands the model is a
    projection: a metric that PASSED is not in it, because a passing check is not something the model
    has to read. A smoke is the other reader — the number it measured is the whole point — so this
    goes to the message underneath.
    """
    for entry in (report.get("metrics") or []):
        if entry.get("id") == name:
            return entry
    return None


async def measure(link, who, label):
    """Wait for the motion to be judgeable and print every metric it produced."""
    print("\n== gate.run: %s" % label)
    report = await G.wait_until_judgeable(
        lambda name: link.call(P.T.GATE_RUN, {"character": name}), who["character"])
    print("   frames %s  status %s" % (report.get("frames"), report.get("status")))
    for m in report.get("metrics") or []:
        print("     %-30s %-10s tol=%-8s %s"
              % (m["id"], m.get("measured"), m.get("tolerance"), m.get("status")))
    return report


def collect(report, problems, when, *names):
    """Every named metric, printed; a failure of any of them is a problem."""
    for name in names:
        entry = metric(report, name)
        show(name, entry and {"measured": entry.get("measured"), "status": entry.get("status")})
        if entry is None:
            problems.append("%s was not measured %s" % (name, when))
        elif entry.get("status") == "fail":
            problems.append("%s failed %s at %s" % (name, when, entry.get("measured")))
    for m in report.get("metrics") or []:
        if m.get("status") == "fail" and m["id"] not in names:
            problems.append("%s failed %s at %s" % (m["id"], when, m.get("measured")))


async def main(host, port, wait):
    kb = KBIndex.load()
    problems = []
    async with EngineLink(host, port) as link:
        print("serving ws://%s:%d — enter play mode in Unity (waiting up to %ds)" % (host, port, wait))
        hello = await link.wait_ready(timeout=wait)
        print("connected: scene=%s characters=%s protocol=v%s"
              % (hello.get("scene"), hello.get("characters"), hello.get("protocol")))
        character = (hello.get("characters") or ["chr:CPRNurse"])[0]
        who = {"character": character}

        registry = kb_tools.register(ToolRegistry(), kb)
        scene_tools.register(registry, link, kb)

        seat = ((await registry.dispatch("unity_query", {"query": "chair"})).get("results")
                or [{}])[0].get("id")
        desk = ((await registry.dispatch("unity_query", {"query": "laptop"})).get("results")
                or [{}])[0].get("id")
        show("seat", seat)
        show("laptop", desk)
        if not seat or not desk:
            raise SystemExit("this scene has no chair or no laptop; nothing here can be checked")

        # ---- 1. walk over and sit down ---------------------------------------------------------
        print("\n== unity_execute: walk over and sit down through %s" % SIT_DOWN)
        t0 = time.perf_counter()
        out = await registry.dispatch("unity_execute", dict(
            who, base=IDLE, then=[{"via": [SIT_DOWN], "base": SEATED}], sit_on=seat))
        print("   %.2f s total" % (time.perf_counter() - t0))
        show("success", out.get("success"))
        if not out.get("success"):
            show("error", out.get("error"))
            show("hint", out.get("hint"))
            raise SystemExit("the sit-down was refused; nothing after this can run")
        show("seated_facing", out.get("seated_facing"))
        show("walked", out.get("walked"))
        show("validated", out.get("validated"))
        show("handover_offsets_m", (out.get("engine") or {}).get("handover_offsets_m"))

        landed = await measure(link, who, "the sit")
        collect(landed, problems, "after the sit", "seat_alignment", "seated_on_support")
        alignment = metric(landed, "seat_alignment")
        if alignment and isinstance(alignment.get("measured"), (int, float)) \
                and alignment["measured"] > SEAT_ALIGNMENT_MAX_M:
            problems.append("seat_alignment %.4f m after the sit" % alignment["measured"])

        # ---- 2. the in-place switch, with both hands on the laptop -----------------------------
        print("\n== unity_execute: type on %s in place, still on %s" % (desk, seat))
        t0 = time.perf_counter()
        out = await registry.dispatch("unity_execute", dict(
            who, base=TYPING, sit_on=seat,
            ik_bindings=[{"effector": "left_hand", "object_id": desk},
                         {"effector": "right_hand", "object_id": desk}]))
        print("   %.2f s total" % (time.perf_counter() - t0))
        show("success", out.get("success"))
        if out.get("error"):
            show("error", out.get("error"))
            show("hint", out.get("hint"))
        show("stayed_seated_on", out.get("stayed_seated_on"))
        show("seated_facing", out.get("seated_facing"))
        show("seated_turn_deg", out.get("seated_turn_deg"))
        show("work_target_bearing_deg", out.get("work_target_bearing_deg"))
        show("walked", out.get("walked"))
        show("stood_up", out.get("stood_up"))
        offsets = (out.get("engine") or {}).get("handover_offsets_m") or []
        show("handover_offsets_m", offsets)

        if not out.get("success"):
            problems.append("the in-place switch was refused")
        if out.get("stayed_seated_on") != seat:
            problems.append("the in-place switch did not stay seated")
        if out.get("walked") is not None or out.get("stood_up") is not None:
            problems.append("the in-place switch walked her or stood her up")
        if (out.get("seated_facing") or {}).get("toward") != desk:
            problems.append("the seated heading was not aimed at %s" % desk)
        bearing = out.get("work_target_bearing_deg")
        if bearing is None:
            problems.append("no work_target_bearing_deg was measured")
        elif bearing > WORK_BEARING_MAX_DEG:
            problems.append("she ended up %.1f deg off %s" % (bearing, desk))

        # THE PELVIS AT THE HANDOVER, which is what the offset is FOR: the correction is exactly how
        # far the pelvis would have jumped between the two clips without it.
        applied = max([o.get("distance_m") or 0.0 for o in offsets] or [0.0])
        print("   the handover was corrected by %.4f m — the jump it removed" % applied)

        switched = await measure(link, who, "the in-place switch")
        collect(switched, problems, "after the switch",
                "seat_alignment", "seated_on_support",
                "contact_hold:left_hand", "contact_hold:right_hand")
        alignment = metric(switched, "seat_alignment")
        if alignment and isinstance(alignment.get("measured"), (int, float)) \
                and alignment["measured"] > SEAT_ALIGNMENT_MAX_M:
            problems.append("seat_alignment %.4f m after the switch" % alignment["measured"])

        # ---- 3. the sit-down this room cannot do ------------------------------------------------
        print("\n== unity_validate: sit down FACING %s, which needs ground the room does not have"
              % desk)
        out = await registry.dispatch("unity_validate", dict(
            who, base=IDLE, then=[{"via": [SIT_DOWN], "base": SEATED}], sit_on=seat,
            ik_bindings=[{"effector": "right_hand", "object_id": desk}]))
        show("success", out.get("success"))
        show("error", out.get("error"))
        show("hint", out.get("hint"))
        if out.get("success"):
            problems.append("the impossible sit-down was accepted")
        elif "not walkable" not in (out.get("error") or ""):
            problems.append("the refusal does not say the standing point is unwalkable")

        # ---- 4. and back onto her feet ----------------------------------------------------------
        print("\n== unity_execute: stand back up through %s" % STAND_UP)
        out = await registry.dispatch("unity_execute", dict(
            who, base=TYPING, sit_on=seat, then=[{"via": [STAND_UP], "base": IDLE}]))
        show("success", out.get("success"))
        if out.get("error"):
            show("error", out.get("error"))
        await asyncio.sleep(2.5)
        state = await link.call(P.T.MOTION_LOCOMOTE, dict(who, query=True))
        show("posture", state.get("posture"))
        show("on_navmesh", state.get("on_navmesh"))
        show("facing_deg", state.get("facing_deg"))
        if state.get("posture") != "standing":
            problems.append("she did not end up standing")
        if not state.get("on_navmesh"):
            problems.append("she is not back on the navigation mesh")

        print("\n== unity_locomotion: and she can walk again")
        walk = await registry.dispatch("unity_locomotion", dict(who, destination="obj:Patient"))
        show("walked", {k: walk.get(k) for k in ("arrived", "path_length_m", "note")})
        if not walk.get("arrived"):
            problems.append("she could not walk after standing up: %s" % walk.get("error"))

        print("\n---- verdict ----")
        if problems:
            for line in problems:
                print("   FAIL  %s" % line)
            raise SystemExit(1)
        print("   every expectation held")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--wait", type=int, default=120)
    args = parser.parse_args()
    asyncio.run(main(args.host, args.port, args.wait))
