#!/usr/bin/env python3
"""
validate_motionkb.py - the non-Unity validation path for the MotionKB v4 data contract
(HANDOFF.md section 8 module A; ADR 0001/0007/0022). Stdlib only - no pip install.

It enforces TWO layers against every v4 action JSON:
  1. SHAPE: the JSON Schema motionkb.v4.schema.json (self-contained subset interpreter:
     type / const / enum / required / properties / additionalProperties (false or a schema) /
     items / $ref / minLength / minItems / minProperties / minimum / maximum, nullable via type
     lists and null-in-enum). Both channel definitions and the top level are
     `additionalProperties: false`, so a record still carrying a v3 field -- `role`, `contact`,
     `ik_goals`, `composability`, `mask_coverage`, `tags`, `display_name`, `overall_intent` -- FAILS
     here rather than passing with a field the contract no longer describes.
  2. INVARIANTS JSON Schema cannot express:
       - every `channels` key is a known channel in engine_mask_map.json (single channel vocabulary)
       - an accepted record carries its DESCRIPTIONS: a non-null `action_description` and a non-empty
         `motion_description` on each of the 8 anatomical channels. Only an explicit
         status='candidate' is exempt, because "measured, not yet described" is a real pipeline state
         (ADR 0014) -- and fail-closed, so a record with no status at all is held to the full bar.

This file used to be twice this size. Most of it gated the SEMANTIC 5-tuple against composability and
ik_goals: role==free iff the channel was composability.free, an ik_goal implying a constraint and an
object contact, motion_type against the measured state_label, plus the cross-file overlay
lock-disjointness and posture-compatibility passes. v4 deletes every field those checks read
(ADR 0022), so they are gone rather than reinterpreted -- there is no cross-field contradiction left
to find between two sentences of prose and a measured number.

SCOPE - this gate certifies that a record is WELL-FORMED and COMPLETE, not that its descriptions are
CORRECT. Whether a sentence actually describes the clip has no machine check here; the frames in
frames/ are the evidence, and `verified_against_screenshots` records whether a human looked.
KINEMATIC stays reproducible via test_golden_extraction.py.

NOT checked here (needs the engine): source_clip.guid resolving via AssetDatabase.GUIDToAssetPath —
that layer is validate_guids.py, which drives the AssetDatabase over the Unity MCP bridge.

Per-file failure isolation (module H): one bad file never aborts the batch; exit non-zero iff any failed.

Usage:
  python validate_motionkb.py                 # validate every record in the store, whatever its status
  python validate_motionkb.py -q              # same, but print only failures (the corpus is ~2400 files)
  python validate_motionkb.py <dir|file>...   # validate explicit dir/files
"""
import sys, os, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths                                                     # noqa: E402

KB_DIR = paths.KB_DIR                                            # see paths.py / MOTIONKB_DIR
SCHEMA_PATH = os.path.join(paths.SCHEMA_DIR, "motionkb.v4.schema.json")
ENGINE_MAP_PATH = paths.ENGINE_MASK_MAP

STATE_CHANNELS = ["root", "torso", "head", "left_arm", "right_arm", "left_leg", "right_leg", "left_hand", "right_hand"]
ANATOMICAL_CHANNELS = ["torso", "head", "left_arm", "right_arm", "left_leg", "right_leg", "left_hand", "right_hand"]


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
    if isinstance(value, dict) and "minProperties" in schema and len(value) < schema["minProperties"]:
        errors.append(f"{path}: object has {len(value)} properties, minProperties {schema['minProperties']}")
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
        extra = schema.get("additionalProperties", True)
        if extra is False:
            for k in value:
                if k not in props:
                    errors.append(f"{path}: unexpected field '{k}' (additionalProperties=false)")
        elif isinstance(extra, dict):
            # A schema rather than a flag: an open-keyed object whose VALUES are constrained. That is
            # what `mean_pose` is -- the keys are the engine's muscle DOF names and differ per
            # channel, so they cannot be enumerated, but every value must be a number.
            for k in value:
                if k not in props:
                    validate_shape(value[k], extra, root, f"{path}.{k}", errors)
        for k, sub in props.items():
            if k in value:
                validate_shape(value[k], sub, root, f"{path}.{k}", errors)


# ------------------------------- invariants -----------------------------------
def validate_invariants(data, errors, engine_channels):
    ch = data.get("channels", {})
    if isinstance(ch, dict) and engine_channels is not None:
        for name in ch:
            if name not in engine_channels:
                errors.append(f"channels.{name}: not a known channel in engine_mask_map.json {sorted(engine_channels)}")

    # The SEMANTIC half is required to ACCEPT a record, not to hold one. The schema lets action_id and
    # every description be null, because "measured, not yet described" is a real state: the bulk
    # corpus is registered and measured in one pass and proposed later. Requiring them in the schema
    # meant a KINEMATIC-complete record was a schema violation, so the gate could not tell an
    # undescribed record from a malformed one. Here the requirement is attached to the claim it
    # actually belongs to -- status. Fail-closed: only an explicit 'candidate' is exempt, so a record
    # with no status at all is still held to the full bar (ADR 0014).
    if data.get("status") != "candidate":
        if data.get("action_id") is None:
            errors.append("action_id is null, which only a status='candidate' record may be")
        validate_descriptions(data, errors, [])


def validate_descriptions(data, errors, warns):
    """The record says what it looks like, for the whole action and for each anatomical part.

    This is the COMPLETENESS gate, and it is the only semantic gate left. Through v3 this function's
    predecessor cross-checked a five-field label tuple against `composability`, `ik_goals` and the
    measured `state_label`; v4 deletes all three of those inputs (ADR 0022) and leaves prose, which
    has nothing to contradict. What can still be wrong is that a description is MISSING -- a channel
    the model skipped, or an empty string standing in for a sentence -- and a record accepted with a
    hole in it is one a retrieval pass silently cannot see.

    Status-agnostic on purpose: `propose.py` runs it against a fresh candidate as its retry gate, and
    the batch validator runs it only once a record claims to be more than a candidate.
    """
    if data.get("action_description") is None:
        errors.append("action_description is null")
    elif not str(data["action_description"]).strip():
        errors.append("action_description is blank")
    ch = data.get("channels")
    if not isinstance(ch, dict):
        errors.append("channels is not an object")
        return
    for c in ANATOMICAL_CHANNELS:
        f = ch.get(c)
        if not isinstance(f, dict):
            errors.append(f"channels.{c}: missing")
            continue
        d = f.get("motion_description")
        if d is None:
            errors.append(f"channels.{c}.motion_description is null")
        elif not str(d).strip():
            errors.append(f"channels.{c}.motion_description is blank")


# --------------------------------- driver -------------------------------------
def collect_files(args):
    """What to validate: the named files/directories, or the whole store.

    THE WHOLE store, always. This used to validate the staged candidates alone whenever any were
    staged, on the reasoning that candidates are what is being worked on -- but it meant the accepted
    records went unchecked for as long as anything sat in candidate/, and the run still printed a pass
    count. A field that the schema forbids (extraction.measurement_space) lived in all eight accepted
    records through several green runs because of it. A gate that stops covering the thing it guards,
    without saying so, is worse than no gate. One store (ADR 0016) removes the choice that made that
    possible: there is nothing to prefer, so nothing to skip.
    """
    if args:
        files = []
        for a in args:
            if os.path.isdir(a):
                files.extend(paths.action_files(a))
            else:
                files.append(a)
        return files
    return paths.action_files()


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

    rest = [a for a in argv[1:] if a not in ("-q", "--quiet")]
    quiet = len(rest) != len(argv[1:])
    files = collect_files(rest)
    if not files:
        print("FATAL: no MotionKB JSON files found")
        return 1

    errors_by_file, warns_by_file = {}, {}
    for fname, data, read_error in paths.read_records(files):
        try:
            short = os.path.relpath(fname, KB_DIR)
        except ValueError:
            short = fname  # path on another drive (e.g. a scratch test file) — don't abort the batch
        errors_by_file[short] = []
        warns_by_file[short] = []
        if read_error:
            errors_by_file[short].append(f"not valid JSON: {read_error}")
            continue
        validate_shape(data, schema, schema, "$", errors_by_file[short])
        validate_invariants(data, errors_by_file[short], engine_channels)

    # There is no cross-file pass any more. The one there was checked `can_overlay_on` against every
    # other record's `locks`, and both fields are gone (ADR 0022): whether two clips can be played
    # together is a question about a task and a scene, and no pair of records answers it between them.

    passed = failed = 0
    print(f"MotionKB validation - schema motionkb/v4 - {len(files)} file(s)"
          + ("  (quiet: failures only)\n" if quiet else "\n"))
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
            if not quiet:
                print(f"  PASS  {short}" + (f"   ({len(warns)} warning(s))" if warns else ""))
        if not quiet:
            for w in warns:
                print(f"          ~ warn: {w}")

    print(f"\n{passed} passed / {failed} failed"
          + ("" if failed else "  (guid->asset resolution needs the engine; run validate_guids.py for that layer)"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
