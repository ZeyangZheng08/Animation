# animation-agent

The agent half of the language-driven animation assembly framework. Engine-independent Python: intent
understanding, agentic retrieval over the MotionKB, symbolic assembly planning, reading gate diagnostics
and retrying. It runs in WSL (Ubuntu 24.04 on ext4); the Unity project it drives runs on Windows.

```
animation-agent/
│
│   the runtime service — reads the KB, decides, and drives the scene
├── agent/
│   ├── protocol.py           the typed message contract with the executor (v3) — the authority
│   ├── engine.py             the runtime channel; this service is the SERVER, the engine connects in
│   ├── console.py            the input sources that are not stdin, and the one way a turn is displayed
│   ├── loop.py               the ReAct loop, shaped after Codex's submission/event queues
│   ├── llm/                  one backend per endpoint (Realtime / Responses / Chat), one interface
│   ├── tools/                the 14 declared tools — kb, files, scene
│   ├── kbindex.py            the KB in memory, and the only place that decides what a model sees
│   ├── assemble.py           which action drives which body channel, and in what share. No model involved
│   ├── segments.py           which frames of a clip one channel is actually moving in; one repetition
│   ├── transitions.py        where two actions join, how far apart they are there, how long the blend
│   ├── gates.py              a geometric measurement, turned into something the model can act on
│   └── digest.py             one tool call, as the line a person reads
├── cli.py                    the service itself, and a stdin session against it
├── terminal.py               the terminal you type into — Windows, standard library only
├── run_eval.py               score retrieval against retrieval_eval_set.json
├── probe_*.py                one fixed plan, no model — the deterministic regressions
│      probe_mix.py           one channel, two clips: is the derived share the one the graph holds
│      probe_pairs.py         all 56 ordered action pairs, one AFTER the other; --engine commits each
│      probe_compose.py       the same 56 played AT THE SAME TIME: what composes, and what only looks like it
├── tests/                    the agent side, without an editor or an API key
│
│   the offline pipeline — builds the KB (stdlib only)
├── config.py                 body-part split + frozen measurement normalization (engine-neutral)
├── metrics.py                the 9-channel MEASURED computation
├── paths.py                  where the KB lives, and the rules for writing to it
├── unity_sampler.py          the ONE place that touches Unity — generates C#, never ships it at runtime
├── extract.py                pipeline: register / resolve-controller / sample / assemble / render / propose / author
├── propose.py                VLM proposes the SEMANTIC 5-tuple; composability derived from it
├── vlm_openai.py             stdlib VLM client
├── build_transitions.py      regenerate the derived seam table (a cache; kb_transition recomputes it)
├── build_segments.py         regenerate the derived per-channel segment table, and report what it found
├── validate_motionkb.py      schema + cross-field invariants + semantic consistency  (no engine)
├── test_golden_extraction.py MEASURED reproduces from frozen _raw                    (no engine)
├── gen_kb_manifest.py        corpus index                                            (no engine)
├── validate_guids.py         guid -> AnimationClip resolution                        (needs the engine)
├── check_kb.sh               all four gates in one command
└── runtime/                  an echo server and a latency probe — how the channel is measured
```

## The two repositories

| this repo (WSL, ext4) | the Unity repo (Windows, `F:\...\Animation`) |
|---|---|
| everything above | the Unity project **and the MotionKB** (`agent/kb/`) |

The KB is not here on purpose. It cannot be regenerated without Unity — `_raw/` comes from in-engine
`AnimationMode` sampling and `_frames/` from in-engine rendering — and it grows only when a new clip is
imported into that project. It is a derivative of those animation assets, so it is versioned with them:
adding an action is then one atomic commit containing both the FBX and its KB entry, and a guid can never
drift out of sync with the store that records it.

Reach it through `MOTIONKB_DIR` (see `paths.py` for the default):

```sh
export MOTIONKB_DIR=/mnt/f/Research/AI_agent/Animation/Animation_agent/Project/Animation/agent/kb
```

The runtime service loads the KB into memory at startup — the whole action store is ~1.4 MB — so
retrieval never touches the disk; only `_frames` PNGs are read on demand when the model asks for visual
evidence. It treats the KB as **read-only**. The only writer is the offline pipeline here.

The agent can also *search* both the KB and the animation assets it derives from, through one set of
ordinary tools — `glob`, `grep`, `read` over two mounted places, `kb/` and `source/` (see
`agent/tools/files.py`). Both are read-only: there is no write tool, no edit tool and no shell. The
second mount is what makes "no sit-down material exists anywhere" a question the agent can answer from
evidence rather than take from its prompt — the KB holds only what was accepted into it, so asking the
KB what exists returns what was already decided.

**Write discipline.** The KB lives on a Windows git worktree reached over DrvFs. Everything written to it
goes through `paths.write_text` / `write_json` / `write_bytes`, which are UTF-8 without BOM and LF, so a
file written from Linux is byte-identical to one written from Windows. **Each repo's git runs natively on
its own side** — Linux git for this one, Windows git for the Unity repo; what is forbidden is WSL git
reaching across to `/mnt/f`, which reports hundreds of bogus dirty files. Pose dumps are written back
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
powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu-24.04\home\chenhui\Research\animation-agent\terminal.ps1
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
pose dump and ~3.2 MB for one clip's frames — so calls are issued **per clip**. This is what makes the
executor replaceable by an Unreal, Blender or remote one rather than only nominally so.

The engine connects **in** to this service, not the other way round: the Unity editor drops its managed
state on every script recompile and on entering or leaving play mode, so the side that must reconnect
with backoff is the engine, and a client does that naturally.

The contract is `agent/protocol.py`, and it is the authority: three shapes (request, response, event), a
version check that is fatal on decode rather than best-effort, and a mirror in
`Assets/Scripts/AgentRuntime/Protocol.cs` that is changed second. Requests run agent -> engine only, so
the executor stays a pure reactor with no pending-request table to reconcile after a domain reload;
events run both ways, because the text box lives in the running scene and a turn's progress has to reach
it. `runtime/` keeps only what it was ever for — the echo server and latency probe the measurements
below were taken with.

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

**Any action after any other action.** All 56 ordered pairs of the 8 actions schedule, and the 14 that
cross between standing and seated have their frames generated in **either** direction. Getting up needs
nothing arranged: name a standing action while she is seated and the rise is committed and landed first,
then the walk — that order is forced, because re-enabling the navigation agent warps the transform to
the nearest walkable point, which is not under the chair.

**One body part driven by two clips.** A channel two actions both claim is mixed rather than won, at
shares taken from the `role` table already in the contract: primary against support is 0.6/0.4, two
primaries are half each. The entry phase is searched per contributing clip, over all the channels it
mixes on at once, because a clip is one performance and giving its channels separate phases would play
one walk cycle at two phases.

That one is the part worth being sceptical of, so it is measured rather than asserted: `probe_mix.py`
reads the weight back **off the mixer**, not off the request, because a plan that asked for a mix and
one that quietly resolved the channel to a single winner both play and look identical from outside.

**A hand is a shape, not an axis.** A channel where either side grips an object is never mixed: half a
hand on a patient's chest and half on a pill bottle satisfies neither grip. One side takes it whole, and
the other keeps its hand MOTION while losing its OBJECT — because a grip is two things fused, and only
one of them is in the animation. The FK curves are joint rotations; what holds the bottle is the
`ik_goal` aiming the wrist and the event that makes the prop visible, both of which live outside the
clip. Every detachment is reported as `dropped_grips`, so "her hand performs that motion with nothing in
it" is something the reply can say. Naming both objects — carrying one and binding a hand to the other —
is still refused, because there is nothing left to decide that the request has not decided twice.

This matters because of the corpus's shape: **six of the eight actions grip with the right hand.** As a
veto that refused 20 of the 56 ordered pairs outright.

**An overlay contributes part of a clip, not all of it.** Assembly's unit used to be a whole clip hung
on a channel, so "walk while doing chest compressions" meant eighteen seconds of arm under a one-second
walk. `agent/segments.py` measures, per action and channel, the frames it is actually moving in — and
where the motion repeats, one repetition. Two clips in this corpus repeat: `cpr` every 18 frames on all
eight channels (0.00° residual against a 2–4° spread — thirty compressions) and `bvm`'s right hand every
89 (0.2° against 9.3). `grab_bottle` loses the six frames it spends holding still at the end. The rest
run edge to edge and are played whole. **The base is never cut** — it sets the posture everything else
hangs on. The model names an action and never sees a frame number; the window is measured for it.

**A composed motion plays while she is walking.** Every clip is in-place: the navigation agent moves the
transform and the composer plays the animation, side by side. So an overlay travels with the walk that
gets her there rather than waiting for it to finish — she is not doing it *while* walking if it starts
when the walking stops. On arrival the walk is replaced by a stance and the overlay carries on.

`probe_compose.py` is the coverage figure for all of this: **24 of the 56 ordered pairs compose** into
two sources both driving something. 18 are degenerate — one action ends up driving everything, which is
every `idle` pair — and are counted separately rather than folded into the total; 14 are refused on
posture, because `typing` is the only seated action. 14 of the 24 need a hand to let go of its object,
which the probe prints pair by pair.

## Running the gates

```sh
conda env create -f environment.yml && conda activate animation-agent   # or a bare python3
./check_kb.sh
```

Steps 1-3 need no engine. Step 4 resolves each action's `source_clip` guid to a real `AnimationClip`,
which only the `AssetDatabase` can do; it runs live when the bridge is up and otherwise falls back to the
last committed `_reports/kb_state.md`.

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
