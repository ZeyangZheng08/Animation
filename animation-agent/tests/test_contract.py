"""test_contract.py — "measured but not labelled" is a state the contract holds, and only that state.

ADR 0014 moved the requirement for the SEMANTIC half out of the JSON Schema and onto `status`. These
are written against the two ways that can go wrong: the schema staying strict enough that a legitimate
measured-only record reads as malformed, or the invariant going soft enough that an unlabelled record
can be accepted. Both are checked against a REAL accepted record rather than a hand-built dict, so a
future schema field cannot make them vacuously pass.
"""
import copy
import json
import os

import pytest

import paths
import validate_motionkb as V


@pytest.fixture(scope="module")
def schema():
    with open(V.SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def accepted():
    """A real accepted record — measured, labelled, and known to pass the whole gate."""
    return paths.read_json(os.path.join(paths.ACTIONS_DIR, "walking.json"))


def check(doc, schema):
    errors = []
    V.validate_shape(doc, schema, schema, "$", errors)
    V.validate_invariants(doc, errors, None)
    return errors


def unlabelled(doc):
    """The same record with its semantic half back to what `register` writes."""
    d = copy.deepcopy(doc)
    d["status"] = "candidate"
    d["action_id"] = d["display_name"] = d["overall_intent"] = None
    d["tags"] = []
    for name, fact in d["channels"].items():
        for k in ("role", "motion_type", "contact", "constraint", "target", "motion_description"):
            if k in fact:
                fact[k] = None
    return d


def test_the_accepted_record_this_is_all_measured_against_still_passes(accepted, schema):
    assert check(accepted, schema) == []


def test_a_measured_but_unlabelled_candidate_is_valid(accepted, schema):
    assert check(unlabelled(accepted), schema) == []


def test_the_measured_half_is_still_required_of_a_candidate(accepted, schema):
    """Nullable applies to the SEMANTIC half only. A record with no measurements is not 'pending',
    it is unextracted, and the schema is right to refuse it."""
    d = unlabelled(accepted)
    d["channels"]["torso"] = {}
    d["extraction"] = {}
    errors = check(d, schema)
    assert any("channels.torso" in e and "state_label" in e for e in errors)
    assert any("extraction" in e and "metric_formula_version" in e for e in errors)


@pytest.mark.parametrize("field", ["action_id", "display_name", "overall_intent"])
def test_an_accepted_record_may_not_have_a_null_semantic_field(accepted, schema, field):
    d = copy.deepcopy(accepted)
    d[field] = None
    errors = check(d, schema)
    assert [e for e in errors if e.startswith(field)], errors


def test_an_accepted_record_may_not_have_empty_tags(accepted, schema):
    d = copy.deepcopy(accepted)
    d["tags"] = []
    assert [e for e in check(d, schema) if e.startswith("tags")]


def test_a_record_with_no_status_is_held_to_the_full_bar(accepted, schema):
    """Fail-closed. Only an explicit 'candidate' is exempt — absence of a claim is not a claim."""
    d = unlabelled(accepted)
    d.pop("status")
    errors = check(d, schema)
    assert len(errors) == 4, errors      # the three fields plus tags


def test_placeholder_composability_does_not_warn_on_an_unlabelled_record(accepted):
    """`soft_warnings` compares two semantic declarations against a measured fact. On a fresh stub both
    are register-time placeholders, so it would report the placeholder, not a disagreement — 24k lines
    of it across the corpus."""
    d = unlabelled(accepted)
    d["mask_coverage"] = {"upper_body": False, "hands": False, "lower_body": False}
    d["composability"]["locks"], d["composability"]["free"] = [], sorted(V.PARTITION_CHANNELS)
    assert any(f.get("state_label") == "dynamic" for f in d["channels"].values())   # there IS something to warn about
    warns = []
    V.soft_warnings(d, warns)
    assert warns == []


def test_the_same_placeholder_does_warn_once_something_has_been_labelled(accepted):
    d = unlabelled(accepted)
    d["mask_coverage"] = {"upper_body": False, "hands": False, "lower_body": False}
    d["composability"]["locks"], d["composability"]["free"] = [], sorted(V.PARTITION_CHANNELS)
    d["channels"]["left_leg"]["role"] = "primary"        # somebody claimed something
    warns = []
    V.soft_warnings(d, warns)
    assert any("mask_coverage.lower_body" in w for w in warns)
    assert any("composability lists it free" in w for w in warns)
