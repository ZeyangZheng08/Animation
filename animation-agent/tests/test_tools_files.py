"""glob, grep and read — the ordinary tools, over the workspace.

The containment tests matter most. These are the only tools that take a path from the model, and
"read-only, and it cannot leave the mounts" has to be a property of the code rather than a sentence in
a docstring.

The other thing pinned here is the SCOPE. These used to be `kb_glob`/`kb_grep`/`kb_read`, fenced inside
the knowledge base, and the question that fencing made unanswerable is the one at the bottom of this
file: whether a sit-down exists anywhere at all. The knowledge base is a derivative; asking it what
exists only returns what was already accepted into it.
"""
import asyncio
import os

import pytest

import paths
from agent.tools import ToolRegistry
from agent.tools import files as F
from tests import corpus as C


@pytest.fixture(scope="module")
def loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def registry():
    if not os.path.isdir(paths.KB_DIR):
        pytest.skip("knowledge base not available")
    return F.register(ToolRegistry())


@pytest.fixture(scope="module")
def has_source():
    return "source" in F.Workspace(F.default_mounts()).names()


def call(registry, loop, name, **kwargs):
    return loop.run_until_complete(registry.dispatch(name, kwargs))


# ---- containment -----------------------------------------------------------------------------

@pytest.mark.parametrize("path, why", [
    ("../../../etc/passwd", "parent traversal"),
    ("/etc/passwd", "absolute path"),
    ("~/.ssh/id_rsa", "home expansion"),
    ("kb/raw/../../../etc/hosts", "traversal in the middle"),
    ("kb/../../etc/passwd", "traversal out of a mount"),
    ("etc/passwd", "a place that does not exist"),
])
def test_read_refuses_to_leave_the_mounts(registry, loop, path, why):
    out = call(registry, loop, "read", file_path=path)
    assert out["success"] is False, "%s should be refused (%s)" % (path, why)


def test_an_unknown_place_names_the_ones_that_exist(registry, loop):
    """A refusal the model cannot act on is a dead end. This one carries the way out."""
    out = call(registry, loop, "read", file_path="motions/typing.json")
    assert out["success"] is False and "kb/" in out["hint"]


def test_glob_refuses_to_search_outside_the_mounts(registry, loop):
    out = call(registry, loop, "glob", pattern="*", path="../..")
    assert out["success"] is False


def test_the_superseded_authoring_snapshot_is_not_in_the_workspace(registry, loop):
    """A stale record looks exactly like a current one, which makes it the worse kind of wrong answer:
    the archived snapshot holds nine records that were replaced wholesale when the semantic half was
    re-authored.

    It used to sit inside the KB and be skipped by name. Since ADR 0017 it is in `motionkb_build/`,
    which is not mounted — so this asserts UNREACHABLE rather than skipped, which is the stronger
    property and the reason the archive was moved instead of denylisted."""
    listing = call(registry, loop, "read", file_path="kb")
    assert not any("authored_claude_backup" in e for e in listing["entries"])

    found = call(registry, loop, "glob", pattern="**/*.json", path="kb")
    assert not any("authored_claude_backup" in p for p in found["paths"])

    # A field every record in the store carries, so a hit here would be the archive and nothing else.
    # It used to be `overall_intent`, which v4 renamed away (ADR 0022) -- leaving the grep matching
    # nothing anywhere and the assertion vacuously true.
    hits = call(registry, loop, "grep", pattern="action_description", path="kb")
    assert hits["files_with_matches"], "the pattern has to match something, or this proves nothing"
    assert not any("authored_claude_backup" in f for f in hits["files_with_matches"])

    # No path spells it, because it is not under a mount. The traversal that would reach it is the
    # containment boundary's job, and it is refused there (see the parametrised cases above).
    for attempt in ("kb/../motionkb_build/archive/authored_claude_backup/idle.json",
                    "motionkb_build/archive/authored_claude_backup/idle.json"):
        out = call(registry, loop, "read", file_path=attempt)
        assert out["success"] is False


def test_there_is_no_tool_that_writes(registry):
    """The whole surface is read-only by construction, not by policy."""
    assert sorted(registry.names()) == ["glob", "grep", "read"]


# ---- read is the entry point -----------------------------------------------------------------

def test_read_dot_lists_the_places(registry, loop):
    out = call(registry, loop, "read", file_path=".")
    assert out["success"] and "kb/" in out["entries"]


def test_read_lists_a_directory(registry, loop):
    out = call(registry, loop, "read", file_path="kb/raw")
    assert out["success"] and out["type"] == "directory"
    assert any(e.endswith(".json") for e in out["entries"])


# ---- glob ------------------------------------------------------------------------------------

def test_glob_finds_the_rendered_frames(registry, loop):
    out = call(registry, loop, "glob", pattern="frames/%s/*.jpg" % C.WALK, path="kb")
    assert out["success"] and out["count"] >= 20
    assert all(p.startswith("kb/") for p in out["paths"]), "results must be usable as read paths"


def test_glob_finds_the_far_side_of_the_ring(registry, loop):
    """Frames are the eight-view ring since 2026-08-26, so the angle a near view hides is on disk and
    reachable by name. Before that a clip had two views and neither of them was ever the back."""
    out = call(registry, loop, "glob", pattern="frames/%s/back*.jpg" % C.WALK, path="kb")
    assert out["success"] and out["count"] >= 3


def test_a_pattern_is_relative_to_path_but_results_are_not(registry, loop):
    """`glob('t*.json', path='kb')` has to mean what it looks like, and still return something `read`
    accepts. Those are two different path flavours and both are load-bearing.

    The pattern is narrower than `*.json` because the store is one directory of 2446 records now
    (ADR 0016) and a wildcard over all of them truncates at MAX_PATHS -- announced in `note`, but it
    would turn this test into a test about the cap."""
    record = "kb/actions/%s.json" % C.WALK
    out = call(registry, loop, "glob", pattern="%s.json" % C.WALK, path="kb/actions")
    assert out["success"] and record in out["paths"]
    assert call(registry, loop, "read", file_path=record)["success"]


def test_a_top_level_glob_does_not_leak_into_subdirectories(registry, loop):
    """`*.json` at the KB root must mean the shared files that sit there, not those plus every record
    under actions/ and every dump under raw/. fnmatch alone matches across separators and would
    silently turn this into a recursive search."""
    out = call(registry, loop, "glob", pattern="*.json", path="kb")
    assert out["success"] and out["paths"]
    assert all(p.count("/") == 1 for p in out["paths"])
    assert not any("/actions/" in p or "/raw/" in p for p in out["paths"])


def test_glob_spans_every_place_when_unscoped(registry, loop, has_source):
    if not has_source:
        pytest.skip("source assets not mounted")
    out = call(registry, loop, "glob", pattern="**/*")
    assert {p.split("/")[0] for p in out["paths"]} >= {"kb"}
    assert out["count"] > 100


# ---- grep ------------------------------------------------------------------------------------

def test_grep_answers_which_actions_are_seated_in_one_call(registry, loop):
    """One call replaces the rephrasing loop.

    WHAT IT MATCHES ON CHANGED TWICE. It used to be `"posture": "seated"`, a stored label; v4 deletes
    the whole `composability` block (ADR 0022) and derives the posture from measured geometry, which
    is a number no regex can bin. What every record now says in words is its `action_description` and
    eight `motion_description`s, so this is a search over prose — which is what grep is for.

    AND IT RETURNS MANY, WHICH IS THE POINT. Over eight records the answer was one file and could be
    written down; over 2446 the answer is a population, and what is worth asserting is that it is a
    SUBSET rather than everything, that a clip known to be seated is in it, and that a clip known to
    be standing is not. A grep that returned every file would be the failure mode
    `test_a_pattern_that_matches_everything_says_so` is about."""
    out = call(registry, loop, "grep", pattern="seated", path="kb/actions", files_only=True)
    assert out["success"]
    hits = set(out["files_with_matches"])
    assert 0 < len(hits) < out["files_searched"]
    assert "kb/actions/%s.json" % C.SEATED in hits
    assert "kb/actions/%s.json" % C.WALK not in hits


def test_grep_skips_binaries_and_says_so(registry, loop):
    out = call(registry, loop, "grep", pattern="posture", path="kb")
    assert out["success"] and "skipped" in out["note"]


def test_grep_truncates_the_two_megabyte_single_line_raw_dumps(registry, loop):
    """A `raw` dump is ONE line. Without a per-line cap, a single hit floods the context window."""
    out = call(registry, loop, "grep", pattern="bones", path="kb/raw")
    assert out["success"] and out["matches"]
    assert all(len(m) < F.MAX_LINE_CHARS + 200 for m in out["matches"])


def test_grep_rejects_a_broken_regex_instead_of_matching_nothing(registry, loop):
    out = call(registry, loop, "grep", pattern="[unclosed")
    assert out["success"] is False and "regular expression" in out["error"]


# ---- the scope the fenced version could not reach ---------------------------------------------

def test_the_library_holds_no_sit_or_stand_action(registry, loop):
    out = call(registry, loop, "grep", pattern="sit_down|stand_up", path="kb")
    assert out["success"] and out["files_with_matches"] == []
    assert "evidence of absence" in out["note"]


def test_a_miss_does_not_rest_on_the_corpus_being_small(registry, loop):
    """It used to say "the corpus is small, so absence here is real". The point was right — a miss has
    to be usable as evidence, or the model rephrases its way to the iteration limit — but the premise
    stopped being true at 2446 records. The note now rests on the count of what was actually searched,
    which the model can check, and on the two things that would widen it."""
    out = call(registry, loop, "grep", pattern="zzz_no_such_token_zzz", path="kb")
    assert out["success"] and out["files_with_matches"] == []
    assert "corpus is small" not in out["note"]
    assert "out of %d file(s) searched" % out["files_searched"] in out["note"]
    assert "widen `path`" in out["note"]


# ---- the pose dumps grep does not read --------------------------------------------------------

def test_grep_does_not_read_the_pose_dumps_and_says_so(registry, loop):
    """`raw` is 1.4 GB of per-frame numbers. The only words in a dump are the 50 bone names and the
    95 muscle names, so reading it costs about 24 s to search text that is not there."""
    out = call(registry, loop, "grep", pattern="Chest", path="kb")
    assert out["success"]
    assert "pose dump(s) under raw/ not searched" in out["note"]
    assert not any("/raw/" in f for f in out["files_with_matches"])


def test_naming_a_path_inside_raw_searches_it_anyway(registry, loop):
    """Not hidden — excluded by default. A caller who names the directory has asked for it."""
    out = call(registry, loop, "grep", pattern="muscle_names", path="kb/raw")
    assert out["success"] and out["files_with_matches"]
    assert "not searched" not in (out.get("note") or "")


def test_glob_still_lists_the_pose_dumps(registry, loop):
    """The exclusion is grep's alone. Whether a clip has been sampled stays answerable."""
    out = call(registry, loop, "glob", pattern="kb/raw/*.json")
    assert out["success"] and out["count"] > 0


def test_the_exclusion_is_by_directory_not_by_a_corpus_prefix(registry, loop):
    """What makes a dump unsearchable is what it contains, which was equally true of the eight
    original dumps. A rule keyed on `mx_` would have been a rule about where the file came from."""
    assert F.UNSEARCHED_BY_GREP == ("raw",)
    out = call(registry, loop, "grep", pattern="Walk_N", path="kb")
    assert out["success"]
    assert not any(f.endswith("raw/Walk_N.json") for f in out["files_with_matches"])


def test_a_pattern_that_matches_everything_says_so(registry, loop):
    """The trap a wide scope opens, caught live. `sit` matched every animation asset on the old
    `source/` mount, because every one of them contained `position`; a model asking whether a
    sit-down exists would have read that as a yes — a confident wrong answer, which is worse than not
    being able to search at all.

    Those assets were YAML. The mount is the Mixamo FBX now and grep opens none of them, so the trap
    has moved rather than gone: the same shape is reachable over the records, where every one of 2446
    contains the schema's own field names."""
    out = call(registry, loop, "grep", pattern="channels", path="kb/actions", files_only=True)
    assert out["success"] and len(out["files_with_matches"]) == out["files_searched"]
    assert "incidental" in out["note"]
    # And never a bare file list with nothing to check it against.
    assert out["example_match"]


def test_word_boundaries_make_the_same_question_answerable(registry, loop):
    """The repair the note points at. `sit` hits `position` and `sitting` alike; `\bsit\b` hits the
    word."""
    loose = call(registry, loop, "grep", pattern="sit", path="kb/actions", files_only=True)
    tight = call(registry, loop, "grep", pattern=r"\bsits\b", path="kb/actions", files_only=True)
    assert loose["success"] and tight["success"]
    assert 0 < len(tight["files_with_matches"]) < len(loose["files_with_matches"])


def test_grep_over_the_binary_source_assets_proves_nothing_and_says_so(registry, loop, has_source):
    """`source/` is 2446 FBX and their .meta: binary, so grep skips every one and searches zero files.

    A MISS OVER NOTHING IS NOT A MISS, and reporting it as evidence of absence would be the worst
    answer this tool can give — a confident no about a question nobody asked. This is the one place
    the count in the note is load-bearing rather than informative."""
    if not has_source:
        pytest.skip("source assets not mounted")
    out = call(registry, loop, "grep", pattern="sit", path="source")
    assert out["success"] and out["files_searched"] == 0
    assert "evidence of absence" not in out["note"]
    assert "glob" in out["note"] and "NAMES" in out["note"]


def test_whether_something_exists_in_the_assets_is_a_question_about_names(registry, loop, has_source):
    """THE test for the narrowed scope. `source/` is the FBX the 2446 records were sampled from, so
    the two mounts describe one library and "does a sit-down exist" has one answer whichever way it
    is asked. Over the assets that answer comes from the file NAMES.

    This asserts the QUESTION is answerable, not a particular answer."""
    if not has_source:
        pytest.skip("source assets not mounted")
    everything = call(registry, loop, "glob", pattern="*.fbx", path="source")
    assert everything["success"] and everything["count"] > 0

    out = call(registry, loop, "glob", pattern="*[Ss]it*", path="source")
    assert out["success"] and out["count"] > 0
    assert all(p.startswith("source/") for p in out["paths"])


def test_the_source_mount_is_the_corpus_and_only_the_corpus(registry, loop, has_source):
    """The isolation the narrowing exists for. `source/` used to be all of `Assets/Animations`, which
    holds the nursing FBX and the nursing `.anim` beside the corpus; those are scene assets and they
    stay where the scenes reference them. What changed is that the agent cannot see them."""
    if not has_source:
        pytest.skip("source assets not mounted")
    out = call(registry, loop, "glob", pattern="*", path="source")
    assert out["success"] and out["count"] > 0
    for path in out["paths"]:
        assert not C.has_nursing_content(path), path
        assert os.path.basename(path).startswith("mx_"), path

def test_the_records_carry_prose_so_grep_over_the_store_earns_its_cost(registry, loop):
    """THE TRIPWIRE THIS REPLACES SAID THE OPPOSITE, and it fired exactly as it was written to.

    It asserted that a measured-only record contains nothing but its own identifiers and a vocabulary
    the schema fixes -- `channels`, `left_arm`, `static`, `null`, and the 95 Unity muscle DOF names
    `mean_pose` is keyed by -- so grep over the store read 2446 files to return what glob matched
    from the file names without opening any. Its instruction on failing was: the semantic pass has
    landed, update glob's description and this test rather than relaxing the bound. Both are done
    (see `register` in agent/tools/files.py).

    The premise it rested on is gone twice over. The corpus pass wrote nine sentences into every
    record, and `accept-corpus` then accepted all 2446 -- so there is no undescribed sample left to
    take, and `candidate` no longer means `wordless`. What is worth guarding now is the fact that
    replaced it: the store is searchable BY MEANING, which is what makes grep over `kb/actions` a
    real question rather than an expensive way to match file names.

    Measured when this was written: 200 sampled records carry about 1900 distinct words beyond the
    schema's vocabulary and their own identifiers, against the 141 the old bound was set at. The bar
    is 1000, which is far below what prose produces and far above what field names ever could.
    """
    import glob as globmod
    import io
    import json
    import re

    files = sorted(globmod.glob(os.path.join(paths.ACTIONS_DIR, "*.json")))
    if len(files) < 50:
        pytest.skip("the corpus is not ingested here")
    sample = files[:200]

    def words_in(text):
        return set(re.findall(r"[A-Za-z][A-Za-z_]{2,}", text))

    def prose(path):
        """Only the SENTENCES: the action's own description and the eight per-channel ones. Read off
        the named fields rather than off the whole file, so the schema's field names and the muscle
        DOF names cannot be mistaken for vocabulary about motion."""
        doc = json.load(io.open(path, encoding="utf-8"))
        text = [doc.get("action_description") or ""]
        for channel in (doc.get("channels") or {}).values():
            text.append((channel or {}).get("motion_description") or "")
        return " ".join(text)

    described = [f for f in sample if prose(f).strip()]
    assert len(described) == len(sample), (
        "%d of %d sampled records carry no sentence; the semantic half has gone missing"
        % (len(sample) - len(described), len(sample)))

    vocabulary = set()
    for f in sample:
        vocabulary |= words_in(prose(f))
    assert len(vocabulary) > 1000, (
        "the sampled records describe themselves in only %d distinct words, which is field-name "
        "vocabulary rather than prose -- if the descriptions have been stripped, grep over "
        "kb/actions/ has stopped being a search by meaning and glob's description in "
        "agent/tools/files.py needs to say so again" % len(vocabulary))
