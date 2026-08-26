"""
kbindex.py — the MotionKB loaded once into memory, and the only place that decides what a model sees.

The KB is small (8 actions, ~1.4 MB with its frozen samples) and read-only at runtime, so it is loaded
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

WHAT IS DERIVED HERE. `posture_of` is a bin over the root channel's measured `mean_body_height`, not
a stored label. It is derived rather than deleted because the ENGINE needs it — every plan step
carries a posture so the executor can refuse to walk a seated character off a chair — and because it
is a fact about the clip's carriage that the measurement already contains.
"""
import json
import os
import re

import paths

# The 8 anatomical channels plus root. Mirrors config.STATE_CHANNELS; kept here as the retrieval-side
# vocabulary so this module does not drag in the extractor's calibration constants.
CHANNELS = ("root", "torso", "head", "left_arm", "right_arm",
            "left_leg", "right_leg", "left_hand", "right_hand")
ANATOMICAL = CHANNELS[1:]

# Below this mean body height (normalised humanoid units, HumanPose.bodyPosition.y) a clip's carriage
# is a seated one. It is a bin over a measurement, and the corpus leaves an unusually wide gap for it
# to sit in: the one seated action reads 0.647 and the lowest standing action (bvm, leaning over a
# patient) reads 0.859. 0.75 is the middle of that gap. Deliberately NOT combined with
# `mean_body_tilt_deg`: tilt measures forward lean, and cpr (44.6 deg) and bvm (39.3 deg) lean far
# harder than the seated clip does (8.7 deg), so any rule reading tilt gets those two wrong.
SEATED_BELOW = 0.75

# What a tool may hand to the model. `extraction` and `source_clip` are absent on purpose: provenance
# and asset guids cost tokens and inform no decision the model makes.
MODEL_VISIBLE_FIELDS = frozenset({
    "action_id", "action_description", "duration", "frame_rate", "loop", "channels",
})


def posture_of(rec):
    """'standing' or 'seated', binned from the record's measured carriage.

    Through v3 this was `composability.posture`, a label a VLM proposed and a human accepted. It is
    computed now because the label is gone (ADR 0022) and because it was never really a judgement:
    where the hips sit over the clip is a measurement, and the record keeps it.

    A record with no root carriage falls back to standing, which is what the executor assumes.
    """
    if not isinstance(rec, dict):
        return "standing"
    h = ((rec.get("channels") or {}).get("root") or {}).get("mean_body_height")
    if not isinstance(h, (int, float)):
        return "standing"
    return "seated" if h < SEATED_BELOW else "standing"

_STOP = frozenset("""
a an the and or but of to in on at by for with from into over under while during as is are was were be
been being it its this that these those her his their she he they them we you i not no nor so then than
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
    """One search result. Kept small on purpose — see the module docstring on the context budget."""

    __slots__ = ("action_id", "description", "score", "why", "posture")

    def __init__(self, action_id, description, score, why, posture):
        self.action_id = action_id
        self.description = description
        self.score = score
        self.why = why
        self.posture = posture

    def as_dict(self):
        return {"action_id": self.action_id, "description": self.description,
                "score": round(self.score, 2), "why": self.why, "posture": self.posture}

    def __repr__(self):
        return "Hit(%s, %.2f)" % (self.action_id, self.score)


class KBIndex:
    """The accepted action store, in memory, with a keyword index over it."""

    def __init__(self, actions):
        self.actions = actions                      # action_id -> full record
        self._ids = sorted(actions)
        self._docs = {aid: self._document(actions[aid]) for aid in self._ids}
        self._bm25 = None                           # built lazily; rank_bm25 import costs ~40 ms

    # ---- loading ---------------------------------------------------------------------------

    @classmethod
    def load(cls, actions_dir=None):
        """The accepted records, in memory.

        The store holds every record whatever its status (ADR 0016), and the agent retrieves only what
        has been described — an undescribed record has no sentence anywhere in it, so it is not
        findable by meaning and would only dilute the ranking. Which ones those are comes from
        `paths.accepted_files()`, which reads the manifest rather than opening 2454 files at every
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
        return Hit(aid, rec.get("action_description"), float(score), why, posture_of(rec))

    def _why(self, aid, query):
        """The query terms this action actually matched, so the model can judge the hit rather than
        trust the score. Ordered by how distinctive the term is across the corpus."""
        present = set(self._docs[aid])
        matched = {t for t in query if t in present}
        if not matched:
            return ""
        rarity = {t: sum(1 for other in self._ids if t in set(self._docs[other])) for t in matched}
        return ", ".join(sorted(matched, key=lambda t: (rarity[t], t))[:4])

    @staticmethod
    def _matches(rec, filters):
        """Structured narrowing, over the two things a v4 record can still be filtered BY.

        v3 filtered on `base_or_overlay`, `motion_type`, `contact_object` and a per-channel `role`
        as well. Each of those read a deleted field, and none has an honest substitute: what a clip
        touches and which part matters are readings the task supplies. `posture` survives because it
        is now measured (see `posture_of`), `loop` because it always was, and `moves_channel` is
        added in their place — "which action moves the legs" is a question the KINEMATIC half can
        answer, and it is the nearest thing to `channel_role` that is not a guess.
        """
        channels = rec.get("channels", {})

        for key, want in filters.items():
            if want is None:
                continue
            if key == "posture" and posture_of(rec) != want:
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
        vocabulary = set()
        for tokens in self._docs.values():
            vocabulary.update(tokens)
        return round(sum(1 for t in query if t in vocabulary) / len(query), 2)

    # `contacts()` lived here: object name -> [(action_id, effector)], read off `ik_goals`, as the
    # bridge from a scene object back to candidate motions. It had no callers, and v4 removes the
    # field it read (ADR 0022) -- what a clip touches is something the scene and the task decide, and
    # the plan's `ik_bindings` / `carry` are where that arrives.
