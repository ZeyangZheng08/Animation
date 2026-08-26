"""test_contract.py — "measured but not labelled" is a state the contract holds, and only that state.

ADR 0014 moved the requirement for the SEMANTIC half out of the JSON Schema and onto `status`. These
are written against the two ways that can go wrong: the schema staying strict enough that a legitimate
measured-only record reads as malformed, or the invariant going soft enough that an unlabelled record
can be accepted. Both are checked against a REAL accepted record rather than a hand-built dict, so a
future schema field cannot make them vacuously pass.

WHAT THE SEMANTIC HALF IS NOW. Through v3 it was a five-field label tuple per channel plus
`display_name`, `tags`, `overall_intent`, `mask_coverage`, `ik_goals` and `composability`, and the
gate cross-checked those against each other. motionkb/v4 deletes all of it (ADR 0022) and leaves
prose: an `action_description` and eight `motion_description`s. Prose has nothing to contradict, so
the only thing left to be wrong is a HOLE — a channel nobody described, or an empty string standing in
for a sentence — and a record accepted with one is a record retrieval silently cannot see.
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
    """A real accepted record — measured, described, and known to pass the whole gate."""
    return paths.read_json(os.path.join(paths.ACTIONS_DIR, "walking.json"))


def check(doc, schema):
    errors = []
    V.validate_shape(doc, schema, schema, "$", errors)
    V.validate_invariants(doc, errors, None)
    return errors


def unlabelled(doc):
    """The same record with its semantic half back to what `register` writes.

    Two fields and eight sentences, where v3 had to blank a five-key tuple per channel plus four
    top-level fields. That shrinkage is the contract change, not a simplification of the fixture.
    """
    d = copy.deepcopy(doc)
    d["status"] = "candidate"
    d["action_id"] = d["action_description"] = None
    for name, fact in d["channels"].items():
        if "motion_description" in fact:
            fact["motion_description"] = None
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


@pytest.mark.parametrize("field", ["action_id", "action_description"])
def test_an_accepted_record_may_not_have_a_null_semantic_field(accepted, schema, field):
    d = copy.deepcopy(accepted)
    d[field] = None
    errors = check(d, schema)
    assert [e for e in errors if e.startswith(field)], errors


def test_an_accepted_record_may_not_have_a_channel_nobody_described(accepted, schema):
    """The hole the completeness gate exists for. A missing sentence is not a schema violation --
    the field is nullable, because a candidate legitimately has none -- so nothing but this catches a
    record that was accepted with one part of the body undescribed."""
    d = copy.deepcopy(accepted)
    d["channels"]["left_hand"]["motion_description"] = None
    assert [e for e in check(d, schema) if "left_hand" in e and "motion_description" in e]


def test_a_blank_description_is_not_a_description(accepted, schema):
    """An empty string passes every type check there is and says nothing. It is what a model writes
    when it has no answer, so it is refused where a null is refused."""
    d = copy.deepcopy(accepted)
    d["action_description"] = "   "
    d["channels"]["head"]["motion_description"] = ""
    errors = check(d, schema)
    assert any(e.startswith("action_description") and "blank" in e for e in errors)
    assert any("channels.head" in e and "blank" in e for e in errors)


def test_a_record_with_no_status_is_held_to_the_full_bar(accepted, schema):
    """Fail-closed. Only an explicit 'candidate' is exempt — absence of a claim is not a claim.

    Ten, because that is what the v4 semantic half is: `action_id`, `action_description`, and one
    `motion_description` for each of the eight anatomical channels. It was four under v3 (three
    fields plus `tags`), and the count is spelled out rather than derived so that a field quietly
    dropping out of the gate fails here instead of passing.
    """
    d = unlabelled(accepted)
    d.pop("status")
    errors = check(d, schema)
    assert len(errors) == 2 + len(V.ANATOMICAL_CHANNELS) == 10, errors
