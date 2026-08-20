"""
paths.py — where the MotionKB lives, and the rules for writing to it.

The KB is NOT in this repository. It is a derivative of the Unity project's animation assets: its
`_raw` dumps come from in-engine `AnimationMode` sampling, its `_frames` from in-engine rendering, and
it grows only when a new clip is imported into Unity. So it is versioned alongside those assets, in the
Unity repository, and this repository reaches it through one configured path.

    MOTIONKB_DIR   absolute path to the KB directory (default below)

This module is the ONLY place that knows that path, and the only sanctioned way to write into it.

WRITE DISCIPLINE — the KB lives on a Windows git worktree reached over DrvFs (`/mnt/...`). git runs on
the Windows side only. Every write here is therefore UTF-8 without BOM and LF-terminated, regardless of
platform, so a file written from Linux is byte-identical to one written from Windows. Use `write_text`
/ `write_json` / `write_bytes` — never a bare `open(..., "w")`, whose newline translation is platform
dependent.
"""
import glob
import json
import os

DEFAULT_KB_DIR = "/mnt/f/Research/AI_agent/Animation/Animation_agent/Project/Animation/agent/kb"

KB_DIR      = os.path.abspath(os.environ.get("MOTIONKB_DIR", DEFAULT_KB_DIR))
# The accepted action records, one file per action, and NOTHING else. Their own directory because a
# flat KB root made every consumer carry the same denylist of the shared JSONs sitting beside them
# ({engine_mask_map, kb_manifest, retrieval_eval_set}.json) -- seven copies under two names, which is
# what a layout costs when membership has to be asserted instead of read off the path. See ADR 0012.
ACTIONS_DIR = os.path.join(KB_DIR, "actions")
RAW_DIR     = os.path.join(KB_DIR, "_raw")
FRAMES_DIR  = os.path.join(KB_DIR, "_frames")
REPORTS_DIR = os.path.join(KB_DIR, "_reports")
CAND_DIR    = os.path.join(KB_DIR, "candidate")
SCHEMA_DIR  = os.path.join(KB_DIR, "schema")


def require_kb():
    """Fail loudly and actionably if the KB is not where we think it is. Called by every entry point:
    a silently-empty KB would make the gates report '0 files, 0 failures' — a false green."""
    if not os.path.isdir(KB_DIR):
        raise SystemExit(
            "MotionKB not found at %s\n"
            "The KB lives in the Unity repository, not here. Set MOTIONKB_DIR to its 'agent/kb'\n"
            "directory (from WSL that is a /mnt/<drive>/... path) and make sure the drive is mounted."
            % KB_DIR)
    return KB_DIR


def action_files(d=None):
    """Every action record in `d`, sorted; the accepted store by default.

    Membership is the DIRECTORY, not a denylist. While the records sat loose in the KB root beside
    engine_mask_map.json / kb_manifest.json / retrieval_eval_set.json, every consumer that walked the
    store had to name those three in order to skip them, and that set was pasted into seven files
    under two different names. A fourth shared file would have had to be added to all seven, or be
    silently validated as an action record. actions/ holds action records and nothing else, so there
    is nothing to exclude and no list to keep in sync.

    Takes a directory so candidate/ -- which holds staged records and equally nothing else -- is read
    through the same call."""
    return sorted(glob.glob(os.path.join(d or ACTIONS_DIR, "*.json")))


def rel(path):
    """Path relative to the KB root, for readable log lines."""
    try:
        return os.path.relpath(path, KB_DIR)
    except ValueError:
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
