#!/usr/bin/env python3
"""probe_walk.py — foot skate DURING the walk, which nothing on the normal path ever reads.

The gate's accumulator is reset per plan and `unity_measure` is asked about the plan that FOLLOWS the
walk, so the number a turn reports describes a character standing still. Travel happens under the
walk's own plan, and nobody ever asks that plan for its numbers. This does, while it is still going.

It is also the witness for the planted-foot test. That test used to compare the foot BONE against a
flat five centimetres, and on this avatar the ankle sits 0.072 to 0.080 m up even when the sole is
flat on the floor -- so it never fired, and every reading was 0.0000, which reads as "no skate"
rather than "never sampled". Against the rig's own sole height the walk measures about 0.9 m/s of
slide with a peak near 2.05 at the stop; the exact figure moves a little between runs with where in
the stride the sampling lands. Three speeds bracket the minimum -- 0.6 gives 1.85, 1.5 gives 0.9,
2.4 gives 1.83 -- so the configured 1.5 is already where the mismatch is smallest, and what is left
is the clip's own within-stance foot motion rather than something to correct. Re-run it before
believing any of that again.

Run with Unity in play mode, and with nothing else serving the runtime channel: this is the server,
so the auto-started service has to be off (Tools > Animation Agent > Open Terminal On Play).
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

# Runtime primitives, named rather than spelled. The eight nursing actions these probes used to
# name left the knowledge base (agent/nursing_assets/); these are real Mixamo records, and
# `tests/corpus.py` holds the same constants for the test suite.
WALK = "mx_Walking_Forward"
IDLE = "mx_Standing_Idle"

from agent import assemble as A
from agent import protocol as P
from agent.engine import DEFAULT_HOST, DEFAULT_PORT, EngineLink
from agent.kbindex import KBIndex

LOCOMOTION = WALK


def walk_step(kb):
    """One looping walk, full body — the same payload unity_locomotion puts under a displacement."""
    assembly = A.arbitrate(LOCOMOTION, [], kb)
    record = kb.record(LOCOMOTION)
    layers = [{"action_id": aid, "channels": chans, "source": "base",
               "owns_root": aid == assembly.root_owner, "hold_final_pose": False,
               "clip": {"guid": record["source_clip"]["guid"],
                        "clip_name": record["source_clip"]["clip_name"]}}
              for aid, chans in assembly.layers]
    return assembly, {"action_id": LOCOMOTION, "layers": layers, "start_at_s": 0.0,
                      "blend_in_s": 0.0, "clip_start_frame": 0, "duration_s": None,
                      "loop": True, "posture": "standing",
                      "frame_rate": record.get("frame_rate") or 30}


async def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", default="obj:Chair", help="where to walk, as a registry id")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--wait", type=int, default=120)
    args = ap.parse_args(argv)

    kb = KBIndex.load()
    async with EngineLink(args.host, args.port) as link:
        print("serving ws://%s:%d — enter play mode in Unity" % (args.host, args.port), flush=True)
        try:
            hello = await link.wait_ready(timeout=args.wait)
        except (asyncio.TimeoutError, TimeoutError):
            _wait_for_unity(None, "%s:%d" % (args.host, args.port))
        character = (hello.get("characters") or ["chr:CPRNurse"])[0]

        start = await link.call(P.T.MOTION_LOCOMOTE,
                                {"character": character, "to": args.to, "stop_within_m": 0.08})
        if not start.get("going"):
            print("she is already there; there is no walk to measure")
            return 1
        print("walking %.2f m, eta %.2f s" % (start.get("path_length_m", -1), start.get("eta_s", -1)))

        assembly, step = walk_step(kb)
        await link.call(P.T.MOTION_ASSEMBLE, {
            "character": character, "steps": [step],
            "free_channels": assembly.free_channels,
            "ik": [], "gaze_at": None, "stand_at": None, "carry": [], "mode": "commit"})

        # Sampled while she travels, because the metric is a running worst-case that the next plan
        # resets. Reading it once at the end would read it after the walk it is about.
        worst, samples, state = 0.0, [], {}
        for _ in range(40):
            await asyncio.sleep(0.2)
            state = await link.call(P.T.MOTION_LOCOMOTE, {"character": character, "query": True})
            report = await link.call(P.T.GATE_RUN, {"character": character})
            for metric in report.get("metrics") or []:
                if metric.get("id") == "foot_skate" and metric.get("measured") is not None:
                    worst = max(worst, float(metric["measured"]))
                    samples.append(round(float(metric["measured"]), 3))
            if state.get("arrived"):
                break

        print("\nagent speed          %.2f m/s" % (state.get("speed_m_per_s") or -1))
        print("samples              %s" % samples[:12])
        print("worst planted-foot   %.3f m/s" % worst)
        if not samples:
            print("\nNo sample was taken. That is the failure the planted-foot test used to have: no "
                  "frame counted as down, so the metric reported nothing and it looked like zero.")
            return 1
        print("\nA planted foot should not travel over the ground at all, so this is the skate. There "
              "is no calibrated threshold — the number is reported, not judged.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
