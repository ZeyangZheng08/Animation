using System;
using System.Collections.Generic;
using UnityEngine;

namespace AgentRuntime
{
    /// <summary>
    /// action_id to AnimationClip, resolved at edit time and serialized.
    ///
    /// The plan carries a clip guid and file id because the knowledge base records them, but a guid is
    /// an EDITOR concept — AssetDatabase does not exist in a player build. So the mapping is baked here
    /// by an editor tool, and at runtime this is a plain lookup. Without that, the executor would be
    /// editor-only, which would quietly undo the point of it being a pre-compiled component.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class ClipLibrary : MonoBehaviour
    {
        [Serializable]
        public sealed class Item
        {
            public string actionId;
            public string clipName;
            public AnimationClip clip;
        }

        [SerializeField] private List<Item> items = new List<Item>();

        public int Count { get { return items.Count; } }

        public AnimationClip Resolve(string actionId, string clipName)
        {
            for (int i = 0; i < items.Count; i++)
            {
                if (items[i].actionId == actionId) return items[i].clip;
            }
            if (!string.IsNullOrEmpty(clipName))
            {
                for (int i = 0; i < items.Count; i++)
                {
                    if (items[i].clipName == clipName) return items[i].clip;
                }
            }
            return null;
        }

        public void Replace(List<Item> rebuilt)
        {
            items = rebuilt;
        }
    }
}
