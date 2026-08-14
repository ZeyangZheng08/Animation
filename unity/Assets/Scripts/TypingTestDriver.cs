using UnityEngine;
using UnityEngine.AI;

/// <summary>
/// One-off test driver for the "walking -> standing Idle -> seated Typing" demo on Nurse1.
///
/// MOVEMENT: reproduces the in-scope slice of the source scheduler — get the nurse to the computer
/// and into the Typing animator state:
///     Walk_N --(Speed&lt;0.01)--> Idle --(typing trigger)--> Typing
/// driving the float `Speed` from the NavMeshAgent's velocity and firing the `typing` trigger once
/// arrived. That mirrors exactly what NurseAnimator's Walk + Anim("typing") steps do in the source;
/// the rest of the scheduler (intents, queue, position arbitration) is out of scope here.
///
/// HAND IK: kept faithful to the source design — the IK is owned by the REUSED NurseIKHelper, which
/// snaps the two-bone hand IK onto the laptop points and ramps the constraint weights. In the source
/// NurseIKHelper is poked by LaptopIK / ResetHandsIK animation events baked into the Typing clip.
/// Those imported-clip events DO NOT dispatch in this standalone scene (verified: the constraint
/// weight stays 0 throughout the Typing clip even though the event sits at t=0.195s, with no "missing
/// receiver" warning — matching the original hand-off note that the event path never engaged here).
/// So this driver calls the SAME NurseIKHelper API the events would, on Typing enter/exit. NurseIKHelper
/// stays ENABLED and does all the actual IK work — we only supply a reliable trigger.
/// </summary>
[RequireComponent(typeof(Animator))]
public class TypingTestDriver : MonoBehaviour
{
    [Tooltip("Where to walk to (e.g. the Chair at the computer station).")]
    public Transform walkTarget;

    [Tooltip("What to face while typing (e.g. ComputerFace look-at anchor).")]
    public Transform faceTarget;

    [Tooltip("Distance at which the agent is considered to have arrived.")]
    public float arriveRadius = 0.2f;

    [Tooltip("Turn speed (deg/s) used to face the computer once arrived.")]
    public float turnSpeed = 360f;

    [Tooltip("Per-frame IK weight ramp handed to NurseIKHelper.LaptopIK/ResetHandsIK. Matches the " +
             "LaptopIK animation event's value (0.1) in the source; used for release too so the hands " +
             "actually let go (the source's ResetHandsIK event passes 0, which cannot ramp down).")]
    public float ikRampSpeed = 0.1f;

    Animator _anim;
    NavMeshAgent _agent;
    NurseIKHelper _ikHelper;   // reused component; it owns the IK, we just trigger it
    bool _arrived;
    bool _typed;
    bool _ikEngaged;

    void Start()
    {
        _anim = GetComponent<Animator>();
        _agent = GetComponent<NavMeshAgent>();
        _ikHelper = GetComponent<NurseIKHelper>();   // left enabled — drives the hand IK itself
        _anim.applyRootMotion = false;     // NavMeshAgent owns translation; clips are in-place

        if (_agent != null)
        {
            if (!_agent.isOnNavMesh)
            {
                NavMeshHit hit;
                if (NavMesh.SamplePosition(transform.position, out hit, 3f, NavMesh.AllAreas))
                    _agent.Warp(hit.position);
            }
            _agent.updateRotation = true;
            _agent.isStopped = false;
            if (walkTarget != null) _agent.SetDestination(walkTarget.position);
        }
    }

    void Update()
    {
        if (_anim == null) return;

        // Drive the controller exactly like the source: Speed from agent velocity.
        float speed = (_agent != null) ? _agent.velocity.magnitude : 0f;
        _anim.SetFloat("Speed", speed);

        if (_agent != null)
        {
            if (!_arrived &&
                !_agent.pathPending &&
                _agent.remainingDistance <= Mathf.Max(arriveRadius, _agent.stoppingDistance) &&
                speed < 0.05f)
            {
                _arrived = true;
                _agent.isStopped = true;
                _agent.updateRotation = false;
            }

            if (_arrived)
            {
                if (faceTarget != null)
                {
                    Vector3 dir = faceTarget.position - transform.position;
                    dir.y = 0f;
                    if (dir.sqrMagnitude > 0.0001f)
                        transform.rotation = Quaternion.RotateTowards(transform.rotation,
                            Quaternion.LookRotation(dir), turnSpeed * Time.deltaTime);
                }
                if (!_typed && _anim.GetCurrentAnimatorStateInfo(0).IsName("Idle"))
                {
                    _anim.SetTrigger("typing");
                    _typed = true;
                }
            }
        }

        DriveHandIKViaHelper();
    }

    // Engage/release the hand IK through the reused NurseIKHelper on Typing enter/exit — the same
    // LaptopIK/ResetHandsIK calls the clip's animation events make in the source scenario.
    void DriveHandIKViaHelper()
    {
        if (_ikHelper == null) return;

        bool typing = _anim.GetCurrentAnimatorStateInfo(0).IsName("Typing");
        if (typing && !_ikEngaged)
        {
            _ikHelper.LaptopIK(ikRampSpeed);
            _ikEngaged = true;
        }
        else if (!typing && _ikEngaged)
        {
            _ikHelper.ResetHandsIK(ikRampSpeed);
            _ikEngaged = false;
        }
    }
}
