"""
kb.py — the four knowledge-base tools that COMPUTE something.

WHAT BELONGS HERE, AND WHAT DOES NOT. `files.py` holds glob, grep and read: ordinary file access,
spelled the ordinary way, reaching the knowledge base and the Unity source assets alike. Everything
here earns a tool of its own by doing work no file read can do:

    kb_search       scores the corpus against a description and reports how well it covered the words
    kb_get_action   projects one record, with two field names repaired (see below)
    kb_pose         indexes into a `_raw` dump -- ONE line of about two megabytes -- and measures
    kb_transition   searches for the best seam between two clips, and costs the blend it would need

The line is not "knowledge base versus filesystem". It is fetching versus computing. `kb_frames` used
to sit here and did not belong: handing back a rendered PNG is a file read whose only distinguishing
feature was the extension, so `read` does it now.

TWO NAMING TRAPS ARE FIXED AT THIS BOUNDARY rather than explained to the model:

  * `channels.*.constraint` (must-maintain | must-reach | unconstrained) and `ik_goals[].constraint`
    (always "TwoBoneIKConstraint") are disjoint vocabularies sharing a field name. They are emitted as
    `reach_requirement` and `solver`.
  * `channels.*.contact` stores "object:pills" while `ik_goals[].contact_object` stores bare "pills" for
    the same referent. Contact is emitted as {"kind": "object", "name": "pills"} so the two line up.

A prompt instruction not to confuse them would be a request; renaming at the boundary is a guarantee.

THE DEFECTIVE FIELDS ARE OMITTED, NOT DISCOURAGED. `composability.can_overlay_on`, `.locks` and `.free`
never appear in any tool output. See `assemble.py` for why they are wrong. A schema omission is
enforceable; a prompt instruction is not.

NO NO-MATCH THRESHOLD. `kb_search` reports `top_margin` and `query_coverage` and lets the model decide.
Tuning a cutoff on the same twelve cases the system is evaluated on is overfitting, and across a corpus
of eight documents the cutoff is noise.
"""
from .. import segments as S
from .. import transitions as T
from ..kbindex import ANATOMICAL, CHANNELS
from .registry import ToolFailure

_CHANNEL_ENUM = list(CHANNELS)
_ANATOMICAL_ENUM = list(ANATOMICAL)

SEARCH_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "query": {
            "type": "string",
            "description": "Natural-language description of the motion, e.g. 'presses on the chest "
                           "repeatedly' or 'walks across the room'.",
        },
        "posture": {"type": "string", "enum": ["standing", "seated"]},
        "kind": {
            "type": "string", "enum": ["base", "overlay"],
            "description": "base = can stand alone and set the posture; overlay = grafted onto a base.",
        },
        "touches_object": {
            "type": "string",
            "description": "Name of a thing the motion must make contact with, as the knowledge base "
                           "spells it: pills, aspirin_bottle, patient_chest, patient_wrist, bvm_mask, "
                           "bvm_bag, keyboard.",
        },
        "drives_channel": {
            "type": "object", "additionalProperties": False,
            "description": "Keep only actions whose role for this body channel is the given one.",
            "properties": {
                "channel": {"type": "string", "enum": _ANATOMICAL_ENUM},
                "role": {"type": "string", "enum": ["primary", "support", "stabilizer", "free"]},
            },
            "required": ["channel", "role"],
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 8, "default": 5},
    },
    "required": ["query"],
}

GET_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action_id": {"type": "string", "description": "As returned by kb_search."},
        "include": {
            "type": "array",
            "description": "What to return. Default is channels only, which is what assembly needs.",
            "items": {"type": "string", "enum": ["channels", "ik_goals", "summary"]},
        },
        "channels": {
            "type": "array", "items": {"type": "string", "enum": _CHANNEL_ENUM},
            "description": "Restrict the channel block to these. Omit for all nine.",
        },
    },
    "required": ["action_id"],
}

POSE_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action_id": {"type": "string"},
        "at": {"type": "string", "enum": ["start", "middle", "end"]},
    },
    "required": ["action_id", "at"],
}

TRANSITION_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "from_action": {"type": "string"},
        "to_action": {"type": "string"},
    },
    "required": ["from_action", "to_action"],
}


def _contact(raw):
    """'object:pills' -> {'kind':'object','name':'pills'}; 'ground'/'none' -> {'kind': ...}."""
    if not raw:
        return None
    if raw.startswith("object:"):
        return {"kind": "object", "name": raw[len("object:"):]}
    return {"kind": raw}


def _channel_block(kb, action_id, wanted=None, segments=None):
    """One action's channels, as the model reads them.

    `repeats` IS THE ONLY THING FROM THE SEGMENT TABLE THAT COMES THROUGH, and deliberately so: an
    action whose arm repeats can be grafted onto something longer without outliving it, which is worth
    knowing when choosing what to combine. The frame numbers behind it are not — the model works in
    names, and the window is taken for it when the plan is built.
    """
    by_channel = {seg["channel"]: seg for seg in (segments or [])}
    out = {}
    for name, ch in kb.channels(action_id).items():
        if wanted and name not in wanted:
            continue
        entry = {"state": ch.get("state")}
        if ch.get("role"):
            entry["role"] = ch["role"]
        if ch.get("motion_type"):
            entry["motion_type"] = ch["motion_type"]
        contact = _contact(ch.get("contact"))
        if contact:
            entry["contact"] = contact
        if (by_channel.get(name) or {}).get("cycle_frames"):
            entry["repeats"] = True
        out[name] = entry
    return out


def _reach_requirements(record, wanted=None):
    """`channels.*.constraint`, renamed. Only the channels that actually require something."""
    out = {}
    for name, ch in record.get("channels", {}).items():
        if wanted and name not in wanted:
            continue
        value = ch.get("constraint")
        if value and value != "unconstrained":
            out[name] = value
    return out


def _ik_goals(record):
    return [{"effector": g.get("effector"),
             "contact_object": g.get("contact_object"),
             "solver": g.get("constraint"),
             "grounded": g.get("target") is not None}
            for g in record.get("ik_goals", [])]


def _blank(value):
    """Empty and whitespace-only strings mean "I did not specify this", not "match the empty string".

    Models fill optional string parameters with "" rather than omitting them. Passed through, that
    became a filter no action could satisfy, so every search returned nothing and the model answered by
    rephrasing — ten calls deep in one measured turn, none of which could ever have matched. A silent
    empty result is the worst failure shape available: it looks like an answer.
    """
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _frame_index(clip, at):
    """`at` is a named point, not a frame number: the model has no business picking frame 331."""
    if at == "start":
        return 0
    if at == "end":
        return clip.frames - 1
    if at == "middle":
        return clip.frames // 2
    raise ToolFailure("unknown time point %r" % at, hint="use start, middle or end")


def _height(clip, bone, frame):
    track = clip.bones.get(bone)
    if not track or frame >= len(track):
        return None
    return round(track[frame][1], 4)


def _known_contact_objects(kb):
    names = set()
    for rec in kb.actions.values():
        for goal in rec.get("ik_goals", []):
            if goal.get("contact_object"):
                names.add(goal["contact_object"])
        for ch in rec.get("channels", {}).values():
            contact = ch.get("contact") or ""
            if contact.startswith("object:"):
                names.add(contact[len("object:"):])
    return names


def register(registry, kb, measuring=True):
    """Attach the KB tools to a registry, bound to a loaded KBIndex.

    `measuring=False` withholds kb_pose and kb_transition. It exists for the narrow comparison arm,
    which is the tool surface as it stood before per-frame measurement was exposed at all — the arm
    only means something if its membership stays fixed while these modules are reorganised around it.
    """
    contact_objects = _known_contact_objects(kb)
    clips = {}                                  # action_id -> Clip, loaded on first use
    # The segment table, read once and lazily. Only one bit of it is exposed -- whether a channel
    # repeats -- so a missing sidecar costs that bit and nothing else.
    segment_table = {}

    def segments_for(action_id):
        if not segment_table:
            segment_table.update(S.read_table() or {"": []})
        return segment_table.get(action_id) or []

    def clip_for(action_id):
        if action_id not in kb.actions:
            raise ToolFailure("unknown action_id: %s" % action_id,
                              hint="use kb_search and pass an action_id it returns")
        if action_id not in clips:
            rec = kb.actions[action_id]
            name = (rec.get("source_clip") or {}).get("clip_name")
            try:
                clips[action_id] = T.load_clip(name, loop=bool(rec.get("loop")))
            except (IOError, OSError):
                raise ToolFailure("no per-frame data for %s" % action_id)
        return clips[action_id]

    def kb_search(query, posture=None, kind=None, touches_object=None,
                  drives_channel=None, limit=5):
        posture, kind = _blank(posture), _blank(kind)
        touches_object = _blank(touches_object)
        if touches_object is not None and touches_object not in contact_objects:
            # Better a correctable error than an empty result: "ground" and "none" are things the
            # knowledge base says about contact, but they are not objects, and filtering on them
            # removes every action while looking like a legitimate miss.
            raise ToolFailure(
                "no action in the library contacts anything called %r" % touches_object,
                hint="objects it knows: " + ", ".join(sorted(contact_objects)) +
                     ". Omit this filter to search without it.")

        filters = {"posture": posture, "base_or_overlay": kind, "contact_object": touches_object}
        if drives_channel:
            filters["channel_role"] = {drives_channel["channel"]: drives_channel["role"]}

        hits = kb.search(query, filters, limit=limit)
        if not hits:
            return {"results": [], "corpus_size": len(kb.actions),
                    "note": "no action matches those filters; relax them or search without filters"}

        results = []
        for hit in hits:
            record = kb.record(hit.action_id)
            channels = kb.channels(hit.action_id)
            results.append({
                "action_id": hit.action_id,
                "display_name": hit.display_name,
                "score": round(hit.score, 2),
                "matched": hit.why,
                "kind": hit.base_or_overlay,
                "posture": hit.posture,
                "duration_s": record.get("duration"),
                "loop": record.get("loop"),
                "drives": [c for c in ANATOMICAL if channels.get(c, {}).get("role") == "primary"],
                "touches": sorted({g["contact_object"] for g in record.get("ik_goals", [])
                                   if g.get("contact_object")}),
            })

        # Diagnostics instead of a tuned cutoff — see the module docstring.
        margin = round(hits[0].score - hits[1].score, 2) if len(hits) > 1 else None
        out = {"results": results, "corpus_size": len(kb.actions), "top_margin": margin,
               "query_coverage": kb.coverage(query)}

        # A SEATED HIT CARRIES HOW TO REACH IT. The library has no sit-down clip, and a model that
        # notices this concludes the sit is impossible — measured, in one sentence: "there is no
        # animation for moving from standing to seated and no seated transition that can be generated".
        # The frames ARE generated, but only by a plan shaped a particular way, and the rule for that
        # lived in the system prompt where a latency-tuned model had already stopped looking. It goes
        # here instead, attached to the hit that raises the question.
        if any(r["posture"] == "seated" for r in results):
            out["note"] = ("A seated action starts already seated; the library has no sit-down clip. "
                           "Those frames are GENERATED — one plan_motion call naming the standing "
                           "action as `base`, the seated one in `then`, and something real to sit on "
                           "as `sit_on` (scene_find category 'seating'). Two separate calls cut "
                           "straight from standing to seated instead.")
        return out

    def kb_get_action(action_id, include=None, channels=None):
        try:
            record = kb.record(action_id)
        except KeyError as e:
            raise ToolFailure(str(e), hint="call kb_search first and use an action_id it returns")

        include = set(include or ["channels"])
        wanted = set(channels) if channels else None
        out = {"action_id": action_id, "display_name": record.get("display_name")}

        if "summary" in include:
            out["intent"] = record.get("overall_intent")
            out["tags"] = record.get("tags")
            out["duration_s"] = record.get("duration")
            out["loop"] = record.get("loop")
            out["kind"] = record.get("composability", {}).get("base_or_overlay")
            out["posture"] = record.get("composability", {}).get("posture")
        if "channels" in include:
            out["channels"] = _channel_block(kb, action_id, wanted, segments_for(action_id))
            requirements = _reach_requirements(record, wanted)
            if requirements:
                out["reach_requirement"] = requirements
        if "ik_goals" in include:
            out["ik_goals"] = _ik_goals(record)
        return out

    def kb_pose(action_id, at):
        clip = clip_for(action_id)
        rec = kb.actions[action_id]
        frame = _frame_index(clip, at)
        left, right = _height(clip, "LeftFoot", frame), _height(clip, "RightFoot", frame)
        lowest = min(clip.foot_y) if clip.foot_y else None
        contacts = {name: ch.get("contact") for name, ch in (rec.get("channels") or {}).items()
                    if ch.get("contact") and ch["contact"] != "none"}
        return {
            "action_id": action_id, "at": at, "frame": frame, "frames_total": clip.frames,
            "hips_height_m": _height(clip, "Hips", frame),
            "head_height_m": _height(clip, "Head", frame),
            "left_foot_height_m": left, "right_foot_height_m": right,
            "both_feet_planted": (left is not None and right is not None and lowest is not None
                                  and (left - lowest) <= T.PLANTED_BAND_M
                                  and (right - lowest) <= T.PLANTED_BAND_M),
            "posture": (rec.get("composability") or {}).get("posture"),
            "contacts": contacts,
            "note": "Heights are metres above the floor for this avatar. Standing hips sit near 0.90 m.",
        }

    def kb_transition(from_action, to_action):
        for aid in (from_action, to_action):
            if aid not in kb.actions:
                raise ToolFailure("unknown action_id: %s" % aid)
        if from_action == to_action:
            raise ToolFailure("an action does not transition to itself")

        cached = T.read_table()
        entry = (cached or {}).get((from_action, to_action))
        if entry is None:
            seam = T.find_seam(from_action, to_action, kb,
                               {a: clip_for(a) for a in (from_action, to_action)})
            entry = seam.as_dict()
            entry["source"] = "computed"
        else:
            entry = dict(entry)
            entry["source"] = "cached"

        entry["joinable_by_blending"] = entry["class"] != T.CLASS_POSTURE_CHANGE
        if entry["class"] == T.CLASS_POSTURE_CHANGE:
            # SAYS WHAT TO DO, NOT WHAT IS MISSING. This used to answer "frames that neither clip
            # contains", which is true and was read as a refusal: measured, the model called this,
            # concluded the library could not do it, and stopped -- with the generator sitting one
            # tool call away. A crossfade genuinely cannot serve here; the frames are made instead,
            # and the sentence has to carry that rather than leave it to be inferred.
            entry["can_be_generated"] = True
            entry["how"] = ("a crossfade cannot serve here, so the frames are GENERATED for you. Find "
                            "something to sit on with scene_find(category='seating'), move_to it, then "
                            "make ONE plan_motion call with base=%s, then=[{base: %s}] and sit_on set "
                            "to that object. Do not report this as impossible."
                            % (from_action, to_action))
        return entry

    registry.add(
        "kb_search",
        "Search the motion knowledge base for actions matching a description. Returns compact "
        "summaries; use kb_get_action for a full channel breakdown. The corpus is small and "
        "clinical -- if nothing scores well, say so rather than forcing a match.",
        SEARCH_PARAMS, kb_search)

    registry.add(
        "kb_get_action",
        "Read one action: which body channels it drives and what it touches. Needed before combining "
        "two actions, to see which channels each one owns.",
        GET_PARAMS, kb_get_action)

    if not measuring:
        return registry

    registry.add(
        "kb_pose",
        "Measure one moment of an action: hip and head height, foot contact, posture. Use it to check "
        "whether two actions start and end in compatible states. The per-frame data behind this is one "
        "two-megabyte line, so it cannot be read as a file.",
        POSE_PARAMS, kb_pose)

    registry.add(
        "kb_transition",
        "Ask how two actions join: the best seam between them, how far apart the poses are there, how "
        "long a blend that needs, and whether blending can serve at all.",
        TRANSITION_PARAMS, kb_transition)

    return registry
