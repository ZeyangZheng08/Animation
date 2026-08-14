using System.Collections.Generic;
using UnityEngine;

namespace AgentRuntime
{
    /// <summary>
    /// Measures the pose that is actually being played, and reports it back as a gate verdict.
    ///
    /// This is the expensive half of the gate story. The cheap structural checks — posture compatibility,
    /// channel conflicts, unbound contacts — run in Python before anything reaches the engine, because
    /// they need no geometry and the agent can iterate on them without a round trip. What cannot be
    /// decided there is whether the composed motion is physically wrong once it exists, and that needs
    /// the real skeleton at real frames.
    ///
    /// SOME THRESHOLDS ARE PRINCIPLED AND SOME ARE NOT, and the difference is stated per metric rather
    /// than papered over. A hand "holding" an object 20 cm away is wrong by inspection; a foot below the
    /// floor is wrong by geometry. Foot skate is not like that: what counts as too much sliding depends
    /// on the clip and the frame rate, and the honest thing is to measure it, report it, and calibrate a
    /// threshold from the corpus the way the v2 divisors were calibrated. Metrics with no defensible
    /// threshold report `null` and never fail a plan — a made-up cutoff would produce confident
    /// rejections with nothing behind them.
    ///
    /// Sampling happens in LateUpdate, after the composer's graph and the rig's IK have both written
    /// the pose, so what is measured is what a viewer would see.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class GateProbe : MonoBehaviour
    {
        // A hand meant to be holding something, this far from it, reads as not holding it.
        private const float ContactHoldToleranceM = 0.02f;
        // Below the floor is below the floor. A centimetre of slack absorbs foot-IK jitter.
        private const float GroundPenetrationToleranceM = 0.01f;
        // How far above its own sole height a foot may sit and still count as down. Small on purpose:
        // this decides which frames the skate metric SEES, and every centimetre added to it starts
        // counting swing frames, where the foot is supposed to be travelling.
        private const float PlantedToleranceM = 0.02f;

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
            public float ExpectedAt;      // realtimeSinceStartup by which the landing should be judgeable
            public float StartHipY;       // where the plan said the hips would start
            public float TargetHipY;      // and where it said they would end
            public float HorizontalMiss = -1f;
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
        private bool _running;
        private int _frames;
        private float _startedAt;

        /// <summary>Begin measuring a plan. `contacts` are the effector bindings the plan committed to,
        /// which is what makes "did the grounding hold?" answerable at all.</summary>
        /// <summary>`dueByEffector` says when each binding starts being due, keyed by effector. A hand
        /// cannot be on a keyboard while she is still walking to it, and judging from frame zero failed
        /// every multi-step plan that bound anything — measured, on a walk-then-sit-then-type plan whose
        /// hands were on the laptop the whole time they were supposed to be.</summary>
        public void Begin(List<KeyValuePair<string, Transform>> boundEffectors,
                          Dictionary<string, Transform> bones,
                          Transform leftFoot, Transform rightFoot, float groundY,
                          Dictionary<string, float> dueByEffector = null)
        {
            _contacts.Clear();
            for (int i = 0; i < boundEffectors.Count; i++)
            {
                Transform bone;
                if (!bones.TryGetValue(boundEffectors[i].Key, out bone) || bone == null) continue;
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
            Animator rig = GetComponent<Animator>();
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
            _startedAt = Time.realtimeSinceStartup;
            _running = true;
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
        /// `judgeableInSeconds` is how long from now the landing should be answerable — the caller knows
        /// it because it is the same schedule the descent runs on. It is an estimate used only to tell a
        /// waiting caller roughly how long to wait; `Armed` is what actually decides.</summary>
        public void ExpectSupport(string objectId, Transform seat, float surfaceY, Transform hips,
                                  float judgeableInSeconds, float startHipY = -1f, float targetHipY = -1f)
        {
            if (seat == null || hips == null) return;
            Bounds footprint = new Bounds(seat.position, Vector3.zero);
            Renderer[] renderers = seat.GetComponentsInChildren<Renderer>(true);
            for (int i = 0; i < renderers.Length; i++) footprint.Encapsulate(renderers[i].bounds);
            _support = new Support
            {
                ObjectId = objectId, Footprint = footprint, SurfaceY = surfaceY, Hips = hips,
                StartHipY = startHipY, TargetHipY = targetHipY,
                ExpectedAt = Time.realtimeSinceStartup + Mathf.Max(0f, judgeableInSeconds)
            };
        }

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
            _support.VerticalGap = hips.y - _support.SurfaceY;
            _support.HipMiss = Mathf.Abs(hips.y - _support.TargetHipY);
            _support.Armed = true;
        }

        public void Stop()
        {
            _running = false;
        }

        private void LateUpdate()
        {
            if (!_running) return;
            _frames++;

            float elapsed = Time.realtimeSinceStartup - _startedAt;

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
                if (p.y - _groundY < _footBottom[i] + PlantedToleranceM && Time.deltaTime > 0f)
                {
                    Vector3 delta = p - _lastFootPos[i];
                    delta.y = 0f;
                    float speed = delta.magnitude / Time.deltaTime;
                    if (speed > _worstFootSkate) _worstFootSkate = speed;
                }
                _lastFootPos[i] = p;
            }
        }

        /// <summary>The verdict so far. Safe to call while still running.</summary>
        public Dictionary<string, object> Report()
        {
            List<object> metrics = new List<object>();

            for (int i = 0; i < _contacts.Count; i++)
            {
                Tracked c = _contacts[i];
                if (!c.Judged)
                {
                    metrics.Add(Pending("contact_hold:" + c.Effector,
                                        Mathf.Max(0f, c.DueAt - (Time.realtimeSinceStartup - _startedAt)),
                                        "the step this binding belongs to has not started"));
                    continue;
                }
                metrics.Add(Metric(
                    "contact_hold:" + c.Effector, c.WorstError, ContactHoldToleranceM, c.WorstFrame,
                    "metres between " + c.Effector + " and the object it was bound to, worst frame"));
            }

            for (int i = 0; i < _declared.Count; i++)
            {
                Tracked c = _declared[i];
                if (!c.Judged)
                {
                    metrics.Add(Pending("contact_reached:" + c.Effector,
                                        Mathf.Max(0f, c.DueAt - (Time.realtimeSinceStartup - _startedAt)),
                                        "the step that touches " + c.ObjectId + " has not started"));
                    continue;
                }
                metrics.Add(Metric(
                    "contact_reached:" + c.Effector, c.BestError, ContactHoldToleranceM, c.WorstFrame,
                    "metres between " + c.Effector + " and " + c.ObjectId + " at their closest. The "
                    + "clip animates this hand against that object by itself, so this measures whether "
                    + "the character is placed where the animation expects the object to be"));
            }

            metrics.Add(Metric("ground_penetration", _worstPenetration, GroundPenetrationToleranceM,
                               _worstPenetrationFrame,
                               "metres a foot went below the floor, worst frame"));

            // No defensible threshold yet -- see the class remarks. Measured and reported, never fatal.
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
                                   + _support.ObjectId + "; a sit that lands on nothing is not a sit"));
                // Reported, not judged: how far a pelvis should sit above a seat surface depends on the
                // avatar, and one measurement is not a calibration.
                metrics.Add(Metric("pelvis_above_surface", _support.VerticalGap, -1f, _frames,
                                   "metres between the pelvis and the top of " + _support.ObjectId
                                   + "; reported only"));

                // HOW FAR ABOVE is not calibrated. BELOW is not a matter of degree. Containment alone
                // certified a sit on the laptop: the pelvis was inside its footprint because the laptop
                // was on the desk directly over her, and 0.70 m underneath the surface she was
                // reported to be sitting on. Same shape as ground penetration and the same tolerance --
                // a centimetre for mesh slack, and past that she is inside the furniture.
                metrics.Add(Metric("sat_through_support",
                                   Mathf.Max(0f, -_support.VerticalGap), GroundPenetrationToleranceM,
                                   _frames,
                                   "metres the pelvis ended up BELOW the top of " + _support.ObjectId
                                   + "; being under a surface is not sitting on it"));

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
                                       + "change did not happen"));

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
                waitFor = Mathf.Max(0f, _support.ExpectedAt - Time.realtimeSinceStartup);
                metrics.Add(Pending("seated_on_support", waitFor,
                                    "the descent onto " + _support.ObjectId + " has not finished, so "
                                    + "where the pelvis lands is not measurable yet"));
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
                { "seconds", Time.realtimeSinceStartup - _startedAt },
                { "metrics", metrics }
            };
        }

        /// <summary>A check that has been declared but cannot be answered yet. Carries no `measured`,
        /// because there is nothing to measure — a zero there would read as a perfect score.</summary>
        private static object Pending(string id, float waitFor, string what)
        {
            return new Dictionary<string, object>
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
        }

        private static object Metric(string id, float measured, float tolerance, int frame, string what)
        {
            bool judged = tolerance >= 0f;
            return new Dictionary<string, object>
            {
                { "id", id },
                { "measured", measured },
                { "tolerance", judged ? (object)tolerance : null },
                { "status", !judged ? "measured" : (measured <= tolerance ? "pass" : "fail") },
                { "over_by", judged && measured > tolerance ? (object)(measured - tolerance) : null },
                { "worst_frame", frame },
                { "what", what }
            };
        }
    }
}
