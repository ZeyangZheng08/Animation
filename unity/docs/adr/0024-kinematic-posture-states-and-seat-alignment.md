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
clip's `root_travel`. `POSTURE_ALGORITHM_VERSION` is **1.1.0**; the sidecar records it, and
`KBIndex.load` refuses a sidecar that does not match or does not cover every accepted record.

**Four states: `standing`, `seated`, `floor`, `other`**, decided per frame in a fixed order. `floor`
is THIS PROJECT'S term for a floor-level kinematic state — lying, crawling, anything with the whole
body down near the ground; it is not a standard posture name and is not claimed as one. `other` is
the conservative fallback catching crouching, kneeling, airborne and mid-transition configurations.
Neither is an error state: the corpus splits 1089 dominantly `standing`, **985 `other`**, 211 `floor`
and 161 `seated`, and a clip that reads `other` has been described correctly rather than skipped.

**The measurement set comes from the literature; the numbers do not.** Guerra et al. (2020) recognise
standing, sitting and lying from geometric relations BETWEEN body segments — joint angles, trunk
pitch, joint heights normalised by stature — rather than from absolute positions, because absolute
positions are subject-dependent and the relations are not. Liu et al. (2017) separate the same states
with deterministic rules over trunk and thigh orientation, and say plainly that their angle cut-offs
are empirical. Schenkman et al. (1990) show that sit-to-stand is a staged dynamic process, which is
why the output is a segmentation over time rather than one label.

So the literature decides WHAT IS MEASURED — normalised body height (`HumanPose.bodyPosition.y`,
which is what the records' `mean_body_height` already averages), trunk inclination, thigh and shank
inclination, knee flexion. The eleven numbers in the rules are **fixed operational thresholds**:
values chosen so the rules cut this corpus where a person would, listed in the sidecar's `_meta` with
their units, versioned so a change is visible, and deliberately NOT called biomechanical thresholds,
because they are not measurements of anybody. Nothing is fitted and there is no calibration step —
heights arrive normalised by Unity's human scale and angles are scale-free by construction, so there
is nothing left for one to do.

**Shank inclination is the one that earns its place**: it is what separates SITTING (thigh
horizontal, shank vertical) from SQUATTING (both pitched), and without it a crouch reads as a sit,
which is the mistake this rule set's shape makes most likely.

**A run shorter than `MIN_POSTURE_DURATION_S` (0.3 s) is not a segment.** Per-frame states flicker at
the boundaries of a dynamic movement; a segmentation that reported the flicker would make every
sit-down a dozen states long.

### 2. `audit_posture.py`, and what it is not

Twenty-one clips whose content is not in doubt, with hand-written expectations in
`motionkb_build/posture_audit.json`, checked against the rules. **It passes 21 / 21** and it is a
sanity audit, not an evaluation: twenty-one clips cannot measure an accuracy and it prints none. What
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
the 2446 end in a different state from the one they start in** — 11 of them standing → seated and 9
coming back — so a posture change is a SEARCH now, and the result says so by carrying no
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
