"""
digest.py — one tool call, as the line a person reads.

A turn used to show as a column of bare tool names. That says the agent is alive and nothing else:
`scene_search` four times over looks identical whether it is narrowing on a chair or asking the same
question with a different typo, and the run that spent an iteration re-searching a list it already had
looked exactly like the run that did not. What a person watching needs is which call, against what, and
what came back.

TWO RULES, AND THEY ARE WHY THIS IS A FILE RATHER THAN A FORMAT STRING.

Nothing is invented. Every phrase below is assembled out of keys the tools genuinely return, so a line
that says "arrived" is the engine's word for it and a line that says nothing is a tool that said
nothing. A summary that guessed would be worse than no summary, because it would be believed.

An unknown tool still gets a line. The fallbacks take the first identifier-shaped argument and the
plain success or error, so a tool added later reads sensibly here without anyone remembering to come
back — and the day it does not, what is missing is detail, not the line.

Both halves of the display use this: `cli.py` renders the stdin session and `terminal.py` renders an
attached console, and they must not describe the same turn differently. Same reason `console.render`
and `terminal.show` mirror each other.
"""

WIDTH = 44


def _clip(text, width=WIDTH):
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[:width - 1] + "…"


def _names(values, limit=3):
    values = [v for v in values if v]
    if not values:
        return ""
    shown = ", ".join(str(v) for v in values[:limit])
    return shown if len(values) <= limit else "%s +%d" % (shown, len(values) - limit)


def describe(name, arguments):
    """What a call is asking for, as one short phrase. `arguments` is the decoded dict, or a string
    when the model emitted something that would not parse."""
    if not isinstance(arguments, dict):
        return _clip(arguments or "")

    if name == "kb_search":
        query = arguments.get("query")
        return _clip('"%s"' % query if query else _names(sorted(arguments.values())))
    if name in ("kb_get_action", "kb_pose"):
        return _clip(arguments.get("action_id") or "")
    if name == "kb_transition":
        return _clip("%s → %s" % (arguments.get("from_action"), arguments.get("to_action")))

    if name == "scene_search":
        return _clip((arguments.get("query") or "").strip() or "the whole room")
    if name == "scene_query":
        return _clip(_names(arguments.get("object_ids") or []))
    if name == "move_to":
        out = str(arguments.get("destination") or "")
        if arguments.get("face"):
            out += " · face %s" % arguments["face"]
        return _clip(out)
    if name == "plan_motion":
        out = " → ".join([str(arguments.get("base"))]
                         + [str(e.get("base")) for e in (arguments.get("then") or [])])
        for extra, label in (("sit_on", "sit on"), ("gaze_at", "look at")):
            if arguments.get(extra):
                out += " · %s %s" % (label, arguments[extra])
        if arguments.get("overlays"):
            out += " · with %s" % _names(arguments["overlays"])
        return _clip(out)

    if name in ("glob", "grep"):
        return _clip(arguments.get("pattern") or "")
    if name == "read":
        return _clip(arguments.get("path") or "")

    # Anything added later. An id-shaped argument is what a call is usually about.
    for key in ("action_id", "object_id", "destination", "query", "path", "character"):
        if arguments.get(key):
            return _clip(arguments[key])
    return _clip(_names([v for v in arguments.values() if isinstance(v, str)]))


def summarise(name, result):
    """What a call came back with, as one short phrase. Empty when there is nothing worth a line."""
    if not isinstance(result, dict):
        return ""
    if result.get("success") is False or result.get("error"):
        return _clip(result.get("error") or "failed")

    if name == "kb_search":
        hits = [r.get("action_id") for r in result.get("results") or []]
        return _names(hits) or "nothing matched"
    if name == "kb_get_action":
        return _clip(result.get("action_id") or "")
    if name == "kb_pose":
        planted = result.get("both_feet_planted")
        return _clip("frame %s%s" % (result.get("frame"),
                                     "" if planted is None else
                                     ", feet planted" if planted else ", feet not planted"))
    if name == "kb_transition":
        joinable = result.get("joinable_by_blending")
        return _clip("%s%s" % (result.get("class") or "",
                               "" if joinable is not False else ", must be generated"))

    if name == "scene_search":
        found = result.get("results") or []
        return _names([r.get("label") or r.get("id") for r in found]) or "nothing matched"
    if name == "scene_query":
        objects = result.get("objects") or []
        here = [o for o in objects if o.get("exists") and not o.get("needs_walking")]
        if not objects:
            return ""
        return _clip("%d of %d within reach" % (len(here), len(objects)))
    if name == "move_to":
        if not result.get("arrived"):
            return _clip(result.get("note") or "not there yet")
        walked = result.get("path_length_m")
        # -1 is the engine's sentinel for "no complete route", not a distance. Printing it read as
        # "walked -1.0 m", which is not something that happened.
        if walked is None or walked < 0:
            return "arrived"
        return _clip("arrived, walked %.1f m" % walked)
    if name == "plan_motion":
        parts = ["dry run" if result.get("mode") == "dry_run" else "committed"]
        # The walk happens INSIDE this call now, and naming a seat is enough to ask for it, so it can
        # be entirely absent from what the model wrote. A line that does not mention it makes a plan
        # that crossed the room look identical to one committed where she already stood.
        travelled = (result.get("walked") or {}).get("path_length_m")
        if travelled is not None and travelled > 0:
            parts.append("walked %.1f m" % travelled)
        if result.get("opened_on"):
            parts.append("opened on %s" % result["opened_on"].get("played"))
        if result.get("sequence"):
            parts.append("%d steps" % len(result["sequence"]))
        generated = result.get("generated_transitions")
        if generated:
            parts.append("%d generated" % len(generated))
        return _clip(" · ".join(parts))
    if name == "check_motion":
        gates = result.get("gates")
        if isinstance(gates, dict):
            return _clip(gates.get("status") or "measured")
        return _clip(gates or "")

    if name == "glob":
        return "%d path%s" % (len(result.get("paths") or []), "" if len(result.get("paths") or []) == 1 else "s")
    if name == "grep":
        matches = result.get("matches") or result.get("results") or []
        return "%d match%s" % (len(matches), "" if len(matches) == 1 else "es")

    # A tool nobody taught this about still says whether it worked.
    for key in ("note", "status", "count"):
        if result.get(key) is not None:
            return _clip(result[key])
    return ""
