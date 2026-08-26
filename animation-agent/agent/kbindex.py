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

TOKENIZATION. Tags are snake_case (`chest_compressions`, `bag_valve_mask`) while queries are ordinary
English ("performs chest compressions"). Splitting on non-alphanumerics is therefore load-bearing, not
cosmetic: without it the highest-signal field in the corpus never matches anything a user types.

WHAT THIS DELIBERATELY DOES NOT READ. `composability.can_overlay_on`, `.locks` and `.free` are in the
contract but are not consulted. `can_overlay_on` is an enumerated whitelist — precisely the pre-enumerated
interaction template the research claim rejects — and `locks`/`free` is derived from `role == "free"`, so
it means "this channel is busy", not "this channel may not be overridden". Taken literally they reject
both decompose cases in the eval set: `grab_bottle.can_overlay_on == ["idle"]` excludes walking, and
`giving_pills.free == []` cannot free the head. Channel ownership is decided by `role` priority instead,
in `arbitrate()` — which needs no change to the KB, since `role` is already in the contract and filled
for all eight actions.
"""
import json
import os
import re

import paths

# Ranked weakest to strongest. A part whose role ranks STRICTLY higher takes the channel; a tie means
# the two parts genuinely contend and the caller must decide (or the structural gate rejects the plan).
ROLE_PRIORITY = {"free": 0, "stabilizer": 1, "support": 2, "primary": 3}

# The 8 anatomical channels plus root. Mirrors config.STATE_CHANNELS; kept here as the retrieval-side
# vocabulary so this module does not drag in the extractor's calibration constants.
CHANNELS = ("root", "torso", "head", "left_arm", "right_arm",
            "left_leg", "right_leg", "left_hand", "right_hand")
ANATOMICAL = CHANNELS[1:]

# What a tool may hand to the model. `extraction` and `source_clip` are absent on purpose: provenance
# and asset guids cost tokens and inform no decision the model makes.
MODEL_VISIBLE_FIELDS = frozenset({
    "action_id", "display_name", "duration", "frame_rate", "loop",
    "overall_intent", "tags", "mask_coverage", "channels", "ik_goals", "composability",
})

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

    __slots__ = ("action_id", "display_name", "score", "why", "base_or_overlay", "posture")

    def __init__(self, action_id, display_name, score, why, base_or_overlay, posture):
        self.action_id = action_id
        self.display_name = display_name
        self.score = score
        self.why = why
        self.base_or_overlay = base_or_overlay
        self.posture = posture

    def as_dict(self):
        return {"action_id": self.action_id, "display_name": self.display_name,
                "score": round(self.score, 2), "why": self.why,
                "kind": self.base_or_overlay, "posture": self.posture}

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
        has been labelled — an unlabelled record has no tags, no intent and no motion_description, so it
        is not findable by meaning and would only dilute the ranking. Which ones those are comes from
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

        Tags carry the most signal per token (they are curated keywords), contact objects next — a query
        naming a bottle or a monitor is usually naming the thing the motion must touch.
        """
        parts = []
        parts += tokenize(" ".join(rec.get("tags", []))) * 3
        parts += tokenize(rec.get("display_name", "")) * 2
        parts += tokenize(rec.get("overall_intent", ""))
        for goal in rec.get("ik_goals", []):
            if goal.get("contact_object"):
                parts += tokenize(goal["contact_object"]) * 2
        for name, ch in rec.get("channels", {}).items():
            if ch.get("motion_description"):
                parts += tokenize(ch["motion_description"])
            if ch.get("motion_type"):
                parts += tokenize(ch["motion_type"])
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
        comp = rec.get("composability", {})
        return Hit(aid, rec.get("display_name", aid), float(score), why,
                   comp.get("base_or_overlay"), comp.get("posture"))

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
        comp = rec.get("composability", {})
        channels = rec.get("channels", {})

        for key, want in filters.items():
            if want is None:
                continue
            if key == "base_or_overlay" and comp.get("base_or_overlay") != want:
                return False
            elif key == "posture" and comp.get("posture") != want:
                return False
            elif key == "loop" and bool(rec.get("loop")) != bool(want):
                return False
            elif key == "motion_type":
                if not any(ch.get("motion_type") == want for ch in channels.values()):
                    return False
            elif key == "contact_object":
                objects = {g.get("contact_object") for g in rec.get("ik_goals", [])}
                objects |= {(ch.get("contact") or "").removeprefix("object:")
                            for ch in channels.values() if (ch.get("contact") or "").startswith("object:")}
                if want not in objects:
                    return False
            elif key == "channel_role":
                # {"left_leg": "primary"} — "which action drives the legs?"
                for channel, role in want.items():
                    if channels.get(channel, {}).get("role") != role:
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
        """Per-channel occupancy: what each body part is doing, what pose it sits in, and what it
        touches.

        This is the projection the assembly step actually reasons over, and it is a fraction of the
        ~2100 tokens of a full record.

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
            for key in ("role", "motion_type", "contact"):
                if ch.get(key) is not None:
                    entry[key] = ch[key]
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

    def contacts(self):
        """object name -> [(action_id, effector)]. Answers "what in the corpus touches an aspirin
        bottle?" — the bridge from a scene object back to candidate motions."""
        out = {}
        for aid in self._ids:
            for goal in self.actions[aid].get("ik_goals", []):
                obj = goal.get("contact_object")
                if obj:
                    out.setdefault(obj, []).append((aid, goal.get("effector")))
        return out


def arbitrate(parts):
    """Decide which part owns each channel, by role priority.

    `parts` is [(action_id, channels_projection)]. Returns (owners, contested) where owners maps
    channel -> action_id and contested lists channels where the top role is tied.

    This replaces `can_overlay_on`/`locks`/`free` — see the module docstring. Strictly-higher wins:
    `grab_bottle.right_arm` is `primary` and `walking.right_arm` is `stabilizer`, so carrying a bottle
    while walking resolves without either clip needing to know the other exists.
    """
    owners, contested = {}, []
    for channel in CHANNELS:
        claims = []
        for action_id, channels in parts:
            ch = channels.get(channel)
            if ch is None:
                continue
            # root has no role in the contract; whoever supplies locomotion supplies it.
            role = ch.get("role", "primary" if channel == "root" else "free")
            claims.append((ROLE_PRIORITY.get(role, 0), action_id))
        if not claims:
            continue
        claims.sort(reverse=True)
        top = claims[0][0]
        if len(claims) > 1 and claims[1][0] == top:
            contested.append(channel)
        owners[channel] = claims[0][1]
    return owners, contested
