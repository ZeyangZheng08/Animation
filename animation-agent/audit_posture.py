#!/usr/bin/env python3
"""
audit_posture.py — a sanity audit of the posture rules against clips a human can label by eye.

WHAT THIS IS. `build_posture.py` decides four coarse states from fixed geometric rules. The rules
came from the literature; the THRESHOLDS in them are operational numbers, and an operational number
that nobody ever checks is an assumption wearing a decimal point. This reads the expectations in
`motionkb_build/posture_audit.json` — twenty Mixamo clips whose content is not in doubt — and reports,
per clip, what the sidecar says, what was expected, and where the two disagree.

WHAT THIS IS NOT. Not a training set, not an evaluation, not a benchmark. Twenty clips cannot measure
an accuracy and this script does not print one. It answers a narrower question: do the rules make the
mistakes their shape makes likely — a crouch or a kneel read as sitting, a deep bend read as lying —
and do they hold the invariant the segmentation claims (no segment shorter than the minimum posture
duration). Everything it checks is a category error, not a near miss.

WHAT TO DO WITH A MISMATCH. Read it before retuning anything. A mismatch says the rule and the
expectation disagree, and either can be the one that is wrong. If a threshold does change, bump
`POSTURE_ALGORITHM_VERSION` and rebuild the sidecar — the version is what lets a stored posture be
trusted, and moving a rule without moving the version is the one change that cannot be detected.

    python audit_posture.py             # audit every case
    python audit_posture.py -q          # mismatches and the summary only

Exit: 0 when every case matches, 1 otherwise. Stdlib only, no Unity.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths                                                     # noqa: E402
import build_posture as P                                        # noqa: E402

EXPECTATIONS = os.path.join(paths.BUILD_DIR, "posture_audit.json")


def _segments_text(entry):
    return " ".join("%s[%d-%d]" % (s["posture"], s["start_frame"], s["end_frame"])
                    for s in entry["posture_segments"])


def _expectation_text(expect):
    parts = []
    for key in ("dominant", "start", "end", "contains", "never"):
        if key in expect:
            parts.append("%s %s" % (key, "|".join(expect[key])))
    if "boundaries" in expect:
        b = expect["boundaries"]
        parts.append("%d boundary %s<->%s" % (b["count"], b["between"][0], b["between"][1]))
    return ", ".join(parts)


def check(entry, expect, min_frames):
    """[reason, ...] — one line per way this clip disagrees with what was expected. Empty is a pass."""
    bad = []
    present = [s["posture"] for s in entry["posture_segments"]]

    if "dominant" in expect and entry["dominant_posture"] not in expect["dominant"]:
        bad.append("dominant_posture is %s, expected %s"
                   % (entry["dominant_posture"], " or ".join(expect["dominant"])))
    if "start" in expect and entry["start_posture"] not in expect["start"]:
        bad.append("start_posture is %s, expected %s"
                   % (entry["start_posture"], " or ".join(expect["start"])))
    if "end" in expect and entry["end_posture"] not in expect["end"]:
        bad.append("end_posture is %s, expected %s"
                   % (entry["end_posture"], " or ".join(expect["end"])))
    for want in expect.get("contains", []):
        if want not in present:
            bad.append("no %s segment anywhere; the clip is expected to pass through it" % want)
    for forbidden in expect.get("never", []):
        if forbidden in present:
            frames = sum(s["end_frame"] - s["start_frame"] + 1
                         for s in entry["posture_segments"] if s["posture"] == forbidden)
            bad.append("%d frame(s) read as %s, which this clip must never be" % (frames, forbidden))
    if "boundaries" in expect:
        pair = set(expect["boundaries"]["between"])
        seen = [t for t in entry["posture_transitions"] if {t["from"], t["to"]} == pair]
        if len(seen) != expect["boundaries"]["count"]:
            bad.append("%d %s<->%s boundary(ies) at frame(s) %s, expected %d"
                       % (len(seen), expect["boundaries"]["between"][0],
                          expect["boundaries"]["between"][1],
                          ", ".join(str(t["at_frame"]) for t in seen) or "-",
                          expect["boundaries"]["count"]))

    # ALWAYS CHECKED, for every case. The segmentation claims no run survives that is shorter than
    # the minimum posture duration; a clip whose only segment is shorter than that is a Mixamo pose
    # with nowhere longer to go, and is exempt.
    if len(entry["posture_segments"]) > 1:
        for s in entry["posture_segments"]:
            n = s["end_frame"] - s["start_frame"] + 1
            if n < min_frames:
                bad.append("segment %s[%d-%d] is %d frame(s), under min_frames=%d"
                           % (s["posture"], s["start_frame"], s["end_frame"], n, min_frames))
    return bad


def _frame_rates(action_ids):
    """{action_id: frame_rate} — `min_frames` is per clip, and the sidecar keeps a segmentation
    rather than the rate it was cut at, so this reads the records."""
    want = set(action_ids)
    out = {}
    for p, doc, err in paths.read_records(paths.accepted_files()):
        if err or doc.get("action_id") not in want:
            continue
        out[doc["action_id"]] = doc.get("frame_rate") or 30
    return out


def main(argv):
    quiet = "-q" in argv or "--quiet" in argv
    paths.require_kb()
    if not os.path.exists(EXPECTATIONS):
        print("FATAL: no expectations at %s" % paths.rel(EXPECTATIONS))
        return 1
    doc = paths.read_json(EXPECTATIONS)
    cases = doc.get("cases") or []
    sidecar = P.read_sidecar()

    stated = (doc.get("_meta") or {}).get("posture_algorithm_version")
    if stated != P.POSTURE_ALGORITHM_VERSION:
        print("NOTE: expectations were written against posture algorithm %s; this is %s.\n"
              "      A rule changed, so the expectations may need to change with it.\n"
              % (stated, P.POSTURE_ALGORITHM_VERSION))

    rates = _frame_rates(c["clip"] for c in cases)
    print("posture rule sanity audit — algorithm %s, %d case(s)\n"
          % (P.POSTURE_ALGORITHM_VERSION, len(cases)))

    mismatches = []
    missing = []
    category = None
    for c in cases:
        clip, expect = c["clip"], c["expect"]
        entry = sidecar.get(clip)
        if entry is None:
            missing.append(clip)
            print("  MISS  %s — not in the sidecar" % clip)
            continue
        min_frames = P.min_frames_for(rates.get(clip, 30))
        bad = check(entry, expect, min_frames)
        if bad:
            mismatches.append((clip, c.get("category"), bad))
        if quiet and not bad:
            continue
        if c.get("category") != category:
            category = c.get("category")
            print("  -- %s" % category)
        print("  %s  %s" % ("FAIL" if bad else "ok  ", clip))
        print("          predicted: %s" % _segments_text(entry))
        print("          expected : %s   (%s)" % (_expectation_text(expect), c.get("why", "")))
        for line in bad:
            print("          - %s" % line)

    print("\n%d matched / %d mismatched / %d missing, out of %d case(s)"
          % (len(cases) - len(mismatches) - len(missing), len(mismatches), len(missing), len(cases)))
    if mismatches:
        print("\nmismatch summary:")
        for clip, cat, bad in mismatches:
            print("  %-48s %-22s %s" % (clip, cat, bad[0]))
            for line in bad[1:]:
                print("  %-48s %-22s %s" % ("", "", line))
        print("\nRead these before changing a threshold. If a rule does change, bump "
              "POSTURE_ALGORITHM_VERSION\nand rerun build_posture.py.")
    return 1 if (mismatches or missing) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
