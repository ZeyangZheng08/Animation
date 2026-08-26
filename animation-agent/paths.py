"""
paths.py — where the MotionKB lives, and the rules for writing to it.

The KB is NOT in this repository. It is a derivative of the Unity project's animation assets: its `raw`
dumps come from in-engine `AnimationMode` sampling, its `frames` from in-engine rendering, and it grows
only when a new clip is imported into Unity. So it is versioned alongside those assets, in the Unity
repository, and this repository reaches it through configured paths.

    MOTIONKB_DIR         absolute path to the knowledge base (default below)
    MOTIONKB_BUILD_DIR   absolute path to the build artifacts (default: a sibling of the KB)

TWO DIRECTORIES, split by who reads them (ADR 0017). `animation_knowledge_base/` is everything a
CONSUMER reads — the records, the frozen evidence they were derived from, the tables derived from
them, the index and the contract. `motionkb_build/` is everything that exists only because the KB was
built and verified: run reports, the corpus enumeration, superseded artifacts kept for audit. Nothing
at runtime reads the second, and the agent's search workspace does not mount it.

This module is the ONLY place that knows those paths, and the only sanctioned way to write into them.

WRITE DISCIPLINE — the KB lives on a Windows git worktree reached over DrvFs (`/mnt/...`). git runs on
the Windows side only. Every write here is therefore UTF-8 without BOM and LF-terminated, regardless of
platform, so a file written from Linux is byte-identical to one written from Windows. Use `write_text`
/ `write_json` / `write_bytes` — never a bare `open(..., "w")`, whose newline translation is platform
dependent.
"""
import glob
import json
import os
from concurrent.futures import ThreadPoolExecutor

DEFAULT_KB_DIR = ("/mnt/f/Research/AI_agent/Animation/Animation_agent/Project/Animation"
                  "/agent/animation_knowledge_base")

KB_DIR      = os.path.abspath(os.environ.get("MOTIONKB_DIR", DEFAULT_KB_DIR))
# Every action record, one file per action, and NOTHING else -- whatever its status. Its own directory
# because a flat KB root made every consumer carry the same denylist of the shared JSONs sitting beside
# them ({engine_mask_map, kb_manifest, retrieval_eval_set}.json) -- seven copies under two names, which
# is what a layout costs when membership has to be asserted instead of read off the path (ADR 0012).
#
# There used to be a second store, candidate/, and the path repeated what `status` already said. That
# cost two places to look for one clip, a window during promotion when one clip had a file in each, and
# a directory choice at every call site that meant a status. ADR 0016 merged them: a record is named by
# its key -- <action_id>.json once it has an action_id, <clip_name>.json until then -- so promotion is a
# rename inside this directory rather than a move between two.
ACTIONS_DIR = os.path.join(KB_DIR, "actions")
# Frozen evidence: `raw` is the per-frame pose dump every KINEMATIC number is computed from, `frames`
# the rendered pictures every SEMANTIC label was read off. Both are inputs to the build AND data the
# runtime reads — kb_pose indexes a dump, the seam and segment tables are derived from them — which is
# why they are in the knowledge base and not among the build artifacts (ADR 0017).
RAW_DIR     = os.path.join(KB_DIR, "raw")
FRAMES_DIR  = os.path.join(KB_DIR, "frames")
DERIVED_DIR = os.path.join(KB_DIR, "derived")
SCHEMA_DIR  = os.path.join(KB_DIR, "schema")
MANIFEST    = os.path.join(KB_DIR, "manifest.json")
ENGINE_MASK_MAP = os.path.join(KB_DIR, "engine_mask_map.json")

# Build artifacts. Default is a sibling of the KB, so moving the KB moves both; MOTIONKB_BUILD_DIR
# overrides. Nothing under here is read at runtime and nothing under here is mounted into the agent's
# search workspace — a report about how the KB was built is not a fact about motion, and a superseded
# record is the one thing a search must never return.
BUILD_DIR   = os.path.abspath(os.environ.get(
    "MOTIONKB_BUILD_DIR", os.path.join(os.path.dirname(KB_DIR), "motionkb_build")))
REPORTS_DIR = os.path.join(BUILD_DIR, "reports")
ARCHIVE_DIR = os.path.join(BUILD_DIR, "archive")
EVAL_SET    = os.path.join(BUILD_DIR, "retrieval_eval_set.json")

# The KB is reached over DrvFs from WSL, where opening a file costs about 28 ms whatever its size. The
# store is 26 MB in 2454 files, so reading it one at a time takes 68 s; 32 threads bring that to 6 s and
# more threads do not help. Anything that walks the whole store goes through read_records().
READ_WORKERS = 32


def require_kb():
    """Fail loudly and actionably if the KB is not where we think it is. Called by every entry point:
    a silently-empty KB would make the gates report '0 files, 0 failures' — a false green."""
    if not os.path.isdir(KB_DIR):
        raise SystemExit(
            "MotionKB not found at %s\n"
            "The KB lives in the Unity repository, not here. Set MOTIONKB_DIR to its 'agent/animation_knowledge_base'\n"
            "directory (from WSL that is a /mnt/<drive>/... path) and make sure the drive is mounted."
            % KB_DIR)
    return KB_DIR


def action_files(d=None):
    """Every action record in `d`, sorted; the whole store by default, accepted or not.

    Membership is the DIRECTORY, not a denylist. While the records sat loose in the KB root beside
    engine_mask_map.json / manifest.json / retrieval_eval_set.json, every consumer that walked the
    store had to name those three in order to skip them, and that set was pasted into seven files
    under two different names. A fourth shared file would have had to be added to all seven, or be
    silently validated as an action record. actions/ holds action records and nothing else, so there
    is nothing to exclude and no list to keep in sync.

    Takes a directory so a test can point it at a temporary store."""
    return sorted(glob.glob(os.path.join(d or ACTIONS_DIR, "*.json")))


def read_records(files=None, workers=READ_WORKERS):
    """[(path, record, error)] for every file, opened concurrently, in path order.

    `error` is None on success and a message otherwise, with `record` None -- one unreadable file in a
    2454-file store should be reported against that file, not raised over the batch. Callers that
    cannot proceed past a bad record still have to say so; this only declines to decide for them.

    Concurrency is not an optimisation here, it is what makes walking the store usable at all: see
    READ_WORKERS above.
    """
    files = action_files() if files is None else list(files)

    def one(p):
        try:
            with open(p, encoding="utf-8") as f:
                return (p, json.load(f), None)
        except Exception as e:
            return (p, None, str(e))

    if len(files) < 2:
        return [one(p) for p in files]
    with ThreadPoolExecutor(min(workers, len(files))) as pool:
        return list(pool.map(one, files))


def accepted_files(actions_dir=None):
    """The accepted records of a store, from its manifest.json.

    One store and `status` as the membership test means selecting the accepted subset is a question
    about 2454 records' contents. read_records() answers it in 6 s, which is fine for a gate and far
    too slow for something the agent does at every start. The manifest indexes exactly this subset
    already, and `gen_kb_manifest.py --check` is gate 3 of check_kb.sh, so it is read as the index it
    is rather than rebuilt on the spot.

    The manifest is found NEXT TO the store rather than at a fixed path, so redirecting the store
    redirects its index with it -- a test that points ACTIONS_DIR at a temporary directory gets that
    directory's answer and cannot reach the real one.

    NO manifest means no index yet, so the store is read and filtered: correct, just slow. A manifest
    that NAMES A FILE THE STORE DOES NOT HOLD raises instead, because that is the one direction of
    staleness nothing else catches -- the gate rebuilds the manifest and compares, so it sees a record
    that stopped being accepted, while a caller that quietly skipped a missing file would just
    retrieve less and never say so.
    """
    d = actions_dir or ACTIONS_DIR
    manifest_path = os.path.join(os.path.dirname(d), "manifest.json")
    if not os.path.exists(manifest_path):
        return sorted(p for p, doc, err in read_records(action_files(d))
                      if not err and doc.get("status") == "accepted")
    out = []
    for entry in read_json(manifest_path).get("actions", []):
        p = os.path.join(d, entry["file"])
        if not os.path.isfile(p):
            raise SystemExit(
                "manifest.json names %s, which is not in %s -- the manifest is stale.\n"
                "Regenerate it:  python gen_kb_manifest.py" % (entry["file"], rel(d)))
        out.append(p)
    return sorted(out)


def records_by_clip_name(files=None):
    """{clip_name: path} across the whole store -- the key the MEASURE half works in.

    A record is addressed by action_id once it has one, but raw dumps, frames directories and the
    sampler are all keyed by clip_name, so the pipeline needs the reverse lookup. Records that do not
    parse, or carry no clip_name, are left out; a caller that needs to know they existed should walk
    read_records() itself.
    """
    out = {}
    for p, doc, err in read_records(files):
        if err or not isinstance(doc, dict):
            continue
        cn = (doc.get("source_clip") or {}).get("clip_name")
        if cn:
            out[cn] = p
    return out


def rel(path):
    """A readable path for a log line: relative to the KB root, or to the directory holding both the KB
    and the build artifacts when it is one of those. Anything else is returned as it came."""
    for base in (KB_DIR, os.path.dirname(KB_DIR)):
        try:
            r = os.path.relpath(path, base)
        except ValueError:
            continue                                   # different drive; try the next base
        if not r.startswith(".."):
            return r
    return path


def _atomic(path, data, binary):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    if binary:
        with open(tmp, "wb") as f:
            f.write(data)
    else:
        # newline="" -> no translation; we emit the LF ourselves. encoding="utf-8" -> no BOM.
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(data)
    os.replace(tmp, path)
    return path


def write_text(path, text):
    """Atomically write text as UTF-8/LF. Any CRLF in `text` is normalized."""
    return _atomic(path, text.replace("\r\n", "\n").replace("\r", "\n"), binary=False)


def write_json(path, obj):
    """Atomically write pretty JSON as UTF-8/LF with a trailing newline (the KB's committed shape)."""
    return write_text(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def write_bytes(path, data):
    """Atomically write binary (render frames). No newline handling — bytes are bytes."""
    return _atomic(path, data, binary=True)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)
