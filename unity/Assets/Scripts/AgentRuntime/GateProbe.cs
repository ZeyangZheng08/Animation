using System.Collections.Generic;
using UnityEngine;

namespace AgentRuntime
{
    /// <summary>
    /// Measures the pose that is actually being played, and reports it back as a gate verdict.
    ///
    /// THIS IS THE CLOCK, NOT THE JUDGEMENT. Every threshold, every accumulation and every metric shape
    /// now lives in <see cref="GateEvaluator"/>, which knows nothing about frames or wall clocks. What
    /// is left here is a MonoBehaviour that samples in LateUpdate — after the composer's graph and the
    /// rig's IK have both written the pose, so what is measured is what a viewer would see — and hands
    /// the evaluator real elapsed seconds.
    ///
    /// The reason for the split is that there are two clocks. <see cref="ValidationCharacter"/> runs
    /// the same evaluator over a hidden duplicate at fixed timestep, before anything visible has moved.
    /// If the thresholds lived in both places they would drift, and the first sign of the drift would
    /// be a plan that passed the pre-execution check and then failed the runtime one — which reads as a
    /// bug in the motion rather than in the pair of numbers.
    ///
    /// AND ITS JOB CHANGED WITH v4. It used to be the only verdict there was, arriving seconds after a
    /// viewer had already watched a bad sit land. A plan is now checked before it plays, so what this
    /// watches for is the real scene doing something the hidden copy could not know about: the seat
    /// moved, somebody else picked the thing up, the route changed under her.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class GateProbe : MonoBehaviour
    {
        private readonly GateEvaluator _gate = new GateEvaluator();
        private bool _running;
        private float _startedAt;
        private float _lastSampleAt;

        private float Elapsed { get { return Time.realtimeSinceStartup - _startedAt; } }

        /// <summary>Begin measuring a plan, from the plan itself. What to watch is decided by
        /// <see cref="GateArming"/>, which the pre-execution validator uses as well — so the check that
        /// runs before a plan plays and the one that runs while it plays are watching the same things
        /// by construction rather than by two people remembering to keep them in step.</summary>
        public void Arm(Request request, SceneQueryService scene, List<object> applied,
                        Animator animator, IkBinder binder,
                        Dictionary<string, Transform> effectorBones, float groundY)
        {
            GateArming.Arm(_gate, request, scene, applied, animator, binder, effectorBones, groundY);
            _startedAt = Time.realtimeSinceStartup;
            _lastSampleAt = _startedAt;
            _running = true;
        }

        /// <summary>`judgeableInSeconds` is how long FROM NOW the landing should be answerable, which is
        /// what the caller naturally knows — it is the same schedule the descent runs on. The evaluator
        /// works in seconds into the plan, so it is converted here, where the clock is.</summary>
        public void ExpectSupport(string objectId, Transform seat, float surfaceY, Transform hips,
                                  float judgeableInSeconds, float startHipY = -1f,
                                  float targetHipY = -1f)
        {
            _gate.ExpectSupport(objectId, seat, surfaceY, hips,
                                Elapsed + Mathf.Max(0f, judgeableInSeconds), startHipY, targetHipY);
        }

        public void SupportLanded(float biasM = 0f, int droppedWrites = 0, int saturatedFrames = 0,
                                  float seatOffsetM = 0f, float seatNeededM = 0f)
        {
            _gate.SupportLanded(biasM, droppedWrites, saturatedFrames, seatOffsetM, seatNeededM);
        }

        public void Stop()
        {
            _running = false;
        }

        private void LateUpdate()
        {
            if (!_running) return;
            float now = Time.realtimeSinceStartup;
            _gate.Sample(now - _startedAt, now - _lastSampleAt);
            _lastSampleAt = now;
        }

        /// <summary>The verdict so far. Safe to call while still running.</summary>
        public Dictionary<string, object> Report()
        {
            return _gate.Report(Elapsed, Elapsed);
        }
    }
}
