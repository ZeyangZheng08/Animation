using System.Collections.Generic;
using System.IO;
using AgentRuntime;
using UnityEditor;
using UnityEngine;
using UnityEngine.Animations.Rigging;

namespace AgentRuntimeEditor
{
    /// <summary>
    /// Builds the runtime wiring: the scene registry, the clip library, and the components on the
    /// driven character. Editor-only, re-runnable, and idempotent.
    ///
    /// THIS IS THE RIGHT USE OF THE EDITOR CHANNEL. Classification and asset resolution genuinely need
    /// AssetDatabase and the open scene, and both are one-time authoring steps. What ships is the
    /// serialized result: at runtime nothing here is reachable, and the executor never asks the editor
    /// for anything. That is what keeps the offline channel and the runtime channel separate.
    ///
    /// The seed table below is deliberately small and hand-reviewable. The scene has 600 GameObjects and
    /// almost none matter to a motion; enumerating the ones that do is a twenty-row job, not a labelling
    /// project. Anchors are picked up automatically from the existing `animpts` hierarchy, which already
    /// encodes where a nurse stands and which way she faces.
    /// </summary>
    public static class AgentRuntimeSetup
    {
        private const string Root = "AgentRuntime";
        private const string NursePath = "DEMO/Nurse4Agent/CPRNurse";

        private sealed class Seed
        {
            public string Match;        // exact GameObject name
            public string Label;
            public string Category;
            public string[] Aliases;
            public bool Carriable;      // picked up and taken along, vs used where it stands
        }

        // Names verified present in EmergencyRoom.unity. Aliases are the knowledge base's contact
        // vocabulary -- they are what joins "the motion touches aspirin_bottle" to a thing in the room.
        //
        // CARRIABLE IS AUTHORED, AND IT HAS TO BE. It was a category test first -- consumables only --
        // and that is wrong in both directions on the same category: the bag valve mask is a `device`
        // the nurse holds in both hands, and the laptop is a `device` she types on where it sits. It is
        // not measurable either; both are about thirty centimetres across. And the knowledge base
        // cannot settle it, because it spells the two contacts identically: `typing` records
        // `contact: object:keyboard, role: primary` and `giving_pills` records
        // `contact: object:pills, role: primary`. Whether a hand grips a thing and takes it or works on
        // it in place is a fact about the object in this room, so it is written here once, next to the
        // aliases, which are the same kind of fact. Default false -- forgetting to annotate something
        // new refuses to carry it, which is a visible refusal rather than a bed hanging off a wrist.
        private static readonly Seed[] Seeds =
        {
            new Seed { Match = "Aspirin Bottle", Label = "Aspirin Bottle", Category = "consumable",
                       Carriable = true,
                       Aliases = new[] { "aspirin_bottle", "pills", "medicine bottle", "bottle" } },
            new Seed { Match = "pill_bottle", Label = "Pill Bottle", Category = "consumable",
                       Carriable = true,
                       Aliases = new[] { "pills", "pill_bottle" } },
            // Held in both hands to ventilate a patient. A device, and carried -- which is exactly the
            // case the old category rule got wrong.
            new Seed { Match = "BVM", Label = "Bag Valve Mask", Category = "device", Carriable = true,
                       Aliases = new[] { "bvm_mask", "bvm_bag", "bvm" } },
            new Seed { Match = "MonitorVitals", Label = "Vitals Monitor", Category = "device",
                       Aliases = new[] { "monitor", "vitals", "cardiac monitor" } },
            // Typed on where it sits. Carrying it is what put it on her wrist and in her lap.
            new Seed { Match = "Laptop", Label = "Laptop", Category = "device",
                       Aliases = new[] { "keyboard", "laptop", "computer" } },
            new Seed { Match = "Computer", Label = "Computer", Category = "device",
                       Aliases = new[] { "keyboard", "computer" } },
            new Seed { Match = "patient_avatar", Label = "Patient", Category = "character",
                       Aliases = new[] { "patient", "patient_chest", "patient_wrist" } },
            new Seed { Match = "hospital_bed", Label = "Hospital Bed", Category = "furniture",
                       Aliases = new[] { "bed" } },
            new Seed { Match = "Emergency Cart", Label = "Emergency Cart", Category = "furniture",
                       Aliases = new[] { "cart", "crash cart" } },
            new Seed { Match = "Bedside Cabinet", Label = "Bedside Cabinet", Category = "furniture",
                       Aliases = new[] { "cabinet" } },
            new Seed { Match = "IV Stand", Label = "IV Stand", Category = "device",
                       Aliases = new[] { "iv", "iv_stand" } },
            // A generated sit-down has to land on something real. `typing` is the only seated action in
            // the library and nothing in the scene was annotated to sit on, so the agent could not have
            // found a seat if it wanted one.
            new Seed { Match = "Chair", Label = "Chair", Category = "seating",
                       Aliases = new[] { "chair", "seat", "stool" } },
            new Seed { Match = "table", Label = "Table", Category = "furniture",
                       Aliases = new[] { "table", "desk", "workstation" } },
        };

        [MenuItem("Tools/Animation Agent/Set Up Runtime In This Scene")]
        public static void SetUp()
        {
            GameObject root = GameObject.Find(Root) ?? new GameObject(Root);

            SceneRegistry registry = Ensure<SceneRegistry>(root);
            SceneQueryService query = Ensure<SceneQueryService>(root);
            ClipLibrary clips = Ensure<ClipLibrary>(root);
            AgentLink link = Ensure<AgentLink>(root);
            // Nothing in the scene displays a turn any more. The console is a terminal on the Windows
            // side, attached to the agent's own console channel; a scene set up before that still
            // carries the component it used to have, so it is stripped here.
            RemoveLegacyConsole(root);

            int objects = BuildRegistry(registry);
            int resolved = BuildClipLibrary(clips);
            AgentCharacter[] characters = SetUpCharacters();

            Wire(query, "registry", registry);
            Wire(query, "characters", characters);
            Wire(link, "sceneQuery", query);
            Wire(link, "characters", characters);

            List<string> named = new List<string>();
            for (int i = 0; i < characters.Length; i++)
            {
                Wire(characters[i], "clips", clips);
                EditorUtility.SetDirty(characters[i].gameObject);
                named.Add(characters[i].DisplayName + " (" + characters[i].Id + ")");
            }

            EditorUtility.SetDirty(root);
            UnityEditor.SceneManagement.EditorSceneManager.MarkSceneDirty(
                UnityEngine.SceneManagement.SceneManager.GetActiveScene());

            Debug.Log(string.Format(
                "[AgentRuntime] registry {0} objects, clip library {1}/8 actions, {2} character(s): {3}",
                objects, resolved, characters.Length,
                characters.Length == 0 ? "NONE" : string.Join(", ", named)));
        }

        // ---- registry --------------------------------------------------------------------------

        // Higher wins. Geometry outranks everything because an entry exists so a motion contact can be
        // grounded on it and nothing grounds on an empty transform. Belonging to the character we drive
        // comes next: when the same prop is duplicated across demo avatars, the one in HER hand is the
        // one she can use, and the others are scenery that happens to share a name.
        private static int Rank(Transform t)
        {
            if (t.GetComponentInChildren<Renderer>(true) == null) return 0;
            for (Transform p = t; p != null; p = p.parent)
            {
                if (p.name == "CPRNurse") return 3;
            }
            return t.gameObject.activeInHierarchy ? 2 : 1;
        }

        // Path alone does not separate siblings: this scene has two `Bedside Cabinet`s under the same
        // parent, so their paths are the same string and the comparison fell back to enumeration order.
        // Position is what actually tells them apart, and it is serialised, so it is stable.
        private static string PathOf(Transform t)
        {
            string path = t.name;
            for (Transform p = t.parent; p != null; p = p.parent) path = p.name + "/" + path;
            return path + "|" + t.position.ToString("F4");
        }

        private static int BuildRegistry(SceneRegistry registry)
        {
            Dictionary<string, Transform> byName = new Dictionary<string, Transform>();
            // Inactive objects MUST be included. NurseAnimatorEvents hides the medicine bottle whenever
            // the Animator's "Hold Pills" is false, so the single most important prop in the demo is
            // inactive at edit time and an active-only search silently misses it.
            Transform[] all = Object.FindObjectsByType<Transform>(
                FindObjectsInactive.Include, FindObjectsSortMode.None);
            // A NAME IS AMBIGUOUS AND THE SCENE IS FULL OF DECOYS, so the choice is ranked rather than
            // left to whichever transform the search happened to return first. Measured in this scene:
            // seven objects called `pill_bottle`, one in each demo nurse's right hand and no standalone
            // prop among them; two `Laptop`s, one an IK helper point at the world origin; two identical
            // `Bedside Cabinet`s. First-found silently picked one, and the pick changed when the scene
            // was reloaded -- the pill bottle moved from our own character's hand to a demo nurse
            // holding one at 2.45 m. Ranking makes the same scene give the same registry every time.
            Dictionary<string, int> rankByName = new Dictionary<string, int>();
            for (int i = 0; i < all.Length; i++)
            {
                int rank = Rank(all[i]);
                Transform existing;
                if (byName.TryGetValue(all[i].name, out existing))
                {
                    int previous = rankByName[all[i].name];
                    if (rank < previous) continue;
                    // Equal rank still has to be decided the same way on every rebuild.
                    if (rank == previous && string.CompareOrdinal(PathOf(all[i]), PathOf(existing)) >= 0)
                    {
                        continue;
                    }
                }
                byName[all[i].name] = all[i];
                rankByName[all[i].name] = rank;
            }

            List<SceneRegistry.Entry> entries = new List<SceneRegistry.Entry>();
            List<string> missing = new List<string>();
            for (int i = 0; i < Seeds.Length; i++)
            {
                Seed seed = Seeds[i];
                Transform t;
                if (!byName.TryGetValue(seed.Match, out t)) { missing.Add(seed.Match); continue; }
                if (t.GetComponentInChildren<Renderer>(true) == null)
                {
                    // A DECOY IS WORSE THAN A GAP. The only `Computer` in this scene is an animation
                    // anchor point under animpts -- no geometry, sitting at ankle height -- and it was
                    // registered as a device aliased "keyboard". Measured: an agent bound both hands to
                    // it, the IK targets landed near the floor, and the gate rejected a plan that was
                    // reasonable given what it had been told. Not registering it leaves the Laptop as
                    // the only thing a typing contact can land on, which is the truth.
                    missing.Add(seed.Match + " (no geometry; not registered)");
                    continue;
                }
                // PER-HAND ANCHORS WERE ALREADY IN THE SCENE and the registry had never learned about
                // them. `NurseIKHelper` drives typing by parenting four transforms under a holder --
                // LaptopHandLeft, LaptopHintLeft, LaptopHandRight, LaptopHintRight -- and putting the
                // hands exactly there. Measured: with them engaged both hands sit 0.000 m from the
                // laptop; with only the registry's single grab point, both wrists collapse onto it.
                // The convention is the object's label plus Hand/Hint and a side, so it is read rather
                // than re-authored, and objects without them simply have none.
                Transform lh, lhint, rh, rhint;
                string key = seed.Label.Replace(" ", "");
                byName.TryGetValue(key + "HandLeft", out lh);
                byName.TryGetValue(key + "HintLeft", out lhint);
                byName.TryGetValue(key + "HandRight", out rh);
                byName.TryGetValue(key + "HintRight", out rhint);

                entries.Add(new SceneRegistry.Entry
                {
                    target = t, label = seed.Label, category = seed.Category, aliases = seed.Aliases,
                    carriable = seed.Carriable, surfaceHeight = SurfaceHeight(t),
                    leftHandAnchor = lh, leftHintAnchor = lhint,
                    rightHandAnchor = rh, rightHintAnchor = rhint
                });
            }

            // Anchors come free: the animpts hierarchy already says where to stand, and its `...Face`
            // children already say which way to look.
            Transform animpts;
            if (byName.TryGetValue("animpts", out animpts))
            {
                foreach (Transform child in animpts)
                {
                    if (child.name.EndsWith("Face")) continue;
                    Transform face = child.Find(child.name + "Face");
                    if (face == null)
                    {
                        foreach (Transform c in child) { if (c.name.EndsWith("Face")) { face = c; break; } }
                    }
                    entries.Add(new SceneRegistry.Entry
                    {
                        target = child, label = child.name, category = "anchor",
                        aliases = new string[0], faceAnchor = face
                    });
                }
            }
            else
            {
                missing.Add("animpts (no anchors registered)");
            }

            if (missing.Count > 0)
            {
                Debug.LogWarning("[AgentRuntime] not found in this scene: " + string.Join(", ", missing));
            }
            registry.Replace(entries);
            EditorUtility.SetDirty(registry);
            return entries.Count;
        }

        // ---- clip library ----------------------------------------------------------------------

        private static int BuildClipLibrary(ClipLibrary library)
        {
            // Built from the manifest, which indexes exactly the accepted records and already carries
            // the identity this needs (action_id + source_clip) -- so no record is opened, where
            // reading the store would mean parsing 2454 files to keep 8 (ADR 0016).
            //
            // The loop this replaces globbed "*.json" at the KB ROOT and skipped three shared files by
            // name. That has returned ZERO items since ADR 0012 moved the records down into actions/:
            // the only *.json left at the root were the three it skipped. It failed silently, because
            // an empty library is indistinguishable from a library nobody asked about.
            string kbDir = Path.Combine(Path.GetDirectoryName(Application.dataPath),
                                        "agent/animation_knowledge_base");
            string manifestPath = Path.Combine(kbDir, "manifest.json");
            List<ClipLibrary.Item> items = new List<ClipLibrary.Item>();
            if (!File.Exists(manifestPath))
            {
                Debug.LogWarning("[AgentRuntime] no knowledge base manifest at " + manifestPath);
                return 0;
            }

            var manifest = Newtonsoft.Json.Linq.JObject.Parse(File.ReadAllText(manifestPath));
            var entries = manifest["actions"] as Newtonsoft.Json.Linq.JArray;
            if (entries == null)
            {
                Debug.LogWarning("[AgentRuntime] manifest has no 'actions' array: " + manifestPath);
                return 0;
            }

            foreach (var entry in entries)
            {
                if (entry.Value<string>("status") != "accepted") continue;
                var source = entry["source_clip"] as Newtonsoft.Json.Linq.JObject;
                if (source == null) continue;

                string guid = source.Value<string>("guid");
                string clipName = source.Value<string>("clip_name");
                string assetPath = AssetDatabase.GUIDToAssetPath(guid);
                AnimationClip clip = FindClip(assetPath, clipName);
                if (clip == null)
                {
                    Debug.LogWarning("[AgentRuntime] no clip for " + entry.Value<string>("action_id") +
                                     " (guid " + guid + ")");
                    continue;
                }
                items.Add(new ClipLibrary.Item
                {
                    actionId = entry.Value<string>("action_id"), clipName = clipName, clip = clip
                });
            }
            library.Replace(items);
            EditorUtility.SetDirty(library);
            return items.Count;
        }

        /// <summary>Top of an object's renderer bounds, in world metres. -1 when it has no renderers.
        ///
        /// Renderers, not colliders: the scene has 13 colliders across 600 objects, so a collider-based
        /// measurement would silently answer for almost nothing. Measured at authoring time and
        /// serialised, so the runtime never walks a hierarchy to answer "how high is the seat".
        /// </summary>
        private static float SurfaceHeight(Transform t)
        {
            Renderer[] renderers = t.GetComponentsInChildren<Renderer>(true);
            if (renderers.Length == 0) return -1f;
            float top = float.NegativeInfinity;
            for (int i = 0; i < renderers.Length; i++)
            {
                float y = renderers[i].bounds.max.y;
                if (y > top) top = y;
            }
            return float.IsNegativeInfinity(top) ? -1f : top;
        }

        private static AnimationClip FindClip(string assetPath, string clipName)
        {
            if (string.IsNullOrEmpty(assetPath)) return null;
            Object[] subs = AssetDatabase.LoadAllAssetsAtPath(assetPath);
            AnimationClip fallback = null;
            for (int i = 0; i < subs.Length; i++)
            {
                AnimationClip c = subs[i] as AnimationClip;
                if (c == null || c.name.StartsWith("__")) continue;
                if (c.name == clipName) return c;
                fallback = c;
            }
            return fallback;
        }

        // ---- character -------------------------------------------------------------------------

        /// <summary>The nurses the agent can drive, and what to call them.
        ///
        /// A NAME IS THE POINT. "Jill, walk to the patient" has to reach one character out of several,
        /// and the names are already in the scene — each nurse carries a floating nameplate, a UI Text
        /// under `&lt;name&gt; Canvas`. But a nameplate is positioned near its nurse rather than parented
        /// under her, so pairing them means guessing by distance, and a wrong pairing would silently
        /// send an instruction to the wrong person. The mapping is authored here instead, where it can
        /// be read and corrected, which is the same reason the registry seed table is a list.
        ///
        /// Three, because three is how many are named. The other four demo nurses are scenery.
        /// </summary>
        private static readonly string[,] Nurses =
        {
            { "CPRNurse", "Jill" },
            { "EKGNurse", "Dana" },
            { "AirwayNurse", "Kate" }
        };

        private static AgentCharacter[] SetUpCharacters()
        {
            List<AgentCharacter> found = new List<AgentCharacter>();
            for (int i = 0; i < Nurses.GetLength(0); i++)
            {
                AgentCharacter character = SetUpCharacter(Nurses[i, 0], Nurses[i, 1]);
                if (character != null) found.Add(character);
            }
            if (found.Count == 0) Debug.LogError("[AgentRuntime] no nurse from the table was found");
            return found.ToArray();
        }

        /// <summary>The one object with this name that is a nurse we can drive, chosen rather than
        /// found-first.
        ///
        /// `AirwayNurse` appears TWICE in this scene, and `GameObject.Find` returns whichever the
        /// search reaches first — which is not stable across a scene reload. The registry already ranks
        /// its way out of the identical problem with seven objects called `pill_bottle`; this ranks the
        /// same way and prints what it picked, so a wrong pick is visible instead of silent. A humanoid
        /// Animator is what makes a transform a nurse rather than a nameplate or an empty.
        /// </summary>
        private static GameObject FindNurse(string name)
        {
            Transform[] all = Object.FindObjectsByType<Transform>(
                FindObjectsInactive.Include, FindObjectsSortMode.None);
            GameObject best = null;
            int bestScore = -1;
            for (int i = 0; i < all.Length; i++)
            {
                if (all[i].name != name) continue;
                Animator animator = all[i].GetComponent<Animator>();
                int score = (animator != null && animator.isHuman ? 4 : 0)
                          + (all[i].GetComponent<UnityEngine.AI.NavMeshAgent>() != null ? 2 : 0)
                          + (all[i].gameObject.activeInHierarchy ? 1 : 0);
                if (score > bestScore) { bestScore = score; best = all[i].gameObject; }
            }
            return best;
        }

        private static AgentCharacter SetUpCharacter(string objectName, string displayName)
        {
            GameObject nurse = FindNurse(objectName);
            if (nurse == null)
            {
                Debug.LogWarning("[AgentRuntime] no nurse called " + objectName
                                 + " in this scene; " + displayName + " will not be drivable");
                return null;
            }
            Animator rig = nurse.GetComponent<Animator>();
            if (rig == null || !rig.isHuman)
            {
                Debug.LogWarning("[AgentRuntime] " + objectName + " has no humanoid Animator, so "
                                 + displayName + " cannot be driven");
                return null;
            }
            // WITHOUT ONE SHE CANNOT GO ANYWHERE, and the failure is at request time rather than here:
            // Locomotion.Go answers "this character has no NavMeshAgent". Only CPRNurse had one, so
            // the two nurses being added would have been drivable for everything except moving.
            // Settings are copied off the one that was tuned rather than left at Unity's defaults --
            // its speed is the one the foot-skate measurement was made against.
            UnityEngine.AI.NavMeshAgent agent = nurse.GetComponent<UnityEngine.AI.NavMeshAgent>();
            if (agent == null)
            {
                agent = nurse.AddComponent<UnityEngine.AI.NavMeshAgent>();
                GameObject reference = FindNurse(Nurses[0, 0]);
                UnityEngine.AI.NavMeshAgent tuned = reference == null
                    ? null : reference.GetComponent<UnityEngine.AI.NavMeshAgent>();
                if (tuned != null && tuned != agent)
                {
                    agent.agentTypeID = tuned.agentTypeID;
                    agent.speed = tuned.speed;
                    agent.angularSpeed = tuned.angularSpeed;
                    agent.acceleration = tuned.acceleration;
                    agent.radius = tuned.radius;
                    agent.height = tuned.height;
                    agent.stoppingDistance = tuned.stoppingDistance;
                }
                Debug.Log("[AgentRuntime] added a NavMeshAgent to " + objectName
                          + (tuned != null ? ", copied from " + Nurses[0, 0] : " with defaults"));
            }

            MotionComposer composer = Ensure<MotionComposer>(nurse);
            IkBinder binder = Ensure<IkBinder>(nurse);
            GateProbe probe = Ensure<GateProbe>(nurse);
            PoseSynth synth = Ensure<PoseSynth>(nurse);
            Locomotion legs = Ensure<Locomotion>(nurse);
            AgentCharacter character = Ensure<AgentCharacter>(nurse);

            Wire(composer, "animator", nurse.GetComponent<Animator>());
            Wire(synth, "composer", composer);
            Wire(synth, "locomotion", legs);
            Wire(legs, "agent", nurse.GetComponent<UnityEngine.AI.NavMeshAgent>());
            Wire(character, "locomotion", legs);
            Wire(character, "id", "chr:" + nurse.name);
            // What a person calls her. The id is what the protocol uses and the name is what an
            // instruction says, and the two are not the same string -- "Jill" is not "chr:CPRNurse".
            Wire(character, "displayName", displayName);
            Wire(character, "composer", composer);
            Wire(character, "ik", binder);
            Wire(character, "gates", probe);
            Wire(character, "synth", synth);

            TwoBoneIKConstraint[] cons = nurse.GetComponentsInChildren<TwoBoneIKConstraint>(true);
            for (int i = 0; i < cons.Length; i++)
            {
                if (cons[i].name == "L_Hand") Wire(binder, "leftHand", cons[i]);
                if (cons[i].name == "R_Hand") Wire(binder, "rightHand", cons[i]);
            }
            MultiAimConstraint[] aims = nurse.GetComponentsInChildren<MultiAimConstraint>(true);
            for (int i = 0; i < aims.Length; i++)
            {
                if (aims[i].name == "HeadAim") Wire(binder, "headAim", aims[i]);
            }

            // The prototype helper writes the same constraint weights every frame; the character
            // disables it while the composer is driving, so they never fight.
            MonoBehaviour legacy = nurse.GetComponent("NurseIKHelper") as MonoBehaviour;
            if (legacy != null) Wire(character, "legacyIkHelper", legacy);

            return character;
        }

        // ---- helpers ---------------------------------------------------------------------------

        private static T Ensure<T>(GameObject go) where T : Component
        {
            T existing = go.GetComponent<T>();
            return existing != null ? existing : go.AddComponent<T>();
        }

        /// <summary>Drop the retired in-scene console. Its script is gone, so the component survives in
        /// an already-set-up scene as a missing-script entry; scoped to the runtime root, which this
        /// setup owns outright, so nothing of the user's is in reach.</summary>
        private static void RemoveLegacyConsole(GameObject root)
        {
            int removed = GameObjectUtility.RemoveMonoBehavioursWithMissingScript(root);
            if (removed > 0)
                Debug.Log("[AgentRuntime] removed " + removed + " retired component(s) from " + root.name
                          + " — drive the agent from a terminal (terminal.ps1)");
        }

        private static void Wire(Object target, string field, object value)
        {
            if (target == null) return;
            SerializedObject so = new SerializedObject(target);
            SerializedProperty p = so.FindProperty(field);
            if (p == null) { Debug.LogWarning("[AgentRuntime] no field " + field + " on " + target); return; }

            if (value is string) p.stringValue = (string)value;
            else if (value is Object) p.objectReferenceValue = (Object)value;
            else if (value is System.Array)
            {
                System.Array arr = (System.Array)value;
                p.arraySize = arr.Length;
                for (int i = 0; i < arr.Length; i++)
                {
                    p.GetArrayElementAtIndex(i).objectReferenceValue = (Object)arr.GetValue(i);
                }
            }
            so.ApplyModifiedPropertiesWithoutUndo();
        }
    }
}
