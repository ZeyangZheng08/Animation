#!/usr/bin/env python3
"""
validate_motionkb.py - the non-Unity validation path for the MotionKB v2 data contract
(HANDOFF.md section 8 module A; ADR 0001/0007). Stdlib only - no pip install.

It enforces TWO layers against every v2 action JSON:
  1. SHAPE: the JSON Schema motionkb.v2.schema.json (self-contained subset interpreter:
     type / const / enum / required / properties / additionalProperties / items / $ref /
     minLength / minItems / minimum / maximum, nullable via type lists and null-in-enum).
  2. INVARIANTS JSON Schema cannot express:
       - composability.locks and free PARTITION the 8 PARTITION_CHANNELS (disjoint, union == all 8)
       - overlay lock-disjointness: an action's locks must not intersect the locks of any base it
         lists in can_overlay_on (cross-file)
       - ik_goals.effector resolves to a real channel (left_foot/right_foot -> left_leg/right_leg)
       - every `channels` key is a known channel in engine_mask_map.json (single channel vocabulary)
       - posture compatibility: a seated action may not overlay-on a standing base (and vice versa)
       - semantic-consistency (fires only once a channel's 5-tuple is filled — the acceptance gate
         for VLM/human proposals; inert while semantic fields are seeded null): role==free iff the
         channel is composability.free; an ik_goal => constraint in {must-reach, must-maintain} & contact==object:<obj>;
         free => unconstrained; locked => constrained; motion_type vs measured state_label (no
         hold-static on dynamic, no reach/manipulate/cyclic-locomotion on static); cyclic-locomotion
         requires the root channel dynamic
       - soft WARN: mask_coverage.lower_body == false while a leg channel is dynamic
       - soft WARN: a channel whose state is dynamic but composability lists it free (occupied? semantic)

SCOPE — this gate certifies SELF-CONSISTENCY, NOT CORRECTNESS. Passing means the semantic labels do not
contradict each other, the MEASURED facts, or the schema; it does NOT mean the interpretation is the
"right"/intended one. The interpretive fields (role/motion_type/constraint) have no measurable ground
truth — e.g. a swinging arm labelled `free` and one labelled `support` BOTH pass — so never read an
all-pass run as "the semantics are verified correct"; it means "the semantics are self-consistent". Run-to-run VLM
variance lives in exactly these fields; MEASURED stays reproducible via test_golden_extraction.py.

NOT checked here (needs the engine): source_clip.guid resolving via AssetDatabase.GUIDToAssetPath —
that layer is validate_guids.py, which drives the AssetDatabase over the Unity MCP bridge.

Per-file failure isolation (module H): one bad file never aborts the batch; exit non-zero iff any failed.

Usage:
  python validate_motionkb.py                 # validate the KB's candidate/*.json, else the accepted store
  python validate_motionkb.py <dir|file>...   # validate explicit dir/files
"""
import sys, os, json, glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths                                                     # noqa: E402

KB_DIR = paths.KB_DIR                                            # see paths.py / MOTIONKB_DIR
CAND_DIR = paths.CAND_DIR
SCHEMA_PATH = os.path.join(paths.SCHEMA_DIR, "motionkb.v2.schema.json")
ENGINE_MAP_PATH = os.path.join(KB_DIR, "engine_mask_map.json")

STATE_CHANNELS = ["root", "torso", "head", "left_arm", "right_arm", "left_leg", "right_leg", "left_hand", "right_hand"]
PARTITION_CHANNELS = ["torso", "head", "left_arm", "right_arm", "left_leg", "right_leg", "left_hand", "right_hand"]
EFFECTOR_TO_CHANNEL = {"left_hand": "left_hand", "right_hand": "right_hand", "left_foot": "left_leg", "right_foot": "right_leg"}

# Semantic 5-tuple vocabulary (the schema enum-checks membership; these drive cross-field consistency).
ROLE_FREE = "free"
RELEVANT_ROLES = {"primary", "stabilizer", "support"}
MOVING_MOTION_TYPES = {"reach", "manipulate", "cyclic-locomotion"}
LEG_CHANNELS = ("left_leg", "right_leg")


def _any_leg_dynamic(doc):
    """Whether either leg channel is measured as moving. The precondition for cyclic-locomotion."""
    ch = doc.get("channels") or {}
    return any((ch.get(c) or {}).get("state_label") == "dynamic" for c in LEG_CHANNELS)


# ----------------------------- schema interpreter -----------------------------
def _json_types(value):
    if value is None:
        return {"null"}
    if isinstance(value, bool):
        return {"boolean"}
    if isinstance(value, int):
        return {"integer", "number"}
    if isinstance(value, float):
        return {"number"}
    if isinstance(value, str):
        return {"string"}
    if isinstance(value, list):
        return {"array"}
    if isinstance(value, dict):
        return {"object"}
    return set()


def _resolve_ref(ref, root):
    if not ref.startswith("#/"):
        raise ValueError("unsupported $ref: " + ref)
    node = root
    for token in ref[2:].split("/"):
        node = node[token]
    return node


def validate_shape(value, schema, root, path, errors):
    if "$ref" in schema:
        validate_shape(value, _resolve_ref(schema["$ref"], root), root, path, errors)
        return
    if "type" in schema:
        allowed = schema["type"]
        allowed = [allowed] if isinstance(allowed, str) else allowed
        if not (_json_types(value) & set(allowed)):
            errors.append(f"{path}: expected type {allowed}, got {sorted(_json_types(value)) or type(value).__name__}")
            return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']}, got {value!r}")
    if isinstance(value, str) and "minLength" in schema and len(value) < schema["minLength"]:
        errors.append(f"{path}: string shorter than minLength {schema['minLength']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} > maximum {schema['maximum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: array shorter than minItems {schema['minItems']}")
        if "items" in schema:
            for i, item in enumerate(value):
                validate_shape(item, schema["items"], root, f"{path}[{i}]", errors)
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}: missing required field '{req}'")
        if schema.get("additionalProperties", True) is False:
            for k in value:
                if k not in props:
                    errors.append(f"{path}: unexpected field '{k}' (additionalProperties=false)")
        for k, sub in props.items():
            if k in value:
                validate_shape(value[k], sub, root, f"{path}.{k}", errors)


# ------------------------------- invariants -----------------------------------
def validate_invariants(data, errors, engine_channels):
    comp = data.get("composability", {})
    if isinstance(comp, dict):
        locks = comp.get("locks"); free = comp.get("free")
        if isinstance(locks, list) and isinstance(free, list):
            sl, sf = set(locks), set(free)
            inter = sl & sf
            if inter:
                errors.append(f"composability: locks and free overlap on {sorted(inter)}")
            union = sl | sf
            if union != set(PARTITION_CHANNELS):
                missing = set(PARTITION_CHANNELS) - union
                extra = union - set(PARTITION_CHANNELS)
                msg = []
                if missing: msg.append(f"missing {sorted(missing)}")
                if extra: msg.append(f"unknown {sorted(extra)}")
                errors.append(f"composability: locks+free must partition the 8 partition channels ({'; '.join(msg)})")

    ch = data.get("channels", {})
    if isinstance(ch, dict) and engine_channels is not None:
        for name in ch:
            if name not in engine_channels:
                errors.append(f"channels.{name}: not a known channel in engine_mask_map.json {sorted(engine_channels)}")

    for g in data.get("ik_goals", []) or []:
        eff = g.get("effector")
        if eff not in EFFECTOR_TO_CHANNEL:
            errors.append(f"ik_goals: effector '{eff}' has no channel resolution")


def validate_overlay_disjointness(by_id, errors_by_file):
    # SCOPE: this is a MODEL-A (mask+layer overlay co-playback) check, NOT a universal invariant. It
    # validates a `can_overlay_on` declaration under the runtime co-playback model, where an overlay may
    # drive only channels a base leaves un-locked. The Phase-2 decomposition / channel-selection assembler
    # picks a source action PER channel and does NOT gate composition through can_overlay_on this way, so
    # this check gets retired/replaced when that assembler exists (see phase2-synthesis-architecture memo).
    locks_of = {aid: set(d.get("composability", {}).get("locks", [])) for aid, (f, d) in by_id.items()}
    posture_of = {aid: (d.get("composability", {}).get("posture", "standing")) for aid, (f, d) in by_id.items()}
    for aid, (fname, data) in by_id.items():
        comp = data.get("composability", {})
        for base in comp.get("can_overlay_on", []):
            if base not in by_id:
                errors_by_file[fname].append(f"composability.can_overlay_on: '{base}' is not a known action_id")
                continue
            clash = locks_of[aid] & locks_of[base]
            if clash:
                errors_by_file[fname].append(
                    f"composability: cannot overlay on '{base}' - both lock {sorted(clash)} (lock-disjointness)")
            if posture_of[aid] != posture_of[base]:
                errors_by_file[fname].append(
                    f"composability: posture mismatch overlaying on '{base}' ({posture_of[aid]} vs {posture_of[base]})")


def soft_warnings(data, warns):
    mc = data.get("mask_coverage", {})
    ch = data.get("channels", {})
    if isinstance(mc, dict) and mc.get("lower_body") is False and isinstance(ch, dict):
        for part in ("left_leg", "right_leg"):
            fact = ch.get(part)
            if isinstance(fact, dict) and fact.get("state_label") == "dynamic":
                warns.append(f"mask_coverage.lower_body=false but channels.{part} is dynamic")
    comp = data.get("composability", {})
    free = set(comp.get("free", []) or []) if isinstance(comp, dict) else set()
    if isinstance(ch, dict):
        for part in free:
            fact = ch.get(part)
            if isinstance(fact, dict) and fact.get("state_label") == "dynamic":
                warns.append(f"channels.{part} is dynamic but composability lists it free (occupied? confirm semantic locks)")


def validate_semantic_consistency(data, errors, warns):
    """Gate the SEMANTIC 5-tuple against the MEASURED block + ik_goals + composability.

    Fires on a partition channel only once its 5-tuple is filled (role != null) — so it is inert on
    candidates whose semantic fields are still seeded null, and becomes the acceptance gate the moment
    a VLM/human proposes values. It is a deterministic data-contract consistency check (NOT a runtime
    agent): the measured magnitudes, the orthogonal ik_goals, and the human-locked composability are the
    facts a semantic proposal must agree with (ADR 0002 / ADR 0008).
    """
    ch = data.get("channels", {})
    if not isinstance(ch, dict):
        return
    comp = data.get("composability", {})
    comp = comp if isinstance(comp, dict) else {}
    free = set(comp.get("free", []) or [])
    locks = set(comp.get("locks", []) or [])
    root = ch.get("root")
    ik_by_channel = {}
    for g in data.get("ik_goals", []) or []:
        c = EFFECTOR_TO_CHANNEL.get(g.get("effector"))
        if c:
            ik_by_channel[c] = g

    for c in PARTITION_CHANNELS:
        f = ch.get(c)
        if not isinstance(f, dict) or f.get("role") is None:
            continue  # semantic 5-tuple not filled yet -> pending, not gated
        role, mt = f.get("role"), f.get("motion_type")
        contact, constraint, target = f.get("contact"), f.get("constraint"), f.get("target")
        state, kind = f.get("state_label"), f.get("kind")
        has_ik = c in ik_by_channel
        in_free, in_locks = c in free, c in locks

        # role <-> composability relevance (role 'free' ~ Li et al. BPQ "Not Relevant").
        # NOTE: the author step DERIVES composability.locks/free from role (free iff role==free), so for
        # pipeline-produced data this pair is a TAUTOLOGY (cannot fail). It is kept as a cheap guard against
        # hand-edited JSON where locks/free and role were set inconsistently — it checks the derivation held,
        # not the VLM's proposal.
        if role == ROLE_FREE and not in_free:
            errors.append(f"channels.{c}: role=free but channel is not in composability.free (relevance contradiction)")
        if role in RELEVANT_ROLES and in_free:
            errors.append(f"channels.{c}: role={role} (relevant) but channel is in composability.free")

        # constraint <-> ik_goal / composability
        if in_free and constraint not in (None, "unconstrained"):
            errors.append(f"channels.{c}: free channel must have constraint=unconstrained, got {constraint!r}")
        if has_ik and constraint not in ("must-reach", "must-maintain"):
            errors.append(f"channels.{c}: has an ik_goal -> constraint must be 'must-reach' or 'must-maintain' "
                          f"(the effector is pinned to the object either way), got {constraint!r}")
        if in_locks and constraint == "unconstrained":
            errors.append(f"channels.{c}: locked channel must be constrained (must-maintain/must-reach), got 'unconstrained'")

        # contact <-> ik_goal.contact_object
        if has_ik:
            co = ik_by_channel[c].get("contact_object")
            if co is not None and contact != f"object:{co}":
                errors.append(f"channels.{c}: ik_goal contacts '{co}' -> contact must be 'object:{co}', got {contact!r}")

        # motion_type <-> measured state
        if state == "dynamic" and mt == "hold-static":
            errors.append(f"channels.{c}: state is dynamic but motion_type=hold-static")
        if state == "static" and mt in MOVING_MOTION_TYPES:
            errors.append(f"channels.{c}: state is static but motion_type={mt} implies movement")
        # Stepping is a LEG fact, not a root fact. The root channel says where the body went, and
        # whether a clip travels is a property the runtime converts either way: Locomotion.cs drives
        # a NavMeshAgent while an in-place walk plays, and root motion can equally be applied. The
        # KB's own `walking` is an in-place walk whose legs are cyclic-locomotion and whose body does
        # not move at all, so gating on the root would reject the clearest example of the label.
        if mt == "cyclic-locomotion" and not _any_leg_dynamic(data):
            errors.append(f"channels.{c}: motion_type=cyclic-locomotion but neither leg channel is dynamic")

        # soft signals (review nudges, not blockers)
        if role == "primary" and state == "static" and not has_ik:
            warns.append(f"channels.{c}: role=primary but static and no ik_goal (confirm it really drives the action)")
        if mt == "gaze" and c != "head":
            warns.append(f"channels.{c}: motion_type=gaze on a non-head channel")
        if mt == "manipulate" and kind != "hand":
            warns.append(f"channels.{c}: motion_type=manipulate on a non-hand channel ({kind})")
        if constraint == "must-reach" and target is None and not has_ik:
            sibling_hand = {"left_arm": "left_hand", "right_arm": "right_hand"}.get(c)
            if not (sibling_hand and sibling_hand in ik_by_channel):
                # an arm reaching is targeted by its own hand's ik_goal — only warn if nothing supplies a target
                warns.append(f"channels.{c}: constraint=must-reach but no target and no ik_goal to supply one")


# --------------------------------- driver -------------------------------------
def accepted_files():
    """The accepted store only (never candidate/) — what validate_guids.py resolves against."""
    return paths.action_files()


def collect_files(args):
    if args:
        files = []
        for a in args:
            if os.path.isdir(a):
                files.extend(paths.action_files(a))
            else:
                files.append(a)
        return files
    # BOTH stores, always. This used to return the candidates alone whenever any were staged, on the
    # reasoning that candidates are what is being worked on -- but it meant the accepted store went
    # unchecked for as long as anything sat in candidate/, and the run still printed a pass count.
    # A field that the schema forbids (extraction.measurement_space) lived in all eight accepted
    # records through several green runs because of it. A gate that stops covering the thing it
    # guards, without saying so, is worse than no gate.
    #
    # Deduped by path, candidates first so a staged file is reported before the record it will
    # replace.
    seen, files = set(), []
    for f in paths.action_files(CAND_DIR) + paths.action_files():
        if f not in seen:
            seen.add(f)
            files.append(f)
    return files


def main(argv):
    paths.require_kb()
    try:
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            schema = json.load(f)
    except Exception as e:
        print(f"FATAL: cannot load schema {SCHEMA_PATH}: {e}")
        return 1
    engine_channels = None
    try:
        with open(ENGINE_MAP_PATH, encoding="utf-8") as f:
            engine_channels = set(json.load(f).get("channels", {}).keys())
    except Exception as e:
        print(f"WARN: cannot load engine_mask_map.json ({e}); skipping channel-vocabulary check")

    files = collect_files(argv[1:])
    if not files:
        print("FATAL: no MotionKB JSON files found")
        return 1

    errors_by_file, warns_by_file, loaded = {}, {}, {}
    for fname in files:
        try:
            short = os.path.relpath(fname, KB_DIR)
        except ValueError:
            short = fname  # path on another drive (e.g. a scratch test file) — don't abort the batch
        errors_by_file[short] = []
        warns_by_file[short] = []
        try:
            with open(fname, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            errors_by_file[short].append(f"not valid JSON: {e}")
            continue
        validate_shape(data, schema, schema, "$", errors_by_file[short])
        validate_invariants(data, errors_by_file[short], engine_channels)
        validate_semantic_consistency(data, errors_by_file[short], warns_by_file[short])
        soft_warnings(data, warns_by_file[short])
        aid = data.get("action_id")
        if isinstance(aid, str):
            loaded[aid] = (short, data)

    validate_overlay_disjointness(loaded, errors_by_file)

    passed = failed = 0
    print(f"MotionKB validation - schema motionkb/v2 - {len(files)} file(s)\n")
    for short in errors_by_file:
        errs = errors_by_file[short]
        warns = warns_by_file.get(short, [])
        if errs:
            failed += 1
            print(f"  FAIL  {short}")
            for e in errs:
                print(f"          - {e}")
        else:
            passed += 1
            print(f"  PASS  {short}" + (f"   ({len(warns)} warning(s))" if warns else ""))
        for w in warns:
            print(f"          ~ warn: {w}")

    print(f"\n{passed} passed / {failed} failed"
          + ("" if failed else "  (guid->asset resolution needs the engine; run validate_guids.py for that layer)"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
