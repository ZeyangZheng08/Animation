# nursing_assets — frozen, referenced by no code

Eight hand-authored nursing actions (`bvm`, `check_pulse`, `cpr`, `giving_pills`, `grab_bottle`,
`idle`, `typing`, `walking`) and the evidence they were built from. They used to be the accepted half
of `agent/animation_knowledge_base/`; they were moved out here so that the formal MotionKB is exactly
the 2446 Mixamo clips and nothing else.

    actions/     the eight motionkb/v4 records, unchanged
    raw/         their per-frame pose dumps (nurse_bvm_2, nurse_check_pulse, nurse_cpr_30,
                 nurse_give_meds, nurse_grab_aspirin, Idle, Typing, Walk_N)
    frames/      the rendered evidence, one directory per clip, git-lfs
    derived/     the segment and seam tables that covered these eight and only these eight

**Nothing reads this directory.** Not the runtime, not the BM25 index, not the system prompt, not the
agent's search workspace, not the offline pipeline, not the gates, not the tests. It is kept as
material for a held-out nursing evaluation that does not exist yet: an evaluation whose clips were
ever visible to retrieval would not be held out. The FBX and `.anim` these were sampled from stay in
`Assets/Animations` — they are scene assets, and moving them would break the scenes that use them.

Nothing here is regenerated. `derived/segments.json` and `derived/transitions.json` are the tables as
they stood on the day of the move; the builders that wrote them now run over the 2446 and write into
the knowledge base instead. Treat the whole directory as an archive: read it, do not rebuild it.
