using System.IO;
using UnityEditor;
using UnityEngine;

namespace MixamoImport
{
    /// <summary>
    /// Import settings for the bulk Mixamo corpus under Assets/Animations/Mixamo30/.
    ///
    /// WHY THIS EXISTS AT ALL. Every Mixamo FBX names its take `mixamo.com`, and a freshly
    /// imported one arrives with an empty clip list. Left alone, two thousand files would all
    /// produce a clip called `mixamo.com`, and everything downstream addresses a clip BY NAME --
    /// the knowledge base's `source_clip.clip_name`, `extract.py register`, `ClipLibrary.Resolve`.
    /// `register` refuses to guess between same-named clips, which is the correct behaviour and
    /// also means an unnamed corpus is an unusable one. So the name is set here, explicitly,
    /// rather than left to whatever Unity would infer.
    ///
    /// WHY THE SETTINGS ARE COPIED RATHER THAN CHOSEN. The target is
    /// Assets/Animations/NurseAnimation/X Bot@Typing.fbx: it is Mixamo-sourced, it is the source
    /// clip behind the knowledge base's `typing` entry, and it demonstrably works end to end. Its
    /// meta is the specification for everything below.
    ///
    /// The corpus is deliberately NOT retargeted here. A Mixamo clip copies the MIXAMO avatar and
    /// is baked into Unity's humanoid muscle space; retargeting onto nurse_avatar -- a completely
    /// different, Unreal-named skeleton -- happens at play time. That is why Jill, Dana and Kate
    /// can all be driven by a clip authored for none of them.
    /// </summary>
    public sealed class MixamoCorpusPostprocessor : AssetPostprocessor
    {
        /// <summary>Only files under here are touched. Everything else in the project keeps
        /// whatever settings it already has, including the inconsistent older imports.</summary>
        private const string CorpusRoot = "Assets/Animations/Mixamo30/";

        /// <summary>The rig the corpus is exported against on mixamo.com, already in the project.
        /// Copying one avatar is the point: leaving avatarSetup at CreateFromThisModel would
        /// generate a redundant Avatar sub-asset per file, which at corpus scale is two thousand
        /// of them and a correspondingly slow reimport.</summary>
        private const string SourceAvatarPath = "Assets/Animations/RawAnimAssets/X Bot.fbx";

        private bool InCorpus { get { return assetPath.Replace('\\', '/').StartsWith(CorpusRoot); } }

        /// <summary>The Avatar inside the reference rig.
        ///
        /// It has to be dug out of the full sub-asset list: the MAIN asset of an FBX is a
        /// GameObject, so `LoadAssetAtPath&lt;Avatar&gt;` returns null and every import silently
        /// falls through to the not-configured branch. That is exactly what happened on the
        /// first pilot import.</summary>
        private static Avatar FindSourceAvatar()
        {
            UnityEngine.Object[] all = AssetDatabase.LoadAllAssetsAtPath(SourceAvatarPath);
            if (all == null) return null;
            for (int i = 0; i < all.Length; i++)
            {
                Avatar avatar = all[i] as Avatar;
                if (avatar != null && avatar.isValid && avatar.isHuman) return avatar;
            }
            return null;
        }

        private void OnPreprocessModel()
        {
            if (!InCorpus) return;

            ModelImporter importer = (ModelImporter)assetImporter;

            Avatar source = FindSourceAvatar();
            if (source == null)
            {
                // Loudly, and without importing anything half-configured. A clip that quietly
                // came in as Generic looks identical in the project window and fails much later,
                // at register or at play time.
                Debug.LogError(string.Format(
                    "[MixamoImport] {0}: no valid humanoid Avatar found in {1}, so the corpus "
                    + "preset cannot be applied. Import that model first, then reimport this folder.",
                    Path.GetFileName(assetPath), SourceAvatarPath));
                return;
            }

            importer.animationType = ModelImporterAnimationType.Human;
            importer.avatarSetup = ModelImporterAvatarSetup.CopyFromOther;
            importer.sourceAvatar = source;
            importer.humanoidOversampling = ModelImporterHumanoidOversampling.X1;

            importer.importAnimation = true;
            importer.resampleCurves = true;
            importer.animationCompression = ModelImporterAnimationCompression.Optimal;

            // A Without-Skin export carries no mesh, material, camera or light. Saying so up front
            // saves the importer the work of looking, times two thousand files.
            importer.materialImportMode = ModelImporterMaterialImportMode.None;
            importer.importCameras = false;
            importer.importLights = false;
            importer.importVisibility = false;
            importer.importBlendShapes = false;
        }

        private void OnPreprocessAnimation()
        {
            if (!InCorpus) return;

            ModelImporter importer = (ModelImporter)assetImporter;
            ModelImporterClipAnimation[] clips = importer.defaultClipAnimations;
            if (clips == null || clips.Length == 0)
            {
                Debug.LogWarning(string.Format(
                    "[MixamoImport] {0}: no animation take found.", Path.GetFileName(assetPath)));
                return;
            }

            string stem = Path.GetFileNameWithoutExtension(assetPath);
            if (clips.Length > 1)
            {
                // Mixamo motion exports hold exactly one take. If one ever holds more, say so
                // rather than silently dropping the extras onto suffixed names nobody expects.
                Debug.LogWarning(string.Format(
                    "[MixamoImport] {0}: {1} takes in one file; naming them {2}, {2}_2, ...",
                    Path.GetFileName(assetPath), clips.Length, stem));
            }

            for (int i = 0; i < clips.Length; i++)
            {
                clips[i].name = i == 0 ? stem : string.Format("{0}_{1}", stem, i + 1);

                // Root motion baked into the pose, matching every Mixamo clip already in the
                // project and the knowledge base's `has_root_motion=false`. The FBX still carries
                // the displacement, so a clip that needs to travel is one checkbox away.
                clips[i].lockRootRotation = true;    // Root Transform Rotation   -> Bake Into Pose
                clips[i].lockRootHeightY = true;     // Root Transform Position Y -> Bake Into Pose
                clips[i].lockRootPositionXZ = true;  // Root Transform Position XZ-> Bake Into Pose
                clips[i].keepOriginalOrientation = false;
                clips[i].keepOriginalPositionY = true;
                clips[i].keepOriginalPositionXZ = false;
                clips[i].heightFromFeet = false;
            }

            importer.clipAnimations = clips;
        }
    }
}
