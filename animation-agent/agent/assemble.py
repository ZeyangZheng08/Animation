"""
assemble.py — deciding which action drives which body channel. Deterministic, no model involved.

THE MODEL DOES NOT EMIT THE PARTITION. It picks actions, scene targets and gaze/IK intent; this module
derives the channel assignment from the `role` table already in the contract. That is checkable, and it
was checked: the rule below reproduces both decompose cases in `retrieval_eval_set.json` exactly, channel
for channel. Having the model produce the partition would add an error mode and would turn the eval from
"can it retrieve and ground?" into "can it do arithmetic over a role table?".

THE RULE, derived from the ground truth rather than assumed:

    a BASE claims the channels where its role is `primary` or `support`
    an OVERLAY claims only the channels where its role is `primary`

Both halves are load-bearing, and each is falsified by the other case:

    grab_bottle (overlay)  primary   = right_arm, right_hand              == ground truth
                           +support  = ... + torso                        != ground truth
    giving_pills (base)    primary   = both arms, both hands              != ground truth
                           +support  = ... + torso, both legs             == ground truth

It is also the semantically right split. A base establishes the postural context, so the channels
actively holding it up belong to it; an overlay is grafted on and should touch as little as possible.
`stabilizer` (incidental balance, idle gaze) and `free` are claimed by nobody — which is exactly what
makes an arm swing overridable by a carry.

CONTENTION USED TO BE WINNER-TAKE-ALL. It no longer is, and that is the change this module exists to
carry: a channel two actions both claim is MIXED, at shares taken from the same role table that used to
decide the winner. Winner-take-all survives as the special case where the loser's share is zero.

    neither claimant grips anything on that channel   ->  mix, shares = normalised ROLE_PRIORITY
                                                          primary vs support  = 0.6 / 0.4
                                                          primary vs primary  = 0.5 / 0.5
    exactly one of them grips something               ->  that one takes the channel whole
    more than one grips something                     ->  the channel goes whole to one side and the
                                                          other's GRIP IS DROPPED, named; Conflict only
                                                          when the request named both objects
    root                                              ->  never mixed (see below)

WHY A GRIP IS NOT MIXABLE, AND WHY IT IS ALSO NOT A VETO. Half a hand on a patient's chest and half on a
pill bottle satisfies neither grip, so the FK on that channel goes whole to one side — a hand is a shape,
not an axis, and this is the one place the "half a pose nobody performs" argument really bites.

But a grip is two things fused, and only one of them is in the animation. The FK curves are joint
rotations; nothing in them holds anything. What holds the bottle is the `ik_goal` pulling the wrist to a
scene anchor and the animation event that makes the prop visible — both of which live outside the clip
and can simply not be applied. So the losing side keeps its hand motion and loses its object, which is
reported rather than done quietly: "she performs giving_pills' hand motion, with nothing in that hand"
is a true and useful description, and a refusal in its place threw away the other six channels too.

This matters because of how the corpus is shaped: EVERY channel where two actions tie is `right_hand`,
and every one of those has both sides holding a different object — `cpr` on `patient_chest`,
`giving_pills` on `pills`, `grab_bottle` on `aspirin_bottle`, `bvm` on `bvm_bag`, `check_pulse` on
`patient_wrist`. Six of the eight actions grip with that one hand. Under a veto, 20 of the 56 ordered
pairs were refused outright.

WHAT IS STILL REFUSED. If the REQUEST named both objects — both in `carry`, both bound with
`ik_bindings` — then dropping either is doing subtraction on the caller's behalf. That is reported as a
Conflict and left for the caller to resolve, which is what the veto was right about all along.

WHAT THE MIX IS ACTUALLY FOR is the other kind of contention:

    base=cpr           overlay=walking   left_leg / right_leg :  support vs primary
    base=giving_pills  overlay=walking   left_leg / right_leg :  support vs primary

"walk while giving the pills". The legs come mostly from the walk, but the bracing stance `giving_pills`
declares on them used to be discarded outright because primary outranks support. Those two are the only
contested pairs in the corpus with no grip on either side, and they are the shape `dc-walk-carry` is
about.

WHERE THE NUMBERS COME FROM. `ROLE_PRIORITY` was already in the contract and already decided this
channel; normalising it is not a new judgement, and it needed no new KB field, no schema change and no
re-authoring. A number chosen instead would have been a number nobody could defend.

ROOT IS NEVER MIXED. Two root motions added together are not a motion, so the channel goes whole to
one part or to nobody.

ROOT FOLLOWS THE LEGS. Whichever part ends up owning the leg channels owns the root, because where a
body goes is decided by what its legs did. It is read off `claims`, so it inherits every step of the
partition above for free — including a mix, whose owner takes the root the same way it takes the
channel. If nobody claims a leg, no part is driving the lower body and the root stays with the base.
Two parts each driving one leg is a real ambiguity and is reported as a conflict.

The rule used to be "root goes to the single part whose root channel is dynamic", which held while
the root signal was `max(gait, trans, heading)` in metres and only `walking` read dynamic. In muscle
space the root is `max(trans, vert, heading)` from `bodyPosition`/`bodyRotation` and counts turning,
so four of the eight actions read dynamic — and `walking` reads the LOWEST of the four (0.0382),
because the store's walk is in place: its body does not travel, only its step bounce rises. Ranking by
that number would hand the root to `giving_pills` (0.0687) over the walk. ADR 0011 had already moved
the same question in the validator, where `cyclic-locomotion` gates on a leg channel being dynamic
rather than on the root; this is that move applied here. See ADR 0013.

WHAT `free_channels` MEANS — and does not. It is an ownership statement: nobody claims these, so a later
overlay may take them without contention. It is NOT a masking instruction. Layer 0 plays the base clip
full-body and overlays mask on top; if unclaimed channels were masked out of layer 0 too, they would fall
back to the bind pose and the character would T-pose from the waist up.

IK IS ORTHOGONAL and does not participate in this partition at all. `engine_mask_map.json` says so
outright ("the IK/contact layer (ik_goals) is ORTHOGONAL to this FK mask"), and the ground truth agrees:
`grab_bottle` owns `right_hand` as an FK channel *and* carries an `ik_goal` for it, because a
TwoBoneIKConstraint post-processes the animated pose rather than replacing it.

WHAT IS NOT READ: `composability.can_overlay_on`, `.locks`, `.free`. `can_overlay_on` is an enumerated
whitelist — the pre-enumerated interaction template the research claim rejects — and taken literally it
kills `dc-walk-carry`, since `grab_bottle.can_overlay_on == ["idle"]` excludes walking. `locks`/`free` is
derived from `role == "free"`, so it means "busy", not "un-overridable".

ROOT IS NOT LOCOMOTION HERE. Every accepted clip is in-place: `body_trans_horiz_stddev` is below 0.01
on all of them, `walking` included — its root reads dynamic from body bob and heading sway, not from
travel (since v2.2.0 the root channel measures where the BODY goes, and an in-place walk's body goes
nowhere). Moving the character across the room is the NavMeshAgent's job; owning `root` here means
owning that in-place body motion, nothing more.
"""
from .kbindex import ANATOMICAL, CHANNELS, ROLE_PRIORITY

LEGS = ("left_leg", "right_leg")

BASE_CLAIMS = frozenset({"primary", "support"})
OVERLAY_CLAIMS = frozenset({"primary"})


class Conflict:
    """One channel two parts want and that cannot be split. Reported by name, never resolved by coin
    flip.

    `detail` is the whole reason as a phrase, when there is one worth reading. For the case this is
    mostly about — two actions gripping two different objects with one hand — "they conflict" does not
    tell anyone which two things cannot both be held, and a model reading it has no way to know which
    pair to break up. Absent, the reason is the bare role and the phrasing falls back to it.
    """

    __slots__ = ("channel", "action_ids", "role", "detail")

    def __init__(self, channel, action_ids, role, detail=None):
        self.channel = channel
        self.action_ids = tuple(action_ids)
        self.role = role
        self.detail = detail

    def why(self):
        return self.detail or ("%s both %s" % (" and ".join(self.action_ids), self.role))

    def as_dict(self):
        return {"channel": self.channel, "actions": list(self.action_ids), "role": self.role,
                "why": self.why()}

    def __repr__(self):
        return "Conflict(%s: %s)" % (self.channel, self.why())


class Mix:
    """One channel two actions drive at once, and the share each of them holds.

    `shares` is the answer to "how much of this channel is whose", normalised to 1 and ordered with the
    base first. `overlay_share` is the same fact in the form the engine needs: a layer mixer is
    cumulative and the base is already underneath unmasked, so what travels on the wire is one number
    per overlay, not a pair.
    """

    __slots__ = ("channel", "shares")

    def __init__(self, channel, shares):
        self.channel = channel
        self.shares = list(shares)          # [(action_id, share)], base first

    @property
    def owner(self):
        """Whoever holds the larger share. Ties go to the first, which is the base — the base
        establishes the posture, so it is the defensible tiebreak rather than an arbitrary one."""
        return max(self.shares, key=lambda pair: pair[1])[0]

    def overlay_weights(self, base_id):
        """The shares, converted into the weights a CUMULATIVE mixer needs. [(action_id, weight)].

        A layer mixer folds one layer in at a time — `result = lerp(result, layer, w)` — so a layer's
        weight is its share of what is left, not its share of the whole. With one overlay the two are
        the same number and this looks like a no-op; with two it is not, and getting it wrong is the
        kind of error that still produces a plausible pose.

        The base never appears: it is layer 0, unmasked, underneath everything, and its share is
        whatever the overlays leave. Read the list back-to-front to check it — the LAST layer is folded
        in last, so its weight is exactly its share, and each earlier one is scaled by the room the
        later ones left it.
        """
        overlays = [(aid, share) for aid, share in self.shares if aid != base_id]
        out, remaining = [], 1.0
        for aid, share in reversed(overlays):
            out.append((aid, min(1.0, share / remaining) if remaining > 1e-9 else 0.0))
            remaining -= share
        out.reverse()
        return out

    def as_dict(self):
        return {"channel": self.channel,
                "shares": [{"action_id": aid, "share": round(share, 4)}
                           for aid, share in self.shares]}

    def __repr__(self):
        return "Mix(%s: %s)" % (self.channel,
                                " + ".join("%.2f %s" % (s, a) for a, s in self.shares))


class DroppedGrip:
    """An object one action holds in its clip that this assembly does not give it.

    Not a failure and not a silent loss. The hand still performs that action's motion; what it does not
    get is the IK goal aiming its wrist and the prop attached to it, because the other action holding
    that same hand is the one being grounded. Carried so the plan can say which hand ends up empty.
    """

    __slots__ = ("action_id", "channel", "object", "kept_action_id", "kept_object")

    def __init__(self, action_id, channel, obj, kept_action_id, kept_object):
        self.action_id = action_id
        self.channel = channel
        self.object = obj
        self.kept_action_id = kept_action_id
        self.kept_object = kept_object

    def why(self):
        return ("%s drives %s and is grounded on %s, so %s's %s is not attached"
                % (self.kept_action_id, self.channel, self.kept_object, self.action_id, self.object))

    def as_dict(self):
        return {"action_id": self.action_id, "channel": self.channel, "object": self.object,
                "kept": {"action_id": self.kept_action_id, "object": self.kept_object},
                "why": self.why()}

    def __repr__(self):
        return "DroppedGrip(%s: %s)" % (self.channel, self.why())


class Assembly:
    """The derived partition: who drives what, what is shared, what is left free, what could not be
    decided."""

    def __init__(self, base, layers, free_channels, conflicts, root_owner, shared=None,
                 dropped=None):
        self.base = base
        self.layers = layers                # [(action_id, [channels])], base first, root appended
        self.free_channels = free_channels
        self.conflicts = conflicts
        self.root_owner = root_owner
        # Channels with two sources. Deliberately NOT folded into `layers`: that list is an OWNERSHIP
        # statement, it is what the eval scores by set equality, and a channel appearing under two
        # actions there would make the ground truth unscoreable. The engine needs both — who owns what,
        # and what is mixed — so they travel as two fields rather than one ambiguous one.
        self.shared = list(shared or [])
        # Grips this partition did not honour. Separate from `conflicts` because they are not failures:
        # the plan proceeds, one hand comes up empty, and the caller is told which.
        self.dropped = list(dropped or [])

    @property
    def ok(self):
        return not self.conflicts

    def channels_of(self, action_id):
        for aid, chans in self.layers:
            if aid == action_id:
                return chans
        return []

    def share_of(self, action_id, channel):
        """This action's share of one channel: 1 if it owns it outright, its mixed share if the channel
        is shared, 0 if it does not drive it at all."""
        for mix in self.shared:
            if mix.channel == channel:
                return dict(mix.shares).get(action_id, 0.0)
        return 1.0 if channel in self.channels_of(action_id) else 0.0

    def as_dict(self):
        return {
            "layers": [{"action_id": aid, "channels": chans,
                        "source": "base" if aid == self.base else "overlay",
                        "owns_root": aid == self.root_owner}
                       for aid, chans in self.layers],
            "shared": [m.as_dict() for m in self.shared],
            "dropped_grips": [d.as_dict() for d in self.dropped],
            "free_channels": self.free_channels,
            "conflicts": [c.as_dict() for c in self.conflicts],
            "rule": "base claims role in {primary,support}; overlay claims role=='primary'; "
                    "a channel both claim is mixed at normalised role priority, unless either side "
                    "grips something there, in which case the gripping side takes it whole; two grips "
                    "give the channel to one side and drop the other's object, and are a conflict only "
                    "when the request named both; "
                    "root to whichever part owns the legs, never mixed",
        }

    def __repr__(self):
        owned = ", ".join("%s=%s" % (a, "+".join(c)) for a, c in self.layers)
        if not self.shared:
            return "Assembly(%s)" % owned
        return "Assembly(%s | %s)" % (owned, ", ".join(repr(m) for m in self.shared))


def _role(channels, channel):
    return (channels.get(channel) or {}).get("role", "free")


def _gripped(channels, channel):
    """What this action holds on this channel, or None. `contact` is spelled `object:<alias>` when it is
    a thing and `ground`/`none` when it is not, so this is a read rather than an interpretation."""
    contact = str((channels.get(channel) or {}).get("contact") or "none")
    return contact[len("object:"):] if contact.startswith("object:") else None


def _shares(claimants):
    """Normalised role priority. `claimants` is [(action_id, role)]; returns [(action_id, share)].

    Nothing is invented here: `ROLE_PRIORITY` already ranked these roles and already decided this
    channel back when the higher rank simply took it. primary(3) against support(2) is 0.6 to 0.4;
    two primaries are half each. A rank of 0 cannot appear — `free` is never claimed.
    """
    weights = [(aid, float(ROLE_PRIORITY[role])) for aid, role in claimants]
    total = sum(w for _, w in weights)
    return [(aid, w / total) for aid, w in weights]


def arbitrate(base_id, overlay_ids, kb, named_objects=None, _promoted=False):
    """Derive the channel partition. `kb` is a KBIndex. Returns an Assembly.

    `named_objects` is what the REQUEST asked for by name — the things it said to carry or to bind a
    hand to. It is consulted for one thing only: when two actions grip the same hand, the one whose
    object was named keeps it, and if BOTH were named the pair is a conflict instead, because choosing
    then would be deciding something the caller already decided twice.

    A base that claims nothing is promoted away. `idle`'s role table is `free` on every channel, so it
    asserts nothing about any body part — as a base it establishes no posture at all. When it is the
    base under a single overlay, the overlay is the only thing setting posture, so it becomes the base
    and claims its `support` channels too. Concretely, "give the pills while standing idle" then keeps
    giving_pills' forward torso lean and its bracing legs, instead of handing pills bolt upright.
    """
    overlay_ids = list(overlay_ids)
    named = {str(n) for n in (named_objects or []) if n}
    if not _promoted and len(overlay_ids) == 1:
        base_channels = kb.channels(base_id)
        if not any(_role(base_channels, c) in BASE_CLAIMS for c in ANATOMICAL):
            return arbitrate(overlay_ids[0], [], kb, named_objects=named, _promoted=True)

    parts = [base_id] + overlay_ids
    channels_of = {aid: kb.channels(aid) for aid in parts}

    # Everyone who claims each channel, in `parts` order so the base is always first — which is what
    # makes the tiebreak and the wire's layer order deterministic rather than dict-order luck.
    claimants = {}       # channel -> [(action_id, role)]
    for aid in parts:
        allowed = BASE_CLAIMS if aid == base_id else OVERLAY_CLAIMS
        for channel in ANATOMICAL:
            role = _role(channels_of[aid], channel)
            if role in allowed:
                claimants.setdefault(channel, []).append((aid, role))

    claims = {}          # channel -> action_id, for the channels one action owns outright
    shared = []
    conflicts = []
    dropped = []
    roles = {(aid, channel): role for channel, who in claimants.items() for aid, role in who}
    for channel, who in sorted(claimants.items()):
        if len(who) == 1:
            claims[channel] = who[0][0]
            continue

        # A GRIP IS NOT A SHARE. Contact is read straight off the contract, so which branch a channel
        # takes is a fact about the data rather than a judgement made here.
        gripping = [(aid, _gripped(channels_of[aid], channel)) for aid, _ in who]
        gripping = [(aid, obj) for aid, obj in gripping if obj]
        if len(gripping) == 1:
            claims[channel] = gripping[0][0]
            continue
        if len(gripping) > 1:
            asked_for = [(aid, obj) for aid, obj in gripping if obj in named]
            if len(asked_for) > 1:
                # BOTH NAMED BY THE REQUEST. Dropping either would be doing subtraction on the
                # caller's behalf, so this one stays a refusal and says which two things it is about.
                conflicts.append(Conflict(
                    channel, [aid for aid, _ in asked_for],
                    "holding " + " and ".join(sorted(obj for _, obj in asked_for)),
                    detail=", ".join("%s holds %s" % (aid, obj) for aid, obj in asked_for)))
                continue
            # Otherwise the channel goes whole to one side and the others lose their OBJECT, not their
            # motion. Order of preference, each step deterministic: what the request named, then role
            # priority, then the base — the same tiebreak `Mix.owner` uses, and for the same reason.
            keeper, kept_object = max(
                gripping,
                key=lambda pair: (pair[1] in named,
                                  ROLE_PRIORITY.get(roles.get((pair[0], channel), "free"), 0),
                                  pair[0] == base_id))
            claims[channel] = keeper
            for aid, obj in gripping:
                if aid != keeper:
                    dropped.append(DroppedGrip(aid, channel, obj, keeper, kept_object))
            continue

        mix = Mix(channel, _shares(who))
        shared.append(mix)
        claims[channel] = mix.owner

    # Read off `claims`, so a mixed leg channel hands the root to the mix's owner without a second
    # rule for it. `claims` is only populated where somebody claimed the channel, so legs left free
    # mean no part is driving the lower body and the base keeps the root.
    leg_owners = sorted({claims[c] for c in LEGS if c in claims})
    if len(leg_owners) > 1:
        conflicts.append(Conflict("root", leg_owners, "driving one leg each"))
        root_owner = None
    else:
        root_owner = leg_owners[0] if leg_owners else base_id

    mixed_in = {aid for mix in shared for aid, _ in mix.shares}
    layers = []
    for aid in parts:
        chans = [c for c in ANATOMICAL if claims.get(c) == aid]
        if aid == root_owner:
            chans = chans + ["root"]
        # An action that owns nothing outright can still be driving something: it may hold a minority
        # share of a channel the other one owns. Kept in `layers` with an empty channel list so the
        # step that gets built has somewhere to hang its share — dropping it here is how a mix would
        # silently become a winner-take-all again, one layer short and looking correct.
        if chans or aid == base_id or aid in mixed_in:
            layers.append((aid, chans))

    free = [c for c in ANATOMICAL if c not in claims]
    # A drop only means something if the losing action is still in the plan. One that ended up driving
    # nothing at all is not "holding nothing" — it is absent, and saying its bottle was detached would
    # describe a hand that is not in this motion.
    still_here = {aid for aid, _ in layers}
    dropped = [d for d in dropped if d.action_id in still_here]
    return Assembly(base_id, layers, free, conflicts, root_owner, shared, dropped)


def decompose(parts, kb):
    """Same rule, expressed the way the eval ground truth is written: an unordered list of actions.

    The base is the one part the KB marks `base`. When every part is an overlay the single part is
    promoted rather than slipping `idle` underneath — `idle` is `free` on every channel, so it would sit
    below everything and claim nothing, making the arbitration vacuous while looking like it worked.
    """
    parts = list(parts)
    if not parts:
        raise ValueError("decompose needs at least one action")

    bases = [aid for aid in parts
             if kb.record(aid).get("composability", {}).get("base_or_overlay") == "base"]
    if len(bases) > 1:
        raise ValueError("more than one base action: %s" % ", ".join(sorted(bases)))
    base = bases[0] if bases else parts[0]
    return arbitrate(base, [aid for aid in parts if aid != base], kb)


FULL_MATCH = "full_match"
DECOMPOSE = "decompose"
NO_MATCH = "no_match"


def verdict(assembly, gaze_at=None):
    """How the library answered: one clip covered the request, or it had to be composed out of several.

    THE SAME DERIVATION ON BOTH PATHS. This used to live only inside run_eval, which meant the eval
    could report that the agent decomposes correctly while a live turn left no record of having
    decomposed at all — the branch that the whole retrieval-first claim rests on was visible only
    under measurement. It is one function now, called by the eval arm and by plan_motion, so the score
    and the trace are statements about the same thing.

    Deliberately mechanical. Which branch a request took is read off the partition that was actually
    derived, never from the model's prose about what it did.

    Two cases that are not what they look like:

    `idle` UNDERNEATH A LONE OVERLAY IS NOT A DECOMPOSITION. An overlay has no posture of its own, so
    naming one over `idle` is how a single overlay is played at all. `idle` is `free` on every channel
    and claims nothing but root, so scoring that as a composition would penalise the model for being
    more correct than the ground truth, which calls `giving_pills` alone a full match.

    A GAZE BINDING IS A DECOMPOSITION even with one retrieved part. Binding the head to a scene target
    frees it to be SOLVED rather than retrieved, which is exactly the FK-retrieval vs IK-goal split —
    `dc-givepills-gaze` in the eval set is one action plus a freed head, and the ground truth calls it
    a decompose.
    """
    mixed_in = {aid for mix in assembly.shared for aid, _ in mix.shares}
    parts = [{"action_id": aid, "channels": chans}
             for aid, chans in assembly.layers if chans or aid in mixed_in]
    contributing = [p for p in parts
                    if p["action_id"] != "idle" or [c for c in p["channels"] if c != "root"]]
    freed_by_gaze = ["head"] if gaze_at else []

    if len(contributing) > 1 or freed_by_gaze:
        out = {"type": DECOMPOSE, "parts": contributing,
               "free_channels": sorted(set(assembly.free_channels) | set(freed_by_gaze))}
        # A mix is the strongest form of decomposition there is — one body part being driven by two
        # retrieved clips at once — so it is named rather than left to be inferred from the partition.
        if assembly.shared:
            out["shared"] = [m.as_dict() for m in assembly.shared]
        return out
    if contributing:
        return {"type": FULL_MATCH, "action_id": contributing[0]["action_id"]}
    return {"type": FULL_MATCH, "action_id": assembly.base}


assert set(CHANNELS) - set(ANATOMICAL) == {"root"}, "root must be the only non-anatomical channel"
