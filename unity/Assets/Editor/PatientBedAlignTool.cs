// Patient/Bed alignment + awake-runtime-start pose freeze tool (one-shot Editor utility).
//
// Goal: align the EmergencyRoom patient & bed to the VR4Nursing_v2 source, and freeze the patient
// in the source's runtime-START pose ("Idle Awake" + bed "Idle Up") so the edit-mode (non-running)
// pose equals the source's play-start frame, with no clipping. Animators are left DISABLED so
// static == runtime.
//
// USAGE (Unity menu -> Tools/Patient Bed Align/...), or via MCP execute_menu_item:
//   1 - Diagnose (read-only)         dumps current patient/bed/controller/clip/clipping facts to Console
//   2 - Apply (align + freeze pose)  moves patient+bed to target, bakes Idle Awake/Idle Up @t=0,
//                                     disables Animators, marks scene dirty, then runs the clip check
//   3 - Measure Clipping (read-only) reports patient mesh min-Y vs mattress top-Y
// After (2): verify visually, then SAVE the scene (Ctrl+S).
//
// All target values below come from the VR4Nursing_v2 source scene. VERIFY them against the
// output of (1) Diagnose before running (2) Apply, and adjust the consts if the live scene differs.

using System.Text;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;

public static class PatientBedAlignTool
{
    // ---- Config (VERIFY against Diagnose output before Apply) ----
    const string PATIENT_AVATAR = "patient_avatar";  // GO carrying the patient Animator + SkinnedMeshRenderer
    const string BED            = "hospital_bed";     // GO carrying the bed Animator
    const string MATTRESS       = "mattress";         // renderer used as the bed top surface for clip checks

    // Verified VR4Nursing_v2 runtime-start values (2026-06-13): patient_avatar WORLD transform.
    // (In v2 the avatar's local pos under DEMO/Patient is (0,0,0); container world == avatar world.)
    static readonly Vector3    PATIENT_WORLD_POS = new Vector3(-1.94f, 0.769f, -3.072f);
    static readonly Quaternion PATIENT_WORLD_ROT = Quaternion.Euler(0f, -90f, 0f);
    const float                PATIENT_SCALE     = 0.9f;

    static readonly Vector3    BED_WORLD_POS = new Vector3(-1.167f, 0f, -3.071f);
    static readonly Quaternion BED_WORLD_ROT = Quaternion.Euler(0f, 90f, 0f);

    // Preferred runtime-start states (fallback: the base layer's defaultState).
    const string PATIENT_STATE = "Idle Awake";
    const string BED_STATE     = "Idle Up";

    const string TAG = "[PBA] ";

    // ---------------- Menu items ----------------

    [MenuItem("Tools/Patient Bed Align/1 - Diagnose (read-only)")]
    public static void Diagnose()
    {
        var sb = new StringBuilder();
        sb.AppendLine(TAG + "DIAGNOSE ----------------------------------------");

        var patient  = FindInScene(PATIENT_AVATAR);
        var bed       = FindInScene(BED);
        var mattress  = FindInScene(MATTRESS);

        if (patient == null) sb.AppendLine(TAG + "PATIENT '" + PATIENT_AVATAR + "' NOT FOUND");
        else
        {
            sb.AppendLine(TAG + "PATIENT '" + patient.name + "' path=" + Path(patient.transform));
            sb.AppendLine(TAG + "  world pos=" + F(patient.transform.position) + " eulr=" + F(patient.transform.eulerAngles) + " lscale=" + F(patient.transform.localScale));
            DescribeAnimator(patient, PATIENT_STATE, sb);
            sb.AppendLine(TAG + "  patient mesh minY=" + PatientMeshMinWorldY(patient).ToString("F4"));
        }

        if (bed == null) sb.AppendLine(TAG + "BED '" + BED + "' NOT FOUND");
        else
        {
            sb.AppendLine(TAG + "BED '" + bed.name + "' path=" + Path(bed.transform));
            sb.AppendLine(TAG + "  world pos=" + F(bed.transform.position) + " eulr=" + F(bed.transform.eulerAngles) + " lscale=" + F(bed.transform.localScale));
            DescribeAnimator(bed, BED_STATE, sb);
        }

        if (mattress == null) sb.AppendLine(TAG + "MATTRESS '" + MATTRESS + "' NOT FOUND (clip check will skip)");
        else sb.AppendLine(TAG + "MATTRESS topY=" + MattressTopY(mattress).ToString("F4"));

        Debug.Log(sb.ToString());
    }

    [MenuItem("Tools/Patient Bed Align/2 - Apply (align + freeze awake pose)")]
    public static void Apply()
    {
        var patient = FindInScene(PATIENT_AVATAR);
        var bed      = FindInScene(BED);
        if (patient == null) { Debug.LogError(TAG + "Abort: patient '" + PATIENT_AVATAR + "' not found."); return; }
        if (bed == null)      { Debug.LogError(TAG + "Abort: bed '" + BED + "' not found."); return; }

        // Bed first (so the patient rests on the raised bed).
        var bedAnim = bed.GetComponent<Animator>();
        var bedClip = GetBaseLayerClip(bedAnim, BED_STATE);
        if (bedClip != null) BakeClipPose(bed, bedClip, 0f);
        else Debug.LogWarning(TAG + "Bed clip ('" + BED_STATE + "'/default) not found; bed pose left as-is.");
        bed.transform.position = BED_WORLD_POS;
        bed.transform.rotation = BED_WORLD_ROT;
        if (bedAnim != null) bedAnim.enabled = false;

        // Patient: bake Idle Awake @ t=0, then set root world transform (SampleAnimation overrides the root).
        var patAnim = patient.GetComponent<Animator>();
        var patClip = GetBaseLayerClip(patAnim, PATIENT_STATE);
        if (patClip != null) BakeClipPose(patient, patClip, 0f);
        else Debug.LogWarning(TAG + "Patient clip ('" + PATIENT_STATE + "'/default) not found; pose left as-is.");
        patient.transform.position   = PATIENT_WORLD_POS;
        patient.transform.rotation   = PATIENT_WORLD_ROT;
        patient.transform.localScale = new Vector3(PATIENT_SCALE, PATIENT_SCALE, PATIENT_SCALE);
        if (patAnim != null) patAnim.enabled = false;

        EditorSceneManager.MarkSceneDirty(SceneManager.GetActiveScene());

        Debug.Log(TAG + "APPLIED. Patient @" + F(patient.transform.position) + " rot " + F(patient.transform.eulerAngles)
            + " | Bed @" + F(bed.transform.position) + " rot " + F(bed.transform.eulerAngles)
            + " | Animators disabled. Running clip check...");
        MeasureClipping();
        Debug.Log(TAG + "Verify visually, then SAVE the scene (Ctrl+S).");
    }

    [MenuItem("Tools/Patient Bed Align/3 - Measure Clipping (read-only)")]
    public static void MeasureClipping()
    {
        var patient  = FindInScene(PATIENT_AVATAR);
        var mattress = FindInScene(MATTRESS);
        if (patient == null) { Debug.LogError(TAG + "patient not found."); return; }
        float minY = PatientMeshMinWorldY(patient);
        if (mattress == null) { Debug.Log(TAG + "patient minY=" + minY.ToString("F4") + " (no mattress to compare)"); return; }
        float topY = MattressTopY(mattress);
        float gap = minY - topY;
        Debug.Log(TAG + "CLIP CHECK: patient minY=" + minY.ToString("F4") + "  mattress topY=" + topY.ToString("F4")
            + "  gap=" + gap.ToString("F4") + (gap < -0.002f ? "  >>> CLIPPING (patient sinks in)" : "  OK (resting/above)"));
    }

    // ---------------- Helpers ----------------

    static void DescribeAnimator(GameObject go, string preferredState, StringBuilder sb)
    {
        var anim = go.GetComponent<Animator>();
        if (anim == null) { sb.AppendLine(TAG + "  (no Animator)"); return; }
        sb.AppendLine(TAG + "  Animator enabled=" + anim.enabled + " controller=" + (anim.runtimeAnimatorController != null ? anim.runtimeAnimatorController.name : "null"));
        var ac = anim.runtimeAnimatorController as AnimatorController;
        if (ac == null) { sb.AppendLine(TAG + "  (controller is not an editable AnimatorController)"); return; }
        foreach (var layer in ac.layers)
        {
            var def = layer.stateMachine.defaultState;
            sb.AppendLine(TAG + "  layer '" + layer.name + "' default=" + (def != null ? def.name : "null")
                + " defaultClip=" + ClipName(def != null ? def.motion : null));
        }
        sb.AppendLine(TAG + "  -> chosen runtime-start clip for freeze: " + ClipName(GetBaseLayerClip(anim, preferredState)));
    }

    static AnimationClip GetBaseLayerClip(Animator anim, string preferredState)
    {
        if (anim == null) return null;
        var ac = anim.runtimeAnimatorController as AnimatorController;
        if (ac == null || ac.layers.Length == 0) return null;
        var sm = ac.layers[0].stateMachine; // base layer
        if (!string.IsNullOrEmpty(preferredState))
            foreach (var cs in sm.states)
                if (cs.state.name == preferredState) return cs.state.motion as AnimationClip;
        return sm.defaultState != null ? sm.defaultState.motion as AnimationClip : null;
    }

    // Bake a clip pose at `time` permanently onto the skeleton (muscle-clip safe: sample in AnimationMode, then persist).
    static void BakeClipPose(GameObject animatorRoot, AnimationClip clip, float time)
    {
        var transforms = animatorRoot.GetComponentsInChildren<Transform>(true);
        bool started = !AnimationMode.InAnimationMode();
        if (started) AnimationMode.StartAnimationMode();
        AnimationMode.BeginSampling();
        AnimationMode.SampleAnimationClip(animatorRoot, clip, time);
        AnimationMode.EndSampling();

        int n = transforms.Length;
        var lp = new Vector3[n]; var lr = new Quaternion[n]; var ls = new Vector3[n];
        for (int i = 0; i < n; i++) { lp[i] = transforms[i].localPosition; lr[i] = transforms[i].localRotation; ls[i] = transforms[i].localScale; }
        if (started) AnimationMode.StopAnimationMode();

        Undo.RecordObjects(transforms, "Bake Clip Pose");
        for (int i = 0; i < n; i++) { transforms[i].localPosition = lp[i]; transforms[i].localRotation = lr[i]; transforms[i].localScale = ls[i]; EditorUtility.SetDirty(transforms[i]); }
        Debug.Log(TAG + "Baked '" + clip.name + "' @t=" + time + " onto " + animatorRoot.name + " (" + n + " transforms).");
    }

    static float PatientMeshMinWorldY(GameObject patient)
    {
        float minY = float.PositiveInfinity;
        foreach (var smr in patient.GetComponentsInChildren<SkinnedMeshRenderer>(true))
        {
            var baked = new Mesh();
            smr.BakeMesh(baked, true);
            var verts = baked.vertices;
            var m = smr.transform.localToWorldMatrix;
            for (int i = 0; i < verts.Length; i++)
            {
                float y = m.MultiplyPoint3x4(verts[i]).y;
                if (y < minY) minY = y;
            }
            UnityEngine.Object.DestroyImmediate(baked);
        }
        return minY;
    }

    static float MattressTopY(GameObject mattress)
    {
        float topY = float.NegativeInfinity;
        foreach (var r in mattress.GetComponentsInChildren<Renderer>(true))
            if (r.bounds.max.y > topY) topY = r.bounds.max.y;
        return topY;
    }

    static GameObject FindInScene(string name)
    {
        var scene = SceneManager.GetActiveScene();
        foreach (var root in scene.GetRootGameObjects())
        {
            var t = FindRecursive(root.transform, name);
            if (t != null) return t.gameObject;
        }
        return null;
    }

    static Transform FindRecursive(Transform t, string name)
    {
        if (t.name == name) return t;
        for (int i = 0; i < t.childCount; i++)
        {
            var r = FindRecursive(t.GetChild(i), name);
            if (r != null) return r;
        }
        return null;
    }

    static string ClipName(Motion m) { var c = m as AnimationClip; return c != null ? c.name : (m != null ? m.name + " (not a clip)" : "null"); }
    static string Path(Transform t) { var s = t.name; while (t.parent != null) { t = t.parent; s = t.name + "/" + s; } return s; }
    static string F(Vector3 v) { return "(" + v.x.ToString("F3") + ", " + v.y.ToString("F3") + ", " + v.z.ToString("F3") + ")"; }
}
