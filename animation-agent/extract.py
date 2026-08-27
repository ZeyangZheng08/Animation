#!/usr/bin/env python3
"""
extract.py — the MotionKB v4 extractor orchestration (pure Python; engine-decoupled).

Two steps, because the only engine-dependent part (sampling muscle clips) is isolated:

  1) python extract.py sample
       Reads each accepted <id>.json for its source_clip, generates the generic Unity pose-sampler C#
       (bone list from config.py) ONE CLIP AT A TIME, runs it over the Unity MCP execute_code bridge
       (the ONLY Unity touch), and writes the returned pose dump to <KB>/raw/<clip_name>.json.
       The dump crosses the HTTP transport as the snippet's return value — Unity writes nothing.
       `emit-sampler` writes the same C# to a file instead, for running by hand at an MCP client.

  2) python extract.py assemble
       Reads the raw pose dumps, computes the 9-channel KINEMATIC blocks (metrics.py), and writes them
       back into <KB>/actions/<key>.json — KINEMATIC authoritative, SEMANTIC preserved. Accepted records
       are left alone: their KINEMATIC half is frozen golden, and re-measuring them is a deliberate
       migration (recalibrate_kinematic.py), not a side effect of bringing in a new clip. Emits a
       run-log. Per-file isolated.

The KB itself lives in the Unity repository (it is a derivative of that project's animation assets);
this repo reaches it through paths.py / MOTIONKB_DIR.

  3) python extract.py migrate
       The v4 structural migration. Rewrites every record into the motionkb/v4 shape WITHOUT
       re-measuring: it renames `overall_intent` to `action_description`, drops the retired keys, and
       restamps the version/provenance lines. It exists because v4 changed no number — running the
       measure half to move a record's shape would reprocess 2454 frozen dumps to write back the
       values they already hold. Idempotent; safe on an already-v4 store.

KINEMATIC is program-generated and never fabricated (ADR 0002); the SEMANTIC half is now exactly two
descriptions — `action_description` and each anatomical channel's `motion_description` — and is
VLM-proposed in the propose stage (ADR 0008). controller_* is RESOLVED from the AnimatorController
(register/resolve-controller). Assemble seeds the descriptions as nulls — never guessed.

What a record does NOT hold, since v4 (ADR 0022): roles, motion types, contacts, constraints,
targets, ik_goals, mask_coverage, tags, a display_name, and the whole composability block. Each of
those was a composition decision dressed as a property of a clip, and the runtime agent — which can
see the task and the scene — is where they belong.
"""
import datetime
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C
import metrics
import paths
import unity_sampler

KB_DIR = paths.KB_DIR                          # see paths.py / MOTIONKB_DIR
ACTIONS_DIR = paths.ACTIONS_DIR
REPORT = os.path.join(paths.REPORTS_DIR, "extract_run.md")
SAMPLER_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_generated_sampler.cs")

# The KINEMATIC half of a channel. Not every key is on every channel: `mean_pose` is the anatomical
# channels' and the root's two carriage means are the root's, so a key absent from a block is
# removed from the record rather than left behind.
KINEMATIC_KEYS = ("state_label", "motion_magnitude", "raw_measurement", "mean_pose",
                  "mean_body_height", "mean_body_tilt_deg")
# Keys the contract used to carry here (the v2.3.0-v2.5.0 posture triple, ADR 0021 deleted them;
# `kind`, which restated the channel name it was keyed by). A re-measure drops them, so a record
# cannot keep a field no formula produces any more.
RETIRED_KINEMATIC_KEYS = ("posture_label", "posture_magnitude", "posture_measurement", "kind")
# The whole SEMANTIC half of a channel in v4: one sentence about how the part moves.
SEMANTIC_CH_KEYS = ("motion_description",)
# The four the channel carried alongside it until v4, plus `target`. Every one of them answered a
# question about a COMPOSITION — may another action drive this part, what does it hold, must the
# effector arrive somewhere — that no clip can answer by itself (ADR 0022). Listed here, not merely
# stopped being written, so that migrating a v3 record DELETES them.
RETIRED_SEMANTIC_CH_KEYS = ("role", "motion_type", "contact", "constraint", "target")
# The same at the top level. `overall_intent` is not here: it is RENAMED, see _apply_v4_shape.
RETIRED_TOP_KEYS = ("display_name", "tags", "mask_coverage", "ik_goals", "composability")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_BY_CLIP = None


def _by_clip():
    """{clip_name: record path} for the whole store, built once per process.

    Three lookups here used to walk the store and json.load every file to find one clip. Since the
    corpus landed that is 2454 opens apiece and 68 s each over the DrvFs mount the KB is reached
    through; one concurrent pass, held for the run, costs 6 s.
    """
    global _BY_CLIP
    if _BY_CLIP is None:
        _BY_CLIP = paths.records_by_clip_name()
    return _BY_CLIP


def _record_path(clip_name):
    """Where this clip's record lives, or where it would live if it has none yet. A record is named by
    its key: <clip_name>.json until PROPOSE decides an action_id, <action_id>.json after (ADR 0016)."""
    return _by_clip().get(clip_name) or os.path.join(ACTIONS_DIR, clip_name + ".json")


def _source_files():
    """Every record the MEASURE half iterates, in clip-name order. Working artifacts -- the raw dumps,
    the frames directories, the sampler itself -- are keyed by clip_name, and the action_id is not
    decided until PROPOSE, so this half works in clip names throughout."""
    return [p for _, p in sorted(_by_clip().items())]


def _load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _atomic_write(path, obj):
    paths.write_json(path, obj)


def _clips_to_sample():
    """(id, guid, file_id) for every source entry, keyed by clip_name — one sampler call each."""
    out = []
    for p in _source_files():
        sc = _load(p)["source_clip"]
        out.append({"id": sc["clip_name"], "guid": sc["guid"], "file_id": sc["file_id"]})
    return out


# ---------------------------------------------------------------------------------------------
def emit_sampler():
    """Write the generated sampler C# for the FIRST clip to a file, for running by hand at an
    MCP-connected client. Sampling is per-clip now, so this is a debugging aid; `sample` posts the
    snippet for every clip itself and needs no file."""
    clips = _clips_to_sample()
    if not clips:
        print("No source entries found under %s" % paths.rel(ACTIONS_DIR))
        return 1
    unity_sampler.emit_sampler_file(clips[0], SAMPLER_OUT)
    print("Wrote the sampler C# for '%s' (1 of %d clips) ->\n  %s" % (clips[0]["id"], len(clips), SAMPLER_OUT))
    print("It RETURNS the pose dump as its result (it writes nothing). Use 'extract.py sample' to run")
    print("every clip and persist the dumps to %s/<clip_name>.json." % paths.rel(paths.RAW_DIR))
    return 0


# ---------------------------------------------------------------------------------------------
def _seed_channel(desc):
    return {"motion_description": desc if desc else None}


def _migrate_from_v1(v1, raw):
    bp = v1.get("body_parts", {})

    def desc(part):
        return (bp.get(part, {}) or {}).get("motion_description", "") or ""

    channels = {}
    channels[C.TORSO] = _seed_channel(desc("chest"))
    channels[C.HEAD] = _seed_channel(desc("head"))
    channels[C.LEFT_ARM] = _seed_channel(desc("left_arm"))
    channels[C.RIGHT_ARM] = _seed_channel(desc("right_arm"))
    leg_desc = " ".join(x for x in (desc("legs"), desc("feet")) if x)
    channels[C.LEFT_LEG] = _seed_channel(leg_desc)
    channels[C.RIGHT_LEG] = _seed_channel(leg_desc)
    channels[C.LEFT_HAND] = _seed_channel("")
    channels[C.RIGHT_HAND] = _seed_channel("")

    return {
        "schema_version": C.SCHEMA_VERSION,
        "action_id": v1.get("action_id"),
        "status": "candidate",
        "source_clip": v1.get("source_clip"),
        "controller_state": v1.get("controller_state"),
        "controller_layer": v1.get("controller_layer"),
        "trigger_param": v1.get("trigger_param"),
        "duration": round(raw["length"], 3),
        "frame_rate": raw["frame_rate"],
        "loop": v1.get("loop"),
        "action_description": v1.get("overall_intent"),
        "channels": channels,
        "extraction": {},
    }


def _apply_v4_shape(doc):
    """Bring one record to the motionkb/v4 SHAPE: rename `overall_intent`, drop every retired key.

    Separate from `_apply_kinematic` because it touches no number and needs no dump — it is what
    `migrate` runs over the store, and what `assemble`/`recalibrate` also run so that a record cannot
    come out of a measure still carrying a field the contract dropped. Idempotent.

    The rename is done in place rather than as a delete-then-add so that `action_description` keeps
    `overall_intent`'s position in the file: these records are read as text and diffed, and moving a
    line that did not change is noise in a 2454-file rewrite.
    """
    if "overall_intent" in doc:
        doc = _rename_key(doc, "overall_intent", "action_description")
    doc.setdefault("action_description", None)
    for k in RETIRED_TOP_KEYS:
        doc.pop(k, None)
    for name, block in (doc.get("channels") or {}).items():
        if not isinstance(block, dict):
            continue
        for k in RETIRED_SEMANTIC_CH_KEYS:
            block.pop(k, None)
        if name == C.ROOT:
            for k in SEMANTIC_CH_KEYS:
                block.pop(k, None)
        else:
            for k in SEMANTIC_CH_KEYS:
                block.setdefault(k, None)
    return doc


def _rename_key(d, old, new):
    """A copy of `d` with `old` renamed to `new`, keeping its position among the keys."""
    out = {}
    for k, v in d.items():
        out[new if k == old else k] = v
    d.clear()
    d.update(out)
    return d


def _apply_kinematic(doc, blocks):
    """Overwrite the KINEMATIC half of every channel; leave the SEMANTIC half exactly as it was.

    Keys that already exist are ASSIGNED IN PLACE rather than removed and re-added, because a Python
    dict keeps insertion order and the records are read as text: re-adding would reorder every
    channel in 2454 files and bury the values that actually changed. New keys land at the end of the
    channel, retired ones are dropped.

    This is also where a record's `schema_version` is stamped, because the KINEMATIC half is what
    the contract version describes: a record cannot be rewritten by this formula and still claim the
    contract of the one before it.
    """
    doc["schema_version"] = C.SCHEMA_VERSION
    ch = doc.setdefault("channels", {})
    for name in C.STATE_CHANNELS:
        existing = ch.get(name) or {}
        block = blocks[name]
        for k in RETIRED_KINEMATIC_KEYS:
            existing.pop(k, None)
        for k in KINEMATIC_KEYS:                      # overwrite kinematic
            if k in block:
                existing[k] = block[k]
            else:
                existing.pop(k, None)                 # not a key this channel carries
        ch[name] = existing
    _apply_v4_shape(doc)                              # drop retired keys, seed the description stubs


SEMANTIC_FIELDS = ["action_description", "channels.*.motion_description"]


def _field_origin(doc):
    """The provenance tiers for one record. `semantic` lists the two description fields once they are
    filled and `semantic_pending` lists them while they are not — which, on the 2446 measured-but-
    unproposed corpus records, is the honest state. The v3 tiers named six more fields per channel;
    v4 has two, because that is all a record holds (ADR 0022)."""
    ch = doc.get("channels") or {}
    described = (doc.get("action_description") is not None
                 and all((ch.get(c) or {}).get("motion_description") is not None
                         for c in C.ANATOMICAL_CHANNELS))
    tiers = {
        "kinematic": ["duration", "frame_rate", "channels.*.state_label",
                      "channels.*.motion_magnitude", "channels.*.raw_measurement",
                      "channels.*.mean_pose", "channels.root.mean_body_height",
                      "channels.root.mean_body_tilt_deg"],
        "resolved": ["controller_state", "controller_layer", "trigger_param"],
    }
    tiers["semantic" if described else "semantic_pending"] = list(SEMANTIC_FIELDS)
    return tiers


def _build_extraction(raw, doc=None):
    return {
        "method": "in_engine_sample_root_local",
        "sampled_frames": raw["frames"],
        "sampling_rule": "clamp(round(duration*frame_rate),2,600), native rate, HumanPose muscles + bodyPosition/bodyRotation",
        # Derived from config, never retyped. This string is the record's own account of how its
        # numbers were produced; a hardcoded copy went stale the moment the divisors were refitted,
        # and every record then claimed a formula it was not computed with. Deriving it also means a
        # signal that disappears from config (root_gait did, in ADR 0011) fails loudly here instead
        # of leaving the description quietly wrong.
        "motion_metric": ("variation, all 8 anatomical channels: muscle_dof_stddev_rms, divided by "
                          "torso %(torso)s / head %(head)s / arm %(arm)s / leg %(leg)s / hand %(hand)s; "
                          "root variation: max(trans/%(trans)s, vert/%(vert)s, heading/%(heading)s) "
                          "from HumanPose.bodyPosition and bodyRotation; mean pose, all 8 "
                          "anatomical channels: mean_pose, the per-frame mean of each of the "
                          "channel's Humanoid muscle degrees of freedom in Unity's normalised "
                          "muscle space, stored as the vector it is and compared with nothing; root "
                          "mean pose: mean_body_height (mean HumanPose.bodyPosition.y, normalised "
                          "humanoid units) and mean_body_tilt_deg (mean angle of bodyRotation's up "
                          "axis from world up)"
                          % {"torso": C.DIVISOR[C.TORSO], "head": C.DIVISOR[C.HEAD],
                             "arm": C.DIVISOR["arm"], "leg": C.DIVISOR["leg"],
                             "hand": C.DIVISOR["hand"], "trans": C.DIVISOR["root_trans"],
                             "vert": C.DIVISOR["root_vert"], "heading": C.DIVISOR["root_heading"]}),
        "bone_map_version": C.BONE_MAP_VERSION,
        "metric_formula_version": C.FORMULA_VERSION,
        "extractor_version": C.EXTRACTOR_VERSION,
        "extractor_lang": "python",
        "avatar": C.CALIBRATION_AVATAR,
        "extracted_at": _now(),
        "field_origin": _field_origin(doc or {}),
        "verified_against_screenshots": False,
    }


def assemble():
    os.makedirs(ACTIONS_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    rows, ok, fail, skipped = [], 0, 0, 0
    rows.append("# MotionKB v4 extraction run — " + _now())
    rows.append("")
    rows.append("| clip | frames | torso | head | l_arm | r_arm | l_leg | r_leg | l_hand | r_hand | root |")
    rows.append("|---|---|---|---|---|---|---|---|---|---|---|")

    for p, rec, read_error in paths.read_records(_source_files()):
        if read_error:
            rows.append("| %s | ERROR: %s |" % (paths.rel(p), read_error))
            fail += 1
            continue
        if rec.get("status") == "accepted":
            # Frozen golden. One store (ADR 0016) means assemble now walks the accepted records too,
            # and writing them here would silently re-measure the eight the KB is built from -- what
            # recalibrate_kinematic.py exists to do deliberately, with a dry run and a report.
            skipped += 1
            continue
        key = rec["source_clip"]["clip_name"]         # working key = clip name, NOT action_id
        try:
            raw = unity_sampler.read_raw(key)
            blocks = metrics.channel_blocks(raw)
            # v1 records are the ones with a `body_parts` block; everything else is already
            # channel-shaped and is brought up to the current contract in place by
            # `_apply_kinematic`. Keying this on "is not the current schema_version" broke the
            # moment the version bumped — a v2 record went down the v1 migration, which reads a
            # `body_parts` block it does not have.
            doc = _migrate_from_v1(rec, raw) if "body_parts" in rec else rec
            doc["duration"] = round(raw["length"], 3)
            doc["frame_rate"] = raw["frame_rate"]
            _apply_kinematic(doc, blocks)
            doc["extraction"] = _build_extraction(raw, doc)
            _atomic_write(p, doc)
            m = lambda c: doc["channels"][c]["motion_magnitude"]
            rows.append("| %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                key, raw["frames"], m(C.TORSO), m(C.HEAD), m(C.LEFT_ARM), m(C.RIGHT_ARM),
                m(C.LEFT_LEG), m(C.RIGHT_LEG), m(C.LEFT_HAND), m(C.RIGHT_HAND), m(C.ROOT)))
            ok += 1
        except FileNotFoundError:
            rows.append("| %s | NO raw (run emit-sampler + sample first) |" % key)
            fail += 1
        except Exception as e:  # per-file isolation
            rows.append("| %s | ERROR: %s |" % (key, e))
            fail += 1

    rows.append("")
    rows.append("**%d ok / %d failed / %d accepted and left alone** -> actions/ (KINEMATIC "
                "authoritative; action_description + the 8 motion_descriptions are PENDING semantic). "
                "Re-measuring an accepted record is recalibrate_kinematic.py's job."
                % (ok, fail, skipped))
    report = "\n".join(rows) + "\n"
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    return 0 if fail == 0 else 1


def sample(host, port, instance, only=None):
    """Drive the one in-engine step, ONE CLIP PER CALL: generate the sampler C#, run it in the live Unity
    editor over the MCP HTTP bridge, and write the RETURNED pose dump into the KB. Unity writes nothing —
    the payload crosses the transport (see unity_sampler). Per-clip so one failure costs one clip, and so
    each response stays far inside the 8 MB ceiling. `only` restricts the run to a single clip_name."""
    clips = _clips_to_sample()
    if only:
        clips = [c for c in clips if c["id"] == only]
        if not clips:
            print("No source entry with clip_name '%s'." % only)
            return 1
    if not clips:
        print("No source entries found under %s" % paths.rel(ACTIONS_DIR))
        return 1
    if not unity_sampler.bridge_healthy(host, port):
        print("Unity MCP bridge not reachable at %s:%d.\n"
              "Open the Unity project and start the MCP server on HTTP (port %d) first." % (host, port, port))
        return 1

    print("Sampling %d clip(s) in Unity via %s:%d (execute_code, one call each) ..." % (len(clips), host, port))
    failed = []
    for c in clips:
        cs = unity_sampler.build_sampler_csharp(c)
        ok, result_text, _ = unity_sampler.run_csharp_over_http(cs, host=host, port=port, instance=instance)
        if not ok or result_text.startswith("ERROR:"):
            print("  FAIL  %-22s %s" % (c["id"], result_text.strip()[:160]))
            failed.append(c["id"]); continue
        try:
            out, dump = unity_sampler.write_raw(c["id"], result_text)
        except ValueError as e:      # malformed/truncated response — never let it land in the KB
            print("  FAIL  %-22s unparseable dump (%s), %d chars" % (c["id"], e, len(result_text)))
            failed.append(c["id"]); continue
        print("  ok    %-22s %d frames, %d bones -> %s"
              % (c["id"], dump.get("frames", 0), len(dump.get("bones") or {}), paths.rel(out)))
    unity_sampler.close_connections()
    print("\n%d sampled / %d failed" % (len(clips) - len(failed), len(failed)))
    return 1 if failed else 0


def _parse_bridge_flags(rest):
    host, port, instance = unity_sampler.DEFAULT_HOST, unity_sampler.DEFAULT_PORT, None
    i = 0
    while i < len(rest):
        if rest[i] == "--host" and i + 1 < len(rest):
            host = rest[i + 1]; i += 2
        elif rest[i] == "--port" and i + 1 < len(rest):
            port = int(rest[i + 1]); i += 2
        elif rest[i] == "--instance" and i + 1 < len(rest):
            instance = rest[i + 1]; i += 2
        else:
            i += 1
    return host, port, instance


def _new_source_stub(clip_name, fbx_or_anim, guid, file_id):
    """A minimal valid candidate skeleton with source_clip filled. KINEMATIC comes from assemble; the
    descriptions (and action_id) from the propose stage; controller_* from register/resolve-controller.
    Nothing here is a placeholder for a judgement: a v4 record has no field to guess at."""
    channels = {ch: {} for ch in C.STATE_CHANNELS}
    return {
        "schema_version": C.SCHEMA_VERSION,
        "action_id": None, "status": "candidate",
        "source_clip": {"fbx_or_anim": fbx_or_anim, "guid": guid, "file_id": int(file_id), "clip_name": clip_name},
        "controller_state": None, "controller_layer": None, "trigger_param": None,
        "duration": None, "frame_rate": None, "loop": None,
        "action_description": None,
        "channels": channels,
        "extraction": {},
    }


def register(clip_name, host, port, instance):
    """Resolve a clip BY NAME in Unity (guid + file_id) and scaffold actions/<clip_name>.json with
    source_clip filled — removes the manual file_id step from 'add a new action'."""
    existing = _by_clip().get(clip_name)
    if existing:
        print("'%s' already has a record at %s." % (clip_name, paths.rel(existing))); return 1
    cand_path = os.path.join(ACTIONS_DIR, clip_name + ".json")
    if not unity_sampler.bridge_healthy(host, port):
        print("Unity MCP bridge not reachable at %s:%d (open Unity + start the MCP HTTP server)." % (host, port)); return 1
    ok, result_text, _ = unity_sampler.run_csharp_over_http(
        unity_sampler.build_find_clip_csharp(clip_name), host=host, port=port, instance=instance)
    if not ok:
        print("Unity error:", result_text); return 1
    matches = [ln for ln in (result_text or "").splitlines() if "|" in ln]
    if not matches:
        print("No AnimationClip named '%s' found under Assets/Animations." % clip_name); return 1
    if len(matches) > 1:
        print("Multiple clips named '%s' — disambiguate (rename the clip, or register by hand):" % clip_name)
        for m in matches:
            print("   ", m)
        return 1
    path, guid, file_id = matches[0].split("|")
    os.makedirs(ACTIONS_DIR, exist_ok=True)
    _atomic_write(cand_path, _new_source_stub(clip_name, os.path.basename(path), guid, file_id))
    print("registered '%s':  fbx_or_anim=%s  guid=%s  file_id=%s" % (clip_name, os.path.basename(path), guid, file_id))
    # Best-effort: if the clip is already wired into a controller, fill controller_* now; else leave blank.
    wiring = _run_resolve(clip_name, host, port, instance)
    if wiring and len(wiring) == 1:
        s, l, t = wiring[0]
        stub = _load(cand_path)
        stub["controller_state"], stub["controller_layer"], stub["trigger_param"] = s, l, t
        _atomic_write(cand_path, stub)
        print("  controller wiring resolved: state=%s layer=%s trigger=%s" % (s, l, t))
    else:
        print("  controller_* left blank (not wired%s) — re-run `resolve-controller %s` after wiring it."
              % (" / ambiguous" if wiring else "", clip_name))
    print("  wrote %s — next: sample -> assemble -> render -> propose -> author "
          "(the descriptions come from propose; controller_* handled above)"
          % paths.rel(cand_path))
    return 0


def _doc_path_by_clip(clip_name):
    """Path to the record whose source_clip.clip_name == clip_name, or None."""
    return _by_clip().get(clip_name)


def _run_resolve(clip_name, host, port, instance):
    """Run the controller-wiring lookup in Unity; return a sorted list of unique (state, layer, trigger)
    tuples (trigger '' -> None), or None on a transport/Unity error."""
    ok, result_text, _ = unity_sampler.run_csharp_over_http(
        unity_sampler.build_resolve_controller_csharp(clip_name), host=host, port=port, instance=instance)
    if not ok:
        print("Unity error:", result_text)
        return None
    seen = {}
    for ln in (result_text or "").splitlines():
        if "|" not in ln:
            continue
        parts = ln.split("|")
        state, layer = parts[0], parts[1]
        trig = parts[2] if len(parts) > 2 and parts[2] != "" else None
        seen[(state, layer, trig)] = True
    return sorted(seen, key=lambda t: (t[0], t[1], t[2] or ""))


def resolve_controller(clip_name, host, port, instance):
    """Fill controller_state/layer/trigger_param from how this clip is wired into a Unity AnimatorController
    (deterministic typed lookup, the program counterpart of `register`). Unwired -> all three null (blank),
    by design; ambiguous (>1 distinct wiring) -> report and leave unchanged."""
    path = _doc_path_by_clip(clip_name)
    if not path:
        print("No action entry with clip_name '%s' (register it first)." % clip_name)
        return 1
    if not unity_sampler.bridge_healthy(host, port):
        print("Unity MCP bridge not reachable at %s:%d (open Unity + start the MCP HTTP server)." % (host, port))
        return 1
    matches = _run_resolve(clip_name, host, port, instance)
    if matches is None:
        return 1
    doc = _load(path)
    if not matches:
        doc["controller_state"] = doc["controller_layer"] = doc["trigger_param"] = None
        _atomic_write(path, doc)
        print("'%s' is not wired into any AnimatorController under Assets/Animations -> controller_* left blank."
              % clip_name)
        return 0
    if len(matches) > 1:
        print("'%s' is wired in >1 distinct way — disambiguate by hand (left unchanged):" % clip_name)
        for s, l, t in matches:
            print("    state=%s layer=%s trigger=%s" % (s, l, t))
        return 1
    state, layer, trig = matches[0]
    doc["controller_state"], doc["controller_layer"], doc["trigger_param"] = state, layer, trig
    _atomic_write(path, doc)
    print("resolved '%s': controller_state=%s  controller_layer=%s  trigger_param=%s  -> %s"
          % (clip_name, state, layer, trig, paths.rel(path)))
    return 0


def _clip_by_name(clip_name):
    """Find the source entry whose source_clip.clip_name == clip_name; return {id, guid, file_id}."""
    p = _by_clip().get(clip_name)
    if not p:
        return None
    sc = _load(p).get("source_clip") or {}
    return {"id": clip_name, "guid": sc["guid"], "file_id": sc["file_id"]}


def _quiet(_msg):
    """A `log` sink that drops the line. The batch caller prints one line per clip, not thirty."""


def render_frames(clip, clip_name, host, port, instance, log=_quiet):
    """Render the eight-view ring of one clip into <KB>/frames/<clip_name>/. Returns
    (written_paths, error_or_None); an empty list with no error means Unity returned nothing readable.

    This is the shared body of the curated `render` verb and the corpus batch
    (`ingest_corpus.py render`), for the same reason the KINEMATIC block has one implementation: two
    callers rendering "the frame set" must not drift into two dialects of what a frame set is. `log`
    receives the per-step lines; the batch passes a sink that drops them."""
    out_dir = os.path.join(paths.FRAMES_DIR, clip_name)
    # WHICH ANGLES is no longer a decision: every clip gets all eight (unity_sampler.view_ring). The raw
    # dump is still read, for two things it alone can answer -- which way the avatar faces (so `front` is
    # the face) and which three moments represent the clip.
    views = unity_sampler.RENDER_VIEWS
    fracs = unity_sampler.RENDER_FRACS
    raw_path = os.path.join(paths.RAW_DIR, clip_name + ".json")
    if os.path.exists(raw_path):
        try:
            with open(raw_path, encoding="utf-8") as f:
                raw = json.load(f)
            views = unity_sampler.view_ring(raw.get("root_fwd"))
            fracs = unity_sampler.select_fracs(raw)
            log("  times (pose coverage): %s" % ", ".join("%d%%" % int(f * 100) for f in fracs))  # int() = the C# frame-filename convention
        except Exception as e:  # never let facing/time selection break the render
            log("  (could not derive facing/times: %s — using the default-facing ring and fixed times)" % e)
    else:
        log("  no raw/%s.json — default-facing ring and fixed times (run `sample` first)" % clip_name)
    log("  views: %s" % ", ".join(n for n, _ in views))
    # Clear stale frames so a re-render doesn't leave old-named images (incl. pre-JPEG PNGs) for `propose`.
    stale = []
    for suf in unity_sampler.FRAME_SUFFIXES:
        stale += glob.glob(os.path.join(out_dir, "*" + suf)) + glob.glob(os.path.join(out_dir, "*" + suf + ".meta"))
    for old in stale:
        try:
            os.remove(old)
        except OSError:
            pass
    batches = unity_sampler.view_batches(views, fracs)
    log("Rendering '%s' via %s:%d (%d view(s) x %d time(s) in %d call(s)) ..."
        % (clip_name, host, port, len(views), len(fracs), len(batches)))
    written = []

    def _save(grp, text):
        got = unity_sampler.write_frames(clip_name, text)
        written.extend(got)
        log("  %s: %d frame(s)" % (", ".join(n for n, _ in grp), len(got)))

    ok, result_text = unity_sampler.render_clip_frames(
        clip, views, fracs, host=host, port=port, instance=instance, on_batch=_save)
    if not ok:
        return written, (result_text or "").strip()[:400] or "Unity returned no text"
    return written, None


def render(clip_name, host, port, instance):
    """Render the eight-view ring of one clip into <KB>/frames/<clip_name>/ (kept for review). The JPEGs
    come back base64 over the transport, in view batches; Unity writes nothing."""
    clip = _clip_by_name(clip_name)
    if not clip:
        print("No source entry with clip_name '%s'." % clip_name)
        return 1
    if not unity_sampler.bridge_healthy(host, port):
        print("Unity MCP bridge not reachable at %s:%d (open Unity + start the MCP HTTP server)." % (host, port))
        return 1
    written, err = render_frames(clip, clip_name, host, port, instance, log=print)
    if err:
        print("Unity reported an error:\n%s" % err)
        return 1
    for p in written:
        print("  %s" % paths.rel(p))
    print("frames saved: %d -> %s" % (len(written), os.path.join(paths.FRAMES_DIR, clip_name)))
    return 0 if written else 1


def propose(clip_name, host, port, instance, stage=False):
    """Propose one clip: ensure frames (render if missing) -> the VLM writes the action_description
    and the 8 motion_descriptions -> they are written back into the record. A NAMED record is then
    accepted by default (`vlm_accepted`, no human required); `--stage` holds it at `candidate` for
    optional human review (`author` then blesses it as `human_accepted`). An UNNAMED record also
    stays `candidate`: describing does not name (ADR 0022), so acceptance waits for an action_id."""
    src = _doc_path_by_clip(clip_name)
    if not src:
        print("No source entry with clip_name '%s'." % clip_name)
        return 1
    frames_dir = os.path.join(paths.FRAMES_DIR, clip_name)
    if not unity_sampler.frame_paths(frames_dir):
        print("No frames yet for '%s' — rendering first." % clip_name)
        if render(clip_name, host, port, instance) != 0:
            return 1
    import propose as _propose
    print("Asking the VLM (%s) to describe '%s' ..." % (_propose.VLM_MODEL, clip_name))
    cand_path, errors, warns = _propose.propose_clip(clip_name, src)
    print("  wrote descriptions : %s" % paths.rel(cand_path))
    print("  completeness gate  : %s" % ("PASS (0 errors)" if not errors else "%d ERROR(S)" % len(errors)))
    for e in errors:
        print("    - %s" % e)
    for w in warns[:8]:
        print("    ~ %s" % w)
    if errors:
        return 1
    if stage:
        print("  staged: %s keeps status 'candidate' (review it, then `author %s`, or re-run `propose`)."
              % (paths.rel(cand_path), clip_name))
        return 0
    if not _load(cand_path).get("action_id"):
        # The describer no longer proposes one. Dozens of corpus clips are walk variants that would
        # collide on a single action_id, and a record is keyed by its clip_name until it is accepted,
        # so naming is left to the human who is looking at it anyway.
        print("  no action_id on this record, so it stays 'candidate'. Give it one, then `author %s`."
              % clip_name)
        return 0
    print("  auto-accepting (VLM output kept by default; human review is optional via `author %s`) ..."
          % clip_name)
    return _promote_candidate(clip_name, human=False)


def _promote_candidate(clip_name, human):
    """Accept the record for `clip_name` and rename it to <action_id>.json. There is one store (ADR
    0016), so what changes is `status`; the path only follows because the record's key changed from the
    clip it came from to the action it now means. `human` records WHO accepted it: human=True is the
    optional human review (status human_accepted, screenshots verified); human=False is the default keep
    (status vlm_accepted — gpt-5.5 proposed + consistency-gated, no human review). action_id is gated
    (slug + uniqueness) either way."""
    cand_path = _by_clip().get(clip_name)
    if not cand_path:
        print("No record for clip '%s' (run `register %s` / `propose %s` first)."
              % (clip_name, clip_name, clip_name))
        return 1
    doc = _load(cand_path)
    aid = doc.get("action_id")
    if not aid or not re.match(r"^[a-z][a-z0-9_]*$", aid):
        print("Candidate action_id %r is not a valid slug ([a-z][a-z0-9_]*)." % aid)
        return 1
    root_path = os.path.join(ACTIONS_DIR, aid + ".json")
    mine = {os.path.abspath(root_path), os.path.abspath(cand_path)}
    for p, other, err in paths.read_records(_source_files()):  # uniqueness across the whole store
        if err or os.path.abspath(p) in mine:                  # the record being promoted isn't its own rival
            continue
        if other.get("action_id") == aid:
            print("action_id '%s' already used by %s — pick a unique id." % (aid, os.path.basename(p)))
            return 1
    ex = doc.setdefault("extraction", {})
    va = ex.setdefault("vlm_proposal", {})
    fo = ex.setdefault("field_origin", {})
    if human:
        va["status"] = "human_accepted"
        promoted = fo.pop("vlm_proposed", [])                  # human now owns the proposed labels
        semantic = fo.setdefault("semantic", [])
        for k in promoted:
            if k not in semantic:
                semantic.append(k)
        ex["verified_against_screenshots"] = True
        ex["verified_by"] = "user via gpt-5.5-proposed accept pass (ADR 0008)"
    else:
        va["status"] = "vlm_accepted"                          # kept as proposed; provenance stays vlm_proposed
        ex["verified_against_screenshots"] = False
        ex["verified_by"] = "auto-accepted: gpt-5.5 proposed + consistency-gated, no human review (ADR 0008)"
    ex["verified_at"] = _now()
    doc["status"] = "accepted"
    _atomic_write(root_path, doc)
    if os.path.abspath(root_path) != os.path.abspath(cand_path):
        os.remove(cand_path)                                   # the rename: <clip_name> -> <action_id>
    print("%s '%s' -> %s  (was %s)"
          % ("human-accepted" if human else "auto-accepted (vlm)", aid,
             paths.rel(root_path), paths.rel(cand_path)))
    return 0


def author(clip_name):
    """Optional human review — the SEMANTIC 'author' step (the human is the author; the VLM only proposes):
    bless a staged candidate as human_accepted and promote it to the accepted store. (The default `propose`
    path already keeps the VLM output as vlm_accepted; this upgrades it.)"""
    return _promote_candidate(clip_name, human=True)


def migrate(dry_run=False):
    """Bring the whole store to the motionkb/v4 SHAPE without re-measuring anything.

    v4 moved no number (ADR 0022), so there is nothing for the measure half to recompute: every value
    a re-measure would write back is the value already on disk. What changes per record is the rename
    `overall_intent` -> `action_description`, the deletion of the retired keys, and three provenance
    lines — `schema_version`, `extraction.extractor_version`, `extraction.extracted_at` — plus the
    `field_origin` tiers, which name different fields now.

    Descriptions are carried through VERBATIM: nothing here reads a frame or asks a model. The
    `vlm_proposal` block is kept as the historical record of what produced the descriptions, with its
    `scope` narrowed to the two fields that survived — the proposal happened, and rewriting its scope
    to claim it only ever covered those two would be a lie, so the narrowing is exactly the
    intersection with what the record still holds.
    """
    changed = untouched = failed = 0
    for p, doc, err in paths.read_records():
        if err:
            print("  FAIL  %s: %s" % (paths.rel(p), err))
            failed += 1
            continue
        before = json.dumps(doc, ensure_ascii=False)
        _apply_v4_shape(doc)
        doc["schema_version"] = C.SCHEMA_VERSION
        ex = doc.setdefault("extraction", {})
        ex["extractor_version"] = C.EXTRACTOR_VERSION
        ex["field_origin"] = _field_origin(doc)
        va = ex.get("vlm_proposal")
        if isinstance(va, dict) and isinstance(va.get("scope"), list):
            scope = ["action_description" if k == "overall_intent" else k for k in va["scope"]]
            va["scope"] = [k for k in scope if k == "action_id" or k in SEMANTIC_FIELDS]
        if json.dumps(doc, ensure_ascii=False) == before:
            untouched += 1                       # already v4; the timestamp is not restamped for nothing
            continue
        ex["extracted_at"] = _now()
        if not dry_run:
            _atomic_write(p, doc)
        changed += 1
    print("%d record(s) migrated to %s%s, %d already there, %d unreadable."
          % (changed, C.SCHEMA_VERSION, " (dry run — nothing written)" if dry_run else "",
             untouched, failed))
    return 1 if failed else 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "register":
        if len(argv) < 3:
            print("usage: extract.py register <clip_name> [--host H --port P --instance Name@hash]"); return 2
        return register(argv[2], *_parse_bridge_flags(argv[3:]))
    if cmd == "resolve-controller":
        if len(argv) < 3:
            print("usage: extract.py resolve-controller <clip_name> [--host H --port P --instance Name@hash]"); return 2
        return resolve_controller(argv[2], *_parse_bridge_flags(argv[3:]))
    if cmd == "emit-sampler":
        return emit_sampler()
    if cmd == "sample":
        # optional positional clip_name restricts the run to one clip (per-clip calls anyway)
        only = argv[2] if len(argv) > 2 and not argv[2].startswith("--") else None
        return sample(*_parse_bridge_flags(argv[2:]), only=only)
    if cmd == "assemble":
        return assemble()
    if cmd == "migrate":
        return migrate(dry_run="--dry-run" in argv[2:])
    if cmd == "render":
        if len(argv) < 3:
            print("usage: extract.py render <clip_name> [--host H --port P --instance Name@hash]"); return 2
        return render(argv[2], *_parse_bridge_flags(argv[3:]))
    if cmd == "propose":
        if len(argv) < 3:
            print("usage: extract.py propose <clip_name> [--stage] [--host H --port P --instance Name@hash]"); return 2
        rest = [a for a in argv[3:] if a != "--stage"]
        return propose(argv[2], *_parse_bridge_flags(rest), stage=("--stage" in argv[3:]))
    if cmd == "author":
        if len(argv) < 3:
            print("usage: extract.py author <clip_name|all>"); return 2
        if argv[2] == "all":
            rc = 0
            # Everything PROPOSED but not yet accepted -- not every unlabelled record. The corpus is
            # 2446 of those and not one has an action_id to be accepted under, so a bare status test
            # would try to promote the whole KB and fail 2446 times.
            for p, doc, err in paths.read_records():
                if err or doc.get("status") == "accepted" or not doc.get("action_id"):
                    continue
                clip = (doc.get("source_clip") or {}).get("clip_name")
                if clip:
                    rc |= author(clip)
            return rc
        return author(argv[2])
    print("usage: extract.py [register <clip>|resolve-controller <clip>|emit-sampler|sample [clip]|assemble|"
          "migrate [--dry-run]|render <clip>|propose <clip>|author <clip|all>]")
    print("  bridge options: --host H (default %s)  --port P (default %d)  --instance Name@hash"
          % (unity_sampler.DEFAULT_HOST, unity_sampler.DEFAULT_PORT))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
