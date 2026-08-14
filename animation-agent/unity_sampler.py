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
# the _raw dump is missing, so a caller can still render without the MEASURED data.
RENDER_VIEWS = [("front_left_3q", (-0.7, 0.25, -1.0)), ("side_right", (1.0, 0.2, 0.0))]
RENDER_FRACS = [0.30, 0.55, 0.80]


def _representative_fwd(root_fwd):
    """Mean flattened (XZ) facing from a _raw dump's per-frame `root_fwd`; unit (x,0,z), fallback (0,0,1).
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
    real front (from _raw root_fwd), which also fixes the fixed views accidentally shooting the back.
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


def _busiest_effector_reach(raw):
    """Per-frame reach (distance from Hips) of the effector (hand/foot) whose reach varies most over the
    clip. This traces the action: it is LOW during the idle transitions at the clip ends (limb down) and
    HIGH while the action is engaged (arm extended / foot in stride). Returns (series, nfr) or (None, 0).

    Note: uses reach-from-Hips, NOT deviation-from-mean-pose — the latter flags the idle->action->idle
    TRANSITIONS as the outliers (the held action pose dominates the mean), which is exactly backwards for
    the settle-and-hold nursing actions."""
    b = raw.get("bones") or {}
    hips = b.get("Hips")
    seqs = [s for s in b.values() if s]
    if not hips or not seqs:
        return None, 0
    nfr = min([len(s) for s in seqs] + [len(hips)])
    if nfr < 2:
        return None, 0
    best = None
    for bone in ("LeftHand", "RightHand", "LeftFoot", "RightFoot"):
        s = b.get(bone)
        if not s:
            continue
        series = []
        for i in range(nfr):
            p, h = s[i], hips[i]
            series.append(math.sqrt((p[0] - h[0]) ** 2 + (p[1] - h[1]) ** 2 + (p[2] - h[2]) ** 2))
        m = sum(series) / nfr
        var = sum((v - m) ** 2 for v in series) / nfr
        if best is None or var > best[0]:
            best = (var, series)
    return (best[1], nfr) if best else (None, 0)


def select_fracs(raw):
    """Data-driven time samples: find the ACTION WINDOW (frames where the busiest effector is engaged /
    extended, which excludes the idle transitions at the clip ends) and spread 3 frames across it, so all
    three show the action. This keeps the count at 3 but places them where the action actually is — a
    plateau for a held pose (pulse/bvm), the peak region for a ballistic one (grab/give). Falls back to
    the fixed interior fractions if _raw is unusable or the signal is flat."""
    series, nfr = _busiest_effector_reach(raw)
    if not series or nfr < 3:
        return list(RENDER_FRACS)
    lo_v, hi_v = min(series), max(series)
    if hi_v - lo_v < 0.02:                              # near-flat (<2cm: static hold/idle) -> spread over the clip
        lo, hi = 0, nfr - 1
    else:
        thr = lo_v + 0.60 * (hi_v - lo_v)               # "engaged" = reach in the top 40% of its range
        active = [i for i in range(nfr) if series[i] >= thr]
        lo, hi = active[0], active[-1]
    span = hi - lo
    idx = [lo + int(round(span * f)) for f in (0.15, 0.50, 0.85)]
    return sorted(round(i / (nfr - 1), 3) for i in idx)

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
    dump = json.loads(dump_text)
    return paths.write_text(raw_path(clip_id), dump_text), dump


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
    return r'''
string GUID="%s"; long FID=%dL; int W=%d,H=%d;
var VN=new string[]{%s}; var VX=new float[]{%s}; var VY=new float[]{%s}; var VZ=new float[]{%s};
var FR=new float[]{%s};

string clipPath = UnityEditor.AssetDatabase.GUIDToAssetPath(GUID);
UnityEngine.AnimationClip clip=null;
foreach (var o in UnityEditor.AssetDatabase.LoadAllAssetsAtPath(clipPath)) { var ac=o as UnityEngine.AnimationClip; if(ac==null)continue; string gg; long lid; if(UnityEditor.AssetDatabase.TryGetGUIDAndLocalFileIdentifier(ac,out gg,out lid)&&lid==FID){clip=ac;break;} }
if(clip==null) return "CLIP NOT FOUND";

string avatarPath=null;
foreach (var g in UnityEditor.AssetDatabase.FindAssets("nurse_avatar")) { var p=UnityEditor.AssetDatabase.GUIDToAssetPath(g); if(p.ToLower().EndsWith(".fbx")){avatarPath=p;break;} }
var model = UnityEditor.AssetDatabase.LoadAssetAtPath(avatarPath, typeof(UnityEngine.GameObject)) as UnityEngine.GameObject;
var inst = UnityEngine.Object.Instantiate(model) as UnityEngine.GameObject;
inst.transform.position = UnityEngine.Vector3.zero; inst.transform.rotation = UnityEngine.Quaternion.identity;

foreach (var tr in inst.GetComponentsInChildren<UnityEngine.Transform>(true)) tr.gameObject.layer = 31;
var lightGO = new UnityEngine.GameObject("MKB_Light"); var light = lightGO.AddComponent<UnityEngine.Light>();
light.type = UnityEngine.LightType.Directional; light.intensity = 1.2f; lightGO.transform.rotation = UnityEngine.Quaternion.Euler(50f,-30f,0f);
// Soft fill from the opposite side so a shadowed limb (e.g. a hand against the body) keeps some form and
// doesn't sink to solid black -- helps read overlapping same-colour parts. The key light stays dominant.
var fillGO = new UnityEngine.GameObject("MKB_Fill"); var fill = fillGO.AddComponent<UnityEngine.Light>();
fill.type = UnityEngine.LightType.Directional; fill.intensity = 0.4f; fillGO.transform.rotation = UnityEngine.Quaternion.Euler(25f,150f,0f);
var camGO = new UnityEngine.GameObject("MKB_RenderCam"); var cam = camGO.AddComponent<UnityEngine.Camera>();
cam.clearFlags = UnityEngine.CameraClearFlags.SolidColor; cam.backgroundColor = new UnityEngine.Color(0.25f,0.27f,0.30f,1f);
cam.fieldOfView=35f; cam.cullingMask = 1<<31; cam.nearClipPlane=0.03f; cam.farClipPlane=50f;
int SS = 2;   // supersample: render at SSx then downscale -> clean anti-aliasing, independent of the URP MSAA path
var rt = new UnityEngine.RenderTexture(W*SS,H*SS,24); rt.antiAliasing = 4; cam.targetTexture = rt;
var rends = inst.GetComponentsInChildren<UnityEngine.SkinnedMeshRenderer>();

// A simple ground plane on the SAME isolation layer, so the VLM can see whether feet are planted on it.
var floor = UnityEngine.GameObject.CreatePrimitive(UnityEngine.PrimitiveType.Plane);
foreach (var fcol in floor.GetComponents<UnityEngine.Collider>()) UnityEngine.Object.DestroyImmediate(fcol);
floor.layer = 31; floor.transform.position = new UnityEngine.Vector3(0f,0f,0f); floor.transform.localScale = new UnityEngine.Vector3(0.6f,1f,0.6f);
var fsh = UnityEngine.Shader.Find("Universal Render Pipeline/Lit"); if(fsh==null) fsh=UnityEngine.Shader.Find("Standard"); if(fsh==null) fsh=UnityEngine.Shader.Find("Sprites/Default");
var fmat = new UnityEngine.Material(fsh);
if(fmat.HasProperty("_BaseColor")) fmat.SetColor("_BaseColor", new UnityEngine.Color(0.42f,0.44f,0.47f,1f)); else fmat.color = new UnityEngine.Color(0.42f,0.44f,0.47f,1f);
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
    inst.transform.position = UnityEngine.Vector3.zero;
    foreach(var sr in rends){ var bm=new UnityEngine.Mesh(); sr.BakeMesh(bm); var vs=bm.vertices; var l2w=sr.transform.localToWorldMatrix; for(int k=0;k<vs.Length;k++){ float wy=l2w.MultiplyPoint3x4(vs[k]).y; if(wy<groundY)groundY=wy; } UnityEngine.Object.DestroyImmediate(bm); }
  }
  if(groundY>1e29f||groundY<-1e29f) groundY=0f;
  for(int fi=0; fi<FR.Length; fi++){
    UnityEditor.AnimationMode.SampleAnimationClip(inst, clip, clip.length*FR[fi]);
    inst.transform.position = new UnityEngine.Vector3(0f, -groundY, 0f);   // rest the lowest-ever point on y=0
    UnityEngine.Bounds b=new UnityEngine.Bounds(inst.transform.position, UnityEngine.Vector3.one); bool has=false;
    foreach(var sr in rends){ if(!has){b=sr.bounds;has=true;} else b.Encapsulate(sr.bounds); }
    UnityEngine.Vector3 c=b.center; float r=b.extents.magnitude; if(r<0.6f)r=0.95f;
    float dist = r / UnityEngine.Mathf.Tan(cam.fieldOfView*0.5f*UnityEngine.Mathf.Deg2Rad) * 1.25f;
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
      string fn = VN[vi]+"_f"+((int)(FR[fi]*100))+".png";
      summary.AppendLine(fn+"|"+System.Convert.ToBase64String(png));
      UnityEngine.Object.DestroyImmediate(tex); wrote++;
    }
  }
} finally {
  UnityEditor.AnimationMode.StopAnimationMode();
  cam.targetTexture=null; UnityEngine.Object.DestroyImmediate(rt); UnityEngine.Object.DestroyImmediate(camGO);
  UnityEngine.Object.DestroyImmediate(lightGO); UnityEngine.Object.DestroyImmediate(fillGO); UnityEngine.Object.DestroyImmediate(floor); UnityEngine.Object.DestroyImmediate(inst);
}
return summary.ToString();
''' % (clip["guid"], int(clip["file_id"]), w, h, vnames, vdx, vdy, vdz, fr)


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
