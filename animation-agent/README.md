# animation-agent

The agent half of the language-driven animation assembly framework. Engine-independent Python: intent
understanding, agentic retrieval over the MotionKB, symbolic assembly planning, reading gate diagnostics
and retrying. It runs in WSL (Ubuntu 24.04 on ext4); the Unity project it drives runs on Windows.

```
animation-agent/
│
│   the runtime service — reads the KB, decides, and drives the scene
├── agent/
│   ├── protocol.py           the typed message contract with the executor (v5) — the authority
│   ├── engine.py             the runtime channel; this service is the SERVER, the engine connects in
│   ├── console.py            the input sources that are not stdin, and the one way a turn is displayed
│   ├── loop.py               the ReAct loop, shaped after Codex's submission/event queues
│   ├── llm/                  one backend per endpoint (Realtime / Responses / Chat), one interface
│   ├── tools/                the 13 declared tools — kb (motion_*), files, scene (unity_*)
│   ├── kbindex.py            the KB in memory, and the only place that decides what a model sees
│   ├── assemble.py           which action drives which body channel, and in what share. No model involved
│   ├── segments.py           which frames of a clip one channel is actually moving in; one repetition
│   ├── transitions.py        where two actions join, how far apart they are there, how long the blend
│   ├── gates.py              a geometric measurement, turned into something the model can act on
│   └── digest.py             one tool call, as the line a person reads
├── cli.py                    the service itself, and a stdin session against it
├── terminal.py               the terminal you type into — Windows, standard library only
├── probe_*.py                one fixed plan, no model — the deterministic regressions
│      probe_mix.py           one channel, two clips: is the derived share the one the graph holds
│      probe_pairs.py         N sampled ordered pairs, one AFTER the other; --engine commits each
│      probe_compose.py       the same sample played AT THE SAME TIME: what composes, what only looks like it
├── smoke_*.py                against a REAL Unity in play mode, no model
│      smoke_validate.py      the fence: preview, check on the duplicate, then walk and commit
│      smoke_engine.py        the executor alone — full match, layered composition, scene grounding
├── tests/                    the agent side, without an editor or an API key
│      corpus.py              the clip ids the suite stands on, named for the property each is there for
├── legacy/eval_8_actions/    the retired retrieval eval, archived with the records it scored
│
│   the offline pipeline — builds the KB (stdlib only)
├── config.py                 body-part split + frozen measurement normalization (engine-neutral)
├── metrics.py                the 9-channel KINEMATIC computation — variation and mean pose
├── paths.py                  where the KB lives, and the rules for writing to it
├── unity_sampler.py          the ONE place that touches Unity — generates C#, never ships it at runtime
├── extract.py                pipeline: register / resolve-controller / emit-sampler / sample / assemble / migrate / render / propose / author
├── ingest_corpus.py          the measure half and the render step over a WHOLE asset folder, stopping
│                            short of any description (ADR 0014); both slow verbs resume
├── HPC_HANDOFF.md            running the semantic pass on HPC: what to build, what not to touch
├── propose.py                the describer: the prompt, the nine-line reply parser, the completeness loop
├── vlm_openai.py             stdlib VLM client (gpt-5.5); `describe` returns the reply text
├── vlm_anthropic.py          the same three symbols on claude-opus-5; MOTIONKB_VLM picks between them
├── build_transitions.py      verify the seam search on a sample of pairs; there is no table to build
├── build_segments.py         regenerate the derived per-channel segment table, and report what it found
├── build_posture.py          the posture sidecar: four coarse states per clip, and how far each travels
├── audit_posture.py          those rules against clips a human can label by eye
├── recalibrate_kinematic.py  rewrite the accepted records' KINEMATIC half after a formula bump
├── calibrate_divisors.py     refit the variation divisors on the corpus, offline from raw/
├── validate_motionkb.py      schema + channel vocabulary + description completeness  (no engine)
├── test_golden_extraction.py KINEMATIC reproduces from frozen raw                   (no engine)
├── gen_kb_manifest.py        corpus index                                            (no engine)
├── validate_guids.py         guid -> AnimationClip resolution                        (needs the engine)
├── check_kb.sh               all five gates in one command
└── runtime/                  an echo server and a latency probe — how the channel is measured
```

## The repositories

| this repo (WSL, ext4) | the Unity repo (Windows, `D:\...\Animation`) |
|---|---|
| everything above | the Unity project **and the MotionKB** (`agent/animation_knowledge_base/`) |

The KB is not here on purpose. It cannot be regenerated without Unity — `raw/` comes from in-engine
`AnimationMode` sampling and `frames/` from in-engine rendering — and it grows only when a new clip is
imported into that project. It is a derivative of those animation assets, so it is versioned with them:
adding an action is then one atomic commit containing both the FBX and its KB entry, and a guid can never
drift out of sync with the store that records it.

What is in that store: **2446 general-purpose Mixamo clips, and nothing else.** Every one is
`status: accepted` with `action_id` equal to its clip name — an `mx_*` string that is already unique,
already what the raw dump and the frames directory are keyed by, and already what the Unity
`ClipLibrary` resolves, so accepting the corpus invented no names for it.

The corpus was measured (2026-08-21) and fully rendered on 2026-08-27 — every clip has its eight-view
ring on disk, 57,680 JPEGs and 3.5 GB, from one resumable pass with no failures. The semantic pass ran
the same day: `qwen3.8-27b`, served locally on HPC rather than through 2446 hosted calls, read those
rings and wrote the nine v4 sentences into every record, so the store answers what a clip MEANS as well
as what it DOES. `HPC_HANDOFF.md` has the setup and the pull-it-home procedure.

**The eight hand-authored nursing actions are no longer in it.** They live in the Unity repository at
`agent/nursing_assets/` — records, dumps, frames and the two derived tables that covered them — and
nothing reads that directory: not the runtime, not the BM25 index, not the prompt, not the agent's
search workspace, not the pipeline, not the gates, not the tests. They are kept as material for a
held-out nursing evaluation that does not exist yet, and an evaluation whose clips were ever visible to
retrieval would not be held out. Their FBX and `.anim` stay in `Assets/Animations`, where the scenes
reference them; what changed is that the agent cannot see them.

A third repository, `~/Research/pub-code`, mirrors both of these to
[`ZeyangZheng08/Animation`, branch `code`](https://github.com/ZeyangZheng08/Animation/tree/code): source
only, no 3D assets, no LFS. Neither working repository is pushed anywhere and neither can be — their
history is full of LFS pointers. The mirror is one-way and downstream: its `sync.sh` copies committed
content out of these two, so anything edited there is overwritten on the next run. Publishing a change
means committing it here first.

Reach it through `MOTIONKB_DIR` (see `paths.py` for the default):

```sh
export MOTIONKB_DIR=/mnt/d/Research/AI_agent/Animation_agent/Animation/agent/animation_knowledge_base
```

The runtime service loads the accepted store into memory at startup — all 2446 records, in about 0.55 s,
taken through `manifest.json` rather than by opening the directory (`KBIndex.load` →
`paths.accepted_files`) — so retrieval never touches the disk; only `frames` JPEGs are read on demand
when the model asks for visual evidence. A search over the loaded index costs about 18 ms. It treats the
KB as **read-only**. The only writer is the offline pipeline here.

Start-up also reads `derived/posture.json` and refuses to run without one that matches the current
algorithm version and covers every accepted record. That is deliberate: every plan step carries a
posture, and a service that fell back to calling everything `standing` would switch off the refusal that
stops it walking a seated character off a chair. See **Posture states** below.

The agent can also *search* both the KB and the animation assets it derives from, through one set of
ordinary tools — `glob`, `grep`, `read` over two mounted places, `kb/` and `source/` (see
`agent/tools/files.py`). Both are read-only: there is no write tool, no edit tool and no shell.

`source/` is `Assets/Animations/Mixamo30`, the 2446 FBX the records were sampled from — that folder and
not `Assets/Animations`, which also holds the nursing FBX, the nursing `.anim` and the character rigs.
So the two mounts describe one library and "does anything like this exist" has the same answer whichever
way it is asked. They answer it differently, though: the records are prose and grep reads them, while
the FBX are binary and grep opens none of them, so a question about `source/` is a question about file
NAMES and glob is what answers it. A grep there returns "nothing was searchable" rather than "nothing
matched" — a miss over zero files is not evidence of absence, and reporting it as one would be the worst
answer the tool can give.

**Write discipline.** The KB lives on a Windows git worktree reached over DrvFs. Everything written to it
goes through `paths.write_text` / `write_json` / `write_bytes`, which are UTF-8 without BOM and LF, so a
file written from Linux is byte-identical to one written from Windows. **Each repo's git runs natively on
its own side** — Linux git for this one, Windows git for the Unity repo; what is forbidden is WSL git
reaching across to `/mnt/d`, which reports hundreds of bogus dirty files. Pose dumps are written back
verbatim rather than re-serialized, so re-sampling unchanged data produces a zero-line diff — `git status`
stays a working drift detector for the KB.

## Driving it: Windows types, WSL thinks, Windows renders

```
Windows   terminal.py  ──tcp://127.0.0.1:8771──┐   one JSON object per line
                                               │
WSL       cli.py --engine --headless  ─────────┘   the agent: KB, model, ReAct loop
              │
              └──ws://127.0.0.1:8770──►  Windows   Unity executor
```

**Press Play in Unity** and the terminal opens by itself — the editor launches `terminal.ps1`, which
starts the service if it is not already up and attaches. Turn it off under
*Tools > Animation Agent > Open Terminal On Play*; the launcher path lives in EditorPrefs, per machine,
because this repository sits inside WSL and its path is nobody else's.

Without Unity, or to attach a second terminal, run it directly:

```powershell
powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu-24.04\home\yuq8cp\Research\animation_agent\terminal.ps1
```

The service is **detached**: closing the terminal leaves the run going and Unity attached, and you can
reattach — or attach a second terminal — whenever. `terminal.py` is standard library only, so the
Windows side needs no `pip install`; that is also why the console channel is line-delimited JSON over
TCP rather than a WebSocket like the engine channel.

The console channel is not a third engine channel. Nothing on it reaches the executor: text goes in,
display events come out, and no code crosses it in either direction. It exists separately because the
engine link holds exactly one connection on purpose — one executor, a pure reactor — while consoles
are zero or more, attaching and detaching whenever someone opens a window.

## Two channels to the engine, and why they must not merge

| | offline (KB construction) | runtime (assembly) |
|---|---|---|
| transport | Unity MCP HTTP bridge, `POST /api/command` | WebSocket, this service is the **server** |
| what crosses | generated C#, compiled by the editor | typed messages only — **never code** |
| engine side | the Unity editor itself | a pre-compiled executor component |
| lives in | `unity_sampler.py` | `agent/engine.py` + `agent/protocol.py` |

Sampling muscle clips and rendering frames genuinely need an editor, so the offline half ships C# over
the MCP bridge. The runtime half must not: shipping code at request time would mean compiling C# during
a request, which the architecture forbids and which does not exist in a player build at all.

**Payload crosses the transport, not a shared filesystem.** The generated C# writes nothing to disk; it
returns its result and Python writes it into the KB (pose dumps verbatim, frames base64-decoded).
Measured ceiling on that channel is **8 MB per response** (16 MB fails), against ~560 KB for one clip's
pose dump — so calls are issued **per clip**. A clip's frames are the eight-view ring, 24 images, which
is ~1.9 MB of base64 and would fit; they go in **two calls of twelve** anyway, because the camera
distance is computed from the three times and not from the angles, so splitting by view costs nothing
and keeps the margin. This is what makes the executor replaceable by an Unreal, Blender or remote one
rather than only nominally so.

The engine connects **in** to this service, not the other way round: the Unity editor drops its managed
state on every script recompile and on entering or leaving play mode, so the side that must reconnect
with backoff is the engine, and a client does that naturally.

The contract is `agent/protocol.py`, and it is the authority: three shapes (request, response, event), a
version check that is fatal on decode rather than best-effort, and a mirror in
`Assets/Scripts/AgentRuntime/Protocol.cs` that is changed second.

**v4 puts a check in front of execution, and the fatal version check is why it can.** `motion.assemble`
takes a third mode, `validate`, and `motion.locomote` takes `preview`. An older executor does not know
the word `validate` and its `Apply` treated anything that was not `commit` as a dry run — so it would
answer "resolved, touched nothing", which reads exactly like a pass, and a plan would commit on the
strength of a check that never ran. Refusing to speak at all is the only safe way to be out of step.

**THE CONTRACT HAS THREE SPEAKERS, AND THE THIRD DOES NOT IMPORT THE FIRST.** `Protocol.cs` is the
mirror everyone remembers. `terminal.py` is the one nobody did: it is standard-library-only by design,
so it cannot import the package, and it had the version written in as a literal. The v3 → v4 bump
updated two of the three and every line typed into the Play-mode console was refused at the door for
days. It reads the number out of this file now, and falls back to reading the constant out of the
source rather than to a number of its own. **When bumping `PROTOCOL_VERSION`, grep for the constant,
not for the import** — and note that neither `smoke_validate.py` nor `drive.py` can catch this, because
both import the contract and are therefore always right. Requests run agent -> engine only, so
the executor stays a pure reactor with no pending-request table to reconcile after a domain reload;
events run both ways, because the text box lives in the running scene and a turn's progress has to reach
it. `runtime/` keeps only what it was ever for — the echo server and latency probe the measurements
below were taken with.

## Nothing visible moves until the plan has been checked

`unity_execute` compiles the whole plan once — steps, layers, channel windows, the retrieved posture
transition, IK bindings, declared contacts, carry — and then sends **that same dictionary** twice:

```
plan compiled once
    │
    ├─ motion.locomote preview   where the walk WOULD end. The NavMeshAgent is not enabled,
    │                            not moved, not given a destination.
    ├─ motion.assemble validate  the whole plan on a hidden duplicate of the character,
    │                            standing at that projected arrival, every renderer off
    │        fail → ToolFailure naming the metric, the object, and which of four things
    │                to change: the motion, the target, the composition, the route
    └─ motion.assemble commit    the same bytes, now visibly
```

`validate` is between this service and the executor: it costs an engine round trip (40–160 ms measured,
including the fixed-step evaluation) and **no iteration of the model's own loop**. `unity_execute` runs
it on the way past, so the ordinary path is one model call; `unity_validate` is the same derivation and
the same check, stopping there, for when the verdict is what was wanted.

Why it exists: every geometric check used to be an autopsy. The worst shape was the walk — the
execution tool walked her across the room first and derived the motion she had crossed it for
afterwards, so a plan that could not work was already on screen. Measured live on `EmergencyRoom`:
`typing` with the patient as `sit_on` now comes back `sat_through_support on obj:Patient` and she does
not move. `walk over, sit down through a retrieved transition clip, settle` validates
`ground_penetration`, `foot_skate`, `seated_on_support`, `seat_alignment`, `pelvis_above_surface` and
`sat_through_support` over 7.3 s of animation in 439 samples, then walks 2.50 m, then commits — 2.8 s
end to end, with nothing generated. `smoke_validate.py` is that run.

What the check does **not** cover, because saying so is the point: a `carry` comes back under
`unmeasured` — attaching the real prop to the duplicate is exactly the visible mutation this avoids —
and there is still no body-versus-scene collision metric anywhere in the system. The runtime `GateProbe`
is kept and its job changed: it watches for the real scene doing something the duplicate could not know
about.

## Which model, and why

`gpt-5.6-terra` on `/v1/responses`, and `gpt-realtime-2.1-mini` pinned beside it as the comparison arm
(`agent/llm/__init__.py`). The default used to be the streaming one, on the argument that the claim is
interaction and a turn costs iterations times a round trip. That argument was made against a corpus of
eight actions and five tools, where retrieval was a lookup and the only real decision was how to arrange
two clips.

Thirteen tools over 2446 actions is a different shape. Five of them answer questions about motion that
have to be asked IN ORDER: finding a clip is a search whose result has to be read, whether two clips
join is a question about their ends, and a posture change is a search, then a ranking, then a choice
between candidates that are geometrically indistinguishable and semantically not. None of that is one
round trip made faster. So correctness per turn is what the default optimises, and latency is the arm
it is measured against — pinned separately, because a comparison whose control follows the treatment is
not a comparison.

The old numbers are kept and labelled in that file: 3 iterations / 3.7 s against 14 / 41.3 s, measured
over EIGHT actions and the pre-corpus tool surface. They are luna-era readings over a library that no
longer exists, so they are history rather than evidence, and re-measuring both arms on the current
surface is what replaces them.

## When a turn goes quiet

A detached service — the one Unity's Play launcher starts — used to run with its output on a hidden
Windows console. When a turn went silent there was nothing to read: no log, no traceback, no progress
line. That cost three investigations, so the service no longer renders turns to a stdout nobody
drains, and says what it has to say in `_traces/service.log` instead.

```
python drive.py --listen          watch what a running turn emits, without sending anything
tail -f _traces/service.log       the service's own log
kill -USR1 <pid>                  every THREAD's stack — works even when blocked in a syscall
kill -USR2 <pid>                  every asyncio TASK and what it is awaiting
```

The first thing that dump ever found: no `run_turn` task at all, and one line in the log saying a
console message had been dropped for being protocol v3 at a v4 service. `terminal.py` had a version
number written into it and the contract had moved; every instruction typed into the Play-mode window
was refused at the door, silently, for days. It reads the number out of `agent/protocol.py` now, and
the console answers a message it cannot read instead of dropping it — either would have been enough,
which is why there are both.

The two signals answer different questions and the difference is the diagnosis. A `run_turn` task
awaiting `_queue.get()` means the model has not replied. No `run_turn` task at all means the turn was
never created. A thread stack sitting in a write means the loop itself is blocked, and nothing
asyncio-level would ever have shown it.

The model leg is bounded: `--model-silence-s`, twenty seconds by default, applies to each stretch of
the model not answering — a response in flight, or a frame going out. Tool time is not on that clock,
and every delta, tool call and completed response resets it. Measured healthy responses on this setup
land between one and three seconds, so twenty is roughly eight times the worst of them; the turn that
prompted the bound sat silent indefinitely with the socket established and answering pings. `0` waits
for ever, which is what this used to do.

## Networking

WSL runs with `networkingMode=mirrored` (`C:\Users\<you>\.wslconfig`, then `wsl --shutdown`). That makes
loopback shared in **both** directions: this side reaches the Unity MCP bridge on `127.0.0.1:8080`, and
Unity reaches a server here on `127.0.0.1:8770`. Under the default NAT mode neither works without
chasing dynamic IPs — the Windows-to-WSL direction especially.

Measured on this setup:

| path | p50 | p99 |
|---|---|---|
| WSL -> Unity MCP bridge (`/health`, 100x) | 2.410 ms | 5.000 ms |
| Windows -> same bridge, for comparison | 3.010 ms | 23.680 ms |
| Unity -> WebSocket server here (256 B, 1000x) | **0.320 ms** | 0.557 ms |

The WSL boundary costs nothing measurable. The real floor is elsewhere: a message can only be acted on at
the next `Update`, so a frame-bound executor adds up to 16.7 ms at 60 fps — roughly fifty times the wire.
Design against the frame loop (receive on a background thread, drain in `Update`); do not spend effort
optimizing the transport further.

## The thirteen tools, in three families

The model sees thirteen tools and no others. They divide by what they are FOR, and the division is the
architecture rather than a filing convenience: search finds candidates by meaning, analysis resolves
what those candidates are and how they fit together, and only the Unity family touches a scene.

| family | tool | what it answers |
|---|---|---|
| Search | `motion_search(query, posture, transition, moves_channels, exclude, top_k)` | which clips mean this, with what each animates and the postures it starts, holds and ends in |
| Search | `glob` / `grep` / `read` | ordinary file access over `kb/` and `source/` |
| Analysis | `motion_channels(action_id)` | what each of the nine body channels does, and the sentence describing it |
| Analysis | `motion_timing(action_id)` | when each part moves, whether it repeats, the postures it passes through, and how far it travels |
| Analysis | `motion_compose(base, overlays, base_channels, pinned)` | who ends up driving what, what is shared, what cannot be, which frames of each |
| Analysis | `motion_transition(from, to, via)` | the seam between two clips, or the posture change no seam can serve |
| Unity | `unity_query(query \| object_ids, relative_to)` | which thing that is, or what it is to her right now |
| Unity | `unity_measure(character)` | the geometric verdict on what is playing |
| Unity | `unity_locomotion(destination, …)` | walk her there |
| Unity | `unity_validate(…)` | resolve a plan and check it on a hidden copy, and stop |
| Unity | `unity_execute(…)` | the same, and then play it |

The four analysis tools touch no engine. Composition, timing and seam geometry are decided from frozen
measurements, so a plan can be settled before anything is asked of the runtime and a wrong one costs a
tool call rather than a character crossing a room.

`unity_validate` and `unity_execute` take identical arguments and run identical derivations; the
difference is where the function stops. That is two tools rather than one with a `mode` because a flag
with a default invited both mistakes at once — measured, one turn spent an iteration on a dry run and
another on the identical commit, and another invented `commit: true`, which is not a parameter, and lost
a third to the error.

Numbers come OUT of this surface and never in. Every parameter is an identifier, an enum or a list of
them, so the invariant is structural rather than aspirational; what comes back does carry numbers —
a frame window, a seam cost, a share — because they are the evidence for a decision the agent then
makes in names.

## Posture states

Every clip carries a posture structure in `derived/posture.json`, written by `build_posture.py` from
the frozen dumps: a coarse state per frame, the runs those frames fall into, and the frames where one
run becomes the next.

**Four states: `standing`, `seated`, `floor`, `other`.** `floor` is this project's term for a
floor-level kinematic state — lying, crawling, and anything else with the whole body down near the
ground; it is not a standard posture name. `other` catches crouching, kneeling, airborne and
mid-transition configurations. Neither is an error state: 985 of the 2446 clips are dominantly `other`,
and a clip described as `other` has been described correctly rather than skipped.

**The measurements come from the literature; the thresholds do not.** Guerra et al. (2020) recognise
standing, sitting and lying from geometric relations BETWEEN body segments — joint angles, trunk pitch,
joint heights normalised by stature — rather than from absolute positions. Liu et al. (2017) separate
the same states with deterministic rules over trunk and thigh orientation, and say plainly that their
angle cut-offs are empirical. Schenkman et al. (1990) show that sit-to-stand is a staged dynamic
process, which is why the output is a segmentation over time rather than one label per clip. So the
literature decides WHAT IS MEASURED — normalised body height, trunk inclination, thigh and shank
inclination, knee flexion — and the numbers in the rules are **fixed operational thresholds**: values
chosen so the rules cut this corpus where a person would, versioned so a change is visible, and
deliberately not called biomechanical thresholds, because they are not measurements of anybody.

`audit_posture.py` checks the rules against twenty-one clips whose content is not in doubt —
`motionkb_build/posture_audit.json` holds the expectations — and it is a sanity audit rather than an
evaluation: twenty-one clips cannot measure an accuracy and it prints none. What it does check is
whether the rules make the mistakes their shape makes likely, a crouch or a kneel read as sitting and a
deep bend read as lying, and whether the segmentation holds its own claim that no run is shorter than
`MIN_POSTURE_DURATION_S`. It passes 21 of 21.

**Where the boundary of this sits.** `seated` here means a seated-like body configuration. Whether the
character is actually sitting ON something is a fact about a scene, and the Unity executor decides it —
`seated_on_support`, `seat_alignment`, contact, penetration. The two are reported separately and never
merged.

## What a request can ask for

One sentence, and the parts of it that are not in the library get made.

**Anyone by name.** Three characters are drivable — Jill (`chr:CPRNurse`), Dana (`chr:EKGNurse`) and
Kate (`chr:AirwayNurse`). The engine sends the names in its handshake, so `_who()` matches a name, an
id, or the scene object behind it without a round trip. With more than one connected, an instruction
that names nobody is **asked about**, never guessed — picking one would send it to the wrong person
silently, which is the failure that matters here.

**Anywhere.** A destination is an object id, an anchor, `near:<object_id>` for beside a thing rather
than at it, or `view:left` / `view:right` / `view:ahead` / `view:behind` for somewhere relative to
whoever is watching. The relative ones are resolved engine-side and sampled onto the navigation mesh, so
the metres never cross the wire — the model says a word, the same way it says `arms_reach` rather than
0.75.

**Any action after any other action.** A pair in one posture is joined at the seam the search picks.
A pair that crosses between postures has two routes, and the first is new: the library HOLDS clips for
the change. `motion_search(transition={from_posture, to_posture})` finds the ones that start in one and
end in the other, `motion_transition(via=[…])` costs each candidate at both joins and ranks them by
geometry, and naming the chosen one in `then[].via` plays it. Nothing is generated on that path, and the
result says so by carrying no `generated_transitions`. Failing that, the frames are still made — the
standing/seated pair in either direction — and the reply names the alternative.

Getting up needs nothing arranged: name a standing action while she is seated and the rise is committed
and landed first, then the walk. That order is forced, because re-enabling the navigation agent warps
the transform to the nearest walkable point, which is not under the chair.

**Sitting down lands on the seat, and that is arithmetic rather than luck.** A retrieved sit-down is a
clip that TRAVELS: `mx_Standing_To_Sitting_Transition` moves the hips 0.446 m backwards over its 67
frames, because that is how a person sits — you step back and lower yourself onto what is behind you.
Played from wherever a walk stopped it finishes 0.446 m in front of the chair. So the compiler runs the
arithmetic backwards: she ends where the seat is, the clip moves her by its own measured `root_travel`
turned into the direction she faces, therefore she starts at `seat − R(yaw) · travel`, facing away from
the seat along the line she came in on. The walk's destination becomes that point, with no stop
tolerance to spend — `right_at_it` is 0.08 m and that was the entire error budget. Measured on the
chair in `EmergencyRoom`: `seated_on_support` 0.0000 m, and the round trip back out through
`mx_Sitting_To_Standing_2` leaves her hips 0.023 m from where she sat down.

`seat_alignment` is the gate that watches this: the distance from the pelvis to the middle of the seat,
tolerance 0.05 m, fatal. It exists because `seated_on_support` is a CONTAINMENT test against the seat's
footprint, and a chair is wide enough that a sit landing near its edge contains perfectly well —
measured, that gate read 0.0000 and passed while the character was visibly 0.3–0.5 m off.

Horizontally, then. The vertical half is **not** corrected and is on the list: the clip lowers the hips
by whatever its own performance lowered them by, and the chair in `EmergencyRoom` happens to match —
`pelvis_above_surface` reads 0.1034 m and `sat_through_support` passes. A seat at a different height
will need the offset applied, and until it is, a taller or lower support is a hover or a sink that no
gate catches, because both gates are measuring the surface she was placed relative to.

**One body part driven by two clips.** A channel two actions both name is mixed rather than won, half
each. The entry phase is searched per contributing clip, over all the channels it
mixes on at once, because a clip is one performance and giving its channels separate phases would play
one walk cycle at two phases.

Half each, because there is nothing left to rank the two by. Until motionkb/v4 the shares came from
normalising the `role` table the knowledge base carried — primary against support was 0.6/0.4 — and
that number was defensible exactly as long as the ranking it normalised existed. v4 deletes `role`
(ADR 0022): which part of a clip matters depends on the task, and a record describing one clip cannot
know the task. So the agent names the channels its overlays drive, in the plan, and two names on one
channel is the agent asking for both. The rule that no numeric ever comes from the model is unchanged
and still structural — every leaf in the plan schema is a string or a boolean.

That one is the part worth being sceptical of, so it is measured rather than asserted: `probe_mix.py`
reads the weight back **off the mixer**, not off the request, because a plan that asked for a mix and
one that quietly resolved the channel to a single winner both play and look identical from outside.

**A hand is a shape, not an axis.** A channel the plan PINS to a scene object — a carried thing, a hand
bound with `ik_bindings`, a gaze-bound head — is never mixed: half a hand shaped for a patient's chest
and half for a pill bottle grips neither, and the IK then drags the wrist of a pose that was never a
grip. Two actions given that same channel is refused by name, so the caller can see which pair of
things cannot both happen rather than being served a blend of them.

Under v3 a grip was something the KB declared, per action, so two clips could each bring one to the
same hand: the channel went whole to one side and the other kept its hand MOTION while losing its
OBJECT, reported as `dropped_grips`. A v4 record declares nothing of the sort (ADR 0022) — what a
hand holds is a fact about the scene — so there is no second grip to lose. A hand holds what the plan
says it holds, once.

**An overlay contributes part of a clip, not all of it.** Assembly's unit used to be a whole clip hung
on a channel, so "walk while doing chest compressions" meant eighteen seconds of arm under a one-second
walk. `agent/segments.py` measures, per action and channel, the frames it is actually moving in — and
where the motion repeats, one repetition. Repetition is rare across this corpus: a Mixamo clip is
usually one performance rather than a cycle of one, so most windows are the moving span and the period
search finds nothing to repeat inside them. `mx_Run_While_Reloading_Rifle` is one that does, at 22
frames of 110 on the legs and the root. The union window a plan actually takes keeps a median of 100%
of the clip and 81% at the 5th percentile; 11 of 2446 keep under half. **The base is never cut** — it
sets the posture everything else hangs on. The model names an action and never sees a frame number; the
window is measured for it.

Whether that window then REPEATS is the one bit the measurement cannot supply, because it is a fact
about the task: `temporal_intent` on an overlay says `once`, `repeat` or `continuous`, and the default
plays the moving part one time.

**Runtime primitives.** Travelling and standing still are not search results. `--locomotion-action`
(default `mx_Walking_Forward`) and `--idle-action` (default `mx_Standing_Idle`) name them, and the
service checks both at start-up rather than discovering at commit time that the walk it was given is a
two-frame T-pose. The walk has to be an animation rather than a pose asset (128 of the 2446 records are
single Mixamo poses sampled at two frames, and `mx_Walking` is one of them — which is exactly the id
somebody reaches for first), has to move the root and both legs, has to be performed standing, and has
to loop without a visible jump. The stance has to be standing and still enough to hold indefinitely.
A failure is a `SystemExit` naming the option to change.

**A composed motion plays while she is walking.** Every clip is in-place: the navigation agent moves the
transform and the composer plays the animation, side by side. So an overlay travels with the walk that
gets her there rather than waiting for it to finish — she is not doing it *while* walking if it starts
when the walking stops. On arrival the walk is replaced by a stance and the overlay carries on.

`probe_compose.py` is the coverage figure for all of this, and over 2446 actions it is a SAMPLE rather
than a sweep: every ordered pair is 5,981,970 of them and about seven CPU-hours, which is why there is
no precomputed seam table either. `--pairs` (default 40) and `--seed` say which sample; the probe
reports how many compose into two sources both driving something, how many are degenerate — one action
ends up driving everything — and how many are refused on posture, pair by pair.

## Running the gates

```sh
conda env create -f environment.yml && conda activate animation-agent   # or a bare python3
./check_kb.sh
```

Five gates. Steps 1–4 need no engine:

1. **schema, channel vocabulary, description completeness** — 2446 / 2446.
2. **golden re-extraction** — the KINEMATIC half reproduces from the frozen dumps for the sixteen clips
   named in `motionkb_build/golden_set.json`. A FIXED subset, not a sample: re-measuring all 2446 would
   make this a gate nobody runs before committing, and a regression has to fail the same way twice to
   be read as one. The sixteen span standing, walking, sitting, the sit/stand transitions, crouching,
   kneeling, bending, crawling, lying, airborne and two two-frame pose assets.
3. **`manifest.json` in sync** with the accepted store.
4. **the posture sidecar is current** — `build_posture.py --check` recomputes from the frozen dumps and
   compares, so a hand-edited sidecar is caught too. It is a gate and not a convenience: the service
   refuses to start without one.
5. **guid → AnimationClip**, which only the `AssetDatabase` can do. It runs live when the bridge is up
   and otherwise falls back to the last committed `motionkb_build/reports/kb_state.md`. A deterministic
   40-clip sample by default — one C# call carries every entry it checks, and 2446 of them is a
   generated source file rather than a query — with `--all` for the run to make after a reimport.

The packages in `environment.yml` are for the runtime service being built on top of this. **The offline
pipeline needs none of them** — it is stdlib-only and runs on a bare `python3`, so a broken environment
never blocks verification of the KB.

## What must never appear here

- **Motion numerics.** The model never emits joint angles, coordinates, velocities or timestamps. Every
  final number comes from a real motion asset, or from a deterministic solver constrained by real
  trajectories and checked by the geometric gates.
- **A Unity dependency at runtime.** No UnityEngine/UnityEditor types, no C# shipped per request. The one
  sanctioned exception is offline and confined to `unity_sampler.py`.

Conventions: code, comments, identifiers, filenames and commit messages are English. Secrets go in
`key.env` at this repo's root (git-ignored).

See the Unity repo's `README.md` for the research statement, `HANDOFF.md` for the engineering handoff,
and `docs/adr/` for the decision record — those stay there, with the project they describe.
