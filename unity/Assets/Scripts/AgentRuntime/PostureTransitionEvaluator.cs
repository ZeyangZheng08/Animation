using UnityEngine;

namespace AgentRuntime
{
    /// <summary>
    /// The shape of a generated posture change, as a function of how far through it you are.
    ///
    /// WHY THIS IS SEPARATE FROM PoseSynth. There are two clocks that need this curve. At runtime a
    /// coroutine waits for the seam and then <see cref="PoseSynth"/> advances it by
    /// <c>Time.deltaTime</c>, once per rendered frame, over the real second or so a sit takes. Before
    /// execution, <see cref="ValidationCharacter"/> has to run the same descent on a hidden duplicate
    /// at fixed timestep, as fast as the CPU will go — waiting a real second to find out whether a plan
    /// works would put the length of the animation inside the answer.
    ///
    /// Those two differ in where the seconds come from and in nothing else. A second implementation of
    /// the curve for the fast path would be a second place for "what a sit looks like" to live, and the
    /// first sign of the two disagreeing would be a plan that validated and then played differently —
    /// which is the one failure this whole path exists to prevent. So the arithmetic is here, once, and
    /// both callers supply `t`.
    ///
    /// WHAT IS NOT HERE: the closed loop. PoseSynth reads the skeleton back each frame and folds the
    /// measured hip error into a bias, because the job writes the humanoid BODY and what has to land on
    /// the seat is the HIPS bone — pinning the feet bends the knees, which moves one relative to the
    /// other. That needs a skeleton to read, so it stays with the component that has one. This is the
    /// open-loop command the loop corrects; keeping the split explicit is what makes the fast path a
    /// faithful replay rather than an approximation of one.
    ///
    /// BOTH DIRECTIONS, AND THEY ARE THE SAME FUNCTION. Standing up is sitting down with the two hip
    /// heights the other way round. That was already true of everything that computed it — `schedule`
    /// derives the travel as an absolute value, the clamp below is symmetric — and it stays true here.
    /// </summary>
    public struct PostureTransitionEvaluator
    {
        public float StartHipY;
        public float TargetHipY;
        public float DurationSeconds;

        /// <summary>How far the pelvis stands above the character's own origin, which is the space the
        /// correction is written in. Nothing can be lowered further than that without going through the
        /// floor, so it is the limit on the commanded drop — read off the character, never chosen.</summary>
        public float Reach;

        /// <summary>Whether the root slides as well as descends. The navigation mesh does not extend
        /// under a chair, so the closest she can WALK to a seat is about 0.15 m from it and no amount
        /// of lowering the hips from there puts her on it. A real sit contains that backward step.</summary>
        public bool Translating;
        public Vector3 StartPos;
        public Vector3 TargetPos;

        /// <summary>Where the outgoing motion left the feet, in world space, captured at the first
        /// frame of the descent. Later would be wherever the half-finished blend had dragged them.</summary>
        public Vector3 LeftFrom;
        public Vector3 RightFrom;

        /// <summary>Where the incoming clip puts them, in character-local space so they follow the root
        /// as it slides onto the seat. Absent means leave that foot where it was: without this the
        /// result is a squat in a walking stride, hips at the right height with one foot still forward
        /// and one still back, which is what the first version produced and what no metric caught.</summary>
        public Vector3? LeftTargetLocal;
        public Vector3? RightTargetLocal;

        /// <summary>Smoothstep. A linear descent starts and stops instantly, which reads as being
        /// lowered on a string; a real sit accelerates and settles.</summary>
        public static float Ease(float t)
        {
            return t * t * (3f - 2f * t);
        }

        /// <summary>How far through, from seconds since the descent began. Clamped, so a caller that
        /// oversteps the end gets the arrived pose rather than an extrapolation.</summary>
        public float Phase(float elapsed)
        {
            return Mathf.Clamp01(elapsed / Mathf.Max(0.05f, DurationSeconds));
        }

        public float HipHeightAt(float t)
        {
            return Mathf.Lerp(StartHipY, TargetHipY, Ease(t));
        }

        /// <summary>Where the root should be at `t`. XZ only — the floor is the floor — so the caller's
        /// current height is passed through untouched.</summary>
        public Vector3 RootAt(float t, float keepY)
        {
            if (!Translating) return new Vector3(StartPos.x, keepY, StartPos.z);
            Vector3 slide = Vector3.Lerp(StartPos, TargetPos, Ease(t));
            return new Vector3(slide.x, keepY, slide.z);
        }

        public Vector3 LeftFootAt(float t, Transform root)
        {
            return FootAt(t, LeftFrom, LeftTargetLocal, root);
        }

        public Vector3 RightFootAt(float t, Transform root)
        {
            return FootAt(t, RightFrom, RightTargetLocal, root);
        }

        private static Vector3 FootAt(float t, Vector3 from, Vector3? targetLocal, Transform root)
        {
            if (!targetLocal.HasValue || root == null) return from;
            return Vector3.Lerp(from, root.TransformPoint(targetLocal.Value), Ease(t));
        }

        /// <summary>The drop to command this frame, given the loop's accumulated bias, and whether it
        /// had to be clipped.
        ///
        /// BOUNDED ON THE COMMAND, NOT ON THE INTEGRATOR. An earlier version clamped the bias to the
        /// claimed travel, which sounds like the plan's own number and is not a limit on anything
        /// physical — measured on the real path, the bias legitimately reaches 92% of it, because the
        /// incoming seated clip lowers the hips faster than the commanded curve and the loop spends the
        /// descent subtracting. Clipping there would have started fighting a correct correction. What
        /// genuinely cannot happen is lowering the body further than the pelvis stands above the
        /// character's own origin, so that is the limit, and it is geometry.
        ///
        /// The clamp keeps the number finite; it does not make the descent work, and it must never be
        /// read as having fixed anything. `saturated` is what says it had to bite, and the gate judges
        /// that count against zero.
        /// </summary>
        public float CommandedDrop(float wantHipY, float bias, out bool saturated)
        {
            float raw = (StartHipY - wantHipY) + bias;
            saturated = Mathf.Abs(raw) > Reach;
            return Mathf.Clamp(raw, -Reach, Reach);
        }
    }
}
