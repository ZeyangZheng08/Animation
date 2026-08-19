# 0009 — a plan is played on a hidden duplicate before the visible character moves

Status: Accepted (2026-08-19), amended the same day — the version check is not fatal on every
channel, and one of the speakers was forgotten. Read the amendment at the end.

## Context

Every geometric check this project had was an autopsy. `GateProbe` samples the pose that is actually
being played, in `LateUpdate`, after the composer's graph and the rig's IK have written it — so the
verdict describes what a viewer has already seen. Three failures of that shape are on record:

- the first generated sit tracked its hip target to 3.5 mm and landed the character squatting in
  mid-air a metre and a half from the chair;
- a plan that named the laptop as `sit_on` put the pelvis 0.70 m under a surface it was reported to be
  sitting on, and passed containment because the laptop was directly overhead;
- worst for the walk: `plan_motion(walk_to=…)` walked her across the room FIRST and derived the motion
  she had crossed it for afterwards, so a plan that could not work was already visible — a nurse
  standing at a chair she cannot sit on is as much a failed plan on screen as a bad sit is.

`check_motion` existed and did not help. It cannot answer until the descent has run, which is seconds
after the commit and long after the model's next round trip; measured, every early call came back
`pending`, and "no failures" was read as "passed".

The composability gate, the posture gate and the channel partition all run agent-side before anything
reaches the engine. What could not run there is anything geometric: it needs the real skeleton, the
real masks, the real IK and the real room.

## Decision

**A commit is played through on a hidden duplicate of the character first, and only a pass reaches the
visible one.** Protocol v4 adds a third mode to `motion.assemble`, `validate`, and a `preview` to
`motion.locomote`.

    plan compiled once
        │
        ├── motion.locomote preview   → route + projected arrival + heading; NOTHING MOVES
        ├── motion.assemble validate  → the whole plan on the duplicate, at that arrival
        │        fail → ToolFailure naming the metric, the object and what to change
        └── motion.assemble commit    → the same bytes, now visibly

Four things make it work rather than merely exist:

- **One compile, two sends, the same bytes.** `AgentCharacter.Compile` produces a `CompiledPlan` from
  the request; `validate` and `commit` arrive as two requests carrying identical payloads. Deriving
  anything between the check and the play would put the gap back exactly where this closes it.
- **One judgement, two clocks.** `GateEvaluator` holds every threshold, accumulation and metric shape
  and knows nothing about frames; `GateProbe` feeds it real elapsed seconds, `ValidationCharacter`
  feeds it simulated ones. `GateArming` decides what to watch, for both. No threshold was added,
  changed, or invented by this change.
- **Fast, not real-time.** The duplicate's composer graph runs in `DirectorUpdateMode.Manual` and is
  stepped by hand; `PoseSynth` and `IkBinder` grew a `Step(dt)` so the descent's closed loop and the IK
  weight ramps advance on that clock too, and `PostureTransitionEvaluator` holds the descent curve both
  paths share. Measured on `EmergencyRoom`: 12.0 s of a walk-and-sit evaluated in 721 samples; the
  whole `validate` round trip is 40–160 ms.
- **The walk is inside the fence.** The route is computed with the static NavMesh queries and the
  arrival projected by walking the corner list back by the stopping distance — the agent is neither
  enabled nor moved nor given a destination. The motion is then judged standing THERE, because a sit
  judged from across the room fails a plan that works.

The model still sees two modes, `dry_run` and `commit`. `validate` is between the agent service and the
executor: it costs an engine round trip and no iteration of the model's own loop.

## Consequences

+ A plan that does not work is refused with nothing on screen — verified live: `typing` with the
  patient as `sit_on` comes back `sat_through_support on obj:Patient`, and the character has not moved.
+ A refusal names a metric, the object or effector it is about, and a reason slug the agent turns into
  one of four repairs (the motion, the target, the composition, the route). "It failed" used to send
  the model rewriting arguments that were already right.
+ The version bump is deliberately fatal. An executor from before this does not know the word
  `validate`, and `Apply` treated anything that was not `commit` as a dry run — so it would answer
  "resolved, touched nothing", which reads exactly like a pass, and a plan would commit on a check that
  never ran.
+ `GateProbe` is kept and its job changed: it now watches for the real scene doing something the
  duplicate could not know about — the seat moved, somebody else picked the thing up.
- **A carry is not checked.** Attaching the real prop to the duplicate's hand is exactly the visible
  mutation this exists to avoid, so `carry` comes back under `unmeasured` rather than under `checked`.
- **There is still no body-versus-scene collision metric.** There was not one before either. This
  refactor did not acquire one and must not be described as having done so.
- One skeleton per driven character, built on first use and destroyed with the character.
- A plan whose route is clear at check time can still be blocked by the time she walks it. That is what
  the runtime probe is for, and `plan_motion` says so rather than pretending the check was a guarantee.

## The measurement that made the design, recorded because it was not obvious

The first working version evaluated Animation Rigging's own `PlayableGraph` after the composer's, which
is how the two are composed at runtime. Under manual evaluation that is wrong: each `Evaluate` is a
full animation update for one graph alone, so the rig's pass replaced the composed pose wholesale.
Every sample came back byte-identical and about a metre low — `ground_penetration` 0.659 m against a
0.01 m tolerance, on a plain `idle`. Two plausible causes were rejected by measurement before the real
one was found; the fix is `RigBuilder.BuildPreviewGraph`, which splices the constraints INTO the
composer's graph, so one evaluation produces clips → masks → correction → IK in that order. Afterwards
the same probe reads feet at 0.0798 m and hips at 0.9011 m, which is the standing pose, and a `walking`
probe shows 0.062 m of hip travel over one cycle — the number that distinguishes "the graph advanced"
from "the clip held still".

Related: [0004](0004-mask-layer-disjoint-only.md) (what composition means here),
[0006](0006-peak-resilience-by-design.md) (degradation by design — an unavailable check is an error,
never a silent pass).

## Amendment (2026-08-19) — "fatal by design" was true of one channel and not the other

The decision above says the version bump is deliberately fatal, and gives the reason: an older
executor treats an unknown mode as a dry run, and "resolved, touched nothing" reads exactly like a
pass. That reasoning is sound and it turned out to describe only half the system.

**The contract has three speakers, not two.** `agent/protocol.py` is the authority and
`Assets/Scripts/AgentRuntime/Protocol.cs` mirrors it — that pair is written down everywhere. The third
is `terminal.py`, the console a person types into, which had the version number written into it as a
literal because it is standard-library-only and does not import the package. The bump updated two of
the three.

**And the two channels fail differently.** On the engine channel a mismatch raises on decode and the
link refuses to run: loud, immediate, exactly as designed. On the console channel `ConsoleServer`
logged the malformed line and dropped it — the right instinct about the session, since there is no
turn to fail, and the wrong one about the person, who saw a fresh prompt and no reason. So every
instruction typed into the Play-mode window was refused at the door, silently, and the service ran on
perfectly happily beside it.

It presented as an intermittent hang in the model — zero CPU, the socket to the model established and
idle, no trace line, `/stop` unable to reach it. Three sessions were spent there. It was deterministic,
it was one literal, and it never reproduced under investigation because the harness used to reproduce
it imports the contract and was therefore always right.

**What changed.** `terminal.py` reads `PROTOCOL_VERSION` out of `agent/protocol.py` instead of
remembering it, and falls back to reading the constant out of the source rather than to a number.
`ConsoleServer` answers a message it cannot read, down the socket the message came up. The line is
still refused; what is no longer true is that the refusal is invisible. Either fix alone would have
been enough, which is why both are there.

**The rule this leaves.** Bumping `PROTOCOL_VERSION` means changing three files, and the third one
does not import the first. Grep for the constant, not for the import. And when adding a channel, decide
where its mismatch surfaces before deciding that it is fatal — "fatal" that nobody can see is
indistinguishable from "ignored".