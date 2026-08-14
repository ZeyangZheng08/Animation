# 0004 — mask+layer co-playback is valid only for disjoint locks

Status: Accepted (2026-06-18), amended (2026-08-12) — the restriction below has been lifted for a
stated subset. Read the amendment at the end before quoting the decision.

## Context
It is tempting to treat Unity avatar-mask + animation-layer co-playback as "composition". It is not:
it plays two clips simultaneously on different masked regions. That is only correct when the composed
parts do not interact.

## Decision
mask+layer is used only where the composed actions have DISJOINT `locks` (the README lock-disjointness
rule; e.g. a right-arm-only overlay on `walking`). The `composability` block encodes which parts each
action locks vs leaves free, and `can_overlay_on` is constrained so an overlay's `locks` never
intersect its base's `locks`. TRUE body-part synthesis of coupled actions (blend + IK + time-alignment,
and later an SMPL offline representation) is explicit Phase-2 work, baked offline — not faked at runtime.

## Consequences
+ The current runtime stays correct (no physically-inconsistent co-playback of coupled parts).
+ `composability` is the seed the Phase-2 assembler consumes.
- "Reusable composition" today is limited to the disjoint-locks subset; do not over-promise it.

## Amendment (2026-08-12) — a channel may have two sources, under stated conditions

The decision above was right about what it refused and wrong about why it had to. It reads as though
mask+layer is INCAPABLE of composing a shared channel. It is not: a masked layer at a fractional weight
interpolates. Measured on `nurse_avatar` before anything was built on it — `walking` under
`nurse_cpr_30`, masked to the right arm, sampled at weights 0, 0.5 and 1: the two ends are 69.14 degrees
apart at the right elbow and the midpoint sits 34.21 from one and 35.01 from the other, summing to 69.22,
a detour of 0.08 degrees off the geodesic. The left elbow, outside the mask, moved 0.00. The weight
blends and the mask still confines.

So the restriction is replaced by one that names the real constraint, decided per channel:

| both claimants free of an `object:` contact there | MIX, at shares from `ROLE_PRIORITY` normalised (primary/support = 0.6/0.4, primary/primary = 0.5/0.5) |
| exactly one of them has one | that one takes the channel whole |
| both, on different objects | still a conflict, reported by name |
| `root` | never mixed |

**A grip is what cannot be split, not a body part.** Half a hand on a patient's chest and half on a pill
bottle satisfies neither grip — the same argument that stops two hands being aimed at one anchor. This
is not a hedge: in this corpus every channel where two actions tie is `right_hand`, and every one of
those has both sides holding a different object, so the tie branch alone would never fire on anything
real. What the mix is FOR is the other contention — `cpr`/`giving_pills` under `walking`, where the legs
are `support` against `primary` and the bracing stance used to be discarded outright.

**The shares are the existing table, not a new number.** `ROLE_PRIORITY` already ranked these roles and
already decided this channel back when the higher rank simply took it. No KB field, no schema change, no
re-authoring. Winner-take-all is now the case where the loser's share is zero.

**Two limits, stated rather than hidden.** Entry phase is aligned per contributing clip (one phase per
clip: two legs at two phases is two legs stepping independently), and the clips then advance at their
own rates and DRIFT. Sustained alignment is time warping and is not implemented. And the honest scope of
"composition" is still bounded — this composes retrieved clips by weight, it does not solve contact or
collision, so `contact_hold` and `foot_skate` remain the things that can falsify a mix.

Verified by `probe_mix.py` end to end, and the weight is read back off the mixer in
`check_motion`'s `blend.mixed_channels` rather than echoed from the request: a plan that asked for a mix
and one that quietly resolved the channel to a single winner otherwise look identical and both play.

## Amendment (2026-08-13) — two grips detach an object rather than refusing the plan

The row above reading "both, on different objects → still a conflict" is withdrawn. The rest of the
table stands, including the part it was protecting: **a channel where either side grips is still never
mixed.** A hand is a shape, not an axis, and that argument has not weakened.

What was wrong was treating the refusal as forced. A grip is two things fused, and only one of them is
in the animation:

1. the hand's FK curves — joint rotations, and nothing in them holds anything;
2. the `ik_goal` aiming the wrist at a scene anchor, and the animation event that makes the prop
   visible — both of which live outside the clip and can simply not be applied.

So the contested channel goes whole to one side and the other keeps (1) while losing (2). The result is
a hand performing that action's motion with nothing in it, which is a real and describable motion rather
than a failure. Every detachment is reported as `dropped_grips` on the plan and as a `warn` gate; the
one thing that must never happen is the object quietly ceasing to exist while the reply still mentions it.

Who keeps the object, in order and all deterministic: **what the request named** (an object passed to
`carry` or `ik_bindings`), then `ROLE_PRIORITY`, then the base — the same tiebreak `Mix.owner` uses, and
for the same reason. Every tie in this corpus is primary-against-primary on `right_hand`, so in practice
the request decides and the base is the fallback.

**Still refused: the request naming both.** Carrying one object and binding a hand to the other leaves
nothing to decide that the caller has not decided twice, and choosing there would be doing subtraction on
their behalf. That case reports the two objects by name, as the whole rule used to.

**Why this was worth changing.** Six of the eight actions in this corpus grip with the right hand. As a
veto the rule refused 20 of the 56 ordered pairs outright; `probe_compose.py` now reports 24 pairs
composing into two sources that both drive something, against 10 before, with 14 of those 24 needing a
hand to let go. That count excludes the 18 degenerate pairs where one action ends up driving everything.

## Amendment (2026-08-13) — a layer may play part of a clip

Also implicit in the original decision, and also not forced: that the unit of composition is a WHOLE
clip on a channel. Under that reading, walking while doing chest compressions meant eighteen seconds of
arm under a one-second walk.

`agent/segments.py` measures, per action and channel, the frames it is moving in and — where the motion
repeats — one repetition, from the frozen `_raw` dumps. `MotionComposer.LayerSpec` gained
`ClipEndSeconds` and `LoopInWindow` to play it; unset, every layer behaves exactly as before. The window
is per ACTION rather than per channel, for the reason the entry phase already is: a clip is one
performance.

The measurement is worth stating because most of it came out negative. Trimming dead ends is nearly
worthless here — `check_pulse`, `giving_pills`, `cpr` and `walking` have no dead frames on any channel.
What is worth a great deal is the repeat, and only two clips have one: `cpr` every 18 frames of 540
(0.00° residual against a 2–4° typical spread) and `bvm`'s right hand every 89 of 180 (0.2° against 9.3).
`grab_bottle` loses the 6 frames it holds still at the end. The thresholds and the gaps they sit in are
in the module docstring rather than here, so they stay next to the code that would falsify them.

This is spatial-and-temporal selection of retrieved frames. It is still not synthesis: nothing is
re-timed, and segments are not re-ordered against each other.
