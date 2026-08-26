# Animation — Language-Driven, Retrieval-First Animation Assembly Framework (engine side)

> This Unity project is the **engine half** of the research effort in §1; the agent half is the sibling Python repo (§4). It names a side rather than a phase because a side does not go stale.
>
> **Phase 1 is complete**: nursing animations understood **at the body-part level** (the MotionKB), plus the visualization scene and the IK validation environment. **Phase 2 is under way**, and its engine half lives here as `Assets/Scripts/AgentRuntime/`. What is built of Phase 2 is the **real-time assembly path** — retrieve, compose on a unified timeline, ground in the scene, gate geometrically, feed failures back. What is **not** built is the **bake path**: §1's "bakes a new `AnimationClip`" and the FBX export do not exist, and are deliberately deferred until a quality gate exists that can admit a synthesized clip. Still a single LLM agent in deterministic scaffolding; multi-agent and cross-engine are later extensions.
>
> **Language convention (project-wide):** prose in **plans and user-facing communication is Chinese**, but all **code, comments, JSON content, field names, file names, and identifiers are English**. (These docs are written in English by request.)

---

## 1. Research vision (updated 2026-07-01)

A **language-driven animation assembly framework** for interactive 3D scenes. Unity is the current implementation and verification platform, but the architecture is engine-decoupled:

- **Agent side — a standalone Python service**: task understanding, action decomposition, motion-KB retrieval, assembly planning, and failure-feedback replanning.
- **Engine side — a replaceable executor**: provides the scene graph, character skeleton, object poses, collision state, IK execution results, and animation previews; consumes the agent's structured assembly data.
- The two connect through an explicit **data contract + communication interface**. The LLM is **not** embedded inside Unity, and the model does **not** generate Unity C# for on-the-fly compilation at runtime.

**Retrieval-first.** Offline, Unity-compatible FBX motion assets are parsed into a structured representation on a unified skeleton, indexing both whole animation clips and body-part-level segments (per-part trajectories, key poses, root motion, contact points, phase information, composability/transition relations). At runtime, given a natural-language intent and the current scene state, a **single LLM agent inside deterministic scaffolding** performs semantic understanding, action decomposition, retrieval selection, and symbolic assembly planning:

- **Full KB match** → return the clip + its playback constraints directly (the truly real-time path).
- **No full match** → plan an assembly over retrieved REAL body-part segments on the unified skeleton and a unified timeline; **deterministic code** performs mask compositing, phase alignment, transition generation, IK, foot locking, contact constraints, and collision correction, then **bakes a new `AnimationClip`** (optionally exported as FBX).

**Core principle:** the LLM only makes semantic-level decisions about motion understanding and assembly — it never outputs joint angles, coordinates, velocities, or timestamps. All final motion numerics come from real motion assets, or from deterministic solvers constrained by real trajectories and passed through geometric verification.

**Hard geometric gates at every stage** keep results executable in the real scene: intent (target reachability, orientation, scene compatibility) → retrieval (part-segment composability, transition validity) → assembly (acceleration discontinuity, foot skate, penetration, end-effector error) → scene landing (world-space geometry between the character and objects/ground/environment). On failure, the scaffolding feeds the reason back to the agent and rolls back to the corresponding upstream stage to replan.

**Scene understanding is not open-ended reasoning:** the engine side first performs deterministic enumeration, caching, and state sync over the scene graph; a multimodal model assists only with candidate-object filtering, affordance judgment, semantic disambiguation, and visual feedback verification.

**Novelty positioning:** the LLM handles dynamic interactive intent understanding and motion-assembly planning, while precise motion numerics and physical/geometric consistency are delegated to real motion assets, engine state, and deterministic solvers — distinct from direct text-to-motion generation, and from traditional runtime animation systems that rely on manually pre-authored interaction bindings.

**Staging:** the current build is **single-agent** (linear control flow, unified context, attributable failures, engineering feasibility). Because the two sides are decoupled by the data contract, the framework extends naturally to a **multi-agent** architecture later (intent understanding / retrieval / assembly planning / visual verification / scene landing as specialist agents), and the executor can be replaced or extended to **Unreal Engine and Blender**. Further extensions: a VLM semantic feedback loop, migrating the FBX-centric assets toward **SMPL/SMPL-X**, an optional full-body coordination refinement before baking, and **write-back of successfully assembled, gate-passing motions into the MotionKB** so the system accumulates reusable composite assets over use.

For the roadmap and design rationale, see [HANDOFF.md](HANDOFF.md) §6.

## 2. What exists in this project right now (done)

- **Body-part-level motion knowledge base `agent/animation_knowledge_base/`** — one JSON per nursing animation; the **canonical store** for the future Python RAG. As of **2026-06-24 the ACCEPTED store is v2** — the 8 root-level `*.json` (schema `motionkb/v2`, `status: accepted`): a **9-channel** split (`root` + `torso` + `head` + `left/right_arm` + `left/right_leg` + `left/right_hand`) plus an orthogonal `ik_goals` layer, produced by the engine-decoupled **Python extractor** (`agent/motionkb/`). Each anatomical channel carries a **MEASURED** block (program-written) + the **SEMANTIC** 5-tuple (`role/motion_type/contact/constraint`, all filled + consistency-gated; the current store is auto-accepted **`vlm_accepted`** — human `author` review optional, `verified_against_screenshots=false`); `composability` is semantic; per-channel `target` stays `null` (deferred to Phase-2 scene grounding / the scene-landing stage). The SEMANTIC 5-tuple was filled via the [ADR 0008](docs/adr/0008-vlm-proposed-authored-fields.md) loop (a VLM proposes from rendered frames → a deterministic consistency check gates it against the MEASURED block / `ik_goals` / `composability` → a human accepts). `python agent/motionkb/validate_motionkb.py` → **8/8**. **As of 2026-06-25 the proposing is now a program** — `render → propose → author` (`agent/motionkb/extract.py` + `vlm_openai.py` + `propose.py`), and all 8 actions were **re-proposed by `gpt-5.5-2026-04-23`** (it even proposed the functional `action_id` from the frames — `nurse_give_meds → giving_pills`, `nurse_grab_aspirin → grab_bottle`); the MEASURE half is now keyed by clip name. The prior `claude-opus-4-8` proposal is preserved in `agent/motionkb_build/archive/authored_claude_backup/`; MEASURED was untouched (golden 8/8).
  - **The former v1 store (6-part, schema `motionkb/v1`) was RETIRED by this promotion** but is preserved at git tag **`kb/v1`** for rollback (ADR 0005 / [docs/ROLLBACK.md](docs/ROLLBACK.md)); `agent/animation_knowledge_base/candidate/` was the staging area for future re-extractions until [ADR 0016](docs/adr/0016-one-store-status-is-the-membership-test.md) (2026-08-21) merged it into `agent/animation_knowledge_base/actions/` — one store of 2454 records, where `status` is the membership test and acceptance renames a record rather than moving it. Decision records: [ADR 0007](docs/adr/0007-v2-body-part-split.md) (supersedes ADR 0003), [ADR 0008](docs/adr/0008-vlm-proposed-authored-fields.md); design narrative: [docs/specs/motionkb-v2-spec.md](docs/specs/motionkb-v2-spec.md); v2 contract: `agent/animation_knowledge_base/schema/motionkb.v2.schema.json`.
- **Runtime executor `Assets/Scripts/AgentRuntime/`** — the engine half of Phase 2, 11 components. `AgentLink` holds the one WebSocket connection to the agent service (the agent is the *server*; the editor drops managed state on every recompile, so the reconnecting party must be this side) and speaks protocol v4, mirrored from the agent repo's `agent/protocol.py` — `Protocol.cs` is changed second, never first. `SceneRegistry`/`SceneQueryService` answer typed scene predicates (identity, category, coarse relations; coordinates only when explicitly asked for) and resolve the destinations that are not objects — `near:<object_id>`, and `view:left|right|ahead|behind` relative to the runtime camera, sampled onto the navmesh with the metres staying engine-side. `MotionComposer` + `ClipLibrary` play an assembled motion on a PlayableGraph of masked layers, to a timetable computed agent-side; **a layer may carry a fractional weight and its own entry phase, so one body channel can be driven by two clips at once** (measured, not assumed — see the ADR 0004 amendment), and **a layer may play only part of its clip** — an entry *and an exit* frame, looping inside that window where the motion repeats, so an overlay contributes one chest compression rather than thirty. Which frames those are is measured agent-side from the frozen pose dumps; nothing here chooses them. `PoseSynth` generates the frames between standing and seated **in either direction**, closed-loop on measured hip height. `Locomotion` walks her (every clip is in-place), and reports a remaining distance of -1 rather than an infinity a JSON serializer would turn into a string. `IkBinder` binds hands and gaze to scene objects on top of Animation Rigging, each binding engaging at the second its own step comes due. `GateProbe` measures the pose that actually played, in `LateUpdate` after IK, and reports metrics with no defensible threshold as *measured* rather than failing a plan on an invented cutoff — but it is no longer the only verdict, and since **v4 it is not the deciding one**. **A commit is played through on a hidden duplicate of the character first** (`ValidationCharacter`), at fixed timestep and with every renderer off, and only a pass reaches the visible one; the route is *previewed* rather than walked (`Locomotion.Preview`) so the motion is judged standing where the walk will actually leave her. Both paths judge through one `GateEvaluator` and are armed by one `GateArming`, so the check that runs before a plan plays and the one that runs while it plays cannot drift apart. `GateProbe`'s job is now to catch the scene changing under a plan that was already checked. See **[ADR 0009](docs/adr/0009-check-before-you-play.md)**. **No LLM and no generated C# on this side** — typed messages only; the MCP bridge that does ship C# is offline-only and builds the KB (§4).
- **Full emergency-room scene `Assets/Scenes/EmergencyRoom.unity`** — the scenario environment brought over from the source project `VR4Nursing_v2 - Copy (7)` (room, hospital bed, ICU equipment, emergency cart, props, nurses, patient), **scenario-aligned with the source** (see [SCENARIO.md](SCENARIO.md)): all source-active nurses are active (the acting/IK nurses are `CPRNurse`/Jill, `AirwayNurse`/Kate, `EKGNurse`/Dana — NOT `Nurse1`, which is a background figure), location anchors / IK target groups / props / NavMesh all verified against the source. The XR rig, intent recognition / LLM / Python server / runtime director scripts were **not** brought over and are **out of scope** — this project focuses on the animation layer only; the scenario is a fixed backdrop. As of **2026-06-13** the scene's patient & bed are **re-aligned 1:1 to the improved `../VR4Nursing_v2/`** and the patient is **awake/reclining in the angled bed = scenario phase P0** (the source's runtime-start frame). As of **2026-06-16** the patient & bed Animators are **enabled / live** (patient plays `Idle Awake`+`Breathing` **in place** — `applyRootMotion` is off, so no drift; bed holds `Idle Up`); the baked P0 pose is the serialized edit-mode pose and survives domain reloads on its own (see the "patient pose" section in HANDOFF). As of **2026-06-17** the bed's cloth was **de-clipped**: the blue `Cover` blanket no longer drapes into the side frame, and the white sheet / grey mattress no longer poke through the blanket beside the legs — fixed by editing the skinned-mesh *source vertices* (so the cloth still follows the bed animation), not by disabling or baking anything; see HANDOFF §0.
- **IK test scene `Assets/Scenes/NurseAnimTest.unity`** — NurseAvatar + Animator + RigBuilder + two-bone hand IK (`TwoBoneIKConstraint`), demonstrating "legs planted, arm reaches a target".
- **Reused scripts `Assets/Scripts/`** — `IK/NurseIKHelper.cs` (IK rig wiring + IK target groups), `IK/AnimatorIkHelper.cs` (the `StateMachineBehaviour` referenced by the controller), `NurseAnimatorEvents.cs` (animation-event receiver for props: medicine bottle, BVM bag squeeze; ported from the source project and wired on the 3 acting nurses — no longer verbatim, see [SCENARIO.md](SCENARIO.md) §7 for the `bagMesh` correction that made the squeeze run at all).
- **Scenario spec [SCENARIO.md](SCENARIO.md)** — the canonical scenario contract aligned with the source project: timeline (STEMI → arrest → ROSC), nurse roster/roles, the 30 location anchors, the walk → turn → act execution pattern, CPR/BVM coordinator rules, intent→animation mapping, MotionKB coverage gaps.

## 3. Environment requirements

| Item | Version / note |
|---|---|
| Unity | **6000.3.16f1** (see `ProjectSettings/ProjectVersion.txt`) |
| Render pipeline | URP **17.3.0** |
| Animation Rigging | **1.4.1**, **embedded** at `Packages/com.unity.animation.rigging/` (do NOT switch back to a registry install — it hits EPERM on Windows; see HANDOFF) |
| MCP for Unity | local `file:` package pointing at the sibling `../unity-mcp-research/MCPForUnity` (lets an AI agent drive the editor) |
| Others | Input System 1.19, AI Navigation 2.0.12, Timeline, TMP, etc. (see `Packages/manifest.json`) |

## 4. Project layout

This repository is the **Unity half**: the executor, the scene, and the MotionKB. The agent half — the
offline pipeline and the runtime service — lives in a separate repository, `animation-agent`, on WSL
(Ubuntu 24.04, ext4); it moved out on 2026-08-05.

Both halves are readable online at
[`ZeyangZheng08/Animation`, branch `code`](https://github.com/ZeyangZheng08/Animation/tree/code) — source
only, published from a third repository that mirrors these two. It carries no 3D assets and uses no LFS,
so it can be read and diffed anywhere but will not open as a running Unity project. Neither working
repository is pushed anywhere.

```
agent/                        # ★ the MotionKB, all that remains of the agent side here
├── kb/                       # ★ body-part-level motion KB: root *.json = v2 ACCEPTED (8, 9-channel, status:accepted);
│                             #   candidate/ = empty staging; v1 retired to git tag kb/v1; raw/ = frozen per-frame
│                             #   sampling; frames/ = render frames (runtime retrieval input, lfs); motionkb_build/reports/ = run-log;
│                             #   schema/ = v1+v2 + CHANGELOG; engine_mask_map.json; manifest.json; README
└── README.md                 # what the KB is and why it is versioned here
```

> **Why the KB stays here while the pipeline left.** The KB cannot be regenerated without Unity — `raw/`
> comes from in-engine `AnimationMode` sampling and `frames/` from in-engine rendering — and it grows only
> when a new clip is imported into this project. It is a derivative of these animation assets, so it is
> versioned with them: adding an action is one atomic commit holding both the FBX and its KB entry, and a
> `source_clip.guid` can never drift out of sync with the store that records it. The pipeline that produces
> it is ordinary engine-independent Python and has no such tie, so it went to the agent repo.
>
> The agent side reaches the KB through one configured path (`MOTIONKB_DIR`), loads the ~1.4 MB action
> store into memory at startup, and treats it as read-only; the only writer is the offline pipeline.

```
Assets/
├── Scenes/
│   ├── EmergencyRoom.unity            # ★ full ER visual scene (patient awake/reclining = P0; patient+bed Animators live)
│   ├── EmergencyRoom_TypingTest.unity # copy: TypingNurse_Avatar (nurse_avatar) walk→idle→seated typing (hand IK) demo
│   └── NurseAnimTest.unity            # ★ IK demo scene
├── Scripts/AgentRuntime/     # ★ the runtime executor (11 components): AgentLink (WebSocket client) + Protocol, AgentCharacter, MotionComposer, PoseSynth, IkBinder, Locomotion, SceneRegistry/SceneQueryService, GateProbe, ClipLibrary
├── Scripts/IK/               # NurseIKHelper.cs, AnimatorIkHelper.cs
├── Animations/
│   ├── NurseAnimation/       # nurse_avatar.fbx + the nurse clip sources + NurseAnimator.controller + masks
│   ├── PatientAnimation/     # PatientAnimation.controller (patient; Animator live — Idle Awake + Breathing)
│   ├── BedAnimation/         # Bed Controller.controller + hospital_bed.fbx (bed; Animator live — holds Idle Up); cloth de-clipped: Cover/Sheet/Mattress_declipped.mesh
│   ├── BVM/ , RawAnimAssets/ # raw animation assets
├── 3D Assets/                # room, hospital bed, equipment, props, avatars (~1.6GB, from the source project)
├── Materials/ , Audio/ , UI/ # resources brought from the source project
├── Settings/                 # URP renderer / RP assets / volume profiles
├── Screenshots/              # MCP screenshot-verification output (debug artifacts)
└── TextMesh Pro/ , TutorialInfo/  # template / package defaults
```

**Other top-level dirs:**
- `docs/adr/` — architecture decision records (0001–0008). `docs/specs/` — the v2 design narrative.

**The agent repository** (`~/Research/animation-agent` on WSL) holds the engine-decoupled **Python** MotionKB **v2** pipeline: `config.py` channels/bone-map/divisors/thresholds · `metrics.py` formulas · `paths.py` KB location + write discipline · `extract.py` orchestration (`register|resolve-controller|emit-sampler|sample|assemble|render|propose|author`) · `unity_sampler.py` the generic pose-sampler + frame renderer run over Unity MCP · `vlm_openai.py` + `propose.py` the VLM proposal loop · `validate_motionkb.py` / `test_golden_extraction.py` / `gen_kb_manifest.py` / `validate_guids.py` the four gates, run together by `check_kb.sh`. It also holds the **runtime service** that drives this project: `agent/` — retrieval over the KB, the deterministic channel partition and seam schedule, the ReAct loop, the model backends, and the typed contract `agent/protocol.py` that `Assets/Scripts/AgentRuntime/Protocol.cs` mirrors — with `cli.py` as the service and `terminal.py` as the console that opens on Play. It reads this repo's `agent/animation_knowledge_base/` through `MOTIONKB_DIR`. See its own `README.md`.

**Two channels to this project, which must not merge.** Offline KB construction ships generated C# over the Unity MCP bridge — sampling muscle clips and rendering frames genuinely need an editor. The runtime channel is a WebSocket on which the agent is the *server* and a pre-compiled executor here connects in; it carries **typed messages only, never code**, since compiling C# during a request is forbidden by the architecture and impossible in a player build. Payloads cross the transport rather than a shared filesystem (measured ceiling 8 MB per response, hence per-clip calls), which is what makes the executor replaceable by an Unreal or Blender one rather than only nominally so.

> **Why the KB is not under `Assets/`.** It is produced by Python and consumed by Python; nothing about it is a Unity artifact. It was verified movable before the move: `MotionKBValidator.cs` reads the JSON with `System.IO` (not `AssetDatabase`, which it uses only to resolve `source_clip.guid` → a clip in `Assets/Animations/`), and all 96 guids of the former `Assets/MotionKB` assets were referenced by **zero** scenes/prefabs/materials/controllers. Moving it makes the engine-decoupling claim literal instead of aspirational — the agent half now runs with no Unity installed.

## 5. Workspace layout (siblings of `Animation/`)

This project lives under a larger workspace `…/Animation_agent/Project/`. Its siblings:

| Directory | Role |
|---|---|
| `Animation/` | **This project** (where all current work lands; has its own `.git`) |
| `VR4Nursing_v2 - Copy (7)/` | Original **source** nursing Unity project — the assets were first copied from here (contains the deliberately-excluded Intent/LLM/Python/XR systems) |
| `VR4Nursing_v2/` | The **improved source** — the scene's patient/bed/scenario are re-aligned 1:1 to this (2026-06-13); see SCENARIO.md §1/§9 |
| `unity-mcp-research/` | **MCP for Unity source** (Python `Server/` + C# `MCPForUnity/` package + `unity-mcp-skill/`); this project references its `MCPForUnity` via `file:` |
| `LLMR_Derived_Decoupled_Animation_Generation/` | Sibling **decoupled LLM→animation prototype** (re-copied 2026-06-16): Unity exports rig facts → a Python FastAPI server + OpenAI generates animation-as-text → Unity parses it to an `AnimationClip`. Reuse its **plumbing** (rig/clip exporters, FastAPI/HTTP bridge, text→clip parser) for Phase 2 — but it is LLM-_generative_, not retrieval-first. See HANDOFF §6. |
| `MCP_offical/` | An MCP-related reference / test Unity project |

## 6. How to open / run

1. Open the `Animation/` folder with **Unity Hub** using **6000.3.16f1** (first open re-imports assets, ~3.5GB, slow).
2. Open `Assets/Scenes/EmergencyRoom.unity` for the full scene, or `Assets/Scenes/NurseAnimTest.unity` for the IK demo.
3. MCP for Unity is wired up (HTTP transport), so the editor can also be driven programmatically — see [HANDOFF.md](HANDOFF.md) for the connection and tooling details.
4. **To drive it by language**, open `EmergencyRoom.unity` (the executor lives there) and press **Play**. The editor opens a console terminal, which starts the agent service in WSL if it is not already running and attaches to it; type an instruction in that window, in English. Leaving play mode closes both. Toggle the auto-open under *Tools > Animation Agent > Open Terminal On Play*. Running the service by hand, attaching a second terminal, and the model/API-key setup are documented in the agent repo's `README.md`.
   - **Three nurses answer to their names** — **Jill** (`CPRNurse`), **Dana** (`EKGNurse`) and **Kate** (`AirwayNurse`) — so say who: *"Kate, go over to the patient and check her pulse"*. With more than one connected, an instruction that names nobody is asked about rather than guessed at. Where to go can be an object, an anchor, `near:` something, or a side of your own view: *"Dana, walk to the right of my view"*.
   - Getting up is not something to arrange. Naming a standing action while she is seated generates the frames for leaving the chair first, then walks; she cannot walk while seated, so anything that goes somewhere already includes it.
   - Wiring is idempotent: *Tools > Animation Agent > Set Up Runtime In This Scene* re-derives the registry, the clip library and all three characters.

## 7. MotionKB at a glance (the 8 animations)

One `agent/animation_knowledge_base/<action_id>.json` per animation. Full field documentation in `agent/animation_knowledge_base/README.md`.

| action_id | Controller state | Trigger | MotionKB role † |
|---|---|---|---|
| `idle` | Idle | default | base |
| `walking` | Walk_N | `Speed`>0.01 | base |
| `typing` | Typing | `typing` | base (**seated**) |
| `giving_pills` | Give Aspirin | `giveAspirin` | overlay |
| `cpr` | CPR | `CPR` | overlay |
| `grab_bottle` | Get Aspirin | `getAspirin` | overlay |
| `check_pulse` | Check Pulse | `pulse` | overlay |
| `bvm` | BVM | `BVM` | overlay |

> **† `MotionKB role` = the action's `composability.base_or_overlay` in the JSON — a _conceptual_ classification for the future Phase-2 RAG composition (a foundational pose vs. something layered onto one). It is NOT the Unity Animator layer the clip lives on.** In the actual `NurseAnimator.controller`, _all_ of these action clips are states on the **Base Layer**, played **one at a time from Idle** (no AnyState; action → Idle → next action). The only _true_ Animator overlay layer is the **Upperbody Layer** (`Hold Pills` bool → carry a bottle while walking; masked to right arm + right fingers). Real body-part composition of the "overlay" actions is **Phase-2 work** that the `composability` block is _designed for_, not something the current engine performs (the current controller does runtime co-playback only, for disjoint parts).

All clips are Humanoid muscle clips retargeted to the single `nurse_avatar.fbx`, and all are in-place (`has_root_motion=false`). `typing` (added 2026-06-13) is the first **seated** action — it locks the whole body into a sitting pose and uses the two-handed `laptop` IK group; see the `posture` field in `agent/animation_knowledge_base/README.md`.

> **v2 note (the accepted store, as of 2026-06-24):** these same 8 actions are now the **v2 9-channel** root accepted store (`schema_version=motionkb/v2`, `status: accepted`) — the *action set* is unchanged; what changed vs the old v1 is the body-part split (legs/hands now left/right, a `root` channel, an orthogonal `ik_goals` layer) and the Python extractor. The "base/overlay" classification above carries over; the MEASURED magnitudes are in the records themselves (`agent/animation_knowledge_base/actions/<id>.json`, `channels.*.motion_magnitude`); the SEMANTIC per-channel 5-tuple is filled + verified (ADR 0008). The v2 field semantics live in [ADR 0007](docs/adr/0007-v2-body-part-split.md) + [ADR 0008](docs/adr/0008-vlm-proposed-authored-fields.md) + the v2 schema; the retired v1 field reference is the appendix in `agent/animation_knowledge_base/README.md` (preserved at git tag `kb/v1`).

## 8. Further documentation

- [HANDOFF.md](HANDOFF.md) — engineering handoff: environment & MCP setup, the pitfalls already hit, the critical-file list, and the next steps along the research roadmap.
- [SCENARIO.md](SCENARIO.md) — the canonical nursing scenario spec, aligned with the source project.
- [docs/adr/](docs/adr/) — architecture decision records (0001–0009); **[ADR 0009](docs/adr/0009-check-before-you-play.md)** is the pre-execution check and protocol v4; **[ADR 0007](docs/adr/0007-v2-body-part-split.md)** is the v2 9-channel split + the engine-decoupled Python extractor (supersedes ADR 0003); **[ADR 0008](docs/adr/0008-vlm-proposed-authored-fields.md)** is the VLM-propose + consistency-gate loop for the SEMANTIC fields.
- [docs/specs/motionkb-v2-spec.md](docs/specs/motionkb-v2-spec.md) — the v2 design narrative; `agent/animation_knowledge_base/schema/CHANGELOG.md` records the v1 and v2 schema changes.
