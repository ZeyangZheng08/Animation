using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AgentRuntime
{
    /// <summary>
    /// One driveable character: turns an assembly plan into a composed, grounded, playing motion.
    ///
    /// The plan arrives with the channel split ALREADY DERIVED on the agent side, from each action's
    /// role table. This side does not arbitrate; it builds masks from the channel lists it is given.
    /// Keeping the rule in one place means the eval runner and the live demo cannot disagree about what
    /// a plan means, and the rule stays unit-testable without an engine.
    ///
    /// PROPS ARE EXPLICIT. NurseAnimatorEvents polls the Animator for "Hold Pills" and hides the
    /// medicine bottle when it is false, and HoldMedicineBottle fires as an animation event on
    /// nurse_grab_bottle. Bypassing the controller means neither happens, so a carried bottle would
    /// simply be invisible. The plan carries a carry list and this parents the object to the hand
    /// directly rather than hoping an animation event fires.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class AgentCharacter : MonoBehaviour
    {
        [SerializeField] private string id = "chr:Nurse";

        [Tooltip("What a person calls her. An instruction says \"Jill\"; the protocol says "
                 + "\"chr:CPRNurse\". Both have to exist or one of them has to be guessed.")]
        [SerializeField] private string displayName = "";
        [SerializeField] private MotionComposer composer;
        [SerializeField] private IkBinder ik;
        [SerializeField] private ClipLibrary clips;
        [SerializeField] private GateProbe gates;
        [SerializeField] private PoseSynth synth;
        [SerializeField] private Locomotion locomotion;

        [Tooltip("Disabled while the composer drives this character, so the two do not both write IK.")]
        [SerializeField] private MonoBehaviour legacyIkHelper;

        private readonly List<Transform> _carried = new List<Transform>();
        private readonly List<Vector3> _carriedLocalPos = new List<Vector3>();

        // Bone transforms are cached ONCE, before anything is attached to the skeleton.
        //
        // Measured on CPRNurse: parenting the carried Aspirin Bottle under the hand bone makes
        // Animator.GetBoneTransform return null for EVERY humanoid bone, and it stays null through a
        // Rebind until the foreign child is removed. Querying live would therefore make CanReach report
        // "cannot reach" for everything from the moment the character picks anything up — silently, and
        // only while carrying, which is the worst possible shape for a bug. Caching at Awake sidesteps
        // it entirely and is cheaper besides.
        private readonly Dictionary<HumanBodyBones, Transform> _bones =
            new Dictionary<HumanBodyBones, Transform>();

        // WHAT SHAPE SHE IS IN RIGHT NOW, which nothing used to record.
        //
        // Every clip in the corpus carries its own posture, so for retrieved motion this is derivable
        // and nobody needed it. A GENERATED posture change is different: it lives in the composer's
        // correction rather than in any clip, and it is the only thing that knows she is no longer
        // standing. Without this the next plan silently rebuilt the graph, the correction went with it,
        // and she stood up — and the walk request that would have dragged her off the chair was
        // accepted too, because nothing could tell she was on one.
        private string _posture = "standing";
        private string _supportObjectId;

        // The hidden duplicate every commit is checked on. Built on first use rather than at Awake:
        // a character nobody drives should not cost a second skeleton, and until a plan arrives there
        // is nothing to check.
        private ValidationCharacter _validation;

        public string Id { get { return id; } }

        /// <summary>Her name, falling back to the object's. Never empty: a character with no name is
        /// one an instruction cannot reach, and the object name is at least something a person could
        /// have typed.</summary>
        public string DisplayName
        {
            get { return string.IsNullOrEmpty(displayName) ? gameObject.name : displayName; }
        }

        public string Posture { get { return _posture; } }
        public string SupportObjectId { get { return _supportObjectId; } }

        private void Awake()
        {
            if (composer == null) composer = GetComponent<MotionComposer>();
            if (ik == null) ik = GetComponent<IkBinder>();
            if (gates == null) gates = GetComponent<GateProbe>();
        }

        // Start, not Awake: the Animator's humanoid mapping is not available yet during Awake, so
        // caching there silently produces an empty table and every reach query answers "no".
        private void Start()
        {
            CacheBones();
        }

        private void CacheBones()
        {
            Animator animator = composer != null ? composer.Animator : GetComponent<Animator>();
            if (animator == null || !animator.isHuman) return;
            HumanBodyBones[] wanted =
            {
                HumanBodyBones.LeftHand, HumanBodyBones.RightHand,
                HumanBodyBones.LeftLowerArm, HumanBodyBones.RightLowerArm,
                HumanBodyBones.LeftUpperArm, HumanBodyBones.RightUpperArm
            };
            for (int i = 0; i < wanted.Length; i++)
            {
                Transform t = animator.GetBoneTransform(wanted[i]);
                if (t != null) _bones[wanted[i]] = t;
            }
        }

        private Transform Bone(HumanBodyBones bone)
        {
            // One retry if the table is empty, for the case where Start has not run yet. Once anything
            // is parented under a hand bone the mapping stops resolving, so this must succeed early or
            // not at all — it never re-caches over a good table.
            if (_bones.Count == 0) CacheBones();
            Transform t;
            return _bones.TryGetValue(bone, out t) ? t : null;
        }

        /// <summary>Turn a request into a motion. Three modes, one derivation.
        ///
        /// THE PLAN IS COMPILED ONCE AND THEN USED, NEVER RE-DERIVED. `validate` and `commit` arrive as
        /// two requests carrying the same bytes, and each is parsed by the same <see cref="Compile"/>
        /// from that same input — so what was checked and what plays cannot be two different plans.
        /// Deriving anything between the check and the play would put the gap back exactly where this
        /// was built to close it.
        ///
        ///     dry_run   resolve everything, touch nothing, report what it would become
        ///     validate  play it through on a hidden duplicate and report the geometry (v4)
        ///     commit    do it, visibly
        ///
        /// The model sees only the first and the last. `validate` is between the agent service and this
        /// executor: the service sends it, reads the verdict, and commits only on a pass, which costs
        /// an engine round trip and no iteration of the model's own loop.
        /// </summary>
        public object Apply(Request request, SceneQueryService scene, AgentLink link)
        {
            CompiledPlan plan = Compile(request);

            // Both of these are about the PLAN rather than about what to do with it, so they answer
            // the same way in every mode. In particular a plan that would snap between postures is
            // refused during `validate`, which is before anything has moved -- it used to be refused at
            // commit, by which time a walk had already happened.
            object already = AlreadyThere(plan);
            if (already != null) return already;
            RefuseCut(plan);

            string mode = request.Str("mode", "dry_run");
            if (mode == "validate") return Validate(plan, scene);
            if (mode == "commit") return Commit(plan, scene, link);

            // A dry run resolves everything and touches nothing, so the agent can see what a plan
            // would become without the character twitching through half-formed states.
            return new Dictionary<string, object>
            {
                { "mode", "dry_run" }, { "steps", plan.Steps.Count }, { "resolved", plan.Resolved },
                { "posture", _posture }, { "ends_posture", plan.LastPosture ?? _posture },
                { "bindings", DescribeBindings(request, scene, null, false) }
            };
        }

        /// <summary>Everything a plan is, derived from the request and nothing else. No component is
        /// touched and no state is written, so this is safe to run for a check as well as for a play.
        /// </summary>
        public CompiledPlan Compile(Request request)
        {
            // v2 sends `steps`; a request with only `layers` is one step starting at zero, which is
            // exactly what v1 meant. Normalising here rather than branching later keeps a sequence and
            // a single action on the same path through the executor.
            JArray stepSpecs = request.Arr("steps");
            if (stepSpecs == null || stepSpecs.Count == 0)
            {
                JArray flat = request.Arr("layers");
                if (flat == null || flat.Count == 0)
                {
                    throw new AgentRequestException(Protocol.Err.BadRequest, "the plan has no layers");
                }
                JObject wrapper = new JObject();
                wrapper["layers"] = flat;
                stepSpecs = new JArray { wrapper };
            }

            Descent pendingDescent = null;
            string pendingSupport = null;
            // Each step says what posture its clip is in — derived agent-side from the knowledge base,
            // not guessed here. The first decides whether this plan can even start from where she is;
            // the last is what she will be in when it ends.
            string firstPosture = null, lastPosture = null;
            List<MotionComposer.StepSpec> steps = new List<MotionComposer.StepSpec>();
            List<object> resolved = new List<object>();
            for (int s = 0; s < stepSpecs.Count; s++)
            {
                JObject stepJson = (JObject)stepSpecs[s];
                JArray layerSpecs = stepJson["layers"] as JArray;
                if (layerSpecs == null || layerSpecs.Count == 0)
                {
                    throw new AgentRequestException(Protocol.Err.BadRequest,
                        "step " + s + " has no layers");
                }

                double fps = stepJson.Value<double?>("frame_rate") ?? 30.0;
                int startFrame = stepJson.Value<int?>("clip_start_frame") ?? 0;
                List<MotionComposer.LayerSpec> layers = new List<MotionComposer.LayerSpec>();

                for (int i = 0; i < layerSpecs.Count; i++)
                {
                    JObject spec = (JObject)layerSpecs[i];
                    string actionId = spec.Value<string>("action_id");
                    JObject clipRef = spec["clip"] as JObject;
                    AnimationClip clip = clips == null ? null : clips.Resolve(
                        actionId, clipRef == null ? null : clipRef.Value<string>("clip_name"));

                    List<string> channels = new List<string>();
                    JArray chans = spec["channels"] as JArray;
                    if (chans != null)
                    {
                        for (int j = 0; j < chans.Count; j++) channels.Add(chans[j].Value<string>());
                    }

                    // A share below 1 means this layer MIXES with what is under it on the channels its
                    // mask reaches, instead of replacing them. Absent means 1, which is every layer
                    // written before one channel could have two sources. The phase is separate because
                    // a mix at unrelated phases averages two unrelated poses; absent, it is the step's.
                    float weight = spec.Value<float?>("weight") ?? 1f;
                    int? layerFrame = spec.Value<int?>("clip_start_frame");
                    // Where this layer STOPS, when it contributes only part of its clip. Absent means
                    // the clip's own end, which is every layer written before an overlay could be one
                    // repetition out of thirty. Whether reaching it wraps or freezes was decided where
                    // the window's two ends were measured, not here.
                    int? layerEndFrame = spec.Value<int?>("clip_end_frame");

                    layers.Add(new MotionComposer.LayerSpec
                    {
                        ActionId = actionId,
                        Clip = clip,
                        Channels = channels,
                        HoldFinalPose = spec.Value<bool?>("hold_final_pose") ?? false,
                        Weight = weight,
                        ClipStartSeconds = layerFrame.HasValue ? layerFrame.Value / fps : (double?)null,
                        ClipEndSeconds = layerEndFrame.HasValue
                            ? layerEndFrame.Value / fps : (double?)null,
                        LoopInWindow = spec.Value<bool?>("loop_in_window") ?? false
                    });
                    Dictionary<string, object> line = new Dictionary<string, object>
                    {
                        { "step", s },
                        { "action_id", actionId },
                        { "clip", clip == null ? null : clip.name },
                        { "channels", channels },
                        { "ok", clip != null }
                    };
                    // Reported only when it is not the default, so a plan that mixes says so and every
                    // plan that does not reads exactly as it did before.
                    if (weight < 1f) line["weight"] = weight;
                    if (layerFrame.HasValue) line["clip_start_frame"] = layerFrame.Value;
                    if (layerEndFrame.HasValue) line["clip_end_frame"] = layerEndFrame.Value;
                    resolved.Add(line);
                }

                // How the handover is spent per channel. Absent means cross whole, which is what the
                // opening step and a hard cut both want; present, it has to name every channel, and the
                // composer refuses it otherwise rather than leaving a body part behind.
                List<MotionComposer.ChannelBlend> channelBlends = null;
                JArray blendSpecs = stepJson["channel_blends"] as JArray;
                if (blendSpecs != null && blendSpecs.Count > 0)
                {
                    channelBlends = new List<MotionComposer.ChannelBlend>();
                    for (int i = 0; i < blendSpecs.Count; i++)
                    {
                        JObject group = (JObject)blendSpecs[i];
                        List<string> groupChannels = new List<string>();
                        JArray names = group["channels"] as JArray;
                        for (int j = 0; names != null && j < names.Count; j++)
                        {
                            groupChannels.Add(names[j].Value<string>());
                        }
                        channelBlends.Add(new MotionComposer.ChannelBlend
                        {
                            Channels = groupChannels,
                            OffsetSeconds = group.Value<double?>("offset_s") ?? 0.0,
                            BlendInSeconds = group.Value<double?>("blend_in_s") ?? 0.0
                        });
                    }
                }

                steps.Add(new MotionComposer.StepSpec
                {
                    ActionId = stepJson.Value<string>("action_id"),
                    Layers = layers,
                    StartAtSeconds = stepJson.Value<double?>("start_at_s") ?? 0.0,
                    BlendInSeconds = stepJson.Value<double?>("blend_in_s") ?? 0.0,
                    ClipStartSeconds = startFrame / fps,
                    DurationSeconds = stepJson.Value<double?>("duration_s"),
                    Loop = stepJson.Value<bool?>("loop") ?? false,
                    ChannelBlends = channelBlends
                });

                string posture = stepJson.Value<string>("posture");
                if (!string.IsNullOrEmpty(posture))
                {
                    if (firstPosture == null) firstPosture = posture;
                    lastPosture = posture;
                }

                // A seated step played on its own still names what it expects to be sitting on. Without
                // this the gate has nothing to judge and a character seated in mid-air passes every
                // check, which is what happened.
                JObject expect = stepJson["expect_support"] as JObject;
                if (expect != null)
                {
                    pendingSupport = expect.Value<string>("object_id");
                }

                // A step may declare that reaching it needs frames no clip contains. The target hip
                // height comes from the knowledge base -- it is the pose this step's own clip opens on
                // -- so nothing here is invented engine-side.
                JObject generated = stepJson["generated"] as JObject;
                if (generated != null)
                {
                    pendingDescent = new Descent
                    {
                        AtSeconds = stepJson.Value<double?>("start_at_s") ?? 0.0,
                        StartHipY = generated.Value<float?>("start_hip_height_m") ?? -1f,
                        TargetHipY = generated.Value<float?>("target_hip_height_m") ?? -1f,
                        DurationSeconds = generated.Value<float?>("duration_s") ?? 0.8f,
                        Kind = generated.Value<string>("kind"),
                        SupportObjectId = generated.Value<string>("support_object_id"),
                        LeftFootLocal = LocalPoint(generated["foot_targets"] as JObject, "left"),
                        RightFootLocal = LocalPoint(generated["foot_targets"] as JObject, "right")
                    };
                }
            }

            return new CompiledPlan
            {
                Request = request,
                Steps = steps,
                Resolved = resolved,
                FirstPosture = firstPosture,
                LastPosture = lastPosture,
                Pending = pendingDescent,
                PendingSupport = pendingSupport
            };
        }

        /// <summary>The "nothing to do" answer, or null.
        ///
        /// ALREADY THERE IS NOT A REFUSAL. A plan that ends where she already is asks for nothing, and
        /// answering it with the stand-up refusal below describes the wrong problem: measured on a real
        /// turn, the model committed a walk-and-sit, it played, it committed the same plan again a
        /// second later, and was told the library has no clip for standing up. It concluded the request
        /// could not be done and said so to the user, while she was sitting in the chair typing. The
        /// refusal was true about the first step and false about the request.
        /// </summary>
        private object AlreadyThere(CompiledPlan plan)
        {
            if (_posture != "seated" || plan.FirstPosture != "standing"
                || (plan.LastPosture ?? "standing") != "seated" || plan.Pending == null
                || plan.Pending.SupportObjectId != _supportObjectId)
            {
                return null;
            }
            return new Dictionary<string, object>
            {
                { "already", true },
                { "posture", _posture },
                { "sitting_on", _supportObjectId },
                { "note", id + " is already seated on " + _supportObjectId + ", which is where "
                          + "this plan ends. Nothing was replayed; the motion you committed is the "
                          + "one running." }
            };
        }

        private void RefuseCut(CompiledPlan plan)
        {
            // A PLAN MUST NOT CUT BETWEEN POSTURES, IN EITHER DIRECTION. Opening in a posture she is
            // not in means arriving at it in one frame: the pose it lands in is correct, so every
            // geometric check passes, and nothing measures that she teleported into it.
            //
            // WHICH DIRECTION IS NOT THE QUESTION. This used to be two rules — sitting down had to be
            // generated, standing up was refused outright because "the library has no clip for standing
            // up". That is true of both directions equally: the corpus has one seated action, so every
            // route between sitting and standing crosses a gap nothing in it covers, and the machinery
            // that makes the frames was symmetric from the start (`schedule` derives the hip travel as
            // an absolute value, and PoseSynth's clamp runs both ways). What was asymmetric was this
            // refusal. So the rule is now the one rule it always was: a posture change needs generated
            // frames, and a plan that declares them may play.
            if (plan.FirstPosture != null && plan.FirstPosture != _posture && plan.Pending == null)
            {
                throw new AgentRequestException(Protocol.Err.ExecFailed,
                    "this plan starts " + plan.FirstPosture + " while " + id + " is " + _posture
                    + ", so she would snap between the two with nothing in between. Name an action in "
                    + "her current posture as `base` and the other one in `then`, in one call — that "
                    + "is what makes the frames between them"
                    + (plan.FirstPosture == "seated" ? ", and `sit_on` says what to sit on." : "."));
            }
        }

        /// <summary>Play the plan through on a hidden duplicate and report what the gate made of it.
        ///
        /// `at` says where to stand the duplicate: the arrival a route preview projected for a walk
        /// that has not happened yet. Absent, it is checked where she is. Checking a sit from across
        /// the room would fail a plan that works, and checking a walk-and-sit at the seat would pass
        /// one whose walk cannot happen — which is why the preview and this take the same route.
        /// </summary>
        private object Validate(CompiledPlan plan, SceneQueryService scene)
        {
            Vector3 at = transform.position;
            Quaternion facing = transform.rotation;
            JObject where = plan.Request.Obj("at");
            if (where != null)
            {
                JArray point = where["position"] as JArray;
                if (point != null && point.Count >= 3)
                {
                    at = new Vector3(point[0].ToObject<float>(), point[1].ToObject<float>(),
                                     point[2].ToObject<float>());
                }
                float? yaw = where.Value<float?>("facing_deg");
                if (yaw.HasValue) facing = Quaternion.Euler(0f, yaw.Value, 0f);
            }

            if (_validation == null) _validation = new ValidationCharacter(this);
            // What she is carrying in her body right now. A generated sit lives in the composer's
            // correction rather than in any clip, so a plan committed while she is seated has to be
            // checked from seated -- starting the duplicate from a stance she is not in would judge a
            // motion nobody is about to perform.
            MotionComposer.Correction inherited = composer == null
                ? new MotionComposer.Correction() : composer.GetCorrection();
            return _validation.Run(plan, scene, at, facing, inherited, at.y);
        }

        private object Commit(CompiledPlan plan, SceneQueryService scene, AgentLink link)
        {
            Request request = plan.Request;
            List<MotionComposer.StepSpec> steps = plan.Steps;
            List<object> resolved = plan.Resolved;
            string lastPosture = plan.LastPosture;
            Descent pendingDescent = plan.Pending;
            string pendingSupport = plan.PendingSupport;
            float t0 = Time.realtimeSinceStartup;

            TakeOverFromLegacy();
            // EVERY COMMIT STARTS FROM UNBOUND HANDS. ReleaseAll used to be reachable only through
            // StopAll, which nothing called, so an effector bound by one plan stayed bound for the
            // rest of the play session. Measured across two turns: a plan bound both hands to the
            // laptop, and the walk committed a few minutes later played with her arms still reaching
            // back at it. A binding is part of the plan that asked for it and does not outlive it.
            if (ik != null) ik.ReleaseAll();
            composer.Prepare(steps);
            // Prepare carries the previous correction across the rebuild, which is what keeps a
            // generated sit from being discarded. A plan that ends standing has no business inheriting
            // a hip drop, so that is the one case that clears it — explicitly, here, where the posture
            // is known, rather than as a side effect of building a graph.
            if ((lastPosture ?? "standing") == "standing" && _posture == "standing")
            {
                composer.SetCorrection(new MotionComposer.Correction());
            }
            List<object> bindings = DescribeBindings(request, scene, ik, true);
            composer.Play();
            StartGates(request, scene, bindings);
            string endsPosture = lastPosture ?? _posture;
            if (pendingDescent != null)
            {
                // A generated posture change needs somewhere to push off from as much as somewhere to
                // land, and standing up has one only if the plan named it. It is the seat she is on,
                // which this side already knows -- so a rise does not have to be told.
                if (string.IsNullOrEmpty(pendingDescent.SupportObjectId))
                {
                    pendingDescent.SupportObjectId = _supportObjectId;
                }
                StartCoroutine(RunPostureChange(pendingDescent, scene, endsPosture));
            }
            else if (pendingSupport != null) StartCoroutine(JudgeSupport(pendingSupport, scene));

            // Recorded at commit, not when the descent lands: from this moment she is committed to
            // ending up seated, and anything that would drag her off the seat has to be refused for the
            // whole of the motion, not only after it finishes.
            _posture = lastPosture ?? _posture;
            _supportObjectId = _posture == "seated"
                ? (pendingDescent != null ? pendingDescent.SupportObjectId : pendingSupport)
                : null;

            float latencyMs = (Time.realtimeSinceStartup - t0) * 1000f;
            link.Emit(Protocol.MotionStatus, new Dictionary<string, object>
            {
                { "character", id }, { "phase", "started" },
                { "frame", Time.frameCount }, { "engine_time", Time.realtimeSinceStartup }
            });

            return new Dictionary<string, object>
            {
                { "mode", "commit" },
                { "steps", steps.Count },
                { "resolved", resolved },
                { "bindings", bindings },
                { "posture", _posture },
                { "sitting_on", _supportObjectId },
                { "prepare_ms", latencyMs },
                { "frame", Time.frameCount }
            };
        }

        /// <summary>What this plan's bindings resolve to, and — when `binder` is given — applying
        /// them to it.
        ///
        /// TWO CHARACTERS RUN THIS, WHICH IS WHY THE BINDER IS A PARAMETER. The visible one binds its
        /// own IkBinder and attaches carried props; the hidden duplicate binds ITS binder against the
        /// same real scene anchors and attaches nothing, because reparenting the real bottle is exactly
        /// the visible mutation a pre-execution check exists to avoid. Both go through this one method
        /// so the refusals cannot differ — including the one below about aiming two hands at an object
        /// that says where only one goes, which changes the pose that gets judged.
        /// </summary>
        internal List<object> DescribeBindings(Request request, SceneQueryService scene,
                                               IkBinder binder, bool attachCarried)
        {
            List<object> out_ = new List<object>();
            if (scene == null || scene.Registry == null) return out_;

            JArray ikBindings = request.Arr("ik");
            if (ikBindings != null)
            {
                // TWO HANDS CANNOT BE AT ONE POINT, and an object has one grab anchor. Asked to type at
                // the laptop, the model bound both hands to it and both wrists were pulled onto the
                // same anchor -- measured, right hand 0.000 m from it and left hand 0.065 m, which is
                // not typing, it is clasping. The clip already had the hand motion; the binding
                // overwrote it with a point. Refused rather than half-applied, because keeping
                // whichever came first would be an arbitrary choice between two hands.
                Dictionary<Transform, int> anchorUse = new Dictionary<Transform, int>();
                for (int i = 0; i < ikBindings.Count; i++)
                {
                    JObject spec = (JObject)ikBindings[i];
                    SceneRegistry.Entry e = scene.Registry.ById(spec.Value<string>("object_id"));
                    if (e == null || e.target == null) continue;
                    Transform anchor = e.HandAnchor(spec.Value<string>("effector"));
                    int seen;
                    anchorUse[anchor] = anchorUse.TryGetValue(anchor, out seen) ? seen + 1 : 1;
                }

                for (int i = 0; i < ikBindings.Count; i++)
                {
                    JObject b = (JObject)ikBindings[i];
                    string effector = b.Value<string>("effector");
                    string objectId = b.Value<string>("object_id");
                    // WHEN THIS HAND COMES DUE. Computed agent-side from the step whose clip declares
                    // the contact, so a hand the walk is animating is left to the walk. Absent means
                    // the first frame, which is what every binding meant before it existed.
                    double at = b.Value<double?>("at_s") ?? 0.0;
                    SceneRegistry.Entry entry = scene.Registry.ById(objectId);
                    bool ok = entry != null && entry.target != null;
                    Transform anchor = ok ? entry.HandAnchor(effector) : null;
                    int users;
                    if (ok && anchorUse.TryGetValue(anchor, out users) && users > 1)
                    {
                        out_.Add(Refusal("ik", effector, objectId, entry,
                            entry.Label + " has one place to put a hand and this plan aims two at it, "
                            + "which would pull both wrists onto the same point. Objects worked with "
                            + "two hands carry a per-hand anchor; this one does not, so bind neither, "
                            + "or bind one hand only."));
                        continue;
                    }
                    if (ok && binder != null) ok = binder.Bind(effector, anchor,
                                                               entry.HintAnchor(effector), at);
                    Dictionary<string, object> binding = (Dictionary<string, object>)
                        Binding("ik", effector, objectId, ok, entry);
                    binding["engages_at_s"] = at;
                    out_.Add(binding);
                }
            }

            string gaze = request.Str("gaze_at");
            if (!string.IsNullOrEmpty(gaze))
            {
                SceneRegistry.Entry entry = scene.Registry.ById(gaze);
                bool ok = entry != null && entry.target != null;
                double at = request.Float("gaze_at_s", 0f);
                if (ok && binder != null) ok = binder.BindGaze(entry.target, at);
                Dictionary<string, object> binding = (Dictionary<string, object>)
                    Binding("gaze", "head", gaze, ok, entry);
                binding["engages_at_s"] = at;
                out_.Add(binding);
            }

            JArray carry = request.Arr("carry");
            if (carry != null)
            {
                if (attachCarried) ReleaseCarried();
                for (int i = 0; i < carry.Count; i++)
                {
                    JObject c = (JObject)carry[i];
                    string objectId = c.Value<string>("object_id");
                    string hand = c.Value<string>("hand");
                    SceneRegistry.Entry entry = scene.Registry.ById(objectId);
                    bool ok = entry != null && entry.target != null;

                    // FIXTURES ARE NOT CARRIED. `carry` parents an object to a hand bone, so it
                    // follows the hand for the rest of the motion. Asked to type at a desk, the model
                    // carried the laptop: it left the desk, hung off her wrist, and she sat holding it
                    // in her lap. Nothing in the tool said no, because "attach this to a hand" is
                    // exactly what it was asked to do. A bed, a chair, a table, a laptop resting on a
                    // desk are things a motion REACHES; only something small enough to pick up is
                    // something it CARRIES. Which is which is authored in the registry rather than
                    // inferred: the first version tested the category, and `device` covers both the bag
                    // valve mask she holds in both hands and the laptop she types on where it sits.
                    if (ok && !entry.carriable)
                    {
                        ok = false;
                        out_.Add(Refusal("carry", hand, objectId, entry,
                            entry.Label + " is used where it stands and is not annotated as carriable. "
                            + "Bind a hand to it with ik_bindings to touch it; carry is for something "
                            + "picked up and taken along, like the pill bottle or the bag valve mask."));
                        continue;
                    }
                    if (ok && attachCarried) ok = Attach(entry.target, hand);
                    out_.Add(Binding("carry", hand, objectId, ok, entry));
                }
            }
            return out_;
        }

        private static object Refusal(string kind, string effector, string objectId,
                                      SceneRegistry.Entry entry, string why)
        {
            Dictionary<string, object> b = (Dictionary<string, object>)
                Binding(kind, effector, objectId, false, entry);
            b["refused"] = why;
            return b;
        }

        private static object Binding(string kind, string effector, string objectId, bool ok,
                                      SceneRegistry.Entry entry)
        {
            return new Dictionary<string, object>
            {
                { "kind", kind }, { "effector", effector }, { "object_id", objectId },
                { "resolved_to", entry == null ? null : entry.Label }, { "ok", ok }
            };
        }

        private bool Attach(Transform target, string hand)
        {
            Transform bone = Bone(hand == "left_hand" ? HumanBodyBones.LeftHand
                                                      : HumanBodyBones.RightHand);
            if (bone == null) return false;

            _carried.Add(target);
            _carriedLocalPos.Add(target.position);
            target.SetParent(bone, true);
            target.gameObject.SetActive(true);
            return true;
        }

        private void ReleaseCarried()
        {
            for (int i = 0; i < _carried.Count; i++)
            {
                if (_carried[i] == null) continue;
                _carried[i].SetParent(null, true);
                _carried[i].position = _carriedLocalPos[i];
            }
            _carried.Clear();
            _carriedLocalPos.Clear();
        }

        /// <summary>Start measuring what is actually being played. The gate needs the bindings the plan
        /// committed to, because "did the hand stay on the bottle?" is only answerable against the
        /// object the plan named.</summary>
        private void StartGates(Request request, SceneQueryService scene, List<object> applied)
        {
            if (gates == null || scene == null || scene.Registry == null) return;

            Dictionary<HumanBodyBones, Transform> _ = _bones;   // ensure the cache is warm
            Dictionary<string, Transform> effectorBones = new Dictionary<string, Transform>
            {
                { "left_hand", Bone(HumanBodyBones.LeftHand) },
                { "right_hand", Bone(HumanBodyBones.RightHand) }
            };

            // The character's own feet define the floor here. A NavMesh sample would be better once
            // locomotion exists; standing height is honest for an in-place corpus.
            //
            // What to watch is decided by GateArming, which the pre-execution validator also uses --
            // so the check that runs before a plan plays and the one that runs while it plays are
            // watching the same contacts, due at the same moments, by construction.
            gates.Arm(request, scene, applied, composer.Animator, ik, effectorBones,
                      transform.position.y);
        }

        /// <summary>The geometric verdict for the plan currently playing.</summary>
        public object GateReport()
        {
            if (gates == null)
            {
                return new Dictionary<string, object>
                {
                    { "status", "unavailable" },
                    { "note", "no GateProbe on this character" }
                };
            }
            Dictionary<string, object> report = gates.Report();
            report["character"] = id;
            report["posture"] = _posture;
            report["sitting_on"] = _supportObjectId;
            if (composer != null)
            {
                // A sequence that hard-cuts and one that crossfades both pass every geometric check and
                // both report the same step count, so whether the fade ran has to be measured, not
                // inferred. max_concurrent_steps of 1 means every handover snapped.
                report["blend"] = new Dictionary<string, object>
                {
                    { "steps", composer.StepCount },
                    { "max_concurrent_steps", composer.MaxConcurrentSteps },
                    { "peak_overlap", composer.PeakOverlap },
                    // Whether a channel really ended up with two sources, read back off the mixer. A
                    // plan that asked for a mix and one that resolved the channel to a single winner
                    // both play and look identical from here without it.
                    { "mixed_channels", composer.MixedChannels }
                };
            }
            if (synth != null)
            {
                report["generated"] = new Dictionary<string, object>
                {
                    { "running", synth.Running },
                    { "hip_height_m", synth.LastHipY },
                    { "worst_tracking_error_m", synth.WorstTrackingErrorM },
                    { "bias_m", synth.BiasM },
                    { "dropped_writes", synth.DroppedWrites },
                    { "saturated_frames", synth.SaturatedFrames }
                };
            }
            return report;
        }

        /// <summary>Walk somewhere, or report on a walk already under way.
        ///
        /// Separate from Apply because moving and animating are separate concerns here: every clip is
        /// in-place, so what is playing and where she ends up are decided by different calls. A `query`
        /// request reports without disturbing anything, which is how the agent waits for arrival.
        /// </summary>
        public object Locomote(Request request, SceneQueryService scene)
        {
            if (locomotion == null)
            {
                throw new AgentRequestException(Protocol.Err.NotReady,
                    "no Locomotion component on " + id);
            }
            if (request.Bool("query", false))
            {
                Dictionary<string, object> here = locomotion.State();
                here["posture"] = _posture;
                here["sitting_on"] = _supportObjectId;
                // WHAT SHE IS ALREADY DOING. A plan whose opening step exists only to be departed
                // from should open on this rather than on a walk cycle she is not walking: playing one
                // while the navigation agent is stationary marches her on the spot. The engine knows
                // both halves of that -- `going` and this -- and neither is inferable agent-side.
                here["playing"] = composer == null ? null : composer.PlayingActionId;
                return here;
            }
            // WHERE A WALK WOULD END, WITHOUT WALKING IT.
            //
            // Above the seated refusal below on purpose: this moves nothing, enables nothing and
            // returns no state, so there is nothing for that refusal to protect against. It exists
            // because the walk used to be the first irreversible thing a plan did -- she crossed the
            // room, and only then did the motion she crossed it for get judged. Now the route is
            // computed, the arrival projected, and the motion checked at that arrival; she sets off
            // afterwards, if at all.
            if (request.Bool("preview", false))
            {
                string cannotReach;
                Vector3? look = ResolveDestination(request.Str("to"), scene, out cannotReach);
                if (look == null)
                {
                    return new Dictionary<string, object>
                    {
                        { "preview", true }, { "reachable", false },
                        { "to", request.Str("to") },
                        { "why", "nothing called " + request.Str("to") + ": " + cannotReach }
                    };
                }
                // Through the SAME resolution the turn itself uses, so the heading this projects is
                // the heading Face() will produce rather than a second opinion about it.
                Vector3? faceAt = null;
                string faceId = request.Str("face");
                if (!string.IsNullOrEmpty(faceId))
                {
                    string cannotFace;
                    faceAt = ResolveDestination(faceId, scene, out cannotFace);
                }
                Dictionary<string, object> preview = locomotion.Preview(
                    look.Value, request.Float("stop_within_m", 0.35f), faceAt);
                preview["to"] = request.Str("to");
                return preview;
            }

            // WALKING WHILE SEATED IS NOT A SMALL ERROR. Go() re-enables the NavMeshAgent, and enabling
            // one warps its transform to the nearest point on the mesh -- which does not extend under a
            // chair. The character would jump off the seat, still in a seated pose, and the gate would
            // report a landing that had genuinely happened a moment earlier.
            //
            // STILL REFUSED, BUT IT IS NO LONGER A DEAD END. The reason used to be that standing up
            // could not be generated; it can now, so this says what to do instead of what is
            // impossible. The order matters and cannot be fixed here: she has to be on her feet BEFORE
            // the agent is re-enabled, and only a plan can put her there.
            if (_posture == "seated" && !request.Bool("halt", false))
            {
                throw new AgentRequestException(Protocol.Err.ExecFailed,
                    id + " is sitting on " + (_supportObjectId ?? "something") + " and cannot walk "
                    + "from there. Stand her up first -- one plan_motion with a standing action, which "
                    + "generates the frames for getting up -- and then walk.");
            }
            if (request.Bool("halt", false))
            {
                locomotion.Halt();
                return locomotion.State();
            }
            // Turning without walking: used once she has arrived, because arriving leaves her facing
            // the way she came and that is backwards for sitting down.
            string faceOnly = request.Str("face_only");
            if (!string.IsNullOrEmpty(faceOnly))
            {
                string why;
                Vector3? look = ResolveDestination(faceOnly, scene, out why);
                if (look == null)
                {
                    throw new AgentRequestException(Protocol.Err.NotFound,
                        "nothing called " + faceOnly + " to face: " + why);
                }
                locomotion.Face(look.Value);
                Dictionary<string, object> faced = locomotion.State();
                faced["facing"] = faceOnly;
                // Turning takes time now rather than a frame, so this returns with it under way. The
                // caller polls `turning` -- the same shape as waiting for a walk to arrive.
                faced["turning"] = locomotion.Turning;
                return faced;
            }

            string targetId = request.Str("to");
            string cannot;
            Vector3? destination = ResolveDestination(targetId, scene, out cannot);
            if (destination == null)
            {
                throw new AgentRequestException(Protocol.Err.NotFound,
                    "cannot walk to " + targetId + ": " + cannot);
            }

            float stopWithin = request.Float("stop_within_m", 0.35f);
            string problem = locomotion.Go(destination.Value, stopWithin);
            if (problem != null)
            {
                throw new AgentRequestException(Protocol.Err.ExecFailed, problem);
            }

            Dictionary<string, object> state = locomotion.State();
            state["to"] = targetId;
            // ALONG THE ROUTE SHE IS ACTUALLY TAKING. This used to measure to the raw destination, and
            // a laptop sits on a desk — off the navigation mesh — so the length came back as -1, the
            // sentinel for "no complete route", about a walk that Go had just accepted. The agent reads
            // this to work out how long to wait, saw -1, and so did not wait at all: it committed the
            // next plan while she was still crossing the room. Go samples the destination onto the
            // mesh and refuses if THAT has no route, so after it succeeds this number is real.
            float length = locomotion.PathLength(locomotion.Destination);
            state["path_length_m"] = length;
            // The agent uses this to decide how long to wait; a wrong ETA costs a retry, not a failure.
            state["eta_s"] = length < 0f || locomotion.Agent.speed <= 0f
                ? -1f : length / locomotion.Agent.speed;
            return state;
        }

        /// <summary>An anchor, the place to stand for an object, the object itself — or a place that is
        /// not an object at all, like "to the right of my view".
        ///
        /// Falls through to the raw scene search, because ids that scene.find handed out have to work
        /// in every tool that takes one. Returning them and then refusing them here is a dead end of
        /// our own making, and the agent hit it.
        /// </summary>
        private Vector3? ResolveDestination(string targetId, SceneQueryService scene, out string why)
        {
            why = null;
            if (string.IsNullOrEmpty(targetId) || scene == null || scene.Registry == null)
            {
                why = "no scene is wired";
                return null;
            }
            SceneRegistry.Entry entry = scene.Registry.ById(targetId);
            if (entry == null) entry = scene.Registry.ByAlias(targetId);
            if (entry != null && entry.target != null)
            {
                // Standing ON a chair is not what "walk to the chair" means.
                return entry.standAnchor != null ? entry.standAnchor.position : entry.target.position;
            }
            // Anything that is not an annotated object: a raw scene name, or one of the relative
            // places. Both are the query service's to answer -- it owns the registry and the metres.
            return scene.ResolvePoint(targetId, transform, out why);
        }

        /// <summary>Everything a request compiles down to. Built once and used by whichever mode
        /// asked for it, so a checked plan and a played plan are the same object graph derived from the
        /// same bytes rather than two derivations that happen to agree.</summary>
        public sealed class CompiledPlan
        {
            public Request Request;
            public List<MotionComposer.StepSpec> Steps;
            public List<object> Resolved;

            /// <summary>The posture each end of the plan is in, derived agent-side from the knowledge
            /// base and carried on the wire. The first decides whether this plan can start from where
            /// she is; the last is what she will be in when it ends.</summary>
            public string FirstPosture;
            public string LastPosture;

            /// <summary>The generated posture change, if this plan declares one.</summary>
            public Descent Pending;

            /// <summary>A seated step played without generating a change into it, which still has to
            /// be judged against something. Without this a character seated in mid-air passes every
            /// check, which is what happened.</summary>
            public string PendingSupport;
        }

        /// <summary>A transition that has to be generated, and when to start generating it.</summary>
        public sealed class Descent
        {
            public double AtSeconds;
            public float StartHipY;
            public float TargetHipY;
            public float DurationSeconds;
            public string Kind;
            public string SupportObjectId;
            public Vector3? LeftFootLocal;
            public Vector3? RightFootLocal;
        }

        private static Vector3? LocalPoint(JObject holder, string key)
        {
            JArray v = holder == null ? null : holder[key] as JArray;
            if (v == null || v.Count < 3) return null;
            return new Vector3(v[0].ToObject<float>(), v[1].ToObject<float>(), v[2].ToObject<float>());
        }

        /// <summary>Wait for the seam, then hand the posture change to PoseSynth.
        ///
        /// Timed off the same clock the composer uses rather than an event, because the correction has
        /// to begin exactly as the crossfade does: start it early and the character sinks while still
        /// walking, start it late and she is already half into the seated clip with her hips up.
        ///
        /// BOTH DIRECTIONS, AND THE DIFFERENCE IS ONLY AT THE END. Going down and coming back up are
        /// the same generated descent with the hip heights the other way round — that was already true
        /// of everything that computes it. What is genuinely different is what to do once it lands:
        /// a sit HOLDS its correction, because the hips are only down as long as something keeps them
        /// there, and letting go pops her back up. A rise must do the opposite and let go completely,
        /// or the next plan inherits a hip drop that no longer describes anything. Navigation comes
        /// back with it, and only then: re-enabling a NavMeshAgent warps it to the nearest walkable
        /// point, which is off the chair, so doing it while she is seated is the bug that refusal
        /// existed to avoid — once she is standing it is simply correct.
        /// </summary>
        private System.Collections.IEnumerator RunPostureChange(Descent descent, SceneQueryService scene,
                                                                string endsPosture)
        {
            if (synth == null)
            {
                Debug.LogWarning("[AgentRuntime] a step asked for a generated transition but no "
                                 + "PoseSynth is wired on " + id);
                yield break;
            }

            // Tell the gate what this descent claims it will land on, BEFORE it runs, so a plan that
            // lands nowhere is caught by measurement rather than by someone looking at it.
            //
            // Only when it ends SEATED. Standing up ends on the floor, and "did the pelvis land inside
            // the footprint of the chair" is the wrong question about it — she is meant to be off the
            // chair by then, so arming it would report the success as a failure. What is worth judging
            // about a rise is that the hips reached the height the plan named, and `hip_reached_target`
            // is part of the same support metric; losing it is the price of not asserting something
            // false. A standing-landing check belongs with a floor reference the gate does not have.
            if (gates != null && scene != null && scene.Registry != null && endsPosture == "seated" &&
                !string.IsNullOrEmpty(descent.SupportObjectId))
            {
                SceneRegistry.Entry seat = scene.Registry.ById(descent.SupportObjectId);
                if (seat != null && seat.target != null)
                {
                    // How long until the landing is answerable: the wait for the seam, plus the descent
                    // itself, plus a beat to settle. The same arithmetic probe_sit.py did by hand before
                    // reading the gate -- moved here so the agent's own path gets it too.
                    float judgeableIn = (float)System.Math.Max(0.0, descent.AtSeconds - composer.Elapsed)
                                        + descent.DurationSeconds + 0.3f;
                    gates.ExpectSupport(descent.SupportObjectId, seat.target, seat.surfaceHeight,
                                        BoneOrNull(HumanBodyBones.Hips), judgeableIn,
                                        descent.StartHipY, descent.TargetHipY);
                }
            }

            while (composer.Playing && composer.Elapsed < descent.AtSeconds) yield return null;
            if (!composer.Playing) yield break;
            // A NavMeshAgent that is still steering will drag the character out from under a sit aimed
            // at a fixed seat, and the gate would then report a landing that the descent did reach.
            if (locomotion != null) locomotion.Halt();

            // WHERE SHE ENDS UP HORIZONTALLY, which is not the same place in the two directions.
            // Sitting lands ON the support: the navigation mesh does not extend under a chair, so
            // walking gets her about 0.15 m short of it and the descent covers the rest, which is what
            // the backward step in a real sit is. Standing up is that step in reverse — she has to come
            // off the seat, and the place to come off it to is the same one walking to the chair aims
            // at, so the stand anchor is reused rather than a clearance being invented here.
            Vector3? landOn = null;
            if (scene != null && scene.Registry != null && !string.IsNullOrEmpty(descent.SupportObjectId))
            {
                SceneRegistry.Entry seat = scene.Registry.ById(descent.SupportObjectId);
                if (seat != null && seat.target != null)
                {
                    landOn = endsPosture == "standing" && seat.standAnchor != null
                        ? seat.standAnchor.position
                        : seat.target.position;
                }
            }
            synth.Begin(descent.TargetHipY, descent.DurationSeconds, landOn,
                        descent.LeftFootLocal, descent.RightFootLocal);

            while (synth.Running) yield return null;
            if (gates != null)
            {
                gates.SupportLanded(synth.BiasM, synth.DroppedWrites, synth.SaturatedFrames);
            }

            // She is on her feet again, so nothing should still be holding her hips down and the
            // navigation agent can have the transform back. Done here rather than at commit time
            // because until now it was not true: the whole rise happens between the two.
            if (endsPosture == "standing")
            {
                composer.SetCorrection(new MotionComposer.Correction());
                if (locomotion != null) locomotion.Resume();
            }
        }

        /// <summary>Judge a seated pose that was retrieved rather than generated. Same check, later
        /// arming: the clip needs a moment to establish the pose before there is anything to measure.
        /// </summary>
        private System.Collections.IEnumerator JudgeSupport(string objectId, SceneQueryService scene)
        {
            if (gates == null || scene == null || scene.Registry == null) yield break;
            SceneRegistry.Entry seat = scene.Registry.ById(objectId);
            if (seat == null || seat.target == null) yield break;
            gates.ExpectSupport(objectId, seat.target, seat.surfaceHeight,
                                BoneOrNull(HumanBodyBones.Hips), 0.5f);
            float until = Time.time + 0.5f;
            while (Time.time < until && composer.Playing) yield return null;
            gates.SupportLanded();
        }

        private Transform BoneOrNull(HumanBodyBones bone)
        {
            Animator animator = composer != null ? composer.Animator : GetComponent<Animator>();
            return animator == null || !animator.isHuman ? null : animator.GetBoneTransform(bone);
        }

        private void TakeOverFromLegacy()
        {
            // Both would otherwise write TwoBoneIKConstraint.weight every frame and fight.
            if (legacyIkHelper != null) legacyIkHelper.enabled = false;
        }

        public void StopAll()
        {
            if (composer != null) composer.Stop();
            if (ik != null) ik.ReleaseAll();
            ReleaseCarried();
            if (legacyIkHelper != null) legacyIkHelper.enabled = true;
            if (_validation != null) { _validation.Dispose(); _validation = null; }
        }

        private void OnDestroy()
        {
            // Otherwise a scene change or a domain reload leaves an invisible nurse standing in the
            // room, which is the kind of thing nobody finds until it is animating.
            if (_validation != null) { _validation.Dispose(); _validation = null; }
        }

        /// <summary>Can this effector reach a world point? Arm length is MEASURED off the skeleton
        /// rather than hardcoded, so it stays right if the avatar changes.</summary>
        public bool CanReach(Vector3 point, string effector)
        {
            bool either = string.IsNullOrEmpty(effector) || effector == "either";
            if (either)
            {
                return CanReach(point, "left_hand") || CanReach(point, "right_hand");
            }

            bool left = effector == "left_hand";
            Transform shoulder = Bone(left ? HumanBodyBones.LeftUpperArm : HumanBodyBones.RightUpperArm);
            Transform elbow = Bone(left ? HumanBodyBones.LeftLowerArm : HumanBodyBones.RightLowerArm);
            Transform wrist = Bone(left ? HumanBodyBones.LeftHand : HumanBodyBones.RightHand);
            if (shoulder == null || elbow == null || wrist == null) return false;

            float armLength = Vector3.Distance(shoulder.position, elbow.position) +
                              Vector3.Distance(elbow.position, wrist.position);
            return Vector3.Distance(shoulder.position, point) <= armLength * 1.02f;
        }
    }
}
