# 0022 — The knowledge base describes; the agent decides

Status: Accepted (2026-08-26). Deletes every field in a MotionKB record that encoded a composition
decision, leaving measured kinematics and two kinds of description. Schema `motionkb/v3` →
**`motionkb/v4`**, `extractor_version` 3.1.0 → **4.0.0**, `metric_formula_version` unchanged at
**v3.0.0** — no number moved. All 2454 records rewritten in place. Supersedes
[0004](0004-mask-layer-disjoint-only.md) entirely and the SEMANTIC-5-tuple half of
[0008](0008-vlm-proposed-authored-fields.md); amends [0002](0002-measured-vs-authored-separation.md),
[0007](0007-v2-body-part-split.md) and [0014](0014-corpus-enters-measured-only.md).

## Context

A v3 record carried, per anatomical channel, a five-field tuple — `role`, `motion_type`, `contact`,
`constraint`, `target` — and, at the top level, `mask_coverage`, `ik_goals` and a `composability`
block with `locks`, `free`, `can_overlay_on`, `base_or_overlay`, `posture` and `seam_owner`. Together
they were the SEMANTIC half ADR 0008 built, and the input the assembler partitioned by.

**Every one of them is a claim about a COMBINATION, stored on a description of one clip.**

- `role` says which body parts matter. `walking`'s arms are `stabilizer` — incidental balance,
  claimed by nobody, free for a carry to override. That is right for "walk while carrying a bottle"
  and wrong for "walk swinging your arms", and the clip is identical in both. The label is not a
  property of the walk; it is a property of the walk *used a particular way*.
- `contact` says what a hand holds. `typing` records both hands on `object:keyboard`. Whether there
  is a keyboard, and which one, is a fact about the room she is standing in.
- `ik_goals` names an effector and an object and leaves `target` null on every record in the store,
  because the actual anchor is engine-specific and scene-specific. The field was two thirds of a
  decision with the deciding third permanently absent.
- `composability.can_overlay_on` is an enumerated whitelist of what may be layered onto what — the
  pre-enumerated interaction template this project's claim rejects. It was already unread by both
  the assembler and the retrieval index, and both files said why: taken literally it rejects both
  decompose cases in the eval set, because `grab_bottle.can_overlay_on == ["idle"]` excludes walking.
- `locks`/`free` was derived from `role == "free"`, so it meant "this channel is busy", not "this
  channel may not be overridden" — a distinction the name actively obscured, and one that had to be
  re-explained in three separate module docstrings.
- `base_or_overlay` says whether a clip is foundational or grafted on. `walking` is a base under a
  carry and an overlay under `cpr`.

The 2026-07-01 memo already recorded the composability defect and proposed fixing it consumer-side.
The consumer-side fix worked, and what it demonstrated is the argument here: `assemble.py`,
`kbindex.py` and `tools/kb.py` each grew a paragraph explaining which contract fields they deliberately
did not read. A contract three consumers agree to ignore in writing is not a contract.

The alternative to deleting `role` was reconstructing it from what survives. It cannot be done, and
this was checked rather than assumed. Across the eight accepted actions, `walking.left_arm` is
`stabilizer` at motion_magnitude 0.165, `cpr.left_arm` is `support` at 0.114, and
`typing.left_arm` is `primary` at 0.125 — the ordering is not even monotone. An argmax-magnitude
contention rule, the most natural substitute, gets 3 of 8 channels wrong on `dc-walk-carry`, and all
three failures are the same shape: a channel that moves and that nobody should claim. That is the
`stabilizer`/`free` distinction, and it is a judgement about what the motion is FOR.

Which is exactly what the runtime agent is for. It has the request, the scene graph and the
character's state; the extractor has one clip on an empty floor.

## Decision

**A record answers two questions and no others: what does this action look like, and how does each
body part move. Everything else is decided at runtime.**

1. **Deleted from the top level**: `display_name`, `tags`, `mask_coverage`, `ik_goals`, and the whole
   `composability` block.
2. **Deleted from all 8 anatomical channels**: `role`, `motion_type`, `contact`, `constraint`,
   `target`. The root channel already carried none of them and still carries none.
3. **`overall_intent` → `action_description`.** A record describes an appearance. An intent is what
   the agent brings to it, and the old name invited a reader to treat the clip as knowing why it was
   being played.
4. **`motion_description` is the only per-channel semantic field**, and the pair
   (`action_description`, `channels.*.motion_description`) is the whole of `field_origin.semantic`.
   Where they are null — the 2446 measured-but-undescribed corpus records — they are listed under
   `semantic_pending` instead.
5. **The KINEMATIC half is untouched.** `state_label`, `motion_magnitude`, `raw_measurement`,
   `mean_pose`, the root's `mean_body_height` / `mean_body_tilt_deg`, the frozen `raw` dumps and
   `derived/` are all bit-identical. `metric_formula_version` stays v3.0.0 because no formula ran.
6. **`schema_version` becomes `motionkb/v4`**, with the top level and both channel definitions
   `additionalProperties: false`, so a leftover v3 field fails validation rather than passing
   unnoticed. `motionkb.v3.schema.json` stays on disk as the historical contract.
7. **The plan carries the partition.** `plan_motion`'s `overlays` are now
   `{action_id, channels[]}` — the agent says which body parts each overlay drives — with an optional
   `base_channels` for parts the base reserves. Anything nobody names is `free_channels`, and the
   base plays layer 0 full-body underneath regardless, which is what it always did.
8. **A contested channel is mixed at equal shares.** v3 normalised `ROLE_PRIORITY` to get 0.6/0.4 for
   primary-against-support; that number was defensible only as long as the ranking it normalised
   existed. Half each is what is left, and it is the honest reading of "the agent asked for both of
   these here". The rule that a numeric never comes from the model is unchanged and still structural:
   every leaf in the plan schema is a string or a boolean.
9. **A pinned channel may not be mixed.** Where the plan attaches an effector to a scene object
   (`carry`, `ik_bindings`, or a gaze-bound head) and two actions are both given that channel, the
   plan is refused by name. Half a hand shaped for a bottle and half for a chest grips neither.
10. **`posture` is derived, not deleted.** It is the one piece of `composability` the engine needs —
    every plan step carries a posture so the executor can refuse to walk a seated character off a
    chair — and it was never a judgement. `kbindex.posture_of` bins the root's measured
    `mean_body_height` at **0.75**: the seated action reads 0.647, the lowest standing action (`bvm`,
    leaning over a patient) reads 0.859, and 0.75 is the middle of that gap. `mean_body_tilt_deg` is
    deliberately NOT consulted — it measures forward lean, and `cpr` (44.6°) and `bvm` (39.3°) lean
    harder than the seated clip (8.7°).

### What the VLM proposes now

Three things: `action_id`, `action_description`, and the eight `motion_description`s. The
constrained-vocabulary apparatus goes with the vocabularies — no enums to violate, no cross-field
rules to satisfy, and the self-correction retry loop's one remaining job is a proposal that skipped a
channel. `composability.locks`/`free`/`seam_owner` and `ik_goals` are no longer derived from the
proposal, because they no longer exist. The eight existing records' descriptions were carried through
the migration **verbatim**; no proposal was re-run, and none of the interpretive run-to-run variance
that lives in a VLM pass entered the store on this change.

### What the validator checks now

Schema conformance, the channel vocabulary against `engine_mask_map.json`, and completeness: an
accepted record has a non-blank `action_description` and a non-blank `motion_description` on each of
the eight anatomical channels. `validate_semantic_consistency`, the cross-file overlay
lock-disjointness pass, the posture-compatibility check and both soft warnings are deleted rather
than reinterpreted. Each read a field that is gone, and there is no contradiction to find between two
sentences of prose and a measured number. The gate certifies WELL-FORMED and COMPLETE now, where it
used to certify SELF-CONSISTENT; it never certified correct, and still does not.

## Consequences

**Keyword retrieval lost four of its six term sources and scored the same.** The searchable document
was `tags`×3 + `display_name`×2 + `overall_intent` + contact objects×2 + per-channel descriptions +
`motion_type`; it is now `action_description`×3 — the tags' old weight, on the field that inherited
their job — plus `action_id` and the eight `motion_description`s. The baseline arm of the retrieval
eval scores **7/12**, which is what it scored before. It was worth checking rather than assuming: the
one `fm-*` case it fails, `fm-giving-pills` ("hands the patient the oral medication to take" →
`grab_bottle`), looks exactly like the cost of losing the curated tags `medication` / `pills` /
`administer_meds`, and it is not — the v3 document rebuilt verbatim over the v3 records out of git
fails the same case to the same wrong answer. No weight was tuned to reach that; fitting the index to
these twelve cases is what the `no_match` cases exist to catch.

**The decompose cases changed what they measure.** They scored a deterministic rule over the `role`
table; they now score whether the MODEL partitions the body correctly. That is harder and more
honest — it is the decision the system claims the agent makes — but a decompose score before and
after this change is not the same measurement, and comparing them would be a category error.

**Two capabilities changed hands rather than disappearing.** The facing rule ("she sits facing the
desk, not the chair") read `channels.*.contact` and now reads what the plan binds a hand to, falling
back to `gaze_at`. Two-handed grounding (`typing` landing 0.000 m on the laptop) read the same field
and now triggers when the agent binds ONE hand to an object whose registry entry declares
`two_handed_anchors` — the object says where both hands go, which is the same rule as before, sourced
from the side that actually knows.

**`dropped_grips` is gone with the thing it reported.** Two v3 actions each declared a contact, one
hand could serve only one of them, and the loser kept its motion without its object. A v4 record
declares nothing, so there is no second grip to lose: a hand holds what the plan says it holds, once.

**Records shrank 8.4%** — 206395 lines deleted against 14732 added across the store. The whole-store
diff reconciles exactly: 6 changed lines per candidate record (`schema_version`,
`action_description`, `extractor_version`, `extracted_at`, and the two `field_origin` entries) and 7
per accepted record (plus the narrowed `vlm_proposal.scope`).

**`support_channels` and `payload_window` lost their `contact` input.** Both are transition-timing
rules and both were re-expressed on measurement: a standing clip's weight is on its legs (which
reproduces the v3 answer on every record in the store), and a non-loop clip whose hands move is a
clip doing something with them. A hand on the ground — a push-up, a crawl — is no longer detectable
and is deliberately not guessed at; the corpus has no such clip, and inventing a threshold for one
would be the move ADR 0021 exists to prevent.

**The rewrite exposed four latent bugs in the runtime, all now fixed.** `plan_motion` raised
`NameError` on every successful plan, from a leftover read of a result the refactor had renamed;
the actionable refusal for an overlay naming no channels was unreachable because normalisation ran
outside its `try`; `_binding_due` had silently dropped the generated-descent offset, so a hand
binding on a walk-then-sit engaged while the descent was still running; and the two-handed pairing
searched the scene with an `{"id": …}` predicate the engine has no clause for, which falls through
its blank-clause guard as NO filter and returns the whole registry — so it had never fired. Three of
the four predate this change and were invisible because nothing exercised them by that path.

**Gates, after the rewrite:** validate 2454/2454 against `motionkb.v4.schema.json`, golden
re-extraction 8/8 reproducing KINEMATIC from frozen `raw`, manifest in sync, guid resolution 8/8,
agent-repo suite 350/350, zero occurrences of any deleted key anywhere in `actions/`, the eight
records' descriptions byte-identical to their v3 text, and every kinematic field verified equal
record by record against the pre-migration store.
