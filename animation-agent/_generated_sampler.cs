
var BONES = new string[]{"Chest","Head","Hips","LeftFoot","LeftHand","LeftIndexDistal","LeftIndexIntermediate","LeftIndexProximal","LeftLittleDistal","LeftLittleIntermediate","LeftLittleProximal","LeftLowerArm","LeftLowerLeg","LeftMiddleDistal","LeftMiddleIntermediate","LeftMiddleProximal","LeftRingDistal","LeftRingIntermediate","LeftRingProximal","LeftShoulder","LeftThumbDistal","LeftThumbIntermediate","LeftThumbProximal","LeftToes","LeftUpperArm","LeftUpperLeg","Neck","RightFoot","RightHand","RightIndexDistal","RightIndexIntermediate","RightIndexProximal","RightLittleDistal","RightLittleIntermediate","RightLittleProximal","RightLowerArm","RightLowerLeg","RightMiddleDistal","RightMiddleIntermediate","RightMiddleProximal","RightRingDistal","RightRingIntermediate","RightRingProximal","RightShoulder","RightThumbDistal","RightThumbIntermediate","RightThumbProximal","RightToes","RightUpperArm","RightUpperLeg"};
var IDS   = new string[]{"Idle","Typing","Walk_N","nurse_bvm_2","nurse_check_pulse","nurse_cpr_30","nurse_give_meds","nurse_grab_aspirin"};
var GUIDS = new string[]{"48e5834385783b14e8615a5bb1b7c0b0","5e6e65f406a6f4f43a6152ec70c028a7","e752a99587aacfb4dbeba586bfd83da8","60951abc95f08324fbfe18c463b3fadf","17f12876b88d7e449bb424d3e0e56e24","586b2e222cc31d0459643d55ec00d5a4","6301e229f38fceb46a5a24ae267a980b","4dde3e992c4eac342a862676aa2d3574"};
var FIDS  = new long[]{7400000L,-203655887218126122L,7400000L,1827226128182048838L,1827226128182048838L,1827226128182048838L,1827226128182048838L,1827226128182048838L};
string OUTDIR = "F:/Research/AI_agent/Animation/Animation_agent/Project/Animation/agent/kb/_raw";
int SMIN=2, SMAX=600;
System.IO.Directory.CreateDirectory(OUTDIR);

string avatarPath = null;
foreach (var g in UnityEditor.AssetDatabase.FindAssets("nurse_avatar")) { var p = UnityEditor.AssetDatabase.GUIDToAssetPath(g); if (p.ToLower().EndsWith(".fbx")) { avatarPath = p; break; } }
var model = UnityEditor.AssetDatabase.LoadAssetAtPath(avatarPath, typeof(UnityEngine.GameObject)) as UnityEngine.GameObject;
var inst = UnityEngine.Object.Instantiate(model) as UnityEngine.GameObject;
var anim = inst.GetComponent<UnityEngine.Animator>(); if (anim==null) anim = inst.GetComponentInChildren<UnityEngine.Animator>();

var HB = typeof(UnityEngine.HumanBodyBones);
var tf = new UnityEngine.Transform[BONES.Length];
for (int i=0;i<BONES.Length;i++){ tf[i] = anim.GetBoneTransform((UnityEngine.HumanBodyBones)System.Enum.Parse(HB, BONES[i])); }

var summary = new System.Text.StringBuilder();
UnityEditor.AnimationMode.StartAnimationMode();
try {
  for (int ci=0; ci<IDS.Length; ci++) {
    string path = UnityEditor.AssetDatabase.GUIDToAssetPath(GUIDS[ci]);
    UnityEngine.AnimationClip clip=null;
    foreach (var o in UnityEditor.AssetDatabase.LoadAllAssetsAtPath(path)) { var ac=o as UnityEngine.AnimationClip; if(ac==null)continue; string gg; long lid; if(UnityEditor.AssetDatabase.TryGetGUIDAndLocalFileIdentifier(ac,out gg,out lid)&&lid==FIDS[ci]){clip=ac;break;} }
    if (clip==null){ summary.AppendLine(IDS[ci]+": CLIP NOT FOUND"); continue; }
    int N = UnityEngine.Mathf.Clamp(UnityEngine.Mathf.RoundToInt(clip.length*clip.frameRate), SMIN, SMAX);

    var sb = new System.Text.StringBuilder();
    sb.Append("{\"clip\":\""+IDS[ci]+"\",\"frames\":"+N+",\"length\":"+clip.length.ToString("R")+",\"frame_rate\":"+clip.frameRate.ToString("R")+",\"bones\":{");
    // collect per-frame data
    var rootPos = new UnityEngine.Vector3[N]; var rootFwd = new UnityEngine.Vector3[N];
    var data = new UnityEngine.Vector3[BONES.Length][];
    for (int b=0;b<BONES.Length;b++) data[b]=new UnityEngine.Vector3[N];
    for (int fr=0; fr<N; fr++) {
      float t = (N<=1)?0f:(clip.length*fr/(N-1));
      UnityEditor.AnimationMode.SampleAnimationClip(inst, clip, t);
      rootPos[fr]=inst.transform.position; rootFwd[fr]=inst.transform.forward;
      for (int b=0;b<BONES.Length;b++){ data[b][fr] = inst.transform.InverseTransformPoint(tf[b].position); }
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
    sb.Append("]}");
    System.IO.File.WriteAllText(OUTDIR+"/"+IDS[ci]+".json", sb.ToString());
    summary.AppendLine(IDS[ci]+": wrote "+N+" frames, "+BONES.Length+" bones");
  }
} finally { UnityEditor.AnimationMode.StopAnimationMode(); UnityEngine.Object.DestroyImmediate(inst); }
return summary.ToString();
