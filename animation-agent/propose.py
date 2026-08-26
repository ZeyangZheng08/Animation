"""
propose.py — the VLM-PROPOSE half of the MotionKB pipeline (ADR 0008), engine-decoupled.

`extract.py render <clip>` saves multi-angle frames; this module builds the prompt, asks the VLM
(gpt-5.5-2026-04-23 via vlm_openai) to PROPOSE the SEMANTIC fields from those frames + the
KINEMATIC facts, runs the SAME deterministic consistency gate the validator uses
(validate_motionkb.validate_semantic_consistency) plus the composability invariants, with a
self-correction retry loop, and writes a candidate. KINEMATIC is never touched (ADR 0002).

The model proposes only SEMANTIC/relevance labels (its strength): the per-channel 5-tuple, the action
identity/summary, mask_coverage, and the composability judgement calls (base_or_overlay / posture /
can_overlay_on).
The mechanical parts of composability are DERIVED, not guessed: locks/free fall out of the proposed roles
(free <=> role==free, the exact relation the gate enforces) and seam_owner is a fixed convention.
mask_coverage stays VLM-proposed (it is 'what the clip drives', not 'what it locks' — a neutral base drives
the whole body yet locks nothing, so coverage != ownership). Every number stays KINEMATIC; the gate enforces
agreement with the measured magnitudes / ik_goals / composability before anything is recorded.
"""
import datetime
import glob
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

# WHICH VLM. ADR 0008 constrains what a proposal must satisfy, not who produces it: every field is
# checked against the KINEMATIC block by validate_semantic_consistency before it is recorded, and
#  records which model actually proposed. So the provider is one import, chosen
# here rather than branched at each call site. Set MOTIONKB_VLM=openai to go back to gpt-5.5.
if os.environ.get("MOTIONKB_VLM", "anthropic").strip().lower() == "openai":
    import vlm_openai as vlm  # noqa: E402
else:
    import vlm_anthropic as vlm  # noqa: E402

KB_DIR = paths.KB_DIR                          # see paths.py / MOTIONKB_DIR
VLM_MODEL = vlm.MODEL

# Fields the VLM proposes (per partition channel) + the top-level identity/summary fields.
SEMANTIC_CH_KEYS = ("role", "motion_type", "contact", "constraint", "target", "motion_description")
TOP_SEMANTIC = ("action_id", "display_name", "overall_intent", "tags")

# Provenance: what the VLM proposed vs. what the program derived from the proposal (audited in field_origin).
VLM_PROPOSED_FIELDS = ["action_id", "display_name", "overall_intent", "tags", "mask_coverage",
                       "channels.*.role", "channels.*.motion_type", "channels.*.contact",
                       "channels.*.constraint", "channels.*.motion_description",
                       "composability.base_or_overlay", "composability.posture",
                       "composability.can_overlay_on"]
DERIVED_FIELDS = ["composability.locks", "composability.free", "composability.seam_owner", "ik_goals"]

# hand/foot channel -> IK effector name (for the ik_goals derivation below).
IK_EFFECTOR = {C.LEFT_HAND: "left_hand", C.RIGHT_HAND: "right_hand",
               C.LEFT_LEG: "left_foot", C.RIGHT_LEG: "right_foot"}


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


def _ik_summary(doc):
    gs = doc.get("ik_goals", []) or []
    if not gs:
        return "  (none)"
    return "\n".join("  effector=%s target=%s contact_object=%s constraint=%s"
                     % (g.get("effector"), g.get("target"), g.get("contact_object"), g.get("constraint"))
                     for g in gs)


def _store_base_actions():
    """{action_id: {'locks': set, 'posture': str}} for accepted BASE actions already in the store —
    the candidate pool an overlay's can_overlay_on may name (must be lock-disjoint + posture-compatible)."""
    out = {}
    for p, d, err in paths.read_records(paths.accepted_files()):
        if err:
            continue
        comp = d.get("composability", {}) or {}
        if comp.get("base_or_overlay") == "base" and isinstance(d.get("action_id"), str):
            out[d["action_id"]] = {"locks": set(comp.get("locks", []) or []),
                                   "posture": comp.get("posture", "standing")}
    return out


def split_frame_name(path):
    """(view, percent) from a rendered frame's filename. The name is `<view>_t<ordinal>_f<pct>.png`;
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


def build_prompt(doc, clip_name, bases, frames=None):
    existing_aid = doc.get("action_id")
    frame_lines = _frame_manifest(frames)
    base_lines = "\n".join("  %-14s owns(locks)=%s posture=%s" % (b, sorted(v["locks"]), v["posture"])
                           for b, v in sorted(bases.items())) or "  (none registered yet)"
    return (
        "You are labelling one animation clip at the BODY-PART level for a motion knowledge base.\n"
        "You are shown several rendered frames of it. They are listed below in the order they are attached,\n"
        "each labelled with its camera angle and how far through the clip it was taken, so you can read the\n"
        "movement as a sequence rather than as unrelated poses. Every frame uses the SAME camera setup, so\n"
        "a change in the figure's size or position between frames is real movement, not framing. Judge\n"
        "ONLY the categorical, meaning-level labels from what you see and from the KINEMATIC facts below. You do\n"
        "NOT set any numbers — those are already measured.\n"
        "THE SETTING IS NOT SHOWN. Every clip is previewed on one shared untextured mannequin, alone on an\n"
        "empty floor: no costume, no scene, and NO PROPS — a figure holding a bottle or pressing a monitor\n"
        "is rendered with empty hands against nothing. So the picture tells you the MOVEMENT and nothing\n"
        "about where it happens or what it touches. This library holds general motion — locomotion, sport,\n"
        "combat, dance, everyday gesture — alongside nursing tasks. Label the movement you actually see;\n"
        "do not reach for a clinical reading the movement does not support, and do not infer an object\n"
        "from the clip name that the pose does not itself show.\n\n"
        "Frames attached, in order:\n%s\n\n"
        "Clip name (asset): %s\n"
        "%s\n\n"
        "KINEMATIC facts per channel (these are FIXED — your labels must agree with them):\n%s\n\n"
        "IK goals (end-effectors constrained to scene targets — orthogonal to the body-part mask):\n%s\n\n"
        "Existing BASE actions an overlay may layer onto (for can_overlay_on):\n%s\n\n"
        "Propose, for each of the 8 anatomical channels (torso, head, left_arm, right_arm, left_leg,\n"
        "right_leg, left_hand, right_hand) — NOT root — these fields:\n"
        "  role         : one of primary | stabilizer | support | free\n"
        "                 (free = this action leaves the part for another action to drive; primary/stabilizer/\n"
        "                  support = this action OWNS the part. The locks/free partition is DERIVED from these\n"
        "                  roles — you do NOT set locks/free directly.)\n"
        "  motion_type  : one of cyclic-locomotion | reach | hold-static | balance | gaze | manipulate\n"
        "  contact      : 'ground' | 'object:<name>' | 'none'\n"
        "  constraint   : must-maintain | must-reach | unconstrained\n"
        "  target       : null (always null here — scene grounding is a later step)\n"
        "  motion_description : one short plain-language sentence of what this part does\n"
        "And at the top level:\n"
        "  action_id    : a short lower_snake_case verb-phrase label for the action (e.g. 'check_pulse').\n"
        "  display_name : a human title.\n"
        "  overall_intent : one sentence describing the whole action.\n"
        "  tags         : 6-10 short retrieval keywords.\n"
        "  mask_coverage : which body REGIONS this clip meaningfully DRIVES — {upper_body, hands, lower_body}\n"
        "                  booleans. A base/idle pose drives the whole body even if it owns nothing; an\n"
        "                  overlay drives the regions it owns. (This is 'what the clip animates', NOT 'what it\n"
        "                  locks' — they differ for a neutral base.)\n"
        "  composability:\n"
        "    base_or_overlay : 'base' (a foundational whole-body pose / locomotion — idle, walking, seated)\n"
        "                      or 'overlay' (a layered action driving only some parts).\n"
        "    posture         : 'standing' or 'seated'.\n"
        "    can_overlay_on  : for an OVERLAY, the subset of the base action_ids listed above whose owned\n"
        "                      parts do NOT conflict with the parts THIS action owns, that share its posture,\n"
        "                      and that make sense performed together. For a BASE action, use [].\n\n"
        "HARD consistency rules your proposal MUST satisfy (auto-checked, rejected otherwise):\n"
        "  - a channel with role=free must have constraint=unconstrained.\n"
        "  - a channel that has an IK goal must have role!=free, constraint=must-reach, and\n"
        "    contact='object:<contact_object>'.\n"
        "  - a channel with a relevant role (primary/stabilizer/support) must be constrained\n"
        "    (must-maintain or must-reach), not unconstrained.\n"
        "  - motion_type must agree with the measured state: a 'dynamic' channel is not 'hold-static'; a\n"
        "    'static' channel is not 'reach'/'manipulate'/'cyclic-locomotion'; 'cyclic-locomotion' is only\n"
        "    allowed if at least one LEG channel is dynamic. It does NOT require the body to travel --\n"
        "    a walk performed on the spot is still a walk, and the scene moves the character.\n"
        "  - gaze only on head; manipulate only on a hand channel.\n"
        "  - can_overlay_on may only name base actions from the list above; none may own a part this action\n"
        "    owns; all must share this action's posture; a base action's can_overlay_on must be empty.\n\n"
        "Return STRICT JSON only, shape:\n"
        "{\"action_id\":\"...\",\"display_name\":\"...\",\"overall_intent\":\"...\",\"tags\":[...],\n"
        " \"mask_coverage\":{\"upper_body\":true,\"hands\":false,\"lower_body\":true},\n"
        " \"composability\":{\"base_or_overlay\":\"...\",\"posture\":\"...\",\"can_overlay_on\":[...]},\n"
        " \"channels\":{\"torso\":{\"role\":...,\"motion_type\":...,\"contact\":...,\"constraint\":...,"
        "\"target\":null,\"motion_description\":\"...\"}, ... all 8 anatomical channels ...}}\n"
        % (frame_lines, clip_name,
           ("Current action_id (keep unless clearly wrong): %s" % existing_aid) if existing_aid else
           "No action_id yet — propose one.",
           _kinematic_summary(doc), _ik_summary(doc), base_lines))


def _derive_composability(cand, proposal):
    """locks/free PARTITION derived from the per-channel role (free <=> role==free, the relation the gate
    enforces); seam_owner a fixed convention (the base layer owns the torso/root seam — true of every
    existing entry); base_or_overlay / posture / can_overlay_on come from the VLM proposal (gated)."""
    part = list(C.PARTITION_CHANNELS)
    ch = cand.get("channels", {})
    free = [c for c in part if (ch.get(c) or {}).get("role") == "free"]
    locks = [c for c in part if c not in free]
    pc = proposal.get("composability", {}) or {}
    return {
        "locks": locks,
        "free": free,
        "can_overlay_on": list(pc.get("can_overlay_on") or []),
        "base_or_overlay": pc.get("base_or_overlay") or "overlay",
        "posture": pc.get("posture") or "standing",
        "seam_owner": {"torso": "base", "root": "base"},
    }


def _derive_ik_goals(cand):
    """ik_goals DERIVED from the SEMANTIC 5-tuple — the inverse of the gate's 'an IK goal => role!=free, an
    object contact, constraint in {must-reach, must-maintain}' rule: each hand/foot channel whose contact is
    object:<obj> and whose constraint pins it to that object (must-reach OR must-maintain — reaching-to and
    holding-at both put the effector at the object) yields one goal. `target` stays null: the actual scene
    anchor is engine-specific (a Unity NurseIKHelper group, an Unreal socket, ...) and is deferred to Phase-2
    grounding / the per-engine adapter — NOT stored in the engine-neutral KB (same as the per-channel target).
    contact_object is the durable, engine-neutral 'what to reach'. Left/right order matches the channel order."""
    ch = cand.get("channels", {})
    goals = []
    for c, eff in IK_EFFECTOR.items():
        f = ch.get(c) or {}
        contact = f.get("contact")
        if f.get("constraint") in ("must-reach", "must-maintain") and isinstance(contact, str) and contact.startswith("object:"):
            goals.append({
                "effector": eff,
                "target": None,
                "constraint": "TwoBoneIKConstraint",
                "contact_object": contact.split(":", 1)[1],
                "world_space": True,
            })
    return goals


def merge_proposal(doc, proposal, keep_action_id=True):
    """Overlay the VLM's SEMANTIC proposal onto a copy of the source doc, then DERIVE composability.locks/
    free/seam_owner AND ik_goals from the proposed roles/contacts. mask_coverage is taken from the proposal
    (it is 'what the clip drives', not derivable from locks). KINEMATIC, source_clip and controller_* are
    untouched; ik_goals[].target stays null (the scene anchor is engine-specific -> Phase-2 grounding).
    Returns (candidate_doc, action_id)."""
    cand = json.loads(json.dumps(doc))  # deep copy
    pch = proposal.get("channels", {}) or {}
    for c in C.PARTITION_CHANNELS:
        src = pch.get(c) or {}
        dst = cand["channels"].setdefault(c, {})
        for k in SEMANTIC_CH_KEYS:
            if k == "target":
                dst["target"] = None            # always null (Phase-2 scene grounding)
            elif k in src:
                dst[k] = src[k]
    cand["composability"] = _derive_composability(cand, proposal)
    cand["ik_goals"] = _derive_ik_goals(cand)          # DERIVED from the proposed contacts/constraints
    mc = proposal.get("mask_coverage")
    if isinstance(mc, dict):
        cand["mask_coverage"] = {"upper_body": bool(mc.get("upper_body")),
                                 "hands": bool(mc.get("hands")),
                                 "lower_body": bool(mc.get("lower_body"))}
    proposed_aid = proposal.get("action_id")
    if not (keep_action_id and cand.get("action_id")):
        cand["action_id"] = proposed_aid
    for k in ("display_name", "overall_intent", "tags"):
        if proposal.get(k) is not None:
            cand[k] = proposal[k]
    cand["status"] = "candidate"
    return cand, proposed_aid


def _composability_errors(cand, bases):
    """The composability invariants the propose loop must gate (the batch validator checks the same at
    accept time): can_overlay_on names known bases, is lock-disjoint and posture-compatible; base => empty."""
    errs = []
    comp = cand.get("composability", {}) or {}
    my_locks = set(comp.get("locks", []) or [])
    my_posture = comp.get("posture", "standing")
    can = comp.get("can_overlay_on", []) or []
    if comp.get("base_or_overlay") == "base" and can:
        errs.append("composability: a base action must have can_overlay_on=[] (got %s)" % can)
    for b in can:
        if b not in bases:
            errs.append("composability.can_overlay_on: '%s' is not a known base action_id %s" % (b, sorted(bases)))
            continue
        clash = my_locks & bases[b]["locks"]
        if clash:
            errs.append("composability.can_overlay_on: cannot overlay on '%s' — both own %s" % (b, sorted(clash)))
        if my_posture != bases[b]["posture"]:
            errs.append("composability.can_overlay_on: posture mismatch with '%s' (%s vs %s)"
                        % (b, my_posture, bases[b]["posture"]))
    return errs


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
    fo["derived"] = list(DERIVED_FIELDS)
    pending = fo.get("semantic_pending")
    if isinstance(pending, list):  # keys the VLM/derivation now fill are no longer pending
        nofill = {"channels.*.role", "channels.*.motion_type", "channels.*.contact",
                  "channels.*.constraint", "channels.*.target", "composability"}
        fo["semantic_pending"] = [k for k in pending if k not in nofill]
    ex["verified_against_screenshots"] = False
    cand["extraction"] = ex


def propose_clip(clip_name, source_doc_path, retries=2):
    """Render-free proposal for one clip: VLM proposes -> consistency + composability gate (with retry)
    -> write the semantic half back into the record. Returns (record_path, errors, warns, action_id)."""
    doc = json.load(open(source_doc_path, encoding="utf-8"))
    frames_dir = os.path.join(paths.FRAMES_DIR, clip_name)
    frames = sorted(glob.glob(os.path.join(frames_dir, "*.png")))
    if not frames:
        raise RuntimeError("no frames at %s — run `python extract.py render %s` first"
                           % (frames_dir, clip_name))
    api_key = vlm.load_api_key(REPO_ROOT)
    bases = _store_base_actions()
    base_prompt = build_prompt(doc, clip_name, bases, frames)
    feedback = ""
    cand = errors = warns = proposed_aid = None
    for attempt in range(retries + 1):
        proposal, _usage = vlm.propose(api_key, base_prompt + feedback, frames)
        cand, proposed_aid = merge_proposal(doc, proposal)
        errors, warns = [], []
        V.validate_semantic_consistency(cand, errors, warns)
        errors.extend(_composability_errors(cand, bases))
        if not errors:
            break
        feedback = ("\n\nYour previous JSON FAILED these automatic consistency checks. Return corrected JSON "
                    "that fixes ALL of them:\n- " + "\n- ".join(errors))
    _stamp_provenance(cand, frames, not errors)
    # Written back over the record it came from. One store (ADR 0016) means proposing fills the
    # semantic half of the record in place; there is no second file to reconcile, and promotion is
    # the rename that follows.
    cand_path = paths.write_json(source_doc_path, cand)
    return cand_path, errors, warns, proposed_aid
