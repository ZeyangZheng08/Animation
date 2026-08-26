"""
assemble.py — turning the agent's channel assignment into an executable partition. No model here.

WHO DECIDES THE PARTITION, AND WHY IT CHANGED. Through v3 this module DERIVED the assignment from a
`role` label the knowledge base carried on every channel: a base claimed `primary` and `support`, an
overlay claimed `primary`, and `stabilizer`/`free` were claimed by nobody. That rule reproduced both
decompose cases in the eval set exactly, and it was the right rule for the contract it read.

motionkb/v4 deletes `role` (ADR 0022), and not as an oversight. `role` was a statement about a
COMBINATION written into a record that describes one clip: `walking`'s arms are "stabilizer" only
relative to some other action that might want them, and whether a swinging arm is incidental or is
the point depends entirely on what the character is being asked to do. A clip previewed alone on an
empty floor cannot answer that, and a stored answer is a pre-enumerated composition — the thing the
research claim rejects.

So the assignment ARRIVES here, from the agent's plan. The agent has the task and the scene; it names
which action drives which channel. This module does what remains, all of it deterministic and all of
it checkable: it detects the channels two actions both claim, it decides what happens there, it
routes the root, and it reports what it could not settle. Nothing here guesses.

It was worth checking that the alternative was really closed. It is: no threshold on the surviving
kinematics reconstructs the old partition. `walking.left_arm` is stabilizer at magnitude 0.165,
`cpr.left_arm` is support at 0.114 and `typing.left_arm` is primary at 0.125 — the ordering is not
even monotone. An argmax-magnitude rule gets 3 of 8 channels wrong on `dc-walk-carry`, and all three
failures are the same shape: a channel that moves and that nobody should claim. That distinction is a
judgement about what the motion is FOR, which is what the agent is there to supply.

THE ASSIGNMENT, and what it defaults to:

    base            the action that sets the posture. Plays on layer 0, FULL BODY, always.
    base_channels   the channels the base CLAIMS, default none. Claiming is not playing: layer 0 is
                    never masked, so this list does not decide what the base animates. It decides
                    what an overlay may not take without contending for it.
    overlays        [(action_id, channels)] — each overlay names the channels it drives. An overlay
                    that names none is rejected rather than dropped: a layer with an empty mask plays
                    FULL BODY at full weight in the engine, so silently keeping it would replace the
                    whole body with the overlay, and silently dropping it would answer a request the
                    caller did not make.

CONTENTION. A channel two parts both name is MIXED, half each.

    two claimants, neither pinned    ->  mix, 0.5 / 0.5
    the channel carries an IK pin    ->  conflict, named
    root                             ->  never mixed (see below)

The shares are equal because there is nothing left to rank them by, and because the plan is the one
place a number must never come from: the agent names actions and channels, never weights (a rule the
plan schema enforces structurally — every leaf in it is a string or a boolean). Under v3 the shares
came from normalising `ROLE_PRIORITY`, which gave 0.6/0.4 for primary-against-support. That number
was defensible only as long as the ranking it normalised existed. Half each is what is left, and it
is the honest reading of "the agent asked for both of these here".

WHY A PINNED CHANNEL IS NOT MIXABLE. Half a hand shaped for a pill bottle and half shaped for a
patient's chest is a shape that grips neither, and an IK constraint then drags the wrist of a pose
that was never a grip. A hand is a shape, not an axis. When the plan pins an effector to an object —
through `carry` or `ik_bindings` — and then asks two actions to drive that same hand, the two halves
of the request contradict each other and the caller is told so by name rather than served a blend.

Nothing is DROPPED any more. v3 carried a `DroppedGrip`: two actions each declared a `contact` in the
KB, one kept its object and the other kept its motion without it. That whole machinery read
`channels.*.contact`, and a v4 record does not say what a clip holds — the plan does, once, per hand.
There is no second grip left to drop.

ROOT IS NEVER MIXED. Two root motions added together are not a motion, so the channel goes whole to
one part or to nobody.

ROOT FOLLOWS THE LEGS. Whichever part owns the leg channels owns the root, because where a body goes
is decided by what its legs did. It is read off the claims, so it inherits the assignment above for
free — including a mix, whose owner takes the root the same way it takes the channel. If nobody
claims a leg, no part is driving the lower body and the root stays with the base. Two parts each
driving one leg is a real ambiguity and is reported as a conflict.

The rule used to be "root goes to the single part whose root channel is dynamic", which held while
the root signal was `max(gait, trans, heading)` in metres and only `walking` read dynamic. In muscle
space the root is `max(trans, vert, heading)` from `bodyPosition`/`bodyRotation` and counts turning,
so four of the eight actions read dynamic — and `walking` reads the LOWEST of the four, because the
store's walk is in place. Ranking by that number would hand the root to `giving_pills` over the walk.
See ADR 0013.

WHAT `free_channels` MEANS — and does not. It is an ownership statement: nobody claimed these, so a
later overlay may take them without contention. It is NOT a masking instruction. Layer 0 plays the
base clip full-body and overlays mask on top; if unclaimed channels were masked out of layer 0 too,
they would fall back to the bind pose and the character would T-pose from the waist up.

IK IS ORTHOGONAL and does not participate in this partition at all. `engine_mask_map.json` says so
outright, and it stays true: a TwoBoneIKConstraint post-processes the animated pose rather than
replacing it, so an action can own a hand as an FK channel and have that same hand pulled to a scene
anchor. What changed is only where the goal comes from — the plan, not the record.

ROOT IS NOT LOCOMOTION HERE. Every accepted clip is in-place: `body_trans_horiz_stddev` is below 0.01
on all of them, `walking` included — its root reads dynamic from body bob and heading sway, not from
travel. Moving the character across the room is the NavMeshAgent's job; owning `root` here means
owning that in-place body motion, nothing more.
"""
from .kbindex import ANATOMICAL, CHANNELS

LEGS = ("left_leg", "right_leg")

# The channels an IK pin can land on. `gaze_at` pins the head the same way a carry pins a hand.
PINNABLE = ("left_hand", "right_hand", "head")


class Conflict:
    """One channel two parts want and that cannot be split. Reported by name, never resolved by coin
    flip.

    `detail` is the whole reason as a phrase, when there is one worth reading. "They conflict" does
    not tell anyone what cannot both happen, and a model reading it has no way to know which part of
    its plan to change. Absent, the phrasing falls back to the bare reason.
    """

    __slots__ = ("channel", "action_ids", "reason", "detail")

    def __init__(self, channel, action_ids, reason, detail=None):
        self.channel = channel
        self.action_ids = tuple(action_ids)
        self.reason = reason
        self.detail = detail

    def why(self):
        return self.detail or ("%s both %s" % (" and ".join(self.action_ids), self.reason))

    def as_dict(self):
        return {"channel": self.channel, "actions": list(self.action_ids), "reason": self.reason,
                "why": self.why()}

    def __repr__(self):
        return "Conflict(%s: %s)" % (self.channel, self.why())


class Mix:
    """One channel two actions drive at once, and the share each of them holds.

    `shares` is the answer to "how much of this channel is whose", normalised to 1 and ordered with the
    base first. `overlay_weights` is the same fact in the form the engine needs: a layer mixer is
    cumulative and the base is already underneath unmasked, so what travels on the wire is one number
    per overlay, not a pair.
    """

    __slots__ = ("channel", "shares")

    def __init__(self, channel, shares):
        self.channel = channel
        self.shares = list(shares)          # [(action_id, share)], base first

    @property
    def owner(self):
        """Whoever holds the larger share. Every mix is equal-share now, so in practice this is
        always the first — which is the base, and the base establishes the posture, so it is the
        defensible tiebreak rather than an arbitrary one."""
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


class Assembly:
    """The partition as the engine will run it: who drives what, what is shared, what is left free,
    what could not be decided."""

    def __init__(self, base, layers, free_channels, conflicts, root_owner, shared=None):
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
            "free_channels": self.free_channels,
            "conflicts": [c.as_dict() for c in self.conflicts],
            "rule": "each part drives the channels the plan gave it; a channel two parts name is "
                    "mixed half each, unless an IK pin lands on it, which makes it a conflict; "
                    "root to whichever part owns the legs, never mixed",
        }

    def __repr__(self):
        owned = ", ".join("%s=%s" % (a, "+".join(c)) for a, c in self.layers)
        if not self.shared:
            return "Assembly(%s)" % owned
        return "Assembly(%s | %s)" % (owned, ", ".join(repr(m) for m in self.shared))


def normalise_overlays(overlays):
    """`overlays` as [(action_id, [channels])], from either that shape or a list of dicts.

    A bare action_id is REJECTED rather than defaulted. Naming the channels is the decision v4 hands
    to the agent, and there is no honest default for it: an empty mask plays full body in the engine,
    and inventing a channel list here would be re-deriving the partition from a record that no longer
    describes one.
    """
    out = []
    for item in overlays or []:
        if isinstance(item, dict):
            aid = item.get("action_id")
            chans = item.get("channels")
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            aid, chans = item
        else:
            aid, chans = item, None
        if not aid:
            raise ValueError("an overlay with no action_id")
        if not chans:
            raise ValueError("overlay '%s' names no channels — say which body parts it drives "
                             "(any of: %s)" % (aid, ", ".join(ANATOMICAL)))
        unknown = [c for c in chans if c not in ANATOMICAL]
        if unknown:
            raise ValueError("overlay '%s' names channels that do not exist: %s (have: %s)"
                             % (aid, ", ".join(unknown), ", ".join(ANATOMICAL)))
        out.append((aid, [c for c in ANATOMICAL if c in set(chans)]))
    return out


def arbitrate(base_id, overlays, kb=None, base_channels=None, pinned_channels=None):
    """Build the Assembly from the agent's assignment. Returns an Assembly.

    `overlays` is what the plan asked for: [(action_id, channels)] or [{"action_id":…,"channels":[…]}].
    `base_channels` is what the base claims; empty by default, because the base plays full-body on
    layer 0 whether or not it claims anything, so claiming is only about keeping an overlay off.
    `pinned_channels` are the channels the plan pins to a scene object (a carried hand, a gaze-bound
    head). A pinned channel two actions both drive is a conflict rather than a mix.

    `kb` is accepted and unused. It is kept in the signature because every caller has one and because
    a later check that a named action exists belongs here; the arbitration itself no longer reads the
    knowledge base at all, which is the whole point of v4.
    """
    overlays = normalise_overlays(overlays)
    pinned = {c for c in (pinned_channels or []) if c in ANATOMICAL}

    unknown = [c for c in (base_channels or []) if c not in ANATOMICAL]
    if unknown:
        raise ValueError("base '%s' names channels that do not exist: %s (have: %s)"
                         % (base_id, ", ".join(unknown), ", ".join(ANATOMICAL)))

    # AN ACTION CANNOT FIGHT ITSELF FOR A BODY PART. A repeated action_id asks for the same layer
    # twice and the second says nothing the first did not, so the channel lists are merged.
    # The base is always a part, even claiming nothing: it plays layer 0 and holds up every channel
    # no overlay took.
    merged = {base_id: {c for c in ANATOMICAL if c in set(base_channels or [])}}
    order = [base_id]
    for aid, chans in overlays:
        if aid == base_id:
            merged[base_id].update(chans)
            continue
        if aid not in merged:
            merged[aid] = set()
            order.append(aid)
        merged[aid].update(chans)

    claimants = {}       # channel -> [action_id], in plan order so the base is always first
    for aid in order:
        for channel in ANATOMICAL:
            if channel in merged[aid]:
                claimants.setdefault(channel, []).append(aid)

    claims = {}          # channel -> action_id, for the channels one action owns outright
    shared = []
    conflicts = []
    for channel, who in sorted(claimants.items()):
        if len(who) == 1:
            claims[channel] = who[0]
            continue
        if channel in pinned:
            conflicts.append(Conflict(
                channel, who, "driving a channel the plan pins to an object",
                detail="%s is bound to something in the scene, and %s were both asked to drive it — "
                       "a hand blended out of two grips holds neither"
                       % (channel, " and ".join(who))))
            continue
        share = 1.0 / len(who)
        mix = Mix(channel, [(aid, share) for aid in who])
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
    for aid in order:
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
    return Assembly(base_id, layers, free, conflicts, root_owner, shared)


def decompose(parts, kb=None, base=None):
    """The same arbitration, expressed the way the eval ground truth is written: a mapping from
    action_id to the channels that action drives.

    The base is named explicitly, or is whichever part drives a leg — where a body's weight goes is
    what a posture is, so the part driving the legs is the one setting the stance. Failing that, the
    first part. Under v3 the base was read off `composability.base_or_overlay`, a stored label that
    v4 deletes (ADR 0022); nothing measured replaces it, because whether a clip is foundational or
    grafted-on is a fact about the combination it is used in.
    """
    if isinstance(parts, dict):
        items = list(parts.items())
    else:
        items = [(p["action_id"], p["channels"]) if isinstance(p, dict) else tuple(p) for p in parts]
    # `root` is accepted in the input and dropped: the eval's ground truth names it on the part that
    # owns the legs, and it is not a channel anything assigns. Whoever ends up with the legs gets it.
    items = [(aid, [c for c in chans if c in ANATOMICAL]) for aid, chans in items]
    if not items:
        raise ValueError("decompose needs at least one action")

    if base is None:
        legged = [aid for aid, chans in items if set(chans) & set(LEGS)]
        base = legged[0] if legged else items[0][0]
    base_channels = [c for aid, chans in items if aid == base for c in chans]
    overlays = [(aid, chans) for aid, chans in items if aid != base and chans]
    return arbitrate(base, overlays, kb, base_channels=base_channels)


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
    built, never from the model's prose about what it did.

    Two cases that are not what they look like:

    A BASE THAT CLAIMS NOTHING IS NOT A PART OF THE COMPOSITION. Naming a posture-setting action under
    a lone overlay is how a single overlay is played at all — something has to hold the rest of the
    body up. A base claiming no anatomical channel contributed no body part, so scoring that as a
    composition would penalise the model for being more correct than the ground truth, which calls
    `giving_pills` alone a full match.

    A GAZE BINDING IS A DECOMPOSITION even with one retrieved part. Binding the head to a scene target
    frees it to be SOLVED rather than retrieved, which is exactly the FK-retrieval vs IK-goal split —
    `dc-givepills-gaze` in the eval set is one action plus a freed head, and the ground truth calls it
    a decompose.
    """
    mixed_in = {aid for mix in assembly.shared for aid, _ in mix.shares}
    parts = [{"action_id": aid, "channels": chans}
             for aid, chans in assembly.layers if chans or aid in mixed_in]
    contributing = [p for p in parts
                    if p["action_id"] != assembly.base
                    or [c for c in p["channels"] if c != "root"]]
    freed_by_gaze = ["head"] if gaze_at else []

    if len(contributing) > 1 or freed_by_gaze:
        # A GAZE ON A BASE THAT CLAIMS NOTHING STILL HAS A PART. `contributing` drops a base holding
        # only the root, because a posture-holder under a lone overlay is not a second motion. When
        # the gaze is the ONLY reason this is a decomposition, dropping it leaves "decomposed into
        # nothing", which is not what happened: the action is there, with its head solved instead of
        # retrieved. So it is named.
        out = {"type": DECOMPOSE, "parts": contributing or parts,
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
