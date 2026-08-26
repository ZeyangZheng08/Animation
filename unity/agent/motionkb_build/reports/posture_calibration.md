# posture calibration — 2446 dumps (prefix `mx_`), origin = Unity's Humanoid reference pose

origin: Unity's Humanoid reference pose — all 95 muscles at 0 (the centre of each DOF's HumanTrait range), bodyPosition.y 1, bodyRotation identity (tilt 0 deg). Definitional: nothing here is fitted, selected or sampled, so the corpus decides scale only.

group             n      p50      p90      p95      p99      max   divisor (p99 -> 0.85)
torso          2446   0.1531   0.3171   0.3793   0.5053   0.6271   0.5944
head           2446   0.2219   0.4311   0.5124   0.6621   0.9337   0.7789
arm            4892   0.3978   0.6217   0.7173   0.9086   1.4207   1.0689
leg            4892   0.2792   0.4120   0.4677   0.5791   0.7845   0.6813
hand           4892   0.6337   0.8809   0.9683   1.1797   1.4333   1.3878
root_height    2446   0.1107   0.4595   0.5824   0.8690   2.0153   1.0224
root_tilt      2446  16.4411  52.5088  68.8735  89.2274 168.2117   104.9734

## neutral/displaced threshold context (fraction of readings below the candidate)
  muscle         <0.02: 0.1%  <0.04: 0.5%  <0.06: 1.4%  <0.08: 2.8%  <0.1: 4.6%  <0.15: 10.4%
  root_height    <0.02: 3.9%  <0.05: 20.0%  <0.1: 45.8%  <0.2: 70.5%  <0.3: 79.1%
  root_tilt deg  <2: 1.3%  <5: 13.5%  <10: 32.3%  <20: 58.0%  <30: 74.2%

## variation inside the corpus's quiet subset (context for config.STATIC_MUSCLE = 0.02). The subset is the
## v2.4.1 rest selection (205 clips); since v2.5.0 it defines no origin, it is just a population of
## clips that hold still, which is what a static threshold has to be read against.
  1640 channel readings: p50 0.0441  p90 0.1532  p95 0.1744  p99 0.1935  max 0.1998
  32.0% of those readings sit below STATIC_MUSCLE (a clip can hold still and still sway; static means it does not move at all)

## static-variation channels whose posture offset exceeds the candidate (the holds the
## variation signal cannot see) — channel readings / clips with at least one
  >=0.02:  3513 channel readings in 1187 clips
  >=0.04:  3493 channel readings in 1184 clips
  >=0.06:  3455 channel readings in 1181 clips
  >=0.08:  3428 channel readings in 1176 clips
  >=0.1:  3393 channel readings in 1175 clips
  >=0.15:  3307 channel readings in 1173 clips

## ablation — the corpus-fitted origin used through v2.4.1, against this one. There
## is no sensitivity table any more because the production origin has no parameters
## to perturb: run --baseline fitted to reproduce that origin and its own analysis.
  fitted origin: median of 205 rest clips (var<0.2, tilt<7 deg, >=30 frames)
  distance from Unity's reference: max |DOF| 1.5955, rms 0.3871, d_body_y 0.0446, d_tilt 3.473 deg
  divisor drift 21.3%, 6822 of 22014 posture labels differ (30.99%)
  how much of a reading is signal rather than a common pedestal (sd/mean per group):
  group             unity     fitted
  torso             0.600      0.656
  head              0.547      0.551
  arm               0.370      0.395
  leg               0.321      0.519
  hand              0.253      0.521

## context clips (posture offset per channel; * = variation-static)
clip                                                 torso       head   left_arm  right_arm   left_leg  right_leg  left_hand  right_han  root_heig  root_tilt
Idle                                               0.0036*   0.0061*   0.3997*   0.3873*   0.4402*   0.4132*   0.5784*   0.6788*    0.0245      0.56
nurse_cpr_30                                       0.1788    0.0005*   0.8106    0.6739    0.4757*   0.4670*   1.0123*   0.6081*    0.0576     44.60
mx_Standing_Idle                                   0.0753*   0.1263*   0.4737    0.4429    0.3264*   0.3822*   0.7600    0.7533     0.0334      2.14
mx_Breathing_Idle                                  0.0532    0.0785    0.3420    0.3286    0.3146    0.3074    0.5809    0.7819     0.0459      2.36
mx_Arms_Raised                                     0.2708*   0.2540*   0.7188*   0.6852*   0.4165*   0.3347*   0.6615*   0.6279*    0.2906      8.50
mx_Agony_Holding_The_Head                          0.2663    0.3605*   0.5792*   0.9153*   0.3619    0.2635    0.5457*   0.4988*    0.0979     21.83
mx_Armed_Villain_Holding_A_Hostage_From_Behind     0.0613*   0.2895*   0.4172*   0.3699*   0.2629*   0.4150*   0.9185*   0.9278*    0.0225      6.58
mx_Legs_Crossed_Arms_Raised                        0.1493*   0.1535*   0.7075*   0.5674*   0.4605*   0.5500*   0.6081*   0.6547*    0.0274      3.73
mx_Boxing_Idle                                     0.0900*   0.2087    0.2870    0.4072    0.2619    0.4453    0.9514*   0.9599*    0.0994      4.55
mx_Crouch_Idle                                     0.2047*   0.4963    0.4629    0.4833    0.5049*   0.4531*   0.7930*   0.6525     0.4335     43.91

## paste into config.py
# REFERENCE_POSE is not emitted here: it is Unity's, not this script's. All 95
# muscles at 0, body_y 1, tilt_deg 0 — see config.REFERENCE_POSE.
POSTURE_DIVISOR = {
    C.TORSO: 0.5944,   # corpus p99 0.5053 -> 0.85
    C.HEAD: 0.7789,   # corpus p99 0.6621 -> 0.85
    "arm": 1.0689,   # corpus p99 0.9086 -> 0.85
    "leg": 0.6813,   # corpus p99 0.5791 -> 0.85
    "hand": 1.3878,   # corpus p99 1.1797 -> 0.85
    "root_height": 1.0224,   # corpus p99 0.8690 -> 0.85
    "root_tilt": 104.9734,   # corpus p99 89.2274 -> 0.85
}
NEUTRAL = {
    C.TORSO: 0.1783,   # 0.3 x divisor
    C.HEAD: 0.2337,   # 0.3 x divisor
    "arm": 0.3207,   # 0.3 x divisor
    "leg": 0.2044,   # 0.3 x divisor
    "hand": 0.4163,   # 0.3 x divisor
    "root_height": 0.3067,   # 0.3 x divisor
    "root_tilt": 31.492,   # 0.3 x divisor
}
