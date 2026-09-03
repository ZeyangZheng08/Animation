"""
kbindex.py — the MotionKB loaded once into memory, and the only place that decides what a model sees.

The KB is 2446 accepted Mixamo actions, ~15 MB of records, and read-only at runtime, so it is loaded
whole at startup and never touched on disk again during a turn. The only writer is the offline pipeline.

WHY PROJECTIONS ARE NOT AN OPTIMIZATION. The demo's model has a 32k context window. One full action
record is ~2100 tokens, so the eight of them are ~17k — over half the window — and ~500 tokens of each is
the `extraction` block, which is pure provenance: extractor version, bone-map version, sampling rule,
VLM proposal metadata. None of that helps a model decide which motion to play. So every tool return here
is a projection, and `MODEL_VISIBLE_FIELDS` is the whitelist. `record()` returns the full document but is
for internal use — the executor needs `source_clip`'s guid and file_id, and the model never does.

TOKENIZATION. Descriptions are ordinary English and so are queries, but the fold on non-alphanumerics
and trivial plurals still earns its place: "chest compressions" has to match a sentence that says
"compression", and `action_id`s are snake_case.

WHAT A RECORD NOW HOLDS, and what follows from it. Since motionkb/v4 (ADR 0022) a record carries
measured kinematics and two kinds of description — one for the action, one per anatomical channel —
and nothing else. There is no `role`, no `contact`, no `composability`, no `ik_goals`. That is not a
loss this module works around: those fields answered questions about a COMBINATION (may another
action drive this part, what is this hand holding, may these two play together), and a clip alone on
a rendering floor cannot answer them. The agent decides them from the task and the scene, and says so
in its plan. So `arbitrate()` — which used to rank `role` and hand back an owner per channel — is
gone from this module, and channel ownership arrives from the plan instead (see agent/assemble.py).

WHERE POSTURE COMES FROM NOW. `posture_of` READS a sidecar, `derived/posture.json`, built by
`build_posture.py`. It used to bin the root channel's `mean_body_height` against one threshold, which
gives one word per clip — and a clip that stands up out of a chair is not one word. The sidecar holds
a per-frame segmentation over four coarse states (standing / seated / floor / other) with its start,
its end and its dominant state; this module reads the dominant one, and the tools that need the time
structure read the sidecar directly. The ENGINE is why it exists at all: every plan step carries a
posture so the executor can refuse to walk a seated character off a chair.

NO FALLBACK IF THE SIDECAR IS MISSING. `load` refuses to start without it. A fallback would answer
"standing" for a corpus of 2446 clips a fifth of which are on the floor, and the refusal it exists to
make would be quietly switched off.
"""
import json
import os
import re

import build_posture
import paths

# The 8 anatomical channels plus root. Mirrors config.STATE_CHANNELS; kept here as the retrieval-side
# vocabulary so this module does not drag in the extractor's calibration constants.
CHANNELS = ("root", "torso", "head", "left_arm", "right_arm",
            "left_leg", "right_leg", "left_hand", "right_hand")
ANATOMICAL = CHANNELS[1:]


def _posture_entry(rec):
    """The record's entry in `derived/posture.json`, or a SystemExit naming the rebuild.

    An action the sidecar does not cover is not a missing value to be defaulted: a store and a
    sidecar that disagree about what is in the KB is a build that did not finish, and the honest
    answer to "what posture is this" is then "the sidecar is stale", not "standing".
    """
    aid = rec.get("action_id") if isinstance(rec, dict) else None
    entry = build_posture.read_sidecar().get(aid)
    if entry is None:
        raise SystemExit(
            "no posture for %r in %s.\n"
            "The sidecar does not cover the accepted store. Rebuild it:  python build_posture.py"
            % (aid, paths.rel(build_posture.PATH)))
    return entry


def posture_detail(rec):
    """The record's whole posture entry: the two ends, the dominant state, the segmentation, the
    boundaries between segments, and how far the clip travels.

    A COPY, because the sidecar is memoised and shared for the life of the process. A tool that
    returned the stored dict would hand a mutable view of the index to whatever formats it next.
    """
    entry = _posture_entry(rec)
    return {
        "start_posture": entry["start_posture"],
        "end_posture": entry["end_posture"],
        "dominant_posture": entry["dominant_posture"],
        "posture_segments": [dict(seg) for seg in entry["posture_segments"]],
        "posture_transitions": [dict(t) for t in entry["posture_transitions"]],
        "root_travel": dict(entry.get("root_travel") or {}),
    }


def root_travel_of(rec):
    """(dx, dz, yaw_deg) for a clip: how far it carries the PELVIS, in world metres.

    THE NUMBER A SEAT IS PLACED FROM. A retrieved sit-down steps backwards into the chair, so the
    point she has to be standing on before it starts is the seat MINUS this displacement, rotated
    into the direction she will be facing. See `scene.standing_point_for`.

    AND IT IS THE PELVIS, NOT THE ROOT, whatever the field is called. Measured on
    `mx_Standing_To_Sitting_Transition`: the clip's own root curve moves the transform 0.331 m, the
    pelvis folds a further 0.115 m back relative to that root as she sits, and this field is their
    sum, 0.446 m. The pelvis is the right one because the pelvis is what has to end up on the seat and
    what `seat_alignment` measures; placing her by the root curve alone would leave her 0.115 m short.
    `build_posture.root_travel` derives it and states the full reconciliation.
    """
    travel = _posture_entry(rec).get("root_travel") or {}
    return (float(travel.get("dx") or 0.0), float(travel.get("dz") or 0.0),
            float(travel.get("yaw_deg") or 0.0))


def posture_of(rec):
    """A record's coarse posture: 'standing', 'seated', 'floor' or 'other'.

    THE VALUE IS NOT COMPUTED HERE. It is the `dominant_posture` of the record's entry in
    `derived/posture.json`, which `build_posture.py` derives from the frozen `raw` dump by geometric
    rules over trunk, thigh, shank and knee angles and the normalised body height. That module owns
    the rules and the version; this one only looks the answer up, so a posture cannot come to mean
    one thing to the pipeline and another to retrieval.

    Takes a RECORD rather than an action_id because every caller already has one, and because the
    record is where the id lives.
    """
    return _posture_entry(rec)["dominant_posture"]


def posture_span_of(rec):
    """(start_posture, end_posture) for the record — the two ends of the same segmentation.

    Separate from `posture_of` because they answer different questions: "what is this clip mostly"
    decides how a step is classified, and "where does it begin and end" decides whether two steps
    can be joined at all. A clip that stands up is dominantly one of the two and compatible with
    neither at both ends.
    """
    entry = _posture_entry(rec)
    return entry["start_posture"], entry["end_posture"]


# `mx` is in here for a reason the rest of the list does not share: it is not an English stopword, it
# is the corpus's filename prefix. Every one of the 2446 action_ids begins with it, so `_document`
# emits it 2446 times and BM25 gives it an IDF of zero — which is harmless — while a query that
# happens to contain it matches everything, and `coverage` counts it as a word the library has. It
# carries no meaning about motion in either direction, so it is dropped where every other meaningless
# token is dropped, rather than by stripping the prefix in `_document` and leaving `coverage` to
# disagree with the index about what a word is.
_STOP = frozenset("""
a an the and or but of to in on at by for with from into over under while during as is are was were be
been being it its this that these those her his their she he they them we you i not no nor so then than
mx
""".split())

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text):
    """Lowercase, split on non-alphanumerics, drop stopwords, fold trivial plurals.

    The plural fold is what makes "chest compressions" match the tag `chest_compressions` and "pills"
    match `pill`. It is deliberately crude — a real stemmer would be more machinery than 8 documents and
    a dozen queries can justify.
    """
    out = []
    for word in _TOKEN.findall(text.lower()):
        if word in _STOP:
            continue
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        out.append(word)
    return out


class Hit:
    """One search result. Kept small on purpose — see the module docstring on the context budget.

    THREE POSTURES, NOT ONE. `posture` is what the clip mostly is, and it is what a filter matches on;
    `start_posture` and `end_posture` are where it begins and ends, which is the pair that decides
    whether two clips can be put in sequence at all. A clip that stands up out of a chair is
    dominantly one of the two and joins neither cleanly at both ends, and a result that offered only
    the dominant reading would hide exactly the clips a posture change needs.
    """

    __slots__ = ("action_id", "description", "score", "why", "posture",
                 "start_posture", "end_posture")

    def __init__(self, action_id, description, score, why, posture,
                 start_posture=None, end_posture=None):
        self.action_id = action_id
        self.description = description
        self.score = score
        self.why = why
        self.posture = posture
        self.start_posture = start_posture
        self.end_posture = end_posture

    def as_dict(self):
        return {"action_id": self.action_id, "description": self.description,
                "score": round(self.score, 2), "why": self.why, "posture": self.posture,
                "start_posture": self.start_posture, "end_posture": self.end_posture}

    def __repr__(self):
        return "Hit(%s, %.2f)" % (self.action_id, self.score)


class KBIndex:
    """The accepted action store, in memory, with a keyword index over it."""

    def __init__(self, actions):
        self.actions = actions                      # action_id -> full record
        self._ids = sorted(actions)
        self._docs = {aid: self._document(actions[aid]) for aid in self._ids}
        self._bm25 = None                           # built lazily; rank_bm25 import costs ~40 ms
        # PRECOMPUTED ONCE, BECAUSE BOTH READERS WERE QUADRATIC IN THE CORPUS. `_why` ranked a
        # matched term by how many documents hold it, and built a fresh set per document per term:
        # over eight documents that was free, over 2446 it is a set construction per term per hit per
        # search. `coverage` unioned every document's tokens on every call for the same reason. Both
        # answers are functions of the index alone, so they are answered from these two tables --
        # built with the rest of the index in under 100 ms, read in constant time afterwards.
        self._terms = {aid: frozenset(tokens) for aid, tokens in self._docs.items()}
        self._document_frequency = {}
        for terms in self._terms.values():
            for term in terms:
                self._document_frequency[term] = self._document_frequency.get(term, 0) + 1
        self._vocabulary = frozenset(self._document_frequency)

    # ---- loading ---------------------------------------------------------------------------

    @classmethod
    def load(cls, actions_dir=None):
        """The accepted records, in memory.

        The store holds every record whatever its status (ADR 0016), and the agent retrieves only what
        has been described — an undescribed record has no sentence anywhere in it, so it is not
        findable by meaning and would only dilute the ranking. Which ones those are comes from
        `paths.accepted_files()`, which reads the manifest rather than opening 2446 files at every
        start. Pass `actions_dir` to read a directory directly instead; tests use it.
        """
        paths.require_kb()
        where = actions_dir or paths.ACTIONS_DIR
        files = paths.action_files(actions_dir) if actions_dir else paths.accepted_files()
        actions = {}
        for path, rec, err in paths.read_records(files):
            if err:
                raise SystemExit("cannot read %s: %s" % (path, err))
            if rec.get("status") != "accepted":
                continue
            actions[rec["action_id"]] = rec
        if not actions:
            raise SystemExit(
                "no accepted actions found in %s — the KB is present but empty, which would make every "
                "retrieval silently return nothing" % where)
        if actions_dir is None:
            # THE POSTURE SIDECAR IS PART OF LOADING THE KB, not an optional extra read later. Every
            # plan step carries a posture, so a store the sidecar does not cover is a KB that cannot
            # answer at the moment it is asked, in the middle of a turn, once per action. Checked
            # here, at start-up, it is one message naming one command.
            postures = build_posture.read_sidecar()
            uncovered = sorted(set(actions) - set(postures))
            if uncovered:
                raise SystemExit(
                    "%s covers %d action(s); the accepted store has %d, and %d of them are not in "
                    "it\n(first: %s).\nRebuild it:  python build_posture.py"
                    % (paths.rel(build_posture.PATH), len(postures), len(actions), len(uncovered),
                       ", ".join(uncovered[:3])))
        return cls(actions)

    # ---- the searchable document -----------------------------------------------------------

    @staticmethod
    def _document(rec):
        """Field-weighted bag of tokens. Weighting is by repetition, which is how BM25 sees emphasis.

        Two sources now, where v3 had six. `tags` carried the most signal per token and is deleted
        along with `display_name`, `overall_intent`, `motion_type` and the contact objects lifted out
        of `ik_goals` (ADR 0022). What replaces them is `action_description` — one sentence about the
        whole action, repeated x3 to hold the weight the curated keywords used to — plus the eight
        per-channel sentences at x1. The x3 is not a tuned number: it is the old tag weight, applied
        to the field that inherited the tags' job, and it keeps a ~15-token summary from being
        outvoted by ~80 tokens of body-part detail. `action_id` is added at x1 because it is the only
        remaining place a compound name like `check_pulse` appears as one phrase.
        """
        parts = []
        parts += tokenize(rec.get("action_description") or "") * 3
        parts += tokenize(rec.get("action_id") or "")
        for name, ch in rec.get("channels", {}).items():
            if ch.get("motion_description"):
                parts += tokenize(ch["motion_description"])
        return parts

    def _ensure_bm25(self):
        if self._bm25 is None:
            from rank_bm25 import BM25Okapi
            self._bm25 = BM25Okapi([self._docs[aid] for aid in self._ids])
        return self._bm25

    # ---- retrieval -------------------------------------------------------------------------

    def search(self, text=None, filters=None, limit=5):
        """Rank actions by keyword relevance, optionally within a structured filter.

        With no `text`, this degenerates to "list everything matching the filter", which is the useful
        shape for questions like "which actions are seated?".
        """
        candidates = [aid for aid in self._ids if self._matches(self.actions[aid], filters or {})]
        if not candidates:
            return []

        if not text:
            return [self._hit(aid, 0.0, "") for aid in candidates][:limit]

        query = tokenize(text)
        scores = dict(zip(self._ids, self._ensure_bm25().get_scores(query)))
        ranked = sorted(candidates, key=lambda aid: scores[aid], reverse=True)
        return [self._hit(aid, scores[aid], self._why(aid, query)) for aid in ranked[:limit]]

    def _hit(self, aid, score, why):
        rec = self.actions[aid]
        start, end = posture_span_of(rec)
        return Hit(aid, rec.get("action_description"), float(score), why, posture_of(rec),
                   start_posture=start, end_posture=end)

    def _why(self, aid, query):
        """The query terms this action actually matched, so the model can judge the hit rather than
        trust the score. Ordered by how distinctive the term is across the corpus."""
        matched = {t for t in query if t in self._terms[aid]}
        if not matched:
            return ""
        return ", ".join(sorted(matched,
                                key=lambda t: (self._document_frequency.get(t, 0), t))[:4])

    @staticmethod
    def _matches(rec, filters):
        """Structured narrowing, over what a v4 record plus its posture sidecar can be filtered BY.

        v3 filtered on `base_or_overlay`, `motion_type`, `contact_object` and a per-channel `role`
        as well. Each of those read a deleted field, and none has an honest substitute: what a clip
        touches and which part matters are readings the task supplies. `posture` survives because it
        is now measured (see `posture_of`), `loop` because it always was, and `moves_channel` is
        added in their place — "which action moves the legs" is a question the KINEMATIC half can
        answer, and it is the nearest thing to `channel_role` that is not a guess.

        TWO MORE ARRIVED WITH THE CORPUS, and both exist because 2446 documents make a search that
        cannot be steered useless in a way eight never did.

        `exclude` names ids to drop. Its job is the second search: the first returned five plausible
        clips, the agent read them and rejected them, and without this the only way to see the sixth
        is to rephrase — which changes the ranking as well as the offset, so the rejected five come
        back in a different order. Naming them is how "not these" is said once.

        `transition` filters on the ENDS rather than the middle: `{from_posture, to_posture}` keeps
        clips that start in one and finish in the other. That is the query a posture change needs and
        `posture` cannot express, because a clip that stands up out of a chair is dominantly one or
        the other and matches neither honestly. It combines with `posture` like every other clause
        here — an intersection, which is what this loop has always computed — so "mostly seated, and
        it gets there from standing" is one search rather than a refusal.
        """
        channels = rec.get("channels", {})

        for key, want in filters.items():
            if want is None:
                continue
            if key == "posture" and posture_of(rec) != want:
                return False
            elif key == "exclude":
                if rec.get("action_id") in set(want):
                    return False
            elif key == "transition":
                start, end = posture_span_of(rec)
                if want.get("from_posture") and start != want["from_posture"]:
                    return False
                if want.get("to_posture") and end != want["to_posture"]:
                    return False
            elif key == "loop" and bool(rec.get("loop")) != bool(want):
                return False
            elif key == "moves_channel":
                # ["left_leg", "right_leg"] — every named channel must be measured dynamic.
                for channel in ([want] if isinstance(want, str) else want):
                    if (channels.get(channel) or {}).get("state_label") != "dynamic":
                        return False
        return True

    # ---- projections -----------------------------------------------------------------------

    def record(self, action_id):
        """The full document, INTERNAL USE ONLY — carries `source_clip` guids and the provenance block.
        Tools must go through `project`/`channels` so nothing unbudgeted reaches the model."""
        try:
            return self.actions[action_id]
        except KeyError:
            raise KeyError("no such action: %r (have: %s)" % (action_id, ", ".join(self._ids)))

    def project(self, action_id, fields=None):
        """Whitelisted field subset for the model."""
        rec = self.record(action_id)
        wanted = MODEL_VISIBLE_FIELDS if fields is None else (set(fields) & MODEL_VISIBLE_FIELDS)
        return {k: rec[k] for k in rec if k in wanted}

    def channels(self, action_id):
        """Per-channel facts: what each body part is doing, what pose it sits in, and how the record
        describes it.

        This is the projection the model and the assembly step reason over, and it is a fraction of
        the ~2100 tokens of a full record. Everything in it is either measured or a sentence: the
        `role` / `motion_type` / `contact` entries this used to carry were the assembler's whole
        input, and their removal (ADR 0022) is exactly why the plan now names channels itself.

        `mean_pose` is handed over whole rather than summarised, because the point of storing the
        vector is that no single number stands in for it: a raised-and-held arm and a hanging-still
        arm are both `state: static`, and the pose is the only thing that tells them apart
        (ADR 0021). Values are rounded to 2 decimals here — that is this projection's compaction,
        not the record's precision, which is 5. Nothing is labelled: there is no neutral/displaced
        reading of these numbers and no threshold that would produce one.
        """
        rec = self.record(action_id)
        out = {}
        for name in CHANNELS:
            ch = rec.get("channels", {}).get(name)
            if ch is None:
                continue
            entry = {"state": ch.get("state_label")}
            pose = ch.get("mean_pose")
            if isinstance(pose, dict):
                entry["mean_pose"] = {dof: round(v, 2) for dof, v in pose.items()}
            for key in ("mean_body_height", "mean_body_tilt_deg"):
                if ch.get(key) is not None:
                    entry[key] = ch[key]
            if ch.get("motion_description"):
                entry["describes"] = ch["motion_description"]
            out[name] = entry
        return out

    def coverage(self, text):
        """Fraction of the query's content words that appear anywhere in the corpus, 0..1.

        The honest no-match signal. BM25 scores are unnormalized and always rank something first, so a
        query about pressing a button still returns `grab_bottle` at the top. Coverage says how much of
        what was asked for the corpus has words for at all — 1.0 means the vocabulary is all there and a
        low score is a real ranking judgement; 0.3 means most of the request is simply absent.

        Reported rather than thresholded: calibrating a cutoff on the same twelve cases the system is
        evaluated against would be overfitting, and over eight documents the cutoff is noise.
        """
        query = tokenize(text)
        if not query:
            return None
        return round(sum(1 for t in query if t in self._vocabulary) / len(query), 2)

    # `contacts()` lived here: object name -> [(action_id, effector)], read off `ik_goals`, as the
    # bridge from a scene object back to candidate motions. It had no callers, and v4 removes the
    # field it read (ADR 0022) -- what a clip touches is something the scene and the task decide, and
    # the plan's `ik_bindings` / `carry` are where that arrives.
