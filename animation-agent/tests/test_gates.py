"""test_gates.py — pending is not passing, and the wait is on the real path.

The bug these are written against: a generated sit only becomes measurable seconds after the plan is
committed, every check_motion the agent made arrived before that, the landing check was ABSENT from the
report rather than pending, and counting failures found none. The gate said "passed" about a character
still walking towards the chair. probe_sit.py had the wait; the path the agent uses did not.
"""
import asyncio

import pytest

from agent import gates as G


def metric(mid, status, measured=0.0, tolerance=0.0):
    return {"id": mid, "status": status, "measured": measured, "tolerance": tolerance,
            "worst_frame": 3, "what": mid}


def report(metrics, judgeable=None, judgeable_in_s=0.0):
    pending = [m["id"] for m in metrics if m["status"] == "pending"]
    return {
        "status": "fail" if any(m["status"] == "fail" for m in metrics)
                  else ("pending" if pending else "pass"),
        "failed": [m["id"] for m in metrics if m["status"] == "fail"],
        "pending": pending,
        "judgeable": (not pending) if judgeable is None else judgeable,
        "judgeable_in_s": judgeable_in_s,
        "frames": 12, "seconds": 0.4, "metrics": metrics,
    }


def test_a_clean_report_passes():
    ok, payload = G.summarise(report([metric("ground_penetration", "pass")]))
    assert ok
    assert payload["status"] == "pass"


def test_a_failed_check_fails():
    ok, payload = G.summarise(report([metric("seated_on_support", "fail", 1.4, 0.0)]))
    assert not ok
    assert payload["status"] == "fail"
    assert "sit finished somewhere other than" in payload["failures"][0]["problem"]


def test_pending_is_not_a_pass():
    """The whole point. Everything else in the report is green and the verdict is still not `pass`."""
    metrics = [metric("ground_penetration", "pass"),
               metric("contact_hold:right_hand", "pass"),
               dict(metric("seated_on_support", "pending"), measured=None, tolerance=None,
                    judgeable_in_s=2.1)]
    ok, payload = G.summarise(report(metrics, judgeable_in_s=2.1))
    assert not ok
    assert payload["status"] == "pending"
    assert payload["pending"][0]["check"] == "seated_on_support"
    assert payload["pending"][0]["answerable_in_s"] == 2.1
    assert "gates" not in payload          # never the word that reads as a verdict


def test_a_failure_outranks_a_pending_check():
    """A foot already through the floor stays a failure however long the sit still has to run."""
    metrics = [metric("ground_penetration", "fail", 0.09, 0.01),
               dict(metric("seated_on_support", "pending"), measured=None, tolerance=None)]
    ok, payload = G.summarise(report(metrics))
    assert not ok
    assert payload["status"] == "fail"


def test_an_omitted_check_would_have_read_as_a_pass():
    """The shape of the original defect, kept as a test so it cannot come back quietly: a report that
    simply leaves the landing check out is indistinguishable from one that ran and was happy."""
    ok, payload = G.summarise(report([metric("ground_penetration", "pass")]))
    assert ok and payload["status"] == "pass"


@pytest.mark.asyncio
async def test_wait_until_judgeable_polls_until_the_landing_is_answerable():
    answers = [report([dict(metric("seated_on_support", "pending"), measured=None)],
                      judgeable_in_s=0.4)] * 3
    answers.append(report([metric("seated_on_support", "pass")]))
    seen = []

    async def call(character):
        seen.append(character)
        return answers[min(len(seen) - 1, len(answers) - 1)]

    async def sleep(_):
        return None

    out = await G.wait_until_judgeable(call, "chr:Nurse", timeout=5.0, sleep=sleep)
    assert out["judgeable"] is True
    assert len(seen) == 4
    ok, payload = G.summarise(out)
    assert ok and payload["status"] == "pass"


@pytest.mark.asyncio
async def test_wait_gives_up_rather_than_hanging_a_turn():
    """A plan the engine says should already be answerable, and never is, returns the pending report —
    which summarises as not-a-pass. Waiting forever would be its own kind of lie."""
    stuck = report([dict(metric("seated_on_support", "pending"), measured=None)], judgeable_in_s=0.0)

    async def call(_):
        return stuck

    out = await G.wait_until_judgeable(call, "chr:Nurse", timeout=0.05, sleep=asyncio.sleep)
    assert out["judgeable"] is False
    ok, payload = G.summarise(out)
    assert not ok and payload["status"] == "pending"


@pytest.mark.asyncio
async def test_the_wait_follows_the_engine_rather_than_a_fixed_ceiling():
    """How long a landing takes to become answerable belongs to the plan: the outgoing step has to
    reach its handover first. A fixed 8 s ceiling was tried and a plan opening on `idle` rather than
    `walking` ran past it, reporting "not yet measurable" about a motion that was measurable moments
    later."""
    slow = report([dict(metric("seated_on_support", "pending"), measured=None)], judgeable_in_s=12.0)
    answers = [slow, slow, report([metric("seated_on_support", "pass")])]
    seen = []

    async def call(_):
        seen.append(1)
        return answers[min(len(seen) - 1, len(answers) - 1)]

    async def sleep(_):
        return None

    out = await G.wait_until_judgeable(call, "chr:Nurse", timeout=0.05, sleep=sleep)
    assert out["judgeable"] is True, "the wait should have followed the engine past its own timeout"
