# MotionKB state - guid->asset resolution (engine-side layer)

Resolves each accepted action's source_clip (guid + file_id) to a real AnimationClip.
Driven from agent-side Python over the Unity MCP bridge; no agent code lives in the Unity project.
Schema + cross-field invariants are checked with no Unity by validate_motionkb.py.

| action | schema | status | clip resolved | clip_name | asset path |
|---|---|---|---|---|---|
| bvm | motionkb/v2 | accepted | YES | nurse_bvm_2 | Assets/Animations/NurseAnimation/nurse_bvm_long.fbx |
| check_pulse | motionkb/v2 | accepted | YES | nurse_check_pulse | Assets/Animations/NurseAnimation/nurse_check_pulse.fbx |
| cpr | motionkb/v2 | accepted | YES | nurse_cpr_30 | Assets/Animations/NurseAnimation/nurse_cpr_long.fbx |
| giving_pills | motionkb/v2 | accepted | YES | nurse_give_meds | Assets/Animations/NurseAnimation/nurse_give_meds.fbx |
| grab_bottle | motionkb/v2 | accepted | YES | nurse_grab_aspirin | Assets/Animations/NurseAnimation/nurse_grab_bottle.fbx |
| idle | motionkb/v2 | accepted | YES | Idle | Assets/Animations/NurseAnimation/Idle.anim |
| typing | motionkb/v2 | accepted | YES | Typing | Assets/Animations/NurseAnimation/X Bot@Typing.fbx |
| walking | motionkb/v2 | accepted | YES | Walk_N | Assets/Animations/NurseAnimation/Walk_N.anim |

**8 resolved / 0 failed / 0 warning(s).**
