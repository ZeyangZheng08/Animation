#!/usr/bin/env python3
"""
run_eval.py — score retrieval against `retrieval_eval_set.json`.

The eval set has existed since Phase 1 with `_meta` saying "DATA ONLY - there is no run_eval.py yet".
This is that runner.

ARMS. The point of the `baseline` arm is to make the agent's number mean something. It is BM25 argmax
with no model at all: it can only ever answer `full_match`, so it should get the eight `fm-*` cases and
fail both `dc-*` and both `nm-*`. That 8/12 is the floor. An agent arm that also scores 8/12 has bought
nothing, and without the floor printed next to it, 8/12 reads like a success.

    baseline   no LLM, no engine, no API key. Hermetic, so it can live in check_kb.sh.
    realtime   gpt-realtime over the Realtime API (added with the LLM layer)
    chat       a Chat Completions reasoning model, same tools, same prompts (comparison arm)

SCORING is deliberately not one number.

    full_match  exact action_id.
    decompose   set-equality of {action_id -> frozenset(channels)} over the eight anatomical channels,
                with root asserted separately because the ground truth only names it in one of the two
                cases. Per-channel ownership F1 is reported alongside, so a near miss is visible rather
                than collapsing to a binary miss.
    no_match    the arm must decline. `nearest` is advisory in the ground truth and is not scored.

ONE WART IN THE GROUND TRUTH, worked around rather than edited: `free_channels` carries two different
meanings across the two decompose cases — "unclaimed by any overlay" in dc-walk-carry, and "retargeted
to IK" in dc-givepills-gaze. They coincide numerically here (giving_pills' head is a `stabilizer`, so
the role rule leaves it unclaimed anyway), so both are reproduced by the same derivation. If a future
case separates them, the field will need splitting.

Usage:
    python run_eval.py                     # baseline arm, all cases
    python run_eval.py --case dc-walk-carry
    python run_eval.py --verbose
"""
import argparse
import asyncio
import json
import os
import sys

import paths
from agent import assemble as A
from agent.kbindex import ANATOMICAL, KBIndex
# Only the constant at module scope: the baseline arm must not drag in the LLM stack to run.
from agent.llm import DEFAULT_MODEL

EVAL_FILE = "retrieval_eval_set.json"


def load_cases():
    path = os.path.join(paths.require_kb(), EVAL_FILE)
    if not os.path.exists(path):
        raise SystemExit("eval set not found at %s" % path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)["cases"]


# ---- arms ----------------------------------------------------------------------------------

def baseline_arm(case, kb):
    """BM25 argmax. Structurally incapable of decomposing or declining — that is the point."""
    hits = kb.search(case["query"], limit=1)
    if not hits:
        return {"type": "no_match", "nearest": None}
    return {"type": "full_match", "action_id": hits[0].action_id}


async def agent_arm(case, kb, model):
    """The real loop, with the real tools, minus the engine. What it ANSWERS is read out of its tool
    trace plus its text, not parsed out of prose — the trace is the ground truth of what it decided.

    A turn that names one action and stops is a full_match; one that plans a base plus overlays is a
    decompose; one that commits to nothing is a no_match. That mapping is deliberately mechanical, so
    the score measures the agent's decisions rather than an LLM judge's reading of its prose.
    """
    from agent import keys, llm
    from agent.loop import Session
    from agent.prompt import INSTRUCTIONS
    from agent.tools import ToolRegistry
    from agent.tools import kb as kb_tools
    from agent import assemble as A

    # Pinned, not defaulted: this eval measures retrieval, and every arm it has ever scored saw
    # kb_search + kb_get_action + plan_motion. Letting the surface widen because the tool modules were
    # reorganised would make new numbers incomparable with the recorded ones for no stated reason.
    registry = kb_tools.register(ToolRegistry(), kb, measuring=False)
    planned = []

    def plan_motion(character=None, base=None, overlays=None, gaze_at=None, **_ignored):
        """Stands in for the engine-backed tool: derives the same partition, commits nothing.

        `gaze_at` must exist here even though there is no engine: without it the model cannot express
        "give the pills while looking at the monitor" at all, and dc-givepills-gaze would be scoring a
        capability the tool surface does not offer rather than anything about the model.
        """
        if base not in kb.actions:
            from agent.tools import ToolFailure
            raise ToolFailure("unknown action_id: %s" % base)
        assembly = A.arbitrate(base, [o for o in (overlays or []) if o in kb.actions], kb)
        planned.append((assembly, gaze_at))
        return {"derived": assembly.as_dict(), "gaze_at": gaze_at}

    registry.add("plan_motion",
                 "Combine one base action with optional overlays and play it. The body-channel split "
                 "is derived for you from the actions you name. Use gaze_at to have the character look "
                 "at something while the motion plays -- the head is then solved by IK, not retrieved.",
                 {"type": "object", "additionalProperties": False,
                  "properties": {"character": {"type": "string"},
                                 "base": {"type": "string"},
                                 "overlays": {"type": "array", "items": {"type": "string"}},
                                 "gaze_at": {"type": "string"}},
                  "required": ["base"]},
                 plan_motion)

    backend = llm.backend_for(model, keys.load_openai_key())
    session = Session(backend, registry, INSTRUCTIONS)
    try:
        await session.start()
        report = await session.run_turn(case["query"])
    finally:
        await session.close()

    answer = _read_answer(planned, report, kb)
    answer["_report"] = report
    return answer


def _read_answer(planned, report, kb):
    if planned:
        assembly, gaze_at = planned[-1]
        # The same call the live plan_motion makes, so what is scored here and what a real turn
        # records in its trace cannot drift apart. The reasoning behind the two awkward cases -- idle
        # under a lone overlay, and a gaze freeing the head -- is in assemble.verdict.
        return A.verdict(assembly, gaze_at)

    # No plan committed. Did it name exactly one action in its answer?
    named = [aid for aid in kb.actions if aid in (report.text or "")]
    if len(named) == 1:
        return {"type": "full_match", "action_id": named[0]}
    return {"type": "no_match", "nearest": named[0] if named else None}


ARMS = {"baseline": baseline_arm, "agent": agent_arm}


# ---- scoring -------------------------------------------------------------------------------

def score_full_match(got, expected):
    if got.get("type") != "full_match":
        return False, "answered %s, expected full_match" % got.get("type")
    if got.get("action_id") != expected["action_id"]:
        return False, "%s != %s" % (got.get("action_id"), expected["action_id"])
    return True, ""


def score_no_match(got, expected):
    if got.get("type") != "no_match":
        return False, "answered %s (%s), expected no_match" % (got.get("type"), got.get("action_id"))
    return True, ""


def _partition(parts):
    """[{action_id, channels}] -> {action_id: frozenset(anatomical channels)}. Root dropped."""
    return {p["action_id"]: frozenset(c for c in p["channels"] if c in ANATOMICAL) for p in parts}


def score_decompose(got, expected):
    if got.get("type") != "decompose":
        return False, "answered %s, expected decompose" % got.get("type"), 0.0
    want = _partition(expected["parts"])
    have = _partition(got.get("parts", []))
    if have == want:
        return True, "", 1.0

    f1 = channel_f1(have, want)
    if set(have) != set(want):
        return False, "actions %s != %s" % (sorted(have), sorted(want)), f1
    diffs = ["%s: %s != %s" % (aid, sorted(have[aid]), sorted(want[aid]))
             for aid in want if have.get(aid) != want[aid]]
    return False, "; ".join(diffs), f1


def channel_f1(have, want):
    """Per-channel ownership F1 over (action_id, channel) pairs. Partial credit, so a one-channel miss
    is visibly different from a wrong action."""
    got_pairs = {(a, c) for a, chans in have.items() for c in chans}
    want_pairs = {(a, c) for a, chans in want.items() for c in chans}
    if not got_pairs and not want_pairs:
        return 1.0
    hit = len(got_pairs & want_pairs)
    if not hit:
        return 0.0
    precision = hit / len(got_pairs)
    recall = hit / len(want_pairs)
    return round(2 * precision * recall / (precision + recall), 3)


def score(case, got):
    expected = case["expected"]
    kind = expected["type"]
    if kind == "full_match":
        ok, detail = score_full_match(got, expected)
        return ok, detail, 1.0 if ok else 0.0
    if kind == "no_match":
        ok, detail = score_no_match(got, expected)
        return ok, detail, 1.0 if ok else 0.0
    if kind == "decompose":
        return score_decompose(got, expected)
    raise ValueError("unknown expected type: %s" % kind)


# ---- reporting -----------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--arm", default="baseline", choices=sorted(ARMS))
    ap.add_argument("--case", action="append", help="run only these case ids")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="agent arm only")
    ap.add_argument("--repeat", type=int, default=1,
                    help="run every case N times. The agent arm is not deterministic -- a single run "
                         "has no variance estimate and cases do flip between runs.")
    ap.add_argument("--trace", help="write per-case traces to this JSON file")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    kb = KBIndex.load()
    cases = load_cases()
    if args.case:
        wanted = set(args.case)
        cases = [c for c in cases if c["id"] in wanted]
        missing = wanted - {c["id"] for c in cases}
        if missing:
            raise SystemExit("no such case: %s" % ", ".join(sorted(missing)))

    arm = ARMS[args.arm]
    print("== retrieval eval: arm=%s, %d case(s), corpus=%d actions ==\n"
          % (args.arm, len(cases), len(kb.actions)))

    by_type = {}
    traces = []
    per_case = {}
    for run in range(args.repeat):
        if args.repeat > 1:
            print("  -- run %d/%d --" % (run + 1, args.repeat))
        for case in cases:
            if args.arm == "agent":
                got = asyncio.run(arm(case, kb, args.model))
            else:
                got = arm(case, kb)
            report = got.pop("_report", None)
            ok, detail, partial = score(case, got)
            kind = case["expected"]["type"]
            bucket = by_type.setdefault(kind, {"pass": 0, "total": 0, "partial": 0.0})
            bucket["total"] += 1
            bucket["pass"] += int(ok)
            bucket["partial"] += partial
            per_case.setdefault(case["id"], []).append(ok)
            if report is not None:
                traces.append({"case": case["id"], "run": run, "expected": case["expected"],
                               "answered": got, "pass": ok, "detail": detail, **report.as_dict()})

            mark = "PASS" if ok else "FAIL"
            line = "  %-4s %-18s %-11s" % (mark, case["id"], kind)
            if report is not None:
                line += " %2d tool %4.1fs " % (report.tool_calls, report.seconds)
            if not ok:
                line += "  %s" % detail
                if kind == "decompose":
                    line += "   (channel F1 %.2f)" % partial
            print(line)
            if args.verbose:
                print("       query:    %s" % case["query"])
                print("       answered: %s" % json.dumps(got, ensure_ascii=False))
                if report is not None:
                    print("       tools:    %s" % " -> ".join(report.tools_used()))
                    print("       said:     %s" % report.text)

    total_pass = sum(b["pass"] for b in by_type.values())
    total = sum(b["total"] for b in by_type.values())
    print("\n  by type:")
    for kind in sorted(by_type):
        b = by_type[kind]
        extra = ""
        if kind == "decompose":
            extra = "   mean channel F1 %.2f" % (b["partial"] / b["total"])
        print("    %-11s %d/%d%s" % (kind, b["pass"], b["total"], extra))
    print("\n  %d/%d" % (total_pass, total))

    if args.repeat > 1:
        flaky = {cid: results for cid, results in per_case.items() if 0 < sum(results) < len(results)}
        print("\n  per-case pass rate over %d runs:" % args.repeat)
        for cid in sorted(per_case):
            results = per_case[cid]
            print("    %-18s %d/%d%s" % (cid, sum(results), len(results),
                                         "   <- unstable" if cid in flaky else ""))
        if flaky:
            print("\n  %d case(s) flipped between runs. A single-run score for this arm is noise."
                  % len(flaky))

    if traces:
        calls = sum(t["tool_calls"] for t in traces)
        seconds = sum(t["seconds"] for t in traces)
        print("\n  %.1f tool calls/case, %.1fs/case, %.0fs total"
              % (calls / len(traces), seconds / len(traces), seconds))
    if args.trace:
        with open(args.trace, "w", encoding="utf-8", newline="") as f:
            f.write(json.dumps(traces, ensure_ascii=False, indent=2) + "\n")
        print("  traces -> %s" % args.trace)

    if args.arm == "baseline":
        print("\n  This is the floor, not a target. BM25 argmax can only answer full_match, so the\n"
              "  decompose and no_match cases are expected failures. An agent arm is only worth\n"
              "  reporting if it beats this number.")
    return 0 if total_pass == total else 1


if __name__ == "__main__":
    sys.exit(main())
