using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AgentRuntime
{
    /// <summary>
    /// Turning a committed plan into the list of things a <see cref="GateEvaluator"/> should watch.
    ///
    /// WHY THIS IS NOT INSIDE AgentCharacter ANY MORE. Two characters get armed from the same plan: the
    /// visible one, whose probe samples what a viewer sees, and the hidden duplicate the pre-execution
    /// check runs on. If the arming were duplicated, the two would eventually watch different things —
    /// and the symptom would be a plan that passed the check and failed at runtime, or worse, one that
    /// passed both while nobody was measuring the hand that mattered. So which contacts are due, when
    /// they are due, and which bones stand for which effector are decided here, once.
    ///
    /// Nothing in here writes to the scene. It reads the plan and the registry and tells the evaluator
    /// what to look at.
    /// </summary>
    internal static class GateArming
    {
        /// <summary>Which effectors were really bound, out of what the executor reported applying.
        ///
        /// ONLY WHAT WAS ACTUALLY BOUND. This used to re-read the request, so a binding the executor
        /// had refused was still measured — the gate would report a hand failing to stay on an object
        /// it had never been attached to.
        /// </summary>
        public static List<KeyValuePair<string, Transform>> Bound(List<object> applied,
                                                                  SceneQueryService scene)
        {
            List<KeyValuePair<string, Transform>> bound = new List<KeyValuePair<string, Transform>>();
            for (int i = 0; applied != null && i < applied.Count; i++)
            {
                Dictionary<string, object> b = applied[i] as Dictionary<string, object>;
                if (b == null || (string)b["kind"] != "ik" || !(bool)b["ok"]) continue;
                SceneRegistry.Entry e = scene.Registry.ById((string)b["object_id"]);
                if (e == null || e.target == null) continue;
                string effector = (string)b["effector"];
                bound.Add(new KeyValuePair<string, Transform>(effector, e.HandAnchor(effector)));
            }
            return bound;
        }

        /// <summary>Point an evaluator at everything this plan claims about contact and the floor.
        ///
        /// `binder` supplies the ramp: a binding that is still arriving is not holding anything. The
        /// constraint weight ramps in rather than snapping, so for a fraction of a second after a
        /// binding comes due the hand is in transit between the clip's own pose and the anchor. Judged
        /// from the instant it was asked for, contact_hold reports that whole journey as a failure to
        /// hold — measured 0.261 m against a 0.020 m tolerance, on a plan whose hands then settled to
        /// within two micrometres of the anchor. Same rule as the generated descent: a thing that takes
        /// time is not due until it has finished, and the length is read off the binder rather than
        /// restated here.
        /// </summary>
        public static void Arm(GateEvaluator gate, Request request, SceneQueryService scene,
                               List<object> applied, Animator animator, IkBinder binder,
                               Dictionary<string, Transform> effectorBones, float groundY)
        {
            if (gate == null || scene == null || scene.Registry == null) return;

            List<KeyValuePair<string, Transform>> bound = Bound(applied, scene);

            Transform leftFoot = animator == null
                ? null : animator.GetBoneTransform(HumanBodyBones.LeftFoot);
            Transform rightFoot = animator == null
                ? null : animator.GetBoneTransform(HumanBodyBones.RightFoot);

            // WHEN EACH CONTACT FALLS DUE, from the step that declares it. A binding and the contact it
            // grounds belong to the same step, so they share the timing: judging either from frame zero
            // fails on the walk that gets her there.
            JArray declared = request.Arr("expect_contact");
            Dictionary<string, float> dueByEffector = new Dictionary<string, float>();
            for (int i = 0; declared != null && i < declared.Count; i++)
            {
                JObject d = (JObject)declared[i];
                dueByEffector[d.Value<string>("effector")] = d.Value<float?>("due_at_s") ?? 0f;
            }

            for (int i = 0; binder != null && i < bound.Count; i++)
            {
                string effector = bound[i].Key;
                float due;
                dueByEffector.TryGetValue(effector, out due);
                dueByEffector[effector] = Mathf.Max(due, (float)binder.DueAt(effector))
                                          + binder.RampSeconds;
            }

            gate.Begin(bound, effectorBones, animator, leftFoot, rightFoot, groundY, dueByEffector);

            // Contacts the CLIPS make by themselves. Measured even where the hands are bound, because
            // reaching an anchor is not the same as the motion reading right.
            for (int i = 0; declared != null && i < declared.Count; i++)
            {
                JObject d = (JObject)declared[i];
                SceneRegistry.Entry e = scene.Registry.ById(d.Value<string>("object_id"));
                Transform bone;
                if (e == null || e.target == null ||
                    !effectorBones.TryGetValue(d.Value<string>("effector"), out bone)) continue;
                gate.ExpectContact(d.Value<string>("effector"), bone, e.target,
                                   d.Value<string>("object_id"),
                                   d.Value<float?>("due_at_s") ?? 0f);
            }
        }
    }
}
