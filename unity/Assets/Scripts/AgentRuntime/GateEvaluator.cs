using System.Collections.Generic;
using UnityEngine;

namespace AgentRuntime
{
    /// <summary>
    /// The geometric judgement, with no opinion about when it is asked.
    ///
    /// WHY THIS IS NOT A MonoBehaviour ANY MORE. There are two places that need to decide whether a
    /// composed motion is physically wrong: the runtime probe, which samples the pose a viewer is
    /// looking at, and the pre-execution validator, which samples a hidden duplicate at fixed timestep
    /// before anything visible has moved. Those differ in exactly one thing — where the seconds come
    /// from — and in nothing else. Two copies of "how far is too far" would drift, and the first
    /// symptom of the drift would be a plan the validator passed and the probe then failed, which
    /// reads as a bug in the motion rather than in the pair of thresholds.
    ///
    /// So the thresholds, the accumulation and the metric shapes live here once, and the two callers
    /// supply the clock. <see cref="GateProbe"/> calls <see cref="Sample"/> from LateUpdate with real
    /// elapsed time; <see cref="ValidationCharacter"/> calls it from a loop with simulated time.
    ///
    /// SOME THRESHOLDS ARE PRINCIPLED AND SOME ARE NOT, and the difference is stated per metric rather
    /// than papered over. A hand "holding" an object 20 cm away is wrong by inspection; a foot below
    /// the floor is wrong by geometry. Foot skate is not like that: what counts as too much sliding
    /// depends on the clip and the frame rate, and the honest thing is to measure it, report it, and
    /// calibrate a threshold from the corpus the way the v2 divisors were calibrated. Metrics with no
    /// defensible threshold report `null` and never fail a plan — a made-up cutoff would produce
    /// confident rejections with nothing behind them. THIS REFACTOR ADDED NO THRESHOLDS. Every number
    /// below is the one that was already here.
    /// </summary>
    public sealed class GateEvaluator
    {
        // A hand meant to be holding something, this far from it, reads as not holding it.
        public const float ContactHoldToleranceM = 0.02f;
        // Below the floor is below the floor. A centimetre of slack absorbs foot-IK jitter.
        public const float GroundPenetrationToleranceM = 0.01f;
        // How far above its own sole height a foot may sit and still count as down. Small on purpose:
        // this decides which frames the skate metric SEES, and every centimetre added to it starts
        // counting swing frames, where the foot is supposed to be travelling.
        public const float PlantedToleranceM = 0.02f;

        /// <summary>One effector bound to one object, and when that binding is due.
        ///
        /// `DueAt` exists because a plan has steps and a contact belongs to one of them. Measured: a
        /// walk-then-sit-then-type plan bound both hands to the laptop and failed contact_hold every
        /// time, on frames from the walk -- her hands were nowhere near the keyboard, correctly, while
        /// she was still crossing the room. Judging the whole plan by its worst frame made a binding
        /// that held perfectly for the part it applied to look like a binding that never held.
        /// </summary>
        private sealed class Tracked
        {
            public string Effector;
            public Transform Bone;
            public Transform Target;      // a point to hold, for a binding
            public Bounds Volume;         // or a volume to reach, for a contact the clip makes itself
            public bool AgainstVolume;
            public string ObjectId;
            public float DueAt;           // seconds into the plan; before this there is nothing to hold
            public float BestError = float.MaxValue;   // contacts: the closest it ever got
            public float WorstError;                   // bindings: the furthest it ever strayed
            public int WorstFrame;
            public bool Judged;
        }

        /// <summary>The support a generated posture change claimed it would land on.
        ///
        /// ADDED BECAUSE THE GATE MISSED SOMETHING REAL. The first generated sit tracked its hip target
        /// to 3.5 mm, held its feet to 2 mm, penetrated nothing, and passed every check -- while the
        /// character squatted in mid-air a metre and a half from the chair. Every metric was about how
        /// well the motion matched its own plan, and none asked whether the plan put her on anything.
        /// So a sit that lands nowhere is now a failure, not a pass with good numbers.
        ///
        /// AND THEN MISSED IT AGAIN, THE OTHER WAY. The metric only existed once `Armed` was set, which
        /// happens after the descent finishes -- and the descent does not even START until the outgoing
        /// step reaches its handover, about two seconds in. A model round trip is under half that, so
        /// every `check_motion` the agent ever made arrived while this was still null, the metric was
        /// simply absent from the report, and "no failures" was read as "passed". A check that has not
        /// run yet has to be distinguishable from one that ran and was happy, so an unarmed support now
        /// reports `pending` and `ExpectedAt` says when it will be answerable.
        /// </summary>
        private sealed class Support
        {
            public string ObjectId;
            public Bounds Footprint;      // world-space bounds of the thing being sat on
            public float SurfaceY;
            public Transform Hips;
            public bool Armed;            // set once the descent finishes; before that there is nothing to judge
            public float ExpectedAt;      // seconds INTO THE PLAN by which the landing should be judgeable
            public float StartHipY;       // where the plan said the hips would start
            public float TargetHipY;      // and where it said they would end
            public float HorizontalMiss = -1f;
            public float CentreMiss = -1f;   // how far the pelvis ended from the middle of the seat
            public float VerticalGap;
            public float HipMiss;
            public float BiasM;           // what the descent's closed loop had to accumulate
            public int DroppedWrites;     // how much of it the graph never received
            public int SaturatedFrames;   // and how much of it did not fit inside what a body can do
        }

        private readonly List<Tracked> _contacts = new List<Tracked>();

        /// <summary>Contacts the CLIP makes by itself, which nothing binds and nothing corrects.
        ///
        /// `typing` animates both hands against a keyboard already; what decides whether they meet the
        /// real one is where the character ends up. That was never measured, and it does not always
        /// work out: the clip types 0.70 m above the floor and 0.33 m in front of the root, this room's
        /// laptop deck sits near 0.90 m, and sitting on a 0.41 m chair leaves her hands a fifth of a
        /// metre under the keyboard. Every check passed and the hands hovered.
        ///
        /// Measured against the object's BOUNDS rather than its grab point, because a keyboard is a
        /// surface: anywhere on it is on it. And the best frame rather than the worst -- a contact the
        /// clip makes and releases (a hand rising between keystrokes) is still a contact, whereas a
        /// binding is a promise to stay.
        /// </summary>
        private readonly List<Tracked> _declared = new List<Tracked>();
        private readonly List<Transform> _feet = new List<Transform>();
        private readonly List<float> _footBottom = new List<float>();
        private Support _support;
        private float _groundY;
        private float _worstPenetration;
        private int _worstPenetrationFrame;
        private float _worstFootSkate;
        private Vector3[] _lastFootPos;
        private int _frames;

        public int Frames { get { return _frames; } }

        /// <summary>Begin measuring a plan. `boundEffectors` are the effector bindings the plan
        /// committed to, which is what makes "did the grounding hold?" answerable at all.
        ///
        /// `dueByEffector` says when each binding starts being due, keyed by effector. A hand cannot be
        /// on a keyboard while she is still walking to it, and judging from frame zero failed every
        /// multi-step plan that bound anything — measured, on a walk-then-sit-then-type plan whose
        /// hands were on the laptop the whole time they were supposed to be.</summary>
        public void Begin(List<KeyValuePair<string, Transform>> boundEffectors,
                          Dictionary<string, Transform> bones, Animator rig,
                          Transform leftFoot, Transform rightFoot, float groundY,
                          Dictionary<string, float> dueByEffector = null)
        {
            _contacts.Clear();
            for (int i = 0; boundEffectors != null && i < boundEffectors.Count; i++)
            {
                Transform bone;
                if (bones == null || !bones.TryGetValue(boundEffectors[i].Key, out bone) || bone == null)
                {
                    continue;
                }
                float due;
                if (dueByEffector == null || !dueByEffector.TryGetValue(boundEffectors[i].Key, out due))
                {
                    due = 0f;
                }
                _contacts.Add(new Tracked
                {
                    Effector = boundEffectors[i].Key,
                    Bone = bone,
                    Target = boundEffectors[i].Value,
                    ObjectId = boundEffectors[i].Value == null ? null : boundEffectors[i].Value.name,
                    DueAt = due
                });
            }
            _declared.Clear();

            // Each foot carries the height of its own sole below its bone, captured here rather than
            // looked up per frame — and paired with the foot at the moment the list is built, because
            // a missing left foot would otherwise shift every index by one and measure the right
            // foot's sole against the left one's offset.
            bool human = rig != null && rig.isHuman;
            _feet.Clear();
            _footBottom.Clear();
            if (leftFoot != null)
            {
                _feet.Add(leftFoot);
                _footBottom.Add(human ? rig.leftFeetBottomHeight : 0f);
            }
            if (rightFoot != null)
            {
                _feet.Add(rightFoot);
                _footBottom.Add(human ? rig.rightFeetBottomHeight : 0f);
            }
            _lastFootPos = new Vector3[_feet.Count];
            for (int i = 0; i < _feet.Count; i++) _lastFootPos[i] = _feet[i].position;

            _groundY = groundY;
            _support = null;
            _worstPenetration = 0f;
            _worstPenetrationFrame = 0;
            _worstFootSkate = 0f;
            _frames = 0;
        }

        /// <summary>Declare a contact the clip is expected to make on its own, from `dueAt` seconds into
        /// the plan. Nothing is bound and nothing is corrected — this only says what to watch.</summary>
        public void ExpectContact(string effector, Transform bone, Transform obj, string objectId,
                                  float dueAt)
        {
            if (bone == null || obj == null) return;
            Bounds volume = new Bounds(obj.position, Vector3.zero);
            Renderer[] renderers = obj.GetComponentsInChildren<Renderer>(true);
            for (int i = 0; i < renderers.Length; i++) volume.Encapsulate(renderers[i].bounds);
            _declared.Add(new Tracked
            {
                Effector = effector, Bone = bone, Volume = volume, AgainstVolume = true,
                ObjectId = objectId, DueAt = dueAt
            });
        }

        /// <summary>Declare that a generated posture change is supposed to end up on `seat`.
        ///
        /// `judgeableAt` is the point ON THE PLAN'S OWN CLOCK by which the landing should be
        /// answerable — the caller knows it because it is the same schedule the descent runs on. It is
        /// used only to tell a waiting caller roughly how long to wait; `Armed` is what decides.</summary>
        public void ExpectSupport(string objectId, Transform seat, float surfaceY, Transform hips,
                                  float judgeableAt, float startHipY = -1f, float targetHipY = -1f)
        {
            if (seat == null || hips == null) return;
            Bounds footprint = new Bounds(seat.position, Vector3.zero);
            Renderer[] renderers = seat.GetComponentsInChildren<Renderer>(true);
            for (int i = 0; i < renderers.Length; i++) footprint.Encapsulate(renderers[i].bounds);
            _support = new Support
            {
                ObjectId = objectId, Footprint = footprint, SurfaceY = surfaceY, Hips = hips,
                StartHipY = startHipY, TargetHipY = targetHipY,
                ExpectedAt = judgeableAt
            };
        }

        public bool HasSupport { get { return _support != null; } }

        /// <summary>Called when the descent finishes: from here the landing is judgeable.
        ///
        /// These come from the loop that ran the descent, and they are here so the gate can say WHY a
        /// landing failed rather than only that it did. Where the pelvis ended up is the outcome;
        /// whether the correction was received, and whether it fitted inside what a body can do, are the
        /// two causes, and they need different fixes.
        /// </summary>
        public void SupportLanded(float biasM = 0f, int droppedWrites = 0, int saturatedFrames = 0)
        {
            if (_support == null || _support.Hips == null) return;
            _support.BiasM = biasM;
            _support.DroppedWrites = droppedWrites;
            _support.SaturatedFrames = saturatedFrames;
            Vector3 hips = _support.Hips.position;
            Bounds f = _support.Footprint;
            // Horizontal miss: how far outside the seat's own footprint the pelvis ended up. Zero when
            // it is over the seat at all, which is a containment test rather than a tuned distance.
            float dx = Mathf.Max(0f, Mathf.Max(f.min.x - hips.x, hips.x - f.max.x));
            float dz = Mathf.Max(0f, Mathf.Max(f.min.z - hips.z, hips.z - f.max.z));
            _support.HorizontalMiss = Mathf.Sqrt(dx * dx + dz * dz);
            // AND HOW FAR FROM THE MIDDLE OF IT, which is a different question and the one a viewer
            // is actually asking. Containment says the pelvis is somewhere over the chair; it says
            // nothing about where, and a chair's footprint is wide enough that a sit landing near its
            // edge -- or on the arm -- contains perfectly well. Measured on the first retrieved
            // sit-down: `seated_on_support` passed at 0.000 while the character was visibly 0.3-0.5 m
            // off the seat, because the whole error fitted inside the box.
            Vector3 centre = f.center;
            float cx = hips.x - centre.x, cz = hips.z - centre.z;
            _support.CentreMiss = Mathf.Sqrt(cx * cx + cz * cz);
            _support.VerticalGap = hips.y - _support.SurfaceY;
            _support.HipMiss = Mathf.Abs(hips.y - _support.TargetHipY);
            _support.Armed = true;
        }

        /// <summary>Read the pose as it stands and fold it into the running worst cases.
        ///
        /// `elapsed` is seconds into the PLAN, not wall clock — that is the whole reason this takes it
        /// rather than reading a clock. `dt` is the step since the last sample, which foot skate needs
        /// because sliding is a speed.
        /// </summary>
        public void Sample(float elapsed, float dt)
        {
            _frames++;

            for (int i = 0; i < _contacts.Count; i++)
            {
                Tracked c = _contacts[i];
                if (c.Target == null || c.Bone == null || elapsed < c.DueAt) continue;
                c.Judged = true;
                float error = Vector3.Distance(c.Bone.position, c.Target.position);
                if (error > c.WorstError) { c.WorstError = error; c.WorstFrame = _frames; }
            }

            for (int i = 0; i < _declared.Count; i++)
            {
                Tracked c = _declared[i];
                if (c.Bone == null || elapsed < c.DueAt) continue;
                c.Judged = true;
                // Distance to the volume, which is zero anywhere inside it. A keyboard is a surface,
                // so "on it" is not a point.
                float gap = Vector3.Distance(c.Bone.position, c.Volume.ClosestPoint(c.Bone.position));
                if (gap < c.BestError) { c.BestError = gap; c.WorstFrame = _frames; }
            }

            for (int i = 0; i < _feet.Count; i++)
            {
                Vector3 p = _feet[i].position;
                float penetration = _groundY - p.y;
                if (penetration > _worstPenetration)
                {
                    _worstPenetration = penetration;
                    _worstPenetrationFrame = _frames;
                }
                // Skate is only meaningful while the foot is down; in the air it is just travel.
                //
                // AGAINST THE SOLE, NOT THE ANKLE. This compared the foot BONE against a flat 5 cm and
                // so never fired on this avatar: measured on CPRNurse, a planted foot's ankle sits at
                // 0.072-0.080 m, because the bone is inside the leg and the sole is what touches the
                // floor. Every reading was therefore 0.0000 — not "no skate" but "never sampled", and
                // it read as the former, which is the worst way for a metric to fail. The offset is
                // the rig's own `leftFeetBottomHeight`, which Unity measures from the avatar, so
                // nothing here is a number somebody chose.
                if (p.y - _groundY < _footBottom[i] + PlantedToleranceM && dt > 0f)
                {
                    Vector3 delta = p - _lastFootPos[i];
                    delta.y = 0f;
                    float speed = delta.magnitude / dt;
                    if (speed > _worstFootSkate) _worstFootSkate = speed;
                }
                _lastFootPos[i] = p;
            }
        }

        /// <summary>The verdict so far. Safe to call while still running. `elapsed` is seconds into the
        /// plan, and is what an unanswerable check counts down from.</summary>
        public Dictionary<string, object> Report(float elapsed, float seconds)
        {
            List<object> metrics = new List<object>();

            for (int i = 0; i < _contacts.Count; i++)
            {
                Tracked c = _contacts[i];
                if (!c.Judged)
                {
                    metrics.Add(Pending("contact_hold:" + c.Effector,
                                        Mathf.Max(0f, c.DueAt - elapsed),
                                        "the step this binding belongs to has not started",
                                        c.Effector, c.ObjectId));
                    continue;
                }
                metrics.Add(Metric(
                    "contact_hold:" + c.Effector, c.WorstError, ContactHoldToleranceM, c.WorstFrame,
                    "metres between " + c.Effector + " and the object it was bound to, worst frame",
                    c.Effector, c.ObjectId));
            }

            for (int i = 0; i < _declared.Count; i++)
            {
                Tracked c = _declared[i];
                if (!c.Judged)
                {
                    metrics.Add(Pending("contact_reached:" + c.Effector,
                                        Mathf.Max(0f, c.DueAt - elapsed),
                                        "the step that touches " + c.ObjectId + " has not started",
                                        c.Effector, c.ObjectId));
                    continue;
                }
                metrics.Add(Metric(
                    "contact_reached:" + c.Effector, c.BestError, ContactHoldToleranceM, c.WorstFrame,
                    "metres between " + c.Effector + " and " + c.ObjectId + " at their closest. The "
                    + "clip animates this hand against that object by itself, so this measures whether "
                    + "the character is placed where the animation expects the object to be",
                    c.Effector, c.ObjectId));
            }

            metrics.Add(Metric("ground_penetration", _worstPenetration, GroundPenetrationToleranceM,
                               _worstPenetrationFrame,
                               "metres a foot went below the floor, worst frame"));

            // No defensible threshold yet -- see the class remarks. Measured and reported, never fatal.
            //
            // AND HERE IS THE MEASUREMENT THAT SAYS DO NOT ADD ONE (2026-08-19). The same corpus, read
            // on the two clocks: a `walking` plan on the validation duplicate reads 2.1449 m/s, and a
            // real walk-and-sit through the runtime probe reads 1.5341 m/s. Neither is a defect. The
            // duplicate has no NavMeshAgent -- ValidationCharacter destroys it -- so nothing translates
            // the root and the clip's whole stride is counted as slide; at runtime the agent cancels
            // part of it and the remainder is the honest difference between the agent's speed and the
            // speed the animator authored, which Locomotion's own remarks predict. So a threshold under
            // ~1.5 fails every walk that plays, one under ~2.15 fails every walk at the pre-execution
            // check, and one above that catches nothing. One number cannot serve both clocks, and no
            // number serves this corpus. Calibrate from a distribution first, the way the v2 divisors
            // were, or leave it reported.
            metrics.Add(Metric("foot_skate", _worstFootSkate, -1f, 0,
                               "metres per second a planted foot slid; needs corpus calibration, "
                               + "reported only"));

            float waitFor = 0f;
            if (_support != null && _support.Armed)
            {
                // Tolerance 0: the pelvis is either over the seat's footprint or it is not. This is a
                // containment test, so unlike foot skate it needs no calibration to be fair.
                metrics.Add(Metric("seated_on_support", _support.HorizontalMiss, 0f, _frames,
                                   "metres the pelvis ended up outside the footprint of "
                                   + _support.ObjectId + "; a sit that lands on nothing is not a sit",
                                   null, _support.ObjectId));
                // NOT A CALIBRATED NUMBER, A GEOMETRIC ONE. Five centimetres is about the slack
                // between a pelvis and the middle of a seat it is genuinely sitting on -- the width of
                // a hand -- and it is the scale the plan places her at: `scene.standing_point_for`
                // works the standing point out from the clip's own travel and lands the hips on the
                // seat centre exactly, so anything past a few centimetres means something moved that
                // was not supposed to. Fatal, because a sit that misses by more than that is the
                // failure this gate exists for and the containment test above cannot see it.
                metrics.Add(Metric("seat_alignment", _support.CentreMiss, 0.05f, _frames,
                                   "metres between the pelvis and the middle of " + _support.ObjectId
                                   + "; containment alone passes a sit that landed on the edge",
                                   null, _support.ObjectId));

                // Reported, not judged: how far a pelvis should sit above a seat surface depends on the
                // avatar, and one measurement is not a calibration.
                metrics.Add(Metric("pelvis_above_surface", _support.VerticalGap, -1f, _frames,
                                   "metres between the pelvis and the top of " + _support.ObjectId
                                   + "; reported only", null, _support.ObjectId));

                // HOW FAR ABOVE is not calibrated. BELOW is not a matter of degree. Containment alone
                // certified a sit on the laptop: the pelvis was inside its footprint because the laptop
                // was on the desk directly over her, and 0.70 m underneath the surface she was
                // reported to be sitting on. Same shape as ground penetration and the same tolerance --
                // a centimetre for mesh slack, and past that she is inside the furniture.
                metrics.Add(Metric("sat_through_support",
                                   Mathf.Max(0f, -_support.VerticalGap), GroundPenetrationToleranceM,
                                   _frames,
                                   "metres the pelvis ended up BELOW the top of " + _support.ObjectId
                                   + "; being under a surface is not sitting on it",
                                   null, _support.ObjectId));

                // AND THE VERTICAL IS JUDGED, against the plan's own numbers. Containment alone passed
                // a character standing over the chair she had just walked to: correct in plan, correct
                // in XZ, and 0.58 m above a seat 0.41 m off the floor. The threshold needs no
                // calibration because the plan supplies it -- it said the hips would travel from here
                // to there, and ending up nearer where they started than where they were going means
                // the descent did not happen. Half the claimed travel is the dividing line, and it is
                // the plan's number, not a taste.
                if (_support.TargetHipY >= 0f && _support.StartHipY >= 0f)
                {
                    float travel = Mathf.Abs(_support.StartHipY - _support.TargetHipY);
                    metrics.Add(Metric("hip_reached_target", _support.HipMiss, travel * 0.5f, _frames,
                                       "metres between where the pelvis ended and where this plan said "
                                       + "it would; more than half the claimed travel means the posture "
                                       + "change did not happen", null, _support.ObjectId));

                    // WHY, not just WHETHER. These separate a descent that worked from the two ways it
                    // can fail, and both are counts against zero rather than distances against a
                    // threshold: writes discarded means the loop was never connected to the graph;
                    // frames saturated means it was connected and asked for more than a body can do.
                    // Both clean with the pelvis short means the descent ran and something else moved
                    // her afterwards.
                    metrics.Add(Metric("correction_reached_graph", _support.DroppedWrites, 0f, _frames,
                                       "frames of the descent the playback graph would not accept. The "
                                       + "loop reads the skeleton back each frame, so a discarded write "
                                       + "leaves it correcting an actuator it is not connected to"));
                    metrics.Add(Metric("descent_saturated", _support.SaturatedFrames, 0f, _frames,
                                       "frames on which the correction asked to lower the body further "
                                       + "than the pelvis stands above the floor. An integrator with "
                                       + "nothing to push against runs away, and this is where it hits "
                                       + "the end of what geometry allows"));
                    // Reported only, and deliberately: it has no expected value. Measured on the real
                    // path it came out at -0.459 m against 0.500 m of travel, because the incoming
                    // seated clip drops the hips faster than the commanded curve and the loop spends
                    // the descent subtracting. Any threshold here would have failed a working sit.
                    metrics.Add(Metric("descent_bias", _support.BiasM, -1f, _frames,
                                       "metres of closed-loop correction accumulated over the descent; "
                                       + "negative means the incoming clip was doing more of the work "
                                       + "than the plan asked for. Reported, not judged"));
                }
            }
            else if (_support != null)
            {
                // Declared and not yet answerable. Emitted rather than omitted: absent looks exactly
                // like passed to anything counting failures, and that is how a sit nobody had performed
                // yet came back green.
                waitFor = Mathf.Max(0f, _support.ExpectedAt - elapsed);
                metrics.Add(Pending("seated_on_support", waitFor,
                                    "the descent onto " + _support.ObjectId + " has not finished, so "
                                    + "where the pelvis lands is not measurable yet",
                                    null, _support.ObjectId));
            }

            string status = "pass";
            List<string> failed = new List<string>();
            List<string> pending = new List<string>();
            for (int i = 0; i < metrics.Count; i++)
            {
                Dictionary<string, object> m = (Dictionary<string, object>)metrics[i];
                if ((string)m["status"] == "fail") { status = "fail"; failed.Add((string)m["id"]); }
                else if ((string)m["status"] == "pending") { pending.Add((string)m["id"]); }
            }
            // A failure already found is a failure whatever else is still running, so `fail` outranks
            // `pending`. Nothing here waits for a verdict that cannot improve.
            if (status != "fail" && pending.Count > 0) status = "pending";

            return new Dictionary<string, object>
            {
                { "status", status },
                { "failed", failed },
                { "pending", pending },
                // The one field a caller polls on. False means come back; it never means "fine".
                { "judgeable", pending.Count == 0 },
                { "judgeable_in_s", waitFor },
                { "frames", _frames },
                { "seconds", seconds },
                { "metrics", metrics }
            };
        }

        // ---- the structured half of a verdict ---------------------------------------------------

        /// <summary>Why a metric failing means the plan is wrong, as a stable slug.
        ///
        /// THE AGENT SWITCHES ON THIS, NOT ON THE SENTENCE. A refusal has to say which of four things
        /// to change — the motion, the thing it is aimed at, the way the parts were combined, or where
        /// it happens — and it cannot derive that from prose. The metric id is engine vocabulary and
        /// carries an effector on the end of it; this is the part that is meant to be read.
        /// </summary>
        public static string Reason(string metricId)
        {
            if (string.IsNullOrEmpty(metricId)) return "unknown";
            if (metricId.StartsWith("contact_hold:")) return "hand_left_its_object";
            if (metricId.StartsWith("contact_reached:")) return "hand_never_reached_object";
            switch (metricId)
            {
                case "ground_penetration": return "foot_through_floor";
                case "seated_on_support": return "pelvis_outside_support";
                case "seat_alignment": return "pelvis_off_centre_on_support";
                case "sat_through_support": return "pelvis_below_support";
                case "hip_reached_target": return "hip_did_not_reach_target";
                case "correction_reached_graph": return "correction_discarded";
                case "descent_saturated": return "descent_saturated";
                default: return metricId;
            }
        }

        /// <summary>The failures in a report, shaped so the agent can name what to change without
        /// parsing anything. Empty when nothing failed.</summary>
        public static List<object> Failures(Dictionary<string, object> report)
        {
            List<object> out_ = new List<object>();
            object raw;
            if (report == null || !report.TryGetValue("metrics", out raw)) return out_;
            List<object> metrics = raw as List<object>;
            for (int i = 0; metrics != null && i < metrics.Count; i++)
            {
                Dictionary<string, object> m = metrics[i] as Dictionary<string, object>;
                if (m == null || (string)m["status"] != "fail") continue;
                string id = (string)m["id"];
                Dictionary<string, object> failure = new Dictionary<string, object>
                {
                    { "metric", id },
                    { "reason", Reason(id) },
                    { "measured", m["measured"] },
                    { "tolerance", m["tolerance"] },
                    { "what", m["what"] }
                };
                object carried;
                if (m.TryGetValue("effector", out carried) && carried != null)
                {
                    failure["effector"] = carried;
                }
                if (m.TryGetValue("object_id", out carried) && carried != null)
                {
                    failure["object_id"] = carried;
                }
                out_.Add(failure);
            }
            return out_;
        }

        /// <summary>The ids of every check that produced an answer, for the record. A caller that is
        /// told a plan passed has no other way to know WHAT passed.</summary>
        public static List<string> Checked(Dictionary<string, object> report)
        {
            List<string> ids = new List<string>();
            object raw;
            if (report == null || !report.TryGetValue("metrics", out raw)) return ids;
            List<object> metrics = raw as List<object>;
            for (int i = 0; metrics != null && i < metrics.Count; i++)
            {
                Dictionary<string, object> m = metrics[i] as Dictionary<string, object>;
                if (m == null || (string)m["status"] == "pending") continue;
                ids.Add((string)m["id"]);
            }
            return ids;
        }

        /// <summary>A check that has been declared but cannot be answered yet. Carries no `measured`,
        /// because there is nothing to measure — a zero there would read as a perfect score.</summary>
        public static object Pending(string id, float waitFor, string what,
                                     string effector = null, string objectId = null)
        {
            Dictionary<string, object> m = new Dictionary<string, object>
            {
                { "id", id },
                { "measured", null },
                { "tolerance", null },
                { "status", "pending" },
                { "over_by", null },
                { "worst_frame", 0 },
                { "judgeable_in_s", waitFor },
                { "what", what }
            };
            if (effector != null) m["effector"] = effector;
            if (objectId != null) m["object_id"] = objectId;
            return m;
        }

        public static object Metric(string id, float measured, float tolerance, int frame, string what,
                                    string effector = null, string objectId = null)
        {
            bool judged = tolerance >= 0f;
            Dictionary<string, object> m = new Dictionary<string, object>
            {
                { "id", id },
                { "measured", measured },
                { "tolerance", judged ? (object)tolerance : null },
                { "status", !judged ? "measured" : (measured <= tolerance ? "pass" : "fail") },
                { "over_by", judged && measured > tolerance ? (object)(measured - tolerance) : null },
                { "worst_frame", frame },
                { "what", what }
            };
            // WHICH HAND AND WHICH THING, carried beside the number rather than only inside the
            // sentence. A refusal names the object it is about; parsing that back out of `what` would
            // be a second place the vocabulary could drift.
            if (effector != null) m["effector"] = effector;
            if (objectId != null) m["object_id"] = objectId;
            return m;
        }
    }
}
