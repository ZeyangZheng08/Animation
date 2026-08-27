"""
propose.py — the VLM-PROPOSE half of the MotionKB pipeline (ADR 0008), engine-decoupled.

`extract.py render <clip>` saves the eight-view ring; this module builds the prompt, asks the VLM to
PROPOSE the record's DESCRIPTIONS from those frames, runs the same completeness gate the validator
uses (validate_motionkb.validate_descriptions), and writes the sentences back into the record.
KINEMATIC is never touched (ADR 0002).

What the model writes is nine sentences: one `action_description` for the clip and one
`motion_description` per anatomical channel. Through v3 it also proposed a five-field label tuple per
channel (role / motion_type / contact / constraint / target), `mask_coverage`, and the composability
judgement calls, and the program derived `locks`/`free`/`seam_owner`/`ik_goals` from them. All of that
is gone (ADR 0022): each one was a decision about how this clip COMBINES with something else, and a
clip previewed alone on an empty floor is the worst possible place from which to make it.

Three things changed when the prompt was rewritten for the corpus pass (2026-08-27):

  * **The kinematic block in the prompt is one sentence of `state_label`.** The rule is to hand the
    model only what the pictures cannot establish. `mean_pose` is a vector in normalised muscle space
    whose origin is a coordinate centre rather than a rest pose (ADR 0021), so no describer can read a
    lean off it, while the eight views show the lean directly. Carriage is visible the same way: a
    figure lying down looks like one from every angle in the ring. `motion_magnitude` as a number
    needs a paragraph of anchors before it is readable at all, and buys back a phrase the model would
    only paraphrase. What three sampled moments genuinely cannot separate is a hand held still from a
    hand that trembles — and `state_label` is the field a consumer reads NEXT TO the sentence, so a
    description contradicting it is a defect in the record. That one fact stays; the rest is the
    model's own reading, which also keeps the description independent evidence rather than a
    restatement of the measurement sitting beside it.

  * **The reply is nine labelled lines, not JSON.** The corpus pass is 2446 clips on a local ~27B
    model, and a model that size asked for nested JSON fails in specific ways — a ```json fence, a
    missing channel key, a trailing comma, the `{...}` placeholder copied verbatim — each of which
    costs the whole record. `label: sentence` parses with one partition, and a line the model skipped
    is an absent key, which is exactly the shape the completeness gate already reports.

  * **No `action_id`.** Dozens of corpus clips are walk variants that would collide on one. A record
    is keyed by `clip_name` while unlabelled and the gate does not require an id, so naming is left to
    acceptance, which is where a human is looking anyway.

The prompt states no reading order for the frames. Each one is labelled with its angle and its time
in the manifest, which is what the model needs; a sentence tracing the ring round the figure was
narration on top of that, and it asserted a sort order this module could not enforce. Frames are
still sorted into ring order here so one angle's moments arrive together, but nothing in the prompt
depends on it.
"""
import datetime
import json
import os
import sys
import textwrap

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

# Fields the VLM proposes: one per anatomical channel, plus the one at the top level.
SEMANTIC_CH_KEYS = ("motion_description",)
TOP_SEMANTIC = ("action_description",)

# Provenance, audited in extraction.field_origin. There is no `derived` tier any more: the fields the
# program used to derive from a proposal (composability.locks/free/seam_owner, ik_goals) do not exist.
VLM_PROPOSED_FIELDS = ["action_description", "channels.*.motion_description"]

# The labels the reply carries, in the order the prompt asks for them.
REPLY_LABELS = ("action",) + tuple(C.ANATOMICAL_CHANNELS)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def frame_ordinal(path):
    """The `_t<n>` ordinal, or "0" for a pre-ordinal name. Which MOMENT of the clip this frame is."""
    stem = os.path.basename(path).rsplit(".", 1)[0]
    head, _, _pct = stem.rpartition("_f")
    _view, sep, ordinal = head.rpartition("_t")
    return ordinal if (sep and ordinal.isdigit()) else "0"


def frame_sort_key(path):
    """Ring order first, then time within the angle.

    Sorted by NAME the eight views interleave alphabetically — back, back_left, back_right, front,
    front_left, front_right, left, right — which separates neighbouring angles and puts `right` last.
    Ring order keeps one angle's moments together in the attached sequence. A view the ring does not
    name (an older frame set) sorts after all of them."""
    view, pct = split_frame_name(path)
    names = unity_sampler.VIEW_RING_NAMES
    vi = names.index(view) if view in names else len(names)
    return (vi, view, int(frame_ordinal(path)), int(pct) if pct.isdigit() else 0)


def _frame_manifest(frames):
    """One line per attached image: which angle, how far through the clip.

    Without it the model gets a bag of pictures with no way to tell a camera move from a body move,
    which is most of what these frames are supposed to show.
    """
    if not frames:
        return "  (frame order not recorded)"
    out = []
    for i, f in enumerate(frames, 1):
        view, pct = split_frame_name(f)
        pretty = view.replace("_", " ") if view else os.path.basename(f)
        out.append("  frame %d: %s, %s%% through the clip" % (i, pretty, pct or "?"))
    return "\n".join(out)


def _state_line(doc):
    """One sentence naming which anatomical channels move and which hold still.

    The only kinematic fact in the prompt — see the module docstring for why it is the only one.
    """
    ch = doc.get("channels", {}) or {}
    moving = [c for c in C.ANATOMICAL_CHANNELS
              if (ch.get(c) or {}).get("state_label") == "dynamic"]
    still = [c for c in C.ANATOMICAL_CHANNELS if c not in moving]

    def listing(names):
        if len(names) == 1:
            return names[0]
        return ", ".join(names[:-1]) + " and " + names[-1]

    if moving and still:
        s = "Measured over every frame: %s move; %s hold still." % (listing(moving), listing(still))
    elif moving:
        s = "Measured over every frame: every part moves."
    else:
        s = "Measured over every frame: every part holds still, so the clip is one held pose."
    # Nine channel names in one sentence runs past 130 characters, which is only a problem for the
    # human auditing the prompt — but that is who reads it before 2446 clips go through it.
    return "\n".join(textwrap.wrap(s, 98))


PROMPT = """\
Describe one animation clip, body part by body part, for a motion knowledge base.

THE FRAMES
%(nframes)d frames: %(nmoments)d moments of the clip, each shot from eight angles 45 degrees apart
around the figure. Each frame below carries its angle and its time in the clip. Frames sharing a
percentage are one pose seen from several sides, so read all eight before you place a hand or say
which way the head faces. The camera holds the same distance and height throughout, so a change in
the figure's size or position between moments is the body moving.

%(frames)s

THE FIGURE
One untextured mannequin alone on an empty floor, hands empty. Every clip renders this way whatever
it depicts, so a figure gripping a bottle or leaning on a bed comes out empty-handed on the same
bare floor. Describe the movement the pose shows.

The frames are the evidence. Use the clip name where the frames agree with it.

Clip name: %(clip_name)s
%(state_line)s

WHAT TO WRITE
Nine lines, each opening with its label and a colon, in this order:

  action: one sentence for what the whole action looks like.
  torso: one short sentence for what this part does and the pose it moves through.
  head:
  left_arm:
  right_arm:
  left_leg:
  right_leg:
  left_hand:
  right_hand:

A part that holds still gets a sentence too: say what pose it holds. Keep each sentence to that part
alone. Write the nine lines and stop.
"""


def build_prompt(doc, clip_name, frames=None):
    """Returns (frames_in_ring_order, prompt). Sorting happens here so one angle's moments arrive
    together; the prompt makes no claim about that order, so a caller passing frames in some other
    order gets a prompt that is still true."""
    frames = sorted(frames or [], key=frame_sort_key)
    moments = len({frame_ordinal(f) for f in frames}) or 1
    return frames, PROMPT % {
        "nframes": len(frames),
        "nmoments": moments,
        "frames": _frame_manifest(frames),
        "clip_name": clip_name,
        "state_line": _state_line(doc),
    }


def parse_reply(text):
    """The labelled lines back into {label: sentence}.

    Tolerates a code fence, blank lines, a leading bullet or hash, and a label written with spaces or
    capitals instead of the channel key. A label the model never wrote is simply absent, which is the
    shape the completeness gate already reports on. First occurrence wins, so a model that restates
    its answer underneath does not overwrite it with a summary.
    """
    out = {}
    for line in (text or "").splitlines():
        line = line.strip().lstrip("-*#> ").strip().strip("`")
        label, sep, sentence = line.partition(":")
        label = label.strip().strip("*_").lower().replace(" ", "_")
        # A model that bolds the label writes `**action:** ...`, which leaves the closing marker at
        # the head of the sentence once the colon is partitioned away.
        sentence = sentence.strip().lstrip("*_ ").strip()
        if sep and label in REPLY_LABELS and sentence:
            out.setdefault(label, sentence)
    return out


def merge_proposal(doc, parsed):
    """Overlay the parsed sentences onto a copy of the source doc. KINEMATIC, source_clip,
    controller_* and action_id are untouched — naming is acceptance's job, not the describer's."""
    cand = json.loads(json.dumps(doc))  # deep copy
    if parsed.get("action"):
        cand["action_description"] = parsed["action"]
    for c in C.ANATOMICAL_CHANNELS:
        if parsed.get(c):
            cand["channels"].setdefault(c, {})["motion_description"] = parsed[c]
    cand["status"] = "candidate"
    return cand


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
    """Describe one clip: VLM writes the lines -> completeness gate (with retry) -> the sentences
    written back into the record. Returns (record_path, errors, warns).

    The retry loop has one thing left to catch: a reply that did not cover every channel. There is no
    vocabulary to violate and no cross-field rule to contradict, and since the reply is lines rather
    than JSON there is no parse to fail either — a malformed line is a missing label, which the gate
    already names.
    """
    doc = json.load(open(source_doc_path, encoding="utf-8"))
    frames_dir = os.path.join(paths.FRAMES_DIR, clip_name)
    frames = unity_sampler.frame_paths(frames_dir)
    if not frames:
        raise RuntimeError("no frames at %s — run `python extract.py render %s` first"
                           % (frames_dir, clip_name))
    api_key = vlm.load_api_key(REPO_ROOT)
    frames, base_prompt = build_prompt(doc, clip_name, frames)
    feedback = ""
    cand = errors = warns = None
    for attempt in range(retries + 1):
        text, _usage = vlm.describe(api_key, base_prompt + feedback, frames)
        cand = merge_proposal(doc, parse_reply(text))
        errors, warns = [], []
        V.validate_descriptions(cand, errors, warns)
        if not errors:
            break
        feedback = ("\n\nYour previous answer was incomplete. Send the nine lines again with all of "
                    "these filled in:\n- " + "\n- ".join(errors))
    _stamp_provenance(cand, frames, not errors)
    # Written back over the record it came from. One store (ADR 0016) means proposing fills the
    # semantic half of the record in place; there is no second file to reconcile, and promotion is
    # the rename that follows.
    cand_path = paths.write_json(source_doc_path, cand)
    return cand_path, errors, warns
