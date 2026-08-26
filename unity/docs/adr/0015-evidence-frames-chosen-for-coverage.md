# 0015 — Evidence frames are chosen to cover the clip, not to sit inside an "action window"

Status: Accepted (2026-08-21). Changes how `extract.py render` picks the times it shoots, which is
the only input ADR 0008 gives the labelling model. No record changed; the eight accepted clips were
re-rendered and their SEMANTIC halves left exactly as they were.

## Context

ADR 0008 makes a VLM propose the semantic half from rendered frames. Those frames are therefore the
entire visual evidence: whatever the pictures fail to show, no label can be based on. Three times
are shot from two angles, six images per clip.

Which three was never a decision anyone recorded. It accreted into `unity_sampler.select_fracs`: find
the ACTION WINDOW — the frames where the busiest effector's distance from the Hips sits in the top
40% of its range — and place three frames at 15/50/85% of it. The reasoning in its docstring is
plausible and was written down honestly: the idle transitions at a clip's ends are not the action, so
sampling inside the window puts all three pictures on the thing being labelled.

Measuring it says otherwise, and says it worst for exactly the actions this KB was built from.

`nurse_check_pulse` is 2.9 s: 13 frames ramping into a pose, 63 frames holding it, 10 ramping out.
The window is the hold, so the three frames landed at 21/50/80% — three photographs of one pose. The
labeller could not have told a held gesture from a still image, and nothing about how the hand
arrives at the wrist was ever shown. `nurse_cpr_30` had two of its three frames 0.0007 apart.

The right way to say this is one number. Take the pose distance between two frames to be the RMS over
the 95 normalised muscle DOF — the space `metric_formula_version` v2.2.0 already measures in, so a
difference here means what it means in `motion_magnitude` — and define the RADIUS of a selection as
the distance from the worst-covered frame of the clip to the nearest chosen frame. Radius is how much
of the motion no attached picture stands for. On `check_pulse` the old selection scored 0.4090, which
is larger than five of the eight clips' entire range of movement.

This matters now rather than earlier because `gpt-5.5` labelled the eight anyway, filling the gaps
from priors. The 2446-clip corpus is to be labelled by a local ~27B model, which has fewer priors to
fill gaps with, on clips whose content nobody has vetted.

## Decision

**Choose the frames that best represent the clip: minimise the radius.** That is the k-center
objective in muscle space, and it needs no notion of an "action window" — a held pose is covered by
one frame because one is enough for 63 identical ones, and the other two go where the clip actually
differs from it; a cycle gets its phases; three genuinely distinct poses get one frame each.

Solved by greedy farthest-point traversal (add the frame furthest from everything chosen), which is
the standard 2-approximation. Its result depends on where it starts and its guarantee does not, so
several starts are tried and the best radius kept — capped at 32 evenly spread frames, which matches
trying all *n* on seven of the eight clips and loses 7% on the eighth for a tenth of the work.

**`select_views` is untouched.** Which angles to shoot from is a different question and the measured
channel data still answers it well.

**Rendered frames are named `<view>_t<ordinal>_f<pct>.png`.** Frames chosen by pose can fall in the
same whole percent of a long clip, and the name was the percent alone — one file would have silently
overwritten another. The ordinal also fixes an ordering bug that was latent while every fraction was
≥ 15%: `propose` attaches `sorted(glob(...))` and tells the model to read the frames as a sequence,
and `_f21` sorts before `_f5`. Frame 0 now gets chosen when the opening pose is the only thing
representing it, so it would have started biting. Both name forms are read back.

## Consequences

Radius on the eight accepted clips, old → new:

| clip | old | new | | clip | old | new |
|---|---|---|---|---|---|---|
| check_pulse | 0.4090 | **0.0931** | | giving_pills | 0.3329 | 0.2365 |
| cpr | 0.0391 | 0.0178 | | typing | 0.3060 | 0.2381 |
| bvm | 0.1116 | 0.0446 | | walking | 0.1221 | 0.1206 |
| grab_bottle | 0.1798 | 0.1311 | | idle | 0.0081 | 0.0065 |

Better on all eight; mean 0.1886 → 0.1110. `check_pulse` now renders at 8/15/96% — reaching down,
the held pose, and standing clear — where it rendered one pose three times.

**k-center is worst-case by construction, so a lone stray frame can claim one of the three pictures.**
Measured on 120 randomly sampled corpus clips: one clip (`mx_Dancing_The_Twerk`) has a chosen frame
that is nearest to only 4 of its 456 frames, and that is a real brief extreme rather than a glitch.
The median smallest cluster is about 16% of the clip, so all three frames usually earn their place.
No trimming heuristic was added — an untested one would be a guess, and the measured rate is 1 in 120.

**Cost:** 0.44 s for a 600-frame clip, the worst case, against ~6 s for trying every start. Roughly
18 minutes of Python across the whole corpus, against engine render calls that dominate anyway.

**The eight accepted clips were re-rendered**, so their frames and filenames both change in git. Their
records did not: `render` writes only PNGs, and no semantic field was re-proposed. The frames that
produced the current labels are recoverable from history if a proposal ever has to be re-derived.

**`probe_frame0.py` loses its original reason to exist** — it was written because the action window
structurally excluded frame 0, so no frame 0 had ever been rendered for any action. It is kept for
what it still does: show a RANGE of opening frames at a chosen resolution, which is what an import
artefact at the head of a clip looks like.

## Alternatives considered

**Keep the window and choose for coverage inside it.** Measured: it fixes cpr (0.0391 → 0.0176), bvm
and typing, and leaves `check_pulse` at 0.4092 — no better than before — because on a held action the
window *is* the plateau. Mean 0.1682. The window was the defect, not the spacing within it.

**Maximise the spread between the chosen frames** (make the three pictures as different from each
other as possible). Measured mean 0.1229, close, but it asks the pictures to differ rather than to
stand for the clip, and the difference shows: it is worse than the old selector on `walking` (0.1369
vs 0.1221), and it costs a combinatorial search rather than a linear pass.

**Minimise mean distance instead of worst-case** (k-medoids). Robust to the stray frame above, but it
follows duration: a 63-frame hold with a 13-frame approach pulls all three frames back into the hold,
which is the defect this ADR exists to remove.

**Shoot more frames.** Six images per clip base64-expand to about 3.2 MB against a measured 8 MB
response ceiling, so there is room for a few more — but it costs VLM tokens per clip across 2446 clips
and does not answer *which* frames. Choosing better is worth more than choosing more, and the two are
independent if the count is ever raised.
