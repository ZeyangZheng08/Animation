"""What the describer is asked, and what comes back.

The prompt is the whole interface to a model that is about to write nine sentences for each of 2446
clips, so the claims worth pinning are the ones a silent change would break: that the only kinematic
fact handed over is which parts move, that the prompt asserts nothing about the frames it was not
given, and that a reply arriving in the shape a smaller model actually produces still parses.
"""
import config as C
import propose


def _doc(dynamic=("torso", "left_leg", "right_leg"), action_id=None):
    channels = {"root": {"state_label": "dynamic", "motion_magnitude": 0.1,
                         "mean_body_height": 0.95, "mean_body_tilt_deg": 4.0}}
    for c in C.ANATOMICAL_CHANNELS:
        channels[c] = {"state_label": "dynamic" if c in dynamic else "static",
                       "motion_magnitude": 0.4 if c in dynamic else 0.0,
                       "mean_pose": {"Spine Front-Back": -0.36},
                       "motion_description": None}
    return {"action_id": action_id, "action_description": None, "channels": channels}


def _frames(views=None, moments=3):
    views = views or list(propose.unity_sampler.VIEW_RING_NAMES)
    return ["/k/frames/c/%s_t%d_f%d.jpg" % (v, i, 10 + i * 30)
            for v in views for i in range(moments)]


# --------------------------------------------------------------- the kinematic half of the prompt
def test_the_only_measurement_in_the_prompt_is_which_parts_move():
    """`mean_pose` is a vector in normalised muscle space whose origin is a coordinate centre and not
    a rest pose (ADR 0021), so nothing can be read off it that the eight views do not show better;
    carriage is visible the same way. What three sampled moments genuinely cannot separate is a hand
    held still from a hand that trembles, so `state_label` is the one measurement handed over."""
    _, prompt = propose.build_prompt(_doc(), "mx_Test", _frames())
    # The state sentence is wrapped for the human who audits the prompt before 2446 clips go through
    # it, so read it as the model does rather than as lines.
    flowed = " ".join(prompt.split())
    assert "torso, left_leg and right_leg move" in flowed
    assert "head, left_arm, right_arm, left_hand and right_hand hold still" in flowed
    for leaked in ("Spine Front-Back", "mean_pose", "mean_body_height", "tilt",
                   "motion_magnitude", "0.4"):
        assert leaked not in prompt


def test_a_clip_that_never_moves_is_named_as_a_held_pose():
    _, prompt = propose.build_prompt(_doc(dynamic=()), "mx_Pose", _frames(moments=2))
    assert "every part holds still, so the clip is one held pose" in prompt
    assert "2 moments" in prompt and "16 frames" in prompt


def test_a_clip_that_moves_everywhere_says_so_without_listing_nine_names():
    _, prompt = propose.build_prompt(_doc(dynamic=C.ANATOMICAL_CHANNELS), "mx_All", _frames())
    assert "every part moves" in prompt
    assert "hold still" not in prompt


# --------------------------------------------------------------------- what the prompt claims
def test_the_prompt_claims_no_reading_order_for_the_frames():
    """It used to open by tracing the ring -- front, then turning toward the figure's own right. That
    was narration over the manifest, which labels every frame with its own angle, and it asserted a
    sort order this module could not enforce on a caller. The manifest stayed; the claim went."""
    frames = _frames()
    frames.reverse()
    ordered, prompt = propose.build_prompt(_doc(), "mx_Test", frames)
    assert "turning toward" not in prompt
    assert "in that order" not in prompt
    # Sorted anyway, so one angle's moments arrive together.
    assert propose.split_frame_name(ordered[0])[0] == "front"
    assert [propose.split_frame_name(f)[0] for f in ordered[::3]] == \
        list(propose.unity_sampler.VIEW_RING_NAMES)


def test_the_prompt_asks_for_nothing_it_will_not_store():
    """Nine sentences and no id: dozens of corpus clips are walk variants that would collide on one
    action_id, and a record is keyed by its clip_name until acceptance names it (ADR 0022)."""
    _, prompt = propose.build_prompt(_doc(), "mx_Test", _frames())
    assert "action_id" not in prompt
    assert "JSON" not in prompt
    for c in C.ANATOMICAL_CHANNELS:
        assert "\n  %s:" % c in prompt


def test_the_prompt_gives_instructions_rather_than_prohibitions():
    """A describer follows what it is told to do more reliably than what it is told to avoid, and the
    reasons behind the contract are not the model's to act on."""
    _, prompt = propose.build_prompt(_doc(), "mx_Test", _frames())
    lowered = prompt.lower()
    for negative in ("do not", "don't", "never ", "avoid "):
        assert negative not in lowered


# ------------------------------------------------------------------------------ parsing the reply
def test_nine_labelled_lines_parse():
    reply = "\n".join(["action: She walks forward."] +
                      ["%s: It moves." % c for c in C.ANATOMICAL_CHANNELS])
    parsed = propose.parse_reply(reply)
    assert parsed["action"] == "She walks forward."
    assert all(parsed[c] == "It moves." for c in C.ANATOMICAL_CHANNELS)


def test_a_reply_dressed_up_by_a_smaller_model_still_parses():
    """A fence, a preamble, bullets, bold, a capitalised label with a space in it. None of these is
    hypothetical for a local model, and each would have cost the whole record under a JSON contract."""
    reply = ("Here are the nine lines:\n```\n"
             "- **action:** She kneels and presses down.\n"
             "  torso: It folds forward.\n"
             "# Head: It stays angled down.\n"
             "LEFT ARM: It braces against the floor.\n"
             "right_arm: It braces against the floor.\n"
             "left_leg: It kneels.\n"
             "right_leg: It kneels.\n"
             "left_hand: It rests flat.\n"
             "right_hand: It rests flat.\n```\n")
    parsed = propose.parse_reply(reply)
    assert len(parsed) == 9
    assert parsed["action"] == "She kneels and presses down."
    assert parsed["head"] == "It stays angled down."
    assert parsed["left_arm"] == "It braces against the floor."


def test_a_restated_answer_does_not_overwrite_the_first_one():
    parsed = propose.parse_reply("action: The real sentence.\naction: A summary afterwards.")
    assert parsed["action"] == "The real sentence."


def test_a_skipped_line_is_an_absent_key_rather_than_a_parse_failure():
    """Which is what the completeness gate already reports on, so a partial reply costs one channel
    and a retry rather than the record."""
    reply = "\n".join(["action: She walks."] +
                      ["%s: It moves." % c for c in C.ANATOMICAL_CHANNELS if c != "head"])
    parsed = propose.parse_reply(reply)
    assert "head" not in parsed
    cand = propose.merge_proposal(_doc(), parsed)
    errors, warns = [], []
    import validate_motionkb as V
    V.validate_descriptions(cand, errors, warns)
    assert errors == ["channels.head.motion_description is null"]


# -------------------------------------------------------------------------------- merging it back
def test_merging_writes_the_sentences_and_touches_no_measurement():
    doc = _doc(action_id="walking")
    before = {c: dict(doc["channels"][c]) for c in doc["channels"]}
    parsed = propose.parse_reply("\n".join(["action: She walks."] +
                                           ["%s: It moves." % c for c in C.ANATOMICAL_CHANNELS]))
    cand = propose.merge_proposal(doc, parsed)
    assert cand["action_description"] == "She walks."
    assert cand["action_id"] == "walking"          # describing does not rename
    assert cand["status"] == "candidate"
    assert cand["channels"]["root"] == before["root"]
    for c in C.ANATOMICAL_CHANNELS:
        for k, v in before[c].items():
            if k != "motion_description":
                assert cand["channels"][c][k] == v
    assert doc["channels"]["torso"]["motion_description"] is None   # the source doc is not mutated


def test_provenance_no_longer_claims_an_action_id_was_proposed():
    cand = {}
    propose._stamp_provenance(cand, _frames(views=["front"]), True)
    scope = cand["extraction"]["vlm_proposal"]["scope"]
    assert scope == ["action_description", "channels.*.motion_description"]
    assert cand["extraction"]["field_origin"]["vlm_proposed"] == scope
