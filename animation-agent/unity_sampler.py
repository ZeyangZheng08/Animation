"""
unity_sampler.py — the ONE place this program touches Unity.

Muscle clips can only be sampled in-engine (they are Humanoid muscle curves with no transform paths),
so a generic POSE SAMPLER must run inside Unity. Everything else (the body-part partition, the metric,
normalization, JSON assembly) is pure Python — so this sampler is deliberately DUMB: it knows nothing
about channels, divisors, or the KB. Python hands it (a) one clip and (b) a flat bone-name list, and it
returns the per-frame ROOT-LOCAL position of each requested bone plus the world root pos/forward.
Python does all the knowledge work, and Python alone writes the KB.

The C# text is GENERATED HERE from Python's config (so the bone partition is owned by Python even
though the sampling executes in C#). Execution reaches Unity via the Unity MCP `execute_code` bridge —
used ONLY because sampling genuinely needs the engine, and ONLY offline while building the KB. The
runtime agent service never ships code: it talks to a pre-compiled engine-side executor over a fixed
typed message contract, on a separate channel.

PAYLOAD CROSSES THE TRANSPORT, NOT A SHARED FILESYSTEM. The generated C# writes nothing to disk; it
hands its result back as the `execute_code` return value and Python writes that into the KB. Measured
ceiling on this channel is 8 MB per response (16 MB fails), against ~560 KB for one clip's pose dump
and ~3.2 MB for one clip's base64 frames — so every call is issued PER CLIP to keep that margin. This
drops the assumption that the engine can write somewhere Python can read, which is what makes the
executor genuinely replaceable (Unreal / Blender / a remote editor) rather than nominally so.

`build_*_csharp` only returns text, so any transport works: `run_csharp_over_http` here is the thin
stdlib client for the offline build (`POST /api/command`, `execute_code`), and a human at an
MCP-connected client can equally paste the snippet.
"""
import base64
import http.client
import json
import math
import os
import urllib.request

import config as C
import paths

# Fallback multi-angle render views (name -> camera direction from the avatar centre) and frame fractions.
# These fixed views assume the avatar faces -Z; `select_views` below replaces them per-action with a
# facing-aware, data-driven pair (used by `extract.py render`). This constant is only the fallback when
# the raw dump is missing, so a caller can still render without the MEASURED data.
RENDER_VIEWS = [("front_left_3q", (-0.7, 0.25, -1.0)), ("side_right", (1.0, 0.2, 0.0))]
RENDER_FRACS = [0.30, 0.55, 0.80]
K_FRAMES = 3      # evidence frames per view; 3 x 2 views x ~400 KB stays well inside the 8 MB response ceiling
SEED_CAP = 32     # candidate starts for the greedy traversal in `select_frame_indices`


def _representative_fwd(root_fwd):
    """Mean flattened (XZ) facing from a raw dump's per-frame `root_fwd`; unit (x,0,z), fallback (0,0,1).
    The clips are in-place, so a single representative forward is enough to orient the camera basis."""
    sx = sz = 0.0
    n = 0
    for f in (root_fwd or []):
        if len(f) >= 3:
            sx += f[0]
            sz += f[2]
            n += 1
    mag = math.hypot(sx, sz)
    if n == 0 or mag < 1e-6:
        return (0.0, 0.0, 1.0)
    return (sx / mag, 0.0, sz / mag)


def _named_views(fwd):
    """Facing-aware camera directions (avatar->centre-to-camera) built from the flat forward `fwd`.
    `front` looks at the avatar's actual front; the lateral axis is the avatar's right = (F.z, 0, -F.x)."""
    F = fwd
    R = (F[2], 0.0, -F[0])  # avatar right = cross(up, F) with up=+Y

    def mk(a_f, a_r, a_u):
        v = (a_f * F[0] + a_r * R[0], a_u, a_f * F[2] + a_r * R[2])
        m = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
        return (v[0] / m, v[1] / m, v[2] / m)

    return {
        "front":         mk(1.0,  0.0, 0.20),   # straight in front of the face
        "front_left_3q": mk(1.0, -0.7, 0.25),   # front, avatar's left
        "front_right_3q": mk(1.0,  0.7, 0.25),  # front, avatar's right
        "side_right":    mk(0.0,  1.0, 0.20),   # avatar's right side (sagittal plane)
    }


def select_views(blocks, root_fwd):
    """Coarse, data-driven per-action camera views from the MEASURED channel blocks + facing.

    The reading axis differs by what the action IS: a locomotion clip (root dynamic) reads best from the
    SIDE (gait/stride is a sagittal-plane signal); a stationary manipulation act (hands/torso working in
    FRONT of the body — cpr/pulse/giving/bvm/typing) reads best from the FRONT, so the reaching hands are
    not foreshortened; an essentially still clip keeps a neutral 3/4 + side pair. `front` is the avatar's
    real front (from raw root_fwd), which also fixes the fixed views accidentally shooting the back.
    Returns a [(name, dir), ...] list shaped like RENDER_VIEWS."""
    V = _named_views(_representative_fwd(root_fwd))

    def dyn(ch):
        return (blocks.get(ch) or {}).get("state_label") == "dynamic"

    upper_active = any(dyn(c) for c in (C.TORSO, C.LEFT_ARM, C.RIGHT_ARM, C.LEFT_HAND, C.RIGHT_HAND, C.HEAD))
    if dyn(C.ROOT):                       # locomotion
        names = ["side_right", "front_left_3q"]
    elif upper_active:                    # stationary manipulation act
        names = ["front", "front_left_3q"]
    else:                                 # near-still (idle-like)
        names = ["front_left_3q", "side_right"]
    return [(n, V[n]) for n in names]


def _pose_distance(a, b):
    """RMS over the 95 normalised muscle DOF. This is the space the channel metric already measures in
    (`muscle_dof_stddev_rms`), so "these two frames differ by 0.4" means the same thing here as it does
    in `motion_magnitude`, and it is avatar-independent by construction."""
    s = 0.0
    for x, y in zip(a, b):
        d = x - y
        s += d * d
    return math.sqrt(s / len(a))


def _greedy_kcenter(M, seed, k):
    """Farthest-point traversal from `seed`: repeatedly add the frame furthest from everything already
    chosen. Returns (sorted indices, radius), where RADIUS is the distance from the worst-covered frame
    of the clip to its nearest chosen frame -- i.e. how much of the motion no attached picture shows."""
    n = len(M)
    sel = [seed]
    mind = [_pose_distance(M[i], M[seed]) for i in range(n)]
    while len(sel) < k:
        nxt = max(range(n), key=lambda i: (mind[i], -i))   # ties -> the earlier frame, so this is deterministic
        sel.append(nxt)
        for i in range(n):
            d = _pose_distance(M[i], M[nxt])
            if d < mind[i]:
                mind[i] = d
    return sorted(sel), max(mind)


def _seed_candidates(n, cap=SEED_CAP):
    """Where the traversal starts changes the result (its 2x-optimal guarantee does not), so try several
    starts and keep the best. Measured on the eight accepted clips, capping the starts at 32 evenly
    spread frames matches trying all n on seven of them and loses 7% on the eighth, for 10x less work:
    the 600-frame worst case costs 0.44 s instead of 6 s."""
    if n <= cap:
        return list(range(n))
    return sorted({int(round(i * (n - 1) / (cap - 1))) for i in range(cap)})


def select_frame_indices(raw, k=K_FRAMES):
    """The k frames that best REPRESENT the clip: minimise the largest distance from any frame to the
    nearest chosen one (the k-center objective, in muscle space). Returns None if `raw` is unusable.

    WHAT THIS REPLACED, AND WHY. The previous selector found an "action window" -- the frames where the
    busiest effector's distance from the Hips was in the top 40% of its range -- and spread three frames
    at 15/50/85% of it, on the reasoning that the idle transitions at the clip ends are not the action.
    That reasoning is wrong for exactly the actions this KB is built from. `check_pulse` is a 2.9 s clip
    that ramps into a held pose over 13 frames and then holds it for 63: the window IS the hold, so all
    three frames landed inside it and the labeller saw one pose three times, while nothing in the clip's
    entire range of movement was shown. Measured as radius, that is 0.4090 -- the worst-covered frame of
    the clip was further from every attached picture than most clips' total range.

    Choosing for coverage instead makes the objective the thing that was actually wanted, and it needs no
    notion of what an "action window" is: a held pose gets one frame (one is enough to cover 63 identical
    ones) and spends the other two on the approach; a cycle gets its phases; a clip that really is three
    distinct poses gets all three. Radius on the eight accepted clips:

        check_pulse  0.4090 -> 0.0931      giving_pills  0.3329 -> 0.2365
        cpr          0.0391 -> 0.0178      typing        0.3060 -> 0.2381
        bvm          0.1116 -> 0.0446      walking       0.1221 -> 0.1206
        grab_bottle  0.1798 -> 0.1311      idle          0.0081 -> 0.0065

    Better on all eight, mean 0.1886 -> 0.1110. `select_views` is untouched: which ANGLES to shoot from
    is a different question, and the measured data still answers it well."""
    M = raw.get("muscles") or []
    n = len(M)
    if n < 2 or not M[0]:
        return None
    if n <= k:
        return list(range(n))
    best = None
    for seed in _seed_candidates(n):
        sel, r = _greedy_kcenter(M, seed, k)
        if best is None or r < best[1] - 1e-12:
            best = (sel, r)
    return best[0]


def select_fracs(raw, k=K_FRAMES):
    """`select_frame_indices` as clip fractions, which is what the render snippet samples at. Frame i of
    a dump was sampled at `length * i / (n - 1)`, so the fraction reproduces that pose exactly. Falls
    back to the fixed interior fractions when there is no usable `raw` (e.g. render before sample)."""
    idx = select_frame_indices(raw, k)
    if not idx:
        return list(RENDER_FRACS)
    n = len(raw["muscles"])
    return [round(i / (n - 1), 5) for i in idx]

# The Unity MCP bridge's HTTP endpoint (localhost by default; override via env for a remote editor).
DEFAULT_HOST = os.environ.get("UNITY_MCP_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("UNITY_MCP_PORT", "8080"))


def build_sampler_csharp(clip):
    """
    Return C# (CodeDom/Roslyn-compatible method body) that samples ONE clip on nurse_avatar at native
    rate and RETURNS the pose dump as a JSON string. `clip` = dict with keys id, guid, file_id. Bone
    names come from Python config (all_sample_bones()).

    One clip per call, deliberately: the response ceiling is 8 MB and a dump is ~560 KB, so a per-clip
    call keeps a 10x margin and makes a mid-corpus failure resumable instead of losing the whole batch.
    """
    bones = C.all_sample_bones()
    bones_lit = ",".join('"%s"' % b for b in bones)

    # NOTE: CodeDom C# 6 — fully-qualified names, no `using`, no local functions.
    return r'''
var BONES = new string[]{%s};
string ID="%s"; string GUID="%s"; long FID=%dL;
int SMIN=%d, SMAX=%d;

string avatarPath = null;
foreach (var g in UnityEditor.AssetDatabase.FindAssets("nurse_avatar")) { var p = UnityEditor.AssetDatabase.GUIDToAssetPath(g); if (p.ToLower().EndsWith(".fbx")) { avatarPath = p; break; } }
var model = UnityEditor.AssetDatabase.LoadAssetAtPath(avatarPath, typeof(UnityEngine.GameObject)) as UnityEngine.GameObject;
var inst = UnityEngine.Object.Instantiate(model) as UnityEngine.GameObject;
var anim = inst.GetComponent<UnityEngine.Animator>(); if (anim==null) anim = inst.GetComponentInChildren<UnityEngine.Animator>();

var HB = typeof(UnityEngine.HumanBodyBones);
var tf = new UnityEngine.Transform[BONES.Length];
for (int i=0;i<BONES.Length;i++){ tf[i] = anim.GetBoneTransform((UnityEngine.HumanBodyBones)System.Enum.Parse(HB, BONES[i])); }

var sb = new System.Text.StringBuilder();
UnityEditor.AnimationMode.StartAnimationMode();
try {
  string path = UnityEditor.AssetDatabase.GUIDToAssetPath(GUID);
  UnityEngine.AnimationClip clip=null;
  foreach (var o in UnityEditor.AssetDatabase.LoadAllAssetsAtPath(path)) { var ac=o as UnityEngine.AnimationClip; if(ac==null)continue; string gg; long lid; if(UnityEditor.AssetDatabase.TryGetGUIDAndLocalFileIdentifier(ac,out gg,out lid)&&lid==FID){clip=ac;break;} }
  if (clip==null) return "ERROR: CLIP NOT FOUND "+ID;
  int N = UnityEngine.Mathf.Clamp(UnityEngine.Mathf.RoundToInt(clip.length*clip.frameRate), SMIN, SMAX);

  sb.Append("{\"clip\":\""+ID+"\",\"frames\":"+N+",\"length\":"+clip.length.ToString("R")+",\"frame_rate\":"+clip.frameRate.ToString("R")+",\"bones\":{");
  // collect per-frame data
  var rootPos = new UnityEngine.Vector3[N]; var rootFwd = new UnityEngine.Vector3[N];
  var rootRot = new UnityEngine.Quaternion[N];
  var data = new UnityEngine.Vector3[BONES.Length][];
  var rots = new UnityEngine.Quaternion[BONES.Length][];
  for (int b=0;b<BONES.Length;b++){ data[b]=new UnityEngine.Vector3[N]; rots[b]=new UnityEngine.Quaternion[N]; }
  // TWO PASSES, so the position traversal stays byte-for-byte what it was before rotations existed.
  //
  // Sampling here is bit-deterministic -- the same clip sampled twice with identical code produces
  // identical bytes -- and that is what makes `git status` usable as the KB's drift detector: a
  // re-sample of unchanged data must produce no diff. Leaving this pass untouched preserves that by
  // construction rather than by assuming a rotation read has no side effect on transform state.
  // Verified against a control: the rotation-free sampler and this one emit identical bytes for every
  // pre-existing key on every clip. One extra offline traversal per clip is a cheap price for not
  // having to re-establish that later.
  for (int fr=0; fr<N; fr++) {
    float t = (N<=1)?0f:(clip.length*fr/(N-1));
    UnityEditor.AnimationMode.SampleAnimationClip(inst, clip, t);
    rootPos[fr]=inst.transform.position; rootFwd[fr]=inst.transform.forward;
    for (int b=0;b<BONES.Length;b++){ data[b][fr] = inst.transform.InverseTransformPoint(tf[b].position); }
  }
  for (int fr=0; fr<N; fr++) {
    float t = (N<=1)?0f:(clip.length*fr/(N-1));
    UnityEditor.AnimationMode.SampleAnimationClip(inst, clip, t);
    rootRot[fr]=inst.transform.rotation;
    var invRoot = UnityEngine.Quaternion.Inverse(inst.transform.rotation);
    for (int b=0;b<BONES.Length;b++){ rots[b][fr] = invRoot * tf[b].rotation; }
  }
  // THIRD PASS: Unity's normalised Humanoid pose.
  //
  // Everything above is metres and world rotations, so it carries the sampled avatar's proportions
  // with it -- the same clip measured on nurse_avatar and on X Bot differs by -18.3%% at the torso
  // and +16.5%% at root_gait. Those arrays are kept for provenance; no MEASURED field reads them.
  //
  // HumanPose is the representation Unity already normalises every avatar into, on BOTH halves: each
  // muscle is its joint's rotation against that avatar's own limits, and bodyPosition is expressed in
  // that same normalised frame -- not metres, and not scaled by the body. Measured across those same
  // two rigs on one clip, 1900 muscle values differed by 0.00009 on average and bodyPosition by
  // 0.0000; re-verified 2026-08-22 on six clips across three rigs spanning 15.6%% in real hip height.
  //
  // Appended after every pre-existing key, like bone_rot before it, so the dump's prefix stays
  // byte-identical and a re-sample is still checkable with a plain string comparison.
  int MUSCLES = UnityEngine.HumanTrait.MuscleCount;
  var muscles = new float[N][];
  var bodyPos = new UnityEngine.Vector3[N];
  var bodyRot = new UnityEngine.Quaternion[N];
  var poseHandler = new UnityEngine.HumanPoseHandler(anim.avatar, inst.transform);
  try {
    for (int fr=0; fr<N; fr++) {
      float t = (N<=1)?0f:(clip.length*fr/(N-1));
      UnityEditor.AnimationMode.SampleAnimationClip(inst, clip, t);
      var hp = new UnityEngine.HumanPose();
      poseHandler.GetHumanPose(ref hp);
      muscles[fr] = (float[])hp.muscles.Clone();
      bodyPos[fr] = hp.bodyPosition;
      bodyRot[fr] = hp.bodyRotation;
    }
  } finally { poseHandler.Dispose(); }
  for (int b=0;b<BONES.Length;b++){
    if(b>0) sb.Append(",");
    sb.Append("\""+BONES[b]+"\":[");
    for(int fr=0;fr<N;fr++){ var v=data[b][fr]; if(fr>0)sb.Append(","); sb.Append("["+v.x.ToString("R")+","+v.y.ToString("R")+","+v.z.ToString("R")+"]"); }
    sb.Append("]");
  }
  sb.Append("},\"root_pos\":[");
  for(int fr=0;fr<N;fr++){ var v=rootPos[fr]; if(fr>0)sb.Append(","); sb.Append("["+v.x.ToString("R")+","+v.y.ToString("R")+","+v.z.ToString("R")+"]"); }
  sb.Append("],\"root_fwd\":[");
  for(int fr=0;fr<N;fr++){ var v=rootFwd[fr]; if(fr>0)sb.Append(","); sb.Append("["+v.x.ToString("R")+","+v.y.ToString("R")+","+v.z.ToString("R")+"]"); }
  // Rotations are APPENDED after every pre-existing key, so the prefix of the dump stays byte-identical
  // and a re-sample can be checked against the old file with a plain string comparison.
  sb.Append("],\"bone_rot\":{");
  for (int b=0;b<BONES.Length;b++){
    if(b>0) sb.Append(",");
    sb.Append("\""+BONES[b]+"\":[");
    for(int fr=0;fr<N;fr++){ var q=rots[b][fr]; if(fr>0)sb.Append(","); sb.Append("["+q.x.ToString("R")+","+q.y.ToString("R")+","+q.z.ToString("R")+","+q.w.ToString("R")+"]"); }
    sb.Append("]");
  }
  sb.Append("},\"root_rot\":[");
  for(int fr=0;fr<N;fr++){ var q=rootRot[fr]; if(fr>0)sb.Append(","); sb.Append("["+q.x.ToString("R")+","+q.y.ToString("R")+","+q.z.ToString("R")+","+q.w.ToString("R")+"]"); }
  sb.Append("],\"muscles\":[");
  for(int fr=0;fr<N;fr++){
    if(fr>0)sb.Append(",");
    sb.Append("[");
    for(int m=0;m<MUSCLES;m++){ if(m>0)sb.Append(","); sb.Append(muscles[fr][m].ToString("R")); }
    sb.Append("]");
  }
  sb.Append("],\"body_pos\":[");
  for(int fr=0;fr<N;fr++){ var v=bodyPos[fr]; if(fr>0)sb.Append(","); sb.Append("["+v.x.ToString("R")+","+v.y.ToString("R")+","+v.z.ToString("R")+"]"); }
  sb.Append("],\"body_rot\":[");
  for(int fr=0;fr<N;fr++){ var q=bodyRot[fr]; if(fr>0)sb.Append(","); sb.Append("["+q.x.ToString("R")+","+q.y.ToString("R")+","+q.z.ToString("R")+","+q.w.ToString("R")+"]"); }
  sb.Append("],\"muscle_names\":[");
  { var mn = UnityEngine.HumanTrait.MuscleName;
    for(int m=0;m<MUSCLES;m++){ if(m>0)sb.Append(","); sb.Append("\""+mn[m]+"\""); } }
  sb.Append("],\"muscle_bone\":[");
  for(int m=0;m<MUSCLES;m++){ if(m>0)sb.Append(","); sb.Append(UnityEngine.HumanTrait.BoneFromMuscle(m).ToString()); }
  sb.Append("],\"rot_frame\":\"root_local\",\"rot_order\":\"xyzw\"}");
} finally { UnityEditor.AnimationMode.StopAnimationMode(); UnityEngine.Object.DestroyImmediate(inst); }
return sb.ToString();
''' % (bones_lit, clip["id"], clip["guid"], int(clip["file_id"]), C.SAMPLE_MIN, C.SAMPLE_MAX)


def raw_path(clip_id):
    return os.path.join(paths.RAW_DIR, clip_id + ".json")


def read_raw(clip_id):
    """Load one clip's frozen pose dump from the KB."""
    return paths.read_json(raw_path(clip_id))


def write_raw(clip_id, dump_text):
    """Persist one clip's pose dump returned by the in-engine sampler.

    Parsed first, so a truncated or error response never lands in the KB as a corrupt file — but written
    back VERBATIM, not re-serialized. The dumps are a single line with no trailing newline (the shape the
    in-engine writer produced), and re-indenting them would turn every re-sample of unchanged data into a
    65k-line diff, destroying `git status` as a drift detector for the KB.
    """
    # Imported here rather than at module scope: this is the pipeline, and it should not pull the
    # runtime package in just to hand back a cache invalidation.
    from agent.transitions import forget_raw

    dump = json.loads(dump_text)
    written = paths.write_text(raw_path(clip_id), dump_text)
    forget_raw()   # `raw` just moved; anything memoised from it is now about a corpus that is gone
    return written, dump


def emit_sampler_file(clip, out_path):
    """Write the generated C# for ONE clip to a file, for an operator to run by hand at an
    MCP-connected client. `extract.py sample` does not need this — it posts the text directly."""
    paths.write_text(out_path, build_sampler_csharp(clip))
    return out_path


# ---- Optional thin transport over the Unity MCP HTTP bridge -----------------------------------------
def bridge_healthy(host=DEFAULT_HOST, port=DEFAULT_PORT, timeout=5):
    """True if the Unity MCP HTTP server answers /health (i.e. Unity is open and the bridge is up)."""
    try:
        with urllib.request.urlopen("http://%s:%d/health" % (host, port), timeout=timeout) as r:
            return getattr(r, "status", r.getcode()) == 200
    except Exception:
        return False


_CONN = {}   # (host, port) -> http.client.HTTPConnection, reused across calls


def _connection(host, port, timeout):
    """One keep-alive connection per endpoint. A per-clip pipeline issues many calls in a row; a fresh
    TCP handshake each time is pure overhead, and the runtime path will want connection reuse anyway."""
    key = (host, port)
    conn = _CONN.get(key)
    if conn is None:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        _CONN[key] = conn
    return conn


def close_connections():
    """Drop the pooled connections (end of a run, or after a transport error)."""
    for conn in _CONN.values():
        try:
            conn.close()
        except Exception:
            pass
    _CONN.clear()


def run_csharp_over_http(cs_code, host=DEFAULT_HOST, port=DEFAULT_PORT, instance=None, timeout=600):
    """Execute a C# method body in the running Unity editor via the bridge's plain HTTP endpoint.

    Posts {"type": "execute_code", "params": {"action": "execute", "code": ..., "safety_checks": False}}
    to POST /api/command over a reused keep-alive connection. `safety_checks` is False because these
    snippets drive editor-only APIs (AnimationMode, AssetDatabase, camera rendering) that the checked
    path refuses. Returns (ok: bool, result_text: str, raw_response: dict). Raises RuntimeError only on
    a transport failure (bridge unreachable / timeout); a Unity-side compile or runtime error comes back
    as ok=False with the message in result_text.
    """
    payload = {"type": "execute_code",
               "params": {"action": "execute", "code": cs_code, "safety_checks": False}}
    if instance:
        payload["unity_instance"] = instance
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Connection": "keep-alive"}

    body = None
    for attempt in (1, 2):          # a pooled connection can go stale; one silent retry on a fresh one
        conn = _connection(host, port, timeout)
        try:
            conn.request("POST", "/api/command", body=data, headers=headers)
            resp = conn.getresponse()
            body = json.loads(resp.read().decode("utf-8"))
            break
        except (http.client.HTTPException, OSError, ValueError) as e:
            close_connections()
            if attempt == 2:
                raise RuntimeError(
                    "Cannot reach the Unity MCP bridge at %s:%d (%s).\n"
                    "Open the Unity project and start the MCP server on HTTP (port %d) first."
                    % (host, port, e, port))

    inner = body.get("result", body) if isinstance(body, dict) else {}
    ok = bool(body.get("status") == "success" and inner.get("success", True))
    data_blk = inner.get("data") or {}
    result_text = data_blk.get("result")
    if result_text is None:
        result_text = inner.get("message") or json.dumps(body)
    return ok, str(result_text), body


# ---- Resolve accepted actions' guid+file_id to real AnimationClips (for `validate_guids.py`) --------
def build_validate_guids_csharp(entries):
    """C# that resolves each (guid, file_id, clip_name) to a real AnimationClip via the AssetDatabase and
    returns one `key|verdict|clip_name|asset_path` line per entry. This is the ONE layer of the data
    contract that genuinely needs the engine; schema, cross-field invariants, semantic consistency and
    golden re-extraction all run in Python with no Unity.

    `entries` = list of dicts with keys key, guid, file_id, clip_name. Verdicts:
      OK        guid resolved and an AnimationClip at that asset matches file_id
      WARN      resolved, no file_id match, but a clip with the recorded name exists (file_id drifted)
      NOPATH    guid does not resolve to an existing asset
      NOCLIP    asset exists but holds no clip matching either file_id or clip_name

    Replaces the former Assets/Editor/MotionKB/MotionKBValidator.cs, so no agent-side code remains
    inside the Unity project. Same pattern as build_find_clip_csharp / build_resolve_controller_csharp.
    """
    keys_lit = ",".join('"%s"' % e["key"] for e in entries)
    guids_lit = ",".join('"%s"' % e["guid"] for e in entries)
    fids_lit = ",".join("%dL" % int(e["file_id"]) for e in entries)
    names_lit = ",".join('"%s"' % e["clip_name"] for e in entries)
    return r'''
var KEYS=new string[]{%s}; var GUIDS=new string[]{%s}; var FIDS=new long[]{%s}; var NAMES=new string[]{%s};
var sb=new System.Text.StringBuilder();
for(int i=0;i<KEYS.Length;i++){
  string path = UnityEditor.AssetDatabase.GUIDToAssetPath(GUIDS[i]);
  if(string.IsNullOrEmpty(path) || !System.IO.File.Exists(path)){ sb.AppendLine(KEYS[i]+"|NOPATH|"+NAMES[i]+"|"+path); continue; }
  bool byId=false, byName=false;
  foreach (var o in UnityEditor.AssetDatabase.LoadAllAssetsAtPath(path)) {
    var ac=o as UnityEngine.AnimationClip; if(ac==null) continue;
    string gg; long lid;
    if(UnityEditor.AssetDatabase.TryGetGUIDAndLocalFileIdentifier(ac, out gg, out lid) && lid==FIDS[i]) byId=true;
    if(ac.name==NAMES[i]) byName=true;
  }
  string verdict = byId ? "OK" : (byName ? "WARN" : "NOCLIP");
  sb.AppendLine(KEYS[i]+"|"+verdict+"|"+NAMES[i]+"|"+path);
}
return sb.ToString().Trim();
''' % (keys_lit, guids_lit, fids_lit, names_lit)


# ---- Resolve a clip by name (for `register`): name -> {path, guid, file_id} --------------------------
def build_find_clip_csharp(clip_name, scan_dirs=("Assets/Animations",)):
    """C# that finds every AnimationClip named `clip_name` under `scan_dirs` (both standalone .anim and
    FBX-embedded sub-clips) and returns one 'path|guid|file_id' line per match. The clip name is the key;
    guid+file_id are read off the resolved clip via AssetDatabase, killing the manual file_id gotcha."""
    dirs_lit = ",".join('"%s"' % d for d in scan_dirs)
    return r'''
string NAME="%s"; var DIRS=new string[]{%s};
var paths=new System.Collections.Generic.HashSet<string>();
foreach (var g in UnityEditor.AssetDatabase.FindAssets("t:AnimationClip", DIRS)) paths.Add(UnityEditor.AssetDatabase.GUIDToAssetPath(g));
foreach (var g in UnityEditor.AssetDatabase.FindAssets("t:GameObject", DIRS)) { var p=UnityEditor.AssetDatabase.GUIDToAssetPath(g); if(p.ToLower().EndsWith(".fbx")) paths.Add(p); }
var sb=new System.Text.StringBuilder();
foreach (var path in paths) {
  foreach (var o in UnityEditor.AssetDatabase.LoadAllAssetsAtPath(path)) {
    var ac=o as UnityEngine.AnimationClip; if(ac==null || ac.name!=NAME) continue;
    string cg; long lid; UnityEditor.AssetDatabase.TryGetGUIDAndLocalFileIdentifier(ac, out cg, out lid);
    sb.AppendLine(path+"|"+cg+"|"+lid);
  }
}
return sb.ToString().Trim();
''' % (clip_name, dirs_lit)


# ---- Render multi-angle frames of one clip (for the VLM propose step) -------------------------------
# ---- Resolve controller wiring for a clip (for `resolve-controller` / `register`) -------------------
def build_resolve_controller_csharp(clip_name, scan_dirs=("Assets/Animations",)):
    """C# that scans every AnimatorController under `scan_dirs` for a state whose motion (directly or
    inside a BlendTree) is an AnimationClip named `clip_name`, and for each hit returns one
    'state|layer|trigger|controller_path' line. `trigger` is the parameter on a transition INTO that state
    (anyState first, then any state's own transitions); empty when the state has no activating transition
    (e.g. a default/entry state — leave trigger_param null). No engine YAML parsing — typed editor API.
    Sub-state-machines and blend trees are walked with explicit work-stacks (CodeDom C# 6: no local funcs)."""
    dirs_lit = ",".join('"%s"' % d for d in scan_dirs)
    return r'''
string NAME="%s"; var DIRS=new string[]{%s};
var sb=new System.Text.StringBuilder();
foreach (var cg in UnityEditor.AssetDatabase.FindAssets("t:AnimatorController", DIRS)) {
  string cpath = UnityEditor.AssetDatabase.GUIDToAssetPath(cg);
  var ctrl = UnityEditor.AssetDatabase.LoadAssetAtPath(cpath, typeof(UnityEditor.Animations.AnimatorController)) as UnityEditor.Animations.AnimatorController;
  if(ctrl==null) continue;
  foreach (var layer in ctrl.layers) {
    var rootSM = layer.stateMachine;
    var smStack = new System.Collections.Generic.Stack<UnityEditor.Animations.AnimatorStateMachine>();
    smStack.Push(rootSM);
    var allStates = new System.Collections.Generic.List<UnityEditor.Animations.AnimatorState>();
    while(smStack.Count>0){
      var cur = smStack.Pop();
      foreach (var cs in cur.states) allStates.Add(cs.state);
      foreach (var css in cur.stateMachines) smStack.Push(css.stateMachine);
    }
    for(int si=0; si<allStates.Count; si++){
      var st = allStates[si];
      bool hit=false;
      var mStack = new System.Collections.Generic.Stack<UnityEngine.Motion>();
      if(st.motion!=null) mStack.Push(st.motion);
      while(mStack.Count>0){
        var mo = mStack.Pop();
        var acl = mo as UnityEngine.AnimationClip;
        if(acl!=null){ if(acl.name==NAME){ hit=true; break; } continue; }
        var bt = mo as UnityEditor.Animations.BlendTree;
        if(bt!=null){ foreach(var ch in bt.children){ if(ch.motion!=null) mStack.Push(ch.motion); } }
      }
      if(!hit) continue;
      string trig="";
      // The layer's default/resting state is entered by default, not by an activating trigger -> leave blank
      // (avoids mistaking a 'return-to-rest' transition's condition, e.g. Speed<0.1 into Idle, for a trigger).
      bool isDefault = (rootSM.defaultState==st);
      if(!isDefault){
        foreach (var t in rootSM.anyStateTransitions){ if(t.destinationState==st && t.conditions.Length>0){ trig=t.conditions[0].parameter; break; } }
        if(trig==""){
          for(int sj=0; sj<allStates.Count && trig=="" ; sj++){
            foreach(var t in allStates[sj].transitions){ if(t.destinationState==st && t.conditions.Length>0){ trig=t.conditions[0].parameter; break; } }
          }
        }
      }
      sb.AppendLine(st.name+"|"+layer.name+"|"+trig+"|"+cpath);
    }
  }
}
return sb.ToString().Trim();
''' % (clip_name, dirs_lit)


def build_render_csharp(clip, views=RENDER_VIEWS, fracs=RENDER_FRACS, w=1024, h=1024):
    """C# (CodeDom method body) that samples one clip on nurse_avatar, renders it from several angles at
    several times, and RETURNS the PNGs as `<filename>|<base64>` lines. `clip` = {id, guid, file_id}.
    Frames are kept for the human-review half of the propose/author loop (ADR 0008).

    Nothing is written engine-side: ~6 frames x ~400 KB base64-expands to ~3.2 MB, comfortably inside the
    measured 8 MB response ceiling for one clip."""
    vnames = ",".join('"%s"' % v[0] for v in views)
    vdx = ",".join("%ff" % v[1][0] for v in views)
    vdy = ",".join("%ff" % v[1][1] for v in views)
    vdz = ",".join("%ff" % v[1][2] for v in views)
    fr = ",".join("%ff" % f for f in fracs)
    return (r'''
string GUID="%s"; long FID=%dL; int W=%d,H=%d;
var VN=new string[]{%s}; var VX=new float[]{%s}; var VY=new float[]{%s}; var VZ=new float[]{%s};
var FR=new float[]{%s};

string clipPath = UnityEditor.AssetDatabase.GUIDToAssetPath(GUID);
UnityEngine.AnimationClip clip=null;
foreach (var o in UnityEditor.AssetDatabase.LoadAllAssetsAtPath(clipPath)) { var ac=o as UnityEngine.AnimationClip; if(ac==null)continue; string gg; long lid; if(UnityEditor.AssetDatabase.TryGetGUIDAndLocalFileIdentifier(ac,out gg,out lid)&&lid==FID){clip=ac;break;} }
if(clip==null) return "CLIP NOT FOUND";

string AVATAR=__RENDER_AVATAR__;
string avatarPath=null;
// Match the FILE NAME exactly. A bare FindAssets("X Bot") also returns "X Bot@Typing.fbx" -- an
// animation file with no mesh and no avatar -- and taking the first .fbx hit lands on it.
foreach (var g in UnityEditor.AssetDatabase.FindAssets(System.IO.Path.GetFileNameWithoutExtension(AVATAR))) {
  var p=UnityEditor.AssetDatabase.GUIDToAssetPath(g);
  if(System.IO.Path.GetFileName(p)==AVATAR){avatarPath=p;break;}
}
if(avatarPath==null) return "RENDER AVATAR NOT FOUND: "+AVATAR;
var model = UnityEditor.AssetDatabase.LoadAssetAtPath(avatarPath, typeof(UnityEngine.GameObject)) as UnityEngine.GameObject;
var inst = UnityEngine.Object.Instantiate(model) as UnityEngine.GameObject;
inst.transform.position = UnityEngine.Vector3.zero; inst.transform.rotation = UnityEngine.Quaternion.identity;

// The avatar itself stays OFF the render layer; the camera sees baked static meshes built per
// frame (below). GPU skinning is not refreshed under a manual cam.Render() in edit mode, which
// stranded the head and hands at an earlier position on any clip that travels.
foreach (var tr in inst.GetComponentsInChildren<UnityEngine.Transform>(true)) tr.gameObject.layer = 0;
var lightGO = new UnityEngine.GameObject("MKB_Light"); var light = lightGO.AddComponent<UnityEngine.Light>();
light.type = UnityEngine.LightType.Directional; light.intensity = 1.2f; lightGO.transform.rotation = UnityEngine.Quaternion.Euler(50f,-30f,0f);
// Soft fill from the opposite side so a shadowed limb (e.g. a hand against the body) keeps some form and
// doesn't sink to solid black -- helps read overlapping same-colour parts. The key light stays dominant.
var fillGO = new UnityEngine.GameObject("MKB_Fill"); var fill = fillGO.AddComponent<UnityEngine.Light>();
fill.type = UnityEngine.LightType.Directional; fill.intensity = 0.4f; fillGO.transform.rotation = UnityEngine.Quaternion.Euler(25f,150f,0f);
// Rim light from behind. The avatar is near-white and so is the floor; without an edge the
// silhouette dissolves into the ground exactly where a reader looks for the feet.
var rimGO = new UnityEngine.GameObject("MKB_Rim"); var rim = rimGO.AddComponent<UnityEngine.Light>();
rim.type = UnityEngine.LightType.Directional; rim.intensity = 0.9f;
rim.color = new UnityEngine.Color(0.75f,0.80f,0.95f,1f); rimGO.transform.rotation = UnityEngine.Quaternion.Euler(8f,200f,0f);
var camGO = new UnityEngine.GameObject("MKB_RenderCam"); var cam = camGO.AddComponent<UnityEngine.Camera>();
cam.clearFlags = UnityEngine.CameraClearFlags.SolidColor; cam.backgroundColor = new UnityEngine.Color(0.25f,0.27f,0.30f,1f);
cam.fieldOfView=35f; cam.cullingMask = 1<<31; cam.nearClipPlane=0.03f; cam.farClipPlane=50f;
int SS = 2;   // supersample: render at SSx then downscale -> clean anti-aliasing, independent of the URP MSAA path
var rt = new UnityEngine.RenderTexture(W*SS,H*SS,24); rt.antiAliasing = 4; cam.targetTexture = rt;
var rends = inst.GetComponentsInChildren<UnityEngine.SkinnedMeshRenderer>();
// WHICH PIECES GET DRAWN. All 88 renderers on this avatar report enabled+activeInHierarchy: Unity
// leans on the LODGroup to choose 34 of them, and the character ships five interchangeable variants
// of each costume item (Mask_SurgicalA..E, HairA..E, Skin_A..E, Glasses_A/C/E). Baking everything
// stacked three LOD levels and five masks on top of each other -- one of which skinned to a
// different place and floated in mid-air, which a labeller would read as a held or dropped prop.
// So: LOD0 only, one renderer per costume family, and BODY ONLY. Masks, straps, glasses and the
// stethoscope are deliberately dropped -- the frames exist to show how the body moves, and a
// clinical prop in shot is exactly the kind of thing that pulls a label toward a nursing reading
// the movement does not support.
var lodg = inst.GetComponentInChildren<UnityEngine.LODGroup>(true);
var lod0 = new System.Collections.Generic.HashSet<UnityEngine.Renderer>();
if(lodg!=null){ var lv=lodg.GetLODs(); if(lv.Length>0) foreach(var r0 in lv[0].renderers) if(r0!=null) lod0.Add(r0); }
string[] FAMILIES = new string[]{"Skin_","TOP","BTM","Shirt","Hair"};
var draw = new System.Collections.Generic.List<UnityEngine.SkinnedMeshRenderer>();
var seenFam = new System.Collections.Generic.HashSet<string>();
foreach(var sr1 in rends){
  if(lod0.Count>0 && !lod0.Contains(sr1)) continue;
  if(sr1.name.Contains("_Fade")) continue;
  string fam=null;
  for(int fi2=0; fi2<FAMILIES.Length; fi2++) if(sr1.name.StartsWith(FAMILIES[fi2])) { fam=FAMILIES[fi2]; break; }
  if(fam==null || seenFam.Contains(fam)) continue;
  seenFam.Add(fam); draw.Add(sr1);
}
// The family list above is specific to nurse_avatar, which ships five interchangeable
// variants of each costume item and three LOD levels. A plain mannequin like Y Bot has
// neither, so nothing matches and everything is drawn -- which is the right answer for it.
if(draw.Count==0) foreach(var sr1 in rends) draw.Add(sr1);
// KEEP EVERY PIECE SKINNED. A SkinnedMeshRenderer culls on bounds derived from the BIND pose, and a
// culled renderer stops being skinned at all -- it keeps rendering its last evaluated pose. On a clip
// that travels (Mixamo locomotion moves the body over a metre), the small separate meshes of this
// avatar -- head, hands -- fall outside those stale bounds and freeze in place while the body mesh
// moves on, so the frames show a detached floating head and hands. The nurse clips never travel, which
// is why this only appeared with the Mixamo corpus. updateWhenOffscreen recomputes bounds and skinning
// every sample, which is what an offline renderer wants regardless of cost.
foreach(var sr0 in rends) sr0.updateWhenOffscreen = true;

// A simple ground plane on the SAME isolation layer, so the VLM can see whether feet are planted on it.
var floor = UnityEngine.GameObject.CreatePrimitive(UnityEngine.PrimitiveType.Plane);
foreach (var fcol in floor.GetComponents<UnityEngine.Collider>()) UnityEngine.Object.DestroyImmediate(fcol);
floor.layer = 31;   // position is set per frame, once groundY is known floor.transform.localScale = new UnityEngine.Vector3(0.6f,1f,0.6f);
var fsh = UnityEngine.Shader.Find("Universal Render Pipeline/Lit"); if(fsh==null) fsh=UnityEngine.Shader.Find("Standard"); if(fsh==null) fsh=UnityEngine.Shader.Find("Sprites/Default");
var fmat = new UnityEngine.Material(fsh);
if(fmat.HasProperty("_BaseColor")) fmat.SetColor("_BaseColor", UnityEngine.Color.white); else fmat.color = UnityEngine.Color.white;
// A metre-scale checker. A flat floor gives a reader nothing to judge against: whether the figure
// travelled, whether a foot is planted or hovering, and how big a stride is all become guesses.
var ctex = new UnityEngine.Texture2D(64,64);
for(int cy=0; cy<64; cy++) for(int cx=0; cx<64; cx++){
  bool even = ((cx/32) + (cy/32)) %% 2 == 0;
  ctex.SetPixel(cx, cy, even ? new UnityEngine.Color(0.40f,0.43f,0.48f,1f) : new UnityEngine.Color(0.28f,0.30f,0.35f,1f));
}
ctex.Apply(); ctex.wrapMode = UnityEngine.TextureWrapMode.Repeat; ctex.filterMode = UnityEngine.FilterMode.Bilinear;
if(fmat.HasProperty("_BaseMap")) { fmat.SetTexture("_BaseMap", ctex); fmat.SetTextureScale("_BaseMap", new UnityEngine.Vector2(12f,12f)); }
fmat.mainTexture = ctex; fmat.mainTextureScale = new UnityEngine.Vector2(12f,12f);
if(fmat.HasProperty("_Smoothness")) fmat.SetFloat("_Smoothness", 0f);
floor.GetComponent<UnityEngine.Renderer>().sharedMaterial = fmat;

var summary = new System.Text.StringBuilder(); int wrote=0;
UnityEditor.AnimationMode.StartAnimationMode();
try {
  // Ground calibration (constant per-clip): the clip's baked body height (Root Transform Position Y) is
  // arbitrary, so the avatar can float or sink. Find the LOWEST the mesh reaches over the whole clip and
  // shift by that CONSTANT below -- a planted foot then rests on y=0 while a lifted/swing foot or a real
  // jump still reads as off the ground. Mesh-accurate via BakeMesh (bones/bounds miss the true lowest).
  float groundY = 1e30f;
  int GN = (int)UnityEngine.Mathf.Min(UnityEngine.Mathf.Max(2f, UnityEngine.Mathf.Round(clip.length*clip.frameRate)), 24f); if(GN<1)GN=1;
  for(int gi=0; gi<GN; gi++){
    UnityEditor.AnimationMode.SampleAnimationClip(inst, clip, GN==1?0f:clip.length*(float)gi/(GN-1));
    foreach(var sr in draw){ var bm=new UnityEngine.Mesh(); sr.BakeMesh(bm); var vs=bm.vertices; var l2w=sr.transform.localToWorldMatrix; for(int k=0;k<vs.Length;k++){ float wy=l2w.MultiplyPoint3x4(vs[k]).y; if(wy<groundY)groundY=wy; } UnityEngine.Object.DestroyImmediate(bm); }
  }
  if(groundY>1e29f||groundY<-1e29f) groundY=0f;

  // ONE camera setup for every frame of this clip. Framing each moment on its own bounds made the
  // figure change size between shots, and apparent size is precisely the cue a reader uses to judge
  // whether the body moved toward or away. Bounds are taken over the moments actually shot, so the
  // subject still fills the frame.
  // FIXED DISTANCE, TRACKING CENTRE. Two things a reader needs at once: the body big enough to read
  // limb by limb, and a size that means something across frames. Framing each moment on its own
  // bounds gave the first and destroyed the second; framing all moments on their union gave the
  // second and shrank the figure to a third of the frame. So the camera distance is fixed -- from
  // the largest SINGLE pose, not the union -- and the camera re-centres each frame. The figure keeps
  // one apparent size, and the checker floor is what shows that it travelled.
  UnityEngine.Vector3 shotC=UnityEngine.Vector3.zero; float shotR=0f; bool shotHas=false;
  for(int pf=0; pf<FR.Length; pf++){
    UnityEditor.AnimationMode.SampleAnimationClip(inst, clip, clip.length*FR[pf]);
    UnityEngine.Bounds pb=new UnityEngine.Bounds(); bool pHas=false;
    foreach(var sr in draw){
      var pm=new UnityEngine.Mesh(); sr.BakeMesh(pm); var pv=pm.vertices; var pl2w=sr.transform.localToWorldMatrix;
      int pstep=UnityEngine.Mathf.Max(1,pv.Length/200);
      for(int k=0;k<pv.Length;k+=pstep){ var wp=pl2w.MultiplyPoint3x4(pv[k]); if(!pHas){pb=new UnityEngine.Bounds(wp,UnityEngine.Vector3.zero);pHas=true;} else pb.Encapsulate(wp); }
      UnityEngine.Object.DestroyImmediate(pm);
    }
    if(!pHas) continue;
    float pr=pb.extents.magnitude; if(pr>shotR) shotR=pr;
    shotC = shotHas ? (shotC+pb.center)*0.5f : pb.center; shotHas=true;
  }
  if(!shotHas){ shotC=inst.transform.position; shotR=0.95f; }
  if(shotR<0.6f) shotR=0.95f;
  float shotDist = shotR / UnityEngine.Mathf.Tan(cam.fieldOfView*0.5f*UnityEngine.Mathf.Deg2Rad) * 1.12f;
  floor.transform.position = new UnityEngine.Vector3(shotC.x, groundY, shotC.z);
  floor.transform.localScale = new UnityEngine.Vector3(3.0f, 1f, 3.0f);

  for(int fi=0; fi<FR.Length; fi++){
    UnityEditor.AnimationMode.SampleAnimationClip(inst, clip, clip.length*FR[fi]);
    // DO NOT move  after sampling. SampleAnimationClip applies the clip's root motion to the
    // instance transform, and moving it afterwards leaves each renderer's cached skinning and bounds
    // describing the pre-move placement -- which is what drew a detached head and hands on clips that
    // travel. The avatar stays where the sample put it and the FLOOR is moved to meet it instead.
    // Frame from BAKED vertices. SkinnedMeshRenderer.bounds is a cache that edit-mode sampling does not
    // refresh -- measured 0.35..0.68 on x while the true skinned centroids were all near -0.30.
    // Frame from BAKED vertices. SkinnedMeshRenderer.bounds is a cache that edit-mode sampling does
    // not refresh -- measured 0.35..0.68 on x while the true skinned centroids were all near -0.30.
    // Only the bounds are taken from the bake; Unity still draws the avatar itself, because it is the
    // only thing that picks the right LOD level and the right one of the five costume variants.
    var proxies = new System.Collections.Generic.List<UnityEngine.GameObject>();
    UnityEngine.Bounds b=new UnityEngine.Bounds(); bool has=false;
    foreach(var sr in draw){
      var bmesh=new UnityEngine.Mesh(); sr.BakeMesh(bmesh);
      var go=new UnityEngine.GameObject("MKB_Baked"); go.layer=31;
      go.transform.position=sr.transform.position; go.transform.rotation=sr.transform.rotation; go.transform.localScale=sr.transform.lossyScale;
      go.AddComponent<UnityEngine.MeshFilter>().sharedMesh=bmesh;
      go.AddComponent<UnityEngine.MeshRenderer>().sharedMaterials=sr.sharedMaterials;
      proxies.Add(go);
      var bv=bmesh.vertices; var bl2w=go.transform.localToWorldMatrix; int bstep=UnityEngine.Mathf.Max(1,bv.Length/200);
      for(int k=0;k<bv.Length;k+=bstep){ var wp=bl2w.MultiplyPoint3x4(bv[k]); if(!has){b=new UnityEngine.Bounds(wp,UnityEngine.Vector3.zero);has=true;} else b.Encapsulate(wp); }
    }
    if(!has) b=new UnityEngine.Bounds(inst.transform.position, UnityEngine.Vector3.one);
    // centre on THIS pose, at the distance fixed above
    UnityEngine.Vector3 c=has?b.center:shotC; float dist=shotDist;
    for(int vi=0; vi<VN.Length; vi++){
      UnityEngine.Vector3 dir=new UnityEngine.Vector3(VX[vi],VY[vi],VZ[vi]).normalized;
      camGO.transform.position = c + dir*dist; camGO.transform.LookAt(c);
      cam.Render();
      var small = UnityEngine.RenderTexture.GetTemporary(W,H,0); UnityEngine.Graphics.Blit(rt, small);  // resolve MSAA + downscale
      var prev=UnityEngine.RenderTexture.active; UnityEngine.RenderTexture.active=small;
      var tex=new UnityEngine.Texture2D(W,H,UnityEngine.TextureFormat.RGB24,false);
      tex.ReadPixels(new UnityEngine.Rect(0,0,W,H),0,0); tex.Apply();
      UnityEngine.RenderTexture.active=prev; UnityEngine.RenderTexture.ReleaseTemporary(small);
      byte[] png = UnityEngine.ImageConversion.EncodeToPNG(tex);
      // The ordinal, not just the percentage, because the percentage identifies neither uniquely nor in
      // order: frames are now chosen by POSE, so two of them can fall in the same whole percent of a long
      // clip (one file silently overwriting another), and a plain lexicographic sort puts "_f21" before
      // "_f5" -- which matters because the prompt tells the model to read the frames as a sequence.
      string fn = VN[vi]+"_t"+fi+"_f"+((int)(FR[fi]*100))+".png";
      summary.AppendLine(fn+"|"+System.Convert.ToBase64String(png));
      UnityEngine.Object.DestroyImmediate(tex); wrote++;
    }
    foreach(var g in proxies){ var mf=g.GetComponent<UnityEngine.MeshFilter>(); if(mf!=null&&mf.sharedMesh!=null) UnityEngine.Object.DestroyImmediate(mf.sharedMesh); UnityEngine.Object.DestroyImmediate(g); }
  }
} finally {
  UnityEditor.AnimationMode.StopAnimationMode();
  cam.targetTexture=null; UnityEngine.Object.DestroyImmediate(rt); UnityEngine.Object.DestroyImmediate(camGO);
  UnityEngine.Object.DestroyImmediate(lightGO); UnityEngine.Object.DestroyImmediate(fillGO); UnityEngine.Object.DestroyImmediate(rimGO); UnityEngine.Object.DestroyImmediate(floor); UnityEngine.Object.DestroyImmediate(inst);
}
return summary.ToString();
''' % (clip["guid"], int(clip["file_id"]), w, h, vnames, vdx, vdy, vdz, fr)
    ).replace("__RENDER_AVATAR__", json.dumps(C.RENDER_AVATAR))


def write_frames(clip_name, result_text):
    """Decode the `<filename>|<base64>` lines returned by the render snippet into the KB's per-clip
    frames directory. Returns the list of written paths. A line that is not `name.png|base64` is
    ignored, so a Unity-side error string never lands on disk as a bogus PNG."""
    out_dir = os.path.join(paths.FRAMES_DIR, clip_name)
    written = []
    for line in (result_text or "").splitlines():
        name, sep, b64 = line.partition("|")
        if not sep or not name.endswith(".png") or not b64:
            continue
        written.append(paths.write_bytes(os.path.join(out_dir, name), base64.b64decode(b64)))
    return written
