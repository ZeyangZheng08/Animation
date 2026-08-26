# posture calibration — 2446 dumps (prefix `mx_`), ABLATION origin = median of 205 rest clips (var < 0.2, tilt < 7 deg, >= 30 frames)

origin: median pose of 205 selected rest clips (body_y 0.9554, tilt 3.47 deg) — fitted from the corpus by measurement alone. This is the pre-v2.5.0 origin, kept as an ablation; the store's production origin is Unity's reference pose.

group             n      p50      p90      p95      p99      max   divisor (p99 -> 0.85)
torso          2446   0.1292   0.2890   0.3538   0.4774   0.5951   0.5617
head           2446   0.2115   0.4201   0.5052   0.6456   0.9169   0.7595
arm            4892   0.4032   0.6241   0.7271   0.9427   1.4696   1.1091
leg            4892   0.2510   0.4685   0.5480   0.7027   0.8755   0.8267
hand           4892   0.4362   0.9160   0.9732   1.2029   1.5165   1.4151
root_height    2446   0.0726   0.4200   0.5527   0.8400   2.0600   0.9882
root_tilt      2446  12.9683  49.0360  65.4007  85.7546 164.7389   100.8878

## neutral/displaced threshold context (fraction of readings below the candidate)
  muscle         <0.02: 0.1%  <0.04: 0.8%  <0.06: 2.5%  <0.08: 4.7%  <0.1: 7.8%  <0.15: 16.7%
  root_height    <0.02: 22.6%  <0.05: 40.1%  <0.1: 60.6%  <0.2: 73.7%  <0.3: 82.5%
  root_tilt deg  <2: 14.6%  <5: 27.4%  <10: 41.7%  <20: 64.5%  <30: 77.9%

## variation inside the corpus's quiet subset (context for config.STATIC_MUSCLE = 0.02). The subset is the
## v2.4.1 rest selection (205 clips); since v2.5.0 it defines no origin, it is just a population of
## clips that hold still, which is what a static threshold has to be read against.
  1640 channel readings: p50 0.0441  p90 0.1532  p95 0.1744  p99 0.1935  max 0.1998
  32.0% of those readings sit below STATIC_MUSCLE (a clip can hold still and still sway; static means it does not move at all)

## static-variation channels whose posture offset exceeds the candidate (the holds the
## variation signal cannot see) — channel readings / clips with at least one
  >=0.02:  3517 channel readings in 1186 clips
  >=0.04:  3481 channel readings in 1182 clips
  >=0.06:  3439 channel readings in 1177 clips
  >=0.08:  3403 channel readings in 1176 clips
  >=0.1:  3348 channel readings in 1172 clips
  >=0.15:  3173 channel readings in 1156 clips

## sensitivity of the fitted origin to its selection thresholds — the reason this
## origin could use round numbers, and the parameters v2.5.0 removed entirely
selection                        n  maxDOFdelta   d_body_y     d_tilt    div drift label flips (of 22014)
var<0.15 tilt<7 frames>=30     113       0.2195     0.0023      0.272         2.5%      492 (2.23%)
var<0.25 tilt<7 frames>=30     283       0.1062     0.0005      0.308         0.7%      291 (1.32%)
var<0.2 tilt<5 frames>=30      156       0.0794     0.0034      0.393         1.8%      318 (1.44%)
var<0.2 tilt<10 frames>=30     264       0.0750     0.0030      0.663         1.6%      373 (1.69%)
var<0.2 tilt<7 frames>=15      228       0.0957     0.0012      0.094         0.4%      214 (0.97%)
var<0.2 tilt<7 frames>=60      120       0.0817     0.0034      0.022         2.2%      317 (1.44%)
var<0.2 tilt<7 frames>=90       72       0.3439     0.0023      0.295         2.4%      506 (2.30%)

## the 205 selected rest clips (the origin is their per-DOF median) — listed so the
## selection can be checked by eye, not taken on the count alone
clip                                                      frames   max var     tilt
mx_135_Degree_Right_Turn                                      51    0.1180    3.59
mx_180_Degree_Right_Turn                                      56    0.1557    3.78
mx_45_Degree_Left_Turn                                        51    0.0849    3.20
mx_45_Degree_Right_Turn                                       46    0.0621    3.23
mx_45_Degree_Right_Turn_While_Aiming_A_Rifle                  60    0.0782    3.89
mx_45_Degree_Right_Turn_While_Aiming_A_Rifle_Game_Blend       30    0.1518    3.63
mx_90_Degree_Left_Turn                                        39    0.0887    3.19
mx_90_Degree_Right_Turn                                       44    0.1287    1.79
mx_90_Degree_Right_Turn_While_Aiming_A_Rifle                  72    0.0914    4.62
mx_Acknowledging_Gesture                                      58    0.1312    3.72
mx_Agreeing_Yes                                               55    0.0523    3.07
mx_Armed_Villain_Holding_A_Hostage_From_Behind               150    0.0043    6.58
mx_Asking_A_Question                                         155    0.1944    2.63
mx_Backward_Walk                                              37    0.1771    3.16
mx_Backward_Walk_Turning_Right                                38    0.1645    2.86
mx_Blocking_With_Both_Arms_Out                                83    0.0728    6.86
mx_Boxing_Idle                                                66    0.0927    4.55
mx_Boxing_Jab_Cross_Medium                                    64    0.1995    6.75
mx_Breathing_Idle                                            298    0.1027    2.36
mx_Brutal_To_Happy_Walk                                       37    0.1635    3.12
mx_Carrying_Someone_Idle                                     300    0.0528    6.66
mx_Carrying_Someone_To_Turn_Left                             116    0.1343    5.81
mx_Cautious_Walk_Backward_Start_To_Stop_With_An_Aimed_Rifle     244    0.1747    1.60
mx_Clap_While_Standing                                        35    0.0697    3.64
mx_Cocky_Lean_Back                                            87    0.1907    6.94
mx_Dismissing_With_Back_Hand                                  67    0.1819    2.82
mx_Female_Dwarf_Standing_Idle                                200    0.0631    1.57
mx_Female_Dwarf_Walk_Forward                                  33    0.1856    3.62
mx_Female_Idle                                               300    0.0131    2.44
mx_Female_Idle_To_Twist_Left_Idle                             53    0.0914    2.55
mx_Female_Idle_To_Twist_Right_Idle                            65    0.1480    3.57
mx_Female_Idle_To_Walk_Forward                                40    0.1384    3.06
mx_Female_Idle_To_Walk_Forward_Knees_High                     34    0.1619    3.07
mx_Female_Idle_To_Walk_Forward_Legs_Crossing                  37    0.1327    2.78
mx_Female_Orc_Standing_Idle                                  221    0.1424    3.43
mx_Female_Sexy_Walk                                           41    0.1966    3.12
mx_Female_Standing_Idle                                      300    0.1730    2.25
mx_Female_Stop_Start_Walking                                 112    0.1793    5.22
mx_Female_Stop_Walking                                        46    0.1929    3.28
mx_Female_Turning_Right_In_Place                              32    0.1124    4.19
mx_Female_Twist_Left_Idle                                    155    0.1729    3.75
mx_Female_Twist_Left_Idle_To_Walk_180_Turning_Left            61    0.1893    4.31
mx_Female_Twist_Right_Idle                                   155    0.0969    3.33
mx_Female_Walk                                                30    0.1853    2.40
mx_Female_Walk_Backward_Arc_Right                             31    0.1428    6.96
mx_Female_Walk_Forward                                        36    0.1561    3.00
mx_Female_Walk_Forward_Crossed_To_Idle                        44    0.1737    3.73
mx_Female_Walk_Forward_In_A_Tight_Turn_To_The_Right          109    0.1642    3.97
mx_Female_Walk_Forward_In_A_Wide_Turn_To_The_Right           178    0.1774    3.76
mx_Female_Walk_Forward_Legs_Crossing                          36    0.1958    3.17
mx_Female_Walk_Forward_To_Idle                                58    0.1485    3.10
mx_Female_Walk_Forward_To_Twist_Left_Idle                     75    0.1627    4.42
mx_Female_Walk_Forward_To_Twist_Right_Idle                    89    0.1632    3.47
mx_Female_Walk_Forward_Turning_Left_90                        75    0.1859    3.01
mx_Female_Walk_Forward_Turning_Left_90_In_An_Arc             108    0.1864    2.87
mx_Female_Walk_Forward_Turning_Right_90                       74    0.1693    2.97
mx_Female_Walk_Forward_Turning_Right_90_In_An_Arc            107    0.1748    3.47
mx_Female_Walk_With_Briefcase                                 32    0.1742    3.57
mx_Female_Walking_And_Texting_On_Phone                        99    0.1896    2.69
mx_Female_Walking_Counter_Clockwise                          376    0.1780    4.36
mx_Firing_A_Rifle_While_Standing                              35    0.1028    2.34
mx_Fishing_Idle                                              106    0.0288    2.11
mx_Gesturing_Head_Side_To_Side                                84    0.1118    2.89
mx_Hanging_By_Hands_Against_Wall                              70    0.0554    1.68
mx_Hanging_From_A_Ledge_Idle                                 141    0.0209    3.23
mx_Happy_Forward_Hand_Gesture                                 88    0.1335    3.49
mx_Happy_Idle_Variation_1                                     60    0.0650    4.42
mx_Happy_Idle_Variation_2                                     88    0.1337    4.42
mx_Hard_Head_Nod_Yes                                          49    0.0755    2.59
mx_Holding_A_Full_Wheelbarrow_Idle                            45    0.0274    4.50
mx_Holding_An_Object_Idle                                    180    0.1966    6.25
mx_Idle_With_Aimed_Pistol                                     40    0.0202    6.36
mx_Inward_Block                                               44    0.1614    6.13
mx_Jumping_Rope                                               77    0.1554    2.74
mx_Kneeling_Idle                                             128    0.0626    1.87
mx_Kneeling_Idle_With_Aimed_Pistol                           114    0.0266    2.48
mx_Kneeling_In_Prayer                                         58    0.0178    1.11
mx_Knife_On_The_Rear_Hand_Reverse_Grip                       134    0.0316    1.91
mx_Leaning_Square_Against_A_Wall_With_One_Leg_Up             100    0.0175    2.46
mx_Leaning_Square_With_Shoulders_Against_A_Wall              100    0.0181    5.58
mx_Left_Upright_Strafe_Walk_To_Stop                           32    0.1463    3.23
mx_Looking_Around_With_Duel_Wielded_Pistols                  194    0.1779    2.65
mx_Looking_Down_Then_Pointing_Forward                        141    0.1769    4.58
mx_Male_Drinking                                             266    0.1460    4.26
mx_Male_Fight_Idle_Boxing_Stance                              99    0.1464    4.47
mx_Male_Fitness_Idle                                          75    0.0064    6.85
mx_Male_Fitness_Idle_2                                        75    0.0064    6.84
mx_Male_Fitness_Idle_With_Breathing                           99    0.0540    6.47
mx_Male_Happy_Walk                                            42    0.1971    6.66
mx_Male_Orc_Standing_Idle                                    200    0.0596    5.14
mx_Male_Orc_Standing_Idle_2                                  160    0.1052    1.14
mx_Male_Sequence_Turn_Left_Turn_Right                        438    0.1902    4.25
mx_Male_Standard_Walk                                         37    0.1565    1.73
mx_Male_Standing_Idle_01                                     133    0.0795    2.45
mx_Male_Standing_Idle_02                                     190    0.1131    2.69
mx_Male_Strut_Walk                                            43    0.1589    3.51
mx_Male_Twist_Left_Idle                                       85    0.0376    2.72
mx_Male_Twist_Left_Idle_To_Twist_Right_Idle                   60    0.1758    3.21
mx_Male_Twist_Right_Idle_To_Twist_Left_Idle                   55    0.1024    4.05
mx_Male_Walk_Backwards                                        45    0.1153    2.39
mx_Male_Walk_Forward_01                                       37    0.1884    4.04
mx_Male_Walk_Forward_03                                       33    0.1970    2.77
mx_Male_Walk_Then_Turn_With_A_Rifle                           36    0.1755    6.06
mx_Male_Walking_Counter_Clockwise                            521    0.1835    3.42
mx_Male_Walking_While_Texting_On_A_Smartphone                120    0.1703    3.79
mx_Male_Walking_With_Shopping_Bag                             38    0.1776    2.64
mx_Male_With_Headphones_Listening_To_Music                   134    0.1060    6.03
mx_Military_Signaling_Stay_Back_While_Crouched               180    0.1840    6.72
mx_Neutral_Idle                                              263    0.1599    5.74
mx_Nodding_Head_Yes                                           78    0.0581    2.95
mx_Nodding_Head_Yes_2                                         52    0.0985    2.60
mx_Punching_A_Speedbag                                       199    0.1915    5.62
mx_Quad_Punch_Combo                                           65    0.1842    6.85
mx_Quick_Formal_Bow                                           82    0.1118    4.80
mx_Quickly_Pointing_Angrily_Forward                           73    0.1934    5.26
mx_Ready_Alert_Two_Hand_Pistol_Grip                          120    0.0172    1.21
mx_Ready_To_Breakdance_End                                    43    0.1610    4.02
mx_Ready_To_Breakdance_Start                                  32    0.1492    4.30
mx_Ready_To_Combat_To_Defensive_Idle                          75    0.0174    3.14
mx_Rifle_Idle                                                 85    0.0257    1.74
mx_Rifle_Left_Side_Step                                       45    0.1040    4.08
mx_Rifle_Right_Side_Step                                      35    0.0919    3.51
mx_Rifle_Standing_Aiming_Idle                                 93    0.0092    2.13
mx_Rifle_Standing_Idle                                        63    0.0378    6.51
mx_Rifle_Standing_Idle_Aiming                                 63    0.0375    5.17
mx_Rifle_Walk_Strafe_Right                                    43    0.1596    3.94
mx_Rifle_Walking_Backward_To_Standing                         86    0.1998    2.46
mx_Rifle_Walking_To_Standing                                 107    0.1996    3.82
mx_Rocking_Back_And_Forth_As_If_Dizzy                        128    0.0906    3.06
mx_Sarcastically_Looking_Away                                 70    0.1444    2.56
mx_Sarcastically_Nodding_Head_Yes                             70    0.0787    1.94
mx_Scary_Clown_Ready_Idle                                     60    0.0317    3.31
mx_Shaking_Head_No_Annoyed                                    77    0.1571    2.13
mx_Shaking_Head_No_Dismissively                               54    0.1237    3.14
mx_Shaking_Head_No_Thoughtfully                               92    0.1111    2.44
mx_Shifting_Weight_From_Side_To_Side                         283    0.0754    1.91
mx_Short_Boxing_Step_Forward                                  30    0.1344    4.57
mx_Short_Head_Jab                                             40    0.1664    5.57
mx_Sighing_In_Relief                                          90    0.0777    1.89
mx_Single_Step_To_The_Right_While_Aiming_Rifle                46    0.1165    2.93
mx_Sit_With_Fidgeting_Feet                                    35    0.0271    5.85
mx_Sitting_Cross_Legged                                      287    0.1132    3.82
mx_Sitting_On_The_Floor                                      326    0.1454    6.30
mx_Standard_Idle                                              90    0.0463    2.11
mx_Standing_180_Left_Turn                                     49    0.1943    2.63
mx_Standing_180_Right_Turn                                    49    0.1872    1.87
mx_Standing_180_Right_Turn_Game_Blend                         50    0.1754    3.00
mx_Standing_Around_Bored_Idle                                320    0.1937    2.75
mx_Standing_Idle                                             180    0.0382    2.14
mx_Standing_Idle_2                                           250    0.0129    2.35
mx_Standing_Idle_3                                           250    0.0629    1.71
mx_Standing_Idle_4                                           300    0.0404    6.77
mx_Standing_Idle_Holding_Suitcase_2                          418    0.1313    3.09
mx_Standing_Idle_Loop                                         57    0.0551    5.54
mx_Standing_Idle_Unarmed                                     153    0.0602    1.20
mx_Standing_Left_Turn_3                                       35    0.1152    2.52
mx_Standing_Left_Turn_Game_Blend                              50    0.1724    3.10
mx_Standing_Left_Turn_Game_Blend_2                            35    0.1152    2.52
mx_Standing_Pout_Gesture                                      89    0.0516    5.72
mx_Standing_Right_Turn_3                                      35    0.1141    2.18
mx_Standing_Right_Turn_Game_Blend                             35    0.1141    2.18
mx_Standing_Short_Idle                                        59    0.0393    6.78
mx_Standing_To_Ready_Pose_Grabbing_Rifle_From_Side            74    0.1962    3.09
mx_Standing_To_Start_Walking_With_Rifle                       92    0.1848    5.19
mx_Standing_Up_From_Seated_Position                          145    0.1614    6.36
mx_Start_Left_Upright_Strafe_Run                              37    0.1768    5.24
mx_Start_Left_Upright_Strafe_Walk                             38    0.1600    6.48
mx_Start_Strafe_Walk_Right                                    46    0.1698    4.16
mx_Start_Walking_Backwards_While_Aiming_Rifle                 61    0.1850    4.41
mx_Start_Walking_While_Aiming                                 44    0.1976    6.30
mx_Step_Back_Cautiously_Agreeing                             141    0.1935    3.77
mx_Stop_Strafe_Walk_Right                                     62    0.1818    3.78
mx_Stops_Walking_While_Aiming_Rifle                           80    0.1867    5.77
mx_Stretching_Neck_Rolling_Side_To_Side                       96    0.1831    2.97
mx_Taking_A_Step_Backward_With_Aiming_Rifle                   55    0.1065    5.07
mx_Taking_A_Step_Forward_With_Aiming_Rifle                    60    0.1014    3.69
mx_Taking_A_Step_To_The_Left_While_Aiming_Rifle               44    0.0939    4.96
mx_Thoughtfully_Nodding_Head_Yes                              88    0.1334    2.05
mx_Thriller_Dance_Beginning_Idle                             130    0.0118    4.64
mx_Timid_Dancing                                             391    0.1715    2.18
mx_Turn_Left_45_Degrees_While_Aiming_Rifle                    35    0.0869    5.21
mx_Turn_Left_While_Aiming                                     30    0.1136    2.60
mx_Turning_Head_To_The_Side_In_A_Cocky_Manner                 76    0.1596    1.83
mx_Turning_In_Place                                          110    0.0623    5.07
mx_Turning_Left_While_Walking                                 36    0.1980    1.95
mx_Turning_Right                                              30    0.0922    1.29
mx_Turning_Right_With_Rifle                                   54    0.1474    4.96
mx_Two_Hand_Lowered_Gun_Rifle_Idle                           231    0.1314    2.71
mx_Two_Hand_Rifle_Idle                                       257    0.1453    1.69
mx_Unarmed_Idle                                              348    0.1147    1.92
mx_Walking_Backward_With_An_Aimed_Pistol                      31    0.1608    6.47
mx_Walking_Backwards_With_Rifle_Down                          38    0.1609    5.20
mx_Walking_From_Standing                                      88    0.1708    3.55
mx_Walking_Strafe_To_The_Left_2                               44    0.1613    6.77
mx_Walking_Strafe_To_The_Right_2                              44    0.1588    6.72
mx_Walking_To_Standing_Idle                                   90    0.1803    3.13
mx_Walking_While_Carrying_Someone                             86    0.1814    6.08
mx_Walking_With_A_Rifle_To_A_Stop                             68    0.1498    2.00
mx_Walking_With_A_Swagger                                     84    0.1651    4.70
mx_Walking_With_A_Swagger_2                                   31    0.1983    6.86
mx_Walking_With_A_Swagger_3                                   31    0.1888    4.11
mx_Walking_With_An_Iv_Pole                                    47    0.0795    6.12
mx_Walking_With_Rifle_At_Waist_Level                          33    0.1969    3.19
mx_Walking_With_Rifle_Down                                    39    0.1868    4.71
mx_Weight_Shift_Idle                                         499    0.0932    4.60

## context clips (posture offset per channel; * = variation-static)
clip                                                 torso       head   left_arm  right_arm   left_leg  right_leg  left_hand  right_han  root_heig  root_tilt
Idle                                               0.0392*   0.0279*   0.2634*   0.1845*   0.2285*   0.1500*   0.2538*   0.2961*    0.0201      2.91
nurse_cpr_30                                       0.1566    0.0290*   0.8953    0.7119    0.3290*   0.3326*   1.3049*   0.5403*    0.1022     41.13
mx_Standing_Idle                                   0.0662*   0.1427*   0.1725    0.1960    0.0935*   0.1287*   0.3161    0.3063     0.0112      1.33
mx_Breathing_Idle                                  0.0584    0.1022    0.3780    0.2496    0.0745    0.0654    0.2126    0.3251     0.0013      1.12
mx_Arms_Raised                                     0.2645*   0.2520*   0.9113*   0.9084*   0.5539*   0.5509*   0.4976*   0.1987*    0.2460      5.02
mx_Agony_Holding_The_Head                          0.2275    0.3716*   0.6659*   1.0268*   0.2162    0.1673    0.3360*   0.4586*    0.0533     18.36
mx_Armed_Villain_Holding_A_Hostage_From_Behind     0.0404*   0.2927*   0.7010*   0.5234*   0.1025*   0.1429*   1.0202*   0.9351*    0.0221      3.11
mx_Legs_Crossed_Arms_Raised                        0.1642*   0.1707*   0.8985*   0.7959*   0.3520*   0.4828*   0.3484*   0.2425*    0.0720      0.26
mx_Boxing_Idle                                     0.0672*   0.2177    0.5091    0.5406    0.2167    0.2902    0.9851*   0.9883*    0.0548      1.07
mx_Crouch_Idle                                     0.1680*   0.4777    0.5475    0.2709    0.6740*   0.6562*   0.8131*   0.2176     0.3889     40.44

## paste into config.py
REFERENCE_POSE = {   # ABLATION ONLY — not the store's origin since v2.5.0
    # Median mean-pose of the 205 corpus clips selected as at-rest by measurement
    # alone (>= 30 frames, every anatomical channel's raw variation < 0.2, mean body
    # tilt < 7 deg — no name matching).
    "muscles": [
        -0.093099, 0.003933, -0.007519, -0.03649, 0.000252, -0.002267, -0.069944, 0.000392,
        -0.005392, -0.025574, 0.005404, -0.014471, 0.05019, 0.001076, -0.040918, -0,
        -0, 0, 0, 0, 0, 0.386654, 0.154488, 0.093543,
        0.67786, 0.060208, -0.043079, -0.163014, -0.069233, 0.461622, 0.049999, 0.059683,
        0.669604, 0.099546, -0.124953, -0.125462, -0.080052, -0.553738, -0.441373, -0.421626,
        0.196489, 0.039413, 0.718002, 0.268524, 0.017701, -0.028325, -0.510778, -0.240413,
        -0.420236, 0.195178, -0.031749, 0.699673, 0.138778, -0.001001, 0.009682, -1.53219,
        -0.08176, 0.13178, 0.589071, 0.291963, -0.515253, 0.308903, 0.53328, 0.254003,
        -0.9976, 0.154899, 0.415305, 0.141821, -0.018513, 0.020253, 0.382881, 0.064051,
        -0.120789, 0.049579, 0.444791, -1.5955, -0.094997, -0.054429, 0.464224, 0.418751,
        -0.496132, 0.340475, 0.504119, 0.240046, -1.09646, 0.153919, 0.4017, 0.126044,
        -0.011513, 0.06198, 0.3916, 0.032134, -0.179281, 0.034016, 0.397617,
    ],
    "body_y": 0.955386,
    "tilt_deg": 3.4728,
}
POSTURE_DIVISOR = {
    C.TORSO: 0.5617,   # corpus p99 0.4774 -> 0.85
    C.HEAD: 0.7595,   # corpus p99 0.6456 -> 0.85
    "arm": 1.1091,   # corpus p99 0.9427 -> 0.85
    "leg": 0.8267,   # corpus p99 0.7027 -> 0.85
    "hand": 1.4151,   # corpus p99 1.2029 -> 0.85
    "root_height": 0.9882,   # corpus p99 0.8400 -> 0.85
    "root_tilt": 100.8878,   # corpus p99 85.7546 -> 0.85
}
NEUTRAL = {
    C.TORSO: 0.1685,   # 0.3 x divisor
    C.HEAD: 0.2278,   # 0.3 x divisor
    "arm": 0.3327,   # 0.3 x divisor
    "leg": 0.248,   # 0.3 x divisor
    "hand": 0.4245,   # 0.3 x divisor
    "root_height": 0.2965,   # 0.3 x divisor
    "root_tilt": 30.2663,   # 0.3 x divisor
}
