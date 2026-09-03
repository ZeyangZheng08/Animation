"""
protocol.py — the typed message contract between this agent service and the engine executor.

This is one half of a two-language contract; the other half is `Assets/Scripts/AgentRuntime/Protocol.cs`
in the Unity repository. The shapes below are the authority — change them here first, then mirror.

WHAT CROSSES THIS CHANNEL, AND WHAT MUST NOT. Typed messages only, never code. The Unity MCP bridge
already exists for shipping generated C#, and it is offline-only: it builds the KB. This channel is the
runtime one, and the engine side of it is a pre-compiled executor. Merging the two would reintroduce
compiling C# during a request, which the architecture forbids and which is impossible in a player build
anyway.

WHAT THE MODEL IS ALLOWED TO SEE. Scene replies carry identity, category, state and COARSE spatial
relations — never exact transforms. Precise poses stay engine-side, where the IK solver and the geometric
gates consume them directly. That is an architectural claim (the language model never handles motion
numerics) and, separately, a hard practical limit: the demo's model has a 32k context window, and a
handful of full-precision transform dumps would eat it.

THREE MESSAGE SHAPES.

    request   {"v":1, "id":"r7", "type":"scene.find", "params":{...}}      agent -> engine
    response  {"v":1, "id":"r7", "ok":true,  "data":{...}}                 engine -> agent
              {"v":1, "id":"r7", "ok":false, "err":{"code":"...","msg":"..."}}
    event     {"v":1, "type":"motion.status", "data":{...}}                engine -> agent, unsolicited

A response always carries the `id` of the request it answers; an event never has one. That is the only
thing needed to tell them apart, and `classify()` is the single place that decides.

EVENTS RUN BOTH WAYS; REQUESTS DO NOT. Requests stay agent -> engine only, so the engine remains a pure
reactor with no pending-request table to reconcile after a domain reload. But the instruction that
starts a turn now arrives FROM the engine, because the text box lives in the running scene, and the
turn's progress has to reach that scene to be shown. Both directions of that are events, which is
already the shape for "something happened and nobody asked", and needs no correlation.

VERSIONING. `v` is checked on every decode and a mismatch is fatal rather than best-effort. A demo that
silently half-speaks an old protocol is worse than one that refuses to start: the failure would surface
later as a wrong pose, not as an error.
"""

# v2 adds the time axis: `motion.assemble` takes a `steps` array, each step being exactly the shape v1
# sent at the top level, plus when it starts and how it fades in. A single-step sequence IS v1's
# behaviour, so there is one code path in the executor rather than a common case and an interesting one.
# The version is fatal on decode by design: both halves ship together, and a Unity session left running
# from before the bump fails loudly instead of half-speaking the old shape.
#
# v3 moves the text input into the running scene. `agent.instruct` is the first message that flows
# engine -> agent as anything other than an answer, and `agent.status` / `agent.reply` are the first
# events that flow the other way. Nothing about the request/response half changes.
#
# v4 puts a check in front of execution. `motion.assemble` gains a third mode, `validate`, which runs
# the whole plan on a hidden duplicate of the character at fixed timestep and answers with the same
# geometric metrics the runtime gate reports -- before anything the viewer can see has changed. And
# `motion.locomote` gains `preview`, which answers where a walk WOULD put her without taking a step,
# so the motion that follows the walk can be judged at the place it will actually happen.
#
# THE BUMP IS NOT OPTIONAL AND THAT IS THE POINT. An older executor receiving `mode: "validate"`
# does not know the word; `Apply` treats anything that is not "commit" as a dry run, so it would
# answer "resolved, touched nothing" -- which reads exactly like a pass. A plan would then commit on
# the strength of a check that never ran. A fatal version mismatch turns that into an error on the
# first message instead.
# v5 ADDS `apply_root_motion` ON A LAYER, and the bump is not optional for the same reason.
# A retrieved posture transition is a clip that TRAVELS -- a sit-down steps 0.45 m backwards into the
# chair -- and the composer has always discarded root motion, which is right for a walk cycle played
# under a NavMeshAgent and wrong for this. So the layer says which it is. An executor from before v5
# does not know the field, drops it, discards the root motion, and plays the sit-down on the spot:
# the feet slide, the hips finish where they started, and the plan reports success about a character
# who never reached the seat. Silently plausible, which is the one failure mode this protocol
# refuses to have.
PROTOCOL_VERSION = 5


class T:
    """Message types. Namespaced by subsystem so the routing table stays readable as it grows."""

    # engine -> agent, unsolicited
    ENGINE_HELLO  = "engine.hello"      # sent once on connect: scene name, actors, protocol version
    MOTION_STATUS = "motion.status"     # start-play / layer-merged / finished, with engine timestamps
    GATE_REPORT   = "gate.report"       # geometric gate metrics for an assembled motion
    AGENT_INSTRUCT = "agent.instruct"   # a line typed in the running scene: {"text": "..."}

    # agent -> engine, unsolicited
    AGENT_STATUS = "agent.status"       # turn progress for whatever is displaying it: {"state", "detail"}
    AGENT_REPLY  = "agent.reply"        # the finished turn: text, counts, seconds, cancelled/error

    # agent -> console only. The console channel carries the same status and reply events, plus this
    # banner, which the engine has no use for: a console attaches and detaches at will and needs to be
    # told what it just attached to.
    CONSOLE_HELLO = "console.hello"     # {"model", "actions", "tools", "engine"}
    # A verdict that could not exist when the reply was written. A generated sit only becomes
    # measurable after the descent runs, seconds after the plan is committed; the reply does not wait
    # for it, so the measurement arrives on its own afterwards: {"status", "detail", "after_s"}.
    GATE_VERDICT  = "gate.verdict"
    # The run is over and this console should close: {"reason"}. Sent when the engine says it has
    # stopped, which is the only thing that ends a session — a terminal detaching does not, and a
    # recompile is not the engine stopping. A console that stayed open past the end of the run was
    # something a person then had to go and find and kill.
    CONSOLE_BYE   = "console.bye"

    # agent -> engine, request/response
    SCENE_FIND     = "scene.find"       # typed predicate filter -> ranked candidate list
    SCENE_DESCRIBE = "scene.describe"   # one object: state, holder, reachability
    SCENE_ANCHORS  = "scene.anchors"    # named standing/facing anchors in the scene
    SCENE_POSITION = "scene.position"   # where things are, in metres — the one query that returns numbers
    MOTION_ASSEMBLE = "motion.assemble"  # per-channel layers + symbolic bindings; dry_run/validate/commit
    MOTION_LOCOMOTE = "motion.locomote"  # walk somewhere; separate because every clip is in-place
    GATE_RUN        = "gate.run"        # geometric verdict for the motion currently playing

# There is deliberately no separate "play one clip" message. A full match is an assembly with a single
# layer and no overlays, so it takes the same path through the executor as a composed motion. Two code
# paths would mean the common case and the interesting case could drift apart, and the interesting one
# is the one that gets less testing.


REQUEST_TYPES = frozenset({
    T.SCENE_FIND, T.SCENE_DESCRIBE, T.SCENE_ANCHORS, T.SCENE_POSITION,
    T.MOTION_ASSEMBLE, T.MOTION_LOCOMOTE, T.GATE_RUN,
})

# Direction is documented per set and checked nowhere on decode: a message is well-formed or it is not,
# and which side sent it is already known by whoever is reading. Keeping the sets separate is for the
# reader, and for `event()` to refuse a type this side has no business emitting.
FROM_ENGINE_EVENTS = frozenset({
    T.ENGINE_HELLO, T.MOTION_STATUS, T.GATE_REPORT, T.AGENT_INSTRUCT,
})

FROM_AGENT_EVENTS = frozenset({
    T.AGENT_STATUS, T.AGENT_REPLY,
})

# What the console channel may carry, per direction. `console.hello` is deliberately absent from
# FROM_AGENT_EVENTS: `EngineLink.notify` validates against that set, so a banner meant for a terminal
# cannot be sent to Unity by mistake.
TO_CONSOLE_EVENTS = FROM_AGENT_EVENTS | frozenset({T.CONSOLE_HELLO, T.GATE_VERDICT, T.CONSOLE_BYE})
FROM_CONSOLE_EVENTS = frozenset({T.AGENT_INSTRUCT})

EVENT_TYPES = FROM_ENGINE_EVENTS | FROM_AGENT_EVENTS | TO_CONSOLE_EVENTS


class E:
    """Error codes. The engine returns these; the agent turns them into model-visible tool results,
    so they are part of what the language model reasons about and must stay stable and meaningful."""

    BAD_REQUEST  = "bad_request"    # malformed params — a bug on the agent side
    UNKNOWN_TYPE = "unknown_type"   # engine does not implement this message type
    NOT_FOUND    = "not_found"      # named object / anchor / actor does not exist in the scene
    NOT_READY    = "not_ready"      # engine is alive but not in play mode, or the actor has no rig
    EXEC_FAILED  = "exec_failed"    # the motion could not be applied (see msg)
    INTERNAL     = "internal"       # unhandled engine-side exception


class ProtocolError(ValueError):
    """A message that does not conform. Raised on decode, never sent."""


def request(msg_type, params=None, msg_id=None):
    if msg_type not in REQUEST_TYPES:
        raise ProtocolError("not a request type: %r" % (msg_type,))
    msg = {"v": PROTOCOL_VERSION, "id": msg_id, "type": msg_type, "params": params or {}}
    return msg


def ok(msg_id, data=None):
    return {"v": PROTOCOL_VERSION, "id": msg_id, "ok": True, "data": data or {}}


def err(msg_id, code, message):
    return {"v": PROTOCOL_VERSION, "id": msg_id, "ok": False, "err": {"code": code, "msg": message}}


def event(msg_type, data=None):
    if msg_type not in EVENT_TYPES:
        raise ProtocolError("not an event type: %r" % (msg_type,))
    return {"v": PROTOCOL_VERSION, "type": msg_type, "data": data or {}}


def classify(msg):
    """'response' | 'event' | 'request'. A response is the only shape carrying both `id` and `ok`."""
    if "ok" in msg:
        return "response"
    if "id" in msg and msg.get("id") is not None:
        return "request"
    return "event"


def validate(msg):
    """Raise ProtocolError unless `msg` is a well-formed message of a known type. Returns its kind.

    Deliberately strict. This runs on every frame off the wire, and the cost of a missed malformation is
    that it surfaces much later as a wrong pose in the scene rather than as an error here.
    """
    if not isinstance(msg, dict):
        raise ProtocolError("message is %s, not an object" % type(msg).__name__)

    v = msg.get("v")
    if v != PROTOCOL_VERSION:
        raise ProtocolError(
            "protocol version %r, expected %d — the engine executor and this service are out of step; "
            "rebuild the Unity side from Protocol.cs" % (v, PROTOCOL_VERSION))

    kind = classify(msg)

    if kind == "response":
        if msg.get("id") is None:
            raise ProtocolError("response without an id")
        if msg["ok"]:
            if not isinstance(msg.get("data", {}), dict):
                raise ProtocolError("response data is not an object")
        else:
            e = msg.get("err")
            if not isinstance(e, dict) or "code" not in e or "msg" not in e:
                raise ProtocolError("error response needs err.code and err.msg")
        return kind

    msg_type = msg.get("type")
    if kind == "event":
        if msg_type not in EVENT_TYPES:
            raise ProtocolError("unknown event type: %r" % (msg_type,))
        if not isinstance(msg.get("data", {}), dict):
            raise ProtocolError("event data is not an object")
    else:
        if msg_type not in REQUEST_TYPES:
            raise ProtocolError("unknown request type: %r" % (msg_type,))
        if not isinstance(msg.get("params", {}), dict):
            raise ProtocolError("request params is not an object")
    return kind
