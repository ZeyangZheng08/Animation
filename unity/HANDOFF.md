# HANDOFF — for the next coding agent

> You're picking up a **language-driven, retrieval-first animation assembly framework** (a single LLM agent in deterministic scaffolding; agent side = Python service, engine side = replaceable executor — engine-decoupled; multi-agent is a later extension). All current work lives in the `Animation/` Unity project. Spend 5 minutes on this file and you'll avoid every pitfall already hit.
>
> **Language convention (important):** communicate with the user and write plans/docs in **Chinese**; but all **code, comments, JSON content, field names, file names, and identifiers** must be **English**. This is a hard requirement from the user. (These handoff docs are in English by the user's request.)

---

## 0. TL;DR — current state

> **✓ THE PROTOCOL BUMP HAD A THIRD SPEAKER, AND IT COST THREE SESSIONS (2026-08-19, later).**
> `agent/protocol.py` is the authority, `Protocol.cs` mirrors it, and **`terminal.py` had the version
> written into it as a literal** — it is standard-library-only and does not import the package. The v3
> → v4 bump updated two of the three, so **every instruction typed into the Play-mode window was
> refused at the door**: `ConsoleServer` logged the malformed line into a file nobody was reading and
> dropped it, the terminal drew a fresh prompt, and nothing happened. It presented as an intermittent
> hang in the model — zero CPU, the socket to the model established and idle, no trace line, `/stop`
> unable to reach it — and it was none of those. See the amendment on ADR
> [0009](docs/adr/0009-check-before-you-play.md).
>
> - **The rule: bumping `PROTOCOL_VERSION` means changing THREE files, and the third does not import
>   the first.** Grep for the constant, never for the import. `terminal.py` now reads it out of
>   `agent/protocol.py` and falls back to reading the source, so there is nothing left to forget.
> - **A refusal nobody can see is indistinguishable from being ignored.** The engine channel's
>   mismatch is fatal on decode and loud; the console channel's was logged and dropped. It now answers
>   down the socket the message came up on. Either fix alone would have sufficed; both are there
>   because the cost of neither was three days.
> - **It never reproduced under investigation**, because the harness used to reproduce it
>   (`drive.py`) imports the contract and was therefore always correct. Guessing found nothing three
>   times; the task dump found it on first use.
> - **What made it findable in the end.** A detached service used to run with its output on a hidden
>   Windows console: no log, no traceback, no progress line. Now `_traces/service.log` holds the log,
>   `kill -USR1 <pid>` writes every thread's stack (which still works when the process is blocked in a
>   syscall) and `kill -USR2 <pid>` writes every asyncio task and what it is awaiting. **No `run_turn`
>   task at all** is what said the turn had never been created, which is a different fault entirely
>   from a turn stuck on a response — and the two are identical from outside.
> - **The model leg is bounded now**: `--model-silence-s`, **20 s** by default, per stretch of the
>   model not answering (a response in flight, or a frame going out). Tool time is not on that clock
>   and progress resets it. Measured healthy responses land at 1–3 s; the first version of this bound
>   was 180 s and that was wrong for an interaction loop — a response that takes a hundred seconds has
>   already failed, whatever it eventually returns.
> - Also fixed on the way: `scene_search` had dropped the name a person says. Asked to drive **Jill**
>   the model got back `CPRNurse` / `EKGNurse` / `AirwayNurse` and answered that there was no
>   character called Jill, about a scene she is standing in. The spoken name lives in the executor's
>   handshake; the projection now merges it in.
>
> **✓ NOTHING VISIBLE MOVES UNTIL THE PLAN HAS BEEN CHECKED (2026-08-19). Protocol v3 → v4; both
> halves must be rebuilt together.** Every geometric check used to be an autopsy: the plan committed,
> the composer played it, `GateProbe` measured the pose a viewer was already looking at. Worse for a
> plan with a walk in it — `plan_motion(walk_to=…)` walked her across the room FIRST and derived the
> motion she had crossed it for afterwards. A commit is now played through on a hidden duplicate of the
> character and only a pass reaches the visible one. See ADR
> [0009](docs/adr/0009-check-before-you-play.md).
>
> - **The order.** `plan compiled once` → `motion.locomote preview` (route + projected arrival +
>   heading, agent neither enabled nor moved) → `motion.assemble mode=validate` (the whole plan on the
>   duplicate, standing at that arrival) → on a pass, `motion.assemble mode=commit` with **the same
>   bytes**. The model still sees `dry_run` and `commit`; `validate` is between the service and the
>   executor and costs no model iteration.
> - **One judgement, two clocks.** `GateEvaluator` (new) holds every threshold, accumulation and metric
>   shape and knows nothing about frames; `GateProbe` is now a thin MonoBehaviour that feeds it real
>   elapsed seconds and `ValidationCharacter` (new) feeds it simulated ones. `GateArming` (new) decides
>   what to watch, for both. **No threshold was added, changed or invented** — the numbers are the ones
>   that were already there, moved once.
> - **Fast, not real-time.** The duplicate's composer graph runs in `DirectorUpdateMode.Manual` and is
>   stepped by hand. `PoseSynth.Step(dt)`, `IkBinder.Step(dt)` and `MotionComposer.Tick(dt)` were split
>   out of the frame loop for that, and `PostureTransitionEvaluator` (new) holds the descent curve both
>   paths share. Measured on `EmergencyRoom`: walk-and-sit = 12.0 s of animation in 721 samples, whole
>   `validate` round trip 40–160 ms.
> - **Verified live, not by inspection.** `smoke_validate.py` (new, agent repo) drives the real tools
>   against play mode. `walk over and sit down to type` validates 13 metrics — both hands' contact hold
>   and reach, `seated_on_support`, `hip_reached_target`, `descent_saturated` — then walks, then
>   commits. `typing` with the patient as `sit_on` comes back `sat_through_support on obj:Patient` and
>   **she does not move**.
> - **The trap that cost the most, written down because it was not obvious.** Animation Rigging runs as
>   a SECOND `PlayableGraph` on the same Animator; at runtime Unity composes the two by output sorting
>   order. Under manual evaluation that does not happen — each `Evaluate` is a full animation update for
>   one graph alone, so the rig's pass **replaced** the composed pose. Every sample came back
>   byte-identical and a metre low: `ground_penetration` 0.659 m on a plain `idle`. Fix is
>   `RigBuilder.BuildPreviewGraph`, splicing the constraints into the composer's graph
>   (`MotionComposer.ExtendGraph`). Afterwards: feet 0.0798 m, hips 0.9011 m, and a `walking` probe
>   shows 0.062 m of hip travel — the number that tells "the graph advanced" from "the clip held still".
> - **What is NOT covered, stated rather than implied.** A `carry` is reported under `unmeasured`:
>   attaching the real prop to the duplicate is exactly the visible mutation this avoids. There is still
>   **no body-versus-scene collision metric** — there was not one before either, and this did not
>   acquire one: the only geometry any gate touches is a foot against a single scalar floor height, a
>   pelvis against one seat's surface height and axis-aligned bounds, and a named hand against a named
>   object. Nothing tests a forearm through a bed rail, and `AgentRuntime` makes no `Physics` call at
>   all.
> - **`foot_skate` reads differently on the two clocks, and that is why it still has no threshold.**
>   Measured the same day: 2.1449 m/s on the duplicate against 1.5341 m/s through the runtime probe on
>   a real walk. The duplicate has no NavMeshAgent, so a locomotion clip strides on the spot and the
>   whole stride counts as slide; at runtime the agent cancels part of it. A cutoff under ~1.5 would
>   fail every walk that plays, under ~2.15 every walk at the check, and above that it catches nothing.
>
> **✓ THE SCENE SURFACE IS TWO TOOLS (2026-08-19).** `scene_find` / `scene_describe` / `scene_anchors` /
> `scene_position` → **`scene_search`** ("which thing is that?" — id, label, aliases, nothing else) and
> **`scene_query`** ("what is it to her right now?" — `exists`, `within_arms_reach`, `needs_walking`,
> `held_by`). Category, surface height, transforms, distances, `carriable` and per-hand-anchor flags are
> gone from the model's view; they still exist and are still consumed by `_verify_seat`, the descent and
> the gate. The wire is **unchanged** — `scene.find`/`describe`/`anchors`/`position` are engine-internal
> API now, reached only from `agent/tools/scene.py`. Anchors are entities, so `scene_search('bedside')`
> finds `anchor:Bedside` and there is no separate anchors call.
>
> - The defect this closes structurally: a model fills in every field a schema offers, and a blank
>   `reachable_by` turned an unconstrained search into "within arm's reach right now" — measured, ten
>   empty `scene_find` calls in one turn and an agent concluding a room with a chair in it had no chair.
>   The old fix was a guard (`_asked_for`); this is the same defect with the fields removed.
> - Residual, out of this change's scope and worth knowing: `plan_motion` still echoes
>   `generated_transitions` with hip heights and surface heights in metres. The model cannot ASK for a
>   number any more; it is still handed some in the report of a plan it committed.
>
> **✓ A CLIP CAN BE TAKEN APART (2026-08-13).** Assembly's smallest unit used to be a whole clip hung on
> a channel, and a grip used to veto the whole plan. Both are gone, and the composition count went from
> **10 real two-source pairs to 24** out of 56. `probe_compose.py` is the figure; run it before quoting
> anything here.
>
> - **An overlay contributes the frames it is actually moving in, and one repetition where it repeats.**
>   New `agent/segments.py` + `build_segments.py` → `agent/kb/_derived/segments.json`, a derived sidecar
>   on exactly the terms `_derived/transitions.json` already had: `kind: derived`, fingerprinted against
>   `_raw`, referenced by no record, **not** a contract change. The accepted 8 JSONs and the schema are
>   byte-identical; golden stays 8/8.
>   - **Measured, and most of it came out negative — read this before assuming it helps everywhere.**
>     Trimming dead ends is worth almost nothing: `check_pulse`, `giving_pills`, `cpr` and `walking` have
>     **no** dead frames on any channel. What is worth a great deal is the repeat: `cpr` repeats every 18
>     frames on all eight channels (0.00° residual against a 2–4° typical spread — thirty compressions,
>     540 frames → 18) and `bvm`'s right hand every 89 of 180. `grab_bottle` loses the 6 frames it holds
>     still at the end. Everything else plays whole.
>   - Thresholds are read off the measured distribution and the gap each sits in is in the module
>     docstring: `MIN_LAG=4`, `CYCLE_TRAVEL_FLOOR=20°` (cyclic channels travel 98.6 and 241.8; the busiest
>     non-mover is `bvm.head` at 4.4 — a 20× gap), `CYCLE_RESIDUAL_FRAC=0.10` (cpr 0.000, bvm 0.088,
>     nearest rejection `typing.torso` 0.103 — close, which is why the fundamental must also beat
>     MIN_LAG; every rejection in this corpus fails both tests).
>   - **One window per ACTION, not per channel** — a clip is one performance, same reason a mixed overlay
>     gets one phase. Still channels are excluded from the union: they look the same at every frame, so
>     they constrain nothing. Without that, `bvm` handed out all 180 frames because three `support`
>     channels travelling under 4° outvoted the hand that repeats.
>   - **The base is never cut.** It sets the posture everything else hangs on.
>   - Wire: `MotionComposer.LayerSpec` gained `ClipEndSeconds` and `LoopInWindow`, handled in the existing
>     `HoldFinal` loop in `Update`. Unset behaves exactly as before. Whether a window loops is decided
>     agent-side, where its two ends were measured (0.00–0.37° apart for a repetition).
> - **A contested grip drops an OBJECT, not the plan.** Six of the eight actions grip with the right
>   hand, so as a veto this refused 20 of 56 pairs outright. A grip is two things fused and only one is in
>   the animation: the FK curves are joint rotations, and what holds the bottle is the `ik_goal` aiming
>   the wrist plus the event that shows the prop — both outside the clip. So the hand goes whole to one
>   side (a hand is a shape, not an axis — never mixed) and the other keeps its MOTION without its
>   OBJECT, reported as `dropped_grips` on the plan and as a `warn` gate. **Still refused** when the
>   request named both objects (`carry` one, `ik_bindings` the other): there is nothing left to decide.
>   - Tiebreak order: what the request named → `ROLE_PRIORITY` → the base. Every tie in this corpus is
>     primary-vs-primary on `right_hand`, so the request is the only real discriminator and the base is
>     the fallback — the same tiebreak `Mix.owner` uses.
> - **A composed motion plays WHILE she walks.** `_walk_there` played a hardcoded bare `walking`, and
>   `plan_motion(walk_to=…)` waited for arrival before committing anything else — so the one shape of
>   composition this corpus can express was the one it never showed. `_play_in_place` takes overlays,
>   `_walk_there` takes `under=`, and the plan is derived and committed at DEPARTURE. On arrival the walk
>   becomes a stance and the overlay carries on (`played_while_walking` says so). The engine needed no
>   change: `locomotion.Halt()` only ever ran inside `RunPostureChange`.
> - **Fixed in passing, in the same poll loop: an `Infinity` that silently dropped a committed walk.**
>   `Locomotion.Remaining` returned `NavMeshAgent.remainingDistance` raw, which is infinite while a path
>   is partial; Newtonsoft writes a non-finite float as the **string** `"Infinity"`; `scene.py` compared
>   it to 0 and raised; `registry.py` reported that as **"bad arguments for plan_motion"** — for a walk
>   already dispatched and still going. Three fixes: `Remaining` returns -1 for non-finite, the reading
>   end treats a non-number as unknown, and `dispatch` binds the signature FIRST so a `TypeError` from
>   inside a tool is named as the tool's defect instead of the model's. Measured symptom: the model sent
>   byte-identical parameters twice, failing then succeeding.
>
> Hermetic: **266 pytest**, eval floor **7/12** unchanged, both decompose cases still reproduce ground
> truth channel for channel, `build_transitions --report-only` unchanged, `probe_pairs` 56/56.

> **✓ ANY NURSE, ANY PLACE, ANY ACTION (2026-08-12).** Four limits that made the demo one scripted path
> are gone. The goal is stated that way on purpose: getting up off a chair was only the instance that
> happened to be blocked, not the feature.
>
> - **A body channel may now have TWO sources.** `assemble.arbitrate` no longer resolves a contested
>   channel winner-take-all. Per channel: neither claimant grips an object → **mix**, at shares from the
>   existing `ROLE_PRIORITY` normalised (primary/support = 0.6/0.4, primary/primary = 0.5/0.5); exactly
>   one grips → that one takes the channel whole; both grip **different** objects → a Conflict, named.
>   Root never mixes. **ADR 0004 is amended, not revoked** — read the amendment before quoting it.
>   - **Superseded on 2026-08-13, in the last clause only:** two grips no longer refuse the plan. The
>     channel goes whole to one side and the other loses its object, reported as `dropped_grips`; a
>     Conflict is left only when the request named both. Everything else in this entry still stands.
>   - The primitive was measured BEFORE anything was built on it, because the whole thing rests on it: a
>     masked `AnimationLayerMixerPlayable` layer at a fractional weight **interpolates**. `walking` under
>     `nurse_cpr_30`, masked to the right arm, sampled at weights 0 / 0.5 / 1 → the ends are 69.14° apart
>     at the right elbow, the midpoint sits 34.21° from one and 35.01° from the other (0.08° off the
>     geodesic); the left elbow, outside the mask, moved 0.00°. Weight blends, mask still confines.
>   - **The finding that changed the design, and it will change yours too.** The first rule was "role tie
>     → 50/50". Enumerating all 22 contested pairs killed it: ties occur ONLY on `right_hand` (20 pairs)
>     and `right_arm` (6), and **every `right_hand` tie has both sides holding a different object** —
>     cpr/`patient_chest`, giving_pills/`pills`, grab_bottle/`aspirin_bottle`, bvm/`bvm_bag`,
>     check_pulse/`patient_wrist`. Half a hand on a chest and half on a bottle satisfies neither grip, so
>     that branch alone has **zero** exercisable pairs here. The only contested pairs with no grip either
>     side are `cpr`+`walking` and `giving_pills`+`walking` — the legs, `support` vs `primary`. That is
>     what mixing is FOR in this corpus, and it is the `dc-walk-carry` shape.
>     - That enumeration is still the right reading of the corpus, and it is why the 2026-08-13 change
>       took the other route: those 20 pairs are not mixable, but they are still ASSEMBLABLE once the
>       object comes off the losing hand. The hand itself is never split, then or now.
>   - Wire: `MotionComposer.LayerSpec` gained `Weight` and a **per-layer** `ClipStartSeconds` (two clips
>     mixed at unrelated phases average two unrelated poses). Layer 0 is pinned at 1 — a weight below 1
>     there fades the whole body toward the bind pose rather than mixing it with anything.
>   - **One phase per clip, not per channel.** Asked separately the two legs want frames 11 and 1; a clip
>     is one performance and honouring both would play one walk cycle at two phases, i.e. two legs
>     stepping independently. `transitions.mix_entry_frame` takes the channel LIST and minimises the
>     worst. Entry alignment only — the clips then drift, and that is left for the gate to measure.
>   - `Assembly.shared` is deliberately **not** folded into `layers`: `layers` is the ownership partition
>     `run_eval` scores by set equality, and a channel appearing under two actions there would make the
>     ground truth unscoreable. Both eval decompose cases still score **F1 1.00**.
> - **Posture change is symmetric.** The two hardcoded refusals in `AgentCharacter` are replaced by one
>   rule (a plan opening in a posture she is not in needs generated frames). `RunDescent` →
>   `RunPostureChange`; landing standing zeroes the correction, restores `_posture`, and calls the
>   previously-uncalled `locomotion.Resume()`. Everything that computes the change was already symmetric
>   (`schedule` takes `abs(start-target)`, PoseSynth's clamp is `±_reach`) — only the refusal was not.
>   - **Order matters and cannot be fixed engine-side.** `plan_motion` now reads the current posture and
>     commits the rise FIRST, waiting on `on_navmesh` (which is the rise finishing, observed rather than
>     timed). Travel cannot come first: `Go()` re-enables the NavMeshAgent, and enabling one warps the
>     transform to the nearest walkable point, which is not under the chair.
> - **Three nurses, by name.** `CPRNurse`=**Jill**, `EKGNurse`=**Dana**, `AirwayNurse`=**Kate**, wired by
>   an authored table in `AgentRuntimeSetup` — not inferred from the nameplates, which sit *near* their
>   nurse rather than parented under her, so pairing by distance would silently send an instruction to
>   the wrong person. `AirwayNurse` appears **twice** in the scene, so the lookup ranks (humanoid
>   Animator + NavMeshAgent + active) and logs the path it chose, the same way `BuildRegistry` already
>   handles seven objects called `pill_bottle`. Hello carries `character_names`; `_who()` resolves
>   id → name → scene object → substring and **asks** on ambiguity.
> - **Destinations that are not objects.** `view:left|right|ahead|behind` (relative to `Camera.main`,
>   keeping her current depth along the line of sight, so "to the right" is sideways and not toward the
>   viewer) and `near:<object_id>`. Resolved engine-side onto the navmesh, metres staying there — the
>   same rule as the `arms_reach`/`same_station` vocabulary.
> - **Console.** Successful calls collapse to their names on one row; failures keep the full line. One
>   closing line: `N tools · X.Xs deciding · +Y.Ys waiting on motion`, where the wait is **measured
>   inside the tool at each poll** and bucketed. Tools can also talk mid-run — the three-second walk used
>   to be three seconds of blank screen. Both borrowed deliberately: codex's `RuntimeMetricsSummary`
>   (`codex-rs/tui/src/history_cell/separators.rs`) buckets a turn's time by what it went on rather than
>   reporting one wall clock, and opencode's `tool_details_visibility`
>   (`packages/tui/src/routes/session/index.tsx:1721`) hides a tool only when it *succeeded*, with
>   `ExecuteResult {title, metadata, output}` splitting what the UI reads from what the model reads.
>
> **Verified:** 258 pytest · eval floor unchanged 7/12, both decompose cases F1 1.00 · `probe_mix.py`
> end to end (**0.600 held on both legs**, matching the derived share, read back off the mixer rather
> than echoed) · `probe_pairs.py --engine` **56/56 committed live**, including the seven `typing → X`
> pairs that were hard-refused before.
>
> **Do not quote `probe_pairs.py` without `--engine` as coverage** — it answered 56/56 before this work
> too, because `schedule` was always willing to plan either direction. It guards the seam table; the
> engine arm is what measures the refusal that was actually blocking.
>
> **✓ The ambubag squeeze now works, and stops flooding the console.** `NurseAnimatorEvents.bagMesh` was
> `abvrm_face_mask` on all three nurses — **0 blend shapes** — while the squeeze lives on
> `abvrm_self_inflatingbag` (`abvrm_blendShape.squish`). So `GetBlendShapeWeight(0)` threw every frame
> out of `Update`, the bag never compressed, and the console filled fast enough to hide anything else.
> Repointed **by what the renderer has** rather than by name (exactly one of the ten under the ambubag
> carries a blendshape), and the script now resolves that once at `Start` and does nothing rather than
> throwing when it cannot — a null check passed and the call threw anyway, which is why this survived so
> long. Also clamped: the bounds were checked before the `deltaTime`-sized step, so a cycle overshot to
> **-1.12** instead of 0 and extrapolated the bag past its neutral shape. Verified in play mode — console
> clean, full cycle returns to 0.0000.
>
> **One thing left standing, pre-existing:** the **prop registry is single-character**. There is one
> `obj:AspirinBottle` and `Rank()` prefers the one in `CPRNurse`'s hand, so asking Kate to grab the
> bottle binds her hand to Jill's. Per-character props are a registry change nobody has scoped.
>
> **✓ AGENT SIDE SPLIT OUT TO WSL — TWO WORKING REPOSITORIES (2026-08-05; a third, the publish mirror,
> arrived 2026-08-18 — see §1.1 for all three and the rules for working across them):** the agent half moved to
> **`~/Research/animation-agent`** on WSL (Ubuntu 24.04, `E:\WSL\Ubuntu`, ext4 — not `/mnt`, whose DrvFs is an
> order of magnitude slower for the stat-heavy work Python imports and `git status` do). **This repo keeps
> the Unity project *and* the MotionKB**; the split line is "derivative of Unity's animation assets" vs
> "engine-independent logic", not "data vs code". The KB cannot be regenerated without Unity (`_raw/` is
> in-engine `AnimationMode` sampling, `_frames/` in-engine rendering) and grows only when a clip is
> imported here, so adding an action stays **one atomic commit** holding both the FBX and its KB entry.
> The agent side reaches it via `MOTIONKB_DIR` and treats it read-only. **Each repo's git runs natively
> on its own side** — Windows git here, Linux git in WSL for the agent repo. (An earlier version of this
> line said git runs on Windows only, which is wrong. What is forbidden is WSL git reaching across to
> `/mnt/f`: it reports ~812 bogus dirty files.) The agent side writes through `paths.write_*` (UTF-8, no
> BOM, LF, atomic) so nothing it writes
> can reintroduce the CRLF mess fixed earlier the same day. Writing LF is only half of it: `.gitattributes`
> pins `agent/kb/**/*.json` and `**/*.md` to `text eol=lf`, because this repo sets `core.autocrlf=true` and
> would otherwise check the KB out as CRLF. That corrupts nothing — the clean filter converts back, so
> `git diff` stays empty — but git will not mark such a file clean in its stat cache, so every pipeline run
> left `_reports/kb_state.md` permanently `M`. Verified 2026-08-06: a full `check_kb.sh` now rewrites the
> report and leaves the tree clean. Do not remove those two lines; `git status` is the KB's drift detector,
> which is the same reason pose dumps are written back verbatim. What landed:
>
> - **`Assets/Editor/MotionKB/MotionKBValidator.cs` DELETED.** The `guid → AnimationClip` layer is now
>   `validate_guids.py` in the agent repo: it generates C# and posts it over the Unity MCP bridge, exactly
>   as `build_find_clip_csharp` / `build_resolve_controller_csharp` already did. **No agent code remains
>   inside the Unity project.** Verified 8 resolved / 0 failed, and Unity recompiles with zero errors
>   (the directory's orphan `Assets/Editor/MotionKB.meta` was caught and removed *before* the commit —
>   the exact trap that bit the 2026-08-05 move earlier).
> - **Payload crosses the transport, not a shared filesystem.** The generated sampler/render C# no longer
>   writes files: the pose dump comes back as the `execute_code` return value and the frames as base64.
>   Measured ceiling **8 MB per response (16 MB fails)**, against ~560 KB per pose dump and ~3.2 MB per
>   clip of frames — so calls are now issued **per clip** (`extract.py sample [clip]`), which also makes a
>   mid-corpus failure resumable. **Verified byte-identical:** re-sampling `Idle` and re-rendering its 6
>   frames leaves `git status` clean. Pose dumps are written back **verbatim, not re-serialized** — they
>   are one line with no trailing newline, and re-indenting them would turn every re-sample into a
>   65k-line diff and destroy `git status` as the KB's drift detector.
> - **Runtime channel is separate from the MCP bridge and must stay so.** MCP ships C# and is offline-only;
>   the runtime channel is a WebSocket where **the agent is the server and the engine connects in** (the
>   editor drops managed state on every recompile and play-mode toggle, so the reconnecting party must be
>   the engine). It carries typed messages, never code. `runtime/echo_server.py` + `runtime/ws_probe.py`
>   are the skeleton that proved the channel. **The contract has since landed:** `agent/protocol.py` (v4)
>   is the authority, mirrored by `Assets/Scripts/AgentRuntime/Protocol.cs`, and the executor is the
>   eleven components under that same folder.
> - **Networking:** `networkingMode=mirrored` in `C:\Users\<you>\.wslconfig` (+ `wsl --shutdown`). It is
>   **required, not an optimisation** — under NAT, WSL could not reach the bridge at all, and Windows → WSL
>   is the harder direction. Measured: WSL → bridge `/health` p50 **2.410 ms** (Windows-side 3.010 ms, so
>   the boundary costs nothing); Unity → WebSocket server in WSL, 1000 × 256 B, p50 **0.320 ms** / p99
>   0.557 ms. The real floor is the frame loop — a message is only actionable at the next `Update`, up to
>   16.7 ms at 60 fps, ~50× the wire. Design against that, not against the transport.
> - Python env: Miniconda at `~/miniconda3`, env `animation-agent` (python 3.12, **conda-forge only** —
>   the `defaults` channel now requires accepting Anaconda's ToS). Packages are for the runtime service;
>   **the offline pipeline is still stdlib-only** and runs on a bare `python3`, so a broken env never
>   blocks KB verification. `check_kb.sh` runs all four gates: 8/8, 8/8, in sync, 8 resolved.
>
> **✓ Direction change applied (2026-06-13):** the frozen patient was changed from
> **P3 (lying flat, unresponsive)** to **P0 (awake, bed "Idle Up" = backrest raised)** = the
> runtime-start pose of the improved source **`VR4Nursing_v2`**, and the patient/bed placement
> was aligned 1:1 to it (same room world origin; assets/controllers verified identical by GUID).
> Done in-engine via Unity MCP: baked `Idle Awake` (patient) + `Idle Up` (bed) @ frame 0, reset
> `patient_avatar` local pos to `(0,0,0)`, Animators kept **disabled**. Verified **edit == Play**
> (identical to 4 decimals) and **no clipping** (mesh rests on mattress/cover). The pre-change P3
> scene is backed up at `EmergencyRoom.P3backup.unity` (project root). See `SCENARIO.md` §1.
>
> **✓ Full-scene alignment to v2 (2026-06-13):** a 6-region diff (root/nurses/props/anchors/lighting/UI)
> confirmed this scene is **visually identical** to `../VR4Nursing_v2/` except the deliberate exclusions
> (XR rig, runtime/intent scripts, gameplay UI canvases; `Main Camera` kept active as the XR-removal
> camera; patient `HeadAim` source null). The one structural gap — `idle1/2/3` + `CPRLocation` anchors
> under `animpts` — was added (animpts now 19 children, matching v2). Keep-list in `SCENARIO.md` §3/§9.
>
> **✓ Typing-IK reworked (2026-06-13):** the `EmergencyRoom_TypingTest` hand IK now **reuses `NurseIKHelper`** — the driver calls its `LaptopIK`/`ResetHandsIK` on Typing enter/exit, because the Typing clip's baked **animation events don't dispatch in this standalone scene** (the earlier version bypassed `NurseIKHelper` and drove the constraints directly). The earlier **palms-up hands** were root-caused to an **avatar mismatch**: the laptop IK-point rotations are authored for `nurse_avatar`, but `Nurse1` is a `FemaleScientists` model (different hand-bone bind axis). The demo now runs on a new **`TypingNurse_Avatar`** (a `nurse_avatar` copy) with correct **palms-down** hands; in that scene only it + `patient_avatar` are active (all other nurse avatars `SetActive(false)`). See §3.

> **✓ Repo hygiene pass (2026-06-16):** removed dev residue from `NurseAnimator.controller` — the
> leftover **`Test State`** + its `Idle→Test State` transition, the unused **`TEST`** trigger, and the
> never-referenced **`IsBusy`** bool param (base-layer params now 13, states now 13) — and deleted the
> orphan **`NurseHands.mask`** (referenced by no layer). Changed the MCP package ref in
> `Packages/manifest.json` from a machine-specific **absolute** `file:` path to the portable relative
> **`file:../../unity-mcp-research/MCPForUnity`** (now matches README §3's wording). Doc fixes: README §7
> clarifies its "base/overlay" column is the MotionKB *composability* role, **not** a Unity Animator layer
> (all action clips are Base-Layer states played one-at-a-time from Idle; the only real overlay layer is
> Upperbody/`Hold Pills`), and the §7 acceptance line below was corrected "7"→"8" MotionKB JSON. Verified
> in-engine: clean compile, no missing scripts, MCP bridge survived the manifest change.

> **✓ Patient/bed animation ENABLED (2026-06-16):** per the user, the patient's and bed's Animators
> were turned back **on** (live), reversing the earlier "frozen backdrop" decision. Patient plays
> `Idle Awake`+`Breathing` **in place** (no drift — `applyRootMotion` is off; patient `worldPos` verified
> unchanged in Play), bed holds `Idle Up`. Edit-mode still shows the baked P0 pose, and an A/B test
> (2026-06-16) confirmed a recompile / domain reload does **not** revert it to T-pose (the pose is in the
> *serialized* transforms, which survive reload; an idle enabled Animator doesn't override them in edit mode).
> So **no editor hook is needed** (an `EditModeAnimatorPosePreviewer` hook was tried, verified unnecessary, and removed). Scene **saved**. **This did NOT fix the blue
> `Cover` clip** — `Idle Up` is a static hold and the bed's cloth (`Cover`/`Sheet`/`mattress`) are plain
> **skinned** meshes (no cloth sim), so the blanket's drape into the side frame is identical animated vs
> frozen. The cover was a separate open item — **RESOLVED 2026-06-17 (see next note)**; the fix had to
> edit the skinned mesh's *source verts* (NOT bake to static — that would desync it from the now-live bed).

> **✓ Blue `Cover` clip FIXED (2026-06-17):** the blanket (`Cover`, a SkinnedMeshRenderer that **shares all
> 21 bones with `mattress`**) draped down to world-Y **0.441**, into the side-rail frame (`Base_00/Base_01/Holders_B`
> top ≈ 0.55). Two non-options were ruled out: moving/scaling the `Cover` GameObject does **nothing** (skinned
> meshes ignore their own transform — bones drive every vertex), and nudging bones is impossible (shared with
> the mattress → would deform it too). The **only** lever that keeps the bone-skinning is editing the mesh.
> Fix: a per-vertex **smooth Y-lift** of the hanging skirt — for every vertex with baked world-Y < 0.63, raise it
> by `tt²·0.149` (`tt = (0.63−Y)/(0.63−0.441)`, a C¹ ramp = no crease at the threshold), leaving XZ untouched so
> the silhouette can't shear. Each new world pos is converted back to a source vertex via the exact inverse skin
> matrix `M⁻¹` (`M = Σ wᵦ·boneᵦ.localToWorld·bindposeᵦ`; verified `M·src == BakeMesh` to 0.00000). Result:
> hem rises to **0.570** (above the rails), **0 cover verts below 0.55**, edge stays smooth, blanket still skins
> to the same bones → still follows the bed animation. Saved as **`Assets/Animations/BedAnimation/Cover_declipped.mesh`**
> and assigned to the scene's `Cover` SMR; `EmergencyRoom.unity` saved. **Lesson:** a *first* attempt that pulled
> each vert a fixed distance toward the bed centroid tore the edge into a sawtooth (per-vertex direction varied) —
> height-only transforms are the safe class for declipping a draped skinned mesh in-engine. To revert: reassign
> `Cover.sharedMesh` to the `hospital_bed.fbx` sub-mesh named `Cover` and delete the `.mesh` asset.

> **✓ White-triangle 穿模 on the blanket FIXED (2026-06-17, the one the user actually saw top-down):** the side-rail
> drape above was *not* what bothered the user — from a top-down view two small **white triangles** showed on the
> blue blanket beside the patient's lower legs. Root cause: the white **`Sheet`** (a separate skinned mesh under the
> `Cover`) **pokes above the blanket** where the blanket dips between/beside the raised legs (verified: hiding `Sheet`
> made the triangles vanish and the `Cover` there was a *continuous* surface = no hole). Double-siding
> `Bed_Cover` (`_Cull=0`) did NOT help (ruling out a flipped face), so it's a true poke-through. The under-layers
> (white `Sheet` AND dark-grey `mattress`, `Bed_Plastic`) poke up through the blanket where it sags beside the raised
> legs. Two detection traps: (1) a vertex-vs-`coverMAX`-per-cell test FALSE-NEGATIVES — the `mattress` is a **sparse
> 214-vert mesh** whose big *triangles* poke through between cover verts even though every mattress *vertex* is below the
> cover; (2) a global "raise the cover above the under-layers" anti-sink **overshot badly** (raised 14k verts up to 0.13 m
> → floated the whole blanket — reverted). **Final fix** = push the under-layers DOWN, accurately: build a `coverMIN`
> heightmap (200-grid, 5×5-neighborhood min = the cover's local floor), then for every `Sheet`/`mattress` vert above
> `coverFloor − 0.010 m`, push it down to there via inverse-skin `M⁻¹`. Push-DOWN only + only inside the cover footprint
> ⇒ everything stays hidden under the blue blanket, and the sheet/mattress still show normally at the bed edges. Moved
> 9872 `Sheet` + 10 `mattress` verts; **verified clean from a 12-shot orbit + top-down + close-ups** (no white or grey
> slivers any angle). Saved as **`Sheet_declipped.mesh`** + **`Mattress_declipped.mesh`** (both in
> `Assets/Animations/BedAnimation/`), assigned to the scene SMRs; `Cover` stays the skirt-lift `Cover_declipped.mesh`.
> `EmergencyRoom.unity` saved with all three. To revert any layer: reassign its SMR `sharedMesh` to the matching
> `hospital_bed.fbx` sub-mesh (`Cover`/`Sheet`/`mattress`) and delete the `_declipped.mesh` asset.

> **✓ MotionKB v2 LANDED as candidate (2026-06-18):** the body-part split was reworked from v1's 6 parts
> to **9 channels** (`root, torso, head, left/right_arm, left/right_leg, left/right_hand`) plus an
> orthogonal `ik_goals` layer, and the extraction was rebuilt as an **engine-decoupled Python program**
> under `agent/motionkb/` (`config.py` channels/bone-map/divisors/thresholds · `metrics.py` formulas ·
> `extract.py` assembly + semantic-preserving merge + run-log · `unity_sampler.py` the generic
> pose-sampler). **Unity MCP is now used ONLY to sample muscle clips** (a generic pose-sampler *generated*
> from Python config, holding no KB knowledge) — all KB knowledge lives in Python. Metrics are empirically
> calibrated (each divisor maps its most-active reference clip to ~0.85; thresholds sit above each
> channel's idle noise floor). The 8 v2 files are in `agent/kb/candidate/` (schema `motionkb/v2`),
> with **MEASURED numerics filled + validated 8/8** — `python agent/motionkb/validate_motionkb.py` now targets
> `candidate/` against `motionkb.v2.schema.json` (run-log: `agent/kb/_reports/extract_run.md`).
> **PENDING AS OF 2026-06-18 (since RESOLVED — see the 2026-06-24 ADR 0008 note below):** the SEMANTIC
> 5-tuple (`role/motion_type/contact/constraint/target`) + `composability` need a **human authoring pass**
> (seeded `null`, flagged `extraction.field_origin.semantic_pending`) before candidate→accepted promotion. **Until then the root-level v1 `*.json` remain the accepted store.**
> Decision record = [ADR 0007](docs/adr/0007-v2-body-part-split.md) (supersedes 0003); narrative =
> `docs/specs/motionkb-v2-spec.md`; CHANGELOG = `agent/kb/schema/CHANGELOG.md`; engine-neutral
> channel vocabulary = `agent/kb/engine_mask_map.json`. The `git tag kb/v1` rollback anchor that
> ADR 0007 references **now exists** (`git tag` → `kb/v1`), so the v1 store can be restored before promoting v2.

> **✓ SEMANTIC 5-tuple FILLED + VERIFIED 8/8 via the VLM-proposal loop (2026-06-24, [ADR 0008](docs/adr/0008-vlm-proposed-authored-fields.md)):**
> the per-channel `role/motion_type/contact/constraint` — the KB gating item — is now authored on all 8
> v2 candidates and `verified_against_screenshots=true`. Mechanism (ADR 0008 — this is **offline
> data-authoring tooling, NOT a Phase-2 `agent = model + harness`**; that concept is reserved for the
> runtime multi-agent framework): a **VLM proposes** the 5-tuple from multi-angle
> frames **rendered over Unity MCP** (`AnimationMode.SampleAnimationClip` onto a nurse_avatar → temp-camera
> RenderTexture PNGs in `Assets/Screenshots/{gp_*,kb_*}.png`); a **new deterministic consistency check**
> (`validate_semantic_consistency()` in `agent/motionkb/validate_motionkb.py`) **gates** each proposal against the
> MEASURED block + `ik_goals` + `composability` (e.g. `role==free` ⟺ composably free; an ik_goal ⇒
> `constraint ∈ {must-reach, must-maintain}` + `contact==object:<obj>`; no `hold-static` on a dynamic channel; `manipulate`/
> `reach` forbidden on a static channel; `cyclic-locomotion` needs root dynamic); then a **human accepts**.
> Provenance: `extraction.field_origin` gained a `vlm_proposed` tier and an `extraction.vlm_proposal`
> audit block (schema updated); on accept the fields move `vlm_proposed → semantic` and `verified_by/at` +
> `verified_against_screenshots=true` are set. **MEASURED was never touched — ADR 0002 still holds.**
> `python agent/motionkb/validate_motionkb.py` → 8/8 pass, **0 `vlm_proposed` left**. Per-channel `target` stays
> `null` by design (deferred to the Phase-2 scene-grounding agent). **The KB authoring blocker is cleared;
> the only remaining KB step is the candidate→accepted store promotion that replaces v1.**

> **✓ candidate→accepted PROMOTION DONE — v2 is now the accepted store (2026-06-24):** the 8 v2 files were
> promoted to the **root** `agent/kb/*.json` with `status: accepted` (moved out of `candidate/`, which
> is now the empty staging area for future re-extractions); the former v1 6-part store is **retired** but
> preserved at git tag **`kb/v1`** for rollback (ADR 0005 / [docs/ROLLBACK.md](docs/ROLLBACK.md)). The v1
> schema (`schema/motionkb.v1.schema.json`) is kept as the `kb/v1` contract. `python agent/motionkb/validate_motionkb.py`
> now validates the **root** accepted store (its dir-glob skips `engine_mask_map.json` via `NON_ACTION_FILES`)
> → **8/8 pass, 0 failed**. **MotionKB Phase-1 is COMPLETE** — data contract + Python extractor + MEASURED +
> SEMANTIC 5-tuple + promotion, and (2026-06-24) the do-now/enabler backlog is now **also closed**:
> `agent/motionkb/test_golden_extraction.py` (golden re-extraction regression, 8/8 — MEASURED reproduces from the
> frozen `_raw`), `Assets/Editor/MotionKB/MotionKBValidator.cs` (the Unity-only guid→asset layer, 8 resolved →
> `_reports/kb_state.md`), `agent/motionkb/gen_kb_manifest.py` + `agent/kb/kb_manifest.json` (corpus index, no
> content hashes — ADR 0005), and the `agent/kb/retrieval_eval_set.json` seed. `scripts/check_motionkb.sh`
> runs all live checks green. The only KB work left is the genuine **Phase-2-build** (run_eval.py, the bake
> emitter), which waits on Phase-2 by design.

> **✓ PROPOSING IS NOW A PROGRAM + ALL 8 RE-PROPOSED WITH gpt-5.5 (2026-06-25, commit `78dea99`):** the
> ADR-0008 propose → gate → accept loop is now code — `extract.py render|propose|author` + `vlm_openai.py`
> (stdlib OpenAI vision client) + `propose.py`. `render` saves isolated multi-angle frames (avatar on a ground
> plane) to `agent/kb/_frames/<clip>/`; `propose` sends them + the MEASURED facts to
> **`gpt-5.5-2026-04-23`**, which proposes `action_id` + the per-channel 5-tuple + descriptions, gated by
> `validate_semantic_consistency` (with a self-correction retry); `accept` gates `action_id` (slug +
> uniqueness) and promotes `candidate/<clip>.json → <action_id>.json`. **All 8 accepted actions were
> re-proposed with gpt-5.5** (replacing claude-opus-4-8) — and gpt-5.5 named actions by FUNCTION from the
> frames (`nurse_give_meds → giving_pills`, `nurse_grab_aspirin → grab_bottle`). The prior Claude proposal is
> preserved at `agent/kb/_authored_claude_backup/` (do not use / do not delete). **MEASURED was
> untouched (golden 8/8)**; the MEASURE half is now keyed by **`clip_name`** (`_raw/<clip>.json`,
> `candidate/<clip>.json`); the full gate is green. `key.env` (the `OPENAI_API_KEY`) is git-ignored
> (`_frames/` WAS too — un-ignored 2026-08-05, see the note below). Operator runbook: §8.3; decision
> record: the 2026-06-25 update in [ADR 0008](docs/adr/0008-vlm-proposed-authored-fields.md).

> **✓ MotionKB moved out of `Assets/` into `agent/` (2026-08-05):** `Assets/MotionKB/` → **`agent/kb/`**,
> `Tools/motionkb/` + the three validator scripts → **`agent/motionkb/`**; `Tools/` no longer exists. The KB
> is produced by Python and consumed by Python — nothing about it is a Unity artifact, and keeping it under
> `Assets/` made the engine-decoupling claim aspirational rather than literal. **The agent half now runs with
> no Unity installed.** Verified movable *before* moving: `MotionKBValidator.cs` reads the JSON with
> `System.IO` (`Directory.GetFiles`/`File.ReadAllText`) and touches `AssetDatabase` only to resolve
> `source_clip.guid`, which points at a clip in `Assets/Animations/` and is unaffected; and all **96 guids**
> of the former `Assets/MotionKB` assets were grepped against every `.unity`/`.prefab`/`.asset`/`.mat`/
> `.controller` — **0 references**, the KB was pure data in Unity's eyes. Landed as two commits: a pure
> `git mv` (92 renames at R100, so `git log --follow` still traces each file's history), then the path and
> doc updates. **What changed mechanically:** the 96 now-meaningless `.meta` were deleted (content outside
> `Assets/` is not a Unity asset), and three scripts needed a *deeper* `REPO_ROOT` — `validate_motionkb.py`,
> `test_golden_extraction.py` and `gen_kb_manifest.py` sat at `Tools/<script>.py` (one `dirname` to the root)
> and now sit at `agent/motionkb/<script>.py` (two); `extract.py`/`propose.py` were already two levels deep
> so their root calculation was unchanged. `Assets/Editor/MotionKB/MotionKBValidator.cs` **stays in Unity** —
> it is the one layer of the contract that genuinely needs the engine — with `KB_DIR` repointed to `agent/kb`
> (relative to Unity's cwd, which is the project root). Gate green after the move: validate 8/8, golden 8/8,
> manifest in sync, Unity resolves 8/8 guids.

> **✓ Local git made a trustworthy rollback net + `_frames/` tracked (2026-08-05):** `git status` had been
> useless — **2541 files reported permanently modified with no real content change**, from TWO independent
> causes. (1) **git-lfs filter vs history mismatch**: `.gitattributes` (the Unity template, added in the
> initial commit) declares `filter=lfs` for `*.png` `*.fbx` etc., but git-lfs was not active when those
> assets were first committed, so they went in as plain blobs; every `status` then ran the clean filter,
> got a ~130-byte pointer, and compared it against the stored raw blob → 752 files always `M`. (2) **eol
> normalization**: `.meta`/`.mat`/`.anim`/`.unity` were committed with CRLF before the template's `eol=lf`
> took effect → ~1789 more. Fix: `git lfs migrate import --everything --yes` over an include list
> **derived from `git check-attr` rather than hand-written** (hand-writing missed `*.Fbx` 65 MB, `*.3DS`,
> `*.ttf`, `*.pdf`), then `git add --renormalize .`. Result: **status 2541 → 0**, 752 files in LFS,
> `.git` 6.4 GB → 3.1 GB, both `kb/v1`/`kb/v2` tags rewritten and preserved, KB validate 8/8 + golden 8/8
> + `check_motionkb.sh` green, Unity resolves migrated assets (console clean). **Traps hit, for whoever
> repeats this:** `git lfs migrate --fixup` reports NOTHING here because `.gitattributes` uses the macro
> form (`*.png  lfs` + `[attr]lfs …`) and git-lfs's fixup parser does not expand custom macros (real git's
> `check-attr` does — hence the two disagree); `migrate import` refuses to run on a dirty worktree, and the
> dirt IS the bug being fixed, so `--yes` is required (verify first that no text file has a real diff under
> `--ignore-all-space`); and **`migrate import` leaves the worktree full of pointer stubs** — the worktree
> dropped 3.8 GB → 785 MB and Unity showed no error only because its `Library/` import cache masked it, so
> **`git lfs checkout` is a mandatory follow-up step**. Rule going forward: **LFS takes binaries only.**
> `agent/kb/*.json`, `_raw/*.json`, `.meta`, and all Unity YAML (`m_SerializationMode: 2` =
> ForceText) stay in plain git — the whole provenance/audit story depends on being able to read a text diff.
> `*.mesh` (235 MB, incl. the three de-clipped bed meshes) is `%YAML` text and deliberately stays out of LFS.
> **`agent/kb/_frames/` is now TRACKED** (48 PNG via LFS + their `.meta` in plain git): it was ignored
> as a regenerable offline intermediate, but under the Phase-2 architecture the agent reads those frames at
> retrieval time as open-ended visual evidence, and regenerating them needs a live Unity editor + MCP bridge
> — which would make restoring the pure-Python agent side depend on booting the engine.

> **✓ Research statement updated (2026-07-01):** the Phase-2 target is now a **language-driven animation
> assembly framework** — ONE LLM agent inside deterministic scaffolding (multi-agent = a later extension),
> agent side = standalone Python service ↔ engine side = replaceable executor over an explicit data
> contract (no LLM inside Unity; no runtime LLM-generated C#), **hard geometric gates** at
> intent/retrieval/assembly/scene-landing with failure-feedback rollback replanning, and the LLM **never
> outputs motion numerics**. Two runtime paths: full KB match → clip + playback constraints (the real-time
> path); else deterministic assembly (mask compositing / phase alignment / transitions / IK / foot locking /
> contact / collision correction) → bake a new `AnimationClip` (optionally export FBX). **KB decision:** the
> **v2 contract stays as-is** — gate intermediates (foot-contact phase, key poses, transition validity) will
> be DERIVED, regenerable sidecars computed from the frozen `_raw/` dumps when the assembler exists (not a
> schema bump); **gated KB write-back** of assembled motions is a sanctioned FUTURE extension (supersedes
> the flat 2026-06-18 write-back prohibition; provenance stays separate from verified source assets).
> README §1 and §6 below rewritten accordingly.

- **Phase 1 (body-part-level motion knowledge base) is done**: 8 JSON files under `agent/kb/` (schema `motionkb/v2`, `status: accepted`, 9-channel + `ik_goals`; MEASURED program-extracted, SEMANTIC VLM-proposed + consistency-gated, current store `vlm_accepted`). (`typing` was added 2026-06-13 — the first **seated** action; see `agent/kb/typing.json` and the `EmergencyRoom_TypingTest` scene in §3.)
- **The full ER scene is imported and cleaned**: `Assets/Scenes/EmergencyRoom.unity` — visual environment only, with **no** Intent/LLM/Python/XR/director scripts (65 missing-script components were stripped).
- **The patient & bed Animators are ENABLED (live) as of 2026-06-16** — the patient breathes **in place** (no drift; `applyRootMotion` is off) and the bed holds **"Idle Up"** (backrest raised). The serialized / edit-mode pose is the baked **awake/reclining P0** state (responsive — the source's runtime-start; see `SCENARIO.md` §1), which replaced the earlier **P3** (flat, unresponsive) freeze on 2026-06-13. (This state was previously kept *frozen* via disabled Animators; that was reversed at the user's request — see the §0 note below and §2 rule 3.)
- **The scene is scenario-aligned with the source project (2026-06-09, see `SCENARIO.md`)**: all source-active nurses are active again. The acting/IK nurses are `CPRNurse` (Jill), `AirwayNurse` (Kate), `EKGNurse` (Dana) — **`Nurse1` is only a background figure with no IK/action rig; animation work targets Jill/Kate/Dana**. `NurseAnimatorEvents.cs` was ported (prop events now have a receiver), and the missing NavMesh agent type "Nurse" (`-1372625422`) was restored in `ProjectSettings/NavMeshAreas.asset` (verified binding to the baked surface).
- **MotionKB was source-aligned (2026-06-09)**: feet/legs magnitudes re-measured in world space (the old hips-relative metric inflated planted feet under pelvis leans), `check_pulse` feet corrected to static, `giving_pills` feet corrected to dynamic (real adjusting step), `giving_pills.can_overlay_on` narrowed to `["idle"]` (lock-disjointness rule; see §8.3 step 5).
- The console has one **benign** NRE only (the Animator editor window's `UnityEditor.Graphs.Edge.WakeUp`), unrelated to the scene/logic.
- **Phase 2 is under way, and this bullet used to deny it** (corrected 2026-08-19; it had said the service, the protocol and the gates were "all still roadmap", which stopped being true some time ago and directly contradicted the notes above). What exists: the single-agent Python service (`~/Research/animation-agent`), the engine-executor contract (`agent/protocol.py` **v4** ↔ `Protocol.cs`), the runtime executor (`Assets/Scripts/AgentRuntime/`), and the geometric gates — which since 2026-08-19 run **before** execution as well as during it (§0, ADR [0009](docs/adr/0009-check-before-you-play.md)). What does **not** exist: the **bake path** — nothing writes a synthesized `AnimationClip` or an FBX, and that is deliberate until a quality gate can admit one. Staged beyond that: VLM feedback loop, SMPL/SMPL-X representation, Unreal/Blender executors, multi-agent split, gated KB write-back.

---

## 1. Environment & MCP connection

- Unity **6000.3.16f1** + URP **17.3.0**. Open the `Animation/` folder (it has its own `.git` — it is a standalone git repo).
- **MCP for Unity** lets you (the AI agent) drive the editor:
  - Transport is **HTTP** (localhost). `Packages/manifest.json` references the sibling `../unity-mcp-research/MCPForUnity` (the C# plugin) via `file:`; the Python server is in `../unity-mcp-research/Server/`.
  - Before using it, read the `mcpforunity://instances` resource to get the instance: name = `Animation`, but the **hash changes every session**. Then call `set_active_instance` with the full `Animation@<hash>` (when multiple instances are connected you must select one first, or it errors).
  - **Invoke the `unity-mcp-skill` skill first** — it has the schemas, usage, and best practices for all the MCP tools.
- **`execute_code` syntax constraints (codedom, C# 6, method-body context):**
  - **Do NOT write `using` directives** (namespaces are pre-imported); use fully-qualified names when needed.
  - `Object` is ambiguous → use `UnityEngine.Object.DestroyImmediate(...)`.
  - No top-level statements / class definitions; write a method body directly; you may `return` a string as the result.
  - After editing scripts, use `read_console` to confirm compilation before using new types.

### 1.1 Windows / WSL — where to run what

Three repositories live on two filesystems, and every real bug of 2026-08-18 came from work crossing
between them rather than from either side on its own. The split is deliberate: what the engine owns is
on Windows because Unity is a Windows application here, and everything engine-independent is on Linux.

| what | where | whose git |
|---|---|---|
| the Unity project, including `agent/kb/` | `F:\...\Project\Animation` | **Windows git** |
| `animation-agent` — pipeline, agent, runtime service | `~/Research/animation-agent` on WSL ext4 | Linux git |
| `pub-code` — the publish mirror, branch `code` of `github.com/ZeyangZheng08/Animation` | `~/Research/pub-code` on WSL ext4 | Linux git, and the only one with a remote |

**Default to WSL bash.** Not because the commands are better but because there is one quoting model
instead of three (PowerShell 5.1 has no `&&`, `Set-Content` defaults to ANSI, Git Bash is POSIX but its
git is the Windows one). Use the Windows side only for what is bound to the engine: the Unity editor
over MCP, and Windows git on the Unity worktree.

**Five boundary rules. Each was learned by breaking it.**

1. **Never run WSL git inside `/mnt/f`.** Measured: it calls 812 files dirty at the same moment Windows
   git reports 0, because autocrlf and the file mode bits differ across DrvFs. From WSL, drive the Unity
   repo through `git.exe -C "F:/..."` — a Windows path, not `/mnt/f`, which that binary cannot resolve.
2. **Read Unity-side file content from the object store, not the working tree.** `git show HEAD:path`
   returns the blob, already normalised to LF and binary-safe. The Windows worktree is checked out CRLF,
   so a byte comparison against an LF consumer calls every text file different, copies it needlessly and
   puts CRLF back — the same phantom-`M` that once made `git status` stop working as the drift detector.
3. **Never copy a worktree across `/mnt/c` or `/mnt/f`.** DrvFs reports every file as mode 755, so the
   copy arrives with a mode change on every file. Clone instead.
4. **Select files by extension whitelist, never by subtracting `git lfs ls-files`.** One FBX under
   `Assets/Animations` has a Unicode apostrophe in its name; `git ls-files` quotes that path and
   `git lfs ls-files` does not, so the difference of the two lists smuggles it through as a text file.
   `git lfs ls-files -n` is the only safe listing — the default output also truncates paths at spaces.
5. **Test list membership with `case`, not `grep -q` down a pipe.** Under `pipefail` the early exit of
   `grep -q` hands the writing end a SIGPIPE, so the pipeline reports failure even though the pattern
   matched. It is a race, so it misfires on only some entries and looks like a real finding.

**The measured cost of the one crossing that remains.** The agent reads the KB over 9p via
`MOTIONKB_DIR=/mnt/f/.../agent/kb`. Same suite, same KB, only the filesystem differs:

| suite excluding `tests/test_tools_files.py` | KB on `/mnt/f` | KB copied to ext4 |
|---|---|---|
| before 2026-08-18 | 19.5 s | 10.6 s |
| after | **12.6 s** | 9.9 s |

`tests/test_tools_files.py` adds 13.1 s on top and is not comparable — it walks the Unity tree by
design, so it skips against a copied KB.

**How that gap was found, because two plausible answers were wrong.** It is not `KBIndex.load()` (0.27 s,
five module-scoped calls) and it is not the content hash in `raw_fingerprint` (memoising it changed
nothing measurable). Instrumenting `os.stat` / `open` / `os.listdir` gave the real shape: **225 stats and
112 opens of `_raw` per run, 6.4 s of the 9**, at about 19 ms per stat and 9 ms per open over DrvFs. Guess
twice, then measure — profile by leaf cost, not by reading the code and reasoning about it.

The fix is not caching by checking, it is **caching because the write discipline already guarantees it**:
`_raw` has exactly one writer, `unity_sampler.write_raw`, which now calls `transitions.forget_raw()`.
Every other process treats the KB as read-only, so within one of them `_raw` cannot move, and the readers
stop proving it — `raw_fingerprint`, `load_clip` and both `read_table`s memoise the default corpus with
no filesystem call on a hit. Pass `raw_dir` or `path` explicitly, as the builders and the tests do, and
the read is verified against size and mtime or not cached at all.

**The live path never had this problem** — every `KBIndex.load()` call site is a process entry point, so
the console loads once at startup and never per request. And do not "fix" the residual 2.8 s by moving
the KB: it is a derivative of the Unity animation assets and ships in the same commit as the FBX it was
measured from.

---

## 2. Conventions & boundaries (don't break these)

1. **Language**: see the top. The plan MD (`.claude/plans/…starfish.md`) is Chinese, but code is all English.
2. **Do NOT bring runtime systems from the source project**: the Intent recognition, LLM, Python server, EdgeAI, PatientChat, TTS/Voice, XR rig, and director scripts (`DemoController`, `InitialStateDirector` — which are **entirely commented out** in the source anyway) from `VR4Nursing_v2 - Copy (7)` must **not** be brought over. They are tangled with the intent system, depend on XR/Newtonsoft, and would break compilation. The user explicitly said: "bring the patient and bed placement, but NOT the intent recognition / python server".
3. **Patient & bed Animators are ENABLED / live (changed 2026-06-16 — this reverses the earlier "keep frozen" rule).** The user wants the patient and bed animated. Both Animators are now `enabled=true`: in Play the patient plays **"Idle Awake" + "Breathing" in place** and the bed holds **"Idle Up"** (backrest raised). There is **no drift** — `applyRootMotion` is already `false` and the patient clip loops in-place (`keepOriginalPositionY=true`); verified the patient `worldPos` is unchanged across Play. The baked reclining transforms are still serialized, so **edit-mode shows the correct P0 pose** (edit mode doesn't tick Animators). **Verified by an A/B test (2026-06-16):** a script recompile / domain reload does **NOT** snap the patient to T-pose — because the pose lives in the *serialized* bone transforms, which a domain reload preserves, and an idle enabled Animator doesn't override them in edit mode. (The source `VR4Nursing_v2` shows a bare T-pose in edit only because it never baked/saved a pose.) So **no editor hook is needed** for this — an `EditModeAnimatorPosePreviewer` re-pose hook was tried and then **removed as unnecessary**. `static == runtime` now holds only for the **first** Play frame (then the patient breathes). Don't re-disable them without the user's say-so. (Historical: these were originally kept *disabled* to freeze the backdrop — see §5 for the original baking procedure.)
4. **MotionKB is the canonical store**: the future Python RAG consumes these JSON directly. When changing an action's semantics, change the JSON — don't just change things in Unity.

---

## 3. Completed work (with critical files)

| Module | Key files / location | Notes |
|---|---|---|
| Body-part knowledge base (v1, **retired**) | git tag `kb/v1` + `schema/motionkb.v1.schema.json` (snapshot contract) | 8 actions, schema `motionkb/v1`, 6-part BodyPartFact + `composability` (incl. `posture`; `idle`/`walking`/`typing` are bases, `typing` is the first **seated** one). **Retired at the 2026-06-24 candidate→accepted promotion** — the root `agent/kb/*.json` are v2 now (next row). |
| MotionKB **v2 (ACCEPTED)** | root `agent/kb/*.json` (schema `motionkb/v2`, `status: accepted`) · `agent/motionkb/` (Python extractor) · `engine_mask_map.json` · [ADR 0007](docs/adr/0007-v2-body-part-split.md) · [ADR 0008](docs/adr/0008-vlm-proposed-authored-fields.md) · `docs/specs/motionkb-v2-spec.md` | 9-channel split + orthogonal `ik_goals`; MEASURED filled + validated 8/8; SEMANTIC 5-tuple **filled + verified 8/8** via the VLM-proposal loop (ADR 0008), `composability` authored, `target` deferred to Phase-2. **Promoted candidate→accepted 2026-06-24** (v1 retired to tag `kb/v1`); `candidate/` now the empty staging area (see §0). |
| **Runtime executor** (Phase 2, engine half) | `Assets/Scripts/AgentRuntime/` (15 components) · [ADR 0009](docs/adr/0009-check-before-you-play.md) | `AgentLink` (the one WebSocket, agent is the server) · `Protocol.cs` **v4**, mirrored second from `agent/protocol.py` · `SceneRegistry`/`SceneQueryService` (typed scene predicates; still answer `scene.find`/`describe`/`anchors`/`position`, which are engine-internal API now — the model's surface is `scene_search` + `scene_query`) · `MotionComposer`+`ClipLibrary` (masked layers, fractional weights, per-layer clip windows) · `PoseSynth`+`PostureTransitionEvaluator` (generated sit/stand, closed loop on measured hip height) · `IkBinder` · `Locomotion` (walking, and `Preview` for a route that is computed rather than walked) · `GateEvaluator`+`GateArming`+`GateProbe`+`ValidationCharacter` (**one judgement, two clocks** — a commit is played through on a hidden duplicate first). No LLM and no generated C# on this side. |
| Agent service (Phase 2, decoupled half) | `~/Research/animation-agent` (WSL, its own repo) | Retrieval over the KB, the deterministic channel partition and seam schedule, the ReAct loop, the 12 declared tools, `agent/protocol.py` as the contract authority. Acceptance: `pytest` (281), `run_eval.py` (floor 7/12), `smoke_validate.py` against play mode. See §1.1 for which side to run git and python on. |
| Typing test (2026-06-13, revised) | `Assets/Scenes/EmergencyRoom_TypingTest.unity` + `Assets/Scripts/TypingTestDriver.cs` | Copy of `EmergencyRoom`; on Play the typing nurse walks → standing Idle → **seated Typing** at the computer, reusing the `NurseAnimator` `Walk_N→Idle→Typing` state machine (driver feeds `Speed` from the NavMeshAgent, fires the `typing` trigger once arrived+Idle). **Hand IK (faithful to the source):** owned by the reused `NurseIKHelper`, which snaps the two `TwoBoneIKConstraint` (`L_Hand`/`R_Hand`) onto the shared `laptop` IK points and ramps their weights. In the source these are fired by `LaptopIK`/`ResetHandsIK` **animation events** baked into the Typing clip — but **those imported-clip events do NOT dispatch in this standalone scene** (verified: the constraint weight stays 0 through the whole ~16 s clip, no "missing receiver" warning, and `AlwaysAnimate` doesn't help — not a culling issue). So `TypingTestDriver` calls the SAME `NurseIKHelper.LaptopIK(speed)` / `ResetHandsIK(speed)` API itself on Typing enter/exit and leaves `NurseIKHelper` **enabled** — this **replaced the earlier bypass** that disabled `NurseIKHelper` and drove the constraints directly in `Update`. `TypingTestDriver.ikRampSpeed` serialized to `0.1`. **Avatar mismatch = root cause of palms-up hands:** the shared laptop IK-point world rotations are hand-authored for the **`nurse_avatar.fbx`** skeleton, and `NurseIKHelper.SetIKPoints` copies BOTH position AND rotation onto the wrist with `targetRotationWeight=1`. `Nurse1` is a **`FemaleScientists.fbx`** instance whose hand-bone bind-pose axis differs (~180° forearm roll), so the identical world rotation flips the palm UP — position lands exactly on the keys, only the wrist roll is wrong (proven from the scene: the constraint Tip/Mid/Root resolve to the FemaleScientists prefab GUID; everything else — point rotations, weights, `NurseIKHelper` code — is identical to the working source). **Fix chosen (user): use `nurse_avatar`** — added **`TypingNurse_Avatar`** (a duplicate of the `CPRNurse` nurse_avatar nurse, plus `TypingTestDriver`), which walks → idles → types with correct **palms-down** hands. In this scene **only `TypingNurse_Avatar` + `patient_avatar` are active**; `Nurse1` (FemaleScientists, palms-up, otherwise unchanged) and all other nurse avatars are `SetActive(false)`. Re-author the two `laptop` point rotations for FemaleScientist, or set `targetRotationWeight=0`, if that avatar must type instead. **Gotcha:** setting `constraint.weight` from `execute_code` does NOT engage the rig (wrong phase) — it must come from a MonoBehaviour `Update` (or `NurseIKHelper`'s own ramp). |
| ER scene | `Assets/Scenes/EmergencyRoom.unity` | full visual environment; patient **awake/reclining (P0)** on the angled bed, patient+bed Animators **live (2026-06-16)**; all source-active nurses active — acting/IK nurses are Jill/Kate/Dana (CPRNurse/AirwayNurse/EKGNurse); Nurse1 is background-only |
| IK demo | `Assets/Scenes/NurseAnimTest.unity` | RigBuilder + TwoBoneIKConstraint hand IK |
| IK scripts | `Assets/Scripts/IK/NurseIKHelper.cs` (261 lines), `AnimatorIkHelper.cs` (~13 lines, trimmed 2026-06-16) | rig wiring + IK target groups; the latter is the `StateMachineBehaviour` referenced by the controller |
| Nurse animation | `Assets/Animations/NurseAnimation/` | nurse_avatar.fbx (shared Humanoid avatar) + clip sources + `NurseAnimator.controller` + masks |
| Animation Rigging | `Packages/com.unity.animation.rigging/` (1.4.1) | **embedded package**, do not touch |
| Cross-session memory | `C:\Users\10411\.claude\projects\f--…\memory\` | `MEMORY.md` index + `motion-kb-bodypart-research.md` |
| Original plan | `C:\Users\10411\.claude\plans\…starfish.md` | the full Chinese Phase-1 plan |

---

## 4. Pitfalls / lessons learned (must read)

1. **`HumanPoseHandler.GetHumanPose/SetHumanPose` is unreliable for this lying patient**: the patient's lying orientation is baked into the bone rotations AND the root transform is rotated 270° about Y, so the Get/Set round-trip does **not** preserve placement — it rotates/translates the whole skeleton. **To re-pose the avatar locally, use "direct bone aiming"**: for each bone compute `curDir = (child.position - bone.position).normalized`, then `bone.rotation = Quaternion.FromToRotation(curDir, targetDir) * bone.rotation` (children follow rigidly; the torso/hips can't move). That's exactly how the arms-on-bed fix was done.
2. **`clip.SampleAnimation` overrides a humanoid clip's root transform**: set `transform.position/rotation` **after** sampling, or it has no effect.
3. **Muscle clips must be sampled in-engine**: the clips are Humanoid muscle curves (no transform paths), so reading the `.anim` text is useless. Use `UnityEditor.AnimationMode` + `AnimationMode.SampleAnimationClip(go, clip, t)`, wrapped in `StartAnimationMode/StopAnimationMode`.
4. **To detect clipping, measure the MESH, not the bones**: the real cause of the repeated "clipping" was the heavyset patient's **body mesh** sinking into the rigid mattress (bones were above the surface, but the flesh of the back dipped to 0.384 vs the ~0.51 mattress top). Bone checks miss it — measure the mesh's lowest Y via `SkinnedMeshRenderer.BakeMesh`.
5. **Screenshot angles**: a straight top-down shot (camera y≳3.2) gets blocked into solid white by the **room ceiling**. Use a high angle at y≤2.35, or temporarily hide the ceiling/nurses. MCP `manage_camera` with `view_position`/`view_target` for positioned screenshots is handy; `include_image=true` shows it inline.
6. **Animation Rigging uses an embedded package**: a registry install hits EPERM on Windows (a locked PackageCache rename). The resolved `com.unity.animation.rigging@…` was copied into `Packages/` as an embedded package. Don't switch it back to registry.
7. **There's a patch in `CharacterCustomize.cs` — don't revert it**: in `Assets/3D Assets/Avatars/FemaleScientist/Scripts/CharacterCustomize.cs`, the `OnValidate` defers its `charCustomize()` call via `UnityEditor.EditorApplication.delayCall` (`#if UNITY_EDITOR`) to avoid "SendMessage during OnValidate" errors.
8. **Benign NRE**: the `UnityEditor.Graphs.Edge.WakeUp` → `Graph.OnEnable` NullReferenceException in the console is an internal quirk of the Animator editor window — **unrelated to the scene / your changes**. Ignore it.
9. **Negative-scale collider**: a negative-scale BoxCollider was fixed on import (`bed_nav_box`, y=-0.074 → flipped positive). Flip any similar ones to positive absolute values.
10. **Nurses were re-activated on 2026-06-09** to match the source scene: `CPRNurse / AirwayNurse / EKGNurse / Nurse3 / FemaleScientists_PRF_URP` are `SetActive(true)` again (only `Nurse7Agent`'s AirwayNurse copy stays inactive, as in the source). The acting nurses with full IK/animation rigs are CPRNurse/AirwayNurse/EKGNurse — not Nurse1.
11. **Orphan animation events (faithful to source)**: `patientCPR` on `nurse_cpr_long` and `lowerBed` on `nurse_drop_bed` have no receiver in either project — playing those clips logs "AnimationEvent has no receiver"; harmless.
12. **Stray `pill_bottle` renderer on `hand_r` (magenta — found & removed 2026-06-13)**: `CPRNurse`'s right-hand **bone** `hand_r` had a `MeshFilter`+`MeshRenderer` (mesh `pill_bottle`, **null material** → Unity draws it magenta) added directly onto the bone. It is **not** in `nurse_avatar.fbx` (the model's `hand_r` carries no renderer), **not** in the source `VR4Nursing_v2` scene, and **not** referenced by `NurseAnimatorEvents` (the real held bottle is the bone's inactive `pill_bottle` child = the `medicineBottle` ref; the ambubag is `held_ambubag`). It was inherited by the `TypingNurse_Avatar` duplicate; only `CPRNurse` had it (EKGNurse/AirwayNurse/Nurse3 did not). Both were cleaned — the `MeshFilter`+`MeshRenderer` removed from the bone — and the scene now has **0 null-material renderers**. Lesson: a skeleton bone should carry no renderer, and a **null material renders magenta** (not always a shader/URP problem; check for an unmanaged mesh on a bone first).
13. **A protocol bump has THREE speakers, and the third does not import the first.**
    `agent/protocol.py` is the authority, `Assets/Scripts/AgentRuntime/Protocol.cs` mirrors it, and
    `terminal.py` — standard-library-only, so no import — had the number written in as a literal. The
    v3 → v4 bump updated two of them and every line typed into the Play-mode console was then dropped
    as malformed. **Grep for the constant, not for the import.** Both remaining copies now derive it,
    but the lesson generalises: any file that cannot import the contract will hold a copy of it.
14. **A refusal nobody can see is the same as being ignored.** The same bug survived because
    `ConsoleServer` logged the dropped line and moved on — correct about the session, wrong about the
    person, who saw a fresh prompt. When adding a channel, decide where its errors surface before
    deciding they are fatal.
15. **Guessing cost three sessions; the task dump answered it on first use.** A detached service now
    logs to `_traces/service.log`, `kill -USR1 <pid>` dumps every thread (works when blocked in a
    syscall), `kill -USR2 <pid>` dumps every asyncio task and what it awaits. "No `run_turn` task at
    all" is what identified it — the turn had never been created, which looks from outside exactly
    like a turn stuck on a model response and is nothing like it.

---

## 5. Patient pose (current = P0 awake; how it was set, for reproduction / tweaking)

**Baked P0 pose (set 2026-06-13; how it was done, for reproduction)** — reproduces `../VR4Nursing_v2/` runtime-start (NOTE: Animators are now **live**, see §0/§2 — this is the original baking procedure):
- `DEMO/Patient/patient_avatar`: world `(-1.94, 0.769, -3.072)`, `rotY=-90` (=270°), scale `0.9`, **local pos `(0,0,0)`** under the `DEMO/Patient` container (container local `(-6.658773, 1.3346183, -4.1605606)`, identity rot).
- `hospital_bed`: world `(-1.167, 0, -3.071)`, `rotY=90`.
- **How it was baked:** sample the base-layer **"Idle Awake"** clip (`patient_idle_angled`) onto the patient skeleton @ frame 0, and the bed **"Idle Up"** clip @ frame 0, via `UnityEditor.AnimationMode.SampleAnimationClip` (persist the resulting local transforms); set the root transforms above; then **disable both Animators**. (⚠ As of **2026-06-16** the Animators are **re-enabled / live** — see §0 and §2 rule 3; this section documents the *original freeze* procedure, kept for reproduction.) The `mattress` (a SkinnedMeshRenderer) follows the bed `Idle Up` backrest — head end ≈ 0.87 m, foot ≈ 0.47 m.
- **No clipping:** the body rests on the mattress/`Cover` (closest underside contact ≈ 3 mm; nothing below the mattress underside). Measure via `SkinnedMeshRenderer.BakeMesh` against the **combined mattress + `Cover`** surface — the sparse 214-vert `mattress` mesh alone gives misleading heightfield reads near its sloped edges, so include the dense `Cover` (≈15.7k verts).
- After any change, **verify in Play mode** that root / min-max-Y / centroid match the editor (this exact match held while the Animators were disabled; now that they're **enabled**, the patient breathes, so only the **first** Play frame matches the editor pose), and `read_console` for errors.

**Historical — the earlier P3 (flat, unresponsive) pose** (backed up in `EmergencyRoom.P3backup.unity`); reuse if you re-pose the patient flat at CPR/BVM action time: `patient_avatar` root ≈ `(-2.0, 0.76, -3.07)`, `rotY=270`, body raised +0.13 so the back rests on the (then-flat) mattress, both arms laid flat via the direct-bone-aiming method in §4 (final hands ≈ left `(-1.0, 0.56, -3.44)`, right `(-1.03, 0.57, -2.66)`).

---

## 6. Next steps / research roadmap

Phase 1 (understanding + visualization + the MotionKB) is done. The research statement was **updated
2026-07-01** (see the §0 note): the target is a **language-driven animation assembly framework** — a single
LLM agent inside deterministic scaffolding, an engine-decoupled Python service talking to a replaceable
engine executor over an explicit data contract, hard geometric gates with rollback replanning, and all
motion numerics from real assets or real-trajectory-constrained deterministic solvers — never from the LLM.
Build order (by dependency; confirm priority with the user):

1. **Agent-side Python service (single agent + deterministic scaffolding)**: consume `agent/kb/*.json`
   (top-level scalars + tags for coarse retrieval; each channel `motion_description` is independently
   embeddable for part-level queries). NL intent + current scene state → semantic understanding, action
   decomposition, retrieval selection, symbolic assembly planning; on a gate failure the scaffold feeds the
   reason back and rolls back to the corresponding upstream stage.
2. **Deterministic assembly + bake (code, not the model)**: full KB match → return the clip + its playback
   constraints (the real-time path). Otherwise assemble retrieved REAL body-part segments on the unified
   skeleton + timeline — mask compositing, phase alignment, transition generation, IK, foot locking, contact
   constraints, collision correction — then **bake a new `AnimationClip`** (optionally export FBX). Gate
   intermediates (foot-contact phase, key poses, transition validity) are DERIVED sidecars computed from the
   frozen `_raw/` dumps when this assembler exists — **the v2 KB contract stays as-is (decided 2026-07-01)**.
3. **Engine-side executor (fixed protocol, replaceable)**: provides the scene graph, character skeleton,
   object poses, collision state, IK execution results, and animation preview; consumes the structured
   assembly data. Scene understanding = deterministic enumeration/caching/state-sync over the scene graph
   first; a multimodal model assists only for candidate-object filtering, affordance judgment, semantic
   disambiguation, and visual feedback verification.
4. **Hard geometric gates (the scaffold's enforcement layer)**: intent (target reachability, orientation,
   scene compatibility) · retrieval (part-segment composability, transition validity) · assembly
   (acceleration discontinuity, foot skate, penetration, end-effector error) · scene landing (world-space
   geometry between character and objects/ground/environment).
   **Status (2026-08-19), because the list above is the target and not a claim:** retrieval-stage checks
   are real and run agent-side with no round trip (posture, channel partition, contested grips). Assembly
   and scene-landing checks are real, live in `GateEvaluator`, and **run before execution** on a hidden
   duplicate as well as during it — contact hold, contact reached, ground penetration, support landing,
   hip travel, descent saturation. Foot skate is measured and deliberately **not** judged (no calibrated
   threshold). Not built: acceleration discontinuity, and any body-versus-scene collision metric. The
   intent stage has no gate of its own — reachability is decided by the walk and by the executor's own
   refusals. Rollback replanning is one hop: a refusal names the metric and which of four things to
   change, and the model re-plans in the same turn.
5. **Staged extensions (not current work)**: multi-agent split (intent / retrieval / assembly planning /
   visual verification / scene landing as specialist agents); Unreal/Blender executors; a VLM semantic
   feedback loop; SMPL/SMPL-X unified representation; optional pre-bake full-body coordination refinement;
   **gated KB write-back** of successfully assembled motions (only through the geometric gates; provenance
   kept separate from the verified source assets).
6. **Grow the MotionKB**: extract more actions offline via the §8.3 runbook (next targets: `button`, `grab`,
   `call`, `lowerBed`, the patient/bed clip families — see `SCENARIO.md` §6).

> Note: `LLMR_Derived_Decoupled_Animation_Generation/` (a sibling) was re-copied **2026-06-16** and is now a
> **working decoupled LLM→animation prototype** (no longer the empty template a prior survey saw): Unity exports
> the rig facts only it knows accurately (armature hierarchy + valid joint paths + object frame) → a local
> **Python FastAPI server** (`Assets/python_server/app.py`) fills an "LLMR" metaprompt and calls **OpenAI**
> (`gpt-5.4-mini`) → the LLM returns animation-as-text (`joint/path,(t,x,y,z,w),…`) → Unity parses it back into
> an `AnimationClip` (`Assets/.../MR_Copilot/AnimationClipFromCSV.cs`).
> **Reusable plumbing for roadmap steps 1–3:** the rig/clip batch exporters
> (`python_server/unity_hierarchy_exporter.py` + `unity_animation_clip_exporter.py`, which drive Editor
> `HierarchySnapshotBatchExporter.cs` / `AnimationClipBatchExporter.cs` in `-batchmode`), the FastAPI HTTP
> skeleton, the Unity↔Python bridge (`MR_Copilot/Orchestration/PythonAnimationClient.cs` + `PythonServerLifecycle.cs`),
> the joint-path normaliser (`app.py::postprocess_joint_names`), and the text→clip parser for **landing clips into Unity**.
> **Gaps (all net-new for Phase 2):** no RAG / motion KB, no body-part decomposition or composition/blending, no SMPL.
> **Caveat on direction:** it is **LLM-_generates_-motion** — the opposite of this project's **retrieval-first** thesis
> (numerics from retrieval, not generation). Reuse its plumbing (exporters, HTTP bridge, text-clip format), **not** its
> generation model. Provider is OpenAI; a Phase-2 service here would default to Claude.

---

## 7. Acceptance / self-check checklist

- `read_console` is error-free (ignore the benign NRE from §4 item 8); the controller has no missing-script and the avatar has no missing materials.
- The MotionKB validates — `python agent/motionkb/validate_motionkb.py` checks the 8 **v2 accepted root files** (`agent/kb/*.json`, `status: accepted`; its dir-glob skips `engine_mask_map.json`) against `motionkb.v2.schema.json` + the cross-field invariants (locks/free partition of the 8 anatomical channels, overlay lock-disjointness, posture compatibility, ik effector→channel resolution, channel-vocabulary agreement with `engine_mask_map.json`) + the **semantic-consistency gate** (`validate_semantic_consistency`); 8/8 pass. Re-run reproducibility is guarded by `python agent/motionkb/test_golden_extraction.py` (golden MEASURED reproduces from the frozen `_raw`, 8/8), and `source_clip.guid → asset` resolution by the Unity-side `MotionKB.MotionKBValidator` (8 resolved → `_reports/kb_state.md`); one-command gate `scripts/check_motionkb.sh` runs them all. The former v1 store is retired to git tag `kb/v1` (`motionkb.v1.schema.json` kept as its snapshot contract). See §8.
- In `EmergencyRoom.unity`, the patient is at the **awake/reclining P0** pose on the angled bed with no clipping; its Animator is now **live** (breathes in Play, in place — no drift), and edit-mode shows the baked P0 pose; the nurse roster matches the source scene (see `SCENARIO.md` §2).
- The runtime path holds — run these in the agent repo (WSL; see §1.1): `pytest` green, `python run_eval.py` still at the 7/12 floor, and `python smoke_validate.py` against play mode, which is the only check that exercises the real executor. **If you changed `PROTOCOL_VERSION`, also type a line into the Play-mode console** — `smoke_validate.py` and `drive.py` both import the contract, so neither can catch a speaker that has the number written into it (§4 item 13). The last is the one that matters after any change to `Assets/Scripts/AgentRuntime/` or `agent/protocol.py`: **both halves ship together and a version mismatch is fatal by design**, so a rebuilt Unity side and an unchanged service will refuse to talk rather than half-speak.
- After changing an avatar pose / asset, **save the scene**, and prefer committing in the `Animation/` git repo (only commit when the user asks).

---

## 8. Auditing the MotionKB + engineering design principles

The MotionKB has a machine-checkable contract. Don't re-document the field semantics here — they
live in `agent/kb/README.md`; the authoritative shape is
`agent/kb/schema/motionkb.v2.schema.json` (v1 kept only as the `kb/v1` snapshot contract).

- **Validate (no Unity needed):** `python agent/motionkb/validate_motionkb.py` — validates the 8 **v2 accepted root files**
  (`agent/kb/*.json`; `collect_files()` prefers `candidate/` then falls back to root, and `candidate/` is
  empty since the 2026-06-24 promotion) against `motionkb.v2.schema.json` + the cross-field invariants JSON
  Schema can't express (`locks`/`free` partition of the **8 anatomical channels**, overlay
  lock-disjointness, posture compatibility, ik effector→channel resolution, channel-vocab agreement with
  `engine_mask_map.json`) with per-file failure isolation. `guid → asset` resolution is the Unity-only
  layer, now landed as `Assets/Editor/MotionKB/MotionKBValidator.cs` (8 resolved → `_reports/kb_state.md`).
  One-command gate `scripts/check_motionkb.sh` runs all live checks (schema/invariants/semantic-consistency
  + golden re-extraction regression + manifest-in-sync; the guid→asset step is Unity-side, result committed
  in `kb_state.md`).
- **Why it's built this way:** see `docs/adr/` (0001 contract-first · 0002 measured/semantic split ·
  0003 skeletal split + metrics · 0004 mask+layer disjoint-only · 0005 git-as-version/ledger ·
  0006 peak-resilience-by-design · **0007 v2 9-channel split + engine-decoupled Python extractor**,
  supersedes 0003 · 0008 VLM-proposed SEMANTIC fields · **0009 a plan is checked on a hidden duplicate
  before the visible character moves, protocol v4**).
- **Rolling back** a KB version / extraction / mesh / pose: `docs/ROLLBACK.md`.

Status: the data contract (v1 **+ v2** schema + `validate_motionkb.py` + ADRs incl. **0007** + CHANGELOG)
is landed and self-verifies (8/8 v2 accepted root files pass; a deliberately-broken file is caught). The
**extractor LANDED in Python** (`agent/motionkb/`, ADR 0007) — bone-map/metric as DATA, measured/semantic
split, run-log — **replacing the originally-planned C# Editor script** (the C# file names in §8.2 are
superseded; read them as "their Python equivalents"). These are now **all landed (2026-06-24)**: the
Unity-only `MotionKBValidator` (`guid → asset`, 8 resolved → `_reports/kb_state.md`), the golden
re-extraction regression (`agent/motionkb/test_golden_extraction.py`, pure Python over the frozen `_raw`), the
`kb_manifest.json` index (`agent/motionkb/gen_kb_manifest.py`), and the `retrieval_eval_set.json` seed. The **SEMANTIC pass** on the v2 candidates' 5-tuple is
**DONE** (the VLM-proposal loop, [ADR 0008](docs/adr/0008-vlm-proposed-authored-fields.md): first
human-accepted 2026-06-24; re-proposed on the 2026-07-01 full-pipeline re-run and auto-accepted as
`vlm_accepted` — human `author` review is optional since 2026-06-25, so the current store has
`verified_against_screenshots=false`); `composability` is VLM-proposed + program-derived (locks/free/seam_owner). The **candidate→accepted store promotion is DONE (2026-06-24)** — v2
is now the root accepted store (`status: accepted`), v1 retired to tag `kb/v1`. The only KB work left is the
still-open Unity-only items below.

### 8.1 Engineering design principles (load-bearing — keep these in mind)

> These are the software-engineering decisions that shape this repo. There is deliberately **no separate
> engineering doc** — internalize the principles below instead of treating them as one more file to read.
> The user calls this out as important SWE design thinking; honor it on every piece of work that lands.

1. **Follow the spirit, not the form.** This is a single-author, never-pushed, offline-bake repo (a handful
   of local commits). Do NOT transplant production-SRE machinery (real-time monitoring/alerting, QPS
   load-testing, traffic-percentage canary routing, immutable audit ledger, artifact registry, content-hash
   anti-tamper). Take what each one *solves* — "where did this number come from", "can bad data get in", "can
   we roll back", "can someone else pick this up" — and meet it with the lightest means that fits this repo.
2. **Stage it.** Every mechanism worth doing is tagged **Do-now** (tiny cost, fixes a real current pain) ·
   **Phase-2 enabler** (seed only the contract/field/annotation now) · **Phase-2 build** (land when the
   consumer/producer actually exists). Online-traffic ops are **out of scope entirely** — not deferred.
3. **Offline-bake NFR reframe.** The NFR here is NOT throughput (QPS) — it is **bake correctness + clip
   quality + reproducibility**. "Capacity planning" collapses to a **per-stage latency budget** (retrieve →
   assemble → bake → land); the canary "cohort" is a fixed offline **eval set** (no traffic to slice);
   "rollback" reverts the *consumed truth* (KB data + landed clip), not a running process.
4. **Peak-resilience = graceful degradation BY DESIGN, not QPS.** "能抗峰值" = a single offline job under a
   burst of queued bakes / a pathological input (over-long clip, a query decomposed into too many body-part
   subtasks, a huge retrieval set) / an asset-load memory spike stays **bounded, no single bad item aborts the
   batch, crash-resumable, degrades to a fallback**. Achieved cheaply via bounded concurrency (in-process
   `Semaphore(N)`) + backpressure + per-stage timeout + fallback-to-cached + resumable/atomic batch + per-file
   isolation. NOT load-testing / autoscaling / capacity planning — there is no concurrent traffic, so
   throughput is a meaningless question for this project.
5. **git IS the version / ledger / rollback** in a single-author repo. `git status`/`git diff` is the drift
   detector; the **commit message is the decision record** (e.g. "promote cpr candidate->accepted, eval pass");
   `git tag kb/<ver>` + `git checkout kb/<ver> -- agent/kb/` is rollback. Do NOT build a parallel
   per-entry `content_sha256` store-and-reconcile or a `promotion_log.jsonl` ledger — that re-implements git.
   Compute a hash on demand inside the validator only if some out-of-band gate ever needs it.
6. **measured vs semantic split.** The extractor rewrites **MEASURED** fields only (magnitudes, duration,
   frame_rate, loop, root motion, the static/dynamic threshold). **SEMANTIC** fields — `motion_description`,
   `overall_intent`, `tags`, `composability`, `target`/`interaction_object`/`faces_target`, `ik_goal`,
   `mask_coverage`, `controller_*` — are human-written + screenshot-verified and must NEVER be clobbered by a
   script. This is what stops "grow the KB" from re-introducing the LLM-positioning/description unreliability
   the whole project is positioned against (see ADR 0002).

The eight qualities these serve map to eight modules (one line each; this replaces the deleted blueprint):
**A. Data Contract** (`motionkb.v2.schema.json` + invariant validator + readable KB-state report) ·
**B. MotionKBExtractor** (saved Editor script; bone-map/metric as DATA; measured/semantic split + run-log) ·
**C. KB Versioning** (`kb_manifest.json` identity/provenance + candidate/accepted channel + git tag + CHANGELOG) ·
**D. Provenance/Audit** (richer `extraction` block: git SHA, formula version, raw measurements, real timestamp, field_origin) ·
**E. Regression & Eval** (C# golden re-extraction test [now] + `retrieval_eval_set.json` annotations [seed]) ·
**F. Bake Observability** (Phase-2 per-stage trace + latency budget + `AssemblyDescription`/`baked_clip` example contracts + `fallback_bake_id`/cache) ·
**G. Handoff & CI** (README as single source of truth + runbook + ADR + local check script) ·
**H. Robustness & Peak Resilience** (resumable+atomic+per-file-isolated+bounded batch [now]; bounded fan-out, backpressure, timeout→fallback, degradation policy [Phase-2 ADR]).

### 8.2 Staged roadmap (compact)

> **Update — v2 landed in Python (2026-06-18, ADR 0007).** The extractor architecture changed from the
> C# Editor scripts planned below to a **pure Python** program (`agent/motionkb/` — `config.py` =
> bone-map/divisors/thresholds as DATA, `metrics.py` = formulas, `extract.py` = assembly + semantic-
> preserving merge + run-log, `unity_sampler.py` = the generic pose-sampler run over Unity MCP). This
> LANDS the substance of: **[B]** (extractor with bone-map/metric as data + measured/semantic split +
> run-log), **[A]**'s no-Unity layer (`validate_motionkb.py`, now v2), **[D]** (the enriched `extraction`
> block — `extractor_version`/`metric_formula_version`/`bone_map_version`, real `extracted_at`,
> `field_origin`, per-channel `raw_measurement`), **[C-partial]** (the `candidate/` channel + per-entry
> `status`), and **[H]**'s per-file isolation + end-of-run summary + run-log. The C# file names below
> (`MotionKBExtractor.cs`, `BodyPartBoneMap.cs`, `MotionMetricConfig.cs`) are **superseded** — read them as
> "the Python equivalents". **Now landed (2026-06-24):** **[A]** the Unity-side `MotionKBValidator.cs`
> (`guid → asset`, 8 resolved → `_reports/kb_state.md`), **[E-now]** the golden re-extraction regression
> (`agent/motionkb/test_golden_extraction.py`), the `kb_manifest.json` index of **[C]** (`agent/motionkb/gen_kb_manifest.py`),
> and the **[E-seed]** `retrieval_eval_set.json` (the `git tag kb/v1` + `kb/v2` anchors exist). **The KB gating item — the human authoring
> pass on the v2 candidates' SEMANTIC 5-tuple — is now DONE** (the VLM-proposal loop, ADR 0008, 2026-06-24:
> proposed-from-renders + consistency-check-gated + human-accepted, 8/8 verified). **The candidate→accepted
> store promotion is also DONE (2026-06-24): v2 is the root accepted store, v1 retired to tag `kb/v1`.**

**Do-now** (small cost, high value; all gated on the Unity MCP connection except where noted):
- **[A]** `motionkb.v1.schema.json` (landed; **superseded by `motionkb.v2.schema.json`** — v1 is the `kb/v1` snapshot contract) + `MotionKBValidator.cs` (schema + invariants + guid resolution
  + a readable `_reports/kb_state.md`) + headless wrapper.
- **[B]** `MotionKBExtractor.cs` — rescue the throwaway extraction snippet into a checked-in Editor script; the
  6-part bone-map and per-part metric divisors become named code constants (`BodyPartBoneMap.cs`,
  `MotionMetricConfig.cs`); writes MEASURED only, emits a run-log.
- **[D]** Enrich the `extraction` block: `extractor_git_sha`, `metric_formula_version`, `raw_measurements`, a
  real timestamp (the current `…T00:00:00Z` are fake placeholders), `field_origin`.
- **[E-now]** Golden re-extraction regression — **DONE** as `agent/motionkb/test_golden_extraction.py` (pure Python, no
  Unity: re-runs `metrics.channel_blocks` over the frozen `_raw` dumps and asserts MEASURED reproduces the
  accepted store; 8/8). (The original "C# Editor test" framing predates the Python extractor — superseded.)
- **[C-partial]** candidate/accepted channel + per-entry `status` (re-extract writes `candidate/`, never
  overwrites accepted) + `git tag kb/<ver>` + `CHANGELOG.md`.
- **[G]** README in-place: an "add a new action" checklist + fix the file_id gotcha (rig-specific, not a
  universal constant); `scripts/check_motionkb.sh` runs schema+invariants now (landed); key ADRs.
- **[H]** Make the A/B batch paths per-file-isolated + end-of-run summary (non-zero exit), checkpoint/resume via
  the run-log (no CLI flag), atomic write + candidate-only; resilience constants fold into
  `MotionMetricConfig.cs`; ADR 0006 already records "peak-resilience by design, no QPS/autoscaling".

**Phase-2 enabler** (seed the contract/field/annotation now; bears weight only in Phase 2):
- **[C]** rest of `kb_manifest.json` (identity + provenance index, NO content_sha256) + `kb_version`.
- **[E-seed]** `retrieval_eval_set.json` (minimal annotations, data only).
- **[F-example]** `assembly_description.example.json` + `baked_clip.example.json` + `bake_trace.example.json`
  + a `cache/` dir — hand-written examples + prose field lists, do NOT freeze schemas for producers that
  don't exist yet.

**Phase-2 build** (land when the dependency/producer exists — normal sequencing, not "maybe"):
- **[E]** `run_eval.py` — waits for the Python retriever (no system-under-test before it).
- **[F]** the bake observability emitter (`BakeRunLogger`/`BakeLander`) + `bake_quality_report` gate — waits for
  the pipeline to produce runs; freeze the `*.example.json` into `*.schema.json` only after the first real agent
  emits the shape.
- **[C/F]** baked-clip canary (sandbox land + geometric metrics gate — an offline eval-gate, NOT traffic routing).
- **[H]** pipeline-level peak resilience (`BoundedConcurrency` = one in-process `Semaphore(N)`/bounded `Channel`
  doing both agent fan-out limiting and bake-request backpressure; `StageTimeout` reusing F's `stage_latency_ms`
  budget; `DegradationPolicy` = fallback-to-cached / best-full-clip / skip-bad-action).

**Out of scope — not in any backlog, NOT "deferred":** real-time monitoring/alerting/dashboards, QPS
load-testing, query-level canary routing / traffic splitting, auto-rollback orchestration, per-entry
`content_sha256` reconciliation, a `promotion_log.jsonl` ledger. These are multi-user online-service ops; a
single-user offline-bake project, by its locked nature, won't become that. git already covers change-detection
and decision-recording; the per-stage latency budget already covers the one offline timing signal that matters.

### 8.3 Adding a new MotionKB action (operational runbook)

> Moved here from `agent/kb/README.md` (2026-06-24) — that README is now the human-facing
> overview; this is the agent/operator runbook (doc-audience convention: README = humans, HANDOFF = agent).

The extractor is `agent/motionkb/` (`config.py` channels/bone-map/divisors/thresholds · `metrics.py`
formulas · `extract.py` orchestration with the `register|resolve-controller|emit-sampler|sample|assemble|render|propose|author`
subcommands · `unity_sampler.py` the generic sampler + the render generator + the stdlib HTTP-bridge client ·
`vlm_openai.py` the gpt-5.5 vision client · `propose.py` the proposal loop). The MEASURE half keys its
working files by **`clip_name`** (`_raw/<clip>.json`, `candidate/<clip>.json`); the `action_id` is decided in
the SEMANTIC half (VLM-proposed at `propose`) and the file is renamed to `<action_id>.json` at promotion
(auto on `propose`, or via the optional human `author` pass).

1. **Register the source clip** — `python agent/motionkb/extract.py register <clip_name>` finds the clip BY
   NAME in Unity (scans `Assets/Animations`, both standalone `.anim` and FBX-embedded sub-clips via
   `unity_sampler.build_find_clip_csharp`), **auto-resolves its `guid` + `file_id`**, and scaffolds
   `candidate/<clip_name>.json` with `source_clip` filled + a blank v2 skeleton (channels, all-free
   composability, status `candidate`, `action_id` null). It then **best-effort resolves `controller_state`/
   `controller_layer`/`trigger_param`** from the AnimatorController the clip is wired into (see step 1b); No
   manual file_id lookup, no manual controller lookup.
   - **Why this matters: `file_id` is rig/importer-specific, NOT a universal constant** — the 5 nurse overlay
     FBX share `1827226128182048838`, a standalone `.anim` uses `7400000`, `X Bot@Typing.fbx` uses
     `-203655887218126122`. `register` reads the clip's actual `file_id` so you never hand-copy it. If two
     clips share the name it lists every match (path|guid|file_id) and refuses to guess — disambiguate by
     renaming the clip or writing the stub by hand.
   - **Nothing else is filled by hand.** `composability` and every meaning-level label come from the AUTHOR
     half (step 6, VLM-proposed / program-derived); `controller_*` is resolved in step 1b.
     (`_source_files()` unions `candidate/` with the root store, so the new clip flows through the next steps.)
   1b. **Resolve controller wiring** — `register` already attempts it; re-run explicitly with
     `python agent/motionkb/extract.py resolve-controller <clip_name>` after you wire the clip into a
     controller. `unity_sampler.build_resolve_controller_csharp` scans every `AnimatorController` under
     `Assets/Animations` via the typed `UnityEditor.Animations` API: it finds the state whose motion (directly
     or inside a BlendTree) is the clip, and reads `controller_state` = state name, `controller_layer` = layer
     name, `trigger_param` = the parameter on a transition INTO that state (the layer's **default/resting state
     gets `trigger_param: null`** — it's entered by default, not by an activating trigger; this is why `idle`
     resolves to `null` not `Speed`). **Not wired → all three left `null` (blank), by design** (schema makes
     `controller_state`/`controller_layer` nullable). Ambiguous (>1 distinct wiring) → reported, left unchanged.
     Verified live: reproduces all 8 stored `controller_*` exactly.
2. **Emit the sampler** — `python agent/motionkb/extract.py emit-sampler` writes
   `agent/motionkb/_generated_sampler.cs` (a generic pose-sampler built from `config.py`, no KB knowledge).
3. **Sample in-engine (the only Unity touch)** — `python agent/motionkb/extract.py sample` drives it: with
   the editor open and the MCP server on HTTP (port 8080), it POSTs the generated C# to the bridge
   (`/api/command`, `execute_code`, `safety_checks:false` since the sampler writes files) and writes
   per-frame root-local bone positions to `agent/kb/_raw/<id>.json`. `unity_sampler.run_csharp_over_http`
   is the stdlib client; `--host`/`--port`/`--instance` override the target. (Or run the C# by hand via any
   MCP `execute_code` client — the "caller is transport" path still works.) Re-sampling is deterministic:
   verified byte-identical `_raw` and golden 8/8 on a fresh run.
4. **Assemble** — `python agent/motionkb/extract.py assemble` computes the 9-channel MEASURED blocks, writes
   `agent/kb/candidate/<id>.json` (MEASURED authoritative; SEMANTIC read-merged if present, else
   migrated from v1 and flagged PENDING), and emits `_reports/extract_run.md`. Per-file isolated, atomic write.
5. **Render frames** — `python agent/motionkb/extract.py render <clip>` renders multi-angle frames (avatar on
   an isolated layer + a ground plane, so the VLM reads ground contact) to `agent/kb/_frames/<clip>/`,
   kept for human review. `unity_sampler.build_render_csharp` is the generator.
6. **Propose (VLM proposes + program derives + auto-keeps)** — `python agent/motionkb/extract.py propose <clip>`
   sends those frames + the MEASURED facts + the existing base-action list to `gpt-5.5-2026-04-23`
   (`vlm_openai.MODEL`). It proposes `action_id` + display_name + overall_intent + tags + `mask_coverage` + the
   per-channel 5-tuple + descriptions **and the composability judgement calls** (`base_or_overlay`, `posture`,
   `can_overlay_on`). The program then **derives `composability.locks`/`free`** from the proposed roles
   (`free ⟺ role==free` — the exact relation the gate enforces, so it reproduces the existing 8 by construction)
   `+ seam_owner` (fixed `{torso:base, root:base}` convention). It runs `validate_semantic_consistency` **plus a
   composability gate** (`propose._composability_errors`: `can_overlay_on` names known bases, lock-disjoint,
   posture-compatible; base ⇒ empty) with a self-correction retry loop. The program NEVER writes MEASURED
   (ADR 0002); the per-channel `target` stays null; `controller_*` untouched. **`ik_goals` is DERIVED** from
   the proposed object contacts (a hand/foot with `contact=object:<obj>` + `constraint∈{must-reach,must-maintain}`),
   its `target` left null (the scene anchor is engine-specific → Phase-2 grounding). Needs `OPENAI_API_KEY` in `key.env`
   (git-ignored). **By default it then AUTO-PROMOTES (`_promote_candidate(human=False)`) to `<action_id>.json`
   with provenance `vlm_accepted`** (no human required — ADR 0008 human gate is now opt-in). `--stage` holds it
   in `candidate/` instead. `mask_coverage` is VLM-proposed, NOT derived from locks (a base/idle pose drives the
   whole body yet locks nothing — they diverge).
7. **Author (OPTIONAL human review)** — `python agent/motionkb/extract.py author <clip|all>` (or `propose --stage`
   first, then this): gates `action_id` (slug + uniqueness), flips `vlm_proposed → semantic`, sets
   `verified_against_screenshots=true` + `verified_by/at`, marks `vlm_proposal.status = human_accepted`, and
   promotes `candidate/<clip>.json → <action_id>.json`. Skipping it leaves the VLM output standing as
   `vlm_accepted` (auditable in `extraction.vlm_proposal.status` / `field_origin.vlm_proposed`).
8. **Validate & record** — `python agent/motionkb/validate_motionkb.py`, then `python agent/motionkb/gen_kb_manifest.py` to
   refresh `kb_manifest.json` (its provenance includes the model + `vlm_proposal_status`); `git tag kb/<ver>`,
   update `schema/CHANGELOG.md`, commit (the commit message is the record; ADR 0005).

The per-channel metric formulas / divisors / thresholds are the ADR 0007 metric table, mirrored as DATA in
`agent/motionkb/config.py` (`DIVISOR`, `STATIC`) + `metrics.py`. `validate_motionkb.collect_files()` prefers
`candidate/*.json` and falls back to the root store once promoted.

> **2026-06-25 — existing 8 re-proposed with gpt-5.5.** All 8 accepted actions were re-proposed via this
> `render → propose → author` loop using `gpt-5.5-2026-04-23` (replacing the prior `claude-opus-4-8` proposal;
> the Claude version is preserved at `agent/kb/_authored_claude_backup/` — do not use / do not delete).
> MEASURED was untouched (golden 8/8); the full gate is green. gpt-5.5 proposed the *functional* action_ids
> from the frames (e.g. clip `nurse_give_meds` → `giving_pills`, `nurse_grab_aspirin` → `grab_bottle`).

> **2026-06-25 — the last two "manual" inputs were automated (this update).** Previously the runbook said
> `controller_*` and `composability` were hand-entered SEMANTIC decisions and `accept` was a mandatory human
> gate. Now: (1) **`controller_*` is RESOLVED by program** — new `resolve-controller` subcommand +
> `build_resolve_controller_csharp` typed-API lookup; `register` calls it best-effort; unwired clips stay
> blank (schema made `controller_state`/`controller_layer` nullable); verified live to reproduce all 8.
> (2) **`composability` is VLM-proposed + program-derived in `propose`** — `locks`/`free` derived from the
> proposed roles (reproduces the 8 by construction), `seam_owner` a constant, `base_or_overlay`/`posture`/
> `can_overlay_on` proposed and gated (`_composability_errors`); `mask_coverage` VLM-proposed too. (3) **`accept`
> is now OPTIONAL** — `propose` auto-keeps as `vlm_accepted` by default (`--stage` to hold; `accept` upgrades to
> `human_accepted`). New schema status `vlm_accepted`; provenance split into `field_origin.vlm_proposed` /
> `derived` / `resolved`. The existing 8 accepted files were NOT re-derived (their hand-authored/VLM-proposed
> composability + controller stand); this only changes how NEW actions are produced. Gate green: validate 8/8,
> golden 8/8 (MEASURED untouched). See the 2026-06-25 update in [ADR 0008](docs/adr/0008-vlm-proposed-authored-fields.md).
