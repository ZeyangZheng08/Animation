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
        private float _elapsed;

        // The open-loop shape of the descent: hip curve, root slide, foot goals, and the geometric
        // limit on the command. Shared with the pre-execution validator, which replays this same
        // struct at fixed timestep -- see PostureTransitionEvaluator for why that split exists.
        private PostureTransitionEvaluator _curve;

        private float _bias;                    // accumulated body-vs-hips offset, learned each frame
        private float _worstError;
        private int _dropped;                   // frames whose correction the composer could not accept
        private int _saturated;                 // frames on which the commanded drop hit its limit
        private bool _feetCaptured;
        private Vector3 _seatOffset;
        private float _seatNeeded;
        private Vector3? _pelvisTarget;
        private Vector3 _pelvisFrom;

        /// <summary>How far a generated descent may pull the pelvis sideways, in metres.
        ///
        /// The navigation mesh stops about 0.15 m short of a seat and a real sit-down step covers
        /// about 0.45 m, so 0.35 m is the stand-off a descent can honestly be asked to make up.
        /// Past it the plan has put her somewhere a sit does not reach from, and the check below says
        /// so with the distance rather than producing a slide.</summary>
        public const float MaxSeatOffsetM = 0.35f;

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

        /// <summary>How far the descent had to pull the PELVIS sideways onto the seat, in metres, and
        /// how far it was ASKED to. `Needed` is measured once at the start; `SeatOffsetM` is what was
        /// actually commanded, which is the same number unless the cap bit.</summary>
        public float SeatOffsetM { get { return _seatOffset.magnitude; } }
        public float SeatOffsetNeededM { get { return _seatNeeded; } }

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
        /// <param name="pelvisTarget">Where the PELVIS has to end up, in world XZ — the seat itself.
        /// Given only for a descent that lands ON something. `landOn` slides the ROOT, which is under
        /// her feet; on a seated pose the pelvis sits about 0.17 m behind the root, so a root that
        /// arrives on the seat centre leaves the pelvis off the back of it. `seat_alignment` measures
        /// the pelvis, and this is what closes that gap. Null on a rise: there is nothing to land on,
        /// and pulling the pelvis onto a stand anchor would be inventing a target.</param>
        public void Begin(float targetHipY, float duration, Vector3? landOn = null,
                          Vector3? leftFootLocal = null, Vector3? rightFootLocal = null,
                          Vector3? pelvisTarget = null)
        {
            Transform hips = Hips();
            if (hips == null || composer == null) return;
            _curve = new PostureTransitionEvaluator
            {
                StartHipY = hips.position.y,
                TargetHipY = targetHipY,
                DurationSeconds = Mathf.Max(0.05f, duration),
                // How far the pelvis is above the character's own origin, which is the space the
                // correction is written in. Nothing can be lowered further than that without going
                // through the floor, so it is the limit on the commanded drop -- read off this
                // character rather than chosen.
                Reach = Mathf.Abs(hips.position.y - transform.position.y),
                LeftTargetLocal = leftFootLocal,
                RightTargetLocal = rightFootLocal
            };
            _elapsed = 0f;
            _bias = 0f;
            _worstError = 0f;
            _dropped = 0;
            _saturated = 0;
            _feetCaptured = false;
            _seatOffset = Vector3.zero;
            _seatNeeded = 0f;
            _pelvisTarget = null;
            _pelvisFrom = hips.position;
            if (pelvisTarget.HasValue)
            {
                Vector3 want = new Vector3(pelvisTarget.Value.x, hips.position.y, pelvisTarget.Value.z);
                // WHAT THE ROOT SLIDE WILL ALREADY DO. `landOn` carries the whole body across, so the
                // pelvis only has to make up what is left after that -- which is the seated pose's own
                // hip-behind-root offset, not the whole distance to the chair.
                Vector3 rootShift = landOn.HasValue
                    ? new Vector3(landOn.Value.x - transform.position.x, 0f,
                                  landOn.Value.z - transform.position.z)
                    : Vector3.zero;
                _pelvisTarget = want;
                _seatNeeded = (want - (hips.position + rootShift)).magnitude;
            }

            // Said once, up front, where it is cheap to act on. Beginning a descent against a graph that
            // holds no correction job is not a marginal condition — every frame of it will be discarded
            // and the character will simply not move, which is the failure that used to present as a
            // runaway number in a field nobody printed.
            if (!composer.CorrectionLive)
            {
                Debug.LogError("[AgentRuntime] descent started with no correction in the playback graph "
                               + "on " + name + "; every frame of it will be discarded");
            }

            _curve.Translating = landOn.HasValue;
            _curve.StartPos = transform.position;
            if (_curve.Translating)
            {
                Vector3 want = landOn.Value;
                // XZ only; the floor is the floor.
                _curve.TargetPos = new Vector3(want.x, _curve.StartPos.y, want.z);
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
            Step(Time.deltaTime);
        }

        /// <summary>One step of the descent, by `dt` seconds.
        ///
        /// SPLIT OUT OF LateUpdate SO SOMETHING OTHER THAN THE FRAME LOOP CAN DRIVE IT. The
        /// pre-execution validator replays a whole plan on a hidden duplicate as fast as the CPU will
        /// go, and a descent that only advanced on rendered frames would take a real second there --
        /// which would put the length of the animation inside the answer to "does this plan work". The
        /// loop below is unchanged; only where `dt` comes from is different, and that is deliberately
        /// the only difference.
        /// </summary>
        public void Step(float dt)
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
                _curve.LeftFrom = leftFoot.position;
                _curve.RightFrom = rightFoot.position;
                correction.leftRot = leftFoot.rotation;
                correction.rightRot = rightFoot.rotation;
                _feetCaptured = true;
            }

            _elapsed += dt;
            float t = _curve.Phase(_elapsed);
            float wantHipY = _curve.HipHeightAt(t);

            if (_curve.Translating)
            {
                // Moved BEFORE the feet are read below, so the pinned goals are captured at the start
                // position and the legs then resolve the step for us.
                transform.position = _curve.RootAt(t, transform.position.y);
            }

            // AND THE PELVIS ARRIVES ON THE SEAT, not merely at the seat's height. The root slide
            // above puts her FEET where the chair is; on a seated pose the pelvis hangs about 0.17 m
            // behind the root, so without this she lands with her hips off the back edge -- which is
            // what `seat_alignment` measures and what a generated descent kept failing on. Same closed
            // loop as the hip drop: command where it should be at this phase, read where it is, fold
            // the error back in. Capped at `MaxSeatOffsetM`, because past that the descent is being
            // asked to carry her further than a sit-down step covers and the plan is wrong.
            if (_pelvisTarget.HasValue)
            {
                Vector3 want = Vector3.Lerp(_pelvisFrom, _pelvisTarget.Value,
                                            PostureTransitionEvaluator.Ease(t));
                Vector3 drift = new Vector3(want.x - hips.position.x, 0f, want.z - hips.position.z);
                _seatOffset += drift * correctionGain;
                _seatOffset = Vector3.ClampMagnitude(_seatOffset, MaxSeatOffsetM);
                correction.seatOffset = transform.InverseTransformVector(_seatOffset);
            }

            // The feet move too, from where the walk left them to where the incoming clip puts them.
            // Without this the result is a squat in a walking stride: the hips arrive at the right
            // height with one foot still forward and one still back, which is what the first version
            // produced and what no metric caught. The targets are character-local, so they follow the
            // root as it slides onto the seat.
            correction.leftFoot = _curve.LeftFootAt(t, transform);
            correction.rightFoot = _curve.RightFootAt(t, transform);

            // Closed loop: the drop that produced the CURRENT hip height was `bodyDrop`, so the error
            // between where the hips are and where they should be is the correction to fold in.
            float error = hips.position.y - wantHipY;
            if (Mathf.Abs(error) > _worstError) _worstError = Mathf.Abs(error);
            _bias += error * correctionGain;

            // AND BOUNDED, because an integrator against a pose that will not move runs away. Measured
            // on an agent run where the descent had no effect on the skeleton: the error stayed at the
            // full travel every frame and `bodyDrop` reached 24.7 m, a number with no physical meaning
            // that the character calmly ignored while every geometric check passed. The limit itself,
            // and why it is on the command rather than on the integrator, is documented where it now
            // lives -- PostureTransitionEvaluator.CommandedDrop. `_saturated` counts the frames it had
            // to bite, `_dropped` counts the frames the graph never received, and the gate judges both.
            bool saturated;
            correction.bodyDrop = _curve.CommandedDrop(wantHipY, _bias, out saturated);
            if (saturated) _saturated++;
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

        private Transform Hips()
        {
            if (_animator == null) _animator = GetComponent<Animator>();
            return _animator == null || !_animator.isHuman
                ? null : _animator.GetBoneTransform(HumanBodyBones.Hips);
        }
    }
}
