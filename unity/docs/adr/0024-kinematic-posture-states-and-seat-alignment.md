# 0024 — Kinematic posture states, and the arithmetic that lands a sit on the seat

Status: Accepted (2026-09-02). Every clip gains a posture SEGMENTATION over time and a measured
`root_travel`, in a derived sidecar rather than in the record; the planner works the standing point
backwards from that travel; and the executor gains a `seat_alignment` gate and a per-step
`apply_root_motion`, protocol v4 → **v5**. Supersedes the single-threshold `posture` bin of
[0022](0022-the-kb-describes-the-agent-decides.md) §10 as the runtime's posture source; extends
[0009](0009-check-before-you-play.md) with the gate and the protocol bump.

## Context

Two problems that look unrelated and are the same problem.

**One word per clip is not enough posture.** ADR 0022 kept `posture` as the one piece of the deleted
`composability` block the engine genuinely needs — a plan step carries a posture so the executor can
refuse to walk a seated character off a chair — and derived it by binning the root channel's
`mean_body_height` at 0.75. That worked over eight nursing clips, where nothing changed posture.
Over 2446 it does not, and the reason is in the name: `mx_Standing_To_Sitting_Transition` averages
to one number, and the average of standing and seated is neither. A clip that stands up is not one
word. The retrieval question the corpus makes possible — *find me a clip that starts standing and
ends seated* — cannot be asked of a single label at all.

**And a sit that was checked still missed the chair.** The generated standing↔seated descent drove
the hips onto the seat directly, so it always landed; the moment a RETRIEVED transition clip played
instead, it landed 0.45 m in front. The clip travels. `mx_Standing_To_Sitting_Transition` moves the
hips 0.446 m backwards over its 67 frames, because that is how a person sits: you step back and lower
yourself onto what is behind you. Played from wherever the walk stopped, it finishes exactly that far
short — and `seated_on_support`, which is a containment test against the seat's footprint, passed it,
because a chair is wide enough that the pelvis was still over the seat while the character was
visibly half a metre off.

Both need the same missing fact: **what the clip does over its own duration, measured from the frozen
dump, available before anything is played.**

## Decision

### 1. A posture segmentation per clip, in `derived/posture.json`

`build_posture.py` reads the frozen `raw` dumps and writes, per clip: a coarse state per frame, the
runs those frames fall into with their start and end times, the boundary events between runs, and the
clip's `root_travel`. `POSTURE_ALGORITHM_VERSION` is **2.0.0**; the sidecar records it, and
`KBIndex.load` refuses a sidecar that does not match or does not cover every accepted record.

**Four states: `standing`, `seated`, `floor`, `other`**, decided per frame in a fixed order. `floor`
is THIS PROJECT'S term for a floor-level kinematic state — lying, crawling, anything with the whole
body down near the ground; it is not a standard posture name and is not claimed as one. `other` is
the conservative fallback catching crouching, kneeling, airborne and mid-transition configurations.
Neither is an error state: the corpus splits 1072 dominantly `standing`, **1077 `other`**, 198
`floor` and 99 `seated`, and a clip that reads `other` has been described correctly rather than
skipped.

### 1a. What decides a seat is mechanics, not angles (2.0.0)

**Sitting, squatting, crouching and a wide fighting stance share their joint angles.** Thighs near
horizontal, a knee folded, the body low. Version 1.x tried to separate them with a shank clause and
the corpus disagreed: eleven clips read `standing → seated` and only two of them were sits. The other
nine were a fist fight, a hurdle landing, a spinning back kick, a duck behind cover, a slide tackle, a
kettlebell swing, a heavy pull, a snatch bottom-position, and a clip that ends folded down with its
feet behind its hips. On a live turn the agent searched for a way to sit a character on a chair —
`motion_search(transition={standing, seated})` — and was offered a spinning back kick.

**A tighter angle was the wrong repair.** What separates these postures is not the pose, it is where
the load goes:

- **Winter DA (1995)**, *Human balance and posture control during standing and walking*, Gait &
  Posture 3:193–214. An unsupported upright posture requires the whole-body **centre of mass** to
  project **inside the base of support** formed by the feet. A squat, a crouch and a stance are all
  unsupported — the feet carry the body, so the COM stays over them. **Sitting is the supported
  case**: the seat carries the load, and the mass projects *behind* the feet, which no unsupported
  posture can hold. The posterior boundary of the base of support is the **heel**, so the test is
  `> 0` and there is no margin: a margin would be a number with no source, and one was tried — at
  0.04 m it cut through the middle of `mx_Aim_Pistol_While_Sitting`, whose mass sits 0.024–0.061 m
  behind the heel across its own frames.
- **Hof AL, Gazendam MGJ, Sinke WE (2005)**, *The condition for dynamic stability*, J Biomech
  38:1–8. Balance is a condition on the **extrapolated** centre of mass, `XCOM = COM + v_COM / ω₀`
  with `ω₀ = √(g / l)`, not on the COM itself: a body in motion can hold its mass outside its base of
  support for as long as the momentum lasts, and an inverted-pendulum model says how long. `l` is
  `pendulum_length_m`, this avatar's upright hip height measured per build (0.8924 m), giving
  `1/ω₀ ≈ 0.30 s`; `v_COM` is a central difference over one frame at the dump's own frame rate,
  one-sided at the ends. **This is what separates a sit from a deceleration.** A still sit has `v = 0`
  and XCOM is the COM, so nothing about a held posture changes.
- **de Leva P (1996)**, *Adjustments to Zatsiorsky-Seluyanov's segment inertia parameters*, J Biomech
  29(9):1223–1230, Table 4 (male). Segment mass fractions and along-segment COM locations, which turn
  a set of root-local bone positions into a whole-body COM. `DE_LEVA_SEGMENTS` is that table; its
  mass fractions sum to 1.0000, which the tests assert.
- **ANSI/HFES 100-2007** (Human Factors Engineering of Computer Workstations) and **ISO 11226:2000**
  (static working postures): seated posture has a trunk-thigh included angle of about 90–120° (up to
  about 130° reclined) and a knee included angle of about 90–135°, and ISO 11226 stops calling a
  trunk upright past 60°.
- **Guerra BMV et al. (2020)**, Sensors 20(6):1602: normalise joint heights by the subject's standing
  height, because absolute positions are subject-dependent and the relations are not.
- **Liu et al. (2017)** keep the standing rule: a high carriage on thighs still under the body, with
  cut-offs the authors state are empirical.
- **Schenkman et al. (1990)**, Phys Ther 70(10):638–648: sit-to-stand runs four phases over roughly
  1.5–2 s, so `MIN_POSTURE_DURATION_S` at 0.3 s sits below one phase.

**Every raw quantity is still Unity's own.** The heights are `HumanPose.bodyPosition.y` and the
positions are `Transform.InverseTransformPoint`, taken in the same frame — the sampler builds the
`HumanPoseHandler` with the instance transform and reads the bones against that same transform, and
`root_pos` is constant per clip — so the two line up without conversion. **de Leva is not a second
source of data. It is the weighting applied to Unity's own bone positions** to get a COM whose
horizontal position can be trusted.

#### Why the horizontal centre of mass is not Unity's `bodyPosition`

`bodyPosition` is a good height and an unusable balance test, and this is the measurement that says
so. Both columns are the mean over each clip's last eight frames of **rear-heel z minus COM z**, in
the dump's own root frame; positive means the COM projects *behind* the feet, which is the seated
case.

| clip | `bodyPosition` | de Leva | difference along z |
|---|---:|---:|---:|
| `mx_Standing_To_Sitting_Transition` | +0.180 | +0.185 | +0.01 |
| `mx_Sitting_Still_In_A_Chair` | **+0.008** | +0.160 | +0.15 |
| `mx_Sitting_At_A_Computer_And_Typing` | +0.040 | +0.188 | +0.15 |
| `mx_Aim_Pistol_While_Sitting` | **−0.071** | +0.061 | +0.13 |
| `mx_Sitting_Down_Arms_Outside_Armrests` | +0.157 | +0.136 | −0.02 |
| `mx_Sitting_Cross_Legged` | +0.209 | +0.299 | +0.09 |
| `mx_Crouch_Cover_To_Cover` | **+0.092** | **−0.113** | −0.21 |
| `mx_Engaged_In_A_Fist_Fight_2` | −0.680 | −0.460 | +0.22 |
| `mx_Jumping_Over_Obstacle_Getting_Ready_To_Fight` | −0.582 | −0.495 | +0.09 |
| `mx_Spinning_Back_Kick_Advancing` | −1.675 | −0.103 | +1.57 |

Read the bold rows. By `bodyPosition`, sitting still in a chair puts the mass 8 mm behind the rear
heel — inside the noise — and aiming a pistol while seated puts it 71 mm *in front* of the feet,
which is a stance. Crouching behind cover puts it 92 mm *behind* them, which is a sit. The two
quantities disagree by up to 0.22 m and in both directions, so a rule written on `bodyPosition`
horizontally would classify a crouch as sitting and a sit as a crouch.

**Vertically there is nothing to choose between them.** `bodyPosition.y` reads 0.967 upright against
the de Leva COM's 0.935, and 0.64–0.69 seated against 0.61–0.66 — within 0.03 everywhere. So the
height stays Unity's, normalised by `upright_body_height` measured on `mx_Standing_Idle`, and only
the balance test is de Leva's. Both halves of that claim are asserted in `tests/test_posture.py`.

**The threshold table, each value with its source or its measurement.**

| Constant | Value | Where it comes from |
|---|---|---|
| `H_FLOOR` | 0.45 | fraction of upright COM height; unchanged from 1.x, still cuts the corpus's lying and crawling clips |
| `H_LOW` | 0.70 | fraction; a horizontal trunk below this is lying rather than bending over |
| `H_SEATED` / `H_STANDING` | 0.80 | fraction; measured seated clips read 0.66–0.70, standing ones ≥ 0.86 |
| `H_AIRBORNE` | 1.15 | fraction; above it the body has left the ground |
| `upright_body_height` | 0.9666 | **measured per build** — mean `HumanPose.bodyPosition.y` over `mx_Standing_Idle`, stored in `_meta`. Guerra's normalisation; every `H_*` above is a fraction of it |
| `THETA_TRUNK_HORIZONTAL` | 60° | ISO 11226:2000 — trunk inclination past this is not upright |
| `THETA_THIGH_UPRIGHT` | 35° | Liu et al. (2017), empirical, for the standing rule |
| `HIP_SEATED` | 60–130° | ANSI/HFES 100-2007 + ISO 11226 give 90–130°; widened downward by the offset **measured** on this avatar, where upright chair sitting reads hip 77–93° because the chest vector leans forward of the trunk line and the thighs slope up on a seat of this height |
| `KNEE_SEATED` | 50–140° | the standards' 90–135°, widened the same way; measured chair sitting reads knee 64–89° |
| `COM_BEHIND_BOS_M` | **0.0 m** | Winter's condition is *outside the base of support*, and the posterior boundary of that base is the heel. No margin, because a margin would have no source |
| `HEEL_BEHIND_ANKLE_M` | 0.07 m | the **calibration avatar's ankle-to-heel offset** — a property of the rig every clip was retargeted to, stated here because the dumps carry no heel marker. Without it the base of support ends at the ankle and every standing frame reads as balanced on its toes |
| `pendulum_length_m` | 0.8924 m | **measured per build** — upright hip height over `mx_Standing_Idle`, giving `ω₀ = √(9.81 / l) = 3.316` rad/s for Hof's XCOM |
| `GRAVITY_M_S2` | 9.81 | standard gravity, for the same |
| `MIN_POSTURE_DURATION_S` | 0.3 s | Schenkman et al. (1990) — below one phase of a 1.5–2 s sit-to-stand |

Heights are fractions and are scale-free. `COM_BEHIND_BOS_M` and `HEEL_BEHIND_ANKLE_M` are **metres,
root-local, on the single calibration avatar every clip in this corpus was retargeted to** — that is
what makes 2446 clips comparable through them and what makes them meaningless outside them. Neither
is a distance in anybody's scene, and `param_units` in the sidecar says so.

**The sagittal test is what the rule uses; the 2-D one is reported.** `bos_behind` is the XCOM against
the rearmost heel — the axis a seat is on. `com_outside_bos` is the signed distance from the XCOM's
ground projection to the convex hull of the six foot points, negative inside, which is the honest
general statement of Winter's condition. It is computed and carried as a feature but not ruled on: a
lunge puts the mass outside its base of support *sideways* without anybody sitting down.

**The intermediate `other` inside a sit-down is correct, and it has a name.** Schenkman et al. (1990)
call it the momentum-transfer phase: she has left her feet and the seat is not carrying her yet, so
she is neither `standing` nor `seated`, which is exactly what `other` is defined as here. The audit's
three transition cases therefore assert a **crossing** rather than a boundary event — one crossing
may be spent over two boundaries, direct or through `other`, and both are the same fact about the
clip. Nothing at runtime reads boundary events: `posture_of` reads the dominant posture and
`_crosses`, `_seating_via`, `_rising_via` and `posture_span_of` all read the two ENDS.
`posture_transitions` is projected to the model by `motion_timing` and is read by nothing that
decides anything.

**A run shorter than `MIN_POSTURE_DURATION_S` (0.3 s) is not a segment.** Per-frame states flicker at
the boundaries of a dynamic movement; a segmentation that reported the flicker would make every
sit-down a dozen states long.

### 2. `audit_posture.py`, and what it is not

Thirty-one clips whose content is not in doubt, with hand-written expectations in
`motionkb_build/posture_audit.json`, checked against the rules. **It passes 31 / 31.** It is a sanity
audit, not an evaluation: thirty-one clips cannot measure an accuracy and it prints none. What
it checks is whether the rules make the mistakes their shape makes likely — a crouch or a kneel read
as sitting, a deep bend read as lying — and whether the segmentation honours its own minimum-duration
claim. Expectations are written as constraints (`start standing|other, end floor`) rather than as
exact sequences, so the audit fails on a wrong ANSWER rather than on a different but correct
segmentation.

`build_posture.py --check` recomputes the whole sidecar from the frozen dumps and compares, which
catches a hand-edited sidecar as well as a stale one. It is the fifth gate in `check_kb.sh`, and it
is a gate rather than a convenience because the service refuses to start without a current sidecar.

### 3. `root_travel`, and where the standing point comes from

Per clip: `{dx, dz, distance_m, yaw_deg}` — where the clip leaves the body relative to where it
picked it up, in the clip's own frame.

It is read off the root-local `Hips`, not off `root_pos`, because the corpus was sampled while every
clip was imported with `lockRootPositionXZ = true`: the travel sits in the pose, `root_pos` is
constant in all 2446 dumps, and `root_fwd` is constant too, so the yaw comes from the Hips' own
rotation. The importer has since been changed so new imports put the travel in root motion instead —
which is what stops a walk sliding the character across the floor — but **the dumps were not
resampled and do not need to be**. A clip's travel is a fact about the clip; which column holds it is
a fact about an import setting, and this was verified against a resample rather than assumed:
`bone_rot` matches bit for bit either way.

From that, one pure function, `scene.standing_point_for(seat_xz, approach_xz, travel)`:

    facing = away from the seat, along the line towards where she is coming from
    stand  = seat - R(facing) * travel

She ends where the seat is. The clip moves her by its own measured travel rotated into whatever
direction she faces, so she starts at the seat minus that. The facing is not free — sitting
down means putting your back to the seat. The walk's destination becomes that point **with no stop
tolerance to spend**: `right_at_it` is 0.08 m and that was the entire error budget, so the seating
path passes 0.0 explicitly rather than accepting the default.

It is a pure function, out in the module rather than inside the tool, because everything it needs is
three pairs of numbers and it can therefore be checked against a chair whose position somebody wrote
down.

### 4. `apply_root_motion`, per step — protocol v5

A plan step now carries `apply_root_motion`. The clips are otherwise in-place and the navigation
agent moves the transform; a transition clip that travels is the exception, and it is an exception
per STEP rather than per character or per plan.

**The dump records the displacement the clip contains; the composer decides, per step, whether to
apply it.** Those are two different decisions and the split is the point: a walk's travel is
measured and deliberately discarded, because the navigation agent is already moving her and applying
both would double it; a sit-down's travel is measured and applied, because nothing else is moving
her. The same field on the same sidecar serves both.

On the Unity side the root motion is read in one place — a `RootMotionTap : IAnimationJob` whose
`ProcessRootMotion` accumulates `stream.velocity * stream.deltaTime` into a `NativeArray<float>` —
because `ProcessRootMotion` is called on both the realtime path and the manual `Evaluate` path the
pre-execution check runs on, and a validator that measured a different motion from the one that
plays is worth nothing.

Two settings make that work and each was measured rather than reasoned to. `Animator.applyRootMotion`
is set **true** once in `Awake`: it is what makes Unity COMPUTE root motion at all, and the first
implementation read exact zeroes — 1386 calls, 0 non-zero — with it left false. An empty
`OnAnimatorMove()` then stops Unity APPLYING what it computed, leaving `ConsumeRootMotion` the only
thing that moves the transform on either path.

Consumption is armed for **any** `apply_root_motion` step whose presence exceeds one half, which is
when the stream is mostly that clip. Below it the pose the tap reads is still mainly the outgoing
one, and consuming there would apply the WALK's root motion — exactly what a locomotion step must
never do. So the crossfade is discarded on purpose: measured on walk → sit → settle, **0.3765 m of
the sit-down's own 0.4460 m** reaches the transform, and the missing 0.07 m is its 0.2 s blend-in.

`PROTOCOL_VERSION` goes to 5 across the three sites it always takes: `agent/protocol.py` is the
authority, `Protocol.cs` mirrors it, and `terminal.py` — standard-library-only by design, so it
cannot import either — reads the constant out of the source.

### 5. `seat_alignment`, fatal at 0.05 m

The distance from the pelvis to the MIDDLE of the seat, judged at 0.05 m, fatal.

`seated_on_support` is a containment test against the footprint and cannot see this failure:
measured, it read 0.0000 and passed while the character was visibly 0.3–0.5 m off the chair. Five
centimetres is not a calibrated number, it is a geometric one — about the slack between a pelvis and
the middle of a seat it is genuinely sitting on, and the scale the plan places her at, since
`standing_point_for` lands the hips on the seat centre exactly. Anything past a few centimetres means
something moved that was not supposed to.

**A support gate has to be told when to start looking.** The first version armed 0.5 s into the plan,
while she was still walking, and reported 0.4667 m — a true statement about where she was and a
useless one about whether the sit worked. `expect_support` now carries `judgeable_at_s`, the time the
descent finishes, and both the visible character and the hidden duplicate judge from there.

## Consequences

**Measured, end to end, on the chair in `EmergencyRoom`:** `seated_on_support` 0.0000 m,
`seat_alignment` 0.0809 m → **pass** once the stop tolerance was spent, `ground_penetration`,
`foot_skate`, `pelvis_above_surface` (0.1034 m, reported not judged) and `sat_through_support` all
green — six checks over 7.3 s of animation in 439 samples, then a 2.50 m walk, then the commit. The
round trip back out through `mx_Sitting_To_Standing_2` leaves her hips 0.023 m from where she sat
down. `smoke_validate.py` is that run and asserts the applied travel against the clip's own.

**The vertical half is NOT corrected, and this is a known gap.** The clip lowers the hips by whatever
its own performance lowered them by, and the chair in `EmergencyRoom` happens to match —
`pelvis_above_surface` reads 0.1034 m and `sat_through_support` passes. A seat at a different height
needs the offset applied. Until it is, a taller or lower support is a hover or a sink that no gate
catches, because both gates measure against the surface she was placed relative to.
`pelvis_above_surface` is reported rather than judged for the same reason it is unfixed: how far a
pelvis should
sit above a seat depends on the avatar, and one measurement is not a calibration.

**The posture sidecar is a hard start-up dependency, deliberately.** There is no fallback. A service
that fell back to calling everything `standing` would switch off the refusal that stops it walking a
seated character off a chair, and it would do so silently.

**Posture is now a retrieval key.** `motion_search(transition={from_posture, to_posture})` finds
clips that START in one state and END in another, `motion_transition(via=[...])` costs each candidate
at both joins and ranks them by geometry, and naming the winner in `then[].via` plays it. **510 of
the 2446 end in a different state from the one they start in** — and since 2.0.0 exactly **2** of
them run standing → seated (`mx_Standing_To_Sitting_Transition` and
`mx_Sitting_Down_Arms_Outside_Armrests`) with **1** coming back (`mx_Sitting_To_Standing_2`). That is
a much smaller set than 1.x reported, and it is the honest one: the other nine were stances and
squats. A posture change is still a SEARCH, and the result says so by carrying no
`generated_transitions`. Generation survives as the fallback for the standing↔seated pair when the
search comes back empty, and the reply names it as the alternative it is.

**Two claims are reported separately and never merged.** `seated` in the sidecar means a seated-like
BODY CONFIGURATION, measured from a dump of a clip on an empty floor. Whether the character is
sitting ON something is a fact about a scene, and only the Unity executor can decide it —
`seated_on_support`, `seat_alignment`, contact, penetration. `bones` is root-local, so a foot's
height in a dump says nothing about where the floor is, and nothing in `build_posture.py` pretends
otherwise.

**Version 1.1.0 moved for the file's shape, not for a rule.** Adding `root_travel` changed no
threshold, no feature and no ordering; every one of the audit's twenty-one expectations passes
unaltered. The version moved because a consumer reading the new field needs a way to tell whether it
is there.

**Version 2.0.0 is a major bump because the FEATURES changed, not only the numbers.** The shank angle
is gone; a de Leva COM against the base of support and a trunk-thigh included angle are in. The
height is still `body_pos`, now normalised rather than absolute. `_meta` gains `upright_body_height`
and `upright_reference`. The sidecar schema and the four states are unchanged, and so is every
runtime consumer: `posture_of`, `posture_span_of` and `posture_detail` read the same fields.

**What it cost, measured against 1.1.0 over the whole corpus.** Dominantly-seated clips fall from 161
to **99**, `standing` 1089 → 1072, `floor` 211 → 198, `other` 985 → 1077. Most of what moved became
`other`, the conservative fallback. `standing → seated` is now **2** clips and `seated → standing`
**1**. Clips that gained a seated reading include `mx_Reclined_Left_Leg_Crossed` and
`mx_Reclined_Left_Leg_Crossed_Hands_Behind_Head`, which the 1.x shank clause excluded and which are
plainly people sitting in chairs.

**The straddled chair is an accepted `other`.** `mx_Sitting_Backwards_On_Chair_Hands_Relaxed` sits
astride a chair with both feet planted either side of it, and its COM is 0.023 m in FRONT of the
heels. Under Winter's definition the feet are carrying the body, so it reads `other`, and that is the
definition working rather than failing. It is in the audit as an accepted case so the answer is a
decision on the record instead of a surprise later.

**What the velocity term costs and buys, measured.** `mx_Change_Direction_180_Degrees_While_Running`
read dominantly `seated` under the static condition — planting hard to reverse, the runner leans back
and her COM goes behind the planted foot. With XCOM her momentum carries the extrapolated point
1.32 m *ahead* of the heels and she reads dominantly `other`, which is right. Going the other way,
four clips became seated that were not: `mx_Rifle_Crouched_Walk_Backward`, `…_Backward_Left`,
`…_Backward_Right` and `…_Walk_Left`, all crouched rifle walks at a mean COM speed of 1.98 m/s.
Travelling backwards pushes XCOM further behind the heels, and Hof's condition says truthfully that
they are not in balance — which walking never is. None of the four crosses `standing → seated`, so
none can be offered as a way to sit down; the cost is that `motion_search(posture="seated")` can
return them. One false positive traded for four, and the trade was worth taking for the other half of
it: with the margin at zero, `mx_Aim_Pistol_While_Sitting` is seated for all 163 of its frames.

**The audit passes 31 / 31.** The nine deep stances and the straddled chair were added as new cases
and all ten hold. The three transition cases were rewritten to assert a crossing rather than a
boundary event, for the reason given above. `mx_Aim_Pistol_While_Sitting` needed no change once the
margin went to zero — it is seated for all 163 of its frames.
