#!/usr/bin/env python3
"""
run_eval.py — ARCHIVED. Score retrieval against `retrieval_eval_set.json`.

DOES NOT RUN, and is kept for the reason README.md next to it gives: every case names one of the
eight nursing actions, and those records left the knowledge base when the Mixamo corpus became the
whole of it. `KBIndex.load()` will not find them. Nothing in the live tree imports this file, and
`sys.path` no longer reaches the repository root from here — running it needs both fixing, which is
deliberate.

The eval set has existed since Phase 1 with `_meta` saying "DATA ONLY - there is no run_eval.py yet".
This is that runner.

ARMS. The point of the `baseline` arm is to make the agent's number mean something. It is BM25 argmax
with no model at all: it can only ever answer `full_match`, so its CEILING is the eight `fm-*` cases
and it must fail both `dc-*` and both `nm-*`. What it actually scores is **7/12**, and has since
before the v4 contract change: `fm-giving-pills` ("hands the patient the oral medication to take")
returns `grab_bottle`, which it also did under v3. That is the floor. An agent arm that only matches
the floor has bought nothing, and without the floor printed next to it the number reads like a success.

THE FLOOR DID NOT MOVE ACROSS motionkb/v4, and that was measured rather than assumed. v4 deletes
`tags`, `display_name` and `overall_intent` (ADR 0022), so the searchable document went from six term
sources to two: `action_description` at triple weight -- the tags' old weight, on the field that
inherited their job -- plus `action_id` and the eight `motion_description`s. Losing curated keywords
is a real weakening, and the natural guess is that `fm-giving-pills` is what it cost. It is not: the
v3 document was rebuilt verbatim over the v3 records straight out of git and scored 7/12, failing the
same case to the same wrong answer. Nothing was reweighted to reach that number, and nothing should
be -- fitting the index to these twelve cases is the thing the `no_match` cases exist to catch.

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

WHAT THE DECOMPOSE CASES MEASURE CHANGED, and the number will move with it. Through motionkb/v3 the
channel split was DERIVED from the KB's `role` labels, so `dc-walk-carry` scored a deterministic rule
and the model only had to name two actions. v4 deletes those labels (ADR 0022) and the split arrives
from the plan, so the same two cases now measure whether the MODEL partitions the body correctly.
That is a harder task and a more honest one -- it is the decision the system claims the agent makes --
but a decompose score before and after this change is not the same measurement.

`free_channels` in the ground truth carries two meanings across the two cases -- "given to nobody" in
dc-walk-carry, and "retargeted to IK" in dc-givepills-gaze. Both are still reproducible: the first is
what the plan leaves unnamed, the second is what `gaze_at` frees.

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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import paths                                                     # noqa: E402
from agent import assemble as A                                  # noqa: E402
from agent.kbindex import ANATOMICAL, KBIndex
# Only the constant at module scope: the baseline arm must not drag in the LLM stack to run.
from agent.llm import DEFAULT_MODEL

def load_cases():
    # The eval set is a build artifact, not knowledge: it says what retrieval SHOULD return, which is a
    # statement about this project's expectations rather than a fact about any motion (ADR 0017).
    paths.require_kb()
    # BUILT HERE, NOT READ FROM `paths`. This eval scores eight records that are no longer in the
    # knowledge base, so the live path table stopped naming its case file (see paths.py). The file
    # sits beside those records, in the Unity repository's own legacy directory.
    path = os.path.join(os.path.dirname(paths.KB_DIR), "legacy", "eval_8_actions",
                        "retrieval_eval_set.json")
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

    def plan_motion(character=None, base=None, overlays=None, base_channels=None, gaze_at=None,
                    **_ignored):
        """Stands in for the engine-backed tool: builds the same partition, commits nothing.

        `gaze_at` must exist here even though there is no engine: without it the model cannot express
        "give the pills while looking at the monitor" at all, and dc-givepills-gaze would be scoring a
        capability the tool surface does not offer rather than anything about the model.
        """
        from agent.tools import ToolFailure
        if base not in kb.actions:
            raise ToolFailure("unknown action_id: %s" % base)
        known = [o for o in (overlays or [])
                 if isinstance(o, dict) and o.get("action_id") in kb.actions]
        try:
            assembly = A.arbitrate(base, known, kb, base_channels=base_channels)
        except ValueError as e:
            raise ToolFailure(str(e))
        planned.append((assembly, gaze_at))
        return {"derived": assembly.as_dict(), "gaze_at": gaze_at}

    registry.add("plan_motion",
                 "Combine one base action with optional overlays and play it. YOU say which body "
                 "parts each overlay drives, and which the base reserves; anything nobody names comes "
                 "from the base. Use gaze_at to have the character look at something while the motion "
                 "plays -- the head is then solved by IK, not retrieved.",
                 {"type": "object", "additionalProperties": False,
                  "properties": {"character": {"type": "string"},
                                 "base": {"type": "string"},
                                 "base_channels": {"type": "array",
                                                   "items": {"type": "string", "enum": list(ANATOMICAL)}},
                                 "overlays": {
                                     "type": "array",
                                     "items": {"type": "object", "additionalProperties": False,
                                               "properties": {
                                                   "action_id": {"type": "string"},
                                                   "channels": {"type": "array", "minItems": 1,
                                                                "items": {"type": "string",
                                                                          "enum": list(ANATOMICAL)}}},
                                               "required": ["action_id", "channels"]}},
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
