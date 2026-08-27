"""
propose.py — the VLM-PROPOSE half of the MotionKB pipeline (ADR 0008), engine-decoupled.

`extract.py render <clip>` saves multi-angle frames; this module builds the prompt, asks the VLM to
PROPOSE the record's DESCRIPTIONS from those frames + the KINEMATIC facts, runs the same completeness
gate the validator uses (validate_motionkb.validate_descriptions), and writes a candidate. KINEMATIC
is never touched (ADR 0002).

What the model proposes is now exactly three kinds of text: an `action_id`, one `action_description`
for the clip, and one `motion_description` per anatomical channel. Through v3 it also proposed a
five-field label tuple per channel (role / motion_type / contact / constraint / target),
`mask_coverage`, and the composability judgement calls, and the program derived `locks`/`free`/
`seam_owner`/`ik_goals` from them. All of that is gone (ADR 0022): each one was a decision about how
this clip COMBINES with something else, and a clip previewed alone on an empty floor is the worst
possible place from which to make it. The runtime agent makes those calls with the task and the scene
in front of it.

That also removes the whole constrained-vocabulary apparatus — the enums, the cross-field rules the
proposal had to satisfy, the self-correction retry loop that fed violations back. Prose has no enum
to violate. What is left to check is that the model answered at all, for every channel, which is what
the gate below does.
"""
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = HERE                               # this repo's root; key.env lives here, not with the KB
sys.path.insert(0, HERE)                       # config, paths, the vlm client, unity_sampler, validate_motionkb
import config as C            # noqa: E402
import paths                  # noqa: E402
import unity_sampler          # noqa: E402
import validate_motionkb as V # noqa: E402

# WHICH VLM. ADR 0008 constrains what a proposal must satisfy, not who produces it, and
# `extraction.vlm_proposal.model` records which model actually proposed. So the provider is one
# import, chosen here rather than branched at each call site. Set MOTIONKB_VLM=openai for gpt-5.5.
if os.environ.get("MOTIONKB_VLM", "anthropic").strip().lower() == "openai":
    import vlm_openai as vlm  # noqa: E402
else:
    import vlm_anthropic as vlm  # noqa: E402

KB_DIR = paths.KB_DIR                          # see paths.py / MOTIONKB_DIR
VLM_MODEL = vlm.MODEL

# Fields the VLM proposes: one per anatomical channel, plus the two at the top level.
SEMANTIC_CH_KEYS = ("motion_description",)
TOP_SEMANTIC = ("action_id", "action_description")

# Provenance, audited in extraction.field_origin. There is no `derived` tier any more: the fields the
# program used to derive from a proposal (composability.locks/free/seam_owner, ik_goals) do not exist.
VLM_PROPOSED_FIELDS = ["action_id", "action_description", "channels.*.motion_description"]


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _kinematic_summary(doc):
    """Compact per-channel kinematic facts the VLM must stay consistent with.

    Two lines per channel: what it MOVES (state + magnitude) and what pose it sits in (the mean of
    each of its muscle degrees of freedom, or the body's carriage on the root). The pose is given as
    the numbers it is — there is no neutral/displaced label to hand over any more, and inventing one
    for the prompt would be inventing a fact the store does not hold (ADR 0021).
    """
    ch = doc.get("channels", {})
    lines = []
    for c in C.STATE_CHANNELS:
        f = ch.get(c) or {}
        lines.append("  %-11s state=%-7s magnitude=%-6s"
                     % (c, f.get("state_label"), f.get("motion_magnitude")))
        pose = f.get("mean_pose")
        if isinstance(pose, dict) and pose:
            lines.append("              mean pose: "
                         + ", ".join("%s=%.2f" % (dof, v) for dof, v in pose.items()))
        elif f.get("mean_body_height") is not None:
            lines.append("              mean carriage: height=%s tilt_deg=%s"
                         % (f.get("mean_body_height"), f.get("mean_body_tilt_deg")))
    return "\n".join(lines)


def split_frame_name(path):
    """(view, percent) from a rendered frame's filename. The name is `<view>_t<ordinal>_f<pct>.jpg`;
    the ordinal is what makes it unique and sortable once frames are chosen by pose rather than by a
    fixed fraction (two can land in the same whole percent of a long clip, and "_f21" sorts before
    "_f5"). Frames rendered before the ordinal existed carry only the percent, so both forms are read
    here rather than making a stale review frame unreadable."""
    stem = os.path.basename(path).rsplit(".", 1)[0]
    head, _, pct = stem.rpartition("_f")
    view, sep, ordinal = head.rpartition("_t")
    if not (sep and ordinal.isdigit()):
        view = head                                   # pre-ordinal name
    return view, pct


def frame_sort_key(path):
    """Ring order first, then time within the angle.

    Sorted by NAME the eight views interleave alphabetically — back, back_left, back_right, front,
    front_left, front_right, left, right — which separates neighbouring angles and puts `right` last.
    The attached order is what the prompt tells the model to read the pictures in, so it should be the
    order the camera actually goes round: front, turning toward the avatar's right, round the back, back
    to front_left. A view the ring does not name (an older frame set) sorts after all of them."""
    stem = os.path.basename(path).rsplit(".", 1)[0]
    head, _, pct = stem.rpartition("_f")
    view, sep, ordinal = head.rpartition("_t")
    if not (sep and ordinal.isdigit()):
        view, ordinal = head, "0"
    names = unity_sampler.VIEW_RING_NAMES
    vi = names.index(view) if view in names else len(names)
    return (vi, view, int(ordinal), int(pct) if pct.isdigit() else 0)


def _frame_manifest(frames):
    """One line per attached image: which angle, how far through the clip.

    Without it the model gets a bag of pictures with no stated order and no way to tell a camera
    move from a body move, which is most of what these frames are supposed to show.
    """
    if not frames:
        return "  (frame order not recorded)"
    out = []
    for i, f in enumerate(frames, 1):
        view, pct = split_frame_name(f)
        pretty = view.replace("_", " ") if view else os.path.basename(f)
        out.append("  frame %d: %s view, %s%% through the clip" % (i, pretty, pct or "?"))
    return "\n".join(out)


def build_prompt(doc, clip_name, frames=None):
    existing_aid = doc.get("action_id")
    frame_lines = _frame_manifest(frames)
    return (
        "You are DESCRIBING one animation clip at the BODY-PART level for a motion knowledge base.\n"
        "You are shown the SAME few moments of it from EIGHT camera angles, 45 degrees apart, all the way\n"
        "round the figure — front, then turning toward the figure's own right, round the back, and home.\n"
        "The frames are listed below in the order they are attached, each labelled with its angle and how\n"
        "far through the clip it was taken: read them angle by angle, and read the times within one angle\n"
        "as a sequence. Two frames with the same percentage are ONE pose seen from two sides, not two\n"
        "poses, so use the far side to settle what a near view hides — whether a hand is in front of the\n"
        "body or behind it, which way the head turns, whether the feet are together or apart. Every frame\n"
        "uses the same camera distance and height, so a change in the figure's size or position between\n"
        "frames at different times is real movement, not framing. You do\n"
        "NOT set any numbers — those are already measured, and they are given to you below.\n"
        "THE SETTING IS NOT SHOWN. Every clip is previewed on one shared untextured mannequin, alone on an\n"
        "empty floor: no costume, no scene, and NO PROPS — a figure holding a bottle or pressing a monitor\n"
        "is rendered with empty hands against nothing. So the picture tells you the MOVEMENT and nothing\n"
        "about where it happens or what it touches. This library holds general motion — locomotion, sport,\n"
        "combat, dance, everyday gesture — alongside nursing tasks. Describe the movement you actually see;\n"
        "do not reach for a clinical reading the movement does not support, and do not infer an object\n"
        "from the clip name that the pose does not itself show.\n"
        "Describe WHAT THE BODY DOES, not what it is for. Do not say which part is the important one, what\n"
        "another animation could or could not override, or what the hands must stay attached to: those are\n"
        "not properties of this clip and nothing downstream will read them from you.\n\n"
        "Frames attached, in order:\n%s\n\n"
        "Clip name (asset): %s\n"
        "%s\n\n"
        "KINEMATIC facts per channel (these are FIXED — your descriptions must agree with them):\n%s\n\n"
        "Propose exactly these fields:\n"
        "  action_id    : a short lower_snake_case verb-phrase label for the action (e.g. 'check_pulse').\n"
        "  action_description : ONE sentence describing what the whole action looks like.\n"
        "  channels     : for each of the 8 anatomical channels (torso, head, left_arm, right_arm,\n"
        "                 left_leg, right_leg, left_hand, right_hand) — NOT root — one short\n"
        "                 plain-language sentence, `motion_description`, saying what that part does.\n"
        "                 A part measured `static` is still described: say what pose it holds.\n\n"
        "Return STRICT JSON only, shape:\n"
        "{\"action_id\":\"...\",\"action_description\":\"...\",\n"
        " \"channels\":{\"torso\":{\"motion_description\":\"...\"}, ... all 8 anatomical channels ...}}\n"
        % (frame_lines, clip_name,
           ("Current action_id (keep unless clearly wrong): %s" % existing_aid) if existing_aid else
           "No action_id yet — propose one.",
           _kinematic_summary(doc)))


def merge_proposal(doc, proposal, keep_action_id=True):
    """Overlay the VLM's descriptions onto a copy of the source doc. KINEMATIC, source_clip and
    controller_* are untouched. Returns (candidate_doc, action_id)."""
    cand = json.loads(json.dumps(doc))  # deep copy
    pch = proposal.get("channels", {}) or {}
    for c in C.ANATOMICAL_CHANNELS:
        src = pch.get(c) or {}
        dst = cand["channels"].setdefault(c, {})
        for k in SEMANTIC_CH_KEYS:
            if k in src:
                dst[k] = src[k]
    proposed_aid = proposal.get("action_id")
    if not (keep_action_id and cand.get("action_id")):
        cand["action_id"] = proposed_aid
    if proposal.get("action_description") is not None:
        cand["action_description"] = proposal["action_description"]
    cand["status"] = "candidate"
    return cand, proposed_aid


def _stamp_provenance(cand, frames, gate_ok):
    ex = cand.get("extraction", {}) or {}
    ex["vlm_proposal"] = {
        "model": VLM_MODEL,
        "proposed_at": _now(),
        "frames": len(frames),
        "render_views": sorted({split_frame_name(f)[0] for f in frames}),
        "consistency_validated": bool(gate_ok),
        "scope": list(VLM_PROPOSED_FIELDS),
        "status": "awaiting_human_accept",
    }
    fo = ex.setdefault("field_origin", {})
    fo["vlm_proposed"] = list(VLM_PROPOSED_FIELDS)
    fo.pop("derived", None)                    # nothing is derived from a proposal any more
    # The descriptions have just been filled, so they move out of the pending tier.
    if gate_ok:
        fo.pop("semantic_pending", None)
        fo["semantic"] = ["action_description", "channels.*.motion_description"]
    ex["verified_against_screenshots"] = False
    cand["extraction"] = ex


def propose_clip(clip_name, source_doc_path, retries=2):
    """Render-free proposal for one clip: VLM proposes -> completeness gate (with retry) -> the
    descriptions written back into the record. Returns (record_path, errors, warns, action_id).

    The retry loop stays, but there is one thing left for it to catch: a proposal that did not cover
    every channel. There is no longer a vocabulary to violate or a cross-field rule to contradict.
    """
    doc = json.load(open(source_doc_path, encoding="utf-8"))
    frames_dir = os.path.join(paths.FRAMES_DIR, clip_name)
    frames = sorted(unity_sampler.frame_paths(frames_dir), key=frame_sort_key)
    if not frames:
        raise RuntimeError("no frames at %s — run `python extract.py render %s` first"
                           % (frames_dir, clip_name))
    api_key = vlm.load_api_key(REPO_ROOT)
    base_prompt = build_prompt(doc, clip_name, frames)
    feedback = ""
    cand = errors = warns = proposed_aid = None
    for attempt in range(retries + 1):
        proposal, _usage = vlm.propose(api_key, base_prompt + feedback, frames)
        cand, proposed_aid = merge_proposal(doc, proposal)
        errors, warns = [], []
        V.validate_descriptions(cand, errors, warns)
        if not errors:
            break
        feedback = ("\n\nYour previous JSON was INCOMPLETE. Return corrected JSON that fixes ALL of "
                    "these:\n- " + "\n- ".join(errors))
    _stamp_provenance(cand, frames, not errors)
    # Written back over the record it came from. One store (ADR 0016) means proposing fills the
    # semantic half of the record in place; there is no second file to reconcile, and promotion is
    # the rename that follows.
    cand_path = paths.write_json(source_doc_path, cand)
    return cand_path, errors, warns, proposed_aid
