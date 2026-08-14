using UnityEngine;

// StateMachineBehaviour referenced by NurseAnimator.controller (Base Layer).
// On exit of any action state, ramp the nurse's hand IK weights back to zero so
// the next state starts with neutral hands. The actual IK is driven by NurseIKHelper.
public class AnimatorIkHelper : StateMachineBehaviour
{
    public override void OnStateExit(Animator animator, AnimatorStateInfo stateInfo, int layerIndex)
    {
        animator.gameObject.GetComponentInChildren<NurseIKHelper>()?.ResetHandsIK(0.3f);
    }
}
