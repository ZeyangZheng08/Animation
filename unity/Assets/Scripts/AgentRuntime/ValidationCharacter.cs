using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.Animations.Rigging;
using UnityEngine.Playables;

namespace AgentRuntime
{
    /// <summary>
    /// A hidden duplicate of a character, used to play a plan through before the visible one does.
    ///
    /// WHAT THIS IS FOR. Every geometric check this project has used to be an autopsy. The plan was
    /// committed, the composer played it, and <see cref="GateProbe"/> measured the pose a viewer was
    /// already looking at — so "the pelvis landed 1.5 m from the chair" arrived after the character had
    /// visibly squatted in mid-air. Worse for a plan with a walk in it: she crossed the room first and
    /// the motion she crossed it for turned out to be impossible when she got there. A failed plan was
    /// something you watched.
    ///
    /// So the plan is played here first: same Animator, same Avatar, same MotionComposer with the same
    /// masks and windows, same IK constraints, same PoseSynth descent, standing where the route preview
    /// says she will be standing — with every Renderer switched off and nothing in the scene touched.
    /// The pose is sampled at fixed timestep and judged by the same <see cref="GateEvaluator"/> the
    /// runtime probe uses. Only a pass reaches the visible character.
    ///
    /// FAST, NOT REAL-TIME. A four-second sit takes four seconds to watch and a few milliseconds to
    /// evaluate: the composer's graph is put in manual time mode and stepped by hand, and the two
    /// things that used to advance only on rendered frames — the descent's closed loop and the IK
    /// weight ramps — grew a `Step(dt)` for the same reason. Waiting out the animation would put the
    /// length of the motion inside the answer to "does this work", which is longer than the whole turn
    /// should be.
    ///
    /// WHAT IT DOES NOT COVER, said plainly rather than implied. Carried props are not attached — the
    /// real ones live in the real scene and reparenting them is exactly the visible mutation this
    /// exists to avoid — so a carry is reported as unmeasured rather than as checked. There is no
    /// body-versus-scene collision metric here because there is not one anywhere yet; that is a
    /// separate capability, not something this quietly acquired. And nothing here can know about the
    /// scene changing after the check, which is what the runtime probe is still for.
    ///
    /// ONE METRIC READS DIFFERENTLY HERE, AND IT IS NOT A BUG. The duplicate has no NavMeshAgent, so
    /// nothing translates it: a locomotion clip strides on the spot and `foot_skate` counts the entire
    /// stride as slide. Measured, same corpus: 2.1449 m/s here against 1.5341 m/s through the runtime
    /// probe on a real walk. That metric is reported and never judged, so nothing fails on it today —
    /// but anyone tempted to give it a threshold has to reconcile the two clocks first. See the note
    /// beside it in GateEvaluator.
    /// </summary>
    public sealed class ValidationCharacter
    {
        /// <summary>No plan is worth simulating past this. A composed motion that has not finished in
        /// twelve seconds is looping, and a loop's second cycle says nothing its first did not.</summary>
        private const float MaxHorizonSeconds = 12f;

        /// <summary>A backstop, not a budget. At the sampling rate below this is longer than the
        /// horizon, so hitting it means something computed a step of zero.</summary>
        private const int MaxSamples = 2000;

        /// <summary>Floor on the sampling rate. Raised per plan to whatever the fastest clip in it
        /// runs at, because a step coarser than the animation's own frame interval can walk straight
        /// past the frame where a foot goes through the floor.</summary>
        private const float MinSampleRate = 60f;

        private readonly AgentCharacter _source;
        private GameObject _root;
        private Animator _animator;
        private MotionComposer _composer;
        private PoseSynth _synth;
        private IkBinder _ik;
        private RigBuilder _rig;
        private readonly Dictionary<string, Transform> _effectorBones =
            new Dictionary<string, Transform>();
        private string _why;

        public ValidationCharacter(AgentCharacter source)
        {
            _source = source;
        }

        /// <summary>Throw the duplicate away. Called when the character stops, so a domain reload or a
        /// scene change does not leave an invisible nurse standing in the room.</summary>
        public void Dispose()
        {
            if (_root != null) Object.Destroy(_root);
            _root = null;
            _composer = null;
        }

        // ---- building ---------------------------------------------------------------------------

        /// <summary>Build the duplicate if it does not exist. False, with <see cref="Why"/> set, when
        /// this character cannot be duplicated at all.</summary>
        private bool Ensure()
        {
            if (_root != null && _composer != null) return true;
            _why = null;

            GameObject source = _source.gameObject;
            // Instantiated ACTIVE, and stripped in the same call. Awake and OnEnable run during
            // Instantiate and cannot be avoided without deactivating the original — which would fire
            // MotionComposer.OnDisable on the visible character and tear down the motion it is
            // playing. Start and Update have not run yet at this point, so disabling the foreign
            // components here is enough: NurseAnimatorEvents, for one, holds a reference to the real
            // medicine bottle and hides it from Update.
            _root = Object.Instantiate(source, source.transform.position, source.transform.rotation);
            _root.name = source.name + " (validation)";
            _root.hideFlags = HideFlags.DontSave;

            // Nothing on the copy may act on the world, drive navigation, or answer for the character.
            DestroyAll<AgentCharacter>();
            DestroyAll<Locomotion>();
            DestroyAll<GateProbe>();
            DestroyAll<UnityEngine.AI.NavMeshAgent>();
            DestroyAll<Collider>();
            DestroyAll<AudioSource>();
            DestroyAll<Camera>();

            // Everything else that is not ours and not the rig: the prop visibility driver, the legacy
            // IK helper, test drivers. Disabled rather than destroyed, because some of them are
            // referenced by things we keep and a null there would be a second problem.
            MonoBehaviour[] behaviours = _root.GetComponentsInChildren<MonoBehaviour>(true);
            for (int i = 0; i < behaviours.Length; i++)
            {
                MonoBehaviour mb = behaviours[i];
                if (mb == null) continue;
                string ns = mb.GetType().Namespace ?? "";
                if (ns == "AgentRuntime" || ns.StartsWith("UnityEngine.Animations.Rigging")) continue;
                mb.enabled = false;
            }

            Renderer[] renderers = _root.GetComponentsInChildren<Renderer>(true);
            for (int i = 0; i < renderers.Length; i++) renderers[i].enabled = false;

            _animator = _root.GetComponent<Animator>();
            _composer = _root.GetComponent<MotionComposer>();
            _synth = _root.GetComponent<PoseSynth>();
            _ik = _root.GetComponent<IkBinder>();
            _rig = _root.GetComponent<RigBuilder>();

            if (_animator == null || _composer == null)
            {
                _why = "the character has no Animator or no MotionComposer to duplicate";
                Dispose();
                return false;
            }
            // A renderer that is off would otherwise let Unity skip the update entirely, and then the
            // bones this reads would never move.
            _animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;

            if (_rig != null)
            {
                // Take the rig out of its own graph and splice it into the composer's -- see
                // MotionComposer.ExtendGraph for the measurement that made this necessary. After
                // Clear() the RigBuilder's Update is a no-op (its graph is gone), so the only thing
                // that advances the constraints is our own evaluation, in the right order.
                _rig.Clear();
                _composer.ExtendGraph = (graph, input) => _rig.BuildPreviewGraph(graph, input);
            }

            // Cached before anything could be parented under a hand, for the same reason
            // AgentCharacter caches its own: a foreign child under a hand bone makes GetBoneTransform
            // return null for EVERY humanoid bone.
            _effectorBones.Clear();
            _effectorBones["left_hand"] = _animator.isHuman
                ? _animator.GetBoneTransform(HumanBodyBones.LeftHand) : null;
            _effectorBones["right_hand"] = _animator.isHuman
                ? _animator.GetBoneTransform(HumanBodyBones.RightHand) : null;
            return true;
        }

        private void DestroyAll<T>() where T : Component
        {
            T[] found = _root.GetComponentsInChildren<T>(true);
            for (int i = 0; i < found.Length; i++)
            {
                if (found[i] != null) Object.DestroyImmediate(found[i]);
            }
        }

        // ---- running ----------------------------------------------------------------------------

        /// <summary>Play `plan` through on the duplicate, standing at `at` and facing `facing`, and
        /// report what the gate made of it.
        ///
        /// `inherited` is the correction the visible character is carrying — a generated sit lives in
        /// there rather than in any clip, so a plan committed while she is seated has to be checked
        /// from seated and not from a stance she is not in.
        /// </summary>
        public Dictionary<string, object> Run(AgentCharacter.CompiledPlan plan,
                                              SceneQueryService scene, Vector3 at, Quaternion facing,
                                              MotionComposer.Correction inherited, float groundY)
        {
            // A CHECK THAT MEASURED NOTHING MUST NOT READ AS A PASS. Without a registry there are no
            // seats, no anchors and no objects, so every geometric metric would simply be absent and
            // the verdict would come back clean about a plan nobody looked at. That is the exact shape
            // of the failure this path was built to remove, so it is an error rather than a pass.
            if (scene == null || scene.Registry == null)
            {
                throw new AgentRequestException(Protocol.Err.NotReady,
                    "no SceneRegistry is wired, so this plan cannot be checked against anything. "
                    + "Nothing was played — a motion that has not been verified is not committed.");
            }
            if (!Ensure())
            {
                throw new AgentRequestException(Protocol.Err.NotReady,
                    "this plan cannot be checked before it plays: " + _why + ". Nothing was played — "
                    + "a motion that has not been verified is not committed.");
            }

            float t0 = Time.realtimeSinceStartup;
            _root.transform.SetPositionAndRotation(at, facing);
            _root.SetActive(true);

            // The clock is ours from here. Prepare carries the previous run's correction across the
            // rebuild, which is right for the visible character and wrong here — what this run must
            // start from is what the VISIBLE character is carrying now.
            _composer.SetManualTime(true);
            _composer.Prepare(plan.Steps);
            _composer.SetCorrection(inherited);
            _composer.Play();
            if (_ik != null) _ik.ReleaseAll();

            // The same bindings the commit will apply, resolved by the same code and against the same
            // real scene anchors — including the refusal when a plan aims two hands at an object that
            // says where only one goes. Nothing is attached: `attachCarried` is false, so a carried
            // prop stays where it is in the real scene.
            List<object> bindings = _source.DescribeBindings(plan.Request, scene, _ik, false);

            GateEvaluator gate = new GateEvaluator();
            GateArming.Arm(gate, plan.Request, scene, bindings, _animator, _ik, _effectorBones,
                           groundY);

            string endsPosture = plan.LastPosture ?? plan.FirstPosture ?? "standing";
            Transform hips = _animator.isHuman
                ? _animator.GetBoneTransform(HumanBodyBones.Hips) : null;
            ArmSupport(gate, plan, scene, hips, endsPosture);

            List<float> marks = new List<float>();
            float step;
            float horizon = Horizon(plan, marks, out step);
            AddContactMarks(plan.Request, marks);
            marks.Sort();

            bool descentStarted = false, landed = false;
            float elapsed = 0f;
            int samples = 0, mark = 0;
            // WHERE THE SKELETON ACTUALLY WAS, first sample and last. A verdict that says a foot went
            // through the floor and cannot say how far down the foot was leaves the reader guessing
            // whether the motion is wrong or the duplicate was assembled wrong -- and the two have very
            // different fixes. Cheap: two reads of two transforms.
            Dictionary<string, object> firstPose = null, lastPose = null;
            float hipsLow = float.MaxValue, hipsHigh = float.MinValue;
            Transform leftFoot = _animator.isHuman
                ? _animator.GetBoneTransform(HumanBodyBones.LeftFoot) : null;
            Transform rightFoot = _animator.isHuman
                ? _animator.GetBoneTransform(HumanBodyBones.RightFoot) : null;
            while (elapsed < horizon && samples < MaxSamples)
            {
                // Never step past a moment that matters. Uniform sampling alone can walk straight over
                // a handover or the instant a contact falls due, and those are exactly the frames a
                // composed motion goes wrong on.
                float dt = step;
                while (mark < marks.Count && marks[mark] <= elapsed + 1e-4f) mark++;
                if (mark < marks.Count && marks[mark] < elapsed + step)
                {
                    dt = Mathf.Max(1e-4f, marks[mark] - elapsed);
                }

                // The order a real frame runs in: Update sets the constraint weights and targets, the
                // animation phase writes the pose, LateUpdate reads it back and corrects. Getting this
                // wrong would make the fast replay a different motion from the slow one.
                if (_ik != null) _ik.Step(dt);
                // Constraint data is read off the scene into the stream here, which is what
                // RigBuilder does from its own Update when it owns a graph. Ours does not, so it has
                // to happen before every evaluation or the constraints solve against last step's
                // targets.
                if (_rig != null) _rig.UpdatePreviewGraph(_composer.Graph);
                _composer.Evaluate(dt);

                elapsed += dt;
                samples++;

                if (plan.Pending != null && !descentStarted && elapsed >= plan.Pending.AtSeconds)
                {
                    BeginDescent(plan, scene, endsPosture);
                    descentStarted = true;
                }
                if (_synth != null) _synth.Step(dt);
                if (descentStarted && !landed && (_synth == null || !_synth.Running))
                {
                    gate.SupportLanded(_synth == null ? 0f : _synth.BiasM,
                                       _synth == null ? 0 : _synth.DroppedWrites,
                                       _synth == null ? 0 : _synth.SaturatedFrames);
                    landed = true;
                }
                // A seated step that was retrieved rather than generated: the clip needs a moment to
                // establish the pose before there is anything to measure, which is the same half
                // second JudgeSupport waits at runtime.
                if (plan.Pending == null && !landed && plan.PendingSupport != null && elapsed >= 0.5f)
                {
                    gate.SupportLanded();
                    landed = true;
                }

                gate.Sample(elapsed, dt);
                lastPose = Pose(elapsed, leftFoot, rightFoot, hips);
                if (firstPose == null) firstPose = lastPose;
                if (hips != null)
                {
                    hipsLow = Mathf.Min(hipsLow, hips.position.y);
                    hipsHigh = Mathf.Max(hipsHigh, hips.position.y);
                }
            }

            Dictionary<string, object> report = gate.Report(elapsed, elapsed);
            List<object> failures = GateEvaluator.Failures(report);

            // A check that could not be answered is not a check that passed. `pending` here does not
            // mean "come back later" the way it does at runtime — this ran the whole plan — it means
            // the plan declared something the run never reached, which is a defect in the plan or in
            // the horizon, and either way it is not a pass.
            List<string> unmeasured = new List<string>((List<string>)report["pending"]);
            JArray carry = plan.Request.Arr("carry");
            if (carry != null && carry.Count > 0)
            {
                unmeasured.Add("carry: props are not attached to the duplicate, so where a carried "
                               + "object ends up is not checked here");
            }

            Cleanup();

            string status = failures.Count > 0 ? "fail" : "pass";
            return new Dictionary<string, object>
            {
                { "mode", "validate" },
                { "status", status },
                { "failures", failures },
                { "checked", GateEvaluator.Checked(report) },
                { "unmeasured", unmeasured },
                { "samples", samples },
                { "seconds_simulated", elapsed },
                { "wall_ms", (Time.realtimeSinceStartup - t0) * 1000f },
                { "at", new float[] { at.x, at.y, at.z } },
                { "facing_deg", facing.eulerAngles.y },
                { "resolved", plan.Resolved },
                { "bindings", bindings },
                { "ground_y", groundY },
                { "first_pose", firstPose },
                { "last_pose", lastPose },
                // A range of zero over a whole plan means the graph never advanced, which is a very
                // different problem from a motion that happens to hold still. Two endpoints cannot
                // tell those apart; this can.
                { "hips_range_m", hipsHigh > hipsLow ? hipsHigh - hipsLow : 0f },
                { "rig_spliced", _rig != null && _composer.ExtendGraph != null },
                { "metrics", report["metrics"] }
            };
        }

        private Dictionary<string, object> Pose(float elapsed, Transform leftFoot,
                                                Transform rightFoot, Transform hips)
        {
            return new Dictionary<string, object>
            {
                { "at_s", elapsed },
                { "root_y", _root.transform.position.y },
                { "left_foot_y", leftFoot == null ? (object)null : leftFoot.position.y },
                { "right_foot_y", rightFoot == null ? (object)null : rightFoot.position.y },
                { "hips_y", hips == null ? (object)null : hips.position.y }
            };
        }

        private void Cleanup()
        {
            if (_synth != null) _synth.Cancel();
            if (_ik != null) _ik.ReleaseAll();
            if (_composer != null) _composer.Stop();
        }

        /// <summary>Tell the gate what the generated posture change claims it will land on, before it
        /// runs — same rule as the runtime path, and only when it ends SEATED. Standing up ends on the
        /// floor, and "did the pelvis land inside the footprint of the chair" is the wrong question
        /// about it.</summary>
        private void ArmSupport(GateEvaluator gate, AgentCharacter.CompiledPlan plan,
                                SceneQueryService scene, Transform hips, string endsPosture)
        {
            if (hips == null || scene == null || scene.Registry == null) return;

            if (plan.Pending != null)
            {
                if (endsPosture != "seated" || string.IsNullOrEmpty(plan.Pending.SupportObjectId))
                {
                    return;
                }
                SceneRegistry.Entry seat = scene.Registry.ById(plan.Pending.SupportObjectId);
                if (seat == null || seat.target == null) return;
                gate.ExpectSupport(plan.Pending.SupportObjectId, seat.target, seat.surfaceHeight, hips,
                                   (float)plan.Pending.AtSeconds + plan.Pending.DurationSeconds + 0.3f,
                                   plan.Pending.StartHipY, plan.Pending.TargetHipY);
                return;
            }

            if (!string.IsNullOrEmpty(plan.PendingSupport))
            {
                SceneRegistry.Entry seat = scene.Registry.ById(plan.PendingSupport);
                if (seat == null || seat.target == null) return;
                gate.ExpectSupport(plan.PendingSupport, seat.target, seat.surfaceHeight, hips, 0.5f);
            }
        }

        /// <summary>Start the descent on the duplicate, aimed at the same place the real one would aim
        /// at: ONTO the support when sitting, and off it to the stand anchor when rising.</summary>
        private void BeginDescent(AgentCharacter.CompiledPlan plan, SceneQueryService scene,
                                  string endsPosture)
        {
            if (_synth == null) return;
            Vector3? landOn = null;
            if (scene != null && scene.Registry != null
                && !string.IsNullOrEmpty(plan.Pending.SupportObjectId))
            {
                SceneRegistry.Entry seat = scene.Registry.ById(plan.Pending.SupportObjectId);
                if (seat != null && seat.target != null)
                {
                    landOn = endsPosture == "standing" && seat.standAnchor != null
                        ? seat.standAnchor.position
                        : seat.target.position;
                }
            }
            _synth.Begin(plan.Pending.TargetHipY, plan.Pending.DurationSeconds, landOn,
                         plan.Pending.LeftFootLocal, plan.Pending.RightFootLocal);
        }

        // ---- how long, and how finely -----------------------------------------------------------

        /// <summary>How far to simulate, the moments that must be sampled exactly, and the step.
        ///
        /// The step is never coarser than the effective frame interval of the clips in the plan,
        /// because a coarser one can step over the single frame where a foot goes through the floor.
        /// Sixty a second is the floor even for a 30 fps clip: the descent and the IK ramps are
        /// continuous and do not care what the animation was authored at.
        /// </summary>
        private static float Horizon(AgentCharacter.CompiledPlan plan, List<float> marks,
                                     out float step)
        {
            float end = 0.5f;
            float fastest = 30f;
            for (int s = 0; s < plan.Steps.Count; s++)
            {
                MotionComposer.StepSpec spec = plan.Steps[s];
                marks.Add((float)spec.StartAtSeconds);
                marks.Add((float)(spec.StartAtSeconds + spec.BlendInSeconds));

                double longest = 0.0;
                for (int i = 0; i < spec.Layers.Count; i++)
                {
                    MotionComposer.LayerSpec layer = spec.Layers[i];
                    if (layer.Clip == null) continue;
                    if (layer.Clip.frameRate > fastest) fastest = layer.Clip.frameRate;
                    double from = layer.ClipStartSeconds ?? spec.ClipStartSeconds;
                    double to = layer.ClipEndSeconds ?? layer.Clip.length;
                    double span = System.Math.Max(0.0, to - from);
                    if (span > longest) longest = span;
                    // Where a windowed layer stops is a moment worth landing on exactly: it is where
                    // it either wraps or freezes, and both are seams.
                    if (layer.ClipEndSeconds.HasValue)
                    {
                        marks.Add((float)(spec.StartAtSeconds + span));
                    }
                }
                if (spec.DurationSeconds.HasValue) longest = spec.DurationSeconds.Value;
                float finishes = (float)(spec.StartAtSeconds + longest);
                marks.Add(finishes);
                if (finishes > end) end = finishes;
            }

            if (plan.Pending != null)
            {
                float begins = (float)plan.Pending.AtSeconds;
                marks.Add(begins);
                marks.Add(begins + plan.Pending.DurationSeconds);
                float settled = begins + plan.Pending.DurationSeconds + 0.3f;
                if (settled > end) end = settled;
            }

            step = 1f / Mathf.Max(MinSampleRate, fastest);
            return Mathf.Min(Mathf.Max(end, 0.2f), MaxHorizonSeconds);
        }

        /// <summary>The instants a contact or a binding falls due. A hand is judged from these, so
        /// stepping over one would judge it from a frame either side of the moment that matters.</summary>
        private static void AddContactMarks(Request request, List<float> marks)
        {
            JArray declared = request.Arr("expect_contact");
            for (int i = 0; declared != null && i < declared.Count; i++)
            {
                marks.Add(((JObject)declared[i]).Value<float?>("due_at_s") ?? 0f);
            }
            JArray bindings = request.Arr("ik");
            for (int i = 0; bindings != null && i < bindings.Count; i++)
            {
                marks.Add(((JObject)bindings[i]).Value<float?>("at_s") ?? 0f);
            }
        }
    }
}
