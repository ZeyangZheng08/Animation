"""
kb.py — the four knowledge-base tools that COMPUTE something.

WHAT BELONGS HERE, AND WHAT DOES NOT. `files.py` holds glob, grep and read: ordinary file access,
spelled the ordinary way, reaching the knowledge base and the Unity source assets alike. Everything
here earns a tool of its own by doing work no file read can do:

    kb_search       scores the corpus against a description and reports how well it covered the words
    kb_get_action   projects one record, with two field names repaired (see below)
    kb_pose         indexes into a `raw` dump -- ONE line of about two megabytes -- and measures
    kb_transition   searches for the best seam between two clips, and costs the blend it would need

The line is not "knowledge base versus filesystem". It is fetching versus computing. `kb_frames` used
to sit here and did not belong: handing back a rendered PNG is a file read whose only distinguishing
feature was the extension, so `read` does it now.

WHAT A RECORD OFFERS THE MODEL, since motionkb/v4. Descriptions and measurements, and nothing else.
The two naming traps this boundary used to repair -- `channels.*.constraint` against
`ik_goals[].constraint`, and `contact: "object:pills"` against `contact_object: "pills"` -- are gone
with the fields (ADR 0022), along with `role`, `motion_type`, `tags`, `display_name` and the whole
`composability` block. A record says what an action looks like and how each part moves; what a hand
holds and which part matters are the agent's to decide, from the task and the scene.

The filters shrank with them. `posture` survives because it is measured now -- a bin over the clip's
mean body height -- `loop` because it always was, and `moves_channel` replaces `drives_channel`:
"which action animates the legs" is a question the kinematic half can answer, where "which action
OWNS the legs" was never a property of a clip. `kind` and `touches_object` are simply gone; there is
no honest substitute for either.

NO NO-MATCH THRESHOLD. `kb_search` reports `top_margin` and `query_coverage` and lets the model decide.
Tuning a cutoff on the same twelve cases the system is evaluated on is overfitting, and across a corpus
of eight documents the cutoff is noise.
"""
from .. import kbindex as KI
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
        "posture": {"type": "string", "enum": ["standing", "seated"],
                    "description": "Measured from how the clip carries the body, not declared."},
        "moves_channel": {
            "type": "array", "minItems": 1, "maxItems": 8,
            "items": {"type": "string", "enum": _ANATOMICAL_ENUM},
            "description": "Keep only actions that actually animate every one of these body parts. "
                           "The question to ask when looking for something to combine: an action that "
                           "does not move a part has nothing to contribute there.",
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
            "items": {"type": "string", "enum": ["channels", "summary"]},
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


def _channel_block(kb, action_id, wanted=None, segments=None):
    """One action's channels, as the model reads them: what each part is doing and how it is described.

    `describes` IS WHAT ASSEMBLY READS NOW. v3 handed over `role` / `motion_type` / `contact` here and
    the model never had to choose a channel, because a deterministic rule chose for it off those
    labels. v4 deletes them (ADR 0022) and the model names the channels itself, so what this owes it
    is the evidence for that choice: whether the part moves, and the sentence saying what it does.

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
        if ch.get("describes"):
            entry["describes"] = ch["describes"]
        if (by_channel.get(name) or {}).get("cycle_frames"):
            entry["repeats"] = True
        out[name] = entry
    return out


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


def register(registry, kb, measuring=True):
    """Attach the KB tools to a registry, bound to a loaded KBIndex.

    `measuring=False` withholds kb_pose and kb_transition. It exists for the narrow comparison arm,
    which is the tool surface as it stood before per-frame measurement was exposed at all — the arm
    only means something if its membership stays fixed while these modules are reorganised around it.
    """
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

    def kb_search(query, posture=None, moves_channel=None, limit=5):
        posture = _blank(posture)
        filters = {"posture": posture}
        if moves_channel:
            filters["moves_channel"] = list(moves_channel)

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
                "description": hit.description,
                "score": round(hit.score, 2),
                "matched": hit.why,
                "posture": hit.posture,
                "duration_s": record.get("duration"),
                "loop": record.get("loop"),
                # MEASURED, and named for what it is. This was `drives`, the channels whose `role` was
                # `primary` -- a preview of a partition that no longer exists. What a hit can honestly
                # offer is the parts it animates, which is the pool the plan's channel lists draw from.
                "moves": [c for c in ANATOMICAL if channels.get(c, {}).get("state") == "dynamic"],
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
                           "as `sit_on` (scene_search('chair') finds one). Two separate calls cut "
                           "straight from standing to seated instead.")
        return out

    def kb_get_action(action_id, include=None, channels=None):
        try:
            record = kb.record(action_id)
        except KeyError as e:
            raise ToolFailure(str(e), hint="call kb_search first and use an action_id it returns")

        include = set(include or ["channels"])
        wanted = set(channels) if channels else None
        out = {"action_id": action_id, "description": record.get("action_description")}

        if "summary" in include:
            out["duration_s"] = record.get("duration")
            out["loop"] = record.get("loop")
            out["posture"] = KI.posture_of(record)
        if "channels" in include:
            out["channels"] = _channel_block(kb, action_id, wanted, segments_for(action_id))
        return out

    def kb_pose(action_id, at):
        clip = clip_for(action_id)
        rec = kb.actions[action_id]
        frame = _frame_index(clip, at)
        left, right = _height(clip, "LeftFoot", frame), _height(clip, "RightFoot", frame)
        lowest = min(clip.foot_y) if clip.foot_y else None
        return {
            "action_id": action_id, "at": at, "frame": frame, "frames_total": clip.frames,
            "hips_height_m": _height(clip, "Hips", frame),
            "head_height_m": _height(clip, "Head", frame),
            "left_foot_height_m": left, "right_foot_height_m": right,
            "both_feet_planted": (left is not None and right is not None and lowest is not None
                                  and (left - lowest) <= T.PLANTED_BAND_M
                                  and (right - lowest) <= T.PLANTED_BAND_M),
            "posture": KI.posture_of(rec),
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
                            "something to sit on with scene_search('chair'), pass it as sit_on, then "
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
        "Read one action: what each body part does in it, and whether that part moves at all. Needed "
        "before combining two actions, to decide which parts to take from each.",
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
