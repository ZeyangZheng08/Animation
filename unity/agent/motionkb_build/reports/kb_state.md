# MotionKB state - guid->asset resolution (engine-side layer)

Resolves an accepted action's source_clip (guid + file_id) to a real AnimationClip.

Scope of this run: 40 of 2446 accepted action(s), sampled with seed 0.
Driven from agent-side Python over the Unity MCP bridge; no agent code lives in the Unity project.
Schema + cross-field invariants are checked with no Unity by validate_motionkb.py.

| action | schema | status | clip resolved | clip_name | asset path |
|---|---|---|---|---|---|
| mx_Arms_Down_2 | motionkb/v4 | accepted | YES | mx_Arms_Down_2 | Assets/Animations/Mixamo30/mx_Arms_Down_2.fbx |
| mx_Body_Spin_On_Staff_Tip | motionkb/v4 | accepted | YES | mx_Body_Spin_On_Staff_Tip | Assets/Animations/Mixamo30/mx_Body_Spin_On_Staff_Tip.fbx |
| mx_Capoeira_Kicks | motionkb/v4 | accepted | YES | mx_Capoeira_Kicks | Assets/Animations/Mixamo30/mx_Capoeira_Kicks.fbx |
| mx_Clap_While_Standing | motionkb/v4 | accepted | YES | mx_Clap_While_Standing | Assets/Animations/Mixamo30/mx_Clap_While_Standing.fbx |
| mx_Dancing_The_Macarena | motionkb/v4 | accepted | YES | mx_Dancing_The_Macarena | Assets/Animations/Mixamo30/mx_Dancing_The_Macarena.fbx |
| mx_Death_Falling_Backwards | motionkb/v4 | accepted | YES | mx_Death_Falling_Backwards | Assets/Animations/Mixamo30/mx_Death_Falling_Backwards.fbx |
| mx_Diagonal_Wall_Run_To_Jumping | motionkb/v4 | accepted | YES | mx_Diagonal_Wall_Run_To_Jumping | Assets/Animations/Mixamo30/mx_Diagonal_Wall_Run_To_Jumping.fbx |
| mx_Dismissing_With_Hand_Forward | motionkb/v4 | accepted | YES | mx_Dismissing_With_Hand_Forward | Assets/Animations/Mixamo30/mx_Dismissing_With_Hand_Forward.fbx |
| mx_Female_Peek_Around_Corner_With_Gun | motionkb/v4 | accepted | YES | mx_Female_Peek_Around_Corner_With_Gun | Assets/Animations/Mixamo30/mx_Female_Peek_Around_Corner_With_Gun.fbx |
| mx_Female_Rumba_Dancing_Loop | motionkb/v4 | accepted | YES | mx_Female_Rumba_Dancing_Loop | Assets/Animations/Mixamo30/mx_Female_Rumba_Dancing_Loop.fbx |
| mx_Female_Using_Touchscreen_Tablet | motionkb/v4 | accepted | YES | mx_Female_Using_Touchscreen_Tablet | Assets/Animations/Mixamo30/mx_Female_Using_Touchscreen_Tablet.fbx |
| mx_Hit_Reaction_From_Behind_With_Bow | motionkb/v4 | accepted | YES | mx_Hit_Reaction_From_Behind_With_Bow | Assets/Animations/Mixamo30/mx_Hit_Reaction_From_Behind_With_Bow.fbx |
| mx_Injured_Running_Backwards_Turning_Left | motionkb/v4 | accepted | YES | mx_Injured_Running_Backwards_Turning_Left | Assets/Animations/Mixamo30/mx_Injured_Running_Backwards_Turning_Left.fbx |
| mx_Laying_Coughing_Severely | motionkb/v4 | accepted | YES | mx_Laying_Coughing_Severely | Assets/Animations/Mixamo30/mx_Laying_Coughing_Severely.fbx |
| mx_Left_Crouched_Strafe_Walk_To_Stop | motionkb/v4 | accepted | YES | mx_Left_Crouched_Strafe_Walk_To_Stop | Assets/Animations/Mixamo30/mx_Left_Crouched_Strafe_Walk_To_Stop.fbx |
| mx_Left_Leg_On_Object_Left_Hand_On_Knee_Right_Hand_On_Hip | motionkb/v4 | accepted | YES | mx_Left_Leg_On_Object_Left_Hand_On_Knee_Right_Hand_On_Hip | Assets/Animations/Mixamo30/mx_Left_Leg_On_Object_Left_Hand_On_Knee_Right_Hand_On_Hip.fbx |
| mx_Lunging_Forward_To_Bite | motionkb/v4 | accepted | YES | mx_Lunging_Forward_To_Bite | Assets/Animations/Mixamo30/mx_Lunging_Forward_To_Bite.fbx |
| mx_Male_Twist_Right_Idle | motionkb/v4 | accepted | YES | mx_Male_Twist_Right_Idle | Assets/Animations/Mixamo30/mx_Male_Twist_Right_Idle.fbx |
| mx_Male_Walking_While_Texting_On_A_Smartphone | motionkb/v4 | accepted | YES | mx_Male_Walking_While_Texting_On_A_Smartphone | Assets/Animations/Mixamo30/mx_Male_Walking_While_Texting_On_A_Smartphone.fbx |
| mx_Military_Signaling_Follow_While_Crouched | motionkb/v4 | accepted | YES | mx_Military_Signaling_Follow_While_Crouched | Assets/Animations/Mixamo30/mx_Military_Signaling_Follow_While_Crouched.fbx |
| mx_On_Left_Side_Left_Arm_Supporting_Head | motionkb/v4 | accepted | YES | mx_On_Left_Side_Left_Arm_Supporting_Head | Assets/Animations/Mixamo30/mx_On_Left_Side_Left_Arm_Supporting_Head.fbx |
| mx_Pushing_A_Heavy_Object | motionkb/v4 | accepted | YES | mx_Pushing_A_Heavy_Object | Assets/Animations/Mixamo30/mx_Pushing_A_Heavy_Object.fbx |
| mx_Quick_Formal_Bow | motionkb/v4 | accepted | YES | mx_Quick_Formal_Bow | Assets/Animations/Mixamo30/mx_Quick_Formal_Bow.fbx |
| mx_Rifle_Death_Crouched_From_Headshot_Front | motionkb/v4 | accepted | YES | mx_Rifle_Death_Crouched_From_Headshot_Front | Assets/Animations/Mixamo30/mx_Rifle_Death_Crouched_From_Headshot_Front.fbx |
| mx_Right_Leg_On_Object_Left_Hand_On_Hip_Right_Hand_On_Chin | motionkb/v4 | accepted | YES | mx_Right_Leg_On_Object_Left_Hand_On_Hip_Right_Hand_On_Chin | Assets/Animations/Mixamo30/mx_Right_Leg_On_Object_Left_Hand_On_Hip_Right_Hand_On_Chin.fbx |
| mx_Running_Jump_To_Run_Forward | motionkb/v4 | accepted | YES | mx_Running_Jump_To_Run_Forward | Assets/Animations/Mixamo30/mx_Running_Jump_To_Run_Forward.fbx |
| mx_Seated_Idle_With_Hands_On_A_Table_Being_Rude | motionkb/v4 | accepted | YES | mx_Seated_Idle_With_Hands_On_A_Table_Being_Rude | Assets/Animations/Mixamo30/mx_Seated_Idle_With_Hands_On_A_Table_Being_Rude.fbx |
| mx_Showing_Loser_Gesture_While_Standing | motionkb/v4 | accepted | YES | mx_Showing_Loser_Gesture_While_Standing | Assets/Animations/Mixamo30/mx_Showing_Loser_Gesture_While_Standing.fbx |
| mx_Standing_180_Right_Turn | motionkb/v4 | accepted | YES | mx_Standing_180_Right_Turn | Assets/Animations/Mixamo30/mx_Standing_180_Right_Turn.fbx |
| mx_Standing_From_A_Lying_Prone | motionkb/v4 | accepted | YES | mx_Standing_From_A_Lying_Prone | Assets/Animations/Mixamo30/mx_Standing_From_A_Lying_Prone.fbx |
| mx_Standing_From_Crouched_Hands_On_Ground | motionkb/v4 | accepted | YES | mx_Standing_From_Crouched_Hands_On_Ground | Assets/Animations/Mixamo30/mx_Standing_From_Crouched_Hands_On_Ground.fbx |
| mx_Standing_Planting_A_Tree | motionkb/v4 | accepted | YES | mx_Standing_Planting_A_Tree | Assets/Animations/Mixamo30/mx_Standing_Planting_A_Tree.fbx |
| mx_Strafe_Left_While_Aiming_Rifle | motionkb/v4 | accepted | YES | mx_Strafe_Left_While_Aiming_Rifle | Assets/Animations/Mixamo30/mx_Strafe_Left_While_Aiming_Rifle.fbx |
| mx_Swing_Dance_Charleston_Variation_2 | motionkb/v4 | accepted | YES | mx_Swing_Dance_Charleston_Variation_2 | Assets/Animations/Mixamo30/mx_Swing_Dance_Charleston_Variation_2.fbx |
| mx_Sword_And_Shield_Running_Jump | motionkb/v4 | accepted | YES | mx_Sword_And_Shield_Running_Jump | Assets/Animations/Mixamo30/mx_Sword_And_Shield_Running_Jump.fbx |
| mx_Thrown_Over_The_Shoulder_With_A_Leg_Hook_By_An_Aggressor | motionkb/v4 | accepted | YES | mx_Thrown_Over_The_Shoulder_With_A_Leg_Hook_By_An_Aggressor | Assets/Animations/Mixamo30/mx_Thrown_Over_The_Shoulder_With_A_Leg_Hook_By_An_Aggressor.fbx |
| mx_Turning_Left_Carrying_A_Box | motionkb/v4 | accepted | YES | mx_Turning_Left_Carrying_A_Box | Assets/Animations/Mixamo30/mx_Turning_Left_Carrying_A_Box.fbx |
| mx_Tut_Hip_Hop_Dance_Variation_Two | motionkb/v4 | accepted | YES | mx_Tut_Hip_Hop_Dance_Variation_Two | Assets/Animations/Mixamo30/mx_Tut_Hip_Hop_Dance_Variation_Two.fbx |
| mx_Using_The_Top_Drawer_Of_A_Filing_Cabinet | motionkb/v4 | accepted | YES | mx_Using_The_Top_Drawer_Of_A_Filing_Cabinet | Assets/Animations/Mixamo30/mx_Using_The_Top_Drawer_Of_A_Filing_Cabinet.fbx |
| mx_Walking_With_Rifle_At_Waist_Level | motionkb/v4 | accepted | YES | mx_Walking_With_Rifle_At_Waist_Level | Assets/Animations/Mixamo30/mx_Walking_With_Rifle_At_Waist_Level.fbx |

**40 resolved / 0 failed / 0 warning(s)** out of 40 of 2446 accepted action(s), sampled with seed 0.
