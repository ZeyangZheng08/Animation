# 0011 — Measure in Unity's normalised Humanoid space; `metric_formula_version` v2.2.0

Status: Accepted (2026-08-20). Supersedes the metre-and-degree signal definitions of ADR 0007 and
the divisor table of ADR 0010. ADR 0002 (measured vs semantic) still binds and was the constraint
this migration ran under. The 9-channel taxonomy of ADR 0007 is unchanged.

## Context

Every MEASURED signal up to v2.1.0 was read off bone positions in metres and bone angles in degrees:
`mean_bone_hips_rel_pos_stddev_m` for an arm, `max_torso_lean_deg` for the torso, and so on. Those
numbers describe the motion **and the body it was measured on**, because a longer forearm sweeps
more metres for the same shoulder rotation.

Measured directly, sampling one clip on two rigs:

| signal | on `nurse_avatar` | on `X Bot` | difference |
| --- | --- | --- | --- |
| torso | 46.49 | 37.99 | **−18.3%** |
| head | 5.26 | 4.96 | −5.8% |
| left_arm | 0.1459 | 0.1533 | +5.0% |
| left_leg | 0.6267 | 0.6682 | +6.6% |
| root gait | 0.1609 | 0.1874 | **+16.5%** |

The two rigs differ by 26% in forearm length and 16% in hip height, and the signals inherit that.
This pinned the whole contract to one avatar: `CALIBRATION_AVATAR` was not a note about provenance,
it was a load-bearing constant, and the ADR 0010 divisors were fitted to one body's proportions.

## Decision

Measure in the representation Unity already normalises every rigged avatar into.

`HumanPose.muscles` is 95 floats, each one joint's rotation expressed against **that avatar's own**
limit — dimensionless and body-independent. `HumanPose.bodyPosition` is expressed in that same
normalised frame: it is not metres, and it does not scale with the body either.
The sampler now records both, appended after every pre-existing key so the dump's prefix stays
byte-identical (verified: the first 449,176 bytes of a re-sampled clip are unchanged).

The same cross-rig test, in muscle space, over 103 frames:

| vs `nurse_avatar` | mean \|Δ muscle\| | max \|Δ muscle\| | mean \|Δ bodyPosition\| |
| --- | --- | --- | --- |
| X Bot | 0.000091 | 0.0258 | 0.000015 |
| Y Bot | 0.000119 | 0.0511 | 0.000017 |

Four orders of magnitude better than the metre-space difference, and it holds across the UE-named
`nurse_avatar` as well as the two `mixamorig:` rigs.

Re-verified 2026-08-22, because the sentence above about `bodyPosition` was ambiguous enough that
`config.py`, `metrics.py` and `unity_sampler.py` had all drifted into asserting the opposite of this
ADR's own conclusion. Three rigs whose real hip heights span 15.6%, six clips chosen to stress the
root channel — standing, walking, crouching, arms raised, CPR and free fall:

| rig | `humanScale` | real hip height | `bodyPosition.y` on `mx_Standing_Idle` |
| --- | --- | --- | --- |
| `nurse_avatar` | 0.9773 | 0.902 m | 0.966577 |
| `Y Bot` | 1.0352 | 0.998 m | 0.966582 |
| `X Bot` | 1.0500 | 1.043 m | 0.966581 |

Muscles agree to six decimal places on every clip; `bodyPosition` to ~1e-5, worst case 1.4e-4
(0.013%) on `nurse_cpr_30`. The root channel is body-independent along with the anatomical eight,
and the comments have been corrected to say so.

**Channel signal.** For each of the 8 anatomical channels: the RMS, across that channel's degrees of
freedom, of each one's standard deviation over time. RMS rather than the mean so a channel is not
diluted by its own size — a hand has 20 DOF against the torso's 9 — and not the max, which would let
one twitchy DOF speak for the whole channel. Muscles are grouped by `HumanTrait.BoneFromMuscle`,
read out of the dump, so the grouping comes from the engine rather than from name matching that
could drift: 89 muscles mapped, 6 excluded (eyes and jaw — no facial animation here, and the Mixamo
rigs do not even carry those bones).

**Root channel.** `max(trans, vert, heading)` from `bodyPosition` and `bodyRotation`. The foot-gait
term is gone: foot lift was a metre-space proxy for "is this locomotion", and that question now
belongs to the legs. `validate_motionkb.py` gates `cyclic-locomotion` on a leg channel being
dynamic, not on the root — the store's own `walking` is an in-place walk whose legs step and whose
body does not move, and gating on the root would reject the clearest example of the label. Whether a
clip travels is something the runtime converts either way: `Locomotion.cs` drives a NavMeshAgent
while an in-place clip plays.

**One static threshold.** v2.1.0 needed a threshold per channel because the signals were
dimensionally incommensurable — degrees, metres, degrees of finger curl. Muscle values are all the
same kind of number, so `STATIC_MUSCLE = 0.02` covers every anatomical channel, with heading keeping
2.0° because it is the one signal still in degrees. 0.02 is about twice the largest channel reading
of the store's own reference for standing still: `idle` measures 0.0047 at the torso and 0.0109 at
its busiest channel.

**Divisors** refitted over the same 150 randomly sampled corpus clips (seed 0), corpus p99 → 0.85:

| group | v2.1.0 (metres/deg) | v2.2.0 (muscle) | corpus p99 |
| --- | --- | --- | --- |
| torso | 193.8 | 0.3174 | 0.2698 |
| head | 53.8 | 0.5809 | 0.4937 |
| arm | 0.595 | 0.6914 | 0.5877 |
| leg | 1.607 | 0.4296 | 0.3652 |
| hand | 166.1 | 0.7327 | 0.6228 |
| root_trans | 1.551 | 1.5637 | 1.3291 |
| root_vert | — | 1.3009 | 1.1058 |
| root_heading | 149.7 | 142.1 | 120.79 |

## Consequences

**15 of 72 accepted channel labels flip, and that is the point.** A divisor change must never move
`state_label`; a change of signal legitimately does. Every flip was reviewed one at a time and each
one is a case where the old signal counted "carried by the torso" as "moved":

- `grab_bottle.left_arm` dynamic → **static**. Muscle 0.0017: reaching for the bottle, the left arm's
  joints barely rotate. The metre signal saw it travel because the torso leaned and took it along.
- `cpr.head` stays static, and now honestly: across 540 frames the neck and head joints move less
  than 0.0003 of their range. In metres it read 0.0856, because a leaning torso carries the head.
- `cpr.left_arm` / `right_arm` static → **dynamic** (0.0779 / 0.0397). Chest compressions move the
  arms; the old label was simply wrong.
- `walking.torso` / `head` static → **dynamic**. The torso and head swing with the stride.
- `typing.head` static → **dynamic** (0.0703) — the head moves between screen and keyboard.
- Four root flips (`check_pulse`, `giving_pills`, `grab_bottle`) are the new root definition
  including **turning**: those clips rotate by 3.1°, 8.2° and 6.6° of yaw standard deviation. The
  old root looked only at foot lift and could not see a turn at all.

Two are borderline and worth knowing: `grab_bottle.left_leg` (0.0318) and `right_leg` (0.0209) sit
just over the threshold on a small weight shift. Raising the threshold to 0.035 would return them to
static, at the cost of also returning `walking.head` (0.0263), which should not be static.

**`CALIBRATION_AVATAR` stops being load-bearing.** It records which avatar was sampled; it no longer
determines the numbers. Any correctly configured Humanoid produces the same measurements.

**Rendering and measurement now use different avatars, deliberately.** `RENDER_AVATAR = "Y Bot.fbx"`
— Mixamo's featureless mannequin — because previewing every clip on a nurse in scrubs biased the
VLM's categorical labels toward a clinical reading for a corpus that is mostly general motion.
Measurement is unaffected by which body it runs on, which is what makes the split safe.

**Magnitudes are not comparable across v2.1.0.** Nor across v2.0.0. The `kb/v2` tag restores a
coherent store, not a comparable one.

**Portability, which is the research point.** Muscle values are Unity's spelling of a concept every
rig format has: a joint's rotation as a fraction of its own range. Unreal, Blender and SMPL all
express the same thing. A KB measured this way describes motion rather than one character, which is
the precondition for the engine-decoupled contract ADR 0001 sets out.

## Alternatives considered

**Normalise the metre signals by limb length.** Divide each distance by the relevant bone length or
by hip height. Rejected: it re-derives, imperfectly, a normalisation the engine already performs
exactly, and it leaves the signal measuring where a body part ended up rather than what its joint
did — which is the distinction that made `cpr.head` read 0.0856 for a head that never turned.

**Keep the old labels and change only `motion_magnitude`.** Rejected: `state_label` and
`motion_magnitude` would then describe the same channel from two different measurements, and
`validate_semantic_consistency` cross-checks them. The store would either fail its own gate or pass
while being wrong.

**Keep a foot-gait term in the root.** It would have preserved `walking`'s old root reading.
Rejected: it conflates "the legs are stepping" with "the body went somewhere", and those are
separate facts that separate consumers need — the first for retrieval, the second for scene landing.
