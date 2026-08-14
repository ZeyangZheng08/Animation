"""
gates.py — turning a geometric measurement into something the model can act on.

THE MODEL NEVER SEES A NUMBER IT COULD ACT ON WRONGLY. The engine reports metres and frame indices; this
translates them into a named failure and a named remedy, in the vocabulary the model already works in —
action ids, body parts, scene objects. "right_hand is 0.31 m from the Aspirin Bottle at frame 34" becomes
"the right hand never reached the Aspirin Bottle; stand closer, or bind a different object". The measured
value still travels, for the log and for a human, but the model is told what to DO.

That is the same shape as a failed tool call, deliberately: a gate rejection comes back as an ordinary
tool result with success=false, so the agent reacts inside the same turn rather than the turn dying. The
retry is another plan_motion call, caused by real geometry rather than staged.

MEASURED IS NOT FAILED. Metrics with no defensible threshold report status "measured" and never fail a
plan. Inventing a cutoff so the gate has something to say would produce confident rejections with nothing
behind them, and the thresholds that do exist here are the ones that follow from geometry rather than
from taste.

AND PENDING IS NOT PASSED. A check the engine has declared but cannot answer yet is a third state, and
collapsing it into either of the other two is a lie in one direction or the other. This mattered: the
landing of a generated sit only becomes measurable about three seconds in, every `check_motion` the agent
made arrived earlier, the metric was absent rather than pending, and counting failures found none. The
gate reported a sit as good while the character was still walking towards the chair.
"""
import asyncio
import time

# What a failed metric means, and what to do about it, in the model's own terms. Keyed by the metric id
# prefix the engine emits.
REMEDIES = {
    "contact_hold": (
        "the {effector} did not stay on the object it was bound to",
        "bind the effector to a closer object, add stand_at so the character is within reach, or drop "
        "the binding if the motion does not actually need to touch anything",
    ),
    "ground_penetration": (
        "a foot went through the floor",
        "the composed layers disagree about the legs; give the legs to a single action, or pick a base "
        "whose posture matches the overlay",
    ),
    "foot_skate": (
        "a planted foot slid along the floor",
        "usually a phase mismatch between a locomotion base and an overlay",
    ),
    # Added after the gate passed a sit that landed on nothing. Every other metric asks how well the
    # motion matched its own plan; this one asks whether the plan put her on anything.
    "seated_on_support": (
        "the sit finished somewhere other than on the thing named to sit on",
        "the descent was generated where she already stood. Get her to the seat first -- pass stand_at "
        "with an anchor beside it -- and change posture only once she is there",
    ),
    "sat_through_support": (
        "the sit finished underneath the thing named to sit on, not on top of it",
        "that object is something to work at, not to sit on -- its surface is above where her hips "
        "end up. Pass a seat as sit_on and reach the other thing where it stands",
    ),
    "hip_reached_target": (
        "the posture change did not happen: the pelvis ended up nearer where it started than where "
        "this plan said it was going",
        "the frames were scheduled but the character did not move through them. Report this as a "
        "failed sit rather than a sit; do not describe her as seated",
    ),
    # Both of these say WHY a posture change did not happen, and neither has a plan-level remedy. They
    # are here so the failure names its own cause instead of arriving as an unexplained one, and so the
    # model stops re-planning against something no plan can fix.
    "correction_reached_graph": (
        "the generated frames were computed and then discarded before they reached the character",
        "nothing about the plan caused this and changing the plan will not fix it. Say the transition "
        "could not be played and that the engine dropped it",
    ),
    "descent_saturated": (
        "the generated frames asked the character to lower further than a body can",
        "nothing about the plan caused this and changing the plan will not fix it. Say the transition "
        "was played and ran past what the character could do",
    ),
    # The clip does the reaching; whether it reaches the REAL object depends on where she is standing
    # or sitting. Nothing about this is fixed by binding a hand -- that overrides the animation with a
    # point and makes it worse.
    "contact_reached": (
        "the {effector} never reached the object its own animation is performed against",
        "the character is not placed where the motion expects the object to be. Move her closer with "
        "move_to, or sit her on something that puts her at the right height for it. Do NOT add an "
        "ik_binding: the clip already animates that hand and a binding replaces it with a single point",
    ),
}


def summarise(report):
    """Engine gate report -> a model-facing dict. Returns (ok, payload).

    `ok` is true only for a real pass. A pending report is not ok, and `payload["status"]` says which of
    the two reasons it was: something failed, or something has not happened yet.
    """
    status = report.get("status")
    if status == "unavailable":
        return True, {"gates": "not measured", "note": report.get("note")}

    metrics = report.get("metrics", [])
    failures, observations, waiting = [], [], []
    for metric in metrics:
        kind = (metric.get("id") or "").split(":")[0]
        effector = (metric.get("id") or "").split(":")[-1]
        measured = metric.get("measured")
        if metric.get("status") == "fail":
            what, how = REMEDIES.get(kind, ("a geometric check failed", "adjust the plan"))
            failures.append({
                "check": metric["id"],
                "problem": what.format(effector=effector.replace("_", " ")),
                "try": how,
                "measured_m": round(measured, 4) if isinstance(measured, (int, float)) else measured,
                "allowed_m": metric.get("tolerance"),
                "worst_frame": metric.get("worst_frame"),
            })
        elif metric.get("status") == "pending":
            waiting.append({"check": metric["id"], "why": metric.get("what"),
                            "answerable_in_s": metric.get("judgeable_in_s")})
        elif metric.get("status") == "measured":
            observations.append({"check": metric["id"], "measured": round(measured, 4),
                                 "note": "no calibrated threshold yet; reported, not judged"})

    payload = {
        "frames_measured": report.get("frames"),
        "observations": observations,
    }

    # WHAT THE GRAPH IS ACTUALLY HOLDING, when that is not what a reader would assume. A plan that asked
    # for a channel to be shared between two clips and one that quietly resolved it to a single winner
    # look identical from outside and both play, so the weight is read back off the mixer and reported
    # here. Left out entirely when nothing is mixed, which is every plan that has one source per
    # channel -- this is the projection that keeps the model's context small, and a line saying "no
    # channel was shared" would be paid for on every round trip of every turn.
    mixed = ((report.get("blend") or {}).get("mixed_channels")) or []
    if mixed:
        payload["composed"] = [
            {"channels": entry.get("channels"), "with": entry.get("action_id"),
             "share": round(float(entry.get("weight") or 0.0), 3)}
            for entry in mixed]
    if failures:
        payload["status"] = "fail"
        payload["failures"] = failures
        return False, payload
    if waiting:
        # Deliberately not a pass. The caller's job is to wait and ask again, and `judgeable_in_s`
        # says roughly how long -- see wait_until_judgeable, which is what every caller should use.
        payload["status"] = "pending"
        payload["pending"] = waiting
        payload["judgeable_in_s"] = report.get("judgeable_in_s")
        return False, payload
    payload["status"] = "pass"
    payload["gates"] = "passed"
    return True, payload


# A floor on how long to keep asking, not the whole budget. How long a landing takes to become
# answerable is a property of the plan -- the outgoing step has to reach its handover first -- so the
# engine reports `judgeable_in_s` and the wait extends to cover it. A fixed ceiling was tried at 8 s and
# a plan that opened on `idle` rather than `walking` ran past it, reporting "not yet measurable" about a
# motion that was measurable moments later. The hard cap is a backstop against a plan that never
# arrives, not an estimate of anything.
JUDGEMENT_TIMEOUT_S = 8.0
JUDGEMENT_HARD_CAP_S = 30.0
POLL_INTERVAL_S = 0.1


async def wait_until_judgeable(call, character, timeout=JUDGEMENT_TIMEOUT_S, sleep=None):
    """Poll the engine's gate until every declared check can be answered, then return the raw report.

    THIS IS WHY IT EXISTS. probe_sit.py computed the same wait by hand -- `start_at_s + blend_in_s + 1.0`
    -- before it read the gate, and passed. The agent's own `check_motion` read immediately, got a report
    with the landing check absent, and reported success about a character who was still standing. A
    verified probe and the real path disagreeing like that is the failure; the wait belongs here, where
    both go through it.

    Polling rather than an engine-side block: a request handler that sleeps stops Unity's main thread,
    which is measured and known (a 45 s sleep in one froze every scene query behind it). Each poll is a
    round trip of about 0.3 ms and costs no model iteration, so the loop is cheap and the engine stays a
    pure reactor.
    """
    if sleep is None:
        sleep = asyncio.sleep
    started = time.monotonic()
    deadline = started + timeout
    report = await call(character)
    while not report.get("judgeable", True) and time.monotonic() < deadline:
        await sleep(POLL_INTERVAL_S)
        report = await call(character)
        # The engine knows when its own plan becomes answerable; extend to cover it rather than
        # calling a slow plan unmeasurable.
        wait_more = report.get("judgeable_in_s") or 0
        if wait_more:
            deadline = min(started + JUDGEMENT_HARD_CAP_S,
                           max(deadline, time.monotonic() + wait_more + 1.0))
    return report
