"""test_ingest_corpus.py — the bulk path's three refusals, and its one guarantee.

The refusals: a duplicate clip name, a malformed index row, and a clip that already lives in either
store. Each of them, if it went through instead, would silently overwrite something — a pose dump, a
record, or an accepted action — and the loss would only surface much later as a wrong measurement
attached to the right name.

The guarantee: KINEMATIC comes out of `extract`, so the bulk path cannot grow its own dialect of the
block. That one is checked by identity, not by comparing two copies that happen to agree today.
"""
import json
import os
import types

import pytest

import config as C
import extract
import ingest_corpus as I
import paths
import unity_sampler

RAW_SOURCE_DIR = paths.RAW_DIR      # bound before the kb fixture redirects it

ROW = ("mx_Test_Clip\tAssets/Animations/Mixamo30/mx_Test_Clip.fbx\t"
       "0123456789abcdef0123456789abcdef\t-203655887218126122\t2.5\t30\ttrue")


def held_pose_dump(source="Walk_N"):
    """A 2-frame dump — Mixamo's pose assets resolve to exactly this.

    Built by taking a REAL dump and holding its first frame, rather than hand-rolling one: the dump
    shape is the sampler's, not this test's, and a fixture invented here would keep passing after the
    sampler grew a field that metrics.py needs.
    """
    real = paths.read_json(os.path.join(RAW_SOURCE_DIR, source + ".json"))
    out = {}
    for k, v in real.items():
        if isinstance(v, list) and len(v) == real["frames"]:
            out[k] = [v[0], v[0]]
        elif isinstance(v, dict) and all(isinstance(x, list) and len(x) == real["frames"] for x in v.values()):
            out[k] = {b: [p[0], p[0]] for b, p in v.items()}
        else:
            out[k] = v
    out["clip"], out["frames"], out["length"] = "mx_Test_Clip", 2, 0.033
    return out


@pytest.fixture
def kb(tmp_path, monkeypatch):
    """A throwaway KB. Every path the module reads is redirected, including the three it bound at
    import time, so a test can never write into the real store."""
    for name in ("ACTIONS_DIR", "RAW_DIR", "REPORTS_DIR"):
        d = tmp_path / name.lower()
        d.mkdir()
        monkeypatch.setattr(paths, name, str(d))
    monkeypatch.setattr(I, "INDEX", str(tmp_path / "reports_dir" / "corpus_index.tsv"))
    monkeypatch.setattr(I, "FAILED", str(tmp_path / "reports_dir" / "failures.txt"))
    monkeypatch.setattr(I, "REPORT", str(tmp_path / "reports_dir" / "ingest.md"))
    return tmp_path


def write_index(rows):
    paths.write_text(I.INDEX, "# header\n" + "\t".join(I.COLUMNS) + "\n" + "\n".join(rows) + "\n")


def fake_engine(monkeypatch, text):
    monkeypatch.setattr(unity_sampler, "bridge_healthy", lambda *a, **k: True)
    monkeypatch.setattr(unity_sampler, "close_connections", lambda *a, **k: None)
    monkeypatch.setattr(unity_sampler, "run_csharp_over_http", lambda *a, **k: (True, text, None))


def args(**kw):
    base = {"host": "h", "port": 1, "instance": None, "dir": "Assets/Animations/Mixamo30"}
    base.update(kw)
    return types.SimpleNamespace(**base)


# ---------------------------------------------------------------------------------- index
def test_index_writes_typed_rows(kb, monkeypatch):
    fake_engine(monkeypatch, ROW + "\n")
    assert I.cmd_index(args()) == 0
    (row,) = I.read_index()
    assert row["clip_name"] == "mx_Test_Clip"
    assert row["file_id"] == -203655887218126122 and isinstance(row["file_id"], int)
    assert row["length"] == 2.5 and row["frame_rate"] == 30.0
    assert row["loop"] is True


def test_index_refuses_a_duplicate_clip_name(kb, monkeypatch, capsys):
    """Everything downstream is keyed by clip name — `raw/<clip>.json`, `actions/<clip>.json`. Two
    clips sharing one would overwrite each other's dump, and the second measurement would land under
    the first one's name."""
    other = ROW.replace("Mixamo30/mx_Test_Clip.fbx", "Mixamo30/other/mx_Test_Clip.fbx")
    fake_engine(monkeypatch, ROW + "\n" + other + "\n")
    assert I.cmd_index(args()) == 1
    assert "REFUSING" in capsys.readouterr().out
    assert not os.path.exists(I.INDEX)          # and it wrote nothing


def test_index_skips_unitys_transient_preview_clips(kb, monkeypatch):
    assert "__preview__" in I.build_index_csharp("Assets/X")


def test_a_malformed_index_row_is_refused_not_guessed(kb):
    write_index(["mx_Truncated\tonly\ttwo"])
    with pytest.raises(SystemExit):
        I.read_index()


def test_reading_an_index_that_does_not_exist_says_what_to_run(kb):
    with pytest.raises(SystemExit) as e:
        I.read_index()
    assert "index" in str(e.value)


# ------------------------------------------------------------------------------- register
def test_register_fills_the_engine_facts_the_dump_cannot_supply(kb):
    write_index([ROW])
    assert I.cmd_register(args()) == 0
    doc = paths.read_json(os.path.join(paths.ACTIONS_DIR, "mx_Test_Clip.json"))
    assert doc["status"] == "candidate" and doc["action_id"] is None
    assert doc["loop"] is True                      # an importer setting; no pose dump can show it
    assert doc["duration"] == 2.5 and doc["frame_rate"] == 30.0
    assert doc["source_clip"]["clip_name"] == "mx_Test_Clip"
    assert doc["source_clip"]["file_id"] == -203655887218126122
    assert doc["source_clip"]["fbx_or_anim"] == "mx_Test_Clip.fbx"


def test_register_never_forks_a_record_that_already_exists(kb, capsys):
    """Writing a stub over a clip the store already knows would leave two files for one clip — and if
    the existing one is accepted, would silently discard its labels.

    The existing record is deliberately NOT named after its clip: an accepted one is named after its
    action_id (ADR 0016), so a check that went by file name would miss exactly the case that costs
    the most."""
    existing = {"source_clip": {"clip_name": "mx_Test_Clip"}, "action_id": "already_here"}
    paths.write_json(os.path.join(paths.ACTIONS_DIR, "whatever_its_called.json"), existing)
    write_index([ROW])
    assert I.cmd_register(args()) == 0
    assert not os.path.exists(os.path.join(paths.ACTIONS_DIR, "mx_Test_Clip.json"))
    assert "registered 0 new" in capsys.readouterr().out


# --------------------------------------------------------------------------------- sample
def test_sampling_skips_clips_that_already_have_a_dump(kb):
    """The resume rule: an hour-long engine run has to be re-runnable after an interruption."""
    rows = [{"clip_name": "a"}, {"clip_name": "b"}]
    paths.write_text(unity_sampler.raw_path("a"), "{}")
    assert [r["clip_name"] for r in I._needs_sampling(rows, force=False)] == ["b"]
    assert [r["clip_name"] for r in I._needs_sampling(rows, force=True)] == ["a", "b"]


def test_the_failure_list_exists_only_when_there_are_failures(kb, monkeypatch):
    """Its EXISTENCE is the signal. A file that is always there and carries a run timestamp shows as
    modified after every sample run, and `git status` is this KB's drift detector."""
    write_index([ROW])
    I.cmd_register(args())
    monkeypatch.setattr(unity_sampler, "bridge_healthy", lambda *a, **k: True)
    monkeypatch.setattr(unity_sampler, "close_connections", lambda *a, **k: None)

    monkeypatch.setattr(unity_sampler, "run_csharp_over_http",
                        lambda *a, **k: (True, "ERROR: CLIP NOT FOUND", None))
    assert I.cmd_sample(args(limit=None, force=False, retry_failed=False)) == 1
    assert os.path.exists(I.FAILED)
    assert "mx_Test_Clip" in open(I.FAILED, encoding="utf-8").read()

    monkeypatch.setattr(unity_sampler, "run_csharp_over_http",
                        lambda *a, **k: (True, json.dumps(held_pose_dump()), None))
    assert I.cmd_sample(args(limit=None, force=True, retry_failed=False)) == 0
    assert not os.path.exists(I.FAILED)


def test_retry_failed_runs_only_the_names_in_the_list(kb, monkeypatch):
    write_index([ROW, ROW.replace("mx_Test_Clip", "mx_Other_Clip")])
    I.cmd_register(args())
    paths.write_text(I.FAILED, "# header\nmx_Other_Clip\n")
    seen = []

    def fake(cs, **kw):
        seen.append("mx_Other_Clip" if "mx_Other_Clip" in cs else "mx_Test_Clip")
        return (True, json.dumps(held_pose_dump()), None)

    monkeypatch.setattr(unity_sampler, "bridge_healthy", lambda *a, **k: True)
    monkeypatch.setattr(unity_sampler, "close_connections", lambda *a, **k: None)
    monkeypatch.setattr(unity_sampler, "run_csharp_over_http", fake)
    assert I.cmd_sample(args(limit=None, force=False, retry_failed=True)) == 0
    assert seen == ["mx_Other_Clip"]


# -------------------------------------------------------------------------------- measure
def test_measure_uses_extracts_own_kinematic_block(kb):
    """Not 'produces the same thing as' — IS the same function. Two paths into one store must not be
    able to drift into two dialects of one contract."""
    assert I.extract._apply_kinematic is extract._apply_kinematic
    assert I.extract._build_extraction is extract._build_extraction


def test_measure_reports_a_clip_it_cannot_measure_rather_than_dropping_it(kb):
    write_index([ROW])
    I.cmd_register(args())
    assert I.cmd_measure(args()) == 0               # no dump yet is not an error
    report = open(I.REPORT, encoding="utf-8").read()
    assert "| awaiting a dump (`sample`) | 1 |" in report
    assert "| measured | 0 |" in report


def test_measure_counts_pose_assets_instead_of_silently_dropping_them(kb):
    """Mixamo ships pose assets that resolve to 2 frames. They measure honestly and are kept, because
    a pose is a legitimate thing to retrieve. But they are counted: 'records where nothing moves' is a
    fact about the corpus a reader should not have to rediscover."""
    write_index([ROW])
    I.cmd_register(args())
    paths.write_text(unity_sampler.raw_path("mx_Test_Clip"), json.dumps(held_pose_dump()))
    assert I.cmd_measure(args()) == 0
    report = open(I.REPORT, encoding="utf-8").read()
    assert "| measured | 1 |" in report
    assert "| sampled at 2 frames (Mixamo pose assets) | 1 |" in report
    assert "| every channel static | 1 |" in report
    assert "`mx_Test_Clip`" in report

    doc = paths.read_json(os.path.join(paths.ACTIONS_DIR, "mx_Test_Clip.json"))
    assert doc["channels"]["torso"]["state_label"] == "static"
    assert doc["channels"]["torso"]["role"] is None          # measured, not labelled
    assert doc["channels"]["torso"]["mean_pose"]              # the pose itself, not a label for it
    assert doc["channels"]["root"]["mean_body_height"] is not None
    assert doc["schema_version"] == C.SCHEMA_VERSION
    assert doc["extraction"]["metric_formula_version"] == C.FORMULA_VERSION


def test_two_frames_and_all_static_are_counted_as_the_different_facts_they_are(kb):
    """They were one count until the corpus produced a counterexample. `mx_Arms_Supporting` is 2
    frames whose poses DIFFER, so its right leg measures 0.0638 and dynamic — short is not still, and
    a report that conflates them is wrong about exactly one clip in 128, which is the hardest kind of
    wrong to notice."""
    write_index([ROW])
    I.cmd_register(args())
    dump = held_pose_dump()
    # Hold every channel except one leg, and move that: a 2-frame clip that is not a held pose.
    moved = [v + 0.3 for v in dump["muscles"][0]]
    dump["muscles"] = [dump["muscles"][0], moved]
    paths.write_text(unity_sampler.raw_path("mx_Test_Clip"), json.dumps(dump))
    assert I.cmd_measure(args()) == 0

    doc = paths.read_json(os.path.join(paths.ACTIONS_DIR, "mx_Test_Clip.json"))
    assert any(doc["channels"][c]["state_label"] == "dynamic" for c in C.STATE_CHANNELS)
    report = open(I.REPORT, encoding="utf-8").read()
    assert "| sampled at 2 frames (Mixamo pose assets) | 1 |" in report
    assert "| every channel static | 0 |" in report


def test_the_report_says_that_nothing_declares_itself_looping(kb):
    """`loop` is an importer setting, not a measurement. The corpus was imported without it, so every
    record reads false — accurate about what was declared, silent about what actually cycles, and
    load-bearing for seam search. The report says so rather than leaving it to be discovered."""
    write_index([ROW.replace("\t30\ttrue", "\t30\tfalse")])
    I.cmd_register(args())
    paths.write_text(unity_sampler.raw_path("mx_Test_Clip"), json.dumps(held_pose_dump()))
    assert I.cmd_measure(args()) == 0
    report = open(I.REPORT, encoding="utf-8").read()
    assert "| declared looping | 0 |" in report
    assert "Not one clip is declared looping" in report
