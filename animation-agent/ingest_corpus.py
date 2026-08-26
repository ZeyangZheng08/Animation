#!/usr/bin/env python3
"""
ingest_corpus.py — bring a whole animation corpus into the MotionKB as KINEMATIC-only records.

WHY THIS IS NOT `extract.py`. The curated path adds ONE action at a time and ends in a semantic
proposal: `register <clip>` resolves a clip by name, `sample`/`assemble` walk the union of the
accepted and staged stores, `render`/`propose`/`author` label it. Every one of those choices is right
for eight hand-picked nursing actions and wrong for 2446 Mixamo clips:

  * `register` calls `build_find_clip_csharp`, which loads every asset under Assets/Animations and
    scans it for one name. Per clip that is fine; 2446 times it is 2446 full sweeps of a directory
    that now holds 2446 FBX. Here one call enumerates the whole directory and Python does the rest.
  * `assemble` re-measures every record in the store, including the eight already accepted. Here the
    population is the index, and nothing else.
  * neither has a resume rule, and a 70-minute engine run needs one.

WHAT LANDS. KINEMATIC and nothing else: the 9-channel block from metrics.py, plus duration/frame_rate/
loop and the source_clip triple, in `actions/<clip_name>.json` with `status: candidate` and the
SEMANTIC half seeded null. That is a complete record of what the clip DOES, and no claim about what it
MEANS. Labelling is a separate pass (ADR 0008) and is deliberately not run from here.

KINEMATIC IS COMPUTED BY THE SAME CODE AS THE CURATED PATH — this module imports
extract._apply_kinematic and extract._build_extraction rather than reimplementing them, so bulk and
curated records cannot drift into two dialects of the same contract.

    python3 ingest_corpus.py index      # 1 engine call: enumerate the corpus -> motionkb_build/reports/corpus_index.tsv
    python3 ingest_corpus.py register   # pure Python: one actions/<clip>.json stub per indexed clip
    python3 ingest_corpus.py sample     # N engine calls, one per clip, resumable -> raw/<clip>.json
    python3 ingest_corpus.py measure    # pure Python: raw -> the KINEMATIC block of each stub
    python3 ingest_corpus.py status     # where the funnel stands

`sample` is the only slow verb and the only one that needs Unity open. It skips clips whose dump
already exists, so an interrupted run resumes by re-running it.
"""
import argparse
import datetime
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as C          # noqa: E402
import extract              # noqa: E402
import metrics              # noqa: E402
import paths                # noqa: E402
import unity_sampler        # noqa: E402

DEFAULT_DIR = "Assets/Animations/Mixamo30"
INDEX = os.path.join(paths.REPORTS_DIR, "corpus_index.tsv")
FAILED = os.path.join(paths.REPORTS_DIR, "corpus_sample_failures.txt")
REPORT = os.path.join(paths.REPORTS_DIR, "corpus_ingest.md")
COLUMNS = ("clip_name", "asset_path", "guid", "file_id", "length", "frame_rate", "loop")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------------------- index
def build_index_csharp(scan_dir):
    """C# that returns one line per AnimationClip under `scan_dir`, with everything the rest of the
    pipeline needs from the engine about a clip it has not sampled yet.

    The per-clip `build_find_clip_csharp` answers "where is the clip called X"; this answers "what is
    in this directory", which is the question a corpus poses. LoadAllAssetsAtPath is the expensive
    part and it runs once per FBX either way — the difference is that here it runs once per FBX in
    total instead of once per FBX per clip.

    __preview__ clips are Unity's transient previewing artifacts, not assets; they are skipped so a
    stray editor preview cannot enter the corpus as a phantom action.
    """
    return r'''
var DIRS = new string[]{"%s"};
var paths = new System.Collections.Generic.HashSet<string>();
foreach (var g in UnityEditor.AssetDatabase.FindAssets("t:GameObject", DIRS)) { var p=UnityEditor.AssetDatabase.GUIDToAssetPath(g); if(p.ToLower().EndsWith(".fbx")) paths.Add(p); }
foreach (var g in UnityEditor.AssetDatabase.FindAssets("t:AnimationClip", DIRS)) paths.Add(UnityEditor.AssetDatabase.GUIDToAssetPath(g));
var sb = new System.Text.StringBuilder();
foreach (var path in paths) {
  foreach (var o in UnityEditor.AssetDatabase.LoadAllAssetsAtPath(path)) {
    var ac = o as UnityEngine.AnimationClip; if (ac==null) continue;
    if (ac.name.StartsWith("__preview__")) continue;
    string cg; long lid; UnityEditor.AssetDatabase.TryGetGUIDAndLocalFileIdentifier(ac, out cg, out lid);
    sb.AppendLine(ac.name+"\t"+path+"\t"+cg+"\t"+lid+"\t"+ac.length.ToString("R")+"\t"+ac.frameRate.ToString("R")+"\t"+(ac.isLooping?"true":"false"));
  }
}
return sb.ToString();
''' % scan_dir


def read_index():
    if not os.path.exists(INDEX):
        raise SystemExit("No corpus index at %s — run `ingest_corpus.py index` first." % paths.rel(INDEX))
    rows = []
    with open(INDEX, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#") or line.startswith(COLUMNS[0] + "\t"):
                continue
            parts = line.split("\t")
            if len(parts) != len(COLUMNS):
                raise SystemExit("Malformed index row (%d fields, expected %d): %r"
                                 % (len(parts), len(COLUMNS), line[:120]))
            r = dict(zip(COLUMNS, parts))
            r["file_id"] = int(r["file_id"])
            r["length"] = float(r["length"])
            r["frame_rate"] = float(r["frame_rate"])
            r["loop"] = r["loop"] == "true"
            rows.append(r)
    return rows


def cmd_index(args):
    if not unity_sampler.bridge_healthy(args.host, args.port):
        print("Unity MCP bridge not reachable at %s:%d (open Unity + start the MCP HTTP server)."
              % (args.host, args.port))
        return 1
    t0 = time.time()
    ok, text, _ = unity_sampler.run_csharp_over_http(
        build_index_csharp(args.dir), host=args.host, port=args.port, instance=args.instance)
    unity_sampler.close_connections()
    if not ok or (text or "").startswith("ERROR:"):
        print("Unity error:", (text or "")[:400])
        return 1

    rows, dupes = [], {}
    for line in (text or "").splitlines():
        if "\t" not in line:
            continue
        parts = line.rstrip("\r").split("\t")
        if len(parts) != len(COLUMNS):
            print("  skipped malformed row: %r" % line[:120]); continue
        dupes.setdefault(parts[0], []).append(parts[1])
        rows.append(parts)
    rows.sort(key=lambda r: r[0])

    # A clip name is this pipeline's key -- raw/<clip>.json, actions/<clip>.json, and every later
    # `register`/`propose` lookup are all keyed by it. Two clips sharing one would silently overwrite
    # each other's dump, so this refuses rather than picking a winner.
    collided = {n: p for n, p in dupes.items() if len(p) > 1}
    if collided:
        print("REFUSING: %d clip name(s) are not unique in %s — the pipeline keys everything by clip "
              "name, so these would overwrite each other:" % (len(collided), args.dir))
        for n, p in sorted(collided.items())[:20]:
            print("   %s\n     %s" % (n, "\n     ".join(p)))
        return 1

    body = ["# corpus index — %s — %s — %d clips" % (args.dir, _now(), len(rows)),
            "\t".join(COLUMNS)]
    body += ["\t".join(r) for r in rows]
    paths.write_text(INDEX, "\n".join(body) + "\n")
    print("indexed %d clip(s) from %s in %.1fs -> %s" % (len(rows), args.dir, time.time() - t0, paths.rel(INDEX)))
    return 0


# ------------------------------------------------------------------------------------ register
def _known_clip_names():
    """clip_name -> record path, for everything already in the store. Registering over one of these
    would fork a record: two files claiming one clip. A record is not always named after its clip
    (an accepted one is named after its action_id), so this reads the records rather than the names."""
    return paths.records_by_clip_name()


def cmd_register(args):
    rows = read_index()
    known = _known_clip_names()
    made = skipped = 0
    for r in rows:
        name = r["clip_name"]
        if name in known:
            skipped += 1
            continue
        stub = extract._new_source_stub(name, os.path.basename(r["asset_path"]), r["guid"], r["file_id"])
        # Engine facts that the index already carries. duration/frame_rate are overwritten from the
        # dump in `measure` (the dump is the authority on what was actually sampled); they are filled
        # here so a registered-but-unsampled record still says how long its clip is. `loop` has no
        # other source -- it is an importer setting, not something a pose dump can show.
        stub["duration"] = round(r["length"], 3)
        stub["frame_rate"] = r["frame_rate"]
        stub["loop"] = r["loop"]
        paths.write_json(os.path.join(paths.ACTIONS_DIR, name + ".json"), stub)
        made += 1
    print("registered %d new stub(s); %d already in the store; %d indexed"
          % (made, skipped, len(rows)))
    return 0


# -------------------------------------------------------------------------------------- sample
def _needs_sampling(rows, force):
    out = []
    for r in rows:
        if force or not os.path.exists(unity_sampler.raw_path(r["clip_name"])):
            out.append(r)
    return out


def cmd_sample(args):
    rows = read_index()
    if args.retry_failed:
        if not os.path.exists(FAILED):
            print("No failure list at %s — nothing to retry." % paths.rel(FAILED)); return 0
        want = {ln.strip() for ln in open(FAILED, encoding="utf-8") if ln.strip() and not ln.startswith("#")}
        rows = [r for r in rows if r["clip_name"] in want]
    todo = _needs_sampling(rows, args.force)
    if args.limit:
        todo = todo[:args.limit]
    if not todo:
        print("Nothing to sample: all %d indexed clip(s) already have a dump in %s."
              % (len(rows), paths.rel(paths.RAW_DIR)))
        return 0
    if not unity_sampler.bridge_healthy(args.host, args.port):
        print("Unity MCP bridge not reachable at %s:%d." % (args.host, args.port)); return 1

    print("Sampling %d clip(s) (%d indexed, %d already done) via %s:%d ..."
          % (len(todo), len(rows), len(rows) - len(_needs_sampling(rows, False)), args.host, args.port))
    t0, failed, done_bytes = time.time(), [], 0
    for i, r in enumerate(todo, 1):
        name = r["clip_name"]
        cs = unity_sampler.build_sampler_csharp({"id": name, "guid": r["guid"], "file_id": r["file_id"]})
        ok, text, _ = unity_sampler.run_csharp_over_http(
            cs, host=args.host, port=args.port, instance=args.instance)
        if not ok or (text or "").startswith("ERROR:"):
            print("  FAIL  %-60s %s" % (name[:60], (text or "").strip()[:120]))
            failed.append(name)
        else:
            try:
                out, dump = unity_sampler.write_raw(name, text)
                done_bytes += len(text)
            except ValueError as e:
                print("  FAIL  %-60s unparseable dump (%s)" % (name[:60], e))
                failed.append(name)
        if i % 25 == 0 or i == len(todo):
            el = time.time() - t0
            eta = el / i * (len(todo) - i)
            print("  %5d/%d  %5.1f%%  elapsed %5.1fm  eta %5.1fm  written %.2f GB"
                  % (i, len(todo), 100.0 * i / len(todo), el / 60, eta / 60, done_bytes / 1e9))
            sys.stdout.flush()
    unity_sampler.close_connections()

    # The list is written only when there is something in it, and removed when there is not. An
    # always-present file carrying a run timestamp would show as modified after every sample run,
    # which is what the KB spends real effort avoiding -- `git status` is its drift detector. This way
    # the file's EXISTENCE is the signal: it is there iff the last run left clips unsampled.
    if failed:
        paths.write_text(FAILED, "# clips whose sampler call failed — %s\n" % _now()
                         + "".join(n + "\n" for n in failed))
    elif os.path.exists(FAILED):
        os.remove(FAILED)
    print("\n%d sampled / %d failed in %.1f min%s"
          % (len(todo) - len(failed), len(failed), (time.time() - t0) / 60,
             ("  (%s — rerun with --retry-failed)" % paths.rel(FAILED)) if failed else ""))
    return 1 if failed else 0


# ------------------------------------------------------------------------------------- measure
def cmd_measure(args):
    rows = read_index()
    known = _known_clip_names()
    ok, missing, errors = 0, [], []
    two_frame, all_static, looping = [], [], 0
    for r in rows:
        name = r["clip_name"]
        cand = known.get(name)
        if not cand:
            errors.append((name, "no record in the store — run `register`")); continue
        if not os.path.exists(unity_sampler.raw_path(name)):
            missing.append(name); continue
        try:
            raw = unity_sampler.read_raw(name)
            doc = paths.read_json(cand)
            doc["duration"] = round(raw["length"], 3)
            doc["frame_rate"] = raw["frame_rate"]
            extract._apply_kinematic(doc, metrics.channel_blocks(raw))
            doc["extraction"] = extract._build_extraction(raw)
            paths.write_json(cand, doc)
            # Two counts, not one. Mixamo ships *pose* assets that resolve to 2 frames, and they are
            # kept rather than dropped -- a pose is a legitimate thing to retrieve -- but counted,
            # because "records where nothing moves" is a fact about the corpus a reader should not
            # have to rediscover.
            #
            # They are NOT the same set, which is why an earlier version of this report was wrong
            # about one clip. `mx_Arms_Supporting` is 2 frames whose poses DIFFER, so its right leg
            # measures 0.0638 and dynamic; and 29 clips are all-static at full length, moving less
            # than the 0.02 threshold across every channel. Short is not the same as still.
            if raw["frames"] <= 2:
                two_frame.append(name)
            if all(doc["channels"][c]["state_label"] == "static" for c in C.STATE_CHANNELS):
                all_static.append(name)
            if doc.get("loop"):
                looping += 1
            ok += 1
        except Exception as e:                       # per-file isolation, as in extract.assemble
            errors.append((name, "%s: %s" % (type(e).__name__, e)))

    lines = ["# corpus ingest — KINEMATIC — %s" % _now(), "",
             "Source: `%s` (%d clips indexed)." % (INDEX and paths.rel(INDEX), len(rows)),
             "Records are `status: candidate` with the SEMANTIC half seeded null: this pass measures "
             "what each clip does and claims nothing about what it means (ADR 0002 / ADR 0014).", "",
             "| | count |", "|---|---|",
             "| measured | %d |" % ok,
             "| awaiting a dump (`sample`) | %d |" % len(missing),
             "| errors | %d |" % len(errors),
             "| sampled at 2 frames (Mixamo pose assets) | %d |" % len(two_frame),
             "| every channel static | %d |" % len(all_static),
             "| declared looping | %d |" % looping]
    if not looping and ok:
        lines += ["",
                  "**Not one clip is declared looping.** `loop` is an importer setting "
                  "(`ModelImporterClipAnimation.loopTime`), not something a pose dump can show, and "
                  "the corpus was imported without it set — so the field reports, accurately, that "
                  "nobody has declared these clips loopable. It is not a measurement that they are "
                  "not: plenty of them plainly cycle. Anything that reads `loop` (seam search does) "
                  "will treat every corpus clip as one-shot until that is decided."]
    if errors:
        lines += ["", "## errors", ""] + ["- `%s` — %s" % e for e in errors[:200]]
    if two_frame:
        lines += ["", "## sampled at 2 frames", "",
                  "Kept, not dropped — a pose is a legitimate thing to retrieve. Note that 2 frames "
                  "does not imply stillness: a clip whose two poses differ measures as moving.", "",
                  ", ".join("`%s`" % n for n in sorted(two_frame))]
    paths.write_text(REPORT, "\n".join(lines) + "\n")
    print("\n".join(lines[:14]))
    print("\n-> %s" % paths.rel(REPORT))
    return 1 if errors else 0


# -------------------------------------------------------------------------------------- status
def cmd_status(args):
    rows = read_index() if os.path.exists(INDEX) else []
    have_raw = sum(1 for r in rows if os.path.exists(unity_sampler.raw_path(r["clip_name"])))
    known = _known_clip_names()
    registered = [known[r["clip_name"]] for r in rows if r["clip_name"] in known]
    measured = 0
    for p, doc, err in paths.read_records(registered):
        if err:
            continue
        if (doc.get("channels", {}).get(C.ROOT) or {}).get("motion_magnitude") is not None:
            measured += 1
    print("corpus index      %5d   %s" % (len(rows), paths.rel(INDEX)))
    print("registered        %5d   %s" % (len(registered), paths.rel(paths.ACTIONS_DIR)))
    print("pose dumps        %5d   %s" % (have_raw, paths.rel(paths.RAW_DIR)))
    print("KINEMATIC filled  %5d" % measured)
    print("store total       %5d   (%d accepted, per %s)"
          % (len(paths.action_files()), len(paths.accepted_files()), paths.rel(paths.MANIFEST)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def bridge(p):
        p.add_argument("--host", default=unity_sampler.DEFAULT_HOST)
        p.add_argument("--port", type=int, default=unity_sampler.DEFAULT_PORT)
        p.add_argument("--instance")
        return p

    p = bridge(sub.add_parser("index", help="enumerate the corpus in one engine call"))
    p.add_argument("--dir", default=DEFAULT_DIR, help="project-relative asset folder (default: %(default)s)")
    p.set_defaults(fn=cmd_index)

    p = sub.add_parser("register", help="write one candidate stub per indexed clip (no engine)")
    p.set_defaults(fn=cmd_register)

    p = bridge(sub.add_parser("sample", help="sample each indexed clip; resumable"))
    p.add_argument("--limit", type=int, help="stop after N clips (for a pilot run)")
    p.add_argument("--force", action="store_true", help="re-sample clips that already have a dump")
    p.add_argument("--retry-failed", action="store_true", help="only the clips in the failure list")
    p.set_defaults(fn=cmd_sample)

    p = sub.add_parser("measure", help="compute KINEMATIC from the dumps (no engine)")
    p.set_defaults(fn=cmd_measure)

    p = sub.add_parser("status", help="where the funnel stands")
    p.set_defaults(fn=cmd_status)

    args = ap.parse_args(argv)
    paths.require_kb()
    os.makedirs(paths.ACTIONS_DIR, exist_ok=True)
    os.makedirs(paths.REPORTS_DIR, exist_ok=True)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
