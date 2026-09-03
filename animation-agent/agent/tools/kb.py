"""
kb.py — the five motion tools that COMPUTE something.

WHAT BELONGS HERE, AND WHAT DOES NOT. `files.py` holds glob, grep and read: ordinary file access,
spelled the ordinary way, reaching the knowledge base and the corpus's source assets alike. Everything
here earns a tool of its own by doing work no file read can do:

    motion_search       scores 2446 records against a description and says how much of it they covered
    motion_channels     projects one record down to what each body part does and how it is described
    motion_timing       measures WHEN: the span each part is moving in, whether it repeats, and the
                        posture the body is in over the clip
    motion_compose      resolves a composition the agent proposed -- who drives what, what is shared,
                        what cannot be shared, and which frames each part contributes
    motion_transition   searches for the seam between two clips, or reports the posture change that
                        no seam can serve

The line is not "knowledge base versus filesystem". It is fetching versus computing.

THE THREE FAMILIES, AND WHY THIS FILE IS TWO OF THEM. Search finds candidates by meaning; Analysis
resolves what those candidates ARE and how they fit together; Unity grounds the result in a scene.
`motion_search` is the first, the other four are the second, and none of them touches the engine. That
is the point: composition, timing and seam geometry are decided from frozen measurements, so the agent
can settle a plan before anything is asked of the runtime — and a wrong plan costs a tool call rather
than a character crossing a room.

NUMBERS COME OUT, NEVER IN. Every parameter on this surface is an identifier, an enum or a list of
them; there is nowhere for a frame index, an angle or a weight to enter. What comes back does carry
numbers -- a frame window, a seam cost, a share -- because they are the evidence for a decision the
agent then makes in names.

NO NO-MATCH THRESHOLD. `motion_search` reports `top_margin` and `query_coverage` and lets the model
decide. A cutoff tuned on the cases the system is evaluated on is overfitting, and one picked without
tuning is a guess with a decimal point.
"""
from .. import assemble as A
from .. import kbindex as KI
from .. import segments as S
from .. import transitions as T
from ..kbindex import ANATOMICAL, CHANNELS
from .registry import ToolFailure

_CHANNEL_ENUM = list(CHANNELS)
_ANATOMICAL_ENUM = list(ANATOMICAL)
_POSTURE_ENUM = ["standing", "seated", "floor", "other"]

# What an overlay is asking of a clip in time. Three words because there are three answers the
# measurement supports, and the model has no fourth: play the part that moves, keep playing it, or
# ignore the window and run the whole clip under the base.
TEMPORAL_INTENT = ["once", "repeat", "continuous"]

_POSTURE_HELP = ("standing, seated, floor (lying, crawling, anything down at ground level) or other "
                 "(crouching, kneeling, airborne, mid-change). Measured from the body's geometry, "
                 "not declared.")

SEARCH_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "query": {
            "type": "string",
            "description": "Natural-language description of the motion, e.g. 'presses on the chest "
                           "repeatedly' or 'walks across the room'.",
        },
        "posture": {
            "type": "string", "enum": _POSTURE_ENUM,
            "description": "Keep only actions the body is mostly in this state for. " + _POSTURE_HELP
                           + " Use `transition` instead when you want a clip that CHANGES state.",
        },
        "transition": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "from_posture": {"type": "string", "enum": _POSTURE_ENUM},
                "to_posture": {"type": "string", "enum": _POSTURE_ENUM},
            },
            "required": ["from_posture", "to_posture"],
            "description": "Keep only actions that START in one state and END in the other -- how you "
                           "find a real clip for getting into a chair or up off the floor. Cannot be "
                           "combined with `posture`: one asks what a clip IS, the other what it "
                           "CROSSES.",
        },
        "moves_channels": {
            "type": "array", "minItems": 1, "maxItems": 8,
            "items": {"type": "string", "enum": _ANATOMICAL_ENUM},
            "description": "Keep only actions that actually animate every one of these body parts. "
                           "The question to ask when looking for something to combine: an action that "
                           "does not move a part has nothing to contribute there.",
        },
        "exclude": {
            "type": "array", "minItems": 1, "maxItems": 20,
            "items": {"type": "string"},
            "description": "action_ids to leave out. Search again with the ones you have already read "
                           "and rejected named here, rather than rephrasing -- rephrasing reorders "
                           "the whole ranking and hands them back in a different order.",
        },
        "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
    },
    "required": ["query"],
}

CHANNELS_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"action_id": {"type": "string", "description": "As returned by motion_search."}},
    "required": ["action_id"],
}

TIMING_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"action_id": {"type": "string", "description": "As returned by motion_search."}},
    "required": ["action_id"],
}

_OVERLAY_ITEM = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "action_id": {"type": "string", "description": "action grafted onto the base."},
        "channels": {
            "type": "array", "minItems": 1, "maxItems": 8,
            "items": {"type": "string", "enum": _ANATOMICAL_ENUM},
            "description": "Which body parts this overlay drives.",
        },
        "temporal_intent": {
            "type": "string", "enum": TEMPORAL_INTENT, "default": "once",
            "description": "How long this overlay lasts. 'once' plays the part of it that is moving, "
                           "one time. 'repeat' keeps that part going under a longer base -- one chest "
                           "compression over and over rather than the thirty in the clip. "
                           "'continuous' ignores the window and runs the whole clip.",
        },
    },
    "required": ["action_id", "channels"],
}

COMPOSE_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "base": {"type": "string", "description": "action_id of the action that sets the posture."},
        "base_channels": {
            "type": "array", "maxItems": 8,
            "items": {"type": "string", "enum": _ANATOMICAL_ENUM},
            "description": "Body parts the base OWNS, when you want to say so. The base animates the "
                           "whole body regardless; naming parts here reserves them.",
        },
        "overlays": {
            "type": "array", "maxItems": 3, "items": _OVERLAY_ITEM,
            "description": "Actions layered on top of the base, each with the body parts it drives.",
        },
        "pinned": {
            "type": "array", "maxItems": 4,
            "items": {"type": "string", "enum": ["left_hand", "right_hand"]},
            "description": "Hands this plan will attach to something in the scene. A pinned hand "
                           "cannot be averaged out of two motions, so naming it here turns a share "
                           "into a conflict you can see before you commit to the arrangement.",
        },
    },
    "required": ["base"],
}

TRANSITION_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "from_action": {"type": "string"},
        "to_action": {"type": "string"},
        "via": {
            "type": "array", "minItems": 1, "maxItems": 6,
            "items": {"type": "string"},
            "description": "Candidate actions to route through, when the two ends are in different "
                           "postures. Each is costed at both joins and ranked geometrically; which "
                           "one MEANS the right thing is still yours to decide.",
        },
    },
    "required": ["from_action", "to_action"],
}


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
    if isinstance(value, dict) and not value:
        return None
    return value


def window_for_intent(window, intent):
    """The measured window, adjusted for what the overlay was asked to do in time.

    THE MEASUREMENT DECIDES WHICH FRAMES, THE INTENT DECIDES WHETHER THEY REPEAT. `segments` answers
    "which part of this clip is the part worth playing" from the frozen dump; nothing an agent says
    can change that answer, and nothing it says here does. What `temporal_intent` sets is the one bit
    the measurement genuinely cannot supply, because it is a fact about the TASK: whether the base
    outlives the overlay and the overlay should keep going.

    `continuous` drops the window entirely -- the whole clip plays, which is what an overlay that is
    the point of the motion rather than a gesture within it wants.

    `repeat` on a window that is not a measured repetition is honoured and reported. The two ends of a
    moving span are usually not the same pose, so wrapping shows a jump; refusing would be worse,
    since a request to keep something going is a real request, and silently not repeating would leave
    the agent describing a motion that stopped.
    """
    if intent == "continuous" or not window:
        return None
    window = dict(window)
    if intent == "repeat":
        if not window["loop"]:
            window["why"] = ("%s, repeated as asked; its two ends are not the same pose, so each wrap "
                             "shows a small jump" % window["why"])
        window["loop"] = True
    else:                                            # "once", and the default
        window["loop"] = False
    return window


def register(registry, kb, measuring=True):
    """Attach the motion tools to a registry, bound to a loaded KBIndex.

    `measuring=False` withholds motion_timing, motion_compose and motion_transition. It exists for the
    narrow comparison arm, which is the tool surface as it stood before per-frame measurement was
    exposed at all — the arm only means something if its membership stays fixed while these modules
    are reorganised around it.
    """
    # The per-channel segment table, read once and lazily. `read_table` memoises the file itself, so
    # this is about not paying the parse per call rather than about the disk.
    segment_table = {}

    def segments_for(action_id):
        if not segment_table:
            segment_table.update(S.read_table() or {"": []})
        return segment_table.get(action_id) or []

    def window_of(action_id, channels, intent="once"):
        """Which frames this action contributes on these channels, or None for the whole clip."""
        if not channels:
            return None
        return window_for_intent(S.window_for(segments_for(action_id), channels), intent)

    def clip_for(action_id):
        """The `raw` dump behind an action.

        NO CACHE OF ITS OWN. There used to be one here, a plain dict keyed by action_id that lived as
        long as the registry -- correct for a store of eight clips and a slow leak across a session
        over 2446, since a search-heavy turn touches dozens. `transitions.load_clip` keeps a bounded
        LRU that every caller shares, so the memo belongs there and this is a lookup.
        """
        if action_id not in kb.actions:
            raise ToolFailure("unknown action_id: %s" % action_id,
                              hint="use motion_search and pass an action_id it returns")
        rec = kb.actions[action_id]
        name = (rec.get("source_clip") or {}).get("clip_name")
        try:
            return T.load_clip(name, loop=bool(rec.get("loop")))
        except (IOError, OSError):
            raise ToolFailure("no per-frame data for %s" % action_id)

    def known(action_ids):
        unknown = [a for a in action_ids if a not in kb.actions]
        if unknown:
            raise ToolFailure("unknown action_id: %s" % ", ".join(unknown),
                              hint="use motion_search and pass an action_id it returns")

    # ---- search --------------------------------------------------------------------------------

    def motion_search(query, posture=None, transition=None, moves_channels=None, exclude=None,
                      top_k=5):
        posture = _blank(posture)
        transition = _blank(transition)
        if posture and transition:
            raise ToolFailure(
                "posture and transition ask different questions and cannot be combined",
                hint="`posture` finds clips that STAY in a state; `transition` finds clips that go "
                     "from one to another. Pick the one you meant.")

        # `moves_channels` on the tool, `moves_channel` in the index. The plural reads correctly on a
        # list parameter; the index's key is the older singular and is not worth a migration for a
        # name only this line sees.
        filters = {"posture": posture, "transition": transition}
        if moves_channels:
            filters["moves_channel"] = list(moves_channels)
        if exclude:
            filters["exclude"] = list(exclude)

        hits = kb.search(query, filters, limit=top_k)
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
                # WHAT IT MATCHED ON, named for what it is. `matched` read as a verdict; these are the
                # query's own words found in the record, which is evidence the agent weighs rather
                # than a claim the search is making.
                "matched_evidence": hit.why,
                "posture": hit.posture,
                # BOTH ENDS, on every hit. Whether two clips can follow one another is decided by
                # where one finishes and the next begins, and a result carrying only the dominant
                # reading makes that a second round trip for every candidate.
                "start_posture": hit.start_posture,
                "end_posture": hit.end_posture,
                "duration_s": record.get("duration"),
                # MEASURED. The parts that move at all in this clip -- the pool an overlay's channel
                # list draws from. A part that does not move has nothing to contribute.
                "moves": [c for c in ANATOMICAL if channels.get(c, {}).get("state") == "dynamic"],
            })

        # Diagnostics instead of a tuned cutoff — see the module docstring.
        margin = round(hits[0].score - hits[1].score, 2) if len(hits) > 1 else None
        return {"results": results, "corpus_size": len(kb.actions), "top_margin": margin,
                "query_coverage": kb.coverage(query)}

    # ---- analysis ------------------------------------------------------------------------------

    def motion_channels(action_id):
        known([action_id])
        record = kb.record(action_id)
        out = {}
        for name in CHANNELS:
            ch = kb.channels(action_id).get(name) or {}
            out[name] = {"state": ch.get("state"),
                         "motion_description": ch.get("describes")}
        return {"action_id": action_id, "description": record.get("action_description"),
                "channels": out}

    def motion_timing(action_id):
        """WHEN each part of a clip is doing something, and what the body is doing over the whole of it.

        READ-ONLY AND ENGINE-FREE. Everything here was measured from the frozen dump when the corpus
        was built: the per-channel spans and cycles from `segments`, the posture structure from the
        sidecar `build_posture.py` writes. Asking costs a dictionary lookup, so this is the tool to
        ask BEFORE proposing a composition rather than after one is refused.
        """
        known([action_id])
        record = kb.record(action_id)
        segments = segments_for(action_id)
        frame_rate = record.get("frame_rate") or 30

        channels = {}
        for seg in segments:
            channels[seg["channel"]] = {
                # The span between the first and last frame this part is actually moving in.
                "active_span": {"start_frame": seg["start_frame"], "end_frame": seg["end_frame"],
                                "seconds": round(seg["frames"] / float(frame_rate), 3)},
                # A measured period, or null where the part does not repeat. Null is the common
                # answer: a Mixamo clip is usually one performance, not a cycle of one.
                "cycle_frames": seg["cycle_frames"],
                # Whether looping the window rejoins cleanly. That is a stronger claim than having a
                # cycle -- it is the one that decides whether `temporal_intent: repeat` looks right.
                "repeatable": bool(seg["cycle_frames"]),
                # Whether this part has any opinion about which frames to take. A still part may well
                # be holding a pose the action needs; it simply looks the same at every frame.
                "moving": seg["moving"],
            }

        moving = sorted(c for c, v in channels.items() if v["moving"])
        out = {
            "action_id": action_id,
            "duration_s": record.get("duration"),
            "frame_rate": frame_rate,
            "channels": channels,
            # What an overlay driving everything that moves would contribute. The same measurement
            # unity_execute takes for itself, said in advance so a composition can be judged before
            # it is sent.
            "frame_window": S.window_for(segments, moving) if moving else None,
        }
        out.update(KI.posture_detail(record))
        return out

    def motion_compose(base, overlays=None, base_channels=None, pinned=None):
        """Resolve a composition the agent proposed, without touching the engine.

        THE SPLIT IS THE AGENT'S AND THE ARITHMETIC IS NOT. Which body part matters in a clip depends
        on the task, so the agent names it; who then OWNS a part two overlays both named, what a
        shared part is worth to each of them, which frames each contributes and where a mixed layer
        enters are all measured or arithmetic, and they are what this returns.

        SAME FUNCTION THE EXECUTOR USES. `unity_execute` calls `assemble.arbitrate` with the same
        arguments and gets the same partition, so a composition that resolves here is the one that
        will be sent — this is a preview of the plan rather than a model of it.
        """
        known([base])
        try:
            pairs = A.normalise_overlays(overlays) if overlays else []
        except ValueError as e:
            raise ToolFailure(str(e),
                              hint="every overlay names the body parts it drives; any of: %s"
                                   % ", ".join(ANATOMICAL))
        known([aid for aid, _ in pairs])
        intents = {}
        for item in (overlays or []):
            if isinstance(item, dict) and item.get("action_id"):
                intents[item["action_id"]] = item.get("temporal_intent") or "once"

        postures = {aid: KI.posture_of(kb.record(aid)) for aid in [base] + [a for a, _ in pairs]}
        pinned = set(pinned or [])
        try:
            assembly = A.arbitrate(base, pairs, kb, base_channels=base_channels,
                                   pinned_channels=pinned)
        except ValueError as e:
            raise ToolFailure(str(e))

        # ONE WINDOW PER ACTION, over everything that action drives — owned channels and shared ones
        # together. A clip is one performance: deciding "which frames" separately for the hands it
        # owns and the legs it half-owns would have one body playing two moments of the same motion.
        driven = {}
        for aid, chans in assembly.layers:
            driven.setdefault(aid, set()).update(chans)
        for mix in assembly.shared:
            for aid, _ in mix.shares:
                driven.setdefault(aid, set()).add(mix.channel)

        shared_channels = {mix.channel for mix in assembly.shared}
        schedule = []
        for aid, chans in assembly.layers:
            outright = chans if aid == base else [c for c in chans if c not in shared_channels]
            if aid != base and not outright:
                continue
            entry = {"action_id": aid, "channels": outright,
                     "source": "base" if aid == base else "overlay",
                     "owns_root": aid == assembly.root_owner}
            if aid != base:
                # THE BASE IS NEVER CUT. It establishes the posture everything else is grafted onto,
                # so a base trimmed to the frames its legs happen to be moving in is a posture that
                # stops halfway through.
                entry["temporal_intent"] = intents.get(aid, "once")
                entry["frame_window"] = window_of(aid, sorted(driven.get(aid) or []),
                                                  intents.get(aid, "once"))
            schedule.append(entry)
        for mix in assembly.shared:
            for aid, weight in mix.overlay_weights(base):
                schedule.append({
                    "action_id": aid, "channels": [mix.channel], "source": "mix",
                    "owns_root": False, "weight": round(weight, 4),
                    "temporal_intent": intents.get(aid, "once"),
                    "frame_window": window_of(aid, sorted(driven.get(aid) or []),
                                              intents.get(aid, "once")),
                })

        out = {
            "base": base,
            "partition": [{"action_id": aid, "channels": chans,
                           "source": "base" if aid == base else "overlay"}
                          for aid, chans in assembly.layers],
            "shared": [m.as_dict() for m in assembly.shared],
            "root_owner": assembly.root_owner,
            "free_channels": assembly.free_channels,
            "conflicts": [c.as_dict() for c in assembly.conflicts],
            "schedule": schedule,
            "rule": "each part drives the channels the plan gave it; a channel two parts name is "
                    "shared between them, unless the plan pinned it to something in the scene.",
        }
        distinct = {p for p in postures.values() if p}
        if len(distinct) > 1:
            # OVERLAYS PLAY AT THE SAME TIME, AND TWO POSTURES CANNOT. Reported here rather than left
            # for unity_execute to refuse, because the fix is a different arrangement rather than a
            # different action, and finding that out without an engine round trip is what this tool
            # is for.
            out["posture_conflict"] = {
                "postures": postures,
                "detail": "these cannot play at once: " + ", ".join(
                    "%s is %s" % (a, p) for a, p in sorted(postures.items())),
                "hint": "overlays are simultaneous. To do one after the other, put it in `then` on "
                        "unity_execute, and ask motion_transition how the two join.",
            }
        return out

    # ---- transition ----------------------------------------------------------------------------

    def _seam(from_action, to_action):
        return T.find_seam(from_action, to_action, kb,
                           {a: clip_for(a) for a in (from_action, to_action)})

    def motion_transition(from_action, to_action, via=None):
        """How two actions join — or what has to happen between them when they do not.

        THE FIRST QUESTION IS POSTURE, NOT DISTANCE. A seam is a crossfade between two poses, and no
        crossfade between standing and seated is a sit-down; it is a character sliding into a chair.
        So the ends are compared first, and a mismatch is answered with the CHANGE that is required
        rather than with a cost that would read as a verdict on a blend nobody should ask for.

        AND IT DOES NOT ENUMERATE THE LIBRARY. 2446 records hold a great many ways to sit down, and
        which of them belongs in this motion is a question about meaning. So the answer names the
        change, the agent searches for it with `motion_search(transition=...)`, and comes back with
        candidates in `via` — at which point this costs each of them at both joins and ranks them
        geometrically. Geometry orders; the agent chooses.
        """
        known([from_action, to_action])
        if from_action == to_action:
            raise ToolFailure("an action does not transition to itself")

        from_end = KI.posture_span_of(kb.record(from_action))[1]
        to_start = KI.posture_span_of(kb.record(to_action))[0]

        if via:
            known(via)
            bad = [v for v in via if v in (from_action, to_action)]
            if bad:
                raise ToolFailure("a via cannot be one of the two ends: %s" % ", ".join(bad))
            routes = []
            for candidate in via:
                entry = _seam(from_action, candidate)
                exit_ = _seam(candidate, to_action)
                start, end = KI.posture_span_of(kb.record(candidate))
                routes.append({
                    "via": candidate,
                    "start_posture": start, "end_posture": end,
                    "entry_cost_deg": round(entry.cost_deg, 2),
                    "exit_cost_deg": round(exit_.cost_deg, 2),
                    "total_cost_deg": round(entry.cost_deg + exit_.cost_deg, 2),
                    "entry_class": entry.cls, "exit_class": exit_.cls,
                    "joins_cleanly": (entry.cls != T.CLASS_POSTURE_CHANGE
                                      and exit_.cls != T.CLASS_POSTURE_CHANGE),
                })
            routes.sort(key=lambda r: r["total_cost_deg"])
            for rank, route in enumerate(routes, 1):
                route["geometric_rank"] = rank
            return {
                "from": from_action, "to": to_action,
                "from_end_posture": from_end, "to_start_posture": to_start,
                "routes": routes,
                "note": "ranked by how far the poses are apart at the two joins, and by nothing else. "
                        "A route that joins cleanly and MEANS the wrong thing is still the wrong "
                        "route; read the descriptions before choosing.",
            }

        if from_end != to_start:
            # NAMES THE CHANGE, NOT THE ABSENCE. Answering "these do not join" is true and reads as a
            # refusal: measured on the eight-action library, the model called this, concluded the
            # motion was impossible and stopped. There are two ways forward and both are named.
            return {
                "from": from_action, "to": to_action,
                "from_end_posture": from_end, "to_start_posture": to_start,
                "joinable_by_blending": False,
                "required_transition": {"from_posture": from_end, "to_posture": to_start},
                "synthesis_available": _SYNTHESISABLE.get((from_end, to_start), False),
                "how": "two ways, in this order. Search for a clip that does the change --  "
                       "motion_search(query, transition={from_posture: '%s', to_posture: '%s'}) -- "
                       "and call this back with those ids in `via` to see which joins best. Failing "
                       "that, unity_execute generates the frames when the step is in `then` and "
                       "`sit_on` names something real to sit on."
                       % (from_end, to_start),
            }

        seam = _seam(from_action, to_action).as_dict()
        seam["joinable_by_blending"] = True
        seam["from_end_posture"] = from_end
        seam["to_start_posture"] = to_start
        return seam

    # ---- declarations --------------------------------------------------------------------------

    registry.add(
        "motion_search",
        "Search the motion library by meaning. Returns the best matches with what each one animates, "
        "the posture it holds, and the postures it starts and ends in. `top_margin` and "
        "`query_coverage` say how confident the ranking is and how much of what you asked for the "
        "library has words for at all -- read both before deciding something is absent.",
        SEARCH_PARAMS, motion_search)

    registry.add(
        "motion_channels",
        "Read one action part by part: what each of the nine body channels does, and the sentence "
        "describing it. This is what to read before deciding which parts to take from which action.",
        CHANNELS_PARAMS, motion_channels)

    if not measuring:
        return registry

    registry.add(
        "motion_timing",
        "When each part of an action is doing something: the span it moves in, whether it repeats, "
        "how long the whole clip is, and the postures the body passes through with the frames where "
        "it changes. Ask before composing -- it says whether an overlay can be kept going under a "
        "longer base, and whether a clip changes posture partway.",
        TIMING_PARAMS, motion_timing)

    registry.add(
        "motion_compose",
        "Work out what a proposed combination actually resolves to: which action drives which body "
        "parts, which parts are shared and in what proportion, which cannot be shared at all, and "
        "which frames of each overlay would be used. No engine and no scene -- this is how to check "
        "an arrangement before committing to it.",
        COMPOSE_PARAMS, motion_compose)

    registry.add(
        "motion_transition",
        "How two actions join. Same posture at the seam: where to cut, how far apart the poses are "
        "and how long a blend that needs. Different postures: the change that has to happen in "
        "between, which you then search for with motion_search(transition=...) and pass back here as "
        "`via` to have each candidate costed and ranked.",
        TRANSITION_PARAMS, motion_transition)

    return registry


# Which posture changes the executor can GENERATE when no clip is found for one. It descends onto a
# support and rises off one, so it covers the standing/seated pair in both directions and claims
# nothing about the others -- there is no generator for lying down, and saying otherwise would send
# an agent to `then` for frames that will not be made.
_SYNTHESISABLE = {
    ("standing", "seated"): True,
    ("seated", "standing"): True,
}
