using System.Collections.Generic;
using Unity.Collections;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Experimental.Animations;
using UnityEngine.Playables;

namespace AgentRuntime
{
    /// <summary>
    /// Plays an assembled motion: a sequence of steps, each a base clip plus masked overlays, on a
    /// PlayableGraph of our own. One step is the common case and takes the same path as a sequence.
    ///
    /// LAYERS BOTH WAYS, AND A SEAM IS PER CHANNEL. Within a step, an AnimationLayerMixerPlayable stacks
    /// overlays on a base with masks. Across steps it is the same primitive again, and that is the point:
    /// a crossfade driven by one weight moves every body part over the same seconds, so the busiest joint
    /// snaps and the quietest crawls. Measured on this corpus, the busiest channel at a seam travels 2.9
    /// to 5.2 times as far as the average, which is the number the single blend length was computed from
    /// — joining `idle` to `walking` gave three frames for 26 degrees of arm, 260 degrees a second
    /// against a stated ceiling of 180.
    ///
    /// So a step arriving over a seam contributes not one input but one per group of channels that share
    /// a ramp, each masked to those channels and each with its own start and length. The step's own layer
    /// mixer feeds all of them through extra output ports rather than being rebuilt per group. Where a
    /// step carries no per-channel schedule it contributes a single unmasked input and behaves exactly as
    /// the old whole-body crossfade did, which is what a `direct` seam and a one-step plan both want.
    ///
    /// THE SCHEDULE IS NOT DECIDED HERE. Start times, fade lengths, which frame each step enters on, and
    /// now which channels move when, all arrive on the wire, computed by agent/transitions.py from the
    /// seam table and the measured per-channel pose distance. This side plays a timetable; it does not
    /// write one. Same split that keeps channel arbitration off the engine.
    ///
    /// NOTHING FADES OUT ANY MORE. A later layer at full weight replaces the ones under it wherever its
    /// mask reaches, so the handover is the incoming step rising rather than a pair of ramps that have to
    /// be kept complementary by hand. The cascade the old sequence mixer needed survives only in the
    /// reported `MaxConcurrentSteps` and `PeakOverlap`, which describe playback rather than drive it.
    ///
    /// A CHANNEL MAY HAVE TWO SOURCES AT ONCE, AND IT WAS MEASURED BEFORE ANYTHING WAS BUILT ON IT.
    /// `full weight` above is the common case, not the only one: a layer at 0.4 inside its mask leaves
    /// 0.6 of what is underneath, so that channel is genuinely a mix rather than a handover. That a
    /// masked layer INTERPOLATES at a fractional weight, rather than overriding binary the way its mask
    /// does, is the assumption the whole thing rests on, so it was checked first, on this rig, in this
    /// project: `walking` under `nurse_cpr_30` masked to the right arm, sampled at the same instant at
    /// weights 0, 0.5 and 1. The two ends are 69.14 degrees apart at the right elbow; the midpoint sits
    /// 34.21 from one and 35.01 from the other, summing to 69.22 — a detour of 0.08 degrees, i.e. on the
    /// geodesic between them. The left elbow, outside the mask, moved 0.00. So the weight blends and the
    /// mask still confines, which is exactly the pair of properties this needs.
    ///
    /// ADR 0004 originally restricted co-playback to DISJOINT channels; ADR 0022 deleted the record
    /// fields that restriction was written against. The agent's plan now states which channels each
    /// overlay drives, and a channel two of them name is mixed half each. Where the plan pins a channel
    /// to a scene object — a carry, an IK binding, a gaze-bound head — mixing is refused by name instead:
    /// half of a hand shaped for a patient's chest and half for a pill bottle grips neither, which is the
    /// same argument that stops two hands being aimed at one anchor.
    ///
    /// Two clips mixed at unrelated phases average two unrelated poses, so a mixed layer also carries its
    /// own entry time — see `LayerSpec.ClipStartSeconds`. Both numbers are computed agent-side.
    ///
    /// COEXISTENCE WITH ANIMATION RIGGING, VERIFIED. The rig's own graph creates its outputs with
    /// sorting order 1000 and AnimationStreamSource.PreviousInputs. This output sits at 500 with
    /// DefaultValues, so it writes the composed pose first and the rig's IK constraints then solve on
    /// top of it. Measured in play mode on CPRNurse: with the CPR clip driving the arm, enabling the
    /// R_Hand TwoBoneIKConstraint moved the hand onto its target with 0.0000 m error. Re-measured after
    /// an AnimationScriptPlayable was spliced in: 0.0353 m against a controller-only baseline of
    /// 0.0361 m, i.e. unchanged. If that ever stops holding, the symptom is IK silently doing nothing.
    ///
    /// MASKS ARE BUILT AT RUNTIME, not authored as assets. AvatarMask.SetHumanoidBodyPartActive is a
    /// runtime API and the knowledge base's engine_mask_map.json already maps every channel to a body part, so the
    /// channel list travels on the wire as data and nothing has to be pre-enumerated as an asset.
    ///
    /// ONE KNOWN LIMIT, documented in that same map: AvatarMaskBodyPart.LeftArm EXCLUDES the clavicle,
    /// while the knowledge base's arm channel includes clavicle and wrist. A body-part mask therefore
    /// cannot express the contract's arm boundary exactly. The same file notes Unity's masks are binary
    /// per transform, so a composed torso seam is harder than UE5's graded blend. Both are engine
    /// limits, not defects here — do not "fix" a visible seam by widening a mask.
    ///
    /// LAYER 0 OF EACH STEP IS UNMASKED ON PURPOSE. The base clip plays full-body and overlays mask on
    /// top, so channels nobody claimed fall through to the base. Masking layer 0 down to its claimed
    /// channels instead would leave the rest at the bind pose and T-pose the character from the waist up.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class MotionComposer : MonoBehaviour
    {
        /// <summary>Each channel and the mask parts it owns. A limb takes its IK goal with it: the clips
        /// play with foot IK on, and a layer that owns a leg without owning that leg's IK goal leaves the
        /// goal behind on whatever was underneath, which is a different pose from the one the clip
        /// describes. The nine channels between them cover every body part Unity has except the goals,
        /// and with the goals attached here they cover all of it — which the seam relies on, since a body
        /// part no layer claims would keep the outgoing step's pose for good.</summary>
        private static readonly Dictionary<string, AvatarMaskBodyPart[]> ChannelParts =
            new Dictionary<string, AvatarMaskBodyPart[]>
            {
                { "root", new[] { AvatarMaskBodyPart.Root } },
                { "torso", new[] { AvatarMaskBodyPart.Body } },
                { "head", new[] { AvatarMaskBodyPart.Head } },
                { "left_arm", new[] { AvatarMaskBodyPart.LeftArm, AvatarMaskBodyPart.LeftHandIK } },
                { "right_arm", new[] { AvatarMaskBodyPart.RightArm, AvatarMaskBodyPart.RightHandIK } },
                { "left_leg", new[] { AvatarMaskBodyPart.LeftLeg, AvatarMaskBodyPart.LeftFootIK } },
                { "right_leg", new[] { AvatarMaskBodyPart.RightLeg, AvatarMaskBodyPart.RightFootIK } },
                { "left_hand", new[] { AvatarMaskBodyPart.LeftFingers } },
                { "right_hand", new[] { AvatarMaskBodyPart.RightFingers } }
            };

        [SerializeField] private Animator animator;

        /// <summary>Correction applied to the composed pose on its way to the output.
        ///
        /// This is how frames that exist in no clip get made. A crossfade between a stance and a sit
        /// interpolates every joint at once and floats the character down with straight legs; adding a
        /// hip target and a foot lock turns the same blend into something that reads as sitting. The
        /// mechanism is an animation job writing AnimationHumanStream, verified in play mode: hips moved
        /// 0.473 m for a 0.440 m ask, feet drifted 0.0020 m, and the rig's IK residual was 0.0353 m
        /// against a 0.0361 m controller-only baseline, i.e. unchanged.
        ///
        /// NOT OnAnimatorIK + Animator.bodyPosition. That is raised by the AnimatorController's IK pass,
        /// and this composer bypasses the controller entirely, so it never fires.
        /// </summary>
        public struct Correction : IAnimationJob
        {
            public float bodyDrop;              // metres to lower the body, avatar-local
            public float footWeight;            // 0 = leave the clip's feet alone, 1 = pin them
            public Vector3 leftFoot, rightFoot; // world goals, captured before the descent
            public Quaternion leftRot, rightRot;

            public void ProcessRootMotion(AnimationStream stream) { }

            public void ProcessAnimation(AnimationStream stream)
            {
                // A zero correction has to be exactly a pass-through, because this job is always in the
                // graph — splicing it in only when needed would mean the common path and the interesting
                // path have different graph shapes, and only one of them gets tested.
                if (bodyDrop == 0f && footWeight <= 0f) return;
                if (!stream.isHumanStream) return;

                AnimationHumanStream human = stream.AsHuman();
                if (bodyDrop != 0f)
                {
                    Vector3 body = human.bodyLocalPosition;
                    body.y -= bodyDrop;
                    human.bodyLocalPosition = body;
                }
                if (footWeight <= 0f) return;
                human.SetGoalPosition(AvatarIKGoal.LeftFoot, leftFoot);
                human.SetGoalRotation(AvatarIKGoal.LeftFoot, leftRot);
                human.SetGoalWeightPosition(AvatarIKGoal.LeftFoot, footWeight);
                human.SetGoalWeightRotation(AvatarIKGoal.LeftFoot, footWeight);
                human.SetGoalPosition(AvatarIKGoal.RightFoot, rightFoot);
                human.SetGoalRotation(AvatarIKGoal.RightFoot, rightRot);
                human.SetGoalWeightPosition(AvatarIKGoal.RightFoot, footWeight);
                human.SetGoalWeightRotation(AvatarIKGoal.RightFoot, footWeight);
                human.SolveIK();
            }
        }

        /// <summary>Reads the root motion out of the stream and leaves it where the composer can pick
        /// it up. Writes nothing to the pose.
        ///
        /// WHY A JOB AND NOT `Animator.applyRootMotion`. That flag is consumed by Unity's animation
        /// UPDATE LOOP, and half of this system does not run one: the pre-execution check replays a
        /// whole plan on a hidden duplicate through `PlayableGraph.Evaluate()`, as fast as the CPU
        /// will go. Under manual evaluation the flag does nothing, so a sit-down that travelled 0.45 m
        /// on the visible character travelled zero on the copy that was supposed to be checking it --
        /// and the check then failed the plan for missing the seat by exactly the distance it had not
        /// been allowed to move. `ProcessRootMotion` is called on both paths, so putting the read here
        /// makes the duplicate and the character the same code rather than two implementations that
        /// agree until they do not.
        ///
        /// WHAT IS ACCUMULATED. Deltas, in the animator's own space, summed across however many
        /// evaluations happen between two reads: the realtime path evaluates once per frame and the
        /// validator many times per frame, and the consumer should not have to know which. Index 0-2
        /// is the translation, 3 the yaw in degrees, 4 a count of evaluations that carried anything --
        /// which is how the first play-mode run told me the stream really was delivering it.
        /// </summary>
        public struct RootMotionTap : IAnimationJob
        {
            public NativeArray<float> accumulated;

            public void ProcessRootMotion(AnimationStream stream)
            {
                if (!accumulated.IsCreated || accumulated.Length < 8) return;
                accumulated[5] = accumulated[5] + 1f;               // called at all

                // TWO READINGS, BECAUSE ONLY MEASUREMENT SETTLES WHICH ONE THIS GRAPH CARRIES.
                // `velocity` is the root motion as a rate and is what the documentation points at;
                // `rootMotionPosition` is the displacement for this evaluation directly. On a graph
                // whose output is an AnimationScriptPlayable chain they are not both populated, and
                // which one is depends on where in the chain the job sits. Both are accumulated and
                // both are reported, so the choice below is made against numbers from this project
                // rather than against an expectation.
                float dt = stream.deltaTime;
                Vector3 fromVelocity = dt > 0f ? stream.velocity * dt : Vector3.zero;
                float yawFromVelocity = dt > 0f ? stream.angularVelocity.y * dt * Mathf.Rad2Deg : 0f;
                Vector3 fromRootMotion = stream.rootMotionPosition;
                float yawFromRootMotion = stream.rootMotionRotation.eulerAngles.y;
                if (yawFromRootMotion > 180f) yawFromRootMotion -= 360f;

                if (fromVelocity.sqrMagnitude > accumulated[6] * accumulated[6])
                    accumulated[6] = fromVelocity.magnitude;
                if (fromRootMotion.sqrMagnitude > accumulated[7] * accumulated[7])
                    accumulated[7] = fromRootMotion.magnitude;

                // Whichever of the two actually carries something is the one that moves her.
                Vector3 move = fromVelocity.sqrMagnitude > 0f ? fromVelocity : fromRootMotion;
                float yaw = fromVelocity.sqrMagnitude > 0f ? yawFromVelocity : yawFromRootMotion;
                if (move.sqrMagnitude <= 0f && yaw == 0f) return;

                accumulated[0] = accumulated[0] + move.x;
                accumulated[1] = accumulated[1] + move.y;
                accumulated[2] = accumulated[2] + move.z;
                accumulated[3] = accumulated[3] + yaw;
                accumulated[4] = accumulated[4] + 1f;
            }

            public void ProcessAnimation(AnimationStream stream) { }
        }

        private PlayableGraph _graph;
        private AnimationLayerMixerPlayable _sequence;
        private AnimationScriptPlayable _correction;
        private AnimationScriptPlayable _rootTap;
        private NativeArray<float> _rootMotion;
        private readonly List<StepRuntime> _steps = new List<StepRuntime>();
        private readonly Dictionary<string, AvatarMask> _maskCache = new Dictionary<string, AvatarMask>();
        private bool _built;
        private bool _manual;

        /// <summary>Something to splice between the correction and the output when the graph is next
        /// built. Returns the new root.
        ///
        /// THIS EXISTS FOR ONE CALLER AND ONE REASON. Animation Rigging normally runs as a SECOND
        /// PlayableGraph writing to the same Animator, and Unity composes the two by output sorting
        /// order every frame. That works at runtime and does not work under manual evaluation:
        /// measured on the validation duplicate, evaluating the rig's graph after this one replaced
        /// the composed pose wholesale -- every sample came back byte-identical and a metre below the
        /// floor, which read as "this motion puts a foot through the ground" about a plain idle. So
        /// the duplicate takes the rig OUT of its own graph and splices it in here instead: one graph,
        /// one evaluation, constraints on top of the pose they are meant to correct.
        ///
        /// Null on the visible character, where the two graphs are composed the normal way.
        /// </summary>
        public System.Func<PlayableGraph, Playable, Playable> ExtendGraph;
        private double _elapsed;
        private int _maxConcurrent;
        private float _peakOverlap;

        /// <summary>How many steps carried real weight at once, at the busiest moment. 1 means every
        /// handover was a hard cut; 2 means a crossfade actually ran. Recorded rather than assumed,
        /// because a scheduler that silently snaps looks identical from the outside.</summary>
        public int MaxConcurrentSteps { get { return _maxConcurrent; } }

        /// <summary>The largest weight the quieter of two blending steps ever held. A real crossfade
        /// peaks near 0.5; a one-frame flicker peaks near zero.</summary>
        public float PeakOverlap { get { return _peakOverlap; } }

        /// <summary>Channels being driven by two sources at once, and the share the graph actually
        /// holds for the second of them.
        ///
        /// The same question PeakOverlap answers across a seam, asked inside one step: a plan that
        /// asked for a mix and a plan that quietly resolved the channel to one winner look identical
        /// from outside, and both play. So the weight is READ BACK off the mixer rather than echoed
        /// from the request — an engine that ignored it reports 1, which is the answer that matters.
        ///
        /// A step whose channels each have one source contributes nothing here, which is every plan
        /// written before mixing existed.
        /// </summary>
        public List<Dictionary<string, object>> MixedChannels
        {
            get
            {
                List<Dictionary<string, object>> mixed = new List<Dictionary<string, object>>();
                for (int k = 0; k < _steps.Count; k++)
                {
                    StepRuntime step = _steps[k];
                    if (!step.Layers.IsValid()) continue;
                    List<LayerSpec> specs = step.Spec.Layers;
                    for (int i = 1; i < specs.Count && i < step.Layers.GetInputCount(); i++)
                    {
                        float held = step.Layers.GetInputWeight(i);
                        if (held >= 0.999f || held <= 0.001f) continue;
                        mixed.Add(new Dictionary<string, object>
                        {
                            { "step", k },
                            { "action_id", specs[i].ActionId },
                            { "channels", specs[i].Channels },
                            { "weight", held },
                            { "asked_for", specs[i].Weight }
                        });
                    }
                }
                return mixed;
            }
        }

        public Animator Animator { get { return animator != null ? animator : GetComponent<Animator>(); } }
        public bool Playing { get { return _built && _graph.IsValid() && _graph.IsPlaying(); } }

        /// <summary>The graph itself, for the one caller that has to synchronise something into it
        /// before each manual evaluation. Not for general use: everything else about this graph is
        /// reachable through the methods above.</summary>
        public PlayableGraph Graph { get { return _graph; } }
        public int StepCount { get { return _steps.Count; } }
        public double Elapsed { get { return _elapsed; } }

        /// <summary>The action id of the step carrying the most weight right now, or null when nothing
        /// is playing. This is what "she is already doing" means to anything outside this component —
        /// the agent reads it to decide whether a plan needs to open on a walk at all, since a walk
        /// cycle played while she is standing still marches her on the spot.</summary>
        public string PlayingActionId
        {
            get
            {
                if (!Playing) return null;
                int k = ActiveStep;
                return k < 0 || k >= _steps.Count ? null : _steps[k].Spec.ActionId;
            }
        }

        /// <summary>Index of the step carrying the most weight right now — what "is currently playing"
        /// means when two are crossfading.</summary>
        public int ActiveStep
        {
            get
            {
                int best = -1;
                float top = -1f;
                for (int i = 0; i < _steps.Count; i++)
                {
                    float w = Presence(i);
                    if (w > top) { top = w; best = i; }
                }
                return best;
            }
        }

        private void Awake()
        {
            if (animator == null) animator = GetComponent<Animator>();
            EnableRootMotionComputation();
        }

        /// <summary>Ask Unity to COMPUTE root motion. It does not follow that anything is moved by it.
        ///
        /// MEASURED, BECAUSE THE FIRST IMPLEMENTATION OF THIS READ ZEROES. With `applyRootMotion`
        /// false the animation system skips the root-motion pass entirely, so `stream.velocity` and
        /// `stream.rootMotionPosition` are both exactly 0 -- and the job reading them was called 1386
        /// times over a looping walk and saw nothing. The flag is not "apply it to the transform"; it
        /// is "work it out at all", and without it there is nothing for any consumer to consume.
        ///
        /// WHAT STOPS UNITY APPLYING IT IS `OnAnimatorMove` BELOW. Its mere existence on this
        /// GameObject is the documented signal that root motion is handled by script, so the engine
        /// hands it over instead of moving the transform itself. That matters here beyond taste: the
        /// realtime path would otherwise move her and `ConsumeRootMotion` would move her again, and
        /// the hidden duplicate -- which never runs an animation update loop and so never fires
        /// `OnAnimatorMove` -- would move her once. Two paths, two answers, which is the one thing a
        /// pre-execution check may not have.
        /// </summary>
        private void EnableRootMotionComputation()
        {
            if (animator != null) animator.applyRootMotion = true;
        }

        /// <summary>Present so Unity leaves the transform alone. Deliberately empty: what to do with
        /// the root motion is decided in <see cref="ConsumeRootMotion"/>, which both the realtime and
        /// the validation path reach.</summary>
        private void OnAnimatorMove() { }

        private void OnDisable()
        {
            Teardown();
        }

        /// <summary>Build the graph paused. Split from Play so start-play latency is measurable and the
        /// expensive part (clip load, mask build, graph build) is off the moment of starting.
        ///
        /// THE CORRECTION SURVIVES THE REBUILD. It lives inside the graph, so tearing the graph down
        /// used to discard it — and with it any posture that had been generated rather than retrieved.
        /// Measured: a character who had sat down stood back up the instant the next plan was
        /// committed, silently, with nothing in any report to say a posture had been thrown away.
        /// Rebuilding the playback graph is not a reason for the character to change shape. Whoever
        /// wants the correction gone says so, with SetCorrection(default) — see AgentCharacter, which
        /// clears it when a plan actually returns her to standing.
        ///
        /// One coupling this relies on: the foot goals inside a Correction are WORLD positions, so
        /// carrying them across a rebuild is only sound while the character has not moved. She cannot —
        /// walking while seated is refused, for the separate reason that re-enabling the NavMeshAgent
        /// warps her to the nearest walkable point, off the chair. If that refusal ever goes, this has
        /// to re-pin the feet rather than carry them.
        /// </summary>
        public void Prepare(List<StepSpec> steps)
        {
            Correction carried = GetCorrection();
            Teardown();
            if (steps == null || steps.Count == 0)
            {
                throw new AgentRequestException(Protocol.Err.BadRequest, "an assembly needs at least one step");
            }

            // Resolved and checked before anything is built, because a layer mixer's width is fixed at
            // creation and a rejected schedule should not leave half a graph behind.
            List<List<ChannelBlend>> seams = new List<List<ChannelBlend>>();
            int inputs = 0;
            for (int s = 0; s < steps.Count; s++)
            {
                List<ChannelBlend> groups = s == 0 ? null : SeamGroups(steps[s]);
                seams.Add(groups);
                inputs += groups == null ? 1 : groups.Count;
            }

            _graph = PlayableGraph.Create("AgentComposer:" + name);
            _graph.SetTimeUpdateMode(_manual ? DirectorUpdateMode.Manual : DirectorUpdateMode.GameTime);
            _sequence = AnimationLayerMixerPlayable.Create(_graph, inputs);
            int input = 0;

            for (int s = 0; s < steps.Count; s++)
            {
                StepSpec spec = steps[s];
                if (spec.Layers == null || spec.Layers.Count == 0)
                {
                    Teardown();
                    throw new AgentRequestException(Protocol.Err.BadRequest,
                        "step " + s + " has no layers");
                }

                AnimationLayerMixerPlayable layers =
                    AnimationLayerMixerPlayable.Create(_graph, spec.Layers.Count);
                StepRuntime runtime = new StepRuntime(spec);

                for (int i = 0; i < spec.Layers.Count; i++)
                {
                    LayerSpec layer = spec.Layers[i];
                    if (layer.Clip == null)
                    {
                        Teardown();
                        throw new AgentRequestException(Protocol.Err.NotFound,
                            "no AnimationClip for action '" + layer.ActionId + "'");
                    }

                    AnimationClipPlayable clip = AnimationClipPlayable.Create(_graph, layer.Clip);
                    clip.SetApplyFootIK(true);
                    // Every step enters at the frame the seam picked, not at zero. That frame is where
                    // the search found the two poses closest, so starting anywhere else throws away the
                    // whole point of having searched. A layer may override it with a phase of its own —
                    // see LayerSpec.ClipStartSeconds, and the note there about why mixing needs it.
                    clip.SetTime(layer.ClipStartSeconds ?? spec.ClipStartSeconds);
                    _graph.Connect(clip, 0, layers, i);
                    // LAYER 0 IS PINNED AT 1, whatever the wire says. It is the unmasked base, so a
                    // weight below 1 there does not mix it with anything — there is nothing underneath
                    // — it fades the whole body toward the bind pose and T-poses the character. The
                    // note above on masking layer 0 is the same invariant seen from the other side.
                    layers.SetInputWeight(i, i == 0 ? 1f : Mathf.Clamp01(layer.Weight));
                    runtime.Clips.Add(clip);
                    runtime.HoldFinal.Add(layer.HoldFinalPose);

                    if (i > 0 && layer.Channels != null && layer.Channels.Count > 0)
                    {
                        layers.SetLayerMaskFromAvatarMask((uint)i, MaskFor(layer.Channels));
                    }
                }

                List<ChannelBlend> seam = seams[s];
                if (seam == null)
                {
                    // The opening step, or one whose seam has no per-channel schedule: a single unmasked
                    // input. Step 0 is fully in from the first frame; a later one starts silent and is
                    // faded up whole, which is the old crossfade exactly.
                    _graph.Connect(layers, 0, _sequence, input);
                    _sequence.SetInputWeight(input, s == 0 ? 1f : 0f);
                    runtime.Seam.Add(new SeamLayer(input, 0.0, spec.BlendInSeconds));
                    input++;
                }
                else
                {
                    // Extra output ports rather than a second copy of the step: one evaluation of the
                    // clips feeds every group, and each group reads the same pose through its own mask.
                    layers.SetOutputCount(seam.Count);
                    for (int g = 0; g < seam.Count; g++)
                    {
                        _graph.Connect(layers, g, _sequence, input);
                        _sequence.SetLayerMaskFromAvatarMask((uint)input, MaskFor(seam[g].Channels));
                        _sequence.SetInputWeight(input, 0f);
                        runtime.Seam.Add(new SeamLayer(input, seam[g].OffsetSeconds,
                                                       seam[g].BlendInSeconds));
                        input++;
                    }
                }
                runtime.Layers = layers;
                _steps.Add(runtime);
            }

            // Always spliced in, never conditionally. A zero Correction is a pass-through, and one graph
            // shape means the plain path and the generated-transition path are the same tested path.
            _correction = AnimationScriptPlayable.Create(_graph, carried, 1);
            _graph.Connect(_sequence, 0, _correction, 0);
            _correction.SetInputWeight(0, 1f);

            // The tap sits ABOVE the correction, so what it reads is the root motion of the pose that
            // will actually be written -- and it writes nothing itself, so its position in the chain
            // costs nothing either way. Always spliced in, for the same reason the correction is: one
            // graph shape, one tested path.
            if (!_rootMotion.IsCreated) _rootMotion = new NativeArray<float>(8, Allocator.Persistent);
            ClearRootMotion();
            _rootTap = AnimationScriptPlayable.Create(_graph, new RootMotionTap
            {
                accumulated = _rootMotion
            }, 1);
            _graph.Connect(_correction, 0, _rootTap, 0);
            _rootTap.SetInputWeight(0, 1f);

            Playable root = _rootTap;
            if (ExtendGraph != null) root = ExtendGraph(_graph, root);

            AnimationPlayableOutput output =
                AnimationPlayableOutput.Create(_graph, "AgentComposer-Out", Animator);
            output.SetSourcePlayable(root);
            output.SetSortingOrder(500);
            output.SetAnimationStreamSource(AnimationStreamSource.DefaultValues);
            _built = true;
            _elapsed = 0;
            _maxConcurrent = 0;
            _peakOverlap = 0f;
        }

        public void Play()
        {
            if (!_built) throw new AgentRequestException(Protocol.Err.NotReady, "nothing prepared");
            _elapsed = 0;
            _graph.Play();
        }

        public void Stop()
        {
            Teardown();
        }

        /// <summary>Whether there is a correction job in the graph to write to at all. False between a
        /// Teardown and the next Prepare, and false before anything has ever been prepared.</summary>
        public bool CorrectionLive { get { return _correction.IsValid(); } }

        public Correction GetCorrection()
        {
            return _correction.IsValid() ? _correction.GetJobData<Correction>() : new Correction();
        }

        /// <summary>Write the correction, and SAY whether it landed.
        ///
        /// This used to return void and no-op on an invalid playable, which is the same silence the gate
        /// was built to remove. PoseSynth drives a closed loop through here: it computes an error,
        /// integrates it, writes, and reads the skeleton back next frame. If the write goes nowhere the
        /// error never shrinks and the integrator winds up — measured, `bodyDrop` reached 24.7 m on a run
        /// where the graph had been torn down underneath it, a number with no physical meaning that
        /// nothing reported because nothing was asked. A control loop has to be able to tell "I corrected
        /// and it did not help" from "my correction was discarded", and only the actuator knows which.
        /// </summary>
        public bool SetCorrection(Correction correction)
        {
            if (!_correction.IsValid()) return false;
            _correction.SetJobData(correction);
            return true;
        }

        /// <summary>Hand the clock over, or take it back.
        ///
        /// A graph in manual mode does not advance with the game; it advances exactly as far as
        /// <see cref="Evaluate"/> is told to advance it. That is what lets the pre-execution validator
        /// play a four-second plan on a hidden duplicate in a handful of milliseconds instead of four
        /// seconds -- and four seconds is longer than the whole turn should be, so without this the
        /// check could not exist. The visible character never sets this; the duplicate always does.
        /// </summary>
        public void SetManualTime(bool manual)
        {
            _manual = manual;
            if (_graph.IsValid())
            {
                _graph.SetTimeUpdateMode(manual ? DirectorUpdateMode.Manual
                                                : DirectorUpdateMode.GameTime);
            }
        }

        /// <summary>Advance by `dt` and write the pose, for a graph whose clock this owns. The weights
        /// and the window logic are the same <see cref="Tick"/> the frame loop runs, so a validated
        /// plan and a played one go through one implementation and not two.</summary>
        public void Evaluate(float dt)
        {
            if (!_built || !_graph.IsValid()) return;
            Tick(dt);
            _graph.Evaluate(dt);
            // Immediately, while the numbers belong to the evaluation that just ran. The validator
            // calls this many times per frame, so leaving it to a LateUpdate would fold a whole plan
            // into one transform move -- correct in total and wrong at every sample the gate takes.
            ConsumeRootMotion();
        }

        private void Update()
        {
            // In manual mode somebody else is stepping this, and doing it here as well would advance
            // the same plan twice per frame.
            if (_manual) return;
            Tick(Time.deltaTime);
        }

        private void LateUpdate()
        {
            // The realtime half of the same read. Unity has evaluated the graph by now -- it is on
            // GameTime, so the animation update owns it -- and this is the first point in the frame
            // where what the tap collected is complete.
            if (_manual) return;
            ConsumeRootMotion();
        }

        /// <summary>Take whatever the tap collected and, if the step playing wants it, move the
        /// character by it. Always drains, so a step that does NOT apply root motion discards it
        /// rather than banking it for the next one that does.
        ///
        /// The yaw is taken about Y alone. A clip's root motion carries the whole rotation, and a
        /// character that also pitched and rolled with it would lie down; what travels here is where
        /// she is and which way she is facing.
        /// </summary>
        private void ConsumeRootMotion()
        {
            if (!_rootMotion.IsCreated) return;
            Vector3 move = new Vector3(_rootMotion[0], _rootMotion[1], _rootMotion[2]);
            float yaw = _rootMotion[3];
            _rootMotionSamples += (int)_rootMotion[4];
            _rootMotionCalls += (int)_rootMotion[5];
            if (_rootMotion[6] > _peakVelocityM) _peakVelocityM = _rootMotion[6];
            if (_rootMotion[7] > _peakRootMotionM) _peakRootMotionM = _rootMotion[7];
            ClearRootMotion();
            if (!RootMotionActive) return;
            if (move.sqrMagnitude > 0f)
            {
                // The stream's translation is in the animator's own space, so it turns with her --
                // which is what makes "step backwards" mean backwards from wherever she is facing.
                transform.position += transform.rotation * move;
                _rootMotionApplied += move.magnitude;
            }
            if (yaw != 0f) transform.rotation *= Quaternion.Euler(0f, yaw, 0f);
        }

        /// <summary>How much root motion has been applied to the transform, and over how many
        /// evaluations the tap saw any. Reported rather than inferred: "the clip should have moved
        /// her" and "the stream delivered it" are different claims, and the first play-mode run of
        /// this needed to tell them apart.</summary>
        public float RootMotionAppliedM { get { return _rootMotionApplied; } }
        public int RootMotionSamples { get { return _rootMotionSamples; } }
        public int RootMotionCalls { get { return _rootMotionCalls; } }
        public float PeakVelocityM { get { return _peakVelocityM; } }
        public float PeakRootMotionM { get { return _peakRootMotionM; } }

        private float _rootMotionApplied;
        private int _rootMotionSamples;
        private int _rootMotionCalls;
        private float _peakVelocityM;
        private float _peakRootMotionM;

        /// <summary>Whether the step playing right now drives the transform from its own root
        /// motion. See <see cref="LayerSpec.ApplyRootMotion"/>.</summary>
        public bool RootMotionActive { get; private set; }

        /// <summary>Whether a step whose root motion is CONSUMED rather than discarded is the one
        /// the stream is mostly showing.
        ///
        /// `Animator.applyRootMotion` is set once in `Awake` and never touched per step. It is what
        /// makes Unity COMPUTE root motion at all -- measured, the tap reads exact zeroes without it
        /// -- and the empty `OnAnimatorMove` is what stops Unity APPLYING it, leaving
        /// <see cref="ConsumeRootMotion"/> the only thing that moves her on either path. Switching
        /// the flag per step would have given the visible character one behaviour and the hidden
        /// duplicate that checks her another, which is the one difference a validator may not have.
        ///
        /// THE NAVIGATION AGENT HAS TO BE OUT OF THE WAY while this is on, or the two fight over the
        /// transform and the agent wins -- the same fight `Locomotion.Suspend` exists to end for a
        /// generated descent. `AgentCharacter` does that around a plan that contains such a step.
        /// </summary>
        private void SyncRootMotion()
        {
            // ANY such step that is at least half established, rather than the single most present
            // one.
            //
            // HALF, BECAUSE THAT IS WHEN THE STREAM IS MOSTLY THIS CLIP. Below it the pose the tap
            // reads is still mainly the outgoing one, and consuming there would apply the WALK's
            // root motion -- which is exactly what a locomotion step must never do. So the blend-in
            // is discarded on purpose: measured on walk -> sit -> settle, 0.3765 m of the sit-down's
            // own 0.4460 m reaches the transform, and the missing 0.07 m is its 0.2 s crossfade.
            //
            // THIS IS A NO-OP ON A LINEAR SEQUENCE, and was measured to be one: `applied_m` is
            // byte-identical before and after the rule changed, because `Presence` already cascades
            // (a step's presence is scaled by 1 - the next step's), so at most one step is above half
            // at a time and "most present" and "over half" pick the same one. It is written this way
            // for the case that is not linear -- two travelling steps overlapping -- where the old
            // rule silently handed the transform to whichever happened to lead.
            bool want = false;
            for (int k = 0; k < _steps.Count; k++)
            {
                if (!_steps[k].Spec.ApplyRootMotion) continue;
                if (Presence(k) > 0.5f) want = true;
            }
            RootMotionActive = want;
        }

        /// <summary>One step of the schedule: seam weights, then the per-layer window ends. Split out
        /// of Update so the pre-execution validator can run it at fixed timestep -- see
        /// <see cref="SetManualTime"/>. Nothing in it reads a clock; `dt` is the only time it sees.
        /// </summary>
        public void Tick(double dt)
        {
            if (!_built || !_graph.IsValid()) return;
            _elapsed += dt;

            // Each seam group rises on its own schedule. Step 0 is the base layer and stays at 1 — it is
            // not faded out, it is covered over, channel by channel as each group arrives.
            for (int k = 0; k < _steps.Count; k++)
            {
                StepRuntime step = _steps[k];
                float sum = 0f;
                for (int g = 0; g < step.Seam.Count; g++)
                {
                    SeamLayer layer = step.Seam[g];
                    float w = k == 0 ? 1f : Ramp(step.Spec, layer);
                    _sequence.SetInputWeight(layer.Input, w);
                    sum += w;
                }
                // How far in this step is, averaged over its groups. A report, not a control signal:
                // playback is the per-group weights above, and this exists so "which step is playing"
                // still has an answer when half of one is in and half is not.
                step.Presence = step.Seam.Count == 0 ? 0f : sum / step.Seam.Count;
            }

            // Cascade, kept for the report alone: a step is only as present as its successor is absent,
            // which is what makes these two numbers comparable with every run recorded before the seam
            // became per channel.
            int live = 0;
            float top = 0f, second = 0f;
            for (int k = 0; k < _steps.Count; k++)
            {
                float weight = Presence(k);
                if (weight > 0.01f) live++;
                if (weight > top) { second = top; top = weight; }
                else if (weight > second) { second = weight; }
            }
            if (live > _maxConcurrent) _maxConcurrent = live;
            if (second > _peakOverlap) _peakOverlap = second;

            // After the weights, because which step is dominant is what decides this.
            SyncRootMotion();

            for (int k = 0; k < _steps.Count; k++)
            {
                StepRuntime step = _steps[k];
                for (int i = 0; i < step.Clips.Count; i++)
                {
                    LayerSpec layer = step.Spec.Layers[i];
                    // WHERE THIS LAYER ENDS is its window's end when it has one, and the clip's own end
                    // otherwise. A window is how an overlay contributes a PART of its clip — one chest
                    // compression out of thirty — rather than all of it; which frames those are is
                    // measured agent-side from the frozen dumps, never decided here.
                    bool windowed = layer.ClipEndSeconds.HasValue;
                    if (!windowed && !step.HoldFinal[i]) continue;

                    AnimationClipPlayable clip = step.Clips[i];
                    double end = windowed
                        ? layer.ClipEndSeconds.Value
                        : clip.GetAnimationClip().length;
                    if (clip.GetTime() < end - 0.001) continue;

                    if (windowed && layer.LoopInWindow && !step.HoldFinal[i])
                    {
                        // Back to the start of the window rather than the start of the clip. The
                        // agent sets this only where it measured the window's two ends to be close
                        // enough to join — a repetition — so the wrap is a loop and not a snap.
                        clip.SetTime(layer.ClipStartSeconds ?? step.Spec.ClipStartSeconds);
                        continue;
                    }
                    // Freezing an overlay on its last frame is how a grasp is kept while the base keeps
                    // looping. Deterministic, and it needs no numbers from the model. A windowed layer
                    // that does not loop freezes the same way, one window-end earlier: the frames past
                    // it are the ones the window deliberately left out.
                    clip.SetTime(end);
                    clip.SetSpeed(0);
                }
            }
        }

        /// <summary>One seam group's ramp at the current time: 0 before it starts, rising to 1 across its
        /// own length, 1 after. The offset is what staggers the body — a group that has less distance to
        /// cover starts later and therefore arrives late. A zero length is a hard cut, which is what a
        /// `direct` seam asks for and what every group of one gets.</summary>
        private float Ramp(StepSpec spec, SeamLayer layer)
        {
            double since = _elapsed - spec.StartAtSeconds - layer.OffsetSeconds;
            if (since <= 0) return 0f;
            if (layer.BlendInSeconds <= 0.0001) return 1f;
            return Mathf.Clamp01((float)(since / layer.BlendInSeconds));
        }

        /// <summary>Step k's share of the visible pose, under the cascade. Report only — see Update.</summary>
        private float Presence(int k)
        {
            if (k < 0 || k >= _steps.Count) return 0f;
            float next = k + 1 < _steps.Count ? _steps[k + 1].Presence : 0f;
            return _steps[k].Presence * (1f - next);
        }

        /// <summary>A step's per-channel seam schedule, or null when it crosses whole.
        ///
        /// A partial one is refused rather than padded. A group list that never mentions a channel would
        /// leave that body part on the outgoing step for the rest of the plan — a head still walking
        /// while the rest of her types — and it would do it silently, since every channel that WAS
        /// mentioned would look right.
        /// </summary>
        private static List<ChannelBlend> SeamGroups(StepSpec spec)
        {
            if (spec.ChannelBlends == null || spec.ChannelBlends.Count == 0) return null;
            HashSet<string> covered = new HashSet<string>();
            for (int i = 0; i < spec.ChannelBlends.Count; i++)
            {
                List<string> channels = spec.ChannelBlends[i].Channels;
                for (int j = 0; channels != null && j < channels.Count; j++) covered.Add(channels[j]);
            }
            foreach (KeyValuePair<string, AvatarMaskBodyPart[]> entry in ChannelParts)
            {
                if (!covered.Contains(entry.Key))
                {
                    throw new AgentRequestException(Protocol.Err.BadRequest,
                        "step '" + spec.ActionId + "' has a per-channel seam that never brings in '"
                        + entry.Key + "'; every channel has to arrive, or that body part keeps the "
                        + "previous step's pose for the rest of the plan");
                }
            }
            return spec.ChannelBlends;
        }

        private AvatarMask MaskFor(List<string> channels)
        {
            channels.Sort();
            string key = string.Join(",", channels.ToArray());
            AvatarMask mask;
            if (_maskCache.TryGetValue(key, out mask) && mask != null) return mask;

            mask = new AvatarMask();
            foreach (AvatarMaskBodyPart part in System.Enum.GetValues(typeof(AvatarMaskBodyPart)))
            {
                if (part != AvatarMaskBodyPart.LastBodyPart) mask.SetHumanoidBodyPartActive(part, false);
            }
            for (int i = 0; i < channels.Count; i++)
            {
                AvatarMaskBodyPart[] parts;
                if (ChannelParts.TryGetValue(channels[i], out parts))
                {
                    for (int p = 0; p < parts.Length; p++) mask.SetHumanoidBodyPartActive(parts[p], true);
                }
            }
            _maskCache[key] = mask;
            return mask;
        }

        private void Teardown()
        {
            if (_graph.IsValid()) _graph.Destroy();
            _steps.Clear();
            _built = false;
            _elapsed = 0;
            ClearRootMotion();
        }

        private void OnDestroy()
        {
            // A persistent NativeArray outlives the component unless it is said so. The job holds a
            // copy of the handle, not the memory, and the graph is already gone by here.
            if (_rootMotion.IsCreated) _rootMotion.Dispose();
        }

        private void ClearRootMotion()
        {
            if (!_rootMotion.IsCreated) return;
            for (int i = 0; i < _rootMotion.Length; i++) _rootMotion[i] = 0f;
        }

        // ---- wire shapes -----------------------------------------------------------------------

        public sealed class LayerSpec
        {
            public string ActionId;
            public AnimationClip Clip;
            public List<string> Channels;
            public bool HoldFinalPose;

            /// <summary>How much of this layer shows through, inside its mask. 1 replaces what is under
            /// it; anything less MIXES with it, which is how one channel comes to have two sources.
            ///
            /// A layer mixer is cumulative, so only the overlay carries a weight: at 0.4 the result is
            /// 0.6 of the base and 0.4 of this, on the channels this layer's mask reaches and nowhere
            /// else. That is why the split is expressed as one number rather than a pair.
            ///
            /// Where it comes from: agent/assemble.py, for a channel that two overlays in the plan both
            /// name and that the plan pins to no scene object — half each since ADR 0022. Nothing here
            /// decides it — same split that keeps the channel partition and the seam schedule on the
            /// agent side.
            /// </summary>
            public float Weight = 1f;

            /// <summary>Which second of its own clip this layer enters on, when that differs from the
            /// step's. Null means the step's, which is what every layer wanted while a step had one
            /// source per channel.
            ///
            /// Two clips mixed on one channel at their own arbitrary phases average two unrelated
            /// poses. The offset that puts them at their closest is searched agent-side, per channel,
            /// by the same pose distance the seam search uses.
            /// </summary>
            public double? ClipStartSeconds;

            /// <summary>Which second of its own clip this layer STOPS on. Null means the clip's end,
            /// which is what every layer wanted while a layer meant a whole clip.
            ///
            /// This is what lets an overlay contribute a PART of its clip. `cpr` is thirty chest
            /// compressions over eighteen seconds; walking while doing one of them wants the one, not
            /// the thirty, and the same clip's useful right-hand span is 18 frames long. Which frames
            /// those are is measured agent-side from the frozen pose dumps — see agent/segments.py —
            /// so nothing here chooses them, and a layer with no window behaves exactly as before.
            /// </summary>
            public double? ClipEndSeconds;

            /// <summary>Whether reaching <see cref="ClipEndSeconds"/> wraps back to the start of the
            /// window instead of freezing there.
            ///
            /// Decided agent-side, where the window's two ends were actually measured: a window that
            /// is one repetition joins to itself (0.00–0.37 degrees apart across this corpus) and a
            /// window that is merely the moving part of a one-shot gesture does not. Inferring it here
            /// would mean guessing at a number the other side already has.
            /// </summary>
            public bool LoopInWindow;

            /// <summary>Whether this layer's root motion is APPLIED to the transform instead of
            /// discarded. Protocol v5.
            ///
            /// Discarding it is right for almost everything here and it is the only thing this
            /// composer has ever done: every clip plays in place while the NavMeshAgent owns where
            /// the character is, so a walk cycle that also moved her would cover the ground twice.
            ///
            /// A RETRIEVED POSTURE TRANSITION IS THE EXCEPTION, and it is not a small one. A sit-down
            /// clip works by stepping backwards and lowering onto what is behind you -- measured on
            /// `mx_Standing_To_Sitting_Transition`, the hips travel 0.446 m -- so discarding that
            /// leaves the feet sliding through a step they are visibly taking and the hips finishing
            /// where they started, in front of the chair rather than on it. The plan works out where
            /// she has to stand for this clip's own travel to end on the seat (see
            /// `scene.standing_point_for`); applying the travel is the other half of that bargain.
            ///
            /// Set agent-side, per layer, and only on a bridge the agent chose in `then[].via`.
            /// </summary>
            public bool ApplyRootMotion;
        }

        /// <summary>One group of channels that cross a seam together, and when. `OffsetSeconds` is
        /// measured from the step's own start, so a group is late by construction rather than by
        /// arithmetic done here. Which channels share a group, and what the offsets are, come off the
        /// wire — they are the measured per-channel pose distance at the seam, and nothing in the engine
        /// is in a position to work them out.</summary>
        public sealed class ChannelBlend
        {
            public List<string> Channels;
            public double OffsetSeconds;
            public double BlendInSeconds;
        }

        /// <summary>One entry on the timeline. All timing is computed agent-side from the seam table.</summary>
        public sealed class StepSpec
        {
            public string ActionId;
            public List<LayerSpec> Layers;
            public double StartAtSeconds;
            public double BlendInSeconds;        // the whole handover; ChannelBlends is how it is spent
            public double ClipStartSeconds;
            public double? DurationSeconds;      // null = plays to the end of its clips, or loops
            public bool Loop;
            public List<ChannelBlend> ChannelBlends;

            /// <summary>Whether this step drives the transform from its own root motion. True when any
            /// of its layers says so -- see <see cref="LayerSpec.ApplyRootMotion"/>.</summary>
            public bool ApplyRootMotion;
        }

        /// <summary>Where one seam group sits in the sequence mixer, and the ramp it runs.</summary>
        private sealed class SeamLayer
        {
            public readonly int Input;
            public readonly double OffsetSeconds;
            public readonly double BlendInSeconds;

            public SeamLayer(int input, double offset, double blendIn)
            {
                Input = input;
                OffsetSeconds = offset;
                BlendInSeconds = blendIn;
            }
        }

        private sealed class StepRuntime
        {
            public readonly StepSpec Spec;
            public AnimationLayerMixerPlayable Layers;
            public readonly List<AnimationClipPlayable> Clips = new List<AnimationClipPlayable>();
            public readonly List<bool> HoldFinal = new List<bool>();
            public readonly List<SeamLayer> Seam = new List<SeamLayer>();
            public float Presence;

            public StepRuntime(StepSpec spec) { Spec = spec; }
        }
    }
}
