# 0008 — VLM proposes the SEMANTIC 5-tuple, consistency-check-gated, human-accepted

Status: Accepted (2026-06-24)

## Context
The v2 candidates (`agent/animation_knowledge_base/candidate/*.json`) have their MEASURED block filled and validated
8/8, but the per-channel SEMANTIC 5-tuple — `role` / `motion_type` / `contact` / `constraint` / `target`
— is seeded `null` (flagged `extraction.field_origin.semantic_pending`). This is the gating item before
candidate→accepted promotion (ADR 0007). ADR 0002 reserves SEMANTIC fields as *human-written,
screenshot-verified* and forbids the (measured) extractor from writing them — to keep the LLM
positioning/description unreliability this project is positioned against (Li et al. 2025) out of the KB.

Two facts reframe who should fill the 5-tuple:
1. The 5-tuple is **semantic / relevance labelling**, not numerics. `role ∈ {primary, stabilizer,
   support, free}` maps onto Li et al.'s BPQ relevance label (`free ≈ "Not Relevant"`); `motion_type` /
   `constraint` / `contact` are categorical judgements. These are the LLM/VLM *strength*. The numbers the
   model is bad at are already MEASURED by the Python extractor — the model never touches them.
2. SEMANTIC requires **screenshot verification** (ADR 0002). A VLM judging **rendered multi-angle frames**
   of the clip satisfies that requirement mechanically — which is the actual reason this must be a VLM and
   not a text-only LLM.

So filling the 5-tuple with a VLM is not a retreat from the retrieval-first thesis: it keeps the model on
semantic labelling (its strength) while a deterministic check enforces agreement with the measured facts.

> **Scope note (do not conflate concepts).** This is **offline data-authoring tooling** — a one-shot
> curation pass over the KB. It is **NOT** one of the Phase-2 `agent = model + harness` agents, and the
> consistency check below is **NOT** that framework's geometric *harness*. The `agent = model + harness`
> concept is reserved for the runtime multi-agent animation framework (intent / retrieval / assembly /
> landing). Here there is no agent and no runtime — just a VLM proposal validated by a data-contract check
> and confirmed by a human.

## Decision
A VLM may **propose** the SEMANTIC 5-tuple, under a three-part gate:

1. **Consistency check (automatic, deterministic).** Every proposed field is checked against the MEASURED
   block, the orthogonal `ik_goals`, and the human-locked `composability` by
   `validate_semantic_consistency()` in `validate_motionkb.py` (animation-agent repo). The invariants (a proposal must
   satisfy all): `role==free` ⟺ channel is `composability.free`; a channel with an `ik_goal` ⇒
   `constraint==must-reach` and `contact==object:<contact_object>`; `free` ⇒ `unconstrained`; `locked` ⇒
   constrained; `motion_type` agrees with the measured `state_label` (no `hold-static` on a dynamic
   channel, no `reach`/`manipulate`/`cyclic-locomotion` on a static one); `cyclic-locomotion` requires the
   `root` channel dynamic. A proposal that fails the consistency check is never recorded.
2. **Provenance.** A check-passing proposal is recorded as a new `field_origin` tier **`vlm_proposed`**
   (distinct from `semantic` = human, and from `semantic_pending` = still null). The pass is audited under
   `extraction.vlm_proposal` (`model`, `proposed_at`, `frames`, `render_views`, `consistency_validated`,
   `scope`, `status: awaiting_human_accept`). `verified_against_screenshots` stays **false** while any
   field is `vlm_proposed`.
3. **Human accept (promotion gate).** A human does a fast accept/correct pass over the proposal (far
   cheaper than authoring from scratch). Only on accept do the fields move `vlm_proposed → semantic`,
   `extraction.vlm_proposal.status → human_accepted`, `verified_by`/`verified_at` are set, and
   `verified_against_screenshots → true`. **candidate→accepted promotion requires zero `vlm_proposed`
   fields remaining.**

`target` may legitimately stay `null` (ADR 0007 / v2 spec assign it to the Phase-2 scene-grounding agent);
the consistency check does not force it. The measured extractor's contract is unchanged: it still writes MEASURED only
and never the 5-tuple (ADR 0002 holds).

## Consequences
+ The gating item gets unblocked at scale without re-introducing model-generated numerics — numbers stay
  MEASURED, semantics are model-proposed but consistency-checked and human-confirmed.
+ "Screenshot-verified" becomes a real, repeatable step (render → VLM), not a manual eyeball.
+ Provenance is explicit and auditable: every field is traceable to measured / human / vlm_proposed, and
  the promotion gate is a mechanical check (no `vlm_proposed` left).
+ The consistency check is reusable: it also catches bad *human* edits, not just VLM proposals.
- Adds a provenance tier and a render dependency (the VLM proposal step needs Unity MCP live to render
  frames). With Unity MCP down, only a text-only draft is possible, which does NOT satisfy the
  screenshot-verification clause and so cannot reach `vlm_proposed` — it stays `semantic_pending`.
- The consistency check encodes a specific semantic theory (e.g. `role==free ⟺ composably free`); if a future action
  needs to break one of these couplings, the invariant — not the data — is what must be revisited.

## Update — 2026-06-25: programmatic loop + gpt-5.5; existing 8 re-proposed

The propose → gate → accept loop is now a **program** (`render` / `propose` / `author` subcommands of
`extract.py` (animation-agent repo), with `vlm_openai.py` the stdlib OpenAI vision client and `propose.py` the loop):
`render` saves multi-angle frames (the avatar isolated on a ground plane so contact is visible), `propose`
sends those frames + the MEASURED facts + ik_goals/composability to **`gpt-5.5-2026-04-23`** (the VLM,
replacing the earlier `claude-opus-4-8`), which proposes the SEMANTIC fields **including `action_id`** — the
scope is widened from the per-channel 5-tuple to the identity/summary fields (action_id/display_name/intent/
tags) — gated by `validate_semantic_consistency` with a self-correction retry that feeds gate errors back;
`author` weak-gates `action_id` (slug + uniqueness, no MEASURED fact to check it against) and promotes
`candidate/<clip>.json → <action_id>.json`. All 8 accepted actions were re-proposed this way; the prior
claude-opus-4-8 proposal is preserved at `agent/motionkb_build/archive/authored_claude_backup/` (do not use / do not
delete). MEASURED was untouched (golden 8/8); the model id is recorded per file in
`extraction.vlm_proposal.model`. Notably gpt-5.5 named actions by FUNCTION from the frames, not by the asset
clip name (e.g. `nurse_give_meds → giving_pills`, `nurse_grab_aspirin → grab_bottle`).

## Update — 2026-06-25b: `controller_*` resolved, `composability` proposed/derived, human-accept made optional

The two remaining "hand-entered" inputs were folded into the program, and the human gate was relaxed from
mandatory to opt-in (a deliberate scope decision by the project owner — categorical labels are already
deterministically gated, and the human spot-check, while it once caught a real error, is no longer required):

- **`controller_state` / `controller_layer` / `trigger_param` — now RESOLVED, not authored.** New
  `resolve-controller` subcommand + `unity_sampler.build_resolve_controller_csharp` read the wiring straight
  from the `AnimatorController` via the typed `UnityEditor.Animations` API (state whose motion — directly or
  inside a BlendTree — is the clip; trigger = the parameter on a transition INTO that state; the layer's
  **default/resting state resolves to `trigger_param: null`**, not its return-transition's condition). `register`
  calls it best-effort. A clip not wired into any controller leaves all three **`null` (blank), by design** — so
  the schema was changed to make `controller_state`/`controller_layer` nullable. Verified live against the real
  `NurseAnimator.controller`: reproduces all 8 stored `controller_*` exactly. Provenance: `field_origin.resolved`.

- **`composability` — now VLM-proposed + program-derived in `propose`.** The VLM proposes the judgement calls
  (`base_or_overlay`, `posture`, `can_overlay_on`) and `mask_coverage`; the program **derives `locks`/`free`
  from the proposed per-channel roles** (`free ⟺ role==free`, the exact relation `validate_semantic_consistency`
  enforces — so the derivation reproduces the existing 8 by construction) and sets `seam_owner` to the fixed
  `{torso:base, root:base}` convention (true of every existing entry). A composability gate
  (`propose._composability_errors`, mirroring the validator's overlay-disjointness/posture invariants) joins the
  consistency gate in the self-correction loop. `mask_coverage` is VLM-proposed, **not** derived from locks: a
  neutral base (idle) drives the whole body yet locks nothing, so coverage ≠ ownership. Provenance:
  `field_origin.derived` for locks/free/seam_owner; the rest under `field_origin.vlm_proposed`.

- **Human accept is now OPTIONAL.** `propose` AUTO-PROMOTES by default (`_promote_candidate(human=False)`,
  provenance `vlm_accepted` — a new `extraction.vlm_proposal.status` value); `--stage` holds the candidate for
  review; `author` is the opt-in human pass that upgrades it to `human_accepted` and moves `vlm_proposed →
  semantic`. Which entries had human review is auditable from `vlm_proposal.status` + `verified_by`.

Scope note: the existing 8 accepted files were **not** re-derived — their hand-authored/VLM-proposed composability and
controller_* stand. This update changes only how NEW actions are produced. Gate green after the change:
validate 8/8, golden 8/8 (MEASURED untouched).
