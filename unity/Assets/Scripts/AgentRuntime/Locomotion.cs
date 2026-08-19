using UnityEngine;
using UnityEngine.AI;

namespace AgentRuntime
{
    /// <summary>
    /// Moves the character through the room. Nothing else here does.
    ///
    /// WHY THIS IS SEPARATE FROM THE MOTION. Every clip in the library is in-place: measured horizontal
    /// root travel is 0.0000 m for walking and under 0.006 m for everything else, and config.py says so
    /// outright. So playing the walk cycle animates a walk and moves the character nowhere. Translation
    /// comes from the NavMeshAgent that has been sitting on CPRNurse unused, and keeping the two apart
    /// means `plan_motion` decides what is playing while this decides where she ends up, rather than one
    /// call trying to own both.
    ///
    /// THE CONSEQUENCE, STATED RATHER THAN HIDDEN. The agent translates at its own speed and the clip
    /// strides at whatever the animator authored, so the feet will slide by the difference. That is
    /// exactly what GateProbe's foot_skate metric measures, and it is why that metric reports without
    /// judging: the honest number here is one nobody has calibrated yet.
    ///
    /// VERIFIED BEFORE BUILDING. The plan listed "is a NavMesh even baked" as an open risk. Measured in
    /// play mode: 109 navmesh vertices, the agent is on it, and CPRNurse to the Chair returns
    /// PathComplete over 1.081 m.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class Locomotion : MonoBehaviour
    {
        [SerializeField] private NavMeshAgent agent;

        private Vector3 _destination;
        private bool _going;
        private Quaternion _facing;
        private bool _turning;

        public bool Going { get { return _going; } }

        /// <summary>Still swinging round to a Face() target. Turning takes time now — see Face.</summary>
        public bool Turning { get { return _turning; } }

        /// <summary>The point she is actually walking to: the destination sampled onto the navigation
        /// mesh, not the raw one that was asked for. Those differ whenever the target is a thing
        /// rather than a place — a laptop sits on a desk, which is not walkable.</summary>
        public Vector3 Destination { get { return _destination; } }

        public NavMeshAgent Agent
        {
            get { return agent != null ? agent : (agent = GetComponent<NavMeshAgent>()); }
        }

        /// <summary>Metres still to walk, or -1 when there is no active path.
        ///
        /// NOT EVERY NavMeshAgent.remainingDistance IS A DISTANCE. Unity returns
        /// <c>float.PositiveInfinity</c> while a path is partial or invalid, and this used to hand
        /// that straight on. Newtonsoft serializes a non-finite float as the STRING "Infinity" by
        /// default, so the number arrived on the agent side as text, the comparison against it threw,
        /// and the failure was reported as bad arguments from the model — for a walk that had already
        /// been dispatched and kept going. -1 is what this property already promised for "no answer",
        /// and an infinite remaining distance is exactly that.
        /// </summary>
        public float Remaining
        {
            get
            {
                NavMeshAgent a = Agent;
                if (a == null || !a.isOnNavMesh || a.pathPending) return -1f;
                float remaining = a.remainingDistance;
                return float.IsInfinity(remaining) || float.IsNaN(remaining) ? -1f : remaining;
            }
        }

        public bool Arrived
        {
            get
            {
                NavMeshAgent a = Agent;
                if (a == null || !a.isOnNavMesh) return false;
                if (a.pathPending) return false;
                return a.remainingDistance <= Mathf.Max(a.stoppingDistance, 0.01f);
            }
        }

        /// <summary>Length of the walkable route to a point, or -1 if there is no complete path.
        /// Computed before committing, so an unreachable destination is refused rather than walked at
        /// until a timeout.</summary>
        public float PathLength(Vector3 destination)
        {
            NavMeshAgent a = Agent;
            if (a == null) return -1f;
            NavMeshPath path = new NavMeshPath();
            if (!NavMesh.CalculatePath(transform.position, destination, a.areaMask, path)) return -1f;
            if (path.status != NavMeshPathStatus.PathComplete) return -1f;
            float length = 0f;
            for (int i = 1; i < path.corners.Length; i++)
            {
                length += Vector3.Distance(path.corners[i - 1], path.corners[i]);
            }
            return length;
        }

        /// <summary>Where a walk to `destination` would leave her, computed and not performed.
        ///
        /// THE WALK USED TO BE THE FIRST IRREVERSIBLE THING A PLAN DID. She crossed the room, and only
        /// then did the motion she crossed it for get judged -- so a plan that could not work was
        /// already visible by the time anybody knew. A character standing at a chair she cannot sit on
        /// is exactly as much of a failed plan on screen as a bad sit is.
        ///
        /// So this answers the two questions the pre-execution check needs -- can she get there, and
        /// where would she be standing and which way would she be facing when she arrived -- using
        /// nothing but the static navigation queries. The agent is neither enabled nor moved nor given
        /// a destination.
        ///
        /// THE ARRIVAL IS NOT THE DESTINATION. A NavMeshAgent stops when its remaining path is inside
        /// `stoppingDistance`, so the place she ends up is the route walked back by that much -- which
        /// for a seat is the 0.15 m the mesh does not extend under. Projecting to the raw destination
        /// instead would stand the hidden copy inside the chair and fail a sit that works.
        /// </summary>
        public System.Collections.Generic.Dictionary<string, object> Preview(
            Vector3 destination, float stopWithin, Vector3? faceAt)
        {
            System.Collections.Generic.Dictionary<string, object> reply =
                new System.Collections.Generic.Dictionary<string, object> { { "preview", true } };
            NavMeshAgent a = Agent;
            if (a == null)
            {
                reply["reachable"] = false;
                reply["why"] = "this character has no NavMeshAgent";
                return reply;
            }

            Vector3 here = transform.position;
            NavMeshHit start;
            if (!NavMesh.SamplePosition(here, out start, 2f, a.areaMask))
            {
                reply["reachable"] = false;
                reply["why"] = "the character is not standing on the navigation mesh";
                return reply;
            }
            NavMeshHit target;
            if (!NavMesh.SamplePosition(destination, out target, 2f, a.areaMask))
            {
                reply["reachable"] = false;
                reply["why"] = "there is no walkable ground near that destination";
                return reply;
            }

            NavMeshPath path = new NavMeshPath();
            if (!NavMesh.CalculatePath(start.position, target.position, a.areaMask, path)
                || path.status != NavMeshPathStatus.PathComplete || path.corners.Length == 0)
            {
                reply["reachable"] = false;
                reply["why"] = "no complete route to that destination";
                return reply;
            }

            float length = 0f;
            for (int i = 1; i < path.corners.Length; i++)
            {
                length += Vector3.Distance(path.corners[i - 1], path.corners[i]);
            }

            float stop = Mathf.Max(0.01f, stopWithin);
            Vector3 approach = path.corners.Length > 1
                ? (path.corners[path.corners.Length - 1]
                   - path.corners[path.corners.Length - 2]).normalized
                : transform.forward;
            Vector3 arrival = WalkBack(path.corners, stop);
            bool alreadyThere = length <= stop;
            if (alreadyThere)
            {
                arrival = here;
                approach = transform.forward;
            }

            // Which way she ends up looking. Arriving leaves her facing the way she walked, which for a
            // seat is backwards -- so a plan that names something to face turns afterwards, and the
            // heading the hidden copy is checked at is that one rather than the route's.
            Vector3 heading = approach;
            if (faceAt.HasValue)
            {
                Vector3 flat = faceAt.Value - arrival;
                flat.y = 0f;
                if (flat.sqrMagnitude > 1e-6f) heading = flat.normalized;
            }
            heading.y = 0f;
            if (heading.sqrMagnitude < 1e-6f) heading = transform.forward;

            reply["reachable"] = true;
            reply["arrived"] = alreadyThere;
            reply["path_length_m"] = length;
            reply["eta_s"] = a.speed > 0f ? length / a.speed : -1f;
            reply["arrival"] = new float[] { arrival.x, arrival.y, arrival.z };
            reply["facing_deg"] = Quaternion.LookRotation(heading.normalized, Vector3.up).eulerAngles.y;
            return reply;
        }

        /// <summary>The point `back` metres from the end of a corner list, measured along it.</summary>
        private static Vector3 WalkBack(Vector3[] corners, float back)
        {
            Vector3 end = corners[corners.Length - 1];
            float left = back;
            for (int i = corners.Length - 1; i > 0; i--)
            {
                float span = Vector3.Distance(corners[i - 1], corners[i]);
                if (span >= left)
                {
                    return Vector3.Lerp(corners[i], corners[i - 1], span <= 0f ? 0f : left / span);
                }
                left -= span;
                end = corners[i - 1];
            }
            return end;
        }

        /// <summary>Start walking. Returns null on success, or why it cannot.</summary>
        public string Go(Vector3 destination, float stopWithin)
        {
            NavMeshAgent a = Agent;
            if (a == null) return "this character has no NavMeshAgent";
            // Re-enabling here rather than at the end of a sit: a generated sit leaves the character on
            // a chair, which is off the navigation mesh, so bringing the agent back then would warp her
            // straight off it. The next walk is the right moment.
            if (!a.enabled) a.enabled = true;
            if (!a.isOnNavMesh)
            {
                NavMeshHit hit;
                if (!NavMesh.SamplePosition(transform.position, out hit, 2f, a.areaMask))
                {
                    return "the character is not standing on the navigation mesh";
                }
                a.Warp(hit.position);
            }

            NavMeshHit target;
            if (!NavMesh.SamplePosition(destination, out target, 2f, a.areaMask))
            {
                return "there is no walkable ground near that destination";
            }
            if (PathLength(target.position) < 0f)
            {
                return "no complete route to that destination";
            }

            a.stoppingDistance = Mathf.Max(0.01f, stopWithin);
            a.updateRotation = true;          // may have been switched off by a previous Face()
            a.isStopped = false;
            a.SetDestination(target.position);
            _destination = target.position;
            _going = true;
            return null;
        }

        /// <summary>Turn to face a point, yaw only, OVER TIME.
        ///
        /// Needed because arriving somewhere leaves the character facing the way she walked, and for a
        /// seat that is exactly backwards: she walks toward the chair, so she ends up facing it, and
        /// sitting from there puts her back to the desk. Which way to face is a fact about the scene
        /// (the workstation the chair belongs to), not something the walk can infer.
        ///
        /// This used to write the rotation in one frame, which is a teleport: arriving at the
        /// workstation and turning 150 degrees to face the desk happened between two rendered frames.
        /// The rate is the navigation agent's own `angularSpeed`, so a turn under this looks like a
        /// turn the agent would have made if it had walked round — nothing new to tune.
        /// </summary>
        public void Face(Vector3 point)
        {
            Vector3 flat = point - transform.position;
            flat.y = 0f;
            if (flat.sqrMagnitude < 1e-6f) return;
            NavMeshAgent a = Agent;
            // updateRotation would fight this the moment the agent has any residual velocity.
            if (a != null) a.updateRotation = false;
            _facing = Quaternion.LookRotation(flat.normalized, Vector3.up);
            _turning = true;
        }

        /// <summary>Stop where she is. Called before a posture change: a NavMeshAgent that is still
        /// steering will drag the character out from under a sit that was aimed at a fixed seat.</summary>
        public void Halt()
        {
            NavMeshAgent a = Agent;
            if (a != null && a.isOnNavMesh) { a.isStopped = true; a.ResetPath(); }
            _going = false;
            // A turn still running would keep writing the rotation while a generated descent owns the
            // transform, which is the same class of fight Suspend exists to end.
            _turning = false;
        }

        /// <summary>Hand the transform over to something else entirely.
        ///
        /// isStopped + updatePosition=false was not enough: measured, the agent kept writing its own
        /// idea of the position back over an externally moved transform, so a generated sit that slid
        /// the character onto a chair was silently undone within a frame. Disabling the component is
        /// the only way to be sure nothing else owns the transform, and the navigation state is
        /// restored by warping it to wherever the character actually ended up.
        /// </summary>
        public void Suspend()
        {
            NavMeshAgent a = Agent;
            if (a == null) return;
            if (a.isOnNavMesh) { a.isStopped = true; a.ResetPath(); }
            a.enabled = false;
            _going = false;
            _turning = false;
        }

        public void Resume()
        {
            NavMeshAgent a = Agent;
            if (a == null) return;
            a.enabled = true;
            NavMeshHit hit;
            if (NavMesh.SamplePosition(transform.position, out hit, 2f, a.areaMask)) a.Warp(hit.position);
            a.isStopped = true;
        }

        private void Update()
        {
            if (_going && Arrived) { _going = false; }
            if (!_turning) return;

            NavMeshAgent a = Agent;
            float degreesPerSecond = a != null && a.angularSpeed > 0f ? a.angularSpeed : 180f;
            transform.rotation = Quaternion.RotateTowards(transform.rotation, _facing,
                                                          degreesPerSecond * Time.deltaTime);
            if (Quaternion.Angle(transform.rotation, _facing) < 0.5f)
            {
                transform.rotation = _facing;
                _turning = false;
            }
        }

        public System.Collections.Generic.Dictionary<string, object> State()
        {
            NavMeshAgent a = Agent;
            return new System.Collections.Generic.Dictionary<string, object>
            {
                { "going", _going },
                { "turning", _turning },
                { "arrived", Arrived },
                { "remaining_m", Remaining },
                { "speed_m_per_s", a == null ? 0f : a.speed },
                { "on_navmesh", a != null && a.isOnNavMesh },
                { "destination", new float[] { _destination.x, _destination.y, _destination.z } }
            };
        }
    }
}
