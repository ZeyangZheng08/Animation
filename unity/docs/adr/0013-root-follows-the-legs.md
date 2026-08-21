# 0013 — The root channel follows the legs, not its own `state_label`

Status: Accepted (2026-08-20). A consequence of ADR 0011, which redefined the root signal; it changes
how `agent/assemble.py` partitions the root channel, and no KB record or schema field.

## Context

`arbitrate()` decided the root channel with a rule of its own, outside the role-priority partition
that decides the other eight:

```python
dynamic_root = [aid for aid in parts if channels_of[aid]["root"]["state"] == "dynamic"]
if len(dynamic_root) > 1:
    conflicts.append(Conflict("root", dynamic_root, "dynamic"))
```

Its docstring gave the reason: *"ROOT goes to the single part whose root channel is `dynamic`. In this
corpus only `walking` qualifies (magnitude 0.850; the next is 0.200 and labelled static)."*

That was true while the root signal was `max(gait, trans, heading)` in metres, where the foot-lift
term made a walk read an order of magnitude above everything else. ADR 0011 replaced it with
`max(trans, vert, heading)` from `HumanPose.bodyPosition` / `bodyRotation`, which counts **turning**
and has no gait term. Measured on the eight accepted records:

| action | root | magnitude | trans (m) | vert (m) | yaw (deg) |
| --- | --- | --- | --- | --- | --- |
| `giving_pills` | dynamic | **0.0687** | 0.034 | 0.089 | 8.23 |
| `check_pulse` | dynamic | 0.0528 | 0.049 | 0.069 | 3.14 |
| `grab_bottle` | dynamic | 0.0467 | 0.007 | 0.007 | 6.64 |
| `walking` | dynamic | **0.0382** | 0.009 | 0.050 | 1.04 |
| `typing` | static | 0.0134 | — | — | — |
| `cpr` | static | 0.0131 | — | — | — |
| `idle` | static | 0.0038 | — | — | — |
| `bvm` | static | 0.0015 | — | — | — |

Four of eight are dynamic, so the "single part" premise is simply false: `dc-walk-carry` — walk while
carrying the bottle — became `Conflict(root: walking and grab_bottle both dynamic)` and was refused.
17 tests failed on it.

Ranking by magnitude instead does not rescue the rule; it inverts it. **`walking` reads the LOWEST of
the four.** The store's walk is an in-place walk: the body does not travel, only the step bounce
rises, and it barely turns. `giving_pills` reads nearly twice as high on 8.2° of yaw. Preferring the
larger number hands locomotion to a clip that turns slightly while handing over pills.

## Decision

The root goes to whichever part owns the **leg channels**.

```python
leg_owners = sorted({claims[c] for c in LEGS if c in claims})
if len(leg_owners) > 1:
    conflicts.append(Conflict("root", leg_owners, "driving one leg each"))
    root_owner = None
else:
    root_owner = leg_owners[0] if leg_owners else base_id
```

Where a body goes is decided by what its legs did. Reading it off `claims` means the root inherits
every step of the partition already made above it — including a mix, whose owner takes the root the
same way it takes the channel — instead of being decided by a second, parallel rule.

ADR 0011 had already made this move once, in the validator: `cyclic-locomotion` gates on a leg
channel being dynamic rather than on the root, *"the store's own `walking` is an in-place walk whose
legs step and whose body does not move, and gating on the root would reject the clearest example of
the label."* The same sentence applies here; this is that decision reaching the assembler.

**Root is still never mixed.** Two root motions added together are not a motion. The channel goes
whole to one part or to nobody — a mixed leg channel resolves to its owner first, and the root
follows that single owner.

**Unclaimed legs leave the root with the base.** `claims` is populated only where somebody claimed a
channel, so legs that are `free` on every part mean no part is driving the lower body, and the base —
which plays full-body on layer 0 regardless — keeps it.

**Two parts each driving one leg is a real ambiguity** and stays a conflict. It cannot arise in this
corpus, where every action's two legs carry the same role, but the rule should not resolve by picking
whichever sorts first.

## Consequences

**The suite goes green: 293 passed, 0 failed**, from 17 failures. The four KB gates are unaffected —
this changes no record.

**The rule is decidable for every pairing in the corpus, and by inspection.** An overlay claims only
its `primary` channels, and `walking` is the only action whose legs are `primary`:

| leg roles | `idle` | `walking` | `typing` | `cpr` | `bvm` | `check_pulse` | `giving_pills` | `grab_bottle` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| | free | **primary** | support | support | free | free | support | free |

So: a pairing containing `walking` gives it the root, whether it is the base or the overlay, and
whether or not the other part also claims the legs (as `cpr` and `giving_pills` do — those contest,
mix, and `walking` wins the mix on role priority). Any other pairing has at most the base claiming
the legs, and the root stays with the base.

**`state_label` on the root is no longer consulted by the assembler.** It remains a measured fact and
is still reported, still validated, and still what a retrieval query can filter on. What it stopped
being is an ownership rule — the same demotion ADR 0011 gave it in the validator.

## Alternatives considered

**Rank the dynamic roots by `motion_magnitude`.** The smallest change, and it keeps the root's own
measurement in charge. Rejected on the numbers above: it awards the root to `giving_pills` over
`walking`, which is the wrong answer in the one case the corpus has a right answer for.

**Add a `travels` boolean to the contract.** An honest field — "does this clip's body go somewhere" —
that the assembler could read directly. Rejected as a schema change to re-derive something the legs
already say, and it would need authoring or a second threshold on the same numbers that just proved
they do not separate an in-place walk from a small turn. It is also the wrong question: an in-place
walk does not travel and still owns the root, because the runtime converts it — `Locomotion.cs`
drives a NavMeshAgent while an in-place clip plays.

**Restore a foot-gait term to the root signal.** It would make `walking` dominant again and the old
rule would work unchanged. Rejected by ADR 0011 already, and for a reason that has not changed: it
conflates "the legs are stepping" with "the body went somewhere", and those are separate facts that
separate consumers need — the first for retrieval, the second for scene landing.
