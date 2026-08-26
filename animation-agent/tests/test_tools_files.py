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

    hits = call(registry, loop, "grep", pattern="overall_intent", path="kb")
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
    out = call(registry, loop, "glob", pattern="frames/**/*.png", path="kb")
    assert out["success"] and out["count"] >= 40
    assert any("Typing" in p for p in out["paths"])
    assert all(p.startswith("kb/") for p in out["paths"]), "results must be usable as read paths"


def test_a_pattern_is_relative_to_path_but_results_are_not(registry, loop):
    """`glob('t*.json', path='kb')` has to mean what it looks like, and still return something `read`
    accepts. Those are two different path flavours and both are load-bearing.

    The pattern is narrower than `*.json` because the store is one directory of 2454 records now
    (ADR 0016) and a wildcard over all of them truncates at MAX_PATHS -- announced in `note`, but it
    would turn this test into a test about the cap."""
    out = call(registry, loop, "glob", pattern="t*.json", path="kb/actions")
    assert out["success"] and "kb/actions/typing.json" in out["paths"]
    assert call(registry, loop, "read", file_path="kb/actions/typing.json")["success"]


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
    """One call replaces the rephrasing loop. The manifest matches too, since it aggregates every
    record — so the claim is about which ACTION records match, not which files."""
    out = call(registry, loop, "grep", pattern='"posture": "seated"', path="kb", include="**/*.json")
    assert out["success"]
    records = [f for f in out["files_with_matches"] if not f.endswith("manifest.json")]
    assert records == ["kb/actions/typing.json"]


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
    stopped being true at 2454 records. The note now rests on the count of what was actually searched,
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


def test_a_pattern_that_matches_everything_says_so(registry, loop, has_source):
    """The trap the widened scope opened, caught live. `sit` matches all 23 animation assets, because
    every one of them contains `position`. A model asking whether a sit-down exists would have read
    23 hits as a yes — a confident wrong answer, which is worse than not being able to search at all.

    So a result that matched every file searched carries a note saying what that means. The fix is not
    to forbid the query; it is to stop the useless answer from looking like a useful one.
    """
    if not has_source:
        pytest.skip("source assets not mounted")
    out = call(registry, loop, "grep", pattern="sit", path="source", include="**/*.anim",
               files_only=True)
    assert out["success"] and len(out["files_with_matches"]) == out["files_searched"]
    assert "incidental" in out["note"] and "position" in out["note"]
    # And never a bare file list with nothing to check it against.
    assert "position" in out["example_match"].lower()


def test_word_boundaries_make_the_same_question_answerable(registry, loop, has_source):
    if not has_source:
        pytest.skip("source assets not mounted")
    out = call(registry, loop, "grep", pattern=r"\bsit\b", path="source", include="**/*.anim",
               files_only=True)
    assert out["success"] and len(out["files_with_matches"]) < out["files_searched"]


def test_whether_a_sit_exists_anywhere_is_answerable_from_the_source_assets(registry, loop, has_source):
    """THE test for widening the scope. "No sit-down exists" was previously a claim the agent could
    only take from its prompt: the knowledge base holds the eight accepted actions, so asking it what
    exists returns what was already decided. The animation assets are the population, and the agent can
    now read them itself.

    This asserts the QUESTION is answerable, not a particular answer — if a sit-down asset is ever
    imported, the right outcome is that the agent finds it, not that this test fails.
    """
    if not has_source:
        pytest.skip("source assets not mounted")
    everything = call(registry, loop, "glob", pattern="**/*.anim", path="source")
    assert everything["success"] and everything["count"] > 0

    out = call(registry, loop, "glob", pattern="**/*[Ss]it*", path="source")
    assert out["success"]
    assert out["count"] == 0 or all("source/" in p for p in out["paths"])

def test_an_unlabelled_record_offers_a_regex_nothing_but_its_own_identifiers(registry, loop):
    """TRIPWIRE, not an invariant. `glob`'s description tells the model to prefer it to grep for
    anything name-shaped, and this is why: a measured-only record contains its own identifiers -- the
    clip name and the guid -- and beyond those, only a vocabulary the schema fixes (`channels`,
    `left_arm`, `static`, `null`, and the 95 Unity muscle DOF names `mean_pose` is keyed by). Not one
    word of it describes the motion. So grep over the store reads 2446 files to return what glob
    matched from the file names without opening any.

    The sample is the store minus the accepted records, because those ARE labelled -- one directory
    holds both now (ADR 0016) and status is what separates them.

    WHEN THIS FAILS, the semantic pass has landed: records now carry motion_description / tags /
    overall_intent, the vocabulary is prose rather than field names, and grep over the store starts
    earning the ~7 s it costs. Update glob's description in agent/tools/files.py and this test —
    do not relax the bound. See ADR 0014.
    """
    import glob as globmod
    import io
    import json
    import re

    accepted = set(paths.accepted_files())
    files = [f for f in sorted(globmod.glob(os.path.join(paths.ACTIONS_DIR, "*.json")))
             if f not in accepted]
    if len(files) < 50:
        pytest.skip("the bulk corpus is not ingested here")
    sample = files[:200]

    def words_in(text):
        return set(re.findall(r"[A-Za-z][A-Za-z_]{2,}", text))

    def words(path):
        return words_in(io.open(path, encoding="utf-8").read())

    def identifiers(path):
        """Every token the record's own identity contributes. Read off `source_clip` rather than
        guessed from the file name, because neither is one token: `mx_135_Degree_Left_Turn` breaks at
        the digits, and a 32-char hex guid yields runs like `aaef` and `dbe`."""
        sc = json.load(io.open(path, encoding="utf-8"))["source_clip"]
        return words_in(" ".join(str(sc.get(k) or "") for k in ("clip_name", "guid", "fbx_or_anim")))

    vocabulary = set()
    for f in sample:
        vocabulary |= words(f)
    for f in sample:
        vocabulary -= identifiers(f)

    # 200, against 164 measured. It was 150 against 104 until formula v3.0.0 gave every channel a
    # `mean_pose` keyed by Unity's muscle DOF names (`Left Forearm Twist In-Out`, `Chest Front-Back`),
    # which is +60 words of vocabulary and not one word about any particular clip: the names are a
    # closed set of 95 fixed by HumanTrait, identical in every record, so what the tripwire watches
    # for is unchanged. The headroom is still nowhere near what 2446 free-text motion_descriptions
    # would produce, so it still fires early in the semantic pass rather than after it.
    assert len(vocabulary) < 200, (
        "the corpus vocabulary has grown to %d words beyond the records\' own identifiers (%s ...) -- "
        "if that is the semantic pass landing, update glob\'s description in agent/tools/files.py and "
        "this test rather than relaxing the bound" % (len(vocabulary), sorted(vocabulary)[:12]))

    for f in sample[:20]:
        beyond = words(f) - vocabulary - identifiers(f)
        assert not beyond, ("%s carries a word that is neither shared vocabulary nor part of its own "
                            "identity: %s" % (os.path.basename(f), sorted(beyond)))
