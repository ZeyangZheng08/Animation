# eval_8_actions — the retrieval eval for a library that no longer exists

`run_eval.py` scored retrieval and composition against `retrieval_eval_set.json`: twelve hand-written
cases over the eight nursing actions, naming the action_ids each request should retrieve and the
channel split each composition should resolve to.

**It does not run.** Every case names `walking`, `typing`, `cpr`, `grab_bottle` and their siblings,
and those eight records left the knowledge base for `agent/nursing_assets/` when the Mixamo corpus
became the whole of it. The eval set moved with them: it is in the Unity repository at
`agent/legacy/eval_8_actions/retrieval_eval_set.json`, next to the records it scores.

Kept rather than deleted because it is the measurement the composition work was done against, and the
numbers in the write-up came from it. Deleting it would leave those numbers with no method behind
them.

**A held-out nursing evaluation has still to be built.** These twelve cases are not it: they were
written against clips the retrieval could see, so they measure whether the partition is derived
correctly, not whether the library can be searched for something it was never shown. That evaluation
needs the eight records kept out of the index — which is now true, and is why they were moved rather
than deleted — and a case set written without reference to them.
