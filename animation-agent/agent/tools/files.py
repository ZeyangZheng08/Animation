"""
files.py — glob, grep and read: one search vocabulary, for everything the agent may look at.

WHY NOT `kb_glob` / `kb_grep` / `kb_read`. The first version of these was fenced inside the knowledge
base and named after it, and that was wrong in two directions at once.

  Naming. glob, grep and read are the three verbs every model has seen ten thousand times, with settled
  parameter names. A `kb_` prefix throws that prior away and makes the model learn from a description
  what it already knew. These are the ordinary tools, spelled the ordinary way.

  Scope. The knowledge base is a DERIVATIVE of the Unity animation assets. So the question that decides
  whether a sit-down has to be generated — is there any sit-down material at all, anywhere? — cannot be
  answered inside it. A tool that sees only the derivative can only repeat what was already decided
  when the derivative was built.

So there is one workspace with named mounts, and the three tools address all of it:

    kb/       the motion knowledge base: accepted records, _raw per-frame dumps, _frames renders,
              _derived tables, the manifest, the eval set
    source/   the Unity animation assets the knowledge base was extracted from

Paths are `<mount>/<rest>`; `read(".")` lists the mounts. There are no absolute paths, because the
workspace is virtual — the model has no business knowing this repository is reached over /mnt/f.

READ-ONLY BY CONSTRUCTION. No write, no edit, no shell. `Workspace.resolve` is the single containment
boundary — syntactic checks first, then realpath, so a symlink pointing out of a mount fails too — and
it is one function rather than a check repeated per tool. No subprocess ever starts, so there is no
command string to parse and police, and no injection surface: only paths.

READ DOES SEVERAL THINGS, deliberately. A directory lists its entries, an image comes back as a picture
the model can look at, everything else comes back as numbered lines. That is what retired `kb_frames`:
fetching a rendered pose was never a different KIND of access, only a different file extension.
"""
import base64
import collections
import fnmatch
import os

import paths
from .registry import ToolFailure

MAX_MATCHES = 60
MAX_LINE_CHARS = 400      # a _raw dump is ONE line of ~2 MB; without this a single hit floods the window
MAX_READ_LINES = 200
MAX_READ_CHARS = 8000
MAX_PATHS = 200
MAX_ENTRIES = 300

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")
BINARY_SUFFIXES = IMAGE_SUFFIXES + (".fbx", ".blend", ".wav", ".mp3", ".ttf", ".dll")
# Unity writes a `.meta` beside every asset, holding a guid and import settings and never any motion.
# They are half the file count under `source/`, so grep skips them; glob still lists them.
SKIP_SUFFIXES = BINARY_SUFFIXES + (".meta",)

FRAME_NOTE = ("Rendered frames are named <view>_f<percent>.png and are sampled INSIDE the action "
              "window -- a clip's very first and last frames are never among them. kb_pose measures "
              "how an action actually starts or ends.")

# Directory names that are not part of the searchable workspace, however deep they sit.
#
# `_authored_claude_backup` is a wholesale-superseded snapshot: every record in it was replaced when
# the semantic half was re-authored, and it is kept only so the change stays auditable. Left visible it
# would answer a search with a record the pipeline no longer produces — the same shape of confident
# wrong answer as `sit` matching `position`, and harder to notice, because a stale record looks exactly
# like a current one. Widening the scope means being deliberate about what is IN it.
EXCLUDED_DIRS = ("_authored_claude_backup",)


def default_mounts():
    """The two places worth searching, with the source assets optional.

    `source` is derived from the KB path rather than configured separately: the KB lives at
    <unity project>/agent/kb, so the animation assets are two directories up and over. If that is not
    where they are — a KB copied elsewhere for a test — the mount is simply absent and the tools work
    with one. Overridable with MOTION_SOURCE_DIR.
    """
    mounts = collections.OrderedDict()
    mounts["kb"] = paths.KB_DIR
    source = os.environ.get("MOTION_SOURCE_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(paths.KB_DIR)), "Assets", "Animations")
    if os.path.isdir(source):
        mounts["source"] = source
    return mounts


class Workspace:
    """Named directories addressed as one tree. The only thing that turns a model-supplied string into
    a filesystem path."""

    def __init__(self, mounts):
        self.mounts = collections.OrderedDict(
            (name, os.path.realpath(d)) for name, d in mounts.items() if os.path.isdir(d))
        if not self.mounts:
            raise ToolFailure("no searchable directory is configured")

    def names(self):
        return list(self.mounts)

    def resolve(self, path):
        """(mount, subpath, absolute path). Mount is None for the workspace root itself."""
        text = (path or "").replace("\\", "/").strip()
        if text in ("", ".", "/", "./"):
            return None, "", None
        if text.startswith("/") or text.startswith("~"):
            raise ToolFailure("paths here start with a place name, not a filesystem root: %r" % path,
                              hint="places: " + ", ".join("%s/" % n for n in self.mounts))
        segments = text.split("/")
        if ".." in segments:
            raise ToolFailure("'..' is not allowed: %r" % path)
        for segment in segments:
            if segment in EXCLUDED_DIRS:
                raise ToolFailure(
                    "%s/ is a superseded snapshot and is not part of the workspace" % segment,
                    hint="the current records are the ones glob and grep can see")

        head, _, rest = text.partition("/")
        base = self.mounts.get(head)
        if base is None:
            raise ToolFailure("no place called %r" % head,
                              hint="places: " + ", ".join("%s/" % n for n in self.mounts) +
                                   ". read('.') lists them.")
        full = os.path.realpath(os.path.join(base, rest))
        if full != base and not full.startswith(base + os.sep):
            raise ToolFailure("%r resolves outside %s/" % (path, head))
        return head, rest.strip("/"), full

    def scope(self, path):
        """Files under `path`, as (path relative to it, path relative to the workspace).

        Two path flavours because that is what the tools need: a pattern is matched against the first
        — so `glob('*.json', path='kb')` means what it looks like — and results are reported as the
        second, so every path that comes back can be handed straight to `read`.
        """
        mount, sub, full = self.resolve(path)
        if mount is None:
            for name, base in self.mounts.items():
                for rel in _walk(base):
                    yield "%s/%s" % (name, rel), "%s/%s" % (name, rel)
            return
        if not os.path.isdir(full):
            raise ToolFailure("%r is not a directory" % path,
                              hint="omit `path` to search everything, or name a directory")
        prefix = "/".join(p for p in (mount, sub) if p)
        for rel in _walk(full):
            yield rel, "%s/%s" % (prefix, rel)


# How many directory walks and file reads to have in flight at once. The KB is normally on a Windows
# worktree reached over DrvFs, where every filesystem call is a protocol round trip -- measured at about
# 11.5 ms per file and 15 ms per directory, regardless of size. Nothing here is CPU-bound, so threads
# overlap the waiting: reading the KB went 379 ms -> 51 ms, walking Assets/Animations 114 ms -> 23 ms.
# Past 16 the curve flattens and then reverses. On a local disk this changes nothing measurable.
FS_WORKERS = 16

# Files read per batch. Bounds peak memory: without it a wide tree is pulled into RAM whole, and one
# `_raw` dump is about 900 KB.
READ_BATCH = 64


def _walk(base):
    """Every file under `base`, relative and slash-separated, depth-first in sorted order.

    The immediate subdirectories are walked concurrently and their results spliced back in order, so
    the output is identical to the serial version -- the concurrency is in the waiting, not the shape.
    """
    return _walk_dir(base, concurrent=True)


def _walk_dir(base, concurrent=False):
    try:
        entries = sorted(os.scandir(base), key=lambda e: e.name)
    except OSError:
        return []
    out, subdirs = [], []
    for entry in entries:
        try:
            if entry.is_dir():
                if entry.name not in EXCLUDED_DIRS:
                    subdirs.append(entry.name)
            else:
                out.append(entry.name)
        except OSError:
            continue

    if not subdirs:
        return out
    if concurrent and len(subdirs) > 1:
        import concurrent.futures as cf
        with cf.ThreadPoolExecutor(max_workers=min(FS_WORKERS, len(subdirs))) as pool:
            walked = list(pool.map(lambda d: _walk_dir(os.path.join(base, d)), subdirs))
    else:
        walked = [_walk_dir(os.path.join(base, d)) for d in subdirs]
    for name, rels in zip(subdirs, walked):
        out.extend("%s/%s" % (name, rel) for rel in rels)
    return out


def _read_lines(abs_path):
    """The file as a list of lines, or None if it could not be read."""
    try:
        with open(abs_path, encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()
    except (IOError, OSError):
        return None


def _read_batched(ws, workspace_paths):
    """(workspace path, lines) in the given order, reading `READ_BATCH` files at a time concurrently.

    In order, because grep reports matches in the order it walked; concurrently, because the wait is
    the whole cost; batched, so peak memory does not track the size of the tree.
    """
    if not workspace_paths:
        return
    import concurrent.futures as cf
    for start in range(0, len(workspace_paths), READ_BATCH):
        batch = workspace_paths[start:start + READ_BATCH]
        if len(batch) == 1:
            yield batch[0], _read_lines(_abs(ws, batch[0]))
            continue
        with cf.ThreadPoolExecutor(max_workers=min(FS_WORKERS, len(batch))) as pool:
            for full, lines in zip(batch, pool.map(lambda p: _read_lines(_abs(ws, p)), batch)):
                yield full, lines


def _matches(rel, pattern):
    """fnmatch, but `**` spans directories and a bare `*.json` stays at the top level.

    fnmatch alone lets `*` cross separators, which would make `*.json` match `_raw/Typing.json` and
    quietly turn a top-level search into a recursive one.
    """
    if not pattern:
        return True
    if "**" in pattern:
        head, _, tail = pattern.partition("**/")
        if head and not rel.startswith(head):
            return False
        return fnmatch.fnmatch(rel[len(head):] if head else rel, tail or "*") or \
            fnmatch.fnmatch(os.path.basename(rel), tail or "*")
    if "/" in pattern:
        return fnmatch.fnmatch(rel, pattern)
    return "/" not in rel and fnmatch.fnmatch(rel, pattern)


def _is_image(rel):
    return rel.lower().endswith(IMAGE_SUFFIXES)


def _mime(rel):
    return "image/jpeg" if rel.lower().endswith((".jpg", ".jpeg")) else "image/png"


def _clip(line):
    line = line.rstrip("\n")
    return line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + "  ...[line truncated]"


def _frames_note(rel):
    return FRAME_NOTE if "/_frames/" in "/" + rel else None


# ---- schemas ---------------------------------------------------------------------------------
#
# Parameter names are opencode's and Claude Code's, not new ones: `pattern`/`path` for glob,
# `pattern`/`path`/`include` for grep, `file_path`/`offset`/`limit` for read. A model that has never
# read these descriptions still calls them correctly, which is the entire point of not inventing a
# private vocabulary.

GLOB_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "pattern": {"type": "string",
                    "description": "Glob pattern, e.g. '*.json', '**/*.png', '_frames/**/*.png'. "
                                   "'**' spans directories. Relative to `path` when one is given."},
        "path": {"type": "string",
                 "description": "Directory to search in, e.g. 'kb' or 'kb/_frames'. Omit to search "
                                "everywhere."},
    },
    "required": ["pattern"],
}

GREP_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "pattern": {"type": "string", "description": "Regular expression to search file contents for."},
        "path": {"type": "string", "description": "Directory to search in. Omit to search everywhere."},
        "include": {"type": "string",
                    "description": "Only search files matching this glob, e.g. '*.json', '**/*.anim'."},
        "files_only": {"type": "boolean",
                       "description": "Return only the file names, not the matching lines."},
        "context": {"type": "integer", "minimum": 0, "maximum": 5,
                    "description": "Lines of context around each match."},
    },
    "required": ["pattern"],
}

READ_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "file_path": {"type": "string",
                      "description": "Path to a file or directory, e.g. 'kb/typing.json'. Use '.' to "
                                     "list the places that exist."},
        "offset": {"type": "integer", "minimum": 1, "description": "First line to read (1-based)."},
        "limit": {"type": "integer", "minimum": 1, "maximum": MAX_READ_LINES},
    },
    "required": ["file_path"],
}


# ---- registration ----------------------------------------------------------------------------

def register(registry, mounts=None):
    import re                                        # only for grep; keeps the import cost honest

    ws = Workspace(mounts or default_mounts())
    places = ", ".join("%s/" % n for n in ws.names())

    def glob(pattern, path=None):
        found = [full for rel, full in ws.scope(path) if _matches(rel, pattern)]
        result = {"pattern": pattern, "count": len(found), "paths": found[:MAX_PATHS]}
        if len(found) > MAX_PATHS:
            result["note"] = "showing the first %d of %d" % (MAX_PATHS, len(found))
        elif not found:
            result["note"] = "nothing matched. Places: %s -- read('.') lists them." % places
        return result

    def grep(pattern, path=None, include=None, files_only=False, context=0):
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise ToolFailure("not a valid regular expression: %s" % e)

        files, matches, skipped, example = [], [], 0, None
        candidates = []
        for rel, full in ws.scope(path):
            if include and not _matches(rel, include):
                continue
            if full.lower().endswith(SKIP_SUFFIXES):
                skipped += 1
                continue
            candidates.append(full)
        searched = len(candidates)

        # Reads are batched rather than one at a time. The file loop below always visits every
        # candidate -- the inner break leaves the LINE loop, never this one -- so nothing extra is
        # read, only read sooner and in parallel.
        for full, lines in _read_batched(ws, candidates):
            if lines is None:
                continue
            hit = None
            for i, line in enumerate(lines):
                if not rx.search(line):
                    continue
                if hit is None:
                    hit = "%s:%d:%s" % (full, i + 1, _clip(line))
                if files_only or len(matches) >= MAX_MATCHES:
                    break
                lo, hi = max(0, i - context), min(len(lines), i + context + 1)
                for k in range(lo, hi):
                    matches.append("%s:%d:%s" % (full, k + 1, _clip(lines[k])))
            if hit is not None:
                files.append(full)
                if example is None:
                    example = hit

        result = {"pattern": pattern, "files_searched": searched, "files_with_matches": files}
        notes = []
        if files_only:
            # One matched line even here. `files_only` is asked for to keep the answer short, and the
            # short answer "23 files matched" is exactly the shape that cannot be checked -- see the
            # note below for what that costs.
            if example:
                result["example_match"] = example
        else:
            result["matches"] = matches
            if len(matches) >= MAX_MATCHES:
                notes.append("stopped at %d matches" % MAX_MATCHES)
        if searched > 1 and len(files) == searched:
            # A pattern that matches EVERY file searched is a fact about the pattern, not about the
            # corpus. Measured: `sit` matches all 23 animation assets, because every one of them
            # contains the word `position`, and a model that asked whether a sit-down exists would have
            # read 23 hits as a yes. This is the one grep result that must never be quietly plausible.
            notes.append("every one of the %d files searched matched, which usually means the pattern "
                         "hit something incidental -- `sit` matches `position`. Try word boundaries "
                         "(\\bsit\\b), or glob over file names if names are what carry the meaning"
                         % searched)
        if skipped:
            notes.append("%d binary or .meta file(s) skipped; glob lists them" % skipped)
        if not files:
            notes.append("no file matched, in %s. The corpus is small, so absence here is real."
                         % (path or "any place"))
        if notes:
            result["note"] = "; ".join(notes)
        return result

    def read(file_path, offset=1, limit=MAX_READ_LINES):
        mount, sub, full = ws.resolve(file_path)
        if mount is None:
            return {"path": ".", "type": "workspace", "entries": ["%s/" % n for n in ws.names()],
                    "note": "kb/ is the motion knowledge base. " +
                            ("source/ is the Unity animation assets it was extracted from."
                             if "source" in ws.names() else "")}

        rel = "/".join(p for p in (mount, sub) if p)
        if not os.path.exists(full):
            raise ToolFailure("no such file: %r" % file_path, hint="glob shows what exists")

        if os.path.isdir(full):
            names = sorted(n for n in os.listdir(full) if n not in EXCLUDED_DIRS)
            entries = [n + "/" if os.path.isdir(os.path.join(full, n)) else n for n in names]
            out = {"path": rel, "type": "directory", "entries": entries[:MAX_ENTRIES],
                   "count": len(entries)}
            if len(entries) > MAX_ENTRIES:
                out["note"] = "showing the first %d of %d" % (MAX_ENTRIES, len(entries))
            note = _frames_note(rel + "/")
            if note:
                out["note"] = ((out.get("note", "") + " ") if out.get("note") else "") + note
            return out

        if _is_image(rel):
            with open(full, "rb") as fh:
                data = base64.b64encode(fh.read()).decode("ascii")
            out = {"path": rel, "type": "image",
                   # Consumed by the loop, stripped before the result reaches the model as text.
                   "images": [{"data_uri": "data:%s;base64,%s" % (_mime(rel), data), "caption": rel}]}
            note = _frames_note(rel)
            if note:
                out["note"] = note
            return out

        if full.lower().endswith(BINARY_SUFFIXES):
            raise ToolFailure("%r is binary" % file_path)

        with open(full, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()

        limit = min(int(limit), MAX_READ_LINES)
        window = lines[offset - 1: offset - 1 + limit]
        rendered, used, notes = [], 0, []
        for n, line in enumerate(window, start=offset):
            piece = "%6d\t%s" % (n, _clip(line))
            if used + len(piece) > MAX_READ_CHARS:
                notes.append("stopped at %d characters" % MAX_READ_CHARS)
                break
            rendered.append(piece)
            used += len(piece)
        if offset - 1 + len(rendered) < len(lines):
            notes.append("file has %d lines; read from offset %d for more"
                         % (len(lines), offset + len(rendered)))
        out = {"path": rel, "type": "file", "lines_total": len(lines), "content": "\n".join(rendered)}
        if notes:
            out["note"] = "; ".join(notes)
        return out

    registry.add("glob",
                 "Find files by name pattern. Places: %s. Use it to settle what exists before "
                 "assuming it does not. Under source/ this is the tool that answers such questions: "
                 "an animation asset is curve data whose only readable name is its file name." % places,
                 GLOB_PARAMS, glob)
    registry.add("grep",
                 "Search file contents with a regular expression. One call answers 'which actions "
                 "mention X' outright, instead of rephrasing a search until something turns up. Use "
                 "word boundaries for short words -- `sit` matches `position`.",
                 GREP_PARAMS, grep)
    registry.add("read",
                 "Read a file, or list a directory. Rendered frames come back as pictures to look at. "
                 "read('.') shows the places that exist.",
                 READ_PARAMS, read)
    return registry


def _abs(ws, workspace_path):
    """Workspace path back to a filesystem path. Cheap because `scope` only ever yields contained
    paths — this is a lookup, not a second containment check."""
    head, _, rest = workspace_path.partition("/")
    return os.path.join(ws.mounts[head], rest.replace("/", os.sep))
