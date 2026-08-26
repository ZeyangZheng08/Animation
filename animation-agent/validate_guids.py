#!/usr/bin/env python3
"""
validate_guids.py — the Unity-dependent layer of the MotionKB data contract.

Every accepted action records a `source_clip` (guid + file_id + clip_name). Only the engine can say
whether that still resolves to a real AnimationClip: guids live in Unity `.meta` files and file_ids in
the FBX importer's sub-asset table, both of which move when an asset is reimported, renamed or replaced.
Everything else about the contract — JSON Schema, cross-field invariants, semantic consistency, golden
re-extraction — is checked with no Unity by the sibling scripts.

This replaces Assets/Editor/MotionKB/MotionKBValidator.cs. That C# Editor tool did the same resolution
from inside the Unity project; driving it from here over the MCP bridge leaves NO agent-side code in the
engine, which is the point of the split (ADR 0008 pattern: Python owns the knowledge, C# is generated
and disposable). Writes agent/motionkb_build/reports/kb_state.md, same report the Editor tool used to write.

Usage:  python validate_guids.py [--host H] [--port P] [--instance NAME]
Exit:   0 if every accepted action resolves; 1 on any failure or if the bridge is unreachable.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths                     # noqa: E402
import unity_sampler             # noqa: E402

REPORT = os.path.join(paths.REPORTS_DIR, "kb_state.md")


def _entries():
    """(key, guid, file_id, clip_name) for every accepted action, keyed by action_id."""
    out = []
    for path in paths.accepted_files():
        doc = paths.read_json(path)
        sc = doc.get("source_clip") or {}
        out.append({
            "key": doc.get("action_id") or os.path.basename(path),
            "guid": sc.get("guid") or "",
            "file_id": sc.get("file_id") or 0,
            "clip_name": sc.get("clip_name") or "",
            "schema": doc.get("schema_version") or "",
            "status": doc.get("status") or "",
        })
    return out


VERDICT_TEXT = {
    "OK":     "YES",
    "WARN":   "WARN (name match, file_id differs)",
    "NOPATH": "NO (guid unresolved)",
    "NOCLIP": "NO (clip not found at asset)",
}


def main(argv):
    paths.require_kb()
    host, port, instance = unity_sampler.DEFAULT_HOST, unity_sampler.DEFAULT_PORT, None
    i = 0
    while i < len(argv):
        if argv[i] == "--host" and i + 1 < len(argv):
            host = argv[i + 1]; i += 2
        elif argv[i] == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1]); i += 2
        elif argv[i] == "--instance" and i + 1 < len(argv):
            instance = argv[i + 1]; i += 2
        else:
            i += 1

    entries = _entries()
    if not entries:
        print("FATAL: manifest.json lists no accepted actions (store: %s)" % paths.ACTIONS_DIR)
        return 1
    missing = [e["key"] for e in entries if not e["guid"]]
    if missing:
        print("FATAL: no source_clip.guid on: %s" % ", ".join(missing))
        return 1

    if not unity_sampler.bridge_healthy(host, port):
        print("Unity MCP bridge not reachable at %s:%d.\n"
              "Open the Unity project and start the MCP server on HTTP (port %d) first." % (host, port, port))
        return 1

    print("guid -> AnimationClip resolution via %s:%d — %d accepted action(s)\n" % (host, port, len(entries)))
    cs = unity_sampler.build_validate_guids_csharp(entries)
    ok, result_text, _ = unity_sampler.run_csharp_over_http(cs, host=host, port=port, instance=instance)
    if not ok:
        print("Unity reported an error:\n%s" % result_text.strip())
        return 1

    got = {}
    for line in result_text.splitlines():
        parts = line.split("|")
        if len(parts) == 4:
            got[parts[0]] = (parts[1], parts[2], parts[3])

    rows = [
        "# MotionKB state - guid->asset resolution (engine-side layer)",
        "",
        "Resolves each accepted action's source_clip (guid + file_id) to a real AnimationClip.",
        "Driven from agent-side Python over the Unity MCP bridge; no agent code lives in the Unity project.",
        "Schema + cross-field invariants are checked with no Unity by validate_motionkb.py.",
        "",
        "| action | schema | status | clip resolved | clip_name | asset path |",
        "|---|---|---|---|---|---|",
    ]
    npass = nfail = nwarn = 0
    for e in entries:
        verdict, clip_name, asset = got.get(e["key"], ("MISSING", e["clip_name"], ""))
        if verdict == "OK":
            npass += 1
        elif verdict == "WARN":
            npass += 1; nwarn += 1
        else:
            nfail += 1
        rows.append("| %s | %s | %s | %s | %s | %s |"
                    % (e["key"], e["schema"], e["status"],
                       VERDICT_TEXT.get(verdict, "NO (%s)" % verdict), clip_name, asset))
        print("  %-6s %s -> %s" % (verdict, e["key"], asset or "(unresolved)"))

    rows += ["", "**%d resolved / %d failed / %d warning(s).**" % (npass, nfail, nwarn)]
    paths.write_text(REPORT, "\n".join(rows) + "\n")
    print("\n%d resolved / %d failed / %d warn -> %s" % (npass, nfail, nwarn, paths.rel(REPORT)))
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
