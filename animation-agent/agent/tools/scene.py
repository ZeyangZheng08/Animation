"""
scene.py — the model's window onto the 3D scene, and the tool that commits a motion.

WHY THE SCENE IS QUERIED AND NOT PUSHED. `EmergencyRoom.unity` holds 600 GameObjects and almost none of
them matter to any given motion. Pushing a snapshot would be both useless and unaffordable: the model has
a 32k window. So the engine enumerates deterministically over an annotated registry and answers typed
predicates, and the model narrows to a handful of candidates over a few calls.

TWO QUESTIONS, TWO TOOLS. `scene_search` answers "which thing is that?" and returns identity alone:
an id, a label, the contact names the motion library spells it by. `scene_query` answers "where does that
leave her?" and returns the relation: does it exist, is it within reach, does she have to walk, is somebody
holding it. Nothing else crosses. The four tools this replaced also handed out categories, surface heights,
carriability, per-hand anchor flags and metres, and every one of those was a fact about the deterministic
backend rather than about what the model has to decide. Measured on real turns, they were mostly used to
guess wrong: an invented `category` filtered a room down to nothing, and `carriable` invited the model to
plan around a capability the executor validates anyway.

WHAT COMES BACK IS SYMBOLIC. Identity and coarse relations. No transforms, no distances, no extents, no
angles. Exact pose stays engine-side where the IK solver, the geometric gates and the sit/carry logic
consume it directly -- see `_verify_seat`, which still reads a surface height, because a deterministic
check may hold numbers the model may not. That split is the architecture's claim that the language model
never handles motion numerics, and it is enforced by the shape of the reply rather than by asking nicely.

THE UNDERLYING PROTOCOL IS UNCHANGED. `scene.find`, `scene.describe`, `scene.anchors` and `scene.position`
are still what the engine speaks; they are engine-internal API now, reached through this file and never
declared to the model. Collapsing the tool surface and rewriting the wire in one change would have put
`plan_motion`, sitting, navigation and IK all in the blast radius of the same edit.

NOTHING VISIBLE MOVES UNTIL THE PLAN HAS BEEN CHECKED. `plan_motion` compiles the whole thing once --
steps, layers, channel windows, generated posture change, IK bindings, contacts, carry -- and then sends
that same compiled plan twice: once as `mode: "validate"`, which the executor runs on a hidden duplicate
of the character at fixed timestep, and only on a pass as `mode: "commit"`. The walk is inside that
fence too: where a walk would put her is PREVIEWED rather than performed, and the motion that follows is
judged at the projected arrival, so a plan that cannot work does not get as far as walking her across
the room to find out. The model still sees two modes, `dry_run` and `commit`; `validate` is between this
file and the engine and costs no model round trip.

WHY NOT JUST READ THE GATE AFTERWARDS. That is what `check_motion` does, and it can only ever say what
already happened -- measured on real turns, a sit that landed nowhere was reported seconds after a
viewer had watched it. The runtime gate is kept (see §2.11 of the design, and `check_motion` below) but
its job changed: it is now watching for the scene moving out from under a plan that was already checked,
not deciding whether the plan was any good.

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
from .. import kbindex as KI
from ..kbindex import ANATOMICAL
from .registry import ToolFailure

SEARCH_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "query": {
            "type": "string",
            "description": "A word for the thing: what it is called, or the contact name the motion "
                           "library spells it by -- pills, aspirin_bottle, patient_chest, "
                           "patient_wrist, bvm_mask, keyboard. Places count as things: 'bedside' "
                           "finds the bedside anchor. Leave it out entirely to list everything this "
                           "scene has been annotated with, which is the cheapest correct answer to "
                           "'is there a chair?'.",
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
    },
}

QUERY_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "object_ids": {
            "type": "array", "minItems": 1, "maxItems": 8,
            "items": {"type": "string", "description": "id from scene_search."},
        },
        "relative_to": {
            "type": "string",
            "description": "Whose point of view. A character name or id; with one character in the "
                           "scene it is hers and can be left out.",
        },
    },
    "required": ["object_ids"],
}

# The model picks a word; the metres stay here. Same reasoning as everywhere else on this surface:
# "beside it" is a decision a model can make and "0.35 m" is one it cannot.
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
            "description": "Where to walk. Any id scene_search returned -- an object or a named "
                           "place -- or 'near:<object_id>' for beside a thing rather than at it, or "
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

PLAN_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "character": {"type": "string", "description": "Only needed when more than one character is connected; there is normally one and it is used by default."},
        "base": {"type": "string", "description": "action_id of the action that sets the posture. It "
                                                  "animates the whole body underneath everything "
                                                  "else, so whatever no overlay drives comes from it."},
        "base_channels": {
            "type": "array", "maxItems": 8,
            "items": {"type": "string", "enum": list(ANATOMICAL)},
            "description": "Body parts the base OWNS, when you want to say so. The base animates the "
                           "whole body regardless; naming parts here reserves them, so an overlay "
                           "asking for one of them contends with the base instead of simply taking "
                           "it. Leave it out unless you mean to reserve something.",
        },
        "overlays": {
            "type": "array", "maxItems": 3,
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "action_id": {"type": "string", "description": "action grafted onto the base."},
                    "channels": {
                        "type": "array", "minItems": 1, "maxItems": 8,
                        "items": {"type": "string", "enum": list(ANATOMICAL)},
                        "description": "Which body parts this overlay drives.",
                    },
                },
                "required": ["action_id", "channels"],
            },
            "description": "Actions layered on top of the base, each with the body parts it drives. "
                           "YOU decide that split, and it is the decision this tool cannot make for "
                           "you: whether a walk's arm swing is incidental or is the point depends on "
                           "what she is doing, and the library describes one clip at a time. Name "
                           "only what the overlay is FOR -- carrying a bottle while walking is the "
                           "arm and the hand that hold it, not the torso that leans with them. Two "
                           "overlays naming the same part get half of it each.",
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
                           "For something small enough to pick up — a pill bottle, a bag valve mask. "
                           "Everything else is used where it stands, however small it looks: a laptop "
                           "is typed on at the desk it is on, and carrying it takes it with her. To "
                           "touch one of those, bind a hand to it with ik_bindings instead. Ask for "
                           "the carry you mean; whether that object can be picked up is checked "
                           "engine-side and refused by name if it cannot. Needed for anything "
                           "carried, since prop visibility is not implied by the motion.",
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
                    "base_channels": {"type": "array", "maxItems": 8,
                                      "items": {"type": "string", "enum": list(ANATOMICAL)}},
                    "overlays": {
                        "type": "array", "maxItems": 3,
                        "items": {
                            "type": "object", "additionalProperties": False,
                            "properties": {
                                "action_id": {"type": "string"},
                                "channels": {"type": "array", "minItems": 1, "maxItems": 8,
                                             "items": {"type": "string", "enum": list(ANATOMICAL)}},
                            },
                            "required": ["action_id", "channels"],
                        },
                    },
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
            "description": "Leave this out to play the motion — it is checked against the scene "
                           "out of sight first, so a plan that does not work is refused rather than "
                           "played. dry_run derives the plan and runs only the cheap structural "
                           "checks, for when you want to see what a plan resolves to first.",
        },
    },
    "required": ["base"],
}


# THE BLANK-CLAUSE DEFENCE IS GONE BECAUSE THE CLAUSES ARE. `near`, `reachable_by` and `category` were
# the fields a model filled in because the schema offered them, and a blank one forwarded as a real
# constraint is what hid a chair that was across the room. `scene_search` takes one word and a limit, so
# there is nothing left to fill in wrongly. The engine keeps its own version of the same guard, because
# `scene.find` is still reachable from inside this file.


def _settled_at(step):
    """When a step's contacts fall due: its start, plus the posture change it is reached through.

    A step reached through a generated posture change is not settled when it starts -- the descent is
    still running through it. Measured: the worst contact error on a walk-sit-type plan landed at
    0.12 m mid-descent, with the hands correct before and after, because the torso was still on its
    way down. The contact falls due when the change finishes.
    """
    due = step.get("start_at_s") or 0.0
    if step.get("generated"):
        due += step["generated"].get("duration_s") or 0.0
    return round(due, 4)


def _binding_due(steps, effector):
    """When an IK binding for this hand should engage.

    ONE ANSWER FOR THE CONTACT THE GATE MEASURES AND THE BINDING THE ENGINE APPLIES, because they are
    the same moment. Computing them separately is how a gate came to judge from frame zero a hand
    that a binding would not reach for until three seconds in.

    A BINDING BELONGS TO THE STEP THAT REACHES FOR SOMETHING, NOT TO THE PLAN. Everything used to be
    applied at commit, so a plan that walked to a laptop and then typed on it pulled both wrists onto
    the keyboard anchor from the first frame -- through the whole walk. Measured on a real turn: the
    walk played with her arms stretched behind her toward the desk, and nothing reported it, because
    every check the gate runs is about where she ENDS up.

    WHICH STEP, in a v4 plan. It used to be read off the knowledge base -- `typing` recorded both
    hands as `contact: object:keyboard`, `walking` recorded them as `free / none`, so the step that
    touched something identified itself. v4 records say no such thing (ADR 0022), so the plan is asked
    instead, in two passes:

      1. A step where some layer explicitly drives this hand. That is the agent saying "this action,
         here, is what this hand is doing", which is exactly the step the binding belongs to.
      2. Failing that, the LAST step -- because a plan with several steps that pins a hand is pinning
         it to what it walks towards, not to what it is walking away from. On a single-step plan the
         last step is the first, so the binding engages at zero, which is the same answer as before.

    Either way the moment is the step's SETTLED time, not its start: a hand cannot be on a keyboard
    while the descent that puts her in front of it is still running. See `_settled_at`.
    """
    for step in steps:
        for layer in step.get("layers") or []:
            if effector in (layer.get("channels") or []):
                return _settled_at(step)
    return _settled_at(steps[-1]) if steps else 0.0


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


# WHAT TO CHANGE, PER REASON. The engine names the metric, the effector or object it is about, and a
# reason slug; this turns the slug into the one sentence a model can act on. Kept as a table rather than
# built into the message because the four things a plan can be wrong about -- the motion, the target,
# the composition, the route -- are the four different repairs, and a failure that does not point at one
# of them sends the model rewriting arguments that were already right.
_REPAIR = {
    "pelvis_outside_support":
        "the sit does not land on the seat. Name a real seat as `sit_on` and let it walk her there "
        "rather than placing her yourself.",
    "pelvis_below_support":
        "she would end up underneath that, not on it. It is something to work AT: pass a chair or a "
        "stool as `sit_on` and reach the other thing from there.",
    "hip_did_not_reach_target":
        "the generated posture change does not arrive at the pose the seated action opens on. Name a "
        "seat as `sit_on`, and name the standing action and the seated one in one call so the frames "
        "between them are made.",
    "hand_left_its_object":
        "a bound hand cannot stay on its object through this motion. Bind one hand rather than two, "
        "or leave the binding out and let the clip animate the hand itself.",
    "hand_never_reached_object":
        "she is not where this action expects its object to be. Pass `walk_to` so the motion happens "
        "at the thing, and `sit_on` as well if the action is seated.",
    "foot_through_floor":
        "the parts of this do not fit together on the legs. Try a different overlay, or play the two "
        "actions one after the other with `then`.",
    "correction_discarded":
        "the generated frames did not reach the character. This is a defect on the engine side, not "
        "something to fix by rewording the plan.",
    "descent_saturated":
        "the posture change asks for more travel than the body has. Check the seat is the height a "
        "seat should be, or play the seated action without generating a change into it.",
}

_REPAIR_DEFAULT = ("change the motion, the thing it is aimed at, or the arrangement, and send it "
                   "again.")


def _validation_failure(verdict):
    """The refusal a model can act on, out of a verdict the engine measured.

    NAMES THE PART, NOT THE NUMBER. A metre is not something the model can reason with and it is not
    something it may write, so what crosses is which check failed, on what, and what to change. The
    numbers stay in the verdict on the wire for the trace and for a person reading it.
    """
    failures = verdict.get("failures") or []
    if not failures:
        return ToolFailure(
            "this plan did not pass the check that runs before anything is played",
            hint="nothing moved. " + _REPAIR_DEFAULT)
    named, repairs = [], []
    for failure in failures:
        subject = failure.get("effector") or failure.get("object_id")
        named.append("%s%s" % (failure.get("metric") or failure.get("reason") or "check",
                               " on %s" % subject if subject else ""))
        repairs.append(_REPAIR.get(failure.get("reason"), _REPAIR_DEFAULT))
    return ToolFailure(
        "this plan was played on a hidden copy of her first and it does not work: %s"
        % ", ".join(named),
        # dict.fromkeys rather than set: two failures with the same repair say it once, in order.
        hint="nothing was played and nothing moved. " + " ".join(dict.fromkeys(repairs)))


def _bad_split(e):
    """A channel assignment the plan got wrong, said in the shape that would have been right.

    The example matters more than the message: what the model has to change is the SHAPE of an
    overlay entry, and an error naming the missing field without showing where it goes sends it
    rewriting the action ids instead.
    """
    return ToolFailure("could not build the channel split: %s" % e,
                       hint="every overlay names the body parts it drives, e.g. "
                            "overlays: [{\"action_id\": \"grab_bottle\", "
                            "\"channels\": [\"right_arm\", \"right_hand\"]}]")


def _engine_failure(e):
    if isinstance(e, EngineUnavailable):
        return ToolFailure("the 3D scene is not connected", hint="the engine is not running; "
                                                                "answer from the motion library alone")
    if isinstance(e, EngineTimeout):
        return ToolFailure("the 3D scene did not answer in time")
    return ToolFailure("%s: %s" % (e.code, e.msg))


def register(registry, engine, kb=None):
    """Attach the scene and plan tools. `kb` enables the assembly derivation in plan_motion."""

    # The per-channel segment table, read once. Built live if the sidecar is missing or `raw` has
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

    async def _validate(payload, at=None):
        """Run this exact plan on the hidden copy and raise if it does not work. Returns what the
        check covered, for the record.

        THE ONE THING THIS MUST NOT DO IS DERIVE ANYTHING. It takes the compiled plan and changes one
        word in it. A validator that rebuilt the plan would be checking a different plan.
        """
        probe = dict(payload, mode="validate")
        if at:
            probe["at"] = at
        verdict = await _call(P.T.MOTION_ASSEMBLE, probe)
        if verdict.get("status") == "fail":
            raise _validation_failure(verdict)
        return {key: verdict[key] for key in
                ("status", "checked", "samples", "seconds_simulated", "unmeasured")
                if key in verdict}

    async def _assemble(payload, at=None, checked=False):
        """Send one compiled plan. A commit is checked out of sight first, and only then played.

        ONE COMPILE, TWO SENDS, THE SAME BYTES. The plan is built once and this dictionary is what
        goes over the wire both times, so what was validated and what plays cannot differ. Deriving
        the plan again between the two would reintroduce exactly the gap this exists to close: the
        check would be about a plan that no longer describes what is about to happen.

        `at` is where to stand the hidden copy: the projected arrival of a walk that has not happened
        yet. Absent, she is checked where she is.

        `checked` says a caller has already validated these same layers -- the walk cycle that
        `_walk_there` starts, which `plan_motion` validated before it began walking. Without it the
        walk would be re-checked from inside the walk, which is a second round trip about a plan whose
        verdict is already in hand.

        THIS IS AN ENGINE ROUND TRIP, NOT A MODEL ONE. It costs about a millisecond on the wire plus
        whatever the fixed-step evaluation takes, and no iteration of the model's own loop -- which is
        the whole reason `validate` is not a mode the model can see. A tool that asked the model to
        plan, check, then commit would spend two thirds of a turn deciding to do what it had already
        decided.
        """
        if payload.get("mode") != "commit" or checked:
            return await _call(P.T.MOTION_ASSEMBLE, payload)
        verdict = await _validate(payload, at)
        played = await _call(P.T.MOTION_ASSEMBLE, payload)
        # Said back so the trace records that the check ran, and what it covered. A plan reported as
        # committed with no verdict beside it is indistinguishable from one that skipped the check.
        played["validated"] = verdict
        return played

    async def _preview_walk(character, destination, stop_within=None, face=None):
        """Where a walk would put her, without taking a step.

        THE WALK USED TO BE THE FIRST THING THAT HAPPENED, and that is the one mutation a plan could
        not take back: she crossed the room, and only then did the motion she crossed it for turn out
        to be impossible. Measured against the design's own rule -- a failed plan must not be visible
        -- a character standing at a chair she cannot sit on is exactly as visible as a bad sit.

        So the route is computed and the arrival projected first, and the motion is validated AT that
        arrival. The engine answers with a point and a heading; neither reaches the model.
        """
        destination = await _resolve_id(destination)
        face = await _resolve_id(face)
        params = {"character": character, "preview": True, "to": destination}
        if stop_within is not None:
            params["stop_within_m"] = _STOP_WITHIN.get(stop_within, 0.35)
        if face:
            params["face"] = face
        data = await _call(P.T.MOTION_LOCOMOTE, params)
        if not data.get("reachable"):
            raise ToolFailure(
                "she cannot get to %s: %s" % (destination, data.get("why") or "no complete route"),
                hint="nothing has moved. Pick somewhere she can walk to -- scene_search lists the "
                     "room -- or name the thing rather than a place beside it.")
        preview = {"destination": destination,
                   # WHETHER THERE IS A WALK AT ALL. A destination she is already standing at is a
                   # walk of zero length: nothing plays, and the plan that follows departs from
                   # whatever she is doing now rather than from a walk cycle. Getting this wrong is
                   # how she came to march on the spot in front of the desk before sitting down, so
                   # the engine is asked rather than assumed at.
                   "will_walk": not bool(data.get("arrived"))}
        for key in ("path_length_m", "eta_s", "arrival", "facing_deg"):
            if data.get(key) is not None:
                preview[key] = data[key]
        if face:
            preview["facing"] = face
        return preview

    def _standing_at(preview):
        """The `at` clause for a validation run: where the walk will have left her. None when there is
        no walk, which stands the hidden copy where the real one is."""
        if not preview or preview.get("arrival") is None:
            return None
        at = {"position": preview["arrival"]}
        if preview.get("facing_deg") is not None:
            at["facing_deg"] = preview["facing_deg"]
        return at

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

    async def _anchor_entities():
        """The named places, as search hits.

        Merged into every listing rather than kept behind a tool of its own. An anchor IS an entity
        here — `anchor:Bedside` is as valid a destination as `obj:Chair` — and giving it a separate
        tool made "walk to the bedside" a two-call question whose first call the model had no reason
        to make. `scene.find` already returns anchors on a name search, so this is belt and braces
        for the bare listing, where the engine's limit could otherwise truncate them away.
        """
        out = []
        for entry in (await _call(P.T.SCENE_ANCHORS, {})).get("anchors") or []:
            if isinstance(entry, str):
                out.append({"id": "anchor:" + entry.replace(" ", ""), "label": entry, "aliases": []})
            elif entry.get("id"):
                out.append({"id": entry["id"], "label": entry.get("name") or entry["id"],
                            "aliases": []})
        return out

    async def scene_search(query=None, limit=10):
        """Which thing is that. Identity, and deliberately nothing else.

        WHAT THIS DOES NOT RETURN, AND WHY THAT IS THE POINT. No category, no surface height, no
        transform, no distance, no grab anchor, no `carriable`. Every one of those is a fact the
        deterministic backend consumes and the model can only guess with. Measured on real turns, the
        guessing is what cost tasks: an invented `category` filtered a room with a chair in it down to
        nothing, ten times in one turn, and the agent concluded there was no chair. The registry is a
        couple of dozen entries, so the honest answer to any question about it is a list.

        A bare call lists everything. That used to be refused, which is how absence came to be inferred
        from repeated misses instead of read off a list.
        """
        query = (query or "").strip()
        results, seen = [], set()

        # A CHARACTER'S NAME IS THE ONE A PERSON SAYS, AND THE REGISTRY DOES NOT HOLD IT.
        #
        # The scene calls her `CPRNurse` and the protocol calls her `chr:CPRNurse`; a person calls her
        # Jill, and that spelling lives only in the executor's handshake. This projection returns
        # identity and nothing else, so dropping it left the model reading a list of three nurses with
        # no Jill among them -- measured on a live turn, it stopped and asked which character Jill was
        # supposed to be, about a scene she is standing in. `_who` had always accepted the name; there
        # was simply nowhere to learn it.
        #
        # So the spoken name becomes the label and the other two spellings become aliases. All three
        # resolve, which is what `_who` already did with whatever it was handed.
        names = dict((engine.hello or {}).get("character_names") or {})

        def take(items):
            for item in items:
                object_id = item.get("id")
                if not object_id or object_id in seen:
                    continue
                seen.add(object_id)
                label = item.get("name") or item.get("label") or object_id
                aliases = list(item.get("aliases") or [])
                spoken = names.get(object_id)
                if spoken and spoken != label:
                    aliases = [a for a in [label] + aliases if a != spoken]
                    label = spoken
                results.append({"id": object_id, "label": label, "aliases": aliases})

        if not query:
            # A generous limit and no truncation: completeness is the whole reason to call this with
            # nothing. `inventory` is what the engine hands back when a search fails, and it is the
            # same list from the other side, so it is folded in rather than ignored.
            data = await _call(P.T.SCENE_FIND, {"limit": 100})
            take(data.get("objects") or [])
            take(data.get("inventory") or [])
            take(await _anchor_entities())
            return {"results": results, "count": len(results),
                    "note": "everything this scene has been annotated with. What is not here is not "
                            "annotated; searching again with different words will not add to it."}

        # Name first, then contact alias. Both are matched by the engine against the label, the scene
        # object's own name and the alias list, so this is two passes and not seven filters.
        raw_only = False
        for key in ("name_contains", "alias"):
            data = await _call(P.T.SCENE_FIND, {key: query, "limit": limit})
            hits = data.get("objects") or []
            raw_only = raw_only or any(hit.get("source") == "scene" for hit in hits)
            take(hits)
            if len(results) >= limit:
                break

        if not results:
            folded = query.lower()
            take([a for a in await _anchor_entities()
                  if folded in a["id"].lower() or folded in a["label"].lower()])

        result = {"results": results[:limit], "count": min(len(results), limit)}
        if not results:
            result["note"] = ("nothing here is called %r. Call scene_search with no query to see "
                              "everything there is; the list is short and it is complete." % query)
        elif raw_only:
            # NOTHING IS INVENTED FOR THESE. They came out of a raw scene-name search rather than the
            # annotated registry, so they have no contact aliases and nothing is known about them
            # beyond existing. Saying so is what stops the model planning against an affordance that
            # was never authored.
            result["note"] = ("some of these were found by raw name in the scene rather than in the "
                              "annotated registry: nothing is known about them beyond that they are "
                              "there.")
        return result

    async def _held_by(object_id):
        """Who is holding this, or None. One extra round trip at 0.3 ms, and it is the one relation
        `scene.position` cannot answer because holding is a parenting fact, not a distance."""
        try:
            return (await _call(P.T.SCENE_DESCRIBE, {"object_id": object_id})).get("held_by")
        except ToolFailure:
            return None

    async def scene_query(object_ids, relative_to=None):
        """What these things are to her right now: does it exist, can she reach it, must she walk,
        is somebody already holding it.

        THE RELATION IS THE ANSWER, NOT THE GEOMETRY BEHIND IT. This used to return position, distance
        in metres, bearing and surface height, and the reversal is deliberate: the decisions those
        numbers were read for are "walk first or not" and "is it in reach", and both come back here as
        booleans the engine derived from the same measurement. The numbers have not gone anywhere —
        `_verify_seat` still reads a surface height, the descent still gets a hip target, the gate
        still judges in metres — they simply stop passing through the model.
        """
        if not object_ids:
            raise ToolFailure("name at least one object", hint="use scene_search to get ids first")

        # Through _who, like every other tool that names a character: the engine knows ids and an
        # instruction says a name, and a `relative_to` that skipped the resolution silently measured
        # from nobody — the engine treats an unknown character here as "no reference point" rather than
        # as an error, so the relation simply came back absent. Unnamed resolves to the only character
        # there is; with several and none named the relation is omitted and said to be omitted, rather
        # than measured from whoever happened to be first.
        who, ask = None, None
        if relative_to:
            who = _who(relative_to)
        else:
            try:
                who = _who(None)
            except ToolFailure as e:
                ask = e.message

        data = await _call(P.T.SCENE_POSITION,
                           {"object_ids": list(object_ids), "relative_to": who})
        answered = {o.get("object_id"): o for o in data.get("objects") or []}

        objects, missing = [], []
        for object_id in object_ids:
            raw = answered.get(object_id) or {}
            item = {"id": object_id, "exists": bool(raw.get("found"))}
            if not item["exists"]:
                missing.append(object_id)
                objects.append(item)
                continue
            relation = raw.get("from_character") or {}
            if who and relation:
                item["within_arms_reach"] = bool(relation.get("within_arms_reach"))
                item["needs_walking"] = bool(relation.get("needs_walking"))
            item["held_by"] = await _held_by(object_id)
            objects.append(item)

        result = {"objects": objects}
        if who:
            result["relative_to"] = who
        if missing:
            result["note"] = ("no object with id %s; ids come from scene_search"
                              % ", ".join(repr(m) for m in missing))
        elif ask:
            result["note"] = ("%s — so whether these are in reach is not answered here. Ask again "
                              "with relative_to." % ask)
        return result

    def _one_step(base, overlays, hold_final_pose, ik_bindings, base_channels=None, pinned=None):
        """Build one step's channel split and its gates. Raises ToolFailure with the reason a model
        can act on.

        `overlays` is [{"action_id":…, "channels":[…]}] -- the split the AGENT decided. Through v3 it
        was a list of bare action_ids and this function derived the split from the KB's `role` labels;
        motionkb/v4 deletes those (ADR 0022) because which part of a clip matters depends on the task,
        which only the agent can see. `pinned` are the channels this plan attaches to a scene object.
        """
        # THROUGH THE SAME REFUSAL AS THE ARBITRATION BELOW. This normalisation is where a malformed
        # overlay is actually caught -- `arbitrate` normalises again, but by then the list is already
        # clean -- so leaving it outside the guard turned the one error a model can fix by itself, an
        # overlay that names no channels, into "plan_motion failed internally".
        try:
            overlays = A.normalise_overlays(overlays) if overlays else []
        except ValueError as e:
            raise _bad_split(e)
        unknown = [a for a in [base] + [aid for aid, _ in overlays] if a not in kb.actions]
        if unknown:
            raise ToolFailure("unknown action_id: %s" % ", ".join(unknown),
                              hint="use kb_search and pass an action_id it returns")

        # Posture first: two actions that cannot share a stance cannot be combined however the channels
        # fall out, and reporting a channel conflict instead would send the model looking for a
        # different overlay when the real problem is that one of them is seated.
        overlay_ids = [aid for aid, _ in overlays if aid != base]
        posture = _posture_gate(base, overlay_ids, kb)
        if posture["status"] == "fail":
            # NAMES THE SHAPE THAT WORKS, because the old hint -- "choose actions with the same
            # posture" -- sent the model looking for a different action when the actions were right and
            # only the arrangement was wrong. Measured: three iterations in one turn spent putting
            # `typing` in `overlays` alongside a standing base, then the turn ran out of budget.
            # Overlays play AT THE SAME TIME, which two postures cannot; `then` plays them in order,
            # and that is the one that makes the frames in between.
            seated = [a for a in [base] + overlay_ids if _posture_of(a) == "seated"]
            raise ToolFailure(
                posture["detail"],
                hint="overlays play at the same time as the base, and two postures cannot happen at "
                     "once. To do one AFTER the other, pass %s in `then` instead -- with `sit_on` "
                     "naming something to sit on, that is what generates the frames between standing "
                     "and seated." % (seated[0] if seated else "the seated action"))

        try:
            assembly = A.arbitrate(base, overlays, kb, base_channels=base_channels,
                                   pinned_channels=pinned)
        except ValueError as e:
            raise _bad_split(e)
        except Exception as e:                       # noqa: BLE001
            raise ToolFailure("could not build the channel split: %s" % e)

        if assembly.conflicts:
            # NAMING THE REASON, NOT JUST THE CHANNEL. A channel two actions both name is normally
            # shared between them, half each. What reaches here is the case that cannot be halved: the
            # plan pinned that hand to something in the scene, so a blend of two grips would hold
            # neither, and the model needs to know which pair to break up rather than that "they
            # conflict".
            names = ", ".join("%s (%s)" % (c.channel, c.why()) for c in assembly.conflicts)
            raise ToolFailure(
                "these actions cannot both drive the same body part: %s" % names,
                hint="a hand bound to an object cannot be blended out of two motions. Give that part "
                     "to one of the actions, drop the binding, or do the two actions one after the "
                     "other with `then`.")

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
        return assembly, layers, gates

    # `_asked_objects` lived here. It resolved the KB's contact aliases (`pills`) against the scene's
    # ids (`obj:PillBottle`) so that arbitrate could tell which of two competing grips the request had
    # asked for. v4 records declare no grips (ADR 0022), so there is no second grip to weigh against
    # the request: what a hand holds is named once, by the plan, in `carry` or `ik_bindings`, and it
    # is already a scene id. The alias vocabulary survives in scene_search, where a person still types
    # "pills".

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

    def _in_place_payload(character, action_id, from_action=None, overlays=None, carry=None):
        """The plan for one action played under a displacement -- see move_to -- with whatever is
        grafted onto it. BUILT, not sent, so the same dictionary can be checked before she moves and
        then committed byte for byte.

        OVERLAYS TRAVEL WITH THE WALK, and that is the point of the parameter. Every clip here is
        in-place: the navigation agent moves the transform and this plays the animation, and the two
        run side by side. So a composed motion is no harder to play while she crosses the room than a
        bare one -- it was simply never handed one. Without that, the only composition this corpus can
        express is "X while walking", and it played after the walking had finished.
        """
        assembly, layers, _ = _one_step(action_id, list(overlays or []), None, None)
        return {
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
            "ik": [], "gaze_at": None, "stand_at": None, "carry": list(carry or []), "mode": "commit"}

    async def _play_in_place(character, action_id, from_action=None, overlays=None, carry=None,
                             payload=None, checked=False):
        """Send one of those. `payload` is a plan a caller already built AND already checked -- the
        walk `plan_motion` validated before it started walking -- and passing it here is what stops
        the same plan being validated twice."""
        await _assemble(payload or _in_place_payload(character, action_id, from_action, overlays,
                                                     carry),
                        checked=checked)

    async def _walk_there(character, destination, face=None, stop_within=None, then_wait=True,
                          settle=True, under=None, carry=None, under_payload=None):
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
            #
            # `under_payload` is that same plan, built and checked by the caller before she set off.
            # Sent verbatim: what was validated and what plays have to be the same thing, and
            # rebuilding it here would make them two plans that merely look alike.
            was = await _call(P.T.MOTION_LOCOMOTE, {"character": character, "query": True})
            await _play_in_place(character, LOCOMOTION_ACTION, from_action=was.get("playing"),
                                 overlays=under, carry=carry,
                                 payload=under_payload, checked=under_payload is not None)
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

    def _face_for(bindings, gaze_at=None):
        """What the coming motion is aimed at. Returns (object_id, why) or (None, None).

        THE RULE IS UNCHANGED: a character faces the thing the action she is about to perform
        interacts with. Typing on a laptop means facing the laptop. A seat decides only where she
        sits; it has nothing to say about which way she faces, and taking the facing from it is how
        she came to sit with her back to the desk.

        WHERE IT READS THE ANSWER DID CHANGE. v3 read it off the knowledge base: `typing` spelled both
        hands `contact: object:keyboard`, `cpr` named the chest, and the registry's alias list joined
        `keyboard` to `obj:Laptop`. v4 records say how a hand moves, not what it is on (ADR 0022), so
        the answer comes from the plan, which names the object once and already names it as a scene
        id -- no alias round trip, and no ambiguity to refuse.

        Hands before gaze, and in a fixed order, so the same plan resolves the same way twice. A gaze
        target counts only if nothing is held: looking at a monitor while working on a patient should
        not turn the body away from the patient.

        A plan that pins nothing returns None, which leaves the facing to the route -- also correct:
        there is nothing it is aimed at. That is what `walking` and `idle` on their own now do.
        """
        for effector in ("left_hand", "right_hand"):
            for b in bindings:
                if b.get("effector") == effector and b.get("object_id"):
                    return b["object_id"], "%s is bound to it" % effector
        if gaze_at:
            return gaze_at, "she is looking at it"
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
            hint="that is something to work AT, not to sit on. Pass a seat as sit_on -- "
                 "scene_search('chair') finds one, and a bare scene_search lists the room -- and let "
                 "her reach the other thing from there")

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
                              hint="scene_search('chair') finds a seat, and a bare scene_search "
                                   "lists everything there is")
        if found.get("surface_height_m") is None:
            raise ToolFailure("%r has no measurable surface to sit on" % object_id,
                              hint="that is not something with a seat. Pass a chair or a stool; "
                                   "scene_search('chair') finds one")
        return found

    async def _pair_bound_hands(ik_bindings):
        """When one hand is bound to something that says where BOTH hands go, bind the other too.

        THE SCENE KNOWS THIS AND NOTHING ELSE DOES. The laptop carries four authored transforms saying
        where each hand and each elbow goes; the demo path engages them and the hands land 0.000 m
        from it. An agent binding one hand and leaving the other to the clip gets one hand on the
        keyboard and one hovering a fifth of a metre under it.

        The rule is the OBJECT's, not the model's and not the library's: two hands may be aimed at
        something exactly when it says where both of them go. Where it does not -- a bottle, a button
        -- this adds nothing and the other hand keeps the clip's own motion. Neither side of that is a
        judgement call, so neither is left to one.

        v3 read the pairs out of the knowledge base instead, off `channels.*.contact` -- `typing`
        declared both hands on `keyboard`, so both were bound without the agent saying anything. A v4
        record does not say what a hand holds (ADR 0022), so the agent names one binding and the
        object supplies the second.

        LOOKED UP BY NAME, THEN NARROWED BY ID. `scene.find` filters on the name, the alias list and
        the category; `id` is not one of its predicates, and the engine keeps the same blank-clause
        guard `scene_search` describes -- so asking for one was asking for no filter at all, which
        comes back as the whole registry and never as the single hit this needs. The id is still what
        decides: the name search is the only way in, and the exact match is taken out of what it
        returns rather than trusted to be the first row.
        """
        bound = {b["effector"] for b in ik_bindings if b.get("effector")}
        added = []
        for binding in list(ik_bindings):
            other = {"left_hand": "right_hand", "right_hand": "left_hand"}.get(binding.get("effector"))
            if not other or other in bound:
                continue
            object_id = binding["object_id"]
            found = (await _call(P.T.SCENE_FIND,
                                 {"name_contains": object_id.split(":", 1)[-1]})).get("objects") or []
            hits = [hit for hit in found if hit.get("id") == object_id]
            if len(hits) != 1 or not hits[0].get("two_handed_anchors"):
                continue
            bound.add(other)
            added.append({"effector": other, "object_id": hits[0]["id"],
                          "because": "%s says where both hands go, and %s is already on it"
                                     % (hits[0]["id"], binding["effector"])})
        return added

    def _expect_contacts(bindings, steps):
        """What the plan says a hand will touch, and when the engine should start measuring it.

        NOT A BINDING -- the bindings are applied separately. This names what to MEASURE, so that "her
        hands are on the laptop" becomes a number instead of something to squint at.

        It is worth measuring because the geometry does not always work out and nothing said so.
        Measured on `typing` against this scene: the clip types at 0.70 m above the floor and 0.33 m in
        front of the root, the laptop's deck sits at about 0.90 m, and sitting on a 0.41 m chair puts
        her hands a fifth of a metre under the keyboard. Every check passed and the hands looked wrong,
        which is the same shape of failure as the sit that landed on nothing.

        `due_at_s` is when the step that pins the hand starts -- the same moment the binding engages,
        computed by the same function, so the check and the thing it checks cannot drift apart.
        Without it the whole plan is judged by its worst frame, and a hand cannot be on a keyboard
        while she is still walking to it.
        """
        out, seen = [], set()
        for b in bindings:
            eff, oid = b.get("effector"), b.get("object_id")
            if not eff or not oid or eff in seen:
                continue
            seen.add(eff)
            out.append({"effector": eff, "object_id": oid, "due_at_s": _binding_due(steps, eff)})
        return out

    async def _opening_step(character, base, overlays, then, projected=None):
        """What a plan should open on, and whether that opener is only there to be departed from.

        Returns (action_id, at_rest).

        `projected` IS THE STATE THIS PLAN IS ABOUT TO CREATE, not the one the engine is in. The
        decision below depends on what she is doing when the plan commits, and since the check that
        gates the commit now runs BEFORE the walk, the engine's answer at that moment is about the
        wrong moment -- she is still standing where she was. A plan that is about to walk her
        somewhere and play out of the walk passes what the walk will leave behind, which is the same
        answer the engine used to give a beat later. Nothing is guessed: `_walk_there` plays the walk
        cycle and does not settle it, so `playing` is `walking` and `going` is false by construction.

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
        if projected is not None:
            state = projected
        else:
            try:
                state = await _call(P.T.MOTION_LOCOMOTE, {"character": character, "query": True})
            except ToolFailure:
                return base, False      # no engine to ask: leave the plan exactly as written
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
        """An action's posture, binned from its measured carriage (kbindex.posture_of).

        v3 read `composability.posture`, a label proposed and accepted alongside the rest of the
        semantic half. v4 deletes it (ADR 0022), and this is the one piece of that block worth
        keeping, for two reasons: the ENGINE needs it -- every plan step carries a posture so the
        executor can refuse to walk a seated character off a chair -- and it was never a judgement.
        Where the hips sit over a clip is a measurement, and `mean_body_height` is that measurement.

        Falls back to standing rather than to null, which is what the executor assumes."""
        return KI.posture_of(kb.record(action_id))

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

        # Through the same fence as everything else. Getting up is a generated posture change like any
        # other, and it is the one this file decides on by itself -- the model never asked for it, so a
        # rise that lands badly is a failure nobody would have predicted from the request.
        await _assemble({
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

    async def plan_motion(base, character=None, overlays=None, base_channels=None,
                          hold_final_pose=None, ik_bindings=None,
                          gaze_at=None, stand_at=None, carry=None, then=None, sit_on=None,
                          walk_to=None, stop_within=None, mode="commit"):
        overlays = overlays or []
        # `overlays` is [{action_id, channels}] since v4 -- the agent's own channel split (ADR 0022).
        overlay_ids = [o.get("action_id") if isinstance(o, dict) else o for o in overlays]
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

        # EVERY OBJECT THE MODEL NAMED, RESOLVED BEFORE ANYTHING MOVES. The walk below needs them --
        # a bottle carried across the room has to be in her hand for the crossing -- and so does the
        # partition, which refuses to blend a hand the plan has pinned to something.
        gaze_at = await _resolve_id(gaze_at)
        for binding in (ik_bindings or []):
            binding["object_id"] = await _resolve_id(binding.get("object_id"))
        for held in (carry or []):
            held["object_id"] = await _resolve_id(held.get("object_id"))
        # The channels this plan attaches to the scene. A hand holding something cannot be averaged
        # out of two motions, so arbitrate reports that as a conflict instead of a mix.
        pinned = ({b["effector"] for b in (ik_bindings or []) if b.get("effector")}
                  | {h["hand"] for h in (carry or []) if h.get("hand")})

        # WHAT SHE DOES WHILE SHE IS WALKING. A plan whose base IS the walk is asking for a composed
        # motion -- "walk over holding the bottle out" -- and its overlays belong on top of the walk
        # that gets her there, not after it. Built here so a plan that cannot be assembled is
        # refused before she takes a step, rather than half-played and then rejected.
        while_walking = list(overlays) if (walk_to and overlays and base == LOCOMOTION_ACTION) else None
        if while_walking:
            _one_step(base, while_walking, hold_final_pose, ik_bindings, base_channels, pinned)

        # ON HER FEET BEFORE ANYTHING ELSE. A plan that opens standing while she is seated used to be
        # refused outright; the frames for getting up are generated now, and the only thing that stays
        # true is the order — she cannot travel while the navigation agent is still off. Returns None
        # and costs one query when she is already standing, which is every plan that came before this.
        #
        # This is the one mutation that still happens ahead of the main check, and it is checked in its
        # own right -- `_commit_sequence` goes through the same fence. It has to come first because
        # everything after it is about a character who is standing up: the route preview would measure
        # from a chair, and the hidden copy would be checked in a posture she is about to leave.
        stood_up = await _get_up_first(character, base) if mode == "commit" else None
        if stood_up and not stood_up.get("landed"):
            raise ToolFailure(stood_up.get("note") or "she did not finish standing up",
                              hint="nothing else was played. Read check_motion for how far the rise "
                                   "got before asking for it again.")

        # WHERE THE WALK WOULD PUT HER, WITHOUT WALKING HER THERE.
        #
        # This used to be the walk itself, and it was the one part of a plan that could not be taken
        # back: she crossed the room, and only then did the motion she crossed it for get built and
        # judged. Measured against the rule this whole path exists for -- a plan that fails must not be
        # visible -- a character standing at a chair she cannot sit on is exactly as visible as a bad
        # sit. So the route is computed, the arrival projected, and the motion below is checked AT that
        # arrival. She takes her first step after the verdict, further down.
        #
        # ONE CALL STILL, SO THERE IS NO MODEL TURN IN THE MIDDLE OF THE MOTION. Walking and then
        # sitting used to be move_to followed by plan_motion, and between them sat a model round trip
        # -- measured at 0.85 s on a real turn -- during which she stood at the chair doing nothing.
        # The check that has been inserted is an ENGINE round trip; the model still makes one call.
        preview, aim, aimed_for = None, None, None
        if walk_to and mode == "commit":
            if stop_within is None:
                stop_within = "right_at_it" if sit_on else "beside_it"
            # Which way she ends up facing is decided by the action and what it touches, not by the
            # seat and not by the route -- see _face_for. Resolved before the preview so the projected
            # arrival carries the heading she will actually be at when the motion starts; a hidden copy
            # checked facing the way she walked is checked in the wrong direction, which for a sit at a
            # desk is backwards.
            aim, aimed_for = _face_for((ik_bindings or []) + [{"effector": h.get("hand"),
                                                              "object_id": h.get("object_id")}
                                                             for h in (carry or [])], gaze_at)
            preview = await _preview_walk(character, walk_to, stop_within=stop_within, face=aim)

        asked_base = base
        # THE WALK IS OVER, THE OVERLAY IS NOT. She has arrived, so keeping `walking` as the base of
        # what commits now would stride her on the spot -- the same mismatch a bare walk gets refused
        # for. The overlays stay: `idle` claims nothing on any channel, so this is the same composed
        # motion over a stance instead of over a stride. Only when nothing follows; a plan with `then`
        # is departing from the walk on purpose.
        arrived_composing = bool(while_walking) and not then and "idle" in kb.actions
        if arrived_composing:
            base = "idle"
        # After a walk the engine's own answer is about the wrong moment -- she has not set off yet --
        # so the opener is decided against what the walk will leave behind. `_walk_there` plays the
        # walk cycle and is told not to settle it, so this is construction rather than a guess.
        base, at_rest = await _opening_step(
            character, base, overlay_ids, then,
            projected={"going": False, "playing": LOCOMOTION_ACTION}
            if preview and preview.get("will_walk") else None)
        assembly, layers, gates = _one_step(base, overlays, hold_final_pose, ik_bindings,
                                            base_channels, pinned)
        if arrived_composing:
            # A DIFFERENT SUBSTITUTION AND A DIFFERENT REASON, so it does not borrow the sentence
            # below. Nothing was dropped and nothing failed: she really did walk and really did
            # perform the overlay while walking, and this is what is left once the walking is done.
            gates.append({
                "id": "opening_step", "status": "pass",
                "detail": "%s played under the walk that got her here and is still playing; the walk "
                          "is over, so what commits now is the same overlay over a stance rather than "
                          "over a stride." % ", ".join(overlay_ids),
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
        seated = [a for a in [base] + overlay_ids + [e["base"] for e in (then or [])]
                  if a in kb.actions and _posture_of(a) == "seated"]
        if seated and not sit_on:
            raise ToolFailure(
                "%s is a seated action and nothing was named to sit on" % seated[0],
                hint="find a seat with scene_search('chair'), and pass its id as the top-level "
                     "`sit_on` of this same call — that also walks her there. Playing a seated clip "
                     "on open floor puts her in mid-air.")

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
                                       "something to sit on with scene_search('chair') and "
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
                        aid, entry.get("overlays") or [], entry.get("hold_final_pose"), None,
                        entry.get("base_channels"), pinned)
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

        paired = await _pair_bound_hands(ik_bindings or [])
        walk_payload = None
        if while_walking and mode == "commit":
            # The walk-with-overlays plan, built here so it can be checked before she sets off and
            # then sent verbatim by `_walk_there`. Entered on the frame closest to what she is
            # currently doing, which is a read-only question and stable: nothing has moved.
            was = await _call(P.T.MOTION_LOCOMOTE, {"character": character, "query": True})
            walk_payload = _in_place_payload(character, LOCOMOTION_ACTION,
                                             from_action=was.get("playing"),
                                             overlays=while_walking, carry=carry)
        payload = {
            "character": character,
            "steps": steps,
            "free_channels": assembly.free_channels,
            "ik": [{"effector": b["effector"], "object_id": b["object_id"],
                    "at_s": _binding_due(steps, b["effector"])}
                   for b in (ik_bindings or []) + paired],
            "expect_contact": _expect_contacts((ik_bindings or []) + paired, steps),
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
        # ---- everything above this line is derivation; nothing has moved ------------------------
        #
        # THE FENCE. Both plans are run on a hidden duplicate of her before the visible one does
        # anything: the walk-with-overlays where she stands now, and the motion itself at the arrival
        # the route preview projected. A failure here raises, and she is exactly where she was.
        validated = None
        if mode == "commit":
            if walk_payload is not None:
                await _validate(walk_payload)
            validated = await _validate(payload, at=_standing_at(preview))

        # ---- and only now does anything visible happen -------------------------------------------
        walked = None
        if preview is not None:
            walked = await _walk_there(character, walk_to, stop_within=stop_within,
                                       then_wait=True, settle=False,
                                       under=while_walking, under_payload=walk_payload,
                                       carry=carry if while_walking else None)
            if not walked.get("arrived"):
                raise ToolFailure(
                    "she did not get to %s: %s" % (walk_to, walked.get("note") or "still walking"),
                    hint="the motion was not played. The route was clear when it was checked, so "
                         "something is in the way now -- try again, or walk there with move_to and "
                         "see how far she gets.")
            if aim:
                # The turn has to finish before the plan commits: a descent that starts mid-turn puts
                # her down facing part of the way round.
                if not await _turn_to_face(character, aim):
                    walked["note"] = "still turning to face %s when the plan committed" % aim
                walked["facing"] = aim
                walked["facing_from"] = aimed_for
            for key in ("path_length_m", "eta_s"):
                if walked.get(key) is None and preview.get(key) is not None:
                    walked[key] = preview[key]

        data = await _assemble(payload, checked=True)

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
        if validated:
            # THAT THE CHECK RAN IS PART OF WHAT HAPPENED. A committed plan with no verdict beside it
            # reads the same as one that skipped the check, and the difference is the whole point of
            # this path.
            result["validated"] = validated
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
        # `dropped_grips` was reported here: two v3 actions each declared a `contact` in the knowledge
        # base, one hand could serve only one of them, and the losing action kept its motion without
        # its object. v4 records declare no contact (ADR 0022), so there is no second grip to lose --
        # a hand holds what the plan says it holds, once, and a plan that asks two actions to drive a
        # pinned hand is refused by name instead.
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
        if paired:
            # The bindings the plan did not write. One hand was named, the object said where both go,
            # and the second binding is therefore something the model has to be told about rather than
            # discover from a reply describing one hand on a keyboard.
            result["paired_hands"] = paired
        if len(steps) > 1:
            result["sequence"] = [{"action_id": s["action_id"], "starts_at_s": s["start_at_s"],
                                   "fades_in_over_s": s["blend_in_s"]} for s in steps]
        if generated:
            result["generated_transitions"] = generated
            result["note"] = ("part of this motion does not exist in the library and was generated: "
                              "say so rather than presenting it as a retrieved clip.")
            if mode == "commit":
                # WHAT THIS WATCHES FOR CHANGED WHEN THE PLAN STARTED BEING CHECKED FIRST. It used to
                # be the only verdict there was, arriving seconds after a viewer had already watched
                # the sit land badly. The plan has now been run on a hidden copy and passed before
                # anything moved, so what is left to find out is whether the real scene did something
                # the copy could not know about -- the seat moved, somebody else picked the thing up,
                # the route changed under her. That is worth measuring and it is not worth waiting for,
                # so it is scheduled: the loop runs it once it is answerable and reports separately.
                result["verify"] = {
                    "status": "scheduled",
                    "tool": "check_motion",
                    "arguments": {"character": character},
                    "confirms": "the pelvis landed on %s, in the real scene rather than the copy"
                                % sit_on,
                    "on_failure": "the sit was checked and passed, but in the scene it did not land:",
                    "note": "this plan already passed a geometric check before it played, so it is "
                            "sound. This watches the real scene for something the check could not "
                            "see. Say she is sitting down; the landing is still being measured.",
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

    registry.add("scene_search",
                 "Which thing is that. Search the scene by name or by the contact name a motion uses, "
                 "and get back the id to plan with, what it is called, and the other names it answers "
                 "to. Named places are things too, so this is also how you find somewhere to walk. "
                 "Call it with no query to list everything there is — the list is short and complete.",
                 SEARCH_PARAMS, scene_search)
    registry.add("scene_query",
                 "What those things are to the character right now: does it exist, is it within arm's "
                 "reach, does she have to walk to it, is somebody holding it. Use it to decide "
                 "whether a motion needs a walk first.",
                 QUERY_PARAMS, scene_query)
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
    """The most fundamental check and the cheapest, so it runs first and alone.

    The posture is MEASURED now -- a bin over the clip's mean body height (kbindex.posture_of) --
    where v3 read a `composability.posture` label. The gate is unchanged: a standing action and a
    seated one cannot play at the same time however the channels fall out.
    """
    postures = {aid: KI.posture_of(kb.record(aid)) for aid in [base] + list(overlays)}
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

    # WHAT THE PLAN PINS, AND WHETHER ANYTHING IS DRIVING IT.
    #
    # This gate used to read `channels.*.contact` and `channels.*.role` off the knowledge base: a hand
    # the CLIP declared it worked against an object was reported as "animated against its object by
    # the clip itself" and quietly handed downstream to be bound and measured. v4 deletes both fields
    # (ADR 0022) -- a record describes how a hand moves, not what it is holding, because what it is
    # holding is a fact about the scene and the task.
    #
    # So the source is the plan, which is where a grip is named now: `carry` and `ik_bindings` each
    # say one effector and one object. What is left to check is the thing the KB could never say
    # anyway -- that the plan is coherent with itself. Two effectors aimed at one object is the shape
    # that once pulled both of typing's wrists onto a single grab point (measured: right hand 0.000 m,
    # left hand 0.065 m -- clasping, not typing), and it is a warn here rather than a refusal because
    # an object CAN say where both hands go, and when it does that is exactly what should happen. The
    # object is asked, in `_pair_bound_hands`.
    pins = [{"effector": b["effector"], "object_id": b["object_id"]}
            for b in ik_bindings if b.get("effector") and b.get("object_id")]
    by_object = {}
    for pin in pins:
        by_object.setdefault(pin["object_id"], []).append(pin["effector"])
    doubled = {oid: effs for oid, effs in by_object.items() if len(set(effs)) > 1}

    gates.append({
        "id": "contact_bindings",
        "status": "warn" if doubled else "pass",
        "detail": ("nothing is bound to the scene" if not pins else
                   "; ".join("%s -> %s" % (p["effector"], p["object_id"]) for p in pins)),
        "hint": ("two hands are aimed at one object. That is right only when the object says where "
                 "each hand goes; where it does not, both wrists are pulled onto the same point. Bind "
                 "one hand and the other keeps the clip's own motion.") if doubled else None,
        # Carried out of the gate because it is measured, not only reported: whether a bound hand
        # actually meets its object depends on where she ends up standing or sitting, and that is a
        # geometric question the engine answers once the motion is playing.
        "plan_contacts": pins,
    })
    return gates
