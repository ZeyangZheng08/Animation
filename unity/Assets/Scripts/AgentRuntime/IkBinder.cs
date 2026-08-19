using UnityEngine;
using UnityEngine.Animations.Rigging;

namespace AgentRuntime
{
    /// <summary>
    /// Binds an effector to an arbitrary scene object, which is what turns a retrieved motion into a
    /// motion aimed at a real thing.
    ///
    /// WHY NOT REUSE NurseIKHelper. That component is the existing prototype and it works, but its
    /// targets are seven serialized fields — laptop, pulseLeft, pulseRight, bvm, aspirinLeft,
    /// aspirinRight, pickUp — one per authored scenario. That is exactly the pre-enumerated interaction
    /// template this research argues against: a new object in the scene needs a new field and a
    /// recompile. This takes the target as an argument.
    ///
    /// What IS reused is its mechanism, because it is right and it is what the scene already looks like:
    /// ramp the constraint weight rather than snapping it, and copy target and hint onto the constraint
    /// every frame while tracking.
    ///
    /// TEARDOWN IS OURS. AnimatorIkHelper zeroes IK weights from OnStateExit, which only fires on an
    /// Animator state transition. Under the composer the controller is idle and never transitions, so
    /// that hook never runs and the IK would stay stuck on after a plan ends. ReleaseAll is the
    /// replacement, and AgentCharacter calls it at the top of every commit.
    ///
    /// A BINDING BELONGS TO A STEP, NOT TO A PLAN, so it carries the second it comes due and holds at
    /// zero weight until then. Everything used to engage the moment a plan was committed: a plan that
    /// walked to a laptop and then typed on it pulled both wrists onto the keyboard anchor from the
    /// first frame, so the walk played with her arms stretched toward the desk. Nothing reported it —
    /// every geometric check is about where she ends UP. Which second belongs to which hand is decided
    /// agent-side from the knowledge base (`typing` records both hands on a keyboard, `walking`
    /// records them free) and arrives on the wire; this side only waits.
    ///
    /// The clock is the composer's, deliberately: the seam, the generated descent and these bindings
    /// all have to agree about when a step begins, and there is only one thing that knows.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class IkBinder : MonoBehaviour
    {
        [SerializeField] private TwoBoneIKConstraint leftHand;
        [SerializeField] private TwoBoneIKConstraint rightHand;
        [SerializeField] private MultiAimConstraint headAim;

        [Tooltip("Where the seconds a binding waits for are measured from. Found on this object if "
                 + "not set.")]
        [SerializeField] private MotionComposer composer;

        [Tooltip("Weight units per second. Matches NurseIKHelper's ramp so the look is unchanged.")]
        [SerializeField] private float weightChangeSpeed = 4f;

        private Transform _leftTarget, _rightTarget, _gazeTarget;
        private Transform _leftHint, _rightHint;
        private float _leftWant, _rightWant, _gazeWant;
        private double _leftAt, _rightAt, _gazeAt;

        // What the head aim was pointed at before any plan touched it, captured once. ReleaseAll's
        // contract is that the gaze goes back to whatever the rig was authored with, and that was only
        // half true: it restored the WEIGHT and left the source, so a plan that had looked at the
        // patient kept looking at the patient — through the next plan, and the walk after that.
        private Transform _authoredAim;
        private float _authoredAimWeight;
        private bool _capturedAim;

        private void Awake()
        {
            if (composer == null) composer = GetComponent<MotionComposer>();
            CaptureAuthoredAim();
        }

        private void CaptureAuthoredAim()
        {
            if (_capturedAim || headAim == null) return;
            var sources = headAim.data.sourceObjects;
            if (sources.Count > 0)
            {
                _authoredAim = sources[0].transform;
                _authoredAimWeight = sources[0].weight;
            }
            _capturedAim = true;
        }

        /// <summary>Seconds into the motion currently playing, or 0 when nothing is.</summary>
        private double Elapsed { get { return composer == null ? 0.0 : composer.Elapsed; } }

        /// <summary>How long a binding takes to arrive once it comes due. The weight ramps rather than
        /// snapping, which is what makes a reach read as a reach — so for this long after a binding
        /// starts, the hand is in transit between the clip's pose and the anchor and is not holding
        /// anything yet. The gate reads this so it does not judge the journey.</summary>
        public float RampSeconds
        {
            get { return weightChangeSpeed > 0f ? 1f / weightChangeSpeed : 0f; }
        }

        private void Update()
        {
            Step(Time.deltaTime);
        }

        /// <summary>One step of the ramps, by `dt` seconds.
        ///
        /// SPLIT OUT OF Update FOR THE SAME REASON PoseSynth's Step WAS. The pre-execution validator
        /// replays a whole plan on a hidden duplicate inside a single frame, so a weight that only
        /// moved on rendered frames would still be at zero when the run finished -- and the check would
        /// then be measuring a pose with no IK in it at all, which is not the pose that is going to
        /// play. A ramp is part of what a reach looks like, so it has to be part of what is judged.
        /// </summary>
        public void Step(float dt)
        {
            Track(leftHand, _leftTarget, _leftHint, _leftWant, _leftAt, dt);
            Track(rightHand, _rightTarget, _rightHint, _rightWant, _rightAt, dt);

            if (headAim != null)
            {
                if (_gazeTarget != null) SetAimSource(headAim, _gazeTarget);
                headAim.weight = Mathf.MoveTowards(headAim.weight, Due(_gazeWant, _gazeAt),
                    weightChangeSpeed * dt);
            }
        }

        /// <summary>The weight this binding is asking for right now: nothing until its step begins,
        /// then what it was bound at. Releasing is never held back — a want of zero applies at once,
        /// so a plan that unbinds a hand does not have to wait for a step that will not come.</summary>
        private float Due(float want, double atSeconds)
        {
            if (want <= 0f) return 0f;
            return Elapsed >= atSeconds ? want : 0f;
        }

        private void Track(TwoBoneIKConstraint constraint, Transform target, Transform hint,
                           float want, double atSeconds, float dt)
        {
            if (constraint == null) return;
            if (target != null && constraint.data.target != null)
            {
                constraint.data.target.position = target.position;
                constraint.data.target.rotation = target.rotation;
            }
            // The hint decides which way the elbow folds. NurseIKHelper copies it alongside the target
            // and this did not, so a bound arm reached the right point with the elbow wherever the
            // rig's authored hint happened to leave it.
            if (hint != null && constraint.data.hint != null)
            {
                constraint.data.hint.position = hint.position;
            }
            constraint.weight = Mathf.MoveTowards(constraint.weight, Due(want, atSeconds),
                weightChangeSpeed * dt);
        }

        /// <summary>Aim an effector at a target from `atSeconds` into the motion. Zero means the first
        /// frame, which is what a one-step plan and every binding before this took.</summary>
        public bool Bind(string effector, Transform target, Transform hint = null,
                         double atSeconds = 0.0)
        {
            if (effector == "left_hand" && leftHand != null)
            {
                _leftTarget = target; _leftHint = hint; _leftWant = 1f; _leftAt = atSeconds;
                return true;
            }
            if (effector == "right_hand" && rightHand != null)
            {
                _rightTarget = target; _rightHint = hint; _rightWant = 1f; _rightAt = atSeconds;
                return true;
            }
            return false;
        }

        public bool BindGaze(Transform target, double atSeconds = 0.0)
        {
            if (headAim == null || target == null) return false;
            _gazeTarget = target;
            _gazeWant = 1f;
            _gazeAt = atSeconds;
            return true;
        }

        /// <summary>What each effector is aiming at and when it comes due — for the plan's reply, so a
        /// binding that has not engaged yet is visible as scheduled rather than as missing.</summary>
        public double DueAt(string effector)
        {
            if (effector == "left_hand") return _leftAt;
            if (effector == "right_hand") return _rightAt;
            return 0.0;
        }

        /// <summary>Ramp everything back to zero, and point the head where the rig had it.
        ///
        /// Must be called on plan teardown — nothing else will. The gaze goes back to whatever the rig
        /// was authored with rather than to zero, because HeadAim ships at weight 1 in this scene and
        /// zeroing it would be a visible change we did not intend to make. That claim used to be half
        /// true: the WEIGHT was left alone and the SOURCE was not restored, so a plan that had looked
        /// at the patient went on looking at the patient — through the next plan, and the walk after
        /// it, with her head turned back over her shoulder the whole way.
        /// </summary>
        public void ReleaseAll()
        {
            _leftTarget = _rightTarget = _gazeTarget = null;
            _leftHint = _rightHint = null;
            _leftWant = _rightWant = 0f;
            _leftAt = _rightAt = _gazeAt = 0.0;
            _gazeWant = headAim != null ? headAim.weight : 0f;
            if (headAim != null && _capturedAim && _authoredAim != null)
            {
                SetAimSource(headAim, _authoredAim, _authoredAimWeight);
            }
        }

        private static void SetAimSource(MultiAimConstraint aim, Transform target, float weight = 1f)
        {
            var sources = aim.data.sourceObjects;
            if (sources.Count == 0)
            {
                sources.Add(new WeightedTransform(target, weight));
            }
            else if (sources[0].transform != target || sources[0].weight != weight)
            {
                sources.SetTransform(0, target);
                sources.SetWeight(0, weight);
            }
            aim.data.sourceObjects = sources;
        }
    }
}
