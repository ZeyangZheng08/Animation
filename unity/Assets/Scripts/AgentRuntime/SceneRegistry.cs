using System;
using System.Collections.Generic;
using UnityEngine;

namespace AgentRuntime
{
    /// <summary>
    /// The annotated subset of the scene the agent is allowed to see.
    ///
    /// WHY A REGISTRY AND NOT A SCENE SCAN. EmergencyRoom holds 600 GameObjects. 448 of 451 are
    /// Untagged, only two layers are in use, and there are 13 colliders in the whole scene, so neither
    /// tags nor physics can classify anything. Names follow three different conventions at once
    /// (PascalCase anchors, "Title Case With Spaces" props, snake_case mesh names inherited from source
    /// FBX files), so name matching alone is guesswork.
    ///
    /// So classification is authored once, into this component, by an editor tool driven from a ~20-row
    /// seed table — and at runtime the query service reads only this list. It never calls
    /// FindObjectsOfType, which keeps results stable, small, and independent of what else is in the
    /// scene.
    ///
    /// ALIASES ARE THE JOIN. A person asks for "the pills"; this scene calls that object
    /// `obj:AspirinBottle`. The alias list is what connects the words an instruction arrives in to the
    /// object that actually exists here, and it is the mechanism by which a planned motion becomes a
    /// motion aimed at a real thing. Every object reference in a plan resolves through it — a carry, an
    /// IK binding, a gaze target, a walk destination — and there is nothing to join on the other side:
    /// since ADR 0022 a knowledge-base record names no object at all.
    ///
    /// BOUNDS COME FROM RENDERERS, NOT COLLIDERS. With 13 colliders in 600 objects, any collider-based
    /// query would silently answer for almost nothing. Do not "fix" this later with an OverlapSphere.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class SceneRegistry : MonoBehaviour
    {
        [Serializable]
        public sealed class Entry
        {
            [Tooltip("The object itself. Everything else is derived from or about this transform.")]
            public Transform target;

            [Tooltip("Human-readable name the agent sees. Defaults to the transform name.")]
            public string label;

            [Tooltip("consumable | device | furniture | station | anchor | character")]
            public string category = "prop";

            [Tooltip("Contact names as the motion library spells them, e.g. aspirin_bottle, pills.")]
            public string[] aliases;

            [Tooltip("Where a hand should go to grasp this. Falls back to the object itself.")]
            public Transform grabAnchor;

            [Tooltip("Where each hand goes to work this object, with the elbow hint that keeps the arm "
                     + "from folding the wrong way. Separate from grabAnchor because one point is only "
                     + "right for something picked up: a keyboard is worked with two hands and aiming "
                     + "both at one anchor pulls the wrists together.")]
            public Transform leftHandAnchor;
            public Transform leftHintAnchor;
            public Transform rightHandAnchor;
            public Transform rightHintAnchor;

            [Tooltip("Where the character should stand to act on this.")]
            public Transform standAnchor;

            [Tooltip("Which way to face while standing there.")]
            public Transform faceAnchor;

            [Tooltip("Height in metres of this object's usable top surface, measured from its renderer "
                     + "bounds at build time. -1 when it has no renderers. A chair's seat and a table's "
                     + "top are the same measurement; what makes one a seat is the category, not the "
                     + "number.")]
            public float surfaceHeight = -1f;

            [Tooltip("Whether this is picked up and taken along, or used where it stands. Authored, "
                     + "because it is neither measurable nor derivable from the category: a bag valve "
                     + "mask and a laptop are both devices about thirty centimetres across, and one is "
                     + "carried while the other is typed on. False by default, so an unannotated "
                     + "object is refused rather than hung off a wrist.")]
            public bool carriable;

            public string Label { get { return string.IsNullOrEmpty(label) ? target.name : label; } }

            /// <summary>Anchors are namespaced apart from props because they collide otherwise: the
            /// animpts hierarchy has a standing spot called "Computer" and the room has a prop called
            /// "Computer", and both were emitting `obj:Computer`. An agent shown two different things
            /// under one id has no way to tell which one it just asked about.</summary>
            public string Id
            {
                get
                {
                    return (category == "anchor" ? "anchor:" : "obj:") + Label.Replace(" ", "");
                }
            }
            public Transform Grab { get { return grabAnchor != null ? grabAnchor : target; } }
            public bool HasSurface { get { return surfaceHeight >= 0f; } }

            /// <summary>Where this effector should go, and the elbow hint that goes with it. Falls back
            /// to the single grab point when the object has no per-hand anchors, which is right for a
            /// bottle and wrong for a keyboard — hence the anchors.</summary>
            public Transform HandAnchor(string effector)
            {
                if (effector == "left_hand" && leftHandAnchor != null) return leftHandAnchor;
                if (effector == "right_hand" && rightHandAnchor != null) return rightHandAnchor;
                return Grab;
            }

            public Transform HintAnchor(string effector)
            {
                return effector == "left_hand" ? leftHintAnchor : rightHintAnchor;
            }

            /// <summary>Whether this object says where BOTH hands go. Two hands may be aimed at it only
            /// when it does; otherwise they land on the same point.</summary>
            public bool HasPerHandAnchors
            {
                get { return leftHandAnchor != null && rightHandAnchor != null; }
            }
        }

        [SerializeField] private List<Entry> entries = new List<Entry>();

        [Tooltip("Bumped by the editor rebuild so the agent can invalidate anything it cached.")]
        [SerializeField] private int version = 1;

        public int Version { get { return version; } }
        public int Count { get { return entries.Count; } }
        public IReadOnlyList<Entry> Entries { get { return entries; } }

        public Entry ById(string id)
        {
            for (int i = 0; i < entries.Count; i++)
            {
                if (entries[i].target != null && entries[i].Id == id) return entries[i];
            }
            return null;
        }

        /// <summary>Resolve a knowledge-base contact name. Accepts both spellings the contract uses:
        /// "object:pills" from a channel and bare "pills" from an ik_goal.</summary>
        public Entry ByAlias(string alias)
        {
            if (string.IsNullOrEmpty(alias)) return null;
            if (alias.StartsWith("object:")) alias = alias.Substring("object:".Length);

            for (int i = 0; i < entries.Count; i++)
            {
                Entry e = entries[i];
                if (e.target == null || e.aliases == null) continue;
                for (int j = 0; j < e.aliases.Length; j++)
                {
                    if (string.Equals(e.aliases[j], alias, StringComparison.OrdinalIgnoreCase)) return e;
                }
            }
            return null;
        }

        public void Replace(List<Entry> rebuilt)
        {
            entries = rebuilt;
            version++;
        }
    }
}
