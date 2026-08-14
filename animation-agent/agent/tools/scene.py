"""
scene.py — the model's window onto the 3D scene, and the tool that commits a motion.

WHY THE SCENE IS QUERIED AND NOT PUSHED. `EmergencyRoom.unity` holds 600 GameObjects and almost none of
them matter to any given motion. Pushing a snapshot would be both useless and unaffordable: the model has
a 32k window. So the engine enumerates deterministically over an annotated registry and answers typed
predicates, and the model narrows to a handful of candidates over a few calls.

WHAT COMES BACK IS SYMBOLIC. Identity, category, coarse relations, reachability as a yes/no. No
transforms, no distances, no extents. Exact pose stays engine-side where the IK solver and the geometric
gates consume it directly. That is the architecture's claim that the model never handles motion numerics,
and it is enforced here by the shape of the reply rather than by asking the model nicely.

`near.radius` is an enum, not a number, for the same reason: "within arm's reach" is a decision the model
can sensibly make, "within 0.75 m" is not.

THE PLAN TOOL HAS NO NUMERIC PARAMETERS AT ALL. Look at `PLAN_PARAMS`: every field is an identifier or an
enum. There is nowhere for a joint angle, a weight, a duration or a frame index to enter, so the
invariant is structural rather than aspirational. Weights, speeds and phase offsets are constants or come
from measured clip data, and the engine fills them in.
"""
import asyncio

from .. import assemble as A
from .. import gates as G
from .. import protocol as P
from .. import segments as S
from .. import transitions as T
from ..engine import EngineError, EngineTimeout, EngineUnavailable
from ..kbindex import ANATOMICAL
from .registry import ToolFailure

FIND_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "category": {
            "type": "string",
            "enum": ["consumable", "device", "furniture", "station", "anchor", "character"],
        },
        "name_contains": {"type": "string", "description": "Substring of the object's display name."},
        "alias": {
            "type": "string",
            "description": "A contact name as the motion library spells it, e.g. pills, "
                           "aspirin_bottle, patient_chest, patient_wrist, bvm_mask, keyboard.",
        },
        "near": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "object_id": {"type": "string"},
                "radius": {"type": "string", "enum": ["arms_reach", "same_station", "same_room"]},
            },
            "required": ["object_id", "radius"],
        },
        "held_by": {"type": "string", "description": "Character id; find what it is currently holding."},
        "reachable_by": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "character": {"type": "string"},
                "effector": {"type": "string", "enum": ["left_hand", "right_hand", "either"]},
            },
            "required": ["character"],
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
    },
}

DESCRIBE_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"object_id": {"type": "string"}},
    "required": ["object_id"],
}

ANCHORS_PARAMS = {"type": "object", "additionalProperties": False, "properties": {}}

# The model picks a word; the metres stay here. Same reasoning as the scene_find radius vocabulary.
_STOP_WITHIN = {"beside_it": 0.35, "right_at_it": 0.08, "arms_reach": 0.6}

# The one action in this corpus that means travelling. `move_to` plays it under the navigation agent,
# and `_opening_step` keys on the same identifier so there is one place that decides what "she is
# walking" refers to, rather than a list that can drift from what move_to actually starts.
LOCOMOTION_ACTION = "walking"

MOVE_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "character": {"type": "string"},
        "destination": {
            "type": "string",
            "description": "Where to walk. An object_id from scene_find, an anchor from scene_anchors, "
                           "'near:<object_id>' for beside a thing rather than at it, or "
                           "'view:left' / 'view:right' / 'view:ahead' / 'view:behind' for somewhere "
                           "relative to whoever is watching.",
        },
        "face": {
            "type": "string",
            "description": "object_id to turn towards after arriving. Arriving leaves her facing the "
                           "way she walked, which is backwards for sitting down — to sit at a desk she "
                           "walks to the chair and faces the desk.",
        },
        "stop_within": {
            "type": "string", "enum": sorted(_STOP_WITHIN),
            "description": "How close to get. 'right_at_it' for something to stand on or sit on, "
                           "'beside_it' for something to work at.",
        },
        "then_wait": {
            "type": "boolean", "default": True,
            "description": "Wait until she arrives before returning. Leave on unless you have a "
                           "reason to act while she is still walking.",
        },
    },
    "required": ["destination"],
}

POSITION_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "object_ids": {
            "type": "array", "minItems": 1, "maxItems": 8,
            "items": {"type": "string", "description": "id from scene_find."},
        },
        "relative_to": {
            "type": "string",
            "description": "Character id. Adds distance, bearing and whether the thing is within "
                           "reach or needs walking to.",
        },
    },
    "required": ["object_ids"],
}

PLAN_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "character": {"type": "string", "description": "Only needed when more than one character is connected; there is normally one and it is used by default."},
        "base": {"type": "string", "description": "action_id of the action that sets the posture."},
        "overlays": {
            "type": "array", "maxItems": 3,
            "items": {"type": "string", "description": "action_id grafted onto the base."},
        },
        "hold_final_pose": {
            "type": "array", "maxItems": 3,
            "items": {"type": "string"},
            "description": "Overlay action_ids that should freeze on their last frame, e.g. keeping a "
                           "grasp while the base keeps looping.",
        },
        "ik_bindings": {
            "type": "array", "maxItems": 4,
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "effector": {"type": "string", "enum": ["left_hand", "right_hand"]},
                    "object_id": {"type": "string"},
                },
                "required": ["effector", "object_id"],
            },
        },
        "gaze_at": {"type": "string", "description": "object_id for the character to look at."},
        "stand_at": {"type": "string", "description": "anchor id to walk to before acting."},
        "carry": {
            "type": "array", "maxItems": 2,
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "object_id": {"type": "string"},
                    "hand": {"type": "string", "enum": ["left_hand", "right_hand"]},
                },
                "required": ["object_id", "hand"],
            },
            "description": "Objects PICKED UP and taken along, attached to a hand for the duration. "
                           "Only for objects scene_find reports as `carriable` — a pill bottle, a bag "
                           "valve mask. Everything else is used where it stands, however small it "
                           "looks: a laptop is typed on at the desk it is on, and carrying it takes it "
                           "with her. To touch one of those, bind a hand to it with ik_bindings "
                           "instead. Needed for anything carried, since prop visibility is not implied "
                           "by the motion.",
        },
        "then": {
            "type": "array", "maxItems": 3,
            "description": "Actions to play AFTER this one, in order. The seam between each pair, how "
                           "long the crossfade needs and where each clip starts are all worked out for "
                           "you — name the order, nothing else. This is also the ONLY way a posture "
                           "change is generated: name the standing action and the seated one in one "
                           "call with sit_on, and the frames between them are made. Two separate "
                           "plan_motion calls cut straight from one to the other instead. "
                           "Name the two actions that matter and nothing else: walking then the "
                           "seated action is a supported pair, and putting `idle` between them does "
                           "not help the change along — it replaces walking into the chair with "
                           "stopping first and sitting from a standstill.",
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "base": {"type": "string", "description": "action_id to play next."},
                    "overlays": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
                    "hold_final_pose": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
                    "sit_on": {"type": "string",
                               "description": "Accepted here as well as at the top level, for the "
                                              "step that does the sitting. There is one seat per "
                                              "plan either way."},
                },
                "required": ["base"],
            },
        },
        "sit_on": {
            "type": "string",
            "description": "object_id of something to sit on. Required for any seated action, and it "
                           "is what lets a standing-to-seated change be generated instead of refused, "
                           "because the library has no clip for sitting down. On its own it only names "
                           "the seat — pass `then` as well for the frames to actually be generated.",
        },
        "walk_to": {
            "type": "string",
            "description": "Walk her here first, then play the motion the moment she arrives — one "
                           "call, so nothing happens in between. Use it whenever the motion has to "
                           "happen somewhere she is not: `walk_to` the seat with `base: walking` and "
                           "the seated action in `then`, and she walks over and sits down out of the "
                           "walk. Calling move_to and then plan_motion separately does the same two "
                           "things with a stop in the middle. Takes the same places move_to does, "
                           "including 'near:<object_id>' and 'view:left' / 'view:right'.",
        },
        "stop_within": {
            "type": "string", "enum": sorted(_STOP_WITHIN),
            "description": "How close walk_to gets. Left out it is chosen for you: right at it when "
                           "there is something to sit on, beside it otherwise.",
        },
        # DEFAULTS TO COMMIT, and it did not used to. A dry run then a commit is two round trips to
        # play one motion, and the model paid them: measured, one turn spent an iteration on a dry run
        # and another on the identical commit. Worse, the pair invited invention -- asked for a commit
        # it sent `commit: true`, which is not a parameter, and lost a third iteration to the error.
        # The tool's job is to play the motion, so playing it is what it does unless asked not to.
        "mode": {
            "type": "string", "enum": ["dry_run", "commit"], "default": "commit",
            "description": "Leave this out to play the motion. dry_run derives the plan and runs the "
                           "cheap checks without moving anything, for when you want to see what a "
                           "plan resolves to first.",
        },
    },
    "required": ["base"],
}


# A clause constrains only if the thing it points AT is named. `radius: "same_room"` with no object and
# `effector: "either"` with no character are defaults the model filled in because the schema offered
# them — not requests. Keyed by the field that carries the subject.
_CLAUSE_SUBJECT = {"near": "object_id", "reachable_by": "character"}


def _asked_for(key, value):
    """Whether a filter was really specified. Blank values are a model filling in a schema, and
    forwarding them as constraints is how `reachable_by: {"character": ""}` became "within arm's reach
    right now" and hid a chair that was across the room."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        subject = _CLAUSE_SUBJECT.get(key)
        if subject is not None:
            return bool((value.get(subject) or "").strip())
        return any(_asked_for(k, v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return bool(value)
    return True


def _step_starts(steps):
    """When each step's contacts fall due, keyed by action id.

    A step reached through a generated posture change is not settled when it starts -- the descent is
    still running through it. Measured: the worst contact error on a walk-sit-type plan landed at
    0.12 m mid-descent, with the hands correct before and after, because the torso was still on its
    way down. The contact falls due when the change finishes.

    Shared by the contacts the gate MEASURES and the bindings the engine APPLIES, because they are the
    same moment. Computing them separately is how a gate came to judge from frame zero a hand that a
    binding would not reach for until three seconds in.
    """
    starts = {}
    for step in steps:
        due = step.get("start_at_s") or 0.0
        if step.get("generated"):
            due += step["generated"].get("duration_s") or 0.0
        starts.setdefault(step["action_id"], due)
    return starts


def _binding_due(gates, starts, effector):
    """When an IK binding for this hand should engage.

    A BINDING BELONGS TO THE STEP THAT REACHES FOR SOMETHING, NOT TO THE PLAN. Everything used to be
    applied at commit, so a plan that walked to a laptop and then typed on it pulled both wrists onto
    the keyboard anchor from the first frame -- through the whole walk. Measured on a real turn: the
    walk played with her arms stretched behind her toward the desk, and nothing reported it, because
    every check the gate runs is about where she ENDS up.

    The step is read off the knowledge base rather than asked for: `typing` records both hands as
    `contact: object:keyboard`, `walking` records them as `free / none`. So the hand is left to the
    walk's own animation until the step that actually touches something begins -- which is the same
    rule as "what the clip already does is not missing", applied in time instead of in space.

    Falls back to zero when no step declares the contact: the model asked for that binding on its own
    and there is nothing to time it against.
    """
    for gate in gates:
        for want in gate.get("clip_contacts") or []:
            if want["effector"] == effector:
                return round(starts.get(want["from_action"], 0.0), 4)
    return 0.0


def _metres(value):
    """A distance off the wire, or None when what arrived is not one.

    DEFENCE ON BOTH SIDES OF A CROSS-LANGUAGE NUMBER. `NavMeshAgent.remainingDistance` is infinite
    while a path is partial, Newtonsoft writes a non-finite float as the string "Infinity", and this
    end then compared a str to an int. That raised a TypeError deep inside a poll loop, which the
    registry reported as bad arguments from the model -- about a walk that had already been dispatched
    and was still going. The executor no longer sends it, and this no longer trusts that it does not:
    a progress line is not worth a turn.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number or number in (float("inf"), float("-inf")) else number


def _engine_failure(e):
    if isinstance(e, EngineUnavailable):
        return ToolFailure("the 3D scene is not connected", hint="the engine is not running; "
                                                                "answer from the motion library alone")
    if isinstance(e, EngineTimeout):
        return ToolFailure("the 3D scene did not answer in time")
    return ToolFailure("%s: %s" % (e.code, e.msg))


def register(registry, engine, kb=None):
    """Attach the scene and plan tools. `kb` enables the assembly derivation in plan_motion."""

    # The per-channel segment table, read once. Built live if the sidecar is missing or `_raw` has
    # moved under it -- a few hundred milliseconds for eight clips, and a stale table would hand out
    # frame numbers for a corpus that no longer exists. A dict rather than None-or-dict so callers do
    # not each have to check.
    _segment_table = {}

    def _segments():
        if not _segment_table:
            table = S.read_table()
            if table is None:
                table = S.build_table(T.load_clips(kb)) if kb is not None else {}
            _segment_table.update(table)
        return _segment_table

    def _window_of(action_id, channels):
        """Which frames this action contributes on these channels, or None for the whole clip.

        THE MODEL NEVER SEES THESE NUMBERS AND NEVER SENDS THEM. It named an action; which part of that
        action is worth playing was measured from the frozen dumps. `cpr` as an overlay means one chest
        compression rather than all thirty, and that is the difference between an arm that pumps under
        a walk and one that outlives it by seventeen seconds.
        """
        if not channels:
            return None
        return S.window_for(_segments().get(action_id), channels)

    async def _call(msg_type, params):
        try:
            return await engine.call(msg_type, params)
        except (EngineUnavailable, EngineTimeout, EngineError) as e:
            raise _engine_failure(e)

    def _who(character):
        """Resolve which character to drive, from whatever the instruction called her.

        Requiring the id bought nothing and cost a round trip: measured twice, `move_to` with character
        "nurse" against a scene whose only character is "chr:CPRNurse", each time an iteration spent on
        an error and a retry. Making the parameter optional did not help, because the model kept
        supplying a plausible wrong value rather than omitting it. Where there is exactly one character
        there is no ambiguity to protect, so a name that does not match is not a question — it is this
        one.

        WITH SEVERAL, THE NAME IS THE WHOLE POINT AND IT IS NOT THE ID. A person says "Jill"; the
        protocol says "chr:CPRNurse"; the scene object is called "CPRNurse". Three spellings of one
        character, and only the middle one used to be accepted — so the natural instruction failed on
        the one thing it was most specific about. The engine sends the names in its handshake, so all
        three are matched here without a round trip.

        AMBIGUITY IS ASKED ABOUT, NOT GUESSED. Two matches means the instruction genuinely did not say
        which, and picking one would send it to the wrong person silently — the failure mode that
        matters when several characters are drivable.
        """
        hello = engine.hello or {}
        known = list(hello.get("characters") or [])
        names = dict(hello.get("character_names") or {})
        if not known:
            raise ToolFailure("no character is connected",
                              hint="enter play mode in Unity so a character connects")
        if len(known) == 1:
            return known[0]

        def label(cid):
            return "%s (%s)" % (names.get(cid) or cid, cid)

        asked = (character or "").strip()
        if not asked:
            raise ToolFailure(
                "there are %d characters here, so which one?" % len(known),
                hint="name one of: %s" % ", ".join(label(c) for c in known))
        if asked in known:
            return asked

        # Exact on the name or the scene object first, and only then a substring. Without the two
        # passes a nurse called "Kate" would be reachable by anything containing "kate" AND by nothing
        # else preferring the exact spelling, which makes "Kate" and "Kates" the same request.
        folded = asked.lower()
        exact = [c for c in known
                 if folded in ((names.get(c) or "").lower(), c.lower(),
                               c.split(":", 1)[-1].lower())]
        if len(exact) == 1:
            return exact[0]
        loose = exact or [c for c in known
                          if folded in (names.get(c) or "").lower() or folded in c.lower()]
        if len(loose) == 1:
            return loose[0]
        if len(loose) > 1:
            raise ToolFailure(
                "%r matches more than one character" % asked,
                hint="say which: %s" % ", ".join(label(c) for c in loose))
        raise ToolFailure(
            "there is nobody here called %r" % asked,
            hint="the characters in this scene are: %s" % ", ".join(label(c) for c in known))

    async def scene_find(**params):
        """Find objects. Every filter is optional, INCLUDING all of them at once.

        This used to refuse a call with no filters, and to replace whatever the engine said about an
        empty result with "nothing in the scene matches those filters". Both were wrong, and together
        they cost a task. The room holds about a dozen annotated objects, so listing them is the
        cheapest correct answer to "is there a chair?" — and the engine already reports which
        categories exist and what the registry holds, which this then threw away. Measured: ten
        `scene_find` calls in one turn, not one of them unfiltered, ending in "the scene contains no
        identifiable chair" about a room with a chair in it.
        """
        limit = params.pop("limit", 10)
        # Blank means "I did not ask for this". Models fill in every field a schema offers, and a blank
        # clause forwarded as a real one is what made the chair invisible — see the matching note in
        # SceneQueryService.Find, and `_blank` in tools/kb.py, where this bug first appeared.
        params = {k: v for k, v in params.items() if _asked_for(k, v)}
        data = await _call(P.T.SCENE_FIND, dict(params, limit=limit))

        objects = data.get("objects", [])
        result = {"objects": objects, "count": len(objects)}
        for key in ("note", "inventory"):
            if data.get(key):
                result[key] = data[key]
        if not objects and not result.get("note"):
            result["note"] = "nothing matched those filters; call scene_find with no filters to see " \
                             "the whole room"
        return result

    async def scene_describe(object_id):
        try:
            return await _call(P.T.SCENE_DESCRIBE, {"object_id": object_id})
        except ToolFailure as e:
            if "not_found" in str(e):
                raise ToolFailure("no object with id %r" % object_id,
                                  hint="use scene_find first and pass an id it returned")
            raise

    async def scene_anchors():
        data = await _call(P.T.SCENE_ANCHORS, {})
        return {"anchors": data.get("anchors", [])}

    async def scene_position(object_ids, relative_to=None):
        """Where things are. The only query that returns coordinates, and it has to be asked for.

        This reverses an earlier decision that the scene tools should never emit numbers. That was too
        strict: an agent deciding whether to walk before it sits needs to know the chair is across the
        room, and inferring that from a coarse "near" label is guesswork dressed as architecture.

        The line that did not move: the model READS these numbers, it never WRITES motion numerics.
        plan_motion's leaves are still strings and booleans, IK targets are still symbolic bindings the
        engine resolves, and the schedule is still computed from measured data on this side.
        """
        if not object_ids:
            raise ToolFailure("name at least one object", hint="use scene_find to get ids first")
        # Through _who, like every other tool that names a character. The engine knows ids and an
        # instruction says a name, and a `relative_to` that skipped the resolution silently measured
        # from nobody -- the engine treats an unknown character here as "no reference point" rather
        # than as an error, so distances simply came back absent.
        data = await _call(P.T.SCENE_POSITION,
                           {"object_ids": object_ids,
                            "relative_to": _who(relative_to) if relative_to else None})
        objects = data.get("objects", [])
        missing = [o["object_id"] for o in objects if not o.get("found")]
        result = {"objects": objects}
        if missing:
            result["note"] = ("no object with id %s; ids come from scene_find"
                              % ", ".join(repr(m) for m in missing))
        return result

    def _one_step(base, overlays, hold_final_pose, ik_bindings, named_objects=None):
        """Derive one step's channel split and its gates. Raises ToolFailure with the reason a model
        can act on.

        `named_objects` is what the request asked for by name. It only ever decides which of two hands
        keeps its object when both grip the same one -- see assemble.arbitrate.
        """
        unknown = [a for a in [base] + list(overlays) if a not in kb.actions]
        if unknown:
            raise ToolFailure("unknown action_id: %s" % ", ".join(unknown),
                              hint="use kb_search and pass an action_id it returns")

        # AN ACTION CANNOT FIGHT ITSELF FOR A BODY PART. Measured on a live turn: the model sent
        # `typing` twice in one call and got back "these actions fight over the same body parts:
        # left_arm (typing and typing)" -- a true sentence about nothing, which cost it an iteration
        # and a wrong conclusion about what had gone wrong. A repeat asks for the same layer twice and
        # the second says nothing the first did not, so it is dropped rather than reported.
        overlays = [a for a in dict.fromkeys(overlays) if a != base]

        # Posture first: two actions that cannot share a stance cannot be combined however the channels
        # fall out, and reporting a channel conflict instead would send the model looking for a
        # different overlay when the real problem is that one of them is seated.
        posture = _posture_gate(base, overlays, kb)
        if posture["status"] == "fail":
            # NAMES THE SHAPE THAT WORKS, because the old hint -- "choose actions with the same
            # posture" -- sent the model looking for a different action when the actions were right and
            # only the arrangement was wrong. Measured: three iterations in one turn spent putting
            # `typing` in `overlays` alongside a standing base, then the turn ran out of budget.
            # Overlays play AT THE SAME TIME, which two postures cannot; `then` plays them in order,
            # and that is the one that makes the frames in between.
            seated = [a for a in [base] + list(overlays)
                      if (kb.record(a).get("composability") or {}).get("posture") == "seated"]
            raise ToolFailure(
                posture["detail"],
                hint="overlays play at the same time as the base, and two postures cannot happen at "
                     "once. To do one AFTER the other, pass %s in `then` instead -- with `sit_on` "
                     "naming something to sit on, that is what generates the frames between standing "
                     "and seated." % (seated[0] if seated else "the seated action"))

        # The partition is DERIVED here; the model never supplies channels. See assemble.py.
        try:
            assembly = A.arbitrate(base, list(overlays), kb, named_objects=named_objects)
        except Exception as e:                       # noqa: BLE001
            raise ToolFailure("could not derive the channel split: %s" % e)

        if assembly.conflicts:
            # NAMING THE REASON, NOT JUST THE CHANNEL. A channel two actions both claim is normally
            # shared between them, and two actions gripping one hand normally resolve by one of them
            # keeping its object. What reaches here is the case neither can settle: the request itself
            # named both things, so there is nothing left to decide that the caller has not already
            # decided twice. The objects are named because "they conflict" leaves the model guessing
            # which pair to break up.
            names = ", ".join("%s (%s)" % (c.channel, c.why()) for c in assembly.conflicts)
            raise ToolFailure(
                "these actions cannot both drive the same body part: %s" % names,
                hint="one hand cannot hold two things, and this plan asked for both of them by name. "
                     "Ask for one of the objects and the other hand keeps its motion without it, or "
                     "do the two actions one after the other with `then`.")

        # A SHARED CHANNEL MUST NOT BE MASKED INTO ITS OWNER'S LAYER. `assembly.layers` is an ownership
        # partition, so the larger shareholder holds a mixed channel there -- and a layer masked to it
        # at full weight is exactly the winner-take-all this replaced. It arrives instead as its own
        # layer below, at the share the role table gave it.
        shared_channels = {mix.channel for mix in assembly.shared}
        # ONE WINDOW PER ACTION, over everything that action drives in this step -- owned channels and
        # shared ones together. A clip is one performance: deciding "which frames of cpr" separately
        # for the hands it owns and the legs it half-owns would have one body playing two moments of
        # the same compression.
        driven = {}
        for aid, chans in assembly.layers:
            driven.setdefault(aid, set()).update(chans)
        for mix in assembly.shared:
            for aid, _ in mix.shares:
                driven.setdefault(aid, set()).add(mix.channel)
        windows = {aid: _window_of(aid, sorted(chans))
                   for aid, chans in driven.items() if aid != assembly.base}

        layers = []
        for aid, chans in assembly.layers:
            outright = chans if aid == assembly.base else [c for c in chans if c not in shared_channels]
            # An overlay left with no channels of its own contributes only through a mix. Emitting it
            # here would be worse than dropping it: the engine masks a layer only when its channel list
            # is non-empty, so an empty one plays FULL BODY at full weight.
            if aid != assembly.base and not outright:
                continue
            entry = {"action_id": aid, "channels": outright,
                     "source": "base" if aid == assembly.base else "overlay",
                     "owns_root": aid == assembly.root_owner,
                     "hold_final_pose": aid in (hold_final_pose or []),
                     "clip": _clip_of(kb, aid)}
            # THE BASE IS NEVER CUT. It establishes the posture everything else is grafted onto, so a
            # base trimmed to the frames its legs happen to be moving in is a posture that stops
            # halfway through the plan. An overlay is grafted on and should contribute only the part
            # that is worth grafting -- see _window_of.
            _apply_window(entry, windows.get(aid))
            layers.append(entry)
        layers.extend(_mixed_layers(assembly, hold_final_pose, windows))

        gates = [posture] + _structural_gates(assembly, kb, ik_bindings or [])
        if assembly.dropped:
            # A WARN, NOT A FAILURE, AND NOT SILENCE. The plan plays: the hand keeps that action's
            # motion and simply has nothing in it. That is a real difference from the clip the model
            # retrieved, so it is said here -- a reply describing her handing over the pills when the
            # pills were never attached is the failure this exists to prevent.
            gates.append({
                "id": "dropped_grip", "status": "warn",
                "detail": "; ".join(d.why() for d in assembly.dropped),
                "hint": "say the hand performs the motion without the object. To hold that object "
                        "instead, name it -- or play the two actions one after the other with `then`.",
                "dropped_grips": [d.as_dict() for d in assembly.dropped]})
        return assembly, layers, gates

    async def _asked_objects(action_ids, object_ids):
        """Which of the library's contact aliases the request named, by naming the scene object.

        TWO VOCABULARIES, AND THE ENGINE OWNS THE MAP. The library spells a grip `pills`; the scene
        calls the thing `obj:PillBottle`. Matching those two strings by shape would work for
        `aspirin_bottle` / `obj:AspirinBottle` and fail for exactly the pair this is about, so the
        alias is resolved the same way every other alias in this file is: by asking the scene, which
        is where the annotation lives.

        Only the aliases actually contested are looked up -- the hand grips of the actions in this
        plan -- so a plan that names nothing costs nothing and the busiest costs a handful of 0.3 ms
        round trips.
        """
        wanted = {oid for oid in object_ids if oid}
        if not wanted:
            return set()
        aliases = set()
        for action_id in dict.fromkeys(action_ids):
            for channel, spec in (kb.record(action_id).get("channels") or {}).items():
                contact = str((spec or {}).get("contact") or "")
                if channel in ("left_hand", "right_hand") and contact.startswith("object:"):
                    aliases.add(contact[len("object:"):])
        named = set()
        for alias in sorted(aliases):
            hits = (await _call(P.T.SCENE_FIND, {"alias": alias})).get("objects") or []
            if any(hit.get("id") in wanted for hit in hits):
                named.add(alias)
        return named

    def _apply_window(entry, window):
        """Put an action's frame window on the layer that carries it. A no-op without one, which is
        every base and every overlay whose clip is worth playing whole."""
        if not window:
            return
        entry["clip_start_frame"] = window["start_frame"]
        entry["clip_end_frame"] = window["end_frame"]
        entry["loop_in_window"] = window["loop"]
        # Said back so a plan that took a part of a clip says which part, rather than the frames only
        # existing on the wire. This is the record that "she did one compression, not thirty".
        entry["window_why"] = window["why"]

    def _mixed_layers(assembly, hold_final_pose, windows=None):
        """The layers that give a channel its second source. One per overlay per distinct weight.

        GROUPED BY ACTION, NOT BY CHANNEL, because a clip is one performance: its channels are coupled
        through it, and giving them separate entry phases would play one walk cycle at two phases, which
        is two legs stepping independently. So an action mixed on several channels gets ONE phase,
        searched over all of them together -- see transitions.mix_entry_frame. It may still get more than
        one layer, when different channels leave it different shares; those share the phase.

        The phase is searched against the BASE, which is what the mix is against: the base plays layer 0
        unmasked, so it is what the overlay is being averaged with.
        """
        by_weight = {}
        for mix in assembly.shared:
            for aid, weight in mix.overlay_weights(assembly.base):
                by_weight.setdefault((aid, round(weight, 4)), []).append(mix.channel)

        phases = {}
        for aid in {aid for aid, _ in by_weight}:
            channels = sorted({c for (a, _), cs in by_weight.items() if a == aid for c in cs})
            phases[aid] = _mix_phase(assembly.base, aid, channels)

        out = []
        # In `assembly.layers` order, so an earlier overlay is folded in before a later one -- which is
        # the order `overlay_weights` did its arithmetic for.
        order = [aid for aid, _ in assembly.layers]
        for (aid, weight), channels in sorted(by_weight.items(),
                                              key=lambda kv: (order.index(kv[0][0]), kv[0][1])):
            frame, apart = phases[aid]
            window = (windows or {}).get(aid)
            entry = {"action_id": aid, "channels": sorted(channels), "source": "mix",
                     "owns_root": False,
                     "hold_final_pose": aid in (hold_final_pose or []),
                     "clip": _clip_of(kb, aid),
                     "weight": weight,
                     "clip_start_frame": frame,
                     # Reported so a mix that had to average two poses far apart is visible as such
                     # rather than only as a result that reads oddly.
                     "entry_apart_deg": None if apart is None else round(apart, 2)}
            _apply_window(entry, window)
            if window:
                # THE PHASE HAS TO LAND INSIDE THE WINDOW, or the layer starts on a frame it is not
                # allowed to play and jumps on the first update. Folding it in is exact for a
                # repetition -- the clip genuinely does look the same one period later, which is the
                # measurement that made it a repetition -- and a clamp otherwise, which is honest
                # about being a compromise rather than an equivalence.
                span = window["end_frame"] - window["start_frame"]
                if window["loop"] and span > 0:
                    entry["clip_start_frame"] = window["start_frame"] + (frame - window["start_frame"]) % span
                else:
                    entry["clip_start_frame"] = min(max(frame, window["start_frame"]),
                                                    max(window["start_frame"], window["end_frame"] - 1))
            out.append(entry)
        return out

    def _mix_phase(base_id, overlay_id, channels):
        """Where the overlay enters so it starts closest to the base on the channels they share.

        Zero when the raw dumps cannot answer -- an unknown action, a missing file, a channel neither
        clip sampled. Frame 0 is at least where the animator began; a guessed frame would not be.
        """
        try:
            clips = {aid: T.load_clip((kb.record(aid).get("source_clip") or {}).get("clip_name"),
                                      loop=bool(kb.record(aid).get("loop")))
                     for aid in (base_id, overlay_id)}
            return T.mix_entry_frame(clips[base_id], 0, clips[overlay_id], channels,
                                     kb.actions.get(overlay_id))
        except (ValueError, KeyError, IOError, OSError):
            return 0, None

    def _entry_frame(from_action, to_action):
        """Which frame of `to_action` to start on, given what she is doing now.

        A COMMIT CANNOT CROSSFADE, so the entry frame is the only lever there is. The composer builds
        a fresh graph per plan and hard-sets step 0 to full weight -- `SetInputWeight(input, s == 0 ?
        1f : 0f)` -- so a `blend_in_s` on the opening step is not a gentler handover, it is nothing at
        all. What IS available is where the clip starts, and the seam search already answers that:
        the frame pair where the two poses are closest. Its own comment says as much -- "starting
        anywhere else throws away the whole point of having searched" -- and every step but the
        opening one already enters that way.

        Zero when there is nothing to search from: no engine answer, an unknown action, or a pairing
        the table has no seam for. A guessed frame would be worse than frame 0, which is at least
        where the animator began.
        """
        if not from_action or from_action == to_action:
            return 0
        if from_action not in kb.actions or to_action not in kb.actions:
            return 0
        try:
            clips = {aid: T.load_clip((kb.record(aid).get("source_clip") or {}).get("clip_name"),
                                      loop=bool(kb.record(aid).get("loop")))
                     for aid in (from_action, to_action)}
            return int(T.find_seam(from_action, to_action, kb, clips).to_frame)
        except (ValueError, KeyError, IOError, OSError):
            return 0

    async def _play_in_place(character, action_id, from_action=None, overlays=None, carry=None):
        """Play one action under a displacement -- see move_to -- with whatever is grafted onto it.

        OVERLAYS TRAVEL WITH THE WALK, and that is the point of the parameter. Every clip here is
        in-place: the navigation agent moves the transform and this plays the animation, and the two
        run side by side. So a composed motion is no harder to play while she crosses the room than a
        bare one -- it was simply never handed one. Without that, the only composition this corpus can
        express is "X while walking", and it played after the walking had finished.
        """
        assembly, layers, _ = _one_step(action_id, list(overlays or []), None, None)
        await _call(P.T.MOTION_ASSEMBLE, {
            "character": character,
            "steps": [{"action_id": action_id, "layers": layers, "start_at_s": 0.0,
                       "blend_in_s": 0.0,
                       "clip_start_frame": _entry_frame(from_action, action_id),
                       "duration_s": None,
                       "loop": bool(kb.record(action_id).get("loop")),
                       "posture": _posture_of(action_id),
                       "frame_rate": kb.record(action_id).get("frame_rate") or 30}],
            "free_channels": assembly.free_channels,
            # Props travel too: a bottle carried across the room has to be in her hand for the walk,
            # not conjured on arrival.
            "ik": [], "gaze_at": None, "stand_at": None, "carry": list(carry or []), "mode": "commit"})

    async def _walk_there(character, destination, face=None, stop_within=None, then_wait=True,
                          settle=True, under=None, carry=None):
        """Walk somewhere and play the walk while doing it. The body of move_to, factored out so a
        plan that begins with a walk can reuse it.

        `settle` IS THE WHOLE REASON THIS IS SEPARATE. move_to ends by dropping her into idle, which
        is right when the walk is the entire request -- left looping she marches on the spot forever.
        It is wrong when a sit follows: the opener of the next plan is then whatever she is playing,
        so an idle she was only parked in becomes the pose the descent departs from, and the sequence
        reads walk, stop, stand, sit rather than walking straight into the chair. A caller that has
        the next step ready passes settle=False and keeps the walk under her until it commits.

        `under` NAMES WHAT ELSE IS PLAYING while she crosses the room -- the overlays of a composed
        motion. Absent it is a bare walk, which is what it always was. The whole reason this parameter
        exists: "walk over while holding the bottle out" used to wait for her to arrive before it
        played anything but the walk, so the one shape of composition this corpus can express was the
        one it never showed. She is not doing it WHILE walking if it starts when the walking stops.
        """
        # Both go through the same lookup the seat does. They used to be passed straight through, so
        # the identical string spelled two ways worked as a destination and failed as a facing.
        destination = await _resolve_id(destination)
        face = await _resolve_id(face)
        params = {"character": character, "to": destination}
        if stop_within is not None:
            params["stop_within_m"] = _STOP_WITHIN.get(stop_within, 0.35)
        data = await _call(P.T.MOTION_LOCOMOTE, params)
        eta = data.get("eta_s")
        result = {"destination": destination, "path_length_m": data.get("path_length_m"),
                  "arrived": data.get("arrived")}
        if data.get("going") and LOCOMOTION_ACTION in kb.actions:
            # Entered on the frame closest to what she is already doing, not on frame 0 -- see
            # _entry_frame. Asking the engine what is playing costs 0.3 ms and no model iteration.
            was = await _call(P.T.MOTION_LOCOMOTE, {"character": character, "query": True})
            await _play_in_place(character, LOCOMOTION_ACTION, from_action=was.get("playing"),
                                 overlays=under, carry=carry)
            result["playing"] = LOCOMOTION_ACTION
            if under:
                result["while_walking"] = list(under)
        if not then_wait or eta is None or eta < 0:
            result["note"] = "walking; call again with then_wait to find out when she arrives"
            return result

        # Poll rather than sleep-and-hope: the ETA assumes a straight run at full speed and any
        # avoidance makes it optimistic.
        deadline = eta * 2.5 + 2.0
        waited = 0.0
        # This loop is the character crossing the room, not the agent thinking, and it is most of what
        # a turn's wall clock used to be. Declared as such so the two can be told apart -- and said out
        # loud, because otherwise a three-second walk is three seconds of blank screen.
        registry.progress("walking %s" % (
            "%.1f m" % result["path_length_m"] if result.get("path_length_m") else "there"))
        while waited < deadline:
            await asyncio.sleep(min(0.25, max(0.05, eta / 8.0)))
            waited += 0.25
            registry.progress.waited(0.25)
            state = await _call(P.T.MOTION_LOCOMOTE, {"character": character, "query": True})
            if state.get("arrived"):
                result["arrived"] = True
                result["walked_for_s"] = round(waited, 2)
                break
            remaining = _metres(state.get("remaining_m"))
            if remaining is not None and remaining >= 0:
                registry.progress("walking, %.1f m to go" % remaining)
        else:
            result["arrived"] = False
            result["note"] = "still walking after %.1fs; the route may be blocked" % waited
            return result

        # She has stopped, so stop walking. Left looping she marches on the spot until the next plan
        # replaces it, which is the same mismatch as the slide with the two halves swapped. Done
        # BEFORE the turn below, so she swings round idling rather than striding on the spot.
        #
        # WHAT WAS ON TOP OF THE WALK STAYS ON. Only the legs were the walk; an overlay she was
        # carrying across the room does not end because the crossing did, and dropping it here would
        # make the arrival look like the motion had been interrupted. `idle` claims nothing on any
        # channel, so this is the same overlay over a stance instead of over a stride.
        if settle and result.get("playing") == LOCOMOTION_ACTION and "idle" in kb.actions:
            await _play_in_place(character, "idle", from_action=LOCOMOTION_ACTION,
                                 overlays=under, carry=carry)
            result["playing"] = "idle"

        if face:
            # Arriving leaves her facing the way she walked. For a seat that is backwards, so which way
            # to end up facing has to be said rather than inferred from the route.
            await _call(P.T.MOTION_LOCOMOTE, {"character": character, "face_only": face})
            result["facing"] = face
            registry.progress("turning to face %s" % face)
            # The turn takes time now instead of a frame -- an about-face is about a second at the
            # navigation agent's angular speed. Waited out here for the same reason arrival is: the
            # next call's precondition is that she is facing the thing, and a tool that returns before
            # that is true only moves the waiting into the model.
            for _ in range(30):
                state = await _call(P.T.MOTION_LOCOMOTE, {"character": character, "query": True})
                if not state.get("turning"):
                    break
                await asyncio.sleep(0.05)
                registry.progress.waited(0.05)
            else:
                result["note"] = "still turning to face %s" % face
        return result

    async def move_to(destination, character=None, face=None, stop_within=None, then_wait=True):
        """Walk somewhere. Separate from plan_motion because every clip in the library is in-place:
        playing the walk cycle animates a walk and moves the character nowhere. This is what moves her.

        AND IT PLAYS THE WALK. Displacement and animation are separate mechanisms here -- the navigation
        agent moves the transform, the composer plays clips -- and with only the first of them running
        the character slid across the room in whatever pose she was already in. Nobody had asked for a
        slide; the walk cycle simply had no one to start it. Started here rather than left to the model
        because it is not a decision: something that moves on its own feet is walking, and making it a
        separate plan_motion call would cost a round trip to say so.

        Blocks until arrival by default, because "she is at the desk" is the precondition the next call
        depends on, and a tool that returns before it is true just moves the waiting into the model.

        For a walk that exists to get her somewhere so she can DO something there, prefer plan_motion's
        `walk_to`: it is this same walk without the stop in between.
        """
        return await _walk_there(_who(character), destination, face=face, stop_within=stop_within,
                                 then_wait=then_wait, settle=True)

    async def _face_for(order):
        """What the coming motion is aimed at, read off the actions themselves. Returns
        (object_id, action_id) or (None, None).

        THE RULE: a character faces the thing the action she is about to perform interacts with.
        Typing on a laptop means facing the laptop. A seat decides only where she sits; it has nothing
        to say about which way she faces, and taking the facing from it is how she came to sit with
        her back to the desk.

        Nothing new is authored for this. Every action already records what it touches --
        `typing` spells both hands `contact: object:keyboard`, `cpr` names the chest, `check_pulse`
        the wrist -- and the registry's alias list is what joins `keyboard` to `obj:Laptop`. So the
        rule generalises to every action in the corpus for free, where a per-seat `faceAnchor` would
        have to be re-authored for each new chair and would still be wrong for a chair used to face
        something else.

        `walking` and `idle` touch nothing and return None, which leaves the facing to the route --
        also correct: there is nothing they are aimed at.

        Hands first, and in a fixed order, so a foot resting on the floor cannot outvote what the
        hands are working on and so the same plan resolves the same way twice.
        """
        for action_id in order:
            channels = kb.record(action_id).get("channels") or {}
            names = [c for c in ("left_hand", "right_hand") if c in channels]
            names += [c for c in sorted(channels) if c not in names]
            for channel in names:
                contact = (channels.get(channel) or {}).get("contact") or ""
                if not contact.startswith("object:"):
                    continue
                hits = (await _call(P.T.SCENE_FIND,
                                    {"alias": contact[len("object:"):]})).get("objects") or []
                if len(hits) == 1:
                    return hits[0]["id"], action_id
                # More than one match is a real question and picking the first would be a guess; keep
                # looking rather than answer it here.
        return None, None

    async def _turn_to_face(character, object_id):
        """Turn, and wait it out. Same reason move_to waits: the next thing to happen is a descent
        onto a seat, and starting it mid-turn puts her down facing part of the way round."""
        await _call(P.T.MOTION_LOCOMOTE, {"character": character, "face_only": object_id})
        registry.progress("turning to face %s" % object_id)
        for _ in range(30):
            state = await _call(P.T.MOTION_LOCOMOTE, {"character": character, "query": True})
            if not state.get("turning"):
                return True
            await asyncio.sleep(0.05)
            registry.progress.waited(0.05)
        return False

    async def _resolve_id(object_id):
        """The registry id for something the model named, accepting the ways it actually writes them.

        Ids are `obj:Chair` and the model writes `Chair` — measured, twice in one turn, and it cost the
        task: `sit_on: "Chair"` came back "no object 'Chair' to sit on" about a room with a chair in
        it, and the second attempt spelled it the same way. The name is not ambiguous, only
        unpunctuated, so it is looked up rather than rejected. Ambiguity is still refused: two matches
        is a real question and answering it by picking the first would be a guess.

        AND THE PREFIX IS OURS, NOT THE MODEL'S. `obj:`, `anchor:` and `chr:` are how this registry
        namespaces its ids; which one a given thing lives under is not something a name tells you.
        Measured on the walk-and-sit run: the model asked to face `obj:Computer`, was told nothing was
        called that, spent an iteration searching, found `anchor:Computer`, and asked again — two round
        trips to change a word it had no way to get right. So a name with the wrong prefix is retried
        without it, which is the same leniency the unprefixed case already gets.
        """
        if not object_id:
            return object_id
        # A PLACE IS NOT AN OBJECT AND MUST NOT BE LOOKED UP AS ONE. `view:right` names somewhere
        # relative to whoever is watching and `near:obj:Bed` names somewhere beside a thing; neither
        # exists in the registry, so the search below would fail to find them and then hand back a
        # substring match on something that happens to be called Right. The engine resolves both.
        if object_id.startswith(("view:", "near:")):
            return object_id
        exact = await _call(P.T.SCENE_POSITION, {"object_ids": [object_id]})
        if ((exact.get("objects") or [{}])[0]).get("found"):
            return object_id
        names = [object_id]
        if ":" in object_id:
            names.append(object_id.split(":", 1)[1])
        for name in names:
            for key in ("name_contains", "alias"):
                hits = (await _call(P.T.SCENE_FIND, {key: name})).get("objects") or []
                if len(hits) == 1:
                    return hits[0]["id"]
        return object_id

    def _refuse_sitting_under_it(object_id, surface_m, target_hip_m, action_id):
        """A seat you would end up beneath is not a seat.

        Measured: the model passed the laptop as `sit_on`. It has a surface, so nothing objected; the
        descent ran to the hip height `typing` opens on, and the pelvis came to rest 0.70 m under a deck
        it was reported to be sitting on -- inside the footprint, so the containment check passed too.
        Both numbers are in hand before anything plays, and comparing them needs no calibration: a sit
        lowers the pelvis onto a surface, so a surface above where the pelvis ends up is a surface she
        cannot be on. Refused here rather than left to the gate, because the gate can only say so
        afterwards and the model has to be told what to pass instead.
        """
        if surface_m is None or target_hip_m is None or target_hip_m >= surface_m:
            return
        raise ToolFailure(
            "sitting on %s would leave her underneath it: %s ends with her hips lower than that "
            "object's surface" % (object_id, action_id),
            hint="that is something to work AT, not to sit on. Pass a seat as sit_on -- scene_find "
                 "with category 'seating' lists them -- and let her reach the other thing from there")

    async def _verify_seat(object_id):
        """Confirm the named object exists and has a surface to sit on, and return its height.

        A posture change is allowed only through here. Taking the model's word that a chair exists
        would let a plan generate a sit in mid-air and still report success, which is worse than
        refusing: the failure would arrive as a visual, three steps later.
        """
        data = await _call(P.T.SCENE_POSITION, {"object_ids": [object_id]})
        found = (data.get("objects") or [{}])[0]
        if not found.get("found"):
            raise ToolFailure("no object %r to sit on" % object_id,
                              hint="scene_find(category='seating') lists what there is")
        if found.get("surface_height_m") is None:
            raise ToolFailure("%r has no measurable surface to sit on" % object_id,
                              hint="pick something with a usable surface; scene_find reports "
                                   "has_usable_surface")
        return found

    async def _ground_declared_hands(gates, ik_bindings):
        """Bind a clip's declared hand contacts to the object's own per-hand anchors, where it has them.

        THE SCENE ALREADY KNEW THIS AND THE REGISTRY DID NOT. `typing` records both hands contacting
        `keyboard`; the laptop carries four authored transforms saying where each hand and each elbow
        goes; the demo path engages them and the hands land 0.000 m from the laptop. The agent path
        disabled that helper and knew only a single grab point, so the same request either left the
        hands hovering a fifth of a metre under the keyboard or, when the model bound them, pulled both
        wrists onto one point.

        The rule is the object's, not the model's: two hands may be aimed at something only when it
        says where both of them go. Where it does not -- a bottle, a button -- this binds nothing and
        the clip is left alone. Neither side of that is a judgement call, so neither is left to one.
        """
        bound = {b["effector"] for b in ik_bindings}
        added = []
        for gate in gates:
            for want in gate.get("clip_contacts") or []:
                if want["effector"] in bound:
                    continue
                hits = (await _call(P.T.SCENE_FIND, {"alias": want["contact"]})).get("objects") or []
                if len(hits) != 1 or not hits[0].get("two_handed_anchors"):
                    continue
                bound.add(want["effector"])
                added.append({"effector": want["effector"], "object_id": hits[0]["id"],
                              "because": "%s works %s with both hands and %s says where each one goes"
                                         % (want["from_action"], want["contact"], hits[0]["id"])})
        return added

    async def _declared_contacts(gates, steps):
        """What the clips say their hands will touch, resolved to real objects and timed.

        NOT A BINDING. Nothing is attached and no IK is applied -- the clip's own hand motion is left
        exactly as authored, which is the point. This only names what the engine should MEASURE, so
        that "her hands are on the laptop" becomes a number instead of something to squint at.

        It is worth measuring because the geometry does not always work out and nothing said so.
        Measured on `typing` against this scene: the clip types at 0.70 m above the floor and 0.33 m in
        front of the root, the laptop's deck sits at about 0.90 m, and sitting on a 0.41 m chair puts
        her hands a fifth of a metre under the keyboard. Every check passed and the hands looked wrong,
        which is the same shape of failure as the sit that landed on nothing.

        `due_at_s` is when the step that declares the contact starts. Without it the whole plan is
        judged by its worst frame, and a hand cannot be on a keyboard while she is still walking to it.
        """
        starts = _step_starts(steps)
        out, seen = [], set()
        for gate in gates:
            for want in gate.get("clip_contacts") or []:
                if want["effector"] in seen:
                    continue
                hits = (await _call(P.T.SCENE_FIND, {"alias": want["contact"]})).get("objects") or []
                if len(hits) != 1:
                    continue          # nothing to measure against, or a choice that is not ours
                seen.add(want["effector"])
                out.append({"effector": want["effector"], "object_id": hits[0]["id"],
                            "due_at_s": starts.get(want["from_action"], 0.0)})
        return out

    async def _opening_step(character, base, overlays, then):
        """What a plan should open on, and whether that opener is only there to be departed from.

        Returns (action_id, at_rest).

        A STANDING-TO-SEATED CHANGE NEEDS A STANDING STEP TO DEPART FROM, and the model reaches for
        `walking` because that is the standing action it just used to get there. But `move_to` has
        already walked her and left her idle, so the walk cycle plays again -- in place. That is what
        "the walking got stuck" was.

        `at_rest` is the more important half. When she is standing still, the opener names a POSE
        rather than a performance, and the schedule enters on the seam frame and leaves at once
        instead of playing a whole cycle first. Substituting the action without that fixes nothing:
        measured, swapping the 0.97 s walk for the 8.4 s idle turned one stride on the spot into eight
        seconds of standing motionless.

        The engine is asked rather than guessed at: it knows whether the navigation agent is going
        anywhere and what the composer is playing, and the call costs 0.3 ms and no model iteration.
        Narrow on purpose -- a plan that just asks for `walking` is honoured, and so is one committed
        while she really is walking.
        """
        if base != LOCOMOTION_ACTION and not then:
            return base, False          # nothing below can apply
        try:
            state = await _call(P.T.MOTION_LOCOMOTE, {"character": character, "query": True})
        except ToolFailure:
            return base, False          # no engine to ask: leave the plan exactly as written
        if state.get("going"):
            return base, False          # she really is walking; the rest of this is about standing still

        if base == LOCOMOTION_ACTION and not then and not overlays:
            # A WALK CYCLE ON ITS OWN, WHILE SHE IS NOT TRAVELLING, IS MARCHING ON THE SPOT. Measured
            # on a real turn: move_to walked her to the patient and the model then committed `walking`
            # by itself, so she arrived and kept striding in place indefinitely — and reported having
            # walked there, which was true and was not what the scene showed. Refused rather than
            # substituted, because there is nothing this plan wants: the walking already happened.
            #
            # ONLY the bare case. `walking` with an overlay is a composed motion whose base carries the
            # posture — walking while grabbing a bottle — and refusing that would take away a capability
            # over a plan the model may yet follow with a move_to.
            raise ToolFailure(
                "she is not going anywhere, so playing the walk cycle would march her on the spot",
                hint="move_to is what moves her and it has already left her standing where she "
                     "arrived. Nothing needs to be played for a walk that is over — say she is there.")

        if base != LOCOMOTION_ACTION:
            return base, True
        playing = state.get("playing")
        if not playing or playing == base or playing not in kb.actions:
            return base, True
        if _posture_of(playing) != _posture_of(base):
            return base, True           # swapping in a different posture would change what the seam is
        return playing, True

    def _posture_of(action_id):
        """An action's posture, as the knowledge base recorded it. Falls back to standing rather than
        to null: the executor treats a missing posture as standing, and agreeing here keeps a record
        with an incomplete composability block from meaning two different things on the two sides."""
        return (kb.record(action_id).get("composability") or {}).get("posture") or "standing"

    async def _commit_sequence(character, order, sit_on=None):
        """Build and commit a plain ordered plan — no overlays, no bindings, no gaze.

        Deliberately not plan_motion. That function's job is to turn what a MODEL asked for into a plan,
        and most of it is about the ways a model can ask for something impossible. This is for the plans
        this file decides on by itself, where the order is already known to be right, and reusing the
        big one would mean re-deriving a seat, a facing and a walk that have already been settled.

        `open_at_seam` because every caller here opens on what she is already doing: that step supplies
        the pose the next one departs from, and playing it out first would be a stride on the spot or
        eight seconds of standing still.
        """
        clips = {aid: T.load_clip((kb.record(aid).get("source_clip") or {}).get("clip_name"),
                                  loop=bool(kb.record(aid).get("loop")))
                 for aid in order}
        timeline = T.schedule(order, kb, clips, generate_posture_changes=True, open_at_seam=True)

        steps, generated = [], []
        for index, aid in enumerate(order):
            _, step_layers, _ = _one_step(aid, [], None, None)
            timed = timeline[index].as_dict()
            timed.update({"layers": step_layers, "posture": _posture_of(aid),
                          "frame_rate": kb.record(aid).get("frame_rate") or 30})
            if timed.get("generated"):
                if sit_on:
                    # What she is pushing off from. The gate uses it to judge a landing; for a rise
                    # there is nothing to land ON, and RunPostureChange uses it to find the place to
                    # step off to instead.
                    timed["generated"]["support_object_id"] = sit_on
                generated.append(timed["generated"])
            steps.append(timed)

        await _call(P.T.MOTION_ASSEMBLE, {
            "character": character, "steps": steps, "free_channels": [],
            "ik": [], "gaze_at": None, "stand_at": None, "carry": [], "mode": "commit"})
        return {"order": order, "generated": generated}

    async def _get_up_first(character, opener):
        """Put her on her feet before anything that needs her on them. Returns what it did, or None.

        THE ORDER IS THE WHOLE PROBLEM, and it is the mirror image of sitting down. Sitting walks first
        and commits second, because the walk is what carries her to the seat. Getting up cannot: the
        navigation agent is disabled while she is seated, and re-enabling it warps the transform to the
        nearest walkable point — which is not under the chair. Travel first would therefore teleport her
        off the seat in a seated pose, and the engine refuses it for exactly that reason. So the rise is
        committed and LANDED first, and only then is there anything to travel with.

        It is an ordinary plan, not a special case: her current seated action into the one the caller
        asked for, with the frames between them generated the same way sitting down generates them. The
        seat comes from the executor, which is the only thing that knows what she is on.

        WAITING ON `on_navmesh`, NOT ON A TIMER. The agent comes back at the end of the rise and not
        before — `RunPostureChange` resumes it once she is standing — so that flag is the rise
        finishing, observed rather than estimated. A timer would have to guess the seam wait.
        """
        try:
            state = await _call(P.T.MOTION_LOCOMOTE, {"character": character, "query": True})
        except ToolFailure:
            return None
        if state.get("posture") != "seated" or _posture_of(opener) == "seated":
            return None

        # What she is in the middle of, which is what the rise departs from. The engine's answer is
        # preferred over the corpus's single seated action, so this keeps working when there are two.
        from_action = state.get("playing")
        if from_action not in kb.actions or _posture_of(from_action) != "seated":
            seated = [a for a in kb.actions if _posture_of(a) == "seated"]
            if len(seated) != 1:
                raise ToolFailure(
                    "%s is seated and I cannot tell which seated action she is in" % character,
                    hint="the engine reported %r, which is not a seated action in the library"
                         % (from_action,))
            from_action = seated[0]

        rise = await _commit_sequence(character, [from_action, opener],
                                      sit_on=state.get("sitting_on"))
        registry.progress("standing up off %s" % (state.get("sitting_on") or "the seat"))
        for _ in range(60):
            await asyncio.sleep(0.1)
            registry.progress.waited(0.1)
            back = await _call(P.T.MOTION_LOCOMOTE, {"character": character, "query": True})
            if back.get("on_navmesh") and back.get("posture") == "standing":
                rise["landed"] = True
                return rise
        rise["landed"] = False
        rise["note"] = "she did not finish standing up; nothing was walked"
        return rise

    async def plan_motion(base, character=None, overlays=None, hold_final_pose=None, ik_bindings=None,
                          gaze_at=None, stand_at=None, carry=None, then=None, sit_on=None,
                          walk_to=None, stop_within=None, mode="commit"):
        overlays = overlays or []
        if kb is None:
            raise ToolFailure("no motion library is loaded")
        character = _who(character)

        # `sit_on` BELONGS TO THE PLAN, BUT IT MAY BE WRITTEN ON THE STEP THAT SITS. Measured: the model
        # put it inside the `then` entry for the seated action — which is where it reads most naturally,
        # since that is the step doing the sitting — and got back "typing is a seated action and nothing
        # was named to sit on" about a call that had named one. It rewrote the same plan four times.
        # There is exactly one seat per plan, so accepting it wherever it is written costs nothing and
        # removes a whole class of loop.
        #
        # EVERY PLACE THE MODEL NAMES AN OBJECT, not just the seat. Only `sit_on` went through the
        # lookup, so the same string was a valid seat and an invalid hand target in one call: measured,
        # a plan bound both hands to `Laptop` and neither binding resolved, the clip played unbound, and
        # the gate reported hands 0.19 m from the keyboard. Whether an id carries its prefix is not
        # something the model can be expected to get right one field at a time.
        #
        # Hoisted ahead of the walk below, which needs the seat to know how close to stop.
        for step in (then or []):
            sit_on = sit_on or step.pop("sit_on", None)
        sit_on = await _resolve_id(sit_on)

        # The order as the model asked for it. The opener may still be substituted below, but what the
        # plan is aimed AT is decided by the actions it named, not by what it ends up opening on.
        intended = [base] + [entry["base"] for entry in (then or [])]

        # SITTING ON SOMETHING MEANS BEING AT IT, so naming a seat is naming somewhere to walk. Left to
        # the model this went wrong in both directions on real turns: one committed the sit while she
        # was still crossing the room, and the rest walked there with move_to, which ends by parking
        # her in idle -- so the plan departed from a standstill and the sequence read walk, stop,
        # stand, sit. It is not a decision either: the seat is already named, the gate already refuses
        # a landing that misses it, and there is nothing else `sit_on` could mean.
        #
        # Harmless when she is already there: the walk is over before it starts and the engine reports
        # the arrival immediately.
        # Checked BEFORE she walks anywhere for it, now that naming a seat is also naming a
        # destination. Otherwise a seat that is not one is reported as a place she could not get to,
        # which is a true sentence about the wrong problem.
        seat = await _verify_seat(sit_on) if sit_on else None
        if sit_on and not walk_to:
            walk_to = sit_on

        # EVERY OBJECT THE MODEL NAMED, RESOLVED BEFORE ANYTHING MOVES. It used to be resolved after
        # the partition was derived, which was fine while the partition did not depend on it. It does
        # now -- when two actions grip the same hand, the one whose object was asked for keeps it, and
        # both being asked for is the one case still refused -- and the walk below needs it too, since
        # a bottle carried across the room has to be in her hand for the crossing.
        gaze_at = await _resolve_id(gaze_at)
        for binding in (ik_bindings or []):
            binding["object_id"] = await _resolve_id(binding.get("object_id"))
        for held in (carry or []):
            held["object_id"] = await _resolve_id(held.get("object_id"))
        asked_objects = await _asked_objects(
            [base] + list(overlays) + [e["base"] for e in (then or [])],
            [b.get("object_id") for b in (ik_bindings or [])]
            + [h.get("object_id") for h in (carry or [])])

        # WHAT SHE DOES WHILE SHE IS WALKING. A plan whose base IS the walk is asking for a composed
        # motion -- "walk over holding the bottle out" -- and its overlays belong on top of the walk
        # that gets her there, not after it. Derived here so a plan that cannot be assembled is
        # refused before she takes a step, rather than half-played and then rejected.
        while_walking = list(overlays) if (walk_to and overlays and base == LOCOMOTION_ACTION) else None
        if while_walking:
            _one_step(base, while_walking, hold_final_pose, ik_bindings, asked_objects)

        # ON HER FEET BEFORE ANYTHING ELSE. A plan that opens standing while she is seated used to be
        # refused outright; the frames for getting up are generated now, and the only thing that stays
        # true is the order — she cannot travel while the navigation agent is still off. Returns None
        # and costs one query when she is already standing, which is every plan that came before this.
        stood_up = await _get_up_first(character, base) if mode == "commit" else None
        if stood_up and not stood_up.get("landed"):
            raise ToolFailure(stood_up.get("note") or "she did not finish standing up",
                              hint="nothing else was played. Read check_motion for how far the rise "
                                   "got before asking for it again.")

        walked = None
        if walk_to and mode == "commit":
            # ONE CALL, SO THERE IS NO MODEL TURN IN THE MIDDLE OF THE MOTION. Walking and then sitting
            # used to be move_to followed by plan_motion, and between them sat a model round trip --
            # measured at 0.85 s on a real turn -- during which she stood at the chair doing nothing.
            # Worse, move_to had parked her in idle to end the walk, so the plan that followed departed
            # from a standstill: walk, stop, stand, sit. Here the walk is left playing and the descent
            # commits the moment she arrives, one WebSocket round trip later (p50 0.32 ms).
            #
            # It stays a walk in the plan too. _opening_step asks the engine what is playing rather
            # than guessing, sees `walking`, and keeps it as the opener with at_rest -- so the step
            # supplies the pose the descent departs from and hands over at once, instead of striding a
            # full cycle on the spot after she has already crossed the room.
            if stop_within is None:
                stop_within = "right_at_it" if sit_on else "beside_it"
            walked = await _walk_there(character, walk_to, stop_within=stop_within,
                                       then_wait=True, settle=False,
                                       under=while_walking, carry=carry if while_walking else None)
            if not walked.get("arrived"):
                raise ToolFailure(
                    "she did not get to %s: %s" % (walk_to, walked.get("note") or "still walking"),
                    hint="nothing was played. Check the destination is reachable, or walk there with "
                         "move_to and see how far she gets.")
            # Which way to face is decided by the action and what it touches, not by the seat and not
            # by the route -- see _face_for. Done before the plan commits, because the descent has to
            # start from the finished orientation.
            aim, aimed_for = await _face_for(intended)
            if aim:
                if not await _turn_to_face(character, aim):
                    walked["note"] = "still turning to face %s when the plan committed" % aim
                walked["facing"] = aim
                walked["facing_from"] = "%s touches it, so that is what she faces" % aimed_for

        asked_base = base
        # THE WALK IS OVER, THE OVERLAY IS NOT. She has arrived, so keeping `walking` as the base of
        # what commits now would stride her on the spot -- the same mismatch a bare walk gets refused
        # for. The overlays stay: `idle` claims nothing on any channel, so this is the same composed
        # motion over a stance instead of over a stride. Only when nothing follows; a plan with `then`
        # is departing from the walk on purpose.
        arrived_composing = bool(while_walking) and not then and "idle" in kb.actions
        if arrived_composing:
            base = "idle"
        base, at_rest = await _opening_step(character, base, overlays, then)
        assembly, layers, gates = _one_step(base, overlays, hold_final_pose, ik_bindings, asked_objects)
        if arrived_composing:
            # A DIFFERENT SUBSTITUTION AND A DIFFERENT REASON, so it does not borrow the sentence
            # below. Nothing was dropped and nothing failed: she really did walk and really did
            # perform the overlay while walking, and this is what is left once the walking is done.
            gates.append({
                "id": "opening_step", "status": "pass",
                "detail": "%s played under the walk that got her here and is still playing; the walk "
                          "is over, so what commits now is the same overlay over a stance rather than "
                          "over a stride." % ", ".join(overlays),
                "hint": "she did this while walking, not after arriving."})
        elif base != asked_base:
            # SAID, NOT DONE QUIETLY. The model reports what it committed, and a substitution it
            # cannot see becomes a reply describing a walk that never happened.
            gates.append({
                "id": "opening_step", "status": "pass",
                "detail": "%s opens this plan only as something to depart from, and she is not "
                          "walking anywhere — %s is what she is already doing, so that is what it "
                          "opens on. Playing the walk cycle here marches her on the spot."
                          % (asked_base, base),
                "hint": "she is already where this happens; do not describe her as walking to it."})

        # A SEATED ACTION NEEDS SOMETHING TO SIT ON, whether or not a transition is generated.
        # Measured: an agent that could not find the chair fell back to planning `typing` on its own,
        # the character sat in mid-air a metre from the stool, every geometric check passed because
        # nothing had claimed a support, and the agent reported success. Playing a seated clip on open
        # floor is exactly as wrong as a badly generated sit, so it is refused here.
        #
        # AFTER the posture gate inside _one_step, not before: two actions that cannot share a stance
        # cannot be combined however the seating works out, and answering "no chair" for
        # typing + grab_bottle would send the model looking for furniture.
        # (`sit_on` is hoisted off the `then` entry and resolved at the top of this function, because
        # the walk that may precede the plan needs the seat to know how close to stop. `gaze_at`,
        # `ik_bindings` and `carry` are resolved above, ahead of the partition that now consults them.)
        seated = [a for a in [base] + list(overlays) + [e["base"] for e in (then or [])]
                  if (kb.record(a).get("composability") or {}).get("posture") == "seated"]
        if seated and not sit_on:
            raise ToolFailure(
                "%s is a seated action and nothing was named to sit on" % seated[0],
                hint="find a seat with scene_find(category='seating'), move_to it stopping right at "
                     "it, and pass its id as the top-level `sit_on` of this same call. Playing a "
                     "seated clip on open floor puts her in mid-air.")

        derived = [assembly.as_dict()]
        assemblies = [assembly]         # the objects behind `derived`, for the retrieval verdict
        first = {"action_id": base, "layers": layers,
                 "start_at_s": 0.0, "blend_in_s": 0.0, "clip_start_frame": 0,
                 "duration_s": None, "loop": bool(kb.record(base).get("loop")),
                 # The engine has no knowledge base, so what posture a step is in has to travel with
                 # it. This is what lets the executor know she ends up seated -- and therefore refuse
                 # to walk her off the chair afterwards, and keep the generated pose across a rebuild.
                 "posture": _posture_of(base),
                 "frame_rate": kb.record(base).get("frame_rate") or 30}
        if seat and not then:
            # A seated action played on its own still has to be checked against the seat, even though
            # nothing is being generated. Without this the gate has nothing to judge and passes.
            first["expect_support"] = {"object_id": sit_on,
                                       "surface_m": seat.get("surface_height_m")}
        steps = [first]

        # A SEATED ACTION WITH NOTHING BEFORE IT IS A CUT, and the geometric gates cannot see it: they
        # judge where she ends up, and she ends up correctly on the seat. What they never see is that
        # she arrived there in one frame. Measured: the first agent run to reach the chair planned the
        # walk and the typing as two separate calls, every check passed, and it reported having
        # generated a sit that was never generated. Saying so in the result is the only place this is
        # visible, so it is said here rather than left to the prompt.
        if seated and not then:
            gates.append({
                "id": "transition_present", "status": "warn",
                "detail": "%s is seated and nothing precedes it in this plan, so it starts at the "
                          "seated pose with no way in. If she is standing now, that is a cut, not a "
                          "sit." % seated[0],
                "hint": "name the standing action as `base` and %s in `then`, in one call, to have "
                        "the frames between them generated." % seated[0]})

        if then:
            # TIMING IS DERIVED HERE TOO. The seam search picks where each step enters and hands over
            # and how long the crossfade needs; the model named an order, nothing more. Same split as
            # the channel partition, for the same reason.
            order = [base] + [entry["base"] for entry in then]
            try:
                clips = {aid: T.load_clip((kb.record(aid).get("source_clip") or {}).get("clip_name"),
                                          loop=bool(kb.record(aid).get("loop")))
                         for aid in order}
                timeline = T.schedule(order, kb, clips,
                                      generate_posture_changes=seat is not None,
                                      open_at_seam=at_rest)
            except ValueError as e:
                raise ToolFailure(str(e),
                                  hint="the library has no clip for standing up or sitting down. Find "
                                       "something to sit on with scene_find(category='seating') and "
                                       "pass it as sit_on; the frames will then be generated against "
                                       "it rather than blended.")
            except (IOError, OSError) as e:
                raise ToolFailure("no per-frame data to find a seam with: %s" % e)

            steps = []
            for index, aid in enumerate(order):
                if index == 0:
                    step_layers, step_gates = layers, gates
                else:
                    entry = then[index - 1]
                    a, step_layers, step_gates = _one_step(
                        aid, entry.get("overlays") or [], entry.get("hold_final_pose"), None)
                    derived.append(a.as_dict())
                    assemblies.append(a)
                    gates = gates + step_gates
                timed = timeline[index].as_dict()
                timed.update({"layers": step_layers,
                              "posture": _posture_of(aid),
                              "frame_rate": kb.record(aid).get("frame_rate") or 30})
                if timed.get("generated") and sit_on:
                    # Name the support on the wire so the gate can check the landing rather than only
                    # how well the descent tracked its own plan.
                    timed["generated"]["support_object_id"] = sit_on
                    timed["generated"]["support_surface_m"] = seat.get("surface_height_m")
                    _refuse_sitting_under_it(sit_on, seat.get("surface_height_m"),
                                             timed["generated"].get("target_hip_height_m"), aid)
                steps.append(timed)

        grounded = await _ground_declared_hands(gates, ik_bindings or [])
        starts = _step_starts(steps)
        payload = {
            "character": character,
            "steps": steps,
            "free_channels": assembly.free_channels,
            "ik": [{"effector": b["effector"], "object_id": b["object_id"],
                    "at_s": _binding_due(gates, starts, b["effector"])}
                   for b in (ik_bindings or []) + grounded],
            "expect_contact": await _declared_contacts(gates, steps),
            "gaze_at": gaze_at,
            # Same reasoning as a hand binding, with nothing in the knowledge base to time it against:
            # a gaze is for the step the plan settles into, and holding a head-aim through the walk
            # that gets her there reads wrong in exactly the way the hands did. A one-step plan is
            # unaffected, since its last step starts at zero.
            "gaze_at_s": round(steps[-1].get("start_at_s") or 0.0, 4),
            "stand_at": stand_at,
            "carry": carry or [],
            "mode": mode,
        }
        data = await _call(P.T.MOTION_ASSEMBLE, payload)

        generated = [s["generated"] for s in steps if s.get("generated")]
        # Said back, because `mode` defaults and a caller that omitted it has no other way to know
        # whether anything moved. It is also what the turn report reads to time the decision: reading
        # the model's own arguments instead reported no motion for every call that took the default.
        # WHICH BRANCH THIS REQUEST TOOK, recorded rather than left to be inferred. A library hit that
        # covered the whole request and a motion composed out of several are different claims about
        # the system, and until now only the eval could tell them apart -- a live turn that assembled
        # a motion existing in no clip left nothing behind saying so. Same function the eval scores
        # with; see assemble.verdict.
        verdicts = [A.verdict(a, gaze_at) for a in assemblies]
        result = {"mode": mode, "derived": derived if len(derived) > 1 else derived[0],
                  "retrieval": verdicts if len(verdicts) > 1 else verdicts[0],
                  "gates": gates, "engine": data}
        # WHICH PART OF EACH CLIP WAS USED, when it was not all of it. Assembly's unit used to be a
        # whole clip on a channel; an overlay now contributes the frames it is actually doing
        # something in, and one repetition where it repeats. Said back because "she did one chest
        # compression, not thirty" is a fact about what played, and a plan that left it on the wire
        # would leave the model describing the clip rather than the motion.
        taken = [{"action_id": layer["action_id"], "channels": layer["channels"],
                  "frames": [layer["clip_start_frame"], layer["clip_end_frame"]],
                  "why": layer["window_why"], "loops": layer["loop_in_window"]}
                 for step in steps for layer in step["layers"] if "window_why" in layer]
        if taken:
            result["segments"] = taken
        # WHICH HAND CAME UP EMPTY. Two actions gripping one hand no longer refuse the plan: the hand
        # keeps one of the two motions and the other's object is simply not attached. That is a real
        # difference from the retrieved clip, so it travels in the result rather than only in a gate --
        # a reply describing her handing over pills that were never in her hand is the failure this is
        # here to stop.
        detached = [d for a in assemblies for d in a.dropped]
        if detached:
            result["dropped_grips"] = [d.as_dict() for d in detached]
        if walked is not None:
            # Said back for the same reason the substituted opener is: the model did not watch the
            # walk happen, and how far she actually went is the difference between "she walked over
            # and sat down" and a sit that began where she already stood.
            result["walked"] = walked
        if stood_up:
            # Same reason again, and this one is a whole generated motion the model never asked for:
            # it asked for what came after. A reply that does not mention getting up describes a
            # character who was already on her feet.
            result["stood_up"] = stood_up
        if arrived_composing:
            result["played_while_walking"] = {
                "base": asked_base, "overlays": list(overlays),
                "why": "the overlays played on top of the walk that got her here, and carried on "
                       "once she arrived"}
        elif base != asked_base:
            result["opened_on"] = {"asked_for": asked_base, "played": base,
                                   "why": "she was not walking anywhere; %s is what she was already "
                                          "doing" % base}
        if grounded:
            result["grounded_hands"] = grounded
        if len(steps) > 1:
            result["sequence"] = [{"action_id": s["action_id"], "starts_at_s": s["start_at_s"],
                                   "fades_in_over_s": s["blend_in_s"]} for s in steps]
        if generated:
            result["generated_transitions"] = generated
            result["note"] = ("part of this motion does not exist in the library and was generated: "
                              "say so rather than presenting it as a retrieved clip.")
            if mode == "commit":
                # THE LANDING CANNOT BE MEASURED YET, AND WAITING FOR IT WOULD PUT THE LENGTH OF THE
                # ANIMATION INSIDE THE ANSWER. The descent does not start until the outgoing step
                # reaches its handover and then takes about a second more, which is longer than the
                # whole turn should be. So the check is scheduled rather than called: the loop runs it
                # once it is answerable and reports separately. Saying so here also removes the
                # check_motion round trip from the model's path -- it does not have to ask.
                result["verify"] = {
                    "status": "scheduled",
                    "tool": "check_motion",
                    "arguments": {"character": character},
                    "confirms": "the pelvis landed on %s" % sit_on,
                    "on_failure": "the sit you just committed did not land:",
                    "note": "the landing is being measured and the result is reported separately. It "
                            "is not known yet, so do not say she is seated -- say she is sitting down.",
                }
        return result

    def _clip_of(kb, action_id):
        """guid + file_id, resolved here so the engine never needs the knowledge base and the model
        never sees an asset id."""
        clip = kb.record(action_id).get("source_clip", {})
        return {"guid": clip.get("guid"), "file_id": clip.get("file_id"),
                "clip_name": clip.get("clip_name")}

    async def check_motion(character=None):
        """The geometric verdict on what is actually playing.

        Waits for the motion to reach the point where its checks can be answered, rather than sampling
        whenever the model happens to ask. A generated sit is not judgeable until the descent has run,
        which is seconds after the plan is committed and long after the next model round trip -- so
        without the wait this returned "passed" about a landing that had not happened.
        """
        # Resolved here, not passed through. This was the one tool that handed whatever it was given
        # straight to the engine, which knows ids only -- so asking about "Jill" came back "no
        # character 'Jill'" from a scene that has her in it. Every other tool goes through _who and
        # this one has to as well, or naming a character works everywhere except when you ask how it
        # went.
        report = await G.wait_until_judgeable(
            lambda who: _call(P.T.GATE_RUN, {"character": who}), _who(character))
        ok, payload = G.summarise(report)
        if not ok and payload.get("status") == "pending":
            waited = ", ".join(p["check"] for p in payload["pending"])
            raise ToolFailure(
                "the motion has not reached the point where %s can be measured" % waited,
                hint="it is still playing. This is not a failure and not a pass -- nothing about the "
                     "landing is known yet, so do not report success. Call check_motion again.")
        if not ok:
            problems = "; ".join(f["problem"] for f in payload["failures"])
            hints = " ".join(f["try"] for f in payload["failures"])
            raise ToolFailure("the motion played but failed a geometric check: " + problems,
                              hint=hints)
        return payload

    registry.add("check_motion",
                 "Measure the motion currently playing against the scene: did the bound hand stay on "
                 "its object, did a foot go through the floor, did a sit land on the seat. Waits for "
                 "the motion to reach the point where that is answerable, so it may take a moment.",
                 {"type": "object", "additionalProperties": False,
                  "properties": {"character": {"type": "string"}}},
                 check_motion)

    registry.add("scene_find",
                 "Find objects in the 3D scene by category, name, or the contact name a motion uses. "
                 "Returns identities and coarse relations; ask scene_position for where they are.",
                 FIND_PARAMS, scene_find)
    registry.add("scene_describe",
                 "Details for one scene object: what it is, what holds it, whether the character can "
                 "reach it.",
                 DESCRIBE_PARAMS, scene_describe)
    registry.add("scene_anchors",
                 "Named places a character can stand and face, e.g. the bedside or the monitor station.",
                 ANCHORS_PARAMS, scene_anchors)
    registry.add("scene_position",
                 "Where objects actually are, in metres, and how far they are from a character. Use it "
                 "to decide whether something is within reach or needs walking to, and to find the "
                 "height of a surface such as a seat.",
                 POSITION_PARAMS, scene_position)
    registry.add("move_to",
                 "Walk the character somewhere and wait until she gets there. The motion clips are all "
                 "in-place, so playing a walk does not move her — this does. Somewhere can be an "
                 "object or anchor by id, or a place: 'near:<object_id>' is beside a thing rather than "
                 "at it, and 'view:left' / 'view:right' / 'view:ahead' / 'view:behind' are relative to "
                 "whoever is watching, for a request like 'go to the right of my view'. Call it before "
                 "anything that has to happen AT a particular place.",
                 MOVE_PARAMS, move_to)
    registry.add("plan_motion",
                 "Combine one base action with optional overlays, bind hands and gaze to scene objects, "
                 "and play it. The body-channel split is derived for you from the actions you name, and "
                 "so is which way she ends up facing — the object an action touches is what she faces. "
                 "Pass walk_to to have her walk there and start the motion on arrival, in one call. "
                 "Use dry_run first to see the split and the checks.",
                 PLAN_PARAMS, plan_motion)
    return registry


def _posture_gate(base, overlays, kb):
    """The most fundamental check and the cheapest, so it runs first and alone."""
    postures = {aid: kb.record(aid).get("composability", {}).get("posture")
                for aid in [base] + list(overlays)}
    distinct = {p for p in postures.values() if p}
    if len(distinct) <= 1:
        return {"id": "posture", "status": "pass",
                "detail": "all %s" % (distinct.pop() if distinct else "unspecified")}
    return {"id": "posture", "status": "fail",
            "detail": "cannot mix postures: "
                      + ", ".join("%s is %s" % (a, p) for a, p in sorted(postures.items()) if p)}


def _structural_gates(assembly, kb, ik_bindings):
    """The rest of the cheap checks, run before anything touches the engine. Microseconds, pure Python.

    They exist so the model can iterate on a plan without a round trip, and so a rejection names what is
    wrong in the model's own vocabulary — action ids and body parts, never coordinates.
    """
    gates = []
    owned = {c for _, chans in assembly.layers for c in chans}
    gates.append({"id": "partition", "status": "pass" if not assembly.conflicts else "fail",
                  "detail": "%d channel(s) driven, %d free"
                            % (len(owned), len(assembly.free_channels))})

    # WHAT THE CLIP ALREADY DOES IS NOT MISSING. This used to report every hand contact that had no
    # ik_binding as "not bound to anything in the scene", and that sentence is what caused the damage:
    # the model read it as an instruction and bound both hands to the laptop, which has one grab
    # anchor, so both wrists were pulled onto the same point -- measured, right hand 0.000 m from it
    # and left hand 0.065 m. That is clasping, not typing.
    #
    # The knowledge base says so plainly and the check simply was not reading it. `typing` records both
    # hands as `role: primary` with `contact: object:keyboard` and `constraint: must-maintain`: the
    # clip animates those hands against a keyboard already. Nothing has to reach for anything. What has
    # to be true is that the CHARACTER is at the laptop, which is what move_to and sit_on are for.
    #
    # A hand a contact is declared on but which this plan does not drive is a different matter, and
    # still reported.
    held_by_clip, adrift = [], []
    for aid, chans in assembly.layers:
        record = kb.record(aid)
        for channel in chans:
            if channel not in ANATOMICAL or channel not in ("left_hand", "right_hand"):
                continue
            spec = record.get("channels", {}).get(channel) or {}
            contact = spec.get("contact") or ""
            if not contact.startswith("object:"):
                continue
            entry = (channel, contact[len("object:"):], aid)
            (held_by_clip if spec.get("role") in ("primary", "stabilizer") else adrift).append(entry)

    gates.append({
        "id": "contact_grounded",
        "status": "pass" if not adrift else "warn",
        "detail": ("no hand contacts to ground" if not held_by_clip and not adrift else
                   "; ".join(filter(None, [
                       ("animated against its object by the clip itself: "
                        + ", ".join("%s on %s (%s)" % m for m in held_by_clip))
                       if held_by_clip else "",
                       ("driven without its contact: "
                        + ", ".join("%s should touch %s (%s)" % m for m in adrift))
                       if adrift else ""]))),
        "hint": ("put the character at the object -- move_to it, and sit_on it if the action is "
                 "seated. Do not bind these hands yourself: where the object says where each hand "
                 "goes, they are bound to those anchors for you, and where it does not, aiming two "
                 "hands at one grab point pulls both wrists onto it.") if held_by_clip else None,
        # Carried out of the gate because it is measured, not only reported. The clip says these hands
        # meet an object; whether they meet the real one depends on where she ends up standing or
        # sitting, and that is a geometric question the engine can answer once the motion is playing.
        "clip_contacts": [{"effector": e, "contact": c, "from_action": a} for e, c, a in held_by_clip],
    })
    return gates
