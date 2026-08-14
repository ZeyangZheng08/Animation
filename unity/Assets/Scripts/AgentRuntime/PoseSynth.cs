using UnityEngine;

namespace AgentRuntime
{
    /// <summary>
    /// Generates the frames that exist in no clip.
    ///
    /// The motion library has eight actions and `typing` is the only seated one, so every route between
    /// sitting and standing crosses a gap nothing in the corpus covers. A crossfade cannot bridge it:
    /// interpolating a stance into a sit moves every joint at once and lowers the character with
    /// straight legs, through a pose nobody performs. What makes it read as sitting is not the
    /// interpolation but the constraints on it — the hips descend along a controlled curve, the feet
    /// stay where they were planted, and the upper body arrives late.
    ///
    /// So this drives MotionComposer.Correction across the seam. The crossfade is still the base signal;
    /// this is what turns it into a descent.
    ///
    /// CLOSED LOOP, NOT OPEN. The job writes the humanoid BODY (the mass centre) and the thing that has
    /// to land on the seat is the HIPS bone, and pinning the feet bends the knees, which moves one
    /// relative to the other. Measured during the spike: a 0.440 m body drop produced 0.473 m of hip
    /// travel, a 3.3 cm error that an open-loop curve would simply deliver. So each frame reads where
    /// the hips actually ended up and corrects toward where they were supposed to be. One frame of lag
    /// at 60 fps is invisible; a 3 cm sit through the seat is not.
    ///
    /// THE TARGET IS NOT GUESSED. It is the first frame of whatever plays next: a sit-down ends in
    /// typing's opening pose because typing is what follows. The seat surface is not the target — it is
    /// what the gate measures the result against, which is a different job and belongs to a different
    /// component.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class PoseSynth : MonoBehaviour
    {
        [SerializeField] private MotionComposer composer;

        [Tooltip("Fraction of the measured hip error corrected per frame. 1 would chase noise; this "
                 + "settles within a few frames without ringing.")]
        [SerializeField, Range(0.05f, 1f)] private float correctionGain = 0.5f;

        [SerializeField] private Locomotion locomotion;

        private Animator _animator;
        private bool _running;
        private float _elapsed, _duration;
        private float _startHipY, _targetHipY;
        private Vector3 _startPos, _targetPos;
        private bool _translating;
        private float _bias;                    // accumulated body-vs-hips offset, learned each frame
        private float _worstError;
        private int _dropped;                   // frames whose correction the composer could not accept
        private int _saturated;                 // frames on which the commanded drop hit its limit
        private float _reach;                   // pelvis height over the character's own origin
        private bool _feetCaptured;
        private Vector3 _leftFrom, _rightFrom;  // where the outgoing motion left the feet, in world space
        private Vector3? _leftTargetLocal, _rightTargetLocal;

        public bool Running { get { return _running; } }

        /// <summary>Peak distance between where the hips were asked to be and where they ended up, over
        /// the whole descent. Reported rather than smoothed away: it is the honest measure of whether
        /// the closed loop kept up.</summary>
        public float WorstTrackingErrorM { get { return _worstError; } }

        /// <summary>The integrator's accumulated correction, in metres. Reported, not judged.
        ///
        /// It has no threshold because it has no expected value. Measured on the real path: a plan whose
        /// hips travelled 0.500 m ended with a bias of -0.459 m and a commanded body drop of 0.039 m —
        /// the loop spent the descent SUBTRACTING, because the incoming seated clip lowers the hips by
        /// itself and does it faster than the commanded curve. Where the correction earns its place is
        /// the shape and the feet, not the distance. A version of this comment claimed the bias settles
        /// at a few centimetres; that came from a spike with no incoming clip under it and is wrong here.
        /// </summary>
        public float BiasM { get { return _bias; } }

        /// <summary>Frames on which the composer refused the write. Anything above zero means the loop
        /// was steering something it was not connected to.</summary>
        public int DroppedWrites { get { return _dropped; } }

        /// <summary>Frames on which the commanded drop hit its limit. THIS is the divergence, and unlike
        /// the bias it needs no threshold: a descent that had to be clipped is one whose loop ran out of
        /// room, whatever the numbers around it look like.</summary>
        public int SaturatedFrames { get { return _saturated; } }

        public float LastHipY
        {
            get
            {
                Transform hips = Hips();
                return hips == null ? -1f : hips.position.y;
            }
        }

        private void Awake()
        {
            if (composer == null) composer = GetComponent<MotionComposer>();
            _animator = GetComponent<Animator>();
        }

        /// <summary>Begin a descent (or a rise) to `targetHipY` over `duration` seconds.
        ///
        /// `landOn`, when given, is a world point the character also translates onto. That is not a
        /// refinement: the navigation mesh does not extend under a chair, so the closest she can WALK
        /// to a seat is about 0.15 m from it, and no amount of lowering the hips from there puts her on
        /// it. A real sit contains that backward step, so the generated one has to as well.
        /// </summary>
        public void Begin(float targetHipY, float duration, Vector3? landOn = null,
                          Vector3? leftFootLocal = null, Vector3? rightFootLocal = null)
        {
            _leftTargetLocal = leftFootLocal;
            _rightTargetLocal = rightFootLocal;
            Transform hips = Hips();
            if (hips == null || composer == null) return;
            _startHipY = hips.position.y;
            _targetHipY = targetHipY;
            _duration = Mathf.Max(0.05f, duration);
            _elapsed = 0f;
            _bias = 0f;
            _worstError = 0f;
            _dropped = 0;
            _saturated = 0;
            // How far the pelvis is above the character's own origin, which is the space the correction
            // is written in. Nothing can be lowered further than that without going through the floor,
            // so it is the limit on the commanded drop -- read off this character rather than chosen.
            _reach = Mathf.Abs(_startHipY - transform.position.y);
            _feetCaptured = false;

            // Said once, up front, where it is cheap to act on. Beginning a descent against a graph that
            // holds no correction job is not a marginal condition — every frame of it will be discarded
            // and the character will simply not move, which is the failure that used to present as a
            // runaway number in a field nobody printed.
            if (!composer.CorrectionLive)
            {
                Debug.LogError("[AgentRuntime] descent started with no correction in the playback graph "
                               + "on " + name + "; every frame of it will be discarded");
            }

            _translating = landOn.HasValue;
            if (_translating)
            {
                _startPos = transform.position;
                Vector3 want = landOn.Value;
                _targetPos = new Vector3(want.x, _startPos.y, want.z);   // XZ only; the floor is the floor
                // The agent has to be off, not merely stopped: measured, isStopped plus
                // updatePosition=false still let it write its own position back over ours.
                if (locomotion != null) locomotion.Suspend();
            }
            _running = true;
        }

        public void Cancel()
        {
            _running = false;
            if (composer != null) composer.SetCorrection(new MotionComposer.Correction());
        }

        /// <summary>LateUpdate, after the composer has advanced its own timeline this frame, and after
        /// the previous frame's pose is readable off the skeleton.</summary>
        private void LateUpdate()
        {
            if (!_running || composer == null || _animator == null) return;

            Transform hips = Hips();
            Transform leftFoot = _animator.GetBoneTransform(HumanBodyBones.LeftFoot);
            Transform rightFoot = _animator.GetBoneTransform(HumanBodyBones.RightFoot);
            if (hips == null || leftFoot == null || rightFoot == null) { _running = false; return; }

            MotionComposer.Correction correction = composer.GetCorrection();
            if (!_feetCaptured)
            {
                // Where the outgoing motion left them. Captured now rather than later, or the start of
                // the slide would be wherever the half-finished blend had dragged them to.
                _leftFrom = leftFoot.position;
                _rightFrom = rightFoot.position;
                correction.leftRot = leftFoot.rotation;
                correction.rightRot = rightFoot.rotation;
                _feetCaptured = true;
            }

            _elapsed += Time.deltaTime;
            float t = Mathf.Clamp01(_elapsed / _duration);
            float wantHipY = Mathf.Lerp(_startHipY, _targetHipY, Ease(t));

            if (_translating)
            {
                // Moved BEFORE the feet are read below, so the pinned goals are captured at the start
                // position and the legs then resolve the step for us.
                Vector3 now = transform.position;
                Vector3 slide = Vector3.Lerp(_startPos, _targetPos, Ease(t));
                transform.position = new Vector3(slide.x, now.y, slide.z);
            }

            // The feet move too, from where the walk left them to where the incoming clip puts them.
            // Without this the result is a squat in a walking stride: the hips arrive at the right
            // height with one foot still forward and one still back, which is what the first version
            // produced and what no metric caught. The targets are character-local, so they follow the
            // root as it slides onto the seat.
            correction.leftFoot = _leftTargetLocal.HasValue
                ? Vector3.Lerp(_leftFrom, transform.TransformPoint(_leftTargetLocal.Value), Ease(t))
                : _leftFrom;
            correction.rightFoot = _rightTargetLocal.HasValue
                ? Vector3.Lerp(_rightFrom, transform.TransformPoint(_rightTargetLocal.Value), Ease(t))
                : _rightFrom;

            // Closed loop: the drop that produced the CURRENT hip height was `bodyDrop`, so the error
            // between where the hips are and where they should be is the correction to fold in.
            float error = hips.position.y - wantHipY;
            if (Mathf.Abs(error) > _worstError) _worstError = Mathf.Abs(error);
            _bias += error * correctionGain;

            // AND BOUNDED, because an integrator against a pose that will not move runs away. Measured
            // on an agent run where the descent had no effect on the skeleton: the error stayed at the
            // full travel every frame and `bodyDrop` reached 24.7 m, a number with no physical meaning
            // that the character calmly ignored while every geometric check passed.
            //
            // BOUNDED ON THE COMMAND, NOT ON THE INTEGRATOR. An earlier version clamped `_bias` to the
            // claimed travel, which sounds like the plan's own number and is not a limit on anything
            // physical — measured on the real path, the bias legitimately reaches 92% of that, because
            // the incoming seated clip lowers the hips faster than the commanded curve and the loop
            // spends the descent subtracting. Clipping there would have started fighting a correct
            // correction. What genuinely cannot happen is lowering the body further than the pelvis
            // stands above the character's own origin, so that is the limit, and it is geometry.
            //
            // The clamp keeps the number finite; it does not make the descent work, and it must never be
            // read as having fixed anything. `_saturated` counts the frames it had to bite, `_dropped`
            // counts the frames the graph never received, and the gate judges both.
            correction.bodyDrop = Mathf.Clamp((_startHipY - wantHipY) + _bias, -_reach, _reach);
            if (Mathf.Abs((_startHipY - wantHipY) + _bias) > _reach) _saturated++;
            correction.footWeight = 1f;
            if (!composer.SetCorrection(correction))
            {
                if (_dropped == 0)
                {
                    Debug.LogWarning("[AgentRuntime] the playback graph stopped accepting the descent "
                                     + "correction on " + name + " part-way through; the hips will stay "
                                     + "where they are from here");
                }
                _dropped++;
            }

            if (t >= 1f)
            {
                // Hold the arrived pose rather than releasing: the next step's clip is already carrying
                // the weight by now, and snapping the correction to zero would pop the hips back up.
                // Deliberately NOT resuming navigation here. She is sitting; the next walk calls Go(),
                // which re-enables the agent, and bringing it back now would warp her to the nearest
                // walkable point -- off the chair she just sat on.
                _running = false;
            }
        }

        /// <summary>Smoothstep. A linear descent starts and stops instantly, which reads as being
        /// lowered on a string; a real sit accelerates and settles.</summary>
        private static float Ease(float t)
        {
            return t * t * (3f - 2f * t);
        }

        private Transform Hips()
        {
            if (_animator == null) _animator = GetComponent<Animator>();
            return _animator == null || !_animator.isHuman
                ? null : _animator.GetBoneTransform(HumanBodyBones.Hips);
        }
    }
}
