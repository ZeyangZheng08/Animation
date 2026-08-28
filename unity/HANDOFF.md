# HANDOFF — for the next coding agent

> You're picking up a **language-driven, retrieval-first animation assembly framework** (a single LLM agent in deterministic scaffolding; agent side = Python service, engine side = replaceable executor — engine-decoupled; multi-agent is a later extension). All current work lives in the `Animation/` Unity project. Spend 5 minutes on this file and you'll avoid every pitfall already hit.
>
> **Language convention (important):** communicate with the user and write plans/docs in **Chinese**; but all **code, comments, JSON content, field names, file names, and identifiers** must be **English**. This is a hard requirement from the user. (These handoff docs are in English by the user's request.)

---

## 0. TL;DR — current state

> **✓ THE SEMANTIC PASS IS DONE — THE WHOLE CORPUS IS DESCRIBED (2026-08-27).** All 2446 `mx_` records
> now carry `action_description` and all eight `channels.*.motion_description`, written by
> **`qwen3.8-27b`** served locally on the HPC cluster, reading each clip's eight-view frame ring.
> `extraction.field_origin` moved `semantic_pending → semantic`, and every record gained an
> `extraction.vlm_proposal` block naming the model, the frame count (24, or 16 for the 128 single-frame
> pose assets) and the eight ring views. The corpus is now retrievable by meaning, not only by
> measurement.
>
> - **Describing is not accepting.** All 2446 stay `status: candidate` under their `mx_` filenames,
>   `vlm_proposal.status: awaiting_human_accept`, `action_id` still null on every one. The describer no
>   longer names; naming happens at acceptance. Nothing was renamed and nothing was promoted.
> - **No kinematic value moved.** A key tally over the whole 2446-file diff turns up only
>   `action_description`, `motion_description`, the `field_origin` lists and the new `vlm_proposal`
>   block. `state_label`, `motion_magnitude`, `raw_measurement`, `mean_pose`, `mean_body_height` and
>   `mean_body_tilt_deg` never appear on a changed line.
> - **Getting the results home is a PULL, not a push.** HPC wants a password plus a Duo prompt on every
>   connection and no key may be installed, so an `rsync` aimed back at the workstation has nothing to
>   authenticate with. It runs from WSL instead, over a ControlMaster the user opens by hand, filtered
>   `--include='mx_*.json' --exclude='*'` so the eight accepted nursing records on the Windows side
>   cannot be clobbered by a corpus that never held them. Written up in the agent repo's
>   `HPC_HANDOFF.md` §7, along with the arrival checks.
> - **Gates:** validate **2454/2454**, golden **8/8**, manifest in sync, guid **8 resolved / 0 failed**
>   (from the last committed report — the bridge was down). `check_kb.sh` green on all four.

> **✓ THE DESCRIBER'S PROMPT IS REWRITTEN FOR THE CORPUS PASS (2026-08-27).** The frames are all on
> disk, so the next thing 2446 clips meet is the prompt, and the shipped one was written for eight
> curated nursing actions on a hosted reasoning model. Three changes, in the agent repo
> (`propose.py`, both `vlm_*.py`, `extract.py`, `tests/test_propose_prompt.py`). **No record was
> touched and no number moved** — this decides what the next 2446 descriptions are written from, not
> what the eight existing ones say.
>
> - **The kinematic block in the prompt is one sentence: which parts move.** The rule is to hand the
>   model only what the pictures cannot establish. `mean_pose` is a vector in normalised muscle space
>   whose origin is a coordinate centre and not a rest pose (ADR 0021), so no describer reads a lean
>   off `Spine Front-Back = -0.36` while the eight views show that lean directly; carriage is visible
>   the same way, since a figure lying down looks like one from every angle in the ring.
>   `motion_magnitude` as a number needs a paragraph of anchors before it is readable at all, and buys
>   back a phrase the model would only paraphrase. What three sampled moments genuinely cannot
>   separate is a hand held still from a hand that trembles — and `state_label` is the field a
>   consumer reads NEXT TO the sentence, so a description contradicting it is a defect in the record.
>   That one fact stays; about 70 numbers a prompt went. It also keeps the description independent
>   evidence rather than a restatement of the measurement sitting beside it.
> - **The reply is nine labelled lines, not JSON.** The corpus pass is planned on a local ~27B model,
>   and a model that size asked for nested JSON fails in specific ways — a ```json fence, a missing
>   channel key, a trailing comma, the `{...}` placeholder copied verbatim — each of which costs the
>   whole record. `label: sentence` parses with one `partition`, and a line the model skipped is an
>   absent key, which is the shape `validate_descriptions` already reports. `response_format:
>   json_object` and both clients' `_extract_json` are gone with it; `vlm.propose` is now
>   `vlm.describe` and returns the reply text.
> - **The describer no longer names.** `action_id` left the prompt: dozens of corpus clips are walk
>   variants that would collide on one, and a record is keyed by `clip_name` until acceptance. So
>   `propose` auto-accepts a record that already HAS a name and holds an unnamed one at `candidate`,
>   which is where all 2446 corpus records stay.
> - **Two things the new tests caught.** A model that bolds its label writes `**action:** ...`, which
>   left `**` at the head of the sentence once the colon was partitioned away. And the prompt used to
>   open by tracing the ring — *front, then turning toward the figure's own right* — which was
>   narration over a manifest that already labels every frame with its own angle, AND a claim about a
>   sort order the module could not enforce: sorting was the caller's, so a caller passing frames in
>   any other order got a prompt that lied with nothing to catch it. The sentence is deleted and
>   `build_prompt` now returns `(frames_in_ring_order, prompt)`, so the order it wants is the order it
>   produces. Use the returned list to attach the images.
> - **Gates:** agent-repo suite **370/370** (358 before, +12 in `tests/test_propose_prompt.py`), KB
>   untouched so validate / golden / manifest / guid are unchanged at 2454 / 8 / in-sync / 8.

> **✓ THE WHOLE CORPUS IS RENDERED (2026-08-27).** All 2446 `mx_` clips now have their eight-view ring
> on disk under `agent/animation_knowledge_base/frames/<clip_name>/`, keyed exactly as the eight
> nursing actions' are, so the semantic pass has its visual evidence. The batch verb is
> **`ingest_corpus.py render`**: it takes its population from the corpus index the way `sample` does,
> does the per-clip work through `extract.render_frames` — the SAME body the curated `extract.py
> render` runs, so bulk and curated cannot drift into two dialects of what a frame set is — and is
> resumable on the frame count, so an interrupted run continues instead of redoing.
>
> - **245.9 min, ~5.7 s a clip, 3.50 GB.** Not one clip failed; nothing needed a retry.
> - **A full ring is not always 24 images.** 128 corpus clips are Mixamo *pose* assets one frame long
>   that sample at `SAMPLE_MIN = 2`, so `select_fracs` has only two moments to return and their ring
>   is 8 × 2 = **16**. `_expected_frames` sizes the ring from the clip's length and frame rate (the
>   same clamp the sampler applies) rather than assuming 24 — the first run assumed it, called all
>   128 failures for "wrote 16/24", and would have re-rendered them on every resume forever. Final
>   counts: **2318 dirs of 24, 128 of 16, 0 PNG**.
> - **Corpus frames are UNTRACKED**, by the same rule as the corpus dumps and the corpus FBX:
>   regenerable offline, and 3.5 GB on a repository that already cannot be pushed. `.gitignore` takes
>   `/agent/animation_knowledge_base/frames/mx_*/`; the eight nursing directories (192 files) stay
>   **tracked** and are byte-identical. `pub-code`'s `.pubignore` excludes them from the mirror too.
> - **Gates:** agent-repo suite **358/358**, Unity-repo `git status` clean.

> **✓ EVERY CLIP IS NOW SHOT FROM ALL EIGHT SIDES (2026-08-26).** `render` used to pick TWO camera
> angles out of four named ones, from the clip's own kinematic labels — side + 3/4 for a locomotion
> clip, front + 3/4 for a manipulation act. Two angles is a bet on which axis the action reads along,
> and the bet is what fails: whatever one view hides has no second view to contradict it, so "the hand
> is in front of the hip" and "the hand is behind it" arrive as the same picture. The selector is
> deleted. `unity_sampler.view_ring` returns eight directions 45° apart around the avatar's OWN
> frame — `front, front_right, right, back_right, back, back_left, left, front_left`, turning toward
> the figure's own right — built from the raw dump's `root_fwd`, all at one shared slight look-down
> (`VIEW_ELEVATION = 0.20`). The TIMES are unchanged (`select_fracs`, three pose-coverage moments), so
> a clip is 8 × 3 = **24 frames**.
>
> - **It costs less than the two views did.** Frames are **JPEG q85** now, not PNG (`EncodeToJPG`),
>   at the same 1024×1024 with the same supersampling, MSAA, three-light rig and checkered ground
>   plane: **~60 KB a frame against ~270 KB**. 24 pictures are **1.42 MB per clip where 6 were
>   1.62 MB**, and the eight re-rendered clips total **11.9 MB where they were 16 MB**. A lit figure
>   on a lit checkered floor has no flat colour for PNG to win on; inspected at every azimuth, nothing
>   a describer reads off a frame — separated fingers, foot-to-floor contact, the cast shadow, the
>   floor squares — is touched.
> - **24 images do not go in one response with any margin**, so `render_clip_frames` splits the ring
>   into calls of at most `IMAGES_PER_CALL = 12` (two per clip). Framing is computed from the three
>   TIMES, which every call shares, so the split changes nothing about the pictures — same distance,
>   same centre, same apparent size. The 8 MB ceiling holds with 4× margin instead of 2×.
> - **Not rewritten: `extraction.vlm_proposal.render_views` on the existing records.** It records what
>   the VLM actually saw when it proposed, which was two views. Only a future `propose` run writes
>   eight. Nothing else in a record mentions the camera.
> - The propose prompt now says the frames are one set of moments seen from eight sides and to use the
>   far side to settle what a near one hides, and the frames are attached in RING order — sorted by
>   name the eight interleave alphabetically (back, back_left, back_right, front, …), which puts
>   neighbouring angles at opposite ends of a list the model is told to read in order.
> - **Gates:** agent-repo suite **358/358**, validate **2454/2454**, golden **8/8**, guid resolution
>   **8/8**; 192 frames re-rendered across the eight actions, 0 PNGs left under `frames/`.

> **✓ THE KNOWLEDGE BASE DESCRIBES; THE AGENT DECIDES (2026-08-26).** A record now answers two
> questions and no others: what does this action LOOK like (`action_description`, renamed from
> `overall_intent`) and how does each body part MOVE (`channels.*.motion_description` + the kinematic
> block). Deleted outright: `display_name`, `tags`, `mask_coverage`, `ik_goals`, the whole
> `composability` block, and the per-channel `role` / `motion_type` / `contact` / `constraint` /
> `target`. Schema `motionkb/v3` → **`motionkb/v4`**, extractor 3.1.0 → **4.0.0**, formula **unchanged
> at v3.0.0 — no number moved anywhere**. See ADR
> [0022](docs/adr/0022-the-kb-describes-the-agent-decides.md).
>
> - **Why, in one line.** Every deleted field was a claim about a COMBINATION written onto a
>   description of one clip. `walking`'s arms are `stabilizer` — free for a carry to override — which
>   is right for "walk while carrying" and wrong for "walk swinging your arms", and the clip is
>   identical in both. `contact: object:keyboard` is a fact about the room. `ik_goals[].target` was
>   `null` on all eight records because the anchor is scene-specific: two thirds of a decision with
>   the deciding third permanently missing. `can_overlay_on` is a pre-enumerated interaction template,
>   which is the thing this project's claim rejects — and `assemble.py`, `kbindex.py` and
>   `tools/kb.py` each already carried a paragraph explaining why they refused to read it. A contract
>   three consumers agree in writing to ignore is not a contract.
> - **The partition moved into the plan.** `plan_motion`'s `overlays` are now
>   `{action_id, channels[]}` and there is an optional `base_channels`; anything nobody names is
>   `free_channels`, and the base still plays layer 0 full-body underneath. A channel two parts name
>   is mixed **half each** — v3's 0.6/0.4 came from normalising `ROLE_PRIORITY`, which is gone, and
>   half is the honest reading of "the agent asked for both here". The model still emits no numeric:
>   every leaf in the plan schema is a string or a boolean, and the structural test still holds.
>   A channel the plan PINS to a scene object (`carry` / `ik_bindings` / gaze) may not be mixed — that
>   is a named refusal, and it is the one thing ADR 0004's geometry argument still buys.
> - **`role` cannot be reconstructed, and that was checked, not assumed.** `walking.left_arm` is
>   stabilizer at magnitude 0.165, `cpr.left_arm` support at 0.114, `typing.left_arm` primary at
>   0.125 — not even monotone. Argmax-magnitude, the natural substitute, gets 3 of 8 channels wrong on
>   `dc-walk-carry`, all three the same shape: a channel that moves and that nobody should claim.
> - **`posture` survives as a MEASUREMENT, not a label.** `kbindex.posture_of` bins the root's
>   `mean_body_height` at 0.75 — seated reads 0.647, the lowest standing (`bvm`) 0.859. Deliberately
>   not `mean_body_tilt_deg`: that is forward lean, and `cpr` (44.6°) and `bvm` (39.3°) lean harder
>   than the seated clip (8.7°). The engine needs it, so it stays on every plan step.
> - **Two capabilities changed hands rather than disappearing.** The facing rule ("she sits facing the
>   desk, not the chair") read `channels.*.contact` and now reads what the plan binds a hand to,
>   falling back to `gaze_at`. Two-handed grounding (`typing` at 0.000 m on the laptop) now fires when
>   ONE hand is bound to an object whose registry entry declares `two_handed_anchors` — the object
>   says where both hands go, which is the same rule sourced from the side that knows.
>   `dropped_grips` is gone with the thing it reported: a hand holds what the plan says, once.
> - **Unity C# needed no code change.** Only `AgentRuntimeSetup.cs` opens anything in the KB, and it
>   reads `manifest.json` for `status` / `source_clip` / `action_id`. `MotionComposer` and
>   `GateEvaluator` consume the PLAN, not records. Four comments were reworded.
> - **The whole-store diff reconciles exactly**: 6 changed lines per candidate record
>   (`schema_version`, `action_description`, `extractor_version`, `extracted_at`, two `field_origin`
>   entries) and 7 per accepted one (plus the narrowed `vlm_proposal.scope`) — 14732 added, 206395
>   deleted, 2446×84 + 931 on the delete side. Every kinematic field verified equal record by record
>   against the pre-migration store; the eight records' descriptions byte-identical, and no VLM
>   proposal re-run.
> - **Gates:** validate 2454/2454 against `motionkb.v4.schema.json`, golden 8/8, manifest in sync,
>   guid resolution 8/8, agent-repo suite **350/350** (349 before; 12 tests deleted with the machinery
>   they tested, 13 added or rewritten), zero occurrences of any deleted key across `actions/`,
>   retrieval eval baseline **7/12 — unchanged**, which was measured by rebuilding the v3 document
>   over the v3 records out of git rather than inferred. **Tagged `kb/v4`**; `kb/v3` is the one step
>   back and is a different contract, so `docs/ROLLBACK.md` says what rolling past v4 costs.
> - **Four bugs the rewrite exposed, all in `agent/tools/scene.py` and all now fixed.** `plan_motion`
>   raised `NameError` on every successful plan (a leftover read of the deleted
>   `_ground_declared_hands` result); the actionable refusal for an overlay with no channels was
>   unreachable because normalisation ran outside the `try`; `_binding_due` had silently dropped the
>   generated-descent offset, so a hand binding on a walk-then-sit engaged mid-descent; and
>   `_pair_bound_hands` searched the scene with an `{"id": …}` predicate the engine does not have,
>   which falls through its blank-clause guard as NO filter and returns the whole registry — so the
>   two-handed pairing never fired at all.
> - **Migration path**: `python extract.py migrate [--dry-run]`, a new verb. It rewrites shape and
>   restamps provenance and reads no pose dump, because v4 moved no number — running the measure half
>   would reprocess 2454 frozen dumps to write back the values they already hold.

> **✓ THE CHANNEL `kind` FIELD IS GONE (2026-08-26).** Every channel block carried a `kind` —
> `fk_part` on six anatomical channels, `hand` on the two hand channels, `root` on the root — and
> not one line of code decided anything by it. `channels` is keyed BY CHANNEL NAME, so the schema
> already dispatches the root's block on the key `"root"`, and `hand` was `left_hand` / `right_hand`
> restated. Its single consumer, the validator's `motion_type=manipulate` nudge, now tests the
> channel name. Removed from the 9 channel blocks of all **2454** records and from both channel
> definitions in `motionkb.v3.schema.json`; with `additionalProperties: false` a leftover `kind` now
> FAILS validation, which is the point. **No number moved:** schema stays **`motionkb/v3`**, formula
> stays **v3.0.0**, extractor 3.0.0 → **3.1.0**. The whole-store diff is 22086 deleted `kind` lines
> plus the two provenance lines per record (`extractor_version`, `extracted_at`) and nothing else.
> `engine_mask_map.json` keeps its own per-channel `kind`: different contract
> (`motionkb-engine-map/v1`), read by people, not by the record schema. Gates: validate 2454/2454,
> golden 8/8, manifest in sync, suite 349/349, retrieval eval 7/12 unchanged. **No new kb tag** —
> `kb/v3` remains the rollback point. See `schema/CHANGELOG.md`.

> **✓ THE STORE STOPPED CLASSIFYING POSES AND STARTED KEEPING THEM (2026-08-25).** The MEASURED half
> is now called **KINEMATIC**, the posture triple is gone, and every channel carries `mean_pose` — the
> per-frame mean of each of its Humanoid muscle degrees of freedom, keyed by the engine's own DOF
> names. Schema `motionkb/v2` → **`motionkb/v3`**, formula v2.5.0 → **v3.0.0**, extractor 2.0.0 →
> **3.0.0**, all 2454 records re-measured from frozen `raw`. See ADR
> [0021](docs/adr/0021-kinematic-facts-not-classifications.md).
>
> - **What was wrong with `posture_label`.** It was a distance from a reference pose, thresholded.
>   The reference moved three times in five days, and the last move (ADR 0020) made
>   `mx_Standing_Idle` read **0.47 displaced** on the left arm while `mx_Boxing_Idle`'s raised guard
>   read **0.29 neutral** — a raised guard sits closer to the centre of the shoulder's range than a
>   relaxed arm does. 31% of labels flipped, and corpus spread fell on every group. The question "is
>   this pose displaced?" has no answer that does not first fix an arbitrary "displaced from what".
> - **And the reduction threw the pose away.** RMS-ing 20 hand DOFs into one number cannot say which
>   fingers are curled. The store held 22086 of those scalars and could not reconstruct one pose.
> - **So it stores the thing the scalar was computed from.** `mx_Arms_Raised`'s left arm is
>   `state: static`, `motion_magnitude: 0.0` — and `Left Shoulder Down-Up: 1.39`. No threshold, no
>   origin, no label. A consumer that wants a distance takes one against whatever reference its own
>   task defines; the store no longer decides that for it.
> - **Unity's muscle 0 survives as the COORDINATE ORIGIN and nothing else** — not a rest pose, not a
>   standard pose, not an idle. `REFERENCE_POSE`, `POSTURE_DIVISOR` and `NEUTRAL` are deleted from
>   `config.py`, `calibrate_posture.py` is deleted, and its two reports move to
>   `agent/motionkb_build/archive/`.
> - **Variation did not move.** `muscle_dof_stddev_rms`, the v2.4.0 divisors, `STATIC_MUSCLE` = 0.02
>   and every `state_label` are **bit-identical across all 2454 records** — verified against a
>   pre-bump snapshot, 0 differing values. `raw/`, `derived/` and the whole SEMANTIC half are
>   untouched.
> - **Two validator checks are gone rather than re-expressed.** Both branched on `posture_label`: the
>   `role=primary but static` nudge, and the static-but-displaced-yet-`free` warning. No replacement
>   distance threshold was invented, because that would put back the arbitrary choice this removes.
>   Every static/dynamic semantic-consistency check stays.
> - **Costs, stated:** `actions/` grows 20.5 MB → 25.9 MB, and the corpus vocabulary a `grep` sees
>   goes from 104 words to 164 (all of them the fixed DOF names, identical in every record; the
>   tripwire in `tests/test_tools_files.py` moved to 200).
> - **Gates:** validate 2454/2454 on `motionkb.v3.schema.json`, golden 8/8, manifest in sync,
>   guid → AnimationClip 8/8 live over the MCP bridge, agent-repo suite 349/349, retrieval eval 7/12
>   — unchanged, since no arm of it read a posture label.
> - **Tagged `kb/v3`** (annotated, like `kb/v1`), and `manifest.json`'s `rollback_tag` points at it.
>   It is the FIRST kb tag holding the KB at `agent/animation_knowledge_base/`: `kb/v1` and `kb/v2`
>   predate ADR 0017's move, so `git checkout kb/v2 -- agent/animation_knowledge_base/` matches
>   nothing and exits 0. Rolling back past `kb/v3` also changes contract, and the pipeline that reads
>   it lives in the other repository — `docs/ROLLBACK.md` says what that costs.

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
>   New `agent/segments.py` + `build_segments.py` → `agent/animation_knowledge_base/derived/segments.json`, a derived sidecar
>   on exactly the terms `derived/transitions.json` already had: `kind: derived`, fingerprinted against
>   `raw`, referenced by no record, **not** a contract change. The accepted 8 JSONs and the schema are
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
>   - **Superseded 2026-08-26 (ADR [0022](docs/adr/0022-the-kb-describes-the-agent-decides.md)):**
>     `dropped_grips` and `ROLE_PRIORITY` are both gone. A v4 record declares no contact, so there is no
>     second grip to lose — a hand holds what the plan binds it to, once — and a channel the plan pins to a
>     scene object is refused by name rather than arbitrated.
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
>   - **Superseded again on 2026-08-26 (ADR [0022](docs/adr/0022-the-kb-describes-the-agent-decides.md)):**
>     `role` and `ROLE_PRIORITY` are deleted, so a contested channel is mixed **half each** rather than
>     0.6/0.4; `dropped_grips` is gone with the `contact` field it reported on; and the partition is no
>     longer derived here at all — it arrives in the plan as `overlays[].channels`. What survives from this
>     entry is the measured primitive below (a masked layer at a fractional weight interpolates) and the
>     rule that a hand is never split.
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
> "engine-independent logic", not "data vs code". The KB cannot be regenerated without Unity (`raw/` is
> in-engine `AnimationMode` sampling, `frames/` in-engine rendering) and grows only when a clip is
> imported here, so adding an action stays **one atomic commit** holding both the FBX and its KB entry.
> The agent side reaches it via `MOTIONKB_DIR` and treats it read-only. **Each repo's git runs natively
> on its own side** — Windows git here, Linux git in WSL for the agent repo. (An earlier version of this
> line said git runs on Windows only, which is wrong. What is forbidden is WSL git reaching across to
> `/mnt/f`: it reports ~812 bogus dirty files.) The agent side writes through `paths.write_*` (UTF-8, no
> BOM, LF, atomic) so nothing it writes
> can reintroduce the CRLF mess fixed earlier the same day. Writing LF is only half of it: `.gitattributes`
> pins `agent/animation_knowledge_base/**/*.json` and `**/*.md` to `text eol=lf`, because this repo sets `core.autocrlf=true` and
> would otherwise check the KB out as CRLF. That corrupts nothing — the clean filter converts back, so
> `git diff` stays empty — but git will not mark such a file clean in its stat cache, so every pipeline run
> left `motionkb_build/reports/kb_state.md` permanently `M`. Verified 2026-08-06: a full `check_kb.sh` now rewrites the
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
>   mid-corpus failure resumable. **Verified byte-identical:** re-sampling `Idle` and re-rendering its
>   frames leaves `git status` clean (6 frames then; 24 since the eight-view ring of 2026-08-26). Pose dumps are written back **verbatim, not re-serialized** — they
>   are one line with no trailing newline, and re-indenting them would turn every re-sample into a
>   65k-line diff and destroy `git status` as the KB's drift detector.
> - **Runtime channel is separate from the MCP bridge and must stay so.** MCP ships C# and is offline-only;
>   the runtime channel is a WebSocket where **the agent is the server and the engine connects in** (the
>   editor drops managed state on every recompile and play-mode toggle, so the reconnecting party must be
>   the engine). It carries typed messages, never code. `runtime/echo_server.py` + `runtime/ws_probe.py`
>   are the skeleton that proved the channel. **The contract has since landed:** `agent/protocol.py` (v4)
>   is the authority, mirrored by `Assets/Scripts/AgentRuntime/Protocol.cs`, and the executor is the
>   components under that same folder (eleven then, fifteen today).
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
> **The column itself is gone as of 2026-08-26** — `composability.base_or_overlay` was deleted with the
> rest of the block (ADR [0022](docs/adr/0022-the-kb-describes-the-agent-decides.md)), and README §7 now
> carries the measured carriage in its place. The "not a Unity Animator layer" half still holds.

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
> channel's idle noise floor). The 8 v2 files are in `agent/animation_knowledge_base/candidate/` (schema `motionkb/v2`),
> with **MEASURED numerics filled + validated 8/8** — `python validate_motionkb.py` now targets
> `candidate/` against `motionkb.v2.schema.json` (run-log: `agent/motionkb_build/reports/extract_run.md`).
> **PENDING AS OF 2026-06-18 (since RESOLVED — see the 2026-06-24 ADR 0008 note below):** the SEMANTIC
> 5-tuple (`role/motion_type/contact/constraint/target`) + `composability` need a **human authoring pass**
> (seeded `null`, flagged `extraction.field_origin.semantic_pending`) before candidate→accepted promotion. **Until then the root-level v1 `*.json` remain the accepted store.**
> Decision record = [ADR 0007](docs/adr/0007-v2-body-part-split.md) (supersedes 0003); narrative =
> `docs/specs/motionkb-v2-spec.md`; CHANGELOG = `agent/animation_knowledge_base/schema/CHANGELOG.md`; engine-neutral
> channel vocabulary = `agent/animation_knowledge_base/engine_mask_map.json`. The `git tag kb/v1` rollback anchor that
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
> `python validate_motionkb.py` → 8/8 pass, **0 `vlm_proposed` left**. Per-channel `target` stays
> `null` by design (deferred to the Phase-2 scene-grounding agent). **The KB authoring blocker is cleared;
> the only remaining KB step is the candidate→accepted store promotion that replaces v1.**

> **✓ candidate→accepted PROMOTION DONE — v2 is now the accepted store (2026-06-24):** the 8 v2 files were
> promoted to the accepted store `agent/animation_knowledge_base/actions/*.json` with `status: accepted` (moved out of `candidate/`, which
> is now the empty staging area for future re-extractions); the former v1 6-part store is **retired** but
> preserved at git tag **`kb/v1`** for rollback (ADR 0005 / [docs/ROLLBACK.md](docs/ROLLBACK.md)). The v1
> schema (`schema/motionkb.v1.schema.json`) is kept as the `kb/v1` contract. `python validate_motionkb.py`
> now validates the **root** accepted store (its dir-glob skips `engine_mask_map.json` via `NON_ACTION_FILES`)
> → **8/8 pass, 0 failed**. **MotionKB Phase-1 is COMPLETE** — data contract + Python extractor + MEASURED +
> SEMANTIC 5-tuple + promotion, and (2026-06-24) the do-now/enabler backlog is now **also closed**:
> `agent/motionkb/test_golden_extraction.py` (golden re-extraction regression, 8/8 — MEASURED reproduces from the
> frozen `raw`), `Assets/Editor/MotionKB/MotionKBValidator.cs` (the Unity-only guid→asset layer, 8 resolved →
> `motionkb_build/reports/kb_state.md`), `agent/motionkb/gen_kb_manifest.py` + `agent/animation_knowledge_base/manifest.json` (corpus index, no
> content hashes — ADR 0005), and the `agent/motionkb_build/retrieval_eval_set.json` seed. `scripts/check_motionkb.sh`
> runs all live checks green. The only KB work left is the genuine **Phase-2-build** (run_eval.py, the bake
> emitter), which waits on Phase-2 by design.

> **✓ PROPOSING IS NOW A PROGRAM + ALL 8 RE-PROPOSED WITH gpt-5.5 (2026-06-25, commit `78dea99`):** the
> ADR-0008 propose → gate → accept loop is now code — `extract.py render|propose|author` + `vlm_openai.py`
> (stdlib OpenAI vision client) + `propose.py`. `render` saves isolated multi-angle frames (avatar on a ground
> plane) to `agent/animation_knowledge_base/frames/<clip>/`; `propose` sends them + the MEASURED facts to
> **`gpt-5.5-2026-04-23`**, which proposes `action_id` + the per-channel 5-tuple + descriptions, gated by
> `validate_semantic_consistency` (with a self-correction retry); `accept` gates `action_id` (slug +
> uniqueness) and promotes `candidate/<clip>.json → <action_id>.json`. **All 8 accepted actions were
> re-proposed with gpt-5.5** (replacing claude-opus-4-8) — and gpt-5.5 named actions by FUNCTION from the
> frames (`nurse_give_meds → giving_pills`, `nurse_grab_aspirin → grab_bottle`). The prior Claude proposal is
> preserved at `agent/motionkb_build/archive/authored_claude_backup/` (do not use / do not delete). **MEASURED was
> untouched (golden 8/8)**; the MEASURE half is now keyed by **`clip_name`** (`raw/<clip>.json`,
> `candidate/<clip>.json`); the full gate is green. `key.env` (the `OPENAI_API_KEY`) is git-ignored
> (`frames/` WAS too — un-ignored 2026-08-05, see the note below). Operator runbook: §8.3; decision
> record: the 2026-06-25 update in [ADR 0008](docs/adr/0008-vlm-proposed-authored-fields.md).

> **✓ MotionKB moved out of `Assets/` into `agent/` (2026-08-05):** `Assets/MotionKB/` → **`agent/animation_knowledge_base/`**,
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
> it is the one layer of the contract that genuinely needs the engine — with `KB_DIR` repointed to `agent/animation_knowledge_base`
> (relative to Unity's cwd, which is the project root). Gate green after the move: validate 8/8, golden 8/8,
> manifest in sync, Unity resolves 8/8 guids.
> **`agent/motionkb/` lasted the rest of the same day.** Two commits later (`dc70a442`, still 2026-08-05)
> the whole pipeline left this repository for `~/Research/animation-agent` and `MotionKBValidator.cs` was
> deleted for `validate_guids.py`; the KB alone stayed. Every `agent/motionkb/<script>.py` path in the
> memos above names a layout that no longer exists — the live invocations are in §8 and §8.3, and they
> all run from the agent repo.

> **✓ Local git made a trustworthy rollback net + `frames/` tracked (2026-08-05):** `git status` had been
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
> `agent/animation_knowledge_base/actions/*.json`, `raw/*.json`, `.meta`, and all Unity YAML (`m_SerializationMode: 2` =
> ForceText) stay in plain git — the whole provenance/audit story depends on being able to read a text diff.
> `*.mesh` (235 MB, incl. the three de-clipped bed meshes) is `%YAML` text and deliberately stays out of LFS.
> **`agent/animation_knowledge_base/frames/` is now TRACKED** (48 PNG via LFS + their `.meta` in plain git): it was ignored
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
> be DERIVED, regenerable sidecars computed from the frozen `raw/` dumps when the assembler exists (not a
> schema bump); **gated KB write-back** of assembled motions is a sanctioned FUTURE extension (supersedes
> the flat 2026-06-18 write-back prohibition; provenance stays separate from verified source assets).
> README §1 and §6 below rewritten accordingly.

- **Phase 1 (body-part-level motion knowledge base) is done**: 8 JSON files under `agent/animation_knowledge_base/actions/` (schema `motionkb/v4`, `status: accepted`, 9 channels; KINEMATIC program-extracted, the two description fields VLM-proposed and completeness-gated, current store `vlm_accepted`. ADR [0022](docs/adr/0022-the-kb-describes-the-agent-decides.md) deleted `ik_goals`, `composability` and the per-channel `role`/`motion_type`/`contact`/`constraint`/`target`: composition, contact and channel ownership are runtime decisions now). (`typing` was added 2026-06-13 — the first **seated** action; see `agent/animation_knowledge_base/actions/typing.json` and the `EmergencyRoom_TypingTest` scene in §3.)
- **The full ER scene is imported and cleaned**: `Assets/Scenes/EmergencyRoom.unity` — visual environment only, with **no** Intent/LLM/Python/XR/director scripts (65 missing-script components were stripped).
- **The patient & bed Animators are ENABLED (live) as of 2026-06-16** — the patient breathes **in place** (no drift; `applyRootMotion` is off) and the bed holds **"Idle Up"** (backrest raised). The serialized / edit-mode pose is the baked **awake/reclining P0** state (responsive — the source's runtime-start; see `SCENARIO.md` §1), which replaced the earlier **P3** (flat, unresponsive) freeze on 2026-06-13. (This state was previously kept *frozen* via disabled Animators; that was reversed at the user's request — see the §0 note below and §2 rule 3.)
- **The scene is scenario-aligned with the source project (2026-06-09, see `SCENARIO.md`)**: all source-active nurses are active again. The acting/IK nurses are `CPRNurse` (Jill), `AirwayNurse` (Kate), `EKGNurse` (Dana) — **`Nurse1` is only a background figure with no IK/action rig; animation work targets Jill/Kate/Dana**. `NurseAnimatorEvents.cs` was ported (prop events now have a receiver), and the missing NavMesh agent type "Nurse" (`-1372625422`) was restored in `ProjectSettings/NavMeshAreas.asset` (verified binding to the baked surface).
- **MotionKB was source-aligned (2026-06-09)**: feet/legs magnitudes re-measured in world space (the old hips-relative metric inflated planted feet under pelvis leans), `check_pulse` feet corrected to static, `giving_pills` feet corrected to dynamic (real adjusting step). (The same pass also narrowed `giving_pills.can_overlay_on` under a lock-disjointness rule; both the field and the rule were deleted by ADR [0022](docs/adr/0022-the-kb-describes-the-agent-decides.md) — the agent names the channel split in its plan now.)
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
| the Unity project, including `agent/animation_knowledge_base/` | `F:\...\Project\Animation` | **Windows git** |
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
`MOTIONKB_DIR=/mnt/f/.../agent/animation_knowledge_base`. Same suite, same KB, only the filesystem differs:

| suite excluding `tests/test_tools_files.py` | KB on `/mnt/f` | KB copied to ext4 |
|---|---|---|
| before 2026-08-18 | 19.5 s | 10.6 s |
| after | **12.6 s** | 9.9 s |
| after, with the 2446-clip corpus in `candidate/` (2026-08-21) | **14.1 s** | — |

`tests/test_tools_files.py` is not comparable — it walks the Unity tree by design, so it skips against
a copied KB. It cost 13.1 s when the KB held 8 records and **82 s** once it held 2454, because ten of
its cases grep the whole KB and one such call now reads 2468 files. That is the true price of the
corpus on a 9p mount, not a defect: **about 7 s per unscoped `grep`, and the model pays it too.**
Checked before accepting it — `FS_WORKERS` at 16 / 32 / 64 / 128 measures 7.2 / 6.5 / 8.9 / 7.1 s, so
the flat curve the constant was chosen against still holds and concurrency is not what is missing.
Excluding `raw` from grep (ADR 0014) already removed the part of the cost that bought nothing; what
remains is 2446 records genuinely being read. A `grep` scoped to `kb/actions` is still instant, but
scoping is what the wide mount exists to avoid — leave the trade where it is until the semantic pass
gives retrieval something better than a regex.

**How that gap was found, because two plausible answers were wrong.** It is not `KBIndex.load()` (0.27 s,
five module-scoped calls) and it is not the content hash in `raw_fingerprint` (memoising it changed
nothing measurable). Instrumenting `os.stat` / `open` / `os.listdir` gave the real shape: **225 stats and
112 opens of `raw` per run, 6.4 s of the 9**, at about 19 ms per stat and 9 ms per open over DrvFs. Guess
twice, then measure — profile by leaf cost, not by reading the code and reasoning about it.

The fix is not caching by checking, it is **caching because the write discipline already guarantees it**:
`raw` has exactly one writer, `unity_sampler.write_raw`, which now calls `transitions.forget_raw()`.
Every other process treats the KB as read-only, so within one of them `raw` cannot move, and the readers
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
| Body-part knowledge base (v1, **retired**) | git tag `kb/v1` + `schema/motionkb.v1.schema.json` (snapshot contract) | 8 actions, schema `motionkb/v1`, 6-part BodyPartFact + `composability` (incl. `posture`; `idle`/`walking`/`typing` are bases, `typing` is the first **seated** one). **Retired at the 2026-06-24 candidate→accepted promotion** — the accepted store `agent/animation_knowledge_base/actions/*.json` is v2 now (next row). |
| MotionKB **v4 (ACCEPTED)** | `agent/animation_knowledge_base/actions/*.json` (schema `motionkb/v4`, 8 with `status: accepted` in a store of 2454) · the Python extractor, now in the agent repo · `engine_mask_map.json` · [ADR 0007](docs/adr/0007-v2-body-part-split.md) · [ADR 0008](docs/adr/0008-vlm-proposed-authored-fields.md) · **[ADR 0022](docs/adr/0022-the-kb-describes-the-agent-decides.md)** · `docs/specs/motionkb-v2-spec.md` (the v2 design narrative, superseded) | 9-channel split; KINEMATIC filled + validated, golden 8/8. Since ADR 0022 the semantic half is two description fields — `action_description` (renamed from `overall_intent`) and each anatomical channel's `motion_description` — and `ik_goals`, `composability` and the per-channel 5-tuple are deleted, because each of them stated a decision about a COMBINATION on a record describing one clip. **Promoted candidate→accepted 2026-06-24** (v1 retired to tag `kb/v1`); one store since ADR 0016, rollback tag `kb/v4`. |
| **Runtime executor** (Phase 2, engine half) | `Assets/Scripts/AgentRuntime/` (15 components, `Protocol.cs` included) · [ADR 0009](docs/adr/0009-check-before-you-play.md) | `AgentLink` (the one WebSocket, agent is the server) · `Protocol.cs` **v4**, mirrored second from `agent/protocol.py` · `SceneRegistry`/`SceneQueryService` (typed scene predicates; still answer `scene.find`/`describe`/`anchors`/`position`, which are engine-internal API now — the model's surface is `scene_search` + `scene_query`) · `MotionComposer`+`ClipLibrary` (masked layers, fractional weights, per-layer clip windows) · `PoseSynth`+`PostureTransitionEvaluator` (generated sit/stand, closed loop on measured hip height) · `IkBinder` · `Locomotion` (walking, and `Preview` for a route that is computed rather than walked) · `GateEvaluator`+`GateArming`+`GateProbe`+`ValidationCharacter` (**one judgement, two clocks** — a commit is played through on a hidden duplicate first). No LLM and no generated C# on this side. |
| Agent service (Phase 2, decoupled half) | `~/Research/animation-agent` (WSL, its own repo) | Retrieval over the KB, the deterministic channel partition and seam schedule, the ReAct loop, the 12 declared tools, `agent/protocol.py` as the contract authority. Acceptance: `pytest` (358), `run_eval.py` (floor 7/12), `smoke_validate.py` against play mode. See §1.1 for which side to run git and python on. |
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

1. **Agent-side Python service (single agent + deterministic scaffolding)**: consume `agent/animation_knowledge_base/actions/*.json`
   (`action_description` for coarse retrieval — it inherited the deleted `tags`' weight in the searchable
   document; each channel `motion_description` is independently embeddable for part-level queries). NL intent + current scene state → semantic understanding, action
   decomposition, retrieval selection, symbolic assembly planning; on a gate failure the scaffold feeds the
   reason back and rolls back to the corresponding upstream stage.
2. **Deterministic assembly + bake (code, not the model)**: full KB match → return the clip + its playback
   constraints (the real-time path). Otherwise assemble retrieved REAL body-part segments on the unified
   skeleton + timeline — mask compositing, phase alignment, transition generation, IK, foot locking, contact
   constraints, collision correction — then **bake a new `AnimationClip`** (optionally export FBX). Gate
   intermediates (foot-contact phase, key poses, transition validity) are DERIVED sidecars computed from the
   frozen `raw/` dumps when this assembler exists — **not a schema bump (decided 2026-07-01 of the v2
   contract; the store is v4 now and the decision is unchanged)**.
3. **Engine-side executor (fixed protocol, replaceable)**: provides the scene graph, character skeleton,
   object poses, collision state, IK execution results, and animation preview; consumes the structured
   assembly data. Scene understanding = deterministic enumeration/caching/state-sync over the scene graph
   first; a multimodal model assists only for candidate-object filtering, affordance judgment, semantic
   disambiguation, and visual feedback verification.
4. **Hard geometric gates (the scaffold's enforcement layer)**: intent (target reachability, orientation,
   scene compatibility) · retrieval (whether the retrieved part-segments can in fact be composed —
   channel coverage and contention — and transition validity) · assembly
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
6. **The semantic pass over the corpus — DONE 2026-08-27 (§0).** All 2446 Mixamo records were measured
   (2026-08-21), rendered (2026-08-27) and then described the same day: `qwen3.8-27b`, served locally on
   the HPC cluster, read each clip's eight-view ring and wrote the nine v4 sentences —
   `action_description` plus one `motion_description` per anatomical channel — into every record. The
   corpus is now retrievable by meaning as well as by measurement. What is still owed is **acceptance**:
   the 2446 stay `status: candidate` with `vlm_proposal.status: awaiting_human_accept` and a null
   `action_id`, because the describer no longer names and naming happens at acceptance. Deciding how a
   library this size gets accepted — by hand, by sampled review, or by a gate — is the open question,
   not the describing.
7. **Grow the accepted set**: extract more nursing actions offline via the §8.3 runbook (next targets:
   `button`, `grab`, `call`, `lowerBed`, the patient/bed clip families — see `SCENARIO.md` §6).

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
- The MotionKB validates — `python validate_motionkb.py`, run from the agent repo (`~/Research/animation-agent`; the pipeline left this repository on 2026-08-05), checks the whole store (`agent/animation_knowledge_base/actions/*.json`, 2454 records, no status filter) against `motionkb.v4.schema.json` + channel-vocabulary agreement with `engine_mask_map.json` + **`validate_descriptions`**: an accepted record carries a non-blank `action_description` and a non-blank `motion_description` on each of the 8 anatomical channels. 2454/2454 pass, 8 of them accepted. The cross-field invariants (locks/free partition, overlay lock-disjointness, posture compatibility, ik effector→channel resolution) and `validate_semantic_consistency` are **deleted** — every one of them read a field ADR [0022](docs/adr/0022-the-kb-describes-the-agent-decides.md) removed, and prose has nothing to contradict. The gate certifies WELL-FORMED and COMPLETE now, where it used to certify SELF-CONSISTENT; it never certified correct. Re-run reproducibility is guarded by `python test_golden_extraction.py` (golden KINEMATIC reproduces from the frozen `raw`, 8/8), and `source_clip.guid → asset` resolution by `python validate_guids.py`, which posts generated C# over the Unity MCP bridge and needs the editor open (8 resolved → `motionkb_build/reports/kb_state.md`; it replaced the in-project `MotionKB.MotionKBValidator`, so no agent-side code lives in the Unity project); one-command gate `check_kb.sh` runs them all. The former v1 store is retired to git tag `kb/v1` (`motionkb.v1.schema.json` kept as its snapshot contract). See §8.
- In `EmergencyRoom.unity`, the patient is at the **awake/reclining P0** pose on the angled bed with no clipping; its Animator is now **live** (breathes in Play, in place — no drift), and edit-mode shows the baked P0 pose; the nurse roster matches the source scene (see `SCENARIO.md` §2).
- The runtime path holds — run these in the agent repo (WSL; see §1.1): `pytest` green, `python run_eval.py` still at the 7/12 floor, and `python smoke_validate.py` against play mode, which is the only check that exercises the real executor. **If you changed `PROTOCOL_VERSION`, also type a line into the Play-mode console** — `smoke_validate.py` and `drive.py` both import the contract, so neither can catch a speaker that has the number written into it (§4 item 13). The last is the one that matters after any change to `Assets/Scripts/AgentRuntime/` or `agent/protocol.py`: **both halves ship together and a version mismatch is fatal by design**, so a rebuilt Unity side and an unchanged service will refuse to talk rather than half-speak.
- After changing an avatar pose / asset, **save the scene**, and prefer committing in the `Animation/` git repo (only commit when the user asks).

---

## 8. Auditing the MotionKB + engineering design principles

The MotionKB has a machine-checkable contract. Don't re-document the field semantics here — they
live in `agent/animation_knowledge_base/README.md`; the authoritative shape is
`agent/animation_knowledge_base/schema/motionkb.v4.schema.json` (v3, v2 and v1 kept only as the contracts of
their snapshots). What a record answers, since ADR
[0022](docs/adr/0022-the-kb-describes-the-agent-decides.md), is two questions and no others: what the
action LOOKS like (`action_description`) and how each body part MOVES (`channels.*.motion_description` +
the kinematic block). Composition, contact, IK and channel ownership are decided at runtime by the agent.

**Every command in this section runs from the agent repo, `~/Research/animation-agent` on WSL** — the
pipeline and all four gates moved out of this repository on 2026-08-05, and reach the KB here through
`MOTIONKB_DIR`. Paths below are relative to that repo's root; only the KB paths are relative to this one.

- **Validate (no Unity needed):** `python validate_motionkb.py` — validates the whole store
  (`agent/animation_knowledge_base/actions/*.json`; `collect_files()` validates BOTH stores every run — it used to return the
  candidates alone whenever any were staged, which let the accepted store go unchecked while still
  printing a pass count. Since 2026-08-21 the store also holds the 2446-clip Mixamo corpus, kinematic-only, so
  the run covers 2454 files and `-q` prints failures only) against `motionkb.v4.schema.json` + the two
  checks JSON Schema can't express: channel-vocab agreement with `engine_mask_map.json`, and
  **`validate_descriptions`** (an accepted record has a non-blank `action_description` and a non-blank
  `motion_description` on each of the **8 anatomical channels**), with per-file failure isolation. The
  old invariant battery — `locks`/`free` partition, overlay lock-disjointness, posture compatibility, ik
  effector→channel resolution — and `validate_semantic_consistency` were deleted with the fields they
  read (ADR [0022](docs/adr/0022-the-kb-describes-the-agent-decides.md)). A leftover v3 field is now a
  schema error, not a silent pass: the top level and both channel definitions are
  `additionalProperties: false`. `guid → asset` resolution is the one layer that needs the engine, and it
  is `validate_guids.py` in the agent repo — generated C# posted over the Unity MCP bridge (8 resolved →
  `motionkb_build/reports/kb_state.md`). It replaced `Assets/Editor/MotionKB/MotionKBValidator.cs`, which
  was deleted on 2026-08-05, so no agent-side code lives in the Unity project.
  One-command gate `check_kb.sh` runs all four (schema/vocabulary/descriptions
  + golden re-extraction regression + manifest-in-sync + guid→asset; the last runs live when the bridge is
  up and otherwise falls back to the committed `kb_state.md`).
- **Why it's built this way:** see `docs/adr/` (0001 contract-first · 0002 kinematic/semantic split ·
  0003 skeletal split + metrics · 0004 mask+layer disjoint-only · 0005 git-as-version/ledger ·
  0006 peak-resilience-by-design · **0007 v2 9-channel split + engine-decoupled Python extractor**,
  supersedes 0003 · 0008 VLM-proposed SEMANTIC fields · **0009 a plan is checked on a hidden duplicate
  before the visible character moves, protocol v4** · … · **0022 the KB describes, the agent decides —
  supersedes 0004 entirely and the SEMANTIC-5-tuple half of 0008**). The list above stops at 0009 for
  historical reasons; `docs/adr/README.md` is the complete index.
- **Rolling back** a KB version / extraction / mesh / pose: `docs/ROLLBACK.md`.

Status: the data contract (the schema — now `motionkb.v4.schema.json`, with v1/v2/v3 kept as their
snapshots' contracts — + `validate_motionkb.py` + ADRs incl. **0007** and **0022** + CHANGELOG)
is landed and self-verifies (2454/2454 pass, 8 of them accepted; a deliberately-broken file is caught). The
**extractor LANDED in Python** (ADR 0007) — bone-map/metric as DATA, kinematic/semantic
split, run-log — **replacing the originally-planned C# Editor script** (the C# file names in §8.2 are
superseded; read them as "their Python equivalents"), and it lives in the agent repo since 2026-08-05.
These are now **all landed (2026-06-24)**: the
guid→asset gate (8 resolved → `motionkb_build/reports/kb_state.md`; a Unity Editor script then,
`validate_guids.py` in the agent repo since 2026-08-05), the golden
re-extraction regression (`test_golden_extraction.py`, pure Python over the frozen `raw`), the
`manifest.json` index (`gen_kb_manifest.py`), and the `retrieval_eval_set.json` seed. The **SEMANTIC pass** is
**DONE** (the VLM-proposal loop, [ADR 0008](docs/adr/0008-vlm-proposed-authored-fields.md): first
human-accepted 2026-06-24; re-proposed on the 2026-07-01 full-pipeline re-run and auto-accepted as
`vlm_accepted` — human `author` review is optional since 2026-06-25, so the current store has
`verified_against_screenshots=false`). Since ADR
[0022](docs/adr/0022-the-kb-describes-the-agent-decides.md) that half is exactly two fields —
`action_description` and each anatomical channel's `motion_description` — and the 5-tuple,
`composability` and `ik_goals` are deleted rather than reinterpreted; the eight records' descriptions were
carried through the migration verbatim, with no proposal re-run. The **candidate→accepted store promotion is DONE (2026-06-24)** — v2
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
   `git tag kb/<ver>` + `git checkout kb/<ver> -- agent/animation_knowledge_base/` is rollback. Do NOT build a parallel
   per-entry `content_sha256` store-and-reconcile or a `promotion_log.jsonl` ledger — that re-implements git.
   Compute a hash on demand inside the validator only if some out-of-band gate ever needs it.
6. **kinematic vs semantic split.** The extractor rewrites **KINEMATIC** fields only (magnitudes, mean poses, duration,
   frame_rate, loop, root motion, the static/dynamic threshold). The **SEMANTIC** half is now exactly two
   fields — `action_description` (renamed from `overall_intent`) and each anatomical channel's
   `motion_description` — proposed from rendered frames, optionally human-reviewed, and never clobbered by
   the measure half. Everything else the old list named (`tags`, `composability`, `mask_coverage`,
   `ik_goal`, the per-channel `target`) was deleted by ADR
   [0022](docs/adr/0022-the-kb-describes-the-agent-decides.md); `controller_*` is resolved by program from
   the AnimatorController, not written by hand. This is what stops "grow the KB" from re-introducing the
   LLM-positioning/description unreliability the whole project is positioned against (see ADR 0002).

The eight qualities these serve map to eight modules (one line each; this replaces the deleted blueprint):
**A. Data Contract** (`motionkb.v4.schema.json` + schema/vocabulary/completeness validator + readable KB-state report) ·
**B. MotionKBExtractor** (the Python pipeline in the agent repo; bone-map/metric as DATA; kinematic/semantic split + run-log) ·
**C. KB Versioning** (`manifest.json` identity/provenance + per-record `status` + git tag + CHANGELOG) ·
**D. Provenance/Audit** (richer `extraction` block: git SHA, formula version, raw measurements, real timestamp, field_origin) ·
**E. Regression & Eval** (golden re-extraction test, `test_golden_extraction.py` [now] + `retrieval_eval_set.json` annotations [seed]) ·
**F. Bake Observability** (Phase-2 per-stage trace + latency budget + `AssemblyDescription`/`baked_clip` example contracts + `fallback_bake_id`/cache) ·
**G. Handoff & CI** (README as single source of truth + runbook + ADR + local check script) ·
**H. Robustness & Peak Resilience** (resumable+atomic+per-file-isolated+bounded batch [now]; bounded fan-out, backpressure, timeout→fallback, degradation policy [Phase-2 ADR]).

### 8.2 Staged roadmap (compact)

> **Update — v2 landed in Python (2026-06-18, ADR 0007).** The extractor architecture changed from the
> C# Editor scripts planned below to a **pure Python** program (in the agent repo since 2026-08-05 — `config.py` =
> bone-map/divisors/thresholds as DATA, `metrics.py` = formulas, `extract.py` = assembly + semantic-
> preserving merge + run-log, `unity_sampler.py` = the generic pose-sampler run over Unity MCP). This
> LANDS the substance of: **[B]** (extractor with bone-map/metric as data + measured/semantic split +
> run-log), **[A]**'s no-Unity layer (`validate_motionkb.py`, now v2), **[D]** (the enriched `extraction`
> block — `extractor_version`/`metric_formula_version`/`bone_map_version`, real `extracted_at`,
> `field_origin`, per-channel `raw_measurement`), **[C-partial]** (the `candidate/` channel + per-entry
> `status`), and **[H]**'s per-file isolation + end-of-run summary + run-log. The C# file names below
> (`MotionKBExtractor.cs`, `BodyPartBoneMap.cs`, `MotionMetricConfig.cs`) are **superseded** — read them as
> "the Python equivalents". **Now landed (2026-06-24):** **[A]** the Unity-side `MotionKBValidator.cs`
> (`guid → asset`, 8 resolved → `motionkb_build/reports/kb_state.md`), **[E-now]** the golden re-extraction regression
> (`test_golden_extraction.py`), the `manifest.json` index of **[C]** (`gen_kb_manifest.py`),
> and the **[E-seed]** `retrieval_eval_set.json` (the `git tag kb/v1` + `kb/v2` anchors exist). **The KB gating item — the human authoring
> pass on the v2 candidates' SEMANTIC 5-tuple — is now DONE** (the VLM-proposal loop, ADR 0008, 2026-06-24:
> proposed-from-renders + consistency-check-gated + human-accepted, 8/8 verified). **The candidate→accepted
> store promotion is also DONE (2026-06-24): v2 is the root accepted store, v1 retired to tag `kb/v1`.**

**Do-now** (small cost, high value; all gated on the Unity MCP connection except where noted):
- **[A]** `motionkb.v1.schema.json` (landed; **superseded by `motionkb.v2.schema.json`, that by `motionkb.v3.schema.json`, and that by `motionkb.v4.schema.json`** — v1, v2 and v3 are their snapshots' contracts) + `MotionKBValidator.cs` (schema + invariants + guid resolution
  + a readable `motionkb_build/reports/kb_state.md`) + headless wrapper.
- **[B]** `MotionKBExtractor.cs` — rescue the throwaway extraction snippet into a checked-in Editor script; the
  6-part bone-map and per-part metric divisors become named code constants (`BodyPartBoneMap.cs`,
  `MotionMetricConfig.cs`); writes KINEMATIC only, emits a run-log.
- **[D]** Enrich the `extraction` block: `extractor_git_sha`, `metric_formula_version`, `raw_measurements`, a
  real timestamp (the current `…T00:00:00Z` are fake placeholders), `field_origin`.
- **[E-now]** Golden re-extraction regression — **DONE** as `test_golden_extraction.py` in the agent repo (pure Python, no
  Unity: re-runs `metrics.channel_blocks` over the frozen `raw` dumps and asserts KINEMATIC reproduces the
  accepted store; 8/8). (The original "C# Editor test" framing predates the Python extractor — superseded.)
- **[C-partial]** candidate/accepted channel + per-entry `status` (re-extract writes `candidate/`, never
  overwrites accepted) + `git tag kb/<ver>` + `CHANGELOG.md`.
- **[G]** README in-place: an "add a new action" checklist + fix the file_id gotcha (rig-specific, not a
  universal constant); the one-command gate runs schema+invariants now (landed; `scripts/check_motionkb.sh` then, `check_kb.sh` in the agent repo today); key ADRs.
- **[H]** Make the A/B batch paths per-file-isolated + end-of-run summary (non-zero exit), checkpoint/resume via
  the run-log (no CLI flag), atomic write + candidate-only; resilience constants fold into
  `MotionMetricConfig.cs`; ADR 0006 already records "peak-resilience by design, no QPS/autoscaling".

**Phase-2 enabler** (seed the contract/field/annotation now; bears weight only in Phase 2):
- **[C]** rest of `manifest.json` (identity + provenance index, NO content_sha256) + `kb_version`.
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

> Moved here from `agent/animation_knowledge_base/README.md` (2026-06-24) — that README is now the human-facing
> overview; this is the agent/operator runbook (doc-audience convention: README = humans, HANDOFF = agent).

**Run every command below from the agent repo, `~/Research/animation-agent` on WSL**, with `MOTIONKB_DIR`
pointing at this repo's `agent/animation_knowledge_base/` (§1.1). The pipeline left this repository on
2026-08-05; script paths are relative to that repo's root, KB paths to this one.

The extractor is that repo's top level (`config.py` channels/bone-map/divisors/thresholds · `metrics.py`
formulas · `extract.py` orchestration with the `register|resolve-controller|emit-sampler|sample|assemble|render|propose|author|migrate`
subcommands (`migrate [--dry-run]` is the v3→v4 converter: it rewrites shape and restamps provenance and
reads no pose dump, because v4 moved no number) · `unity_sampler.py` the generic sampler + the render generator + the stdlib HTTP-bridge client ·
`vlm_openai.py` / `vlm_anthropic.py` the two vision clients, one `describe` each, picked by
`MOTIONKB_VLM` · `propose.py` the prompt, the reply parser and the completeness loop). The MEASURE half
keys its working files by **`clip_name`** (`raw/<clip>.json`, `actions/<clip>.json`); the `action_id` is
decided by a human at acceptance, when the file is renamed to `<action_id>.json`
(auto on `propose`, or via the optional human `author` pass).

**ONE STORE since 2026-08-21 (ADR 0016).** `agent/animation_knowledge_base/actions/` holds all 2454 records whatever their
status; `candidate/` is gone. A record is named by its key — `<clip_name>.json` while unlabelled,
`<action_id>.json` once accepted — so acceptance is a RENAME inside one directory, not a move between
two. Selecting the accepted subset is `paths.accepted_files()`, which reads `manifest.json` (opening
2454 records to ask costs 68 s over DrvFs, 6 s threaded); walking the whole store is
`paths.read_records()`, which reads with 32 threads. Anywhere below that says `candidate/<clip>.json`,
read `actions/<clip>.json`.

1. **Register the source clip** — `python extract.py register <clip_name>` finds the clip BY
   NAME in Unity (scans `Assets/Animations`, both standalone `.anim` and FBX-embedded sub-clips via
   `unity_sampler.build_find_clip_csharp`), **auto-resolves its `guid` + `file_id`**, and scaffolds
   `actions/<clip_name>.json` with `source_clip` filled + a blank v4 skeleton (channels, status
   `candidate`, `action_id` and both description fields null). It refuses if the clip already has a record
   anywhere in the store, found by reading `source_clip.clip_name` rather than by file name — an
   accepted record is named after its `action_id`, so a name check would miss the costly collision. It then **best-effort resolves `controller_state`/
   `controller_layer`/`trigger_param`** from the AnimatorController the clip is wired into (see step 1b); No
   manual file_id lookup, no manual controller lookup.
   - **Why this matters: `file_id` is rig/importer-specific, NOT a universal constant** — the 5 nurse overlay
     FBX share `1827226128182048838`, a standalone `.anim` uses `7400000`, `X Bot@Typing.fbx` uses
     `-203655887218126122`. `register` reads the clip's actual `file_id` so you never hand-copy it. If two
     clips share the name it lists every match (path|guid|file_id) and refuses to guess — disambiguate by
     renaming the clip or writing the stub by hand.
   - **Nothing else is filled by hand.** The only meaning-level fields left are `action_description` and
     the per-channel `motion_description`, and both come from step 6; `controller_*` is resolved in step 1b.
     There is no `composability`, `ik_goals` or 5-tuple to scaffold — ADR
     [0022](docs/adr/0022-the-kb-describes-the-agent-decides.md) deleted them, because each stated a
     decision about a COMBINATION on a record that describes one clip.
     (`_source_files()` walks the one store, so the new clip flows through the next steps.)
   1b. **Resolve controller wiring** — `register` already attempts it; re-run explicitly with
     `python extract.py resolve-controller <clip_name>` after you wire the clip into a
     controller. `unity_sampler.build_resolve_controller_csharp` scans every `AnimatorController` under
     `Assets/Animations` via the typed `UnityEditor.Animations` API: it finds the state whose motion (directly
     or inside a BlendTree) is the clip, and reads `controller_state` = state name, `controller_layer` = layer
     name, `trigger_param` = the parameter on a transition INTO that state (the layer's **default/resting state
     gets `trigger_param: null`** — it's entered by default, not by an activating trigger; this is why `idle`
     resolves to `null` not `Speed`). **Not wired → all three left `null` (blank), by design** (schema makes
     `controller_state`/`controller_layer` nullable). Ambiguous (>1 distinct wiring) → reported, left unchanged.
     Verified live: reproduces all 8 stored `controller_*` exactly.
2. **Emit the sampler** — `python extract.py emit-sampler` writes
   `_generated_sampler.cs` (a generic pose-sampler built from `config.py`, no KB knowledge).
3. **Sample in-engine (the only Unity touch)** — `python extract.py sample` drives it: with
   the editor open and the MCP server on HTTP (port 8080), it POSTs the generated C# to the bridge
   (`/api/command`, `execute_code`, `safety_checks:false` since the sampler writes files) and writes
   per-frame root-local bone positions to `agent/animation_knowledge_base/raw/<id>.json`. `unity_sampler.run_csharp_over_http`
   is the stdlib client; `--host`/`--port`/`--instance` override the target. (Or run the C# by hand via any
   MCP `execute_code` client — the "caller is transport" path still works.) Re-sampling is deterministic:
   verified byte-identical `raw` and golden 8/8 on a fresh run.
4. **Assemble** — `python extract.py assemble` computes the 9-channel KINEMATIC blocks, writes
   them back into `agent/animation_knowledge_base/actions/<key>.json` (KINEMATIC authoritative; SEMANTIC preserved), and emits
   `motionkb_build/reports/extract_run.md`. Per-file isolated, atomic write. **It SKIPS accepted records** — with one
   store it now walks them, and their KINEMATIC half is frozen golden; re-measuring those is
   `recalibrate_kinematic.py`'s deliberate job (dry-runnable, KINEMATIC-only).
5. **Render frames** — `python extract.py render <clip>` renders the eight-view ring (avatar on
   an isolated layer + a ground plane, so the VLM reads ground contact) to `agent/animation_knowledge_base/frames/<clip>/`,
   kept for human review. `unity_sampler.build_render_csharp` is the generator. WHICH angles: all of
   them, since 2026-08-26 — `view_ring` returns eight directions 45° apart around the avatar's own
   facing, so nothing is left to a per-action guess about which axis an action reads along. WHICH three
   times: `select_frame_indices` picks the frames that minimise how far the worst-covered frame of the
   clip is from the nearest picture, in normalised muscle space (ADR 0015). Before 2026-08-21 they were
   spread across an "action window", which on a held action put all three inside the hold --
   `check_pulse` was labelled from one pose photographed three times. 24 JPEGs a clip, ~60 KB each,
   issued in two bridge calls of twelve — 16 for a clip whose dump has only two frames, since
   `select_fracs` cannot return three moments a clip does not have.
6. **Propose (VLM describes + auto-keeps)** — `python extract.py propose <clip>` sends those frames to
   the configured VLM (`MOTIONKB_VLM=openai` for `gpt-5.5-2026-04-23`, else `claude-opus-5`). Since ADR
   [0022](docs/adr/0022-the-kb-describes-the-agent-decides.md) it writes **descriptions only**, and since
   the 2026-08-27 prompt rewrite (§0) that means **nine sentences**: `action_description` plus one
   `motion_description` per anatomical channel, returned as nine `label: sentence` lines that
   `propose.parse_reply` reads. **The prompt carries one measurement** — which parts move — for the
   reasons in §0; `mean_pose`, carriage and the magnitudes are left to the pictures, which show them
   better. **It does not name**: `action_id` collides across walk variants, so a record keeps its
   `clip_name` key until acceptance. There is nothing to derive from the reply either — the 2026-06-25b
   derivation of `composability.locks`/`free`/`seam_owner` and of `ik_goals` went with the fields, and so
   did the constrained-vocabulary and consistency apparatus, because there are no enums left to violate
   and no cross-field rule left to satisfy. The retry loop's one remaining job is a reply that skipped a
   channel. The program NEVER writes KINEMATIC (ADR 0002); `controller_*` untouched. Needs the provider's
   key in `key.env` (git-ignored). **A record that already has an `action_id` then AUTO-PROMOTES
   (`_promote_candidate(human=False)`) to `<action_id>.json` with provenance `vlm_accepted`** (no human
   required — ADR 0008 human gate is now opt-in). `--stage` holds it at `status: candidate`, and so does
   having no name yet, which is where every corpus record stays.
7. **Author (OPTIONAL human review)** — `python extract.py author <clip|all>` (or `propose --stage`
   first, then this): gates `action_id` (slug + uniqueness), flips `vlm_proposed → semantic`, sets
   `verified_against_screenshots=true` + `verified_by/at`, marks `vlm_proposal.status = human_accepted`, and
   renames `actions/<clip>.json → actions/<action_id>.json`. Skipping it leaves the VLM output standing as
   `vlm_accepted` (auditable in `extraction.vlm_proposal.status` / `field_origin.vlm_proposed`).
8. **Validate & record** — `python validate_motionkb.py`, then `python gen_kb_manifest.py` to
   refresh `manifest.json` (its provenance includes the model + `vlm_proposal_status`); `git tag kb/<ver>`,
   update `schema/CHANGELOG.md`, commit (the commit message is the record; ADR 0005).

The per-channel metric formulas / divisors / thresholds are the ADR 0007 metric table, mirrored as DATA in
`config.py` (`DIVISOR`, `STATIC`) + `metrics.py`. `validate_motionkb.collect_files()` validates
the WHOLE store every run — 2454 records, no status filter, nothing to prefer and so nothing to skip.

### 8.3b Bulk corpus ingest — `ingest_corpus.py` (KINEMATIC + frames, no semantic)

Steps 1-3 above add ONE action and end in a semantic proposal. A corpus does not fit that shape, so it has
its own six verbs in the agent repo (ADR 0014). Run from `~/Research/animation-agent`:

```
python3 ingest_corpus.py index      # ONE engine call enumerates the folder -> motionkb_build/reports/corpus_index.tsv
python3 ingest_corpus.py register   # pure Python: one actions/<clip>.json stub per indexed clip
python3 ingest_corpus.py sample     # one engine call per clip; skips clips that have a dump; ~60 min for 2446
python3 ingest_corpus.py measure    # pure Python: raw -> the KINEMATIC block -> motionkb_build/reports/corpus_ingest.md
python3 ingest_corpus.py render     # one engine call per clip; skips clips whose ring is complete; ~246 min for 2446
python3 ingest_corpus.py status     # where the funnel stands
```

All six have been run over the 2446-clip Mixamo corpus: measured 2026-08-21, rendered 2026-08-27 (§0).
The semantic half followed on 2026-08-27, outside these verbs, on HPC (§0) — what is still owed is
acceptance; see §6 step 6.

Four things to know before running it:

- **It stops short of the semantic half.** `render` produces the evidence frames; nothing here proposes or
  promotes. Records come out of these verbs at `status: candidate` with
  `action_id`, `action_description` and every `motion_description` null — the whole semantic half, since
  ADR [0022](docs/adr/0022-the-kb-describes-the-agent-decides.md). (The descriptions in the store today
  were written afterwards by the HPC pass, not by these verbs.) That is a legal record: the schema makes
  those nullable, they are listed under `field_origin.semantic_pending`, and `validate_descriptions`
  requires them the moment `status` is anything but `candidate` (fail-closed — a record with no `status`
  is held to the full bar).
- **The population is the index, not the store.** `extract.py assemble` writes a candidate for every source
  entry it can see, including the 8 accepted ones; this does not. `actions/` is never read or written.
- **`sample` and `render` are the slow verbs, the only two that need Unity, and both resume.** `sample`
  skips clips whose `raw` dump exists; `render` skips clips whose frame count already matches
  `_expected_frames(row)`, which sizes the ring from the clip's own length rather than assuming 24 — a
  one-frame Mixamo pose asset owes 8 × 2 = 16, and assuming 24 called all 128 of them failures forever.
  Each has `--retry-failed` against its own failure list in `motionkb_build/reports/` and `--limit N` for
  a pilot.
- **`index` refuses on a duplicate clip name** rather than picking a winner. `raw/<clip>.json`,
  `actions/<clip>.json` and `frames/<clip>/` are keyed by clip name, so two clips sharing one would
  overwrite each other.

KINEMATIC is computed by importing `extract._apply_kinematic` / `extract._build_extraction`, not by a second
implementation — the bulk and curated paths cannot drift into two dialects of one contract.

**The corpus's `raw/mx_*.json` (~1.4 GB) and `frames/mx_*/` (~3.5 GB) are gitignored**, following the
corpus FBX under `Assets/Animations/Mixamo30/`, which are gitignored for the same reason: regenerable
offline, and too large for a repository that already cannot be pushed. `pub-code`'s `.pubignore` excludes
both from the mirror as well. The candidate records built from them ARE tracked, and so are the eight
nursing actions' own frame directories. Promoting a corpus clip into `actions/` means `git mv`-ing its FBX into a tracked folder
and `git add -f`-ing its dump in the same commit — the one-commit invariant is unchanged.

> **2026-06-25 — existing 8 re-proposed with gpt-5.5.** All 8 accepted actions were re-proposed via this
> `render → propose → author` loop using `gpt-5.5-2026-04-23` (replacing the prior `claude-opus-4-8` proposal;
> the Claude version is preserved at `agent/motionkb_build/archive/authored_claude_backup/` — do not use / do not delete).
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
>
> **Half of this entry is history as of 2026-08-26 (ADR
> [0022](docs/adr/0022-the-kb-describes-the-agent-decides.md)):** point (2) is void — `composability` and
> `ik_goals` are deleted, so nothing is derived from a proposed role, and `mask_coverage` is gone too.
> Points (1) and (3) still describe the live pipeline: `controller_*` is program-resolved, and `propose`
> auto-keeps as `vlm_accepted` with `author` the opt-in human upgrade.
