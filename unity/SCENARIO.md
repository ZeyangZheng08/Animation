# SCENARIO — Canonical Nursing Scenario Spec (aligned with VR4Nursing)

> This document is the **alignment contract** between this project and the **improved source
> project `../VR4Nursing_v2/`** (Unity 6000.3). The scenario (environment, locations, props,
> patient/bed states, who does what where) is kept **identical to the source** and serves as a
> **fixed backdrop**; this project focuses **only on the animation layer** (MotionKB retrieval +
> body-part composition). The source's voice/LLM/intent-recognition/scheduling stack is
> **out of scope** — not ported and not planned for reimplementation here.
> Everything below was extracted from the source code/scenes with file:line evidence on
> 2026-06-09 and verified against our `Assets/Scenes/EmergencyRoom.unity`. **2026-06-13:** the
> patient & bed were re-aligned 1:1 to the improved `../VR4Nursing_v2/` (same room world origin,
> identical assets/controllers verified by GUID), and the frozen patient was changed from
> **P3 (flat, unresponsive)** to **P0 (awake, bed raised)** = the source's runtime-start frame
> (the one intentional deviation: the source shows a bare **T-pose** in edit mode, we freeze the
> run-start pose instead — see §1).

---

## 1. Scenario timeline (STEMI → arrest → ROSC)

| Phase | Trigger | What happens |
|---|---|---|
| P0 Responsive | Play start | Patient awake in the angled bed (patient `Idle Awake` + `Breathing`, bed `Idle Up`). Nurses idle / do monitoring, IV, labs. **(= our scene's P0 backdrop; patient/bed Animators now live as of 2026-06-16.)** |
| P1 Diagnosis | 12-lead ECG ordered | `ECG_Assessment` (leads on chest) → `ECG_Result` (10 s timer) → "Anterior STEMI confirmed". |
| P2 Deterioration | ECG completed | Delay, patient says he feels unwell, nurse reports deterioration. |
| P3 Unresponsive | auto | Patient `collapse` → a nurse walks to Bedside and lowers the bed (`BedCollapse`: bed `Bed Down` + patient `bed` + nurse `lowerBed`) → patient `Idle Unresponsive`, flat, eyes closed. CPR/defib become allowed. |
| P4 Code | "Start CPR" | Dual-nurse resuscitation coordinator (see §5). |
| P5 Defib chain | doctor orders | Pads (CPR continues) → Rhythm check (yields CPR) → Charge 200 J (CPR continues) → "Everybody clear!" + Shock (patient `defib` reaction). |
| P6 ROSC | ≥30 s total CPR, ≥15 s post-shock, shock delivered | Stop resuscitation, raise bed (reverse-play bed/patient clips), patient back to `Idle Awake`. |
| P7 Disposition | "Call the cath lab" | Phone call intent; session wrap-up. |

**Our scene's patient = phase P0 Responsive.** The serialized / edit-mode pose is the source's
**runtime-start** pose (so the non-running view matches the source's first play frame). **As of
2026-06-16 the patient & bed Animators are ENABLED (live)** — so in Play the patient breathes and
behaves like the source. The earlier approach of *freezing* the run-start pose (Animators disabled)
was reversed at the user's request (see HANDOFF §0/§2). Edit-mode shows the baked P0 pose, and it survives
recompiles / domain reloads on its own: the pose lives in the *serialized* bone transforms, which a
reload preserves (an idle enabled Animator doesn't override them in edit mode) — A/B-verified. No editor
hook is needed; the source `VR4Nursing_v2` shows a T-pose in edit only because it never baked a pose.

- Patient Animator base default **"Idle Awake"** (clip `patient_idle_angled`, from
  `patient_laying_angled_idle.fbx`, sampled @ frame 0) + Breath Layer **"Breathing"**, eyes open.
- Bed Animator default **"Idle Up"** = **backrest raised / angled bed** (mattress head end ≈ 0.87 m,
  foot ≈ 0.47 m — empirically verified by sampling the clip; `Idle Up`/`Idle Down` change the
  **backrest angle**, *not* the bed height). `startUnresponsive = 0`, no start-script repositioning.
- **As of 2026-06-16 both Animators are ENABLED / live** (reversing the earlier freeze, at the
  user's request): the patient plays `Idle Awake`+`Breathing` **in place** (no drift — `applyRootMotion`
  is off; patient `worldPos` verified unchanged in Play) and the bed holds `Idle Up`. The serialized
  edit-mode pose is still the baked P0, so edit-mode looks correct, but it is no longer strictly
  `edit-mode == runtime` (the patient breathes in Play — only the first frame matches). See HANDOFF
  §0/§2. *(Originally both Animators were kept disabled to freeze the backdrop, verified identical to
  4 decimals in Play — patient root, mesh min/max Y, centroid, bed and mattress all unchanged.)*

**Placement (aligned 1:1 to `../VR4Nursing_v2/`):**
- `DEMO/Patient/patient_avatar`: world `(-1.94, 0.769, -3.072)`, rotY `-90` (= 270°), scale `0.9`.
- `hospital_bed` (scene root): world `(-1.167, 0, -3.071)`, rotY `90`.
- **No clipping:** the patient rests on the mattress/`Cover`; closest underside contact ≈ 3 mm,
  nothing reaches below the mattress underside (checked via `SkinnedMeshRenderer.BakeMesh` against
  the combined mattress + cover surface, not bones).

> **Implication for the animation work:** the backdrop is now the **awake P0** state, but the
> CPR / BVM / `check_pulse` work assumes a **flat, unresponsive** patient (phase P3,
> `patient_laying_flat_unresponsive.fbx`). That work must re-pose the patient flat **at action
> time** (re-bake P3 or re-enable the Animators with correct posing) rather than relying on the
> backdrop. Known follow-up.

## 2. Nurse roster and roles

| GameObject (scene path) | nurseName | Role | Animation-capable? |
|---|---|---|---|
| `DEMO/Nurse4Agent/CPRNurse` | Jill | compressions, pulse checks | YES — nurse_avatar + NurseAnimator.controller + IK rig + NurseIKHelper + NurseAnimatorEvents |
| `DEMO/Nurse5Agent/AirwayNurse` | Kate | airway / BVM | YES — same setup |
| `DEMO/Nurse6Agent/EKGNurse` | Dana | EKG, meds, typing | YES — same setup |
| `Avatars/Nurse1Agent/Nurse1` | — | background figure | NO IK / no action rig (FemaleScientists prefab; NavMeshAgent + RigBuilder only). **Not one of the scenario's acting nurses.** |
| `Avatars/Nurse2Agent/FemaleScientists_PRF_URP`, `Avatars/Nurse3Agent/Nurse3` | — | background figures | no |
| `DEMO/Nurse7Agent/AirwayNurse` (copy) | — | inactive in the source too | kept inactive |

All of the above except Nurse7Agent's copy are **active**, matching the source scene
(restored 2026-06-09; they had been temporarily hidden). **Animation work should target
Jill/Kate/Dana**, not Nurse1.

## 3. Location anchors (verified positional match with `../VR4Nursing_v2/`)

Resolution convention (from source `NurseAnimator.cs`): each acting nurse carries a
name→Transform registry; `WalkTo(name)` / `TurnTo(name)` look names up case-insensitively.
All three nurses share the same anchor set. Anchors live under the root object `animpts`
(face/look-at variants are children of their station anchor):

`Home` (= `ExitDoor`), `Bedside(+Face)`, `ChestSide(+Face)`, `HeadSide(+Face)`,
`MonitorStation(+Face)`, `ECGStation(+Face)`, `DefibStation(+Face)`, `BVMStation(+Face)`,
`PhoneStation(+Face)`, `SampleCounter(+Face)`, `MedCabinet(+Face)` (same position as
SampleCounter, true in source too), `Computer(+Face)`, standbys `s1/s2/s3` (+`s1f/s2f/s3f`),
idle-standbys `idle1/idle2/idle3` (+`idle1Face/2Face/3Face`), and `CPRLocation`.

> `idle1/2/3` (+Face) and `CPRLocation` were **added 2026-06-13** to align the anchor set to the
> improved `../VR4Nursing_v2/` (they were absent from the earlier `Copy (7)`-derived scene).
> They are empty Transform markers (no renderer) used only by the source's (out-of-scope)
> `NurseAnimator` registry — inert in this project, kept for structural parity / future posing.
> `animpts` now has 19 children, matching v2.

## 4. Action execution pattern (the "scenario logic" for motion)

From source `NurseAnimator.cs` / `IntentDefinitions.cs` (verified, see git history of this doc):

1. **Strictly sequential per nurse**: `Walk(target)` → blocks until NavMeshAgent arrival
   (drives Animator float `Speed` from agent velocity; crossfades base layer to Idle on walk
   start) → `TurnTo(face target)` (0.5 s slerp) → `Anim(trigger)` stationary.
2. The animator controller enforces it: **no AnyState transitions**; all action triggers
   transition **from Idle only**; `Walk_N`'s sole exit is `Speed < 0.01` → Idle.
3. The **only** simultaneous-with-walking overlay is the `Hold Pills` bool → "Upperbody
   Layer" (mask = **right arm + right fingers only**, despite the name): set by an animation
   event mid `nurse_grab_bottle`, cleared near the end of `nurse_give_meds` — the nurse
   carries the bottle while walking cabinet → bedside.
4. **Give-meds sequence** (`Administer_Med`): Walk MedCabinet → TurnTo → `getAspirin` (3 s)
   → Walk Bedside → TurnTo → patient trigger `aspirin` → `giveAspirin` (5 s) → Walk Home.

## 5. CPR/BVM resuscitation coordinator (dual nurse)

- Primary nurse does compressions; a second free nurse is claimed for BVM.
- One-time BVM fetch: Walk BVMStation → `grab` → Walk HeadSide.
- **Strict alternation, never simultaneous**: CPR round (Walk ChestSide + `CPR`, 12 s) ↔
  BVM round (Walk HeadSide + `BVM`, 6 s); the off-duty nurse is pinned in place.
- Patient sync: nurse CPR start/end toggles patient bool `CPR` (`Receive CPR` clip, breath 0).
- Yield rule: any pending intent overlapping PatientChest/PatientAirway (rhythm check, shock,
  pulse check) makes BOTH nurses yield and stand by; rounds re-walk into place afterwards.
- ROSC: shock delivered + ≥30 s total CPR + ≥15 s post-shock CPR.

## 6. Intent → animation mapping and MotionKB coverage

Covered by `agent/animation_knowledge_base/` (8): `idle`, `walking` (Walk_N), `typing` (Typing), `giving_pills`
(giveAspirin), `cpr` (CPR), `grab_bottle` (getAspirin), `check_pulse` (pulse), `bvm` (BVM) — these
fully cover Check_Pulse, CPR+BVM resuscitation, the Administer_Med nurse motion, and the seated
computer charting (`typing` added 2026-06-13 — the first **seated** action; uses the two-handed
`laptop` IK group).

> **MotionKB schema note (2026-06-18).** The KB was re-split from v1's 6 body parts to **v2's 9
> channels** (ADR 0007 — legs/hands now left/right, a `root` channel, an orthogonal `ik_goals` layer,
> extractor rebuilt in Python). This changes the *body-part representation*, **not the action coverage**
> (still exactly these 8 actions), so the scenario contract documented here is unaffected. The v2 files
> are now the **root accepted store** (`status: accepted`); their SEMANTIC 5-tuple was filled + verified 8/8
> via the VLM-proposal loop (ADR 0008) and **promoted candidate→accepted on 2026-06-24** (v1 retired to git
> tag `kb/v1`). See the project README §2/§7 and `docs/adr/0007`/`0008`.

Used by the source scenario but **not yet in MotionKB** (next extraction targets):

| Animator param / clip | Used by intents |
|---|---|
| `button` (Button Pushing.anim) | Monitoring, ECG read, Rhythm_Check, Defib charge/shock, Prepare_Med |
| `grab` (Grab.anim) | IV_Access, Draw_Blood, ECG leads, defib pads, oxygen, weigh, BVM fetch |
| `call` (Call.anim) | Call_Cath_Lab, Consult_Cardiology, Call_For_Help |
| `lowerBed` (nurse_drop_bed) | Lower_Bed, Unresponsive_BedDown, Raise_Bed |
| patient clips | patient_get_meds, patient_reach_hand, patient_angled_collapse, patient_bed_angle, patient_laying_flat_unresponsive, patient_cpr_long, patient_defib, patient_laying_angled_idle |
| bed clips | Bed Controller: Idle Up / Angle Down / Idle Down / Angle Up |

## 7. Props and animation events

Receivers live on each acting nurse root:
- **`NurseAnimatorEvents`** (ported from source, GUID preserved; added + wired 2026-06-09, and
  **no longer verbatim** — see the bagMesh note below): `nurseAnimator` = own Animator,
  `ambubag` = own `held_ambubag` (hand bone), `bagMesh` = own **`abvrm_self_inflatingbag`**,
  `medicineBottle` = own `pill_bottle`.
  Handles: `ToggleAmbubagVisibility`, `AmbubagCompress` (blendshape squeeze in `Update`),
  `HoldMedicineBottle` / `ReleaseMedicineBottle` (bottle visibility + `Hold Pills` bool),
  `PrepareForBvmCycle`, `ResetRuntimeProps`.
  - **`bagMesh` has been wrong twice, in two different ways.** The source had all three nurses
    sharing AirwayNurse's renderer; that was fixed per-nurse on 2026-06-09. But the renderer each
    one then pointed at was **`abvrm_face_mask`, which has no blendshapes at all** — the squeeze
    lives on `abvrm_self_inflatingbag` (`abvrm_blendShape.squish`), one of ten renderers under the
    ambubag and the only one with a shape. So `GetBlendShapeWeight(0)` threw out of `Update` on
    every nurse every frame and **the bag never compressed once**; the flood also buried anything
    else in the console. Corrected 2026-08-12, choosing the renderer by what it *has* rather than
    by name. The script now resolves that at `Start` and does nothing rather than throwing when it
    cannot — the original null check passed and the call threw anyway, which is why this survived
    two rounds of attention.
- **`NurseIKHelper`** receives the IK events (`PulseIK`, `PickUpIK`, `AspirinLeft/RightHandIK`,
  `LaptopIK`, `ResetHands/Left/RightHandIK`). All 7 IK target groups are intact in the scene
  (pulse/aspirin groups parented under patient bones; `pickUp` under the root-level
  `Aspirin Bottle` at the MedCabinet; `Laptop`/`BVM` under `IK Hand Helper Points`).
- **Known orphan events (faithful to source — they have no receiver there either):**
  `patientCPR` on `nurse_cpr_long` (t=0) and `lowerBed` on `nurse_drop_bed` — playing those
  clips logs "AnimationEvent has no receiver"; harmless, by design until a future client
  implements them.

## 8. Navigation

- Agent type **"Nurse"** (`agentTypeID -1372625422`, radius 0.3, height 2, slope 10,
  climb 0.05) in `ProjectSettings/NavMeshAreas.asset` (was missing after the copy; restored
  2026-06-09 and verified: `NavMesh.SamplePosition` binds at the Bedside anchor).
- Baked surface `Assets/Scenes/EmergencyRoom/NavMesh-NavMeshSurface.asset`, scene
  `NavMeshSurface` (collect = MarkedWithModifier; Floor/Walls/bed_nav_box/Cabinets/etc.).
- All NavMeshAgents on nurses survive with source-identical params (speed 1.5,
  angularSpeed 360, stoppingDistance 0.05, radius 0.1).

## 9. Out of scope (not ported, not planned)

Voice/LLM/intent-recognition stack (SpeechManager, TTS, CommandProcessor, IntentQueueHandler,
Scheduler, NurseManager), directors (ScenarioDirector, StemiMgmt/CardiacArrest/Rosc),
`NurseController`, `NurseAnimator` (its walk→turn→act semantics are documented in §4 purely
as scenario reference), XR rig, PatientChat, EdgeAI server. **None of these are goals of this
project.** The scenario substrate documented above is kept only as the fixed context in which
the animation work happens, so that animation experiments reproduce the same situations as
the source training scenario.

**Full-scene alignment check (2026-06-13).** A 6-region diff of this scene against
`../VR4Nursing_v2/` (root inventory, nurses/DEMO, room/props/equipment, anchors, lighting/camera/
NavMesh/render, UI) found the visual scene **identical** to v2 — same root objects, nurses (all
positions/avatars/controllers), room props, lighting, NavMesh and render settings. The only
differences are the **deliberate exclusions** above, namely:
- **XR rig** (`XR Interaction Setup`, `XR Device Simulator`) — absent here by design.
- **`Main Camera` kept ACTIVE** (v2 disables it because its XR rig supplies the camera; with the
  XR rig removed, this is the only camera, so it must stay on — do NOT disable it to "match v2").
- Patient **`HeadAim` AimConstraint source = null** (in v2 it aims the patient's head at the XR
  camera/user; the XR camera is gone, so the null source just leaves the head un-aimed — with the Animator now live, the head follows the `Idle Awake`/`Breathing` clip like the rest of the body).
- Scripts absent (excluded stack): `DemoController` (on `DEMO`), `HudBinder` + `EKGDisplay` (on
  `Canvas_HUD`, which is inactive in both). `ScenarioManager` / `GameController` exist but inactive (as in v2).
- UI canvases absent: `Report Canvas`, `Camera Canvas` (gameplay/XR-camera-attached UI).
The `idle1/2/3` + `CPRLocation` anchors (§3) were the one structural gap and have been added.
