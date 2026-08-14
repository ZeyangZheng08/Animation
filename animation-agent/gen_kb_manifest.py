#!/usr/bin/env python3
"""
gen_kb_manifest.py — generate agent/kb/kb_manifest.json (HANDOFF.md §8 module C).

A single discoverable index of the accepted store: per-action IDENTITY (action_id, file, source_clip)
+ PROVENANCE (extractor/formula/bone-map version, extracted_at, verification, vlm-proposal status). It
is for the Phase-2 RAG to enumerate the corpus and trust its provenance from one file.

Deliberately NOT a content ledger: no per-entry content_sha256, no promotion log — git is the version /
ledger / rollback (ADR 0005), and the commit is the timestamp, so the manifest carries no generated_at
and is fully derived from the store (regenerating is idempotent — `git diff` shows only real changes).
Stdlib only, no Unity.

Usage:
  python gen_kb_manifest.py            # (re)generate the KB's kb_manifest.json
  python gen_kb_manifest.py --check    # fail (exit 1) if the on-disk manifest is stale
"""
import sys, os, glob, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths                                                     # noqa: E402

KB_DIR = paths.KB_DIR                                            # see paths.py / MOTIONKB_DIR
MANIFEST = os.path.join(KB_DIR, "kb_manifest.json")
NON_ACTION_FILES = {"engine_mask_map.json", "kb_manifest.json", "retrieval_eval_set.json"}


def _accepted_files():
    return [f for f in sorted(glob.glob(os.path.join(KB_DIR, "*.json")))
            if os.path.basename(f) not in NON_ACTION_FILES]


def build_manifest():
    actions = []
    for f in _accepted_files():
        d = json.load(open(f, encoding="utf-8"))
        ex = d.get("extraction", {})
        comp = d.get("composability", {})
        va = ex.get("vlm_proposal") or {}
        actions.append({
            "action_id": d.get("action_id"),
            "file": os.path.basename(f),
            "status": d.get("status"),
            "display_name": d.get("display_name"),
            "base_or_overlay": comp.get("base_or_overlay"),
            "posture": comp.get("posture"),
            "source_clip": d.get("source_clip"),
            "provenance": {
                "extractor_version": ex.get("extractor_version"),
                "metric_formula_version": ex.get("metric_formula_version"),
                "bone_map_version": ex.get("bone_map_version"),
                "extracted_at": ex.get("extracted_at"),
                "verified_against_screenshots": ex.get("verified_against_screenshots"),
                "verified_at": ex.get("verified_at"),
                "vlm_proposal_status": va.get("status"),
            },
        })
    return {
        "kb_version": "v2",
        "schema_version": "motionkb/v2",
        "schema": "schema/motionkb.v2.schema.json",
        "engine_mask_map": "engine_mask_map.json",
        "rollback_tag": "kb/v2",
        "generator": "animation-agent/gen_kb_manifest.py",
        "action_count": len(actions),
        "actions": actions,
    }


def _serialize(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def main(argv):
    paths.require_kb()
    manifest = build_manifest()
    text = _serialize(manifest)
    if "--check" in argv[1:]:
        if not os.path.exists(MANIFEST):
            print("STALE: kb_manifest.json does not exist (run gen_kb_manifest.py)"); return 1
        # universal newlines on read -> the comparison is about content, not line endings
        on_disk = open(MANIFEST, encoding="utf-8").read()
        if on_disk != text:
            print("STALE: kb_manifest.json is out of date (regenerate via gen_kb_manifest.py)"); return 1
        print(f"kb_manifest.json up to date ({manifest['action_count']} actions)"); return 0
    paths.write_text(MANIFEST, text)
    print(f"wrote {paths.rel(MANIFEST)} ({manifest['action_count']} actions)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
