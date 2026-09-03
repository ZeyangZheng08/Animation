using System.Collections.Generic;
using UnityEngine;

namespace AgentRuntime
{
    /// <summary>
    /// Answers typed predicates over the registry, and — since a generated sit has to land on a real
    /// seat — measures where things are.
    ///
    /// FIND AND DESCRIBE STAY SYMBOLIC. Identity, category, coarse relations, reachability as a yes or
    /// no. The radius vocabulary is an enum for the same reason: "within arm's reach" is a judgement a
    /// model can make, "within 0.75 m" is one it cannot, and the number belongs on this side where it is
    /// measured off the actual skeleton.
    ///
    /// POSITION IS A SEPARATE, EXPLICIT ASK. Earlier this component returned no coordinates at all, on
    /// the argument that precise pose should never enter the model's context. That was too strong: an
    /// agent deciding whether to walk before it sits needs to know the chair is across the room, and
    /// inferring it from "near: MonitorStation" is guesswork dressed as architecture. So Position()
    /// exists and is opt-in — a caller that does not ask still gets no numbers.
    ///
    /// The line that did NOT move: the model reads coordinates, it never writes motion numerics. IK
    /// targets are still symbolic bindings resolved here, plan_motion's leaves are still strings and
    /// booleans, and the schedule is still computed agent-side from measured data. Reading where a chair
    /// is does not let a model invent a hip trajectory.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class SceneQueryService : MonoBehaviour
    {
        [SerializeField] private SceneRegistry registry;
        [SerializeField] private AgentCharacter[] characters;

        [Header("Radius vocabulary (metres)")]
        [Tooltip("These stay on this side of the wire. The model chooses a word, not a number.")]
        [SerializeField] private float armsReach = 0.75f;
        [SerializeField] private float sameStation = 2.5f;

        [Tooltip("How far off-centre `view:left` and `view:right` land, and how far `view:ahead` and "
                 + "`view:behind` move along the line of sight. Same rule as the radii above: the "
                 + "instruction says a word and the metres stay on this side.")]
        [SerializeField] private float viewSpread = 2.0f;

        public int EntryCount { get { return registry == null ? 0 : registry.Count; } }
        public SceneRegistry Registry { get { return registry; } }

        // ---- queries ---------------------------------------------------------------------------

        public object Find(Request request)
        {
            Require();
            string category = request.Str("category");
            string nameContains = request.Str("name_contains");
            string alias = request.Str("alias");
            string heldBy = request.Str("held_by");
            int limit = request.Int("limit", 10);

            // A BLANK CLAUSE MEANS "I DID NOT ASK FOR THIS", NOT "APPLY IT WITH A DEFAULT". Models fill
            // in every field a schema offers, so `near: {object_id: ""}` and `reachable_by:
            // {character: ""}` arrive on searches that never meant to constrain anything.
            //
            // This is the whole reason the chair could not be found, and it is worth stating exactly.
            // FindCharacter("") resolves to the driven character, which is the right default when a
            // TOOL needs an actor. As a FILTER it turned an unconstrained search into "within arm's
            // reach right now" -- and the chair is across the room. Every query the model made came
            // back empty, including the relaxation fallback, which re-ran with the same reach filter.
            // It concluded the room had no chair and gave up, having asked ten times.
            Newtonsoft.Json.Linq.JObject near = request.Obj("near");
            string nearId = near == null ? null : near.Value<string>("object_id");
            SceneRegistry.Entry nearOf = string.IsNullOrWhiteSpace(nearId) ? null : registry.ById(nearId);
            float nearRadius = nearOf == null ? 0f : RadiusOf(near.Value<string>("radius"));

            Newtonsoft.Json.Linq.JObject reachable = request.Obj("reachable_by");
            string reachId = reachable == null ? null : reachable.Value<string>("character");
            AgentCharacter reachChar = string.IsNullOrWhiteSpace(reachId) ? null : FindCharacter(reachId);
            string reachEffector = reachable == null ? "either" : reachable.Value<string>("effector");

            List<object> found = Filter(category, nameContains, alias, heldBy, nearOf, nearRadius,
                                        reachChar, reachEffector, limit);

            // THE CHARACTER WE DRIVE HAS TO BE FINDABLE. It lives on the AgentLink, not in the object
            // registry, so a search for category "character" returned the patient and nothing else --
            // and every tool that acts needs a character id as its first argument. Measured: an agent
            // called scene_find six times, concluded "the scene exposes no nurse character", and gave
            // up on a task it had already worked out how to do. The id was only ever discoverable by
            // guessing wrong and reading the error.
            found.InsertRange(0, Drivable(category, nameContains, alias, limit - found.Count));

            // A GUESSED CATEGORY MUST NOT KILL A GOOD NAME. The filters are an AND, and `category` is
            // the one a caller has to invent. Measured: an agent asked for category "furniture" with
            // the name "stool" five times running; the Chair is category "seating", so every one came
            // back empty and it concluded the room had no seat. Dropping the category and saying so
            // turns a wrong guess into a recoverable one, and the caller still learns it was wrong.
            string relaxed = null;
            if (found.Count == 0 && !string.IsNullOrEmpty(category) &&
                (!string.IsNullOrEmpty(nameContains) || !string.IsNullOrEmpty(alias)))
            {
                List<object> without = Filter(null, nameContains, alias, heldBy, nearOf, nearRadius,
                                              reachChar, reachEffector, limit);
                if (without.Count > 0)
                {
                    found = without;
                    relaxed = "nothing with that name is category '" + category + "', so the category "
                            + "filter was dropped. Categories in this scene: "
                            + string.Join(", ", Categories());
                }
            }

            // FALLBACK TO THE WHOLE SCENE, only when the caller asked by NAME ALONE and the curated
            // list had nothing. The registry is 30 hand-reviewed objects out of 600, so a name outside
            // it used to come back as a flat "nothing matches", which reads as "the scene has no chair"
            // when it means "nobody annotated the chair".
            //
            // NOT when a category was given. Measured: an agent asked for category "furniture" plus the
            // name "stool", which excluded the Chair (category "seating"), fell through here, and got
            // back an FBX asset called one-step-medical-examination-stool that it then tried to walk to.
            // A structured filter that matches nothing is an answer; only a bare name is a lookup.
            bool fromScene = false;
            if (found.Count == 0 && !string.IsNullOrEmpty(nameContains) &&
                string.IsNullOrEmpty(category) && string.IsNullOrEmpty(alias) &&
                nearOf == null && reachChar == null)
            {
                fromScene = true;
                Transform[] all2 = Object.FindObjectsByType<Transform>(
                    FindObjectsInactive.Include, FindObjectsSortMode.None);
                for (int i = 0; i < all2.Length && found.Count < limit; i++)
                {
                    Transform t = all2[i];
                    if (t.name.IndexOf(nameContains, System.StringComparison.OrdinalIgnoreCase) < 0) continue;
                    if (registry.ById("obj:" + t.name.Replace(" ", "")) != null) continue;
                    found.Add(new Dictionary<string, object>
                    {
                        { "id", "scene:" + t.name.Replace(" ", "") },
                        { "name", t.name },
                        { "category", "unannotated" },
                        { "source", "scene" }
                    });
                }
            }

            Dictionary<string, object> reply = new Dictionary<string, object>
            {
                { "objects", found }, { "registry_version", registry.Version }
            };
            if (relaxed != null)
            {
                reply["note"] = relaxed;
            }
            else if (fromScene)
            {
                reply["note"] = "nothing in the annotated registry matched, so these came from a raw "
                              + "scene name search; they have no contact aliases and no stand anchor.";
            }
            else if (found.Count == 0)
            {
                // Say which categories exist AND list the room. A filter value the caller invented is
                // the most likely reason for an empty result, and "no matches" alone gives no way to
                // find that out -- it reads as "the room has no chair" when it means "not with those
                // filters". The annotated registry is a couple of dozen entries, so the honest answer
                // to a failed search is simply to show what is there.
                reply["note"] = "no match with those filters. Categories in this scene: "
                              + string.Join(", ", Categories())
                              + ". `inventory` below is everything the registry holds -- search it "
                              + "yourself rather than guessing another filter.";
                reply["inventory"] = Inventory();
            }
            return reply;
        }

        /// <summary>Every annotated object, as id + label + category. Small on purpose: this is what a
        /// failed search hands back so absence can be read off a list instead of inferred from a miss.
        /// Anchors are excluded — scene_anchors already answers that, and they are most of the count.</summary>
        private List<object> Inventory()
        {
            List<object> all = new List<object>();
            foreach (SceneRegistry.Entry e in registry.Entries)
            {
                if (e.target == null || e.category == "anchor") continue;
                all.Add(new Dictionary<string, object>
                {
                    { "id", e.Id }, { "name", e.Label }, { "category", e.category }
                });
            }
            return all;
        }

        /// <summary>Where things are. The one query that returns numbers, and only when asked.</summary>
        public object Position(Request request)
        {
            Require();
            Newtonsoft.Json.Linq.JArray ids = request.Arr("object_ids");
            if (ids == null || ids.Count == 0)
            {
                throw new AgentRequestException(Protocol.Err.BadRequest, "no object_ids given");
            }
            AgentCharacter relative = FindCharacter(request.Str("relative_to"));

            List<object> items = new List<object>();
            for (int i = 0; i < ids.Count; i++)
            {
                string id = ids[i].ToObject<string>();
                Transform t = Resolve(id);
                if (t == null)
                {
                    items.Add(new Dictionary<string, object>
                    {
                        { "object_id", id }, { "found", false }
                    });
                    continue;
                }

                Vector3 p = t.position;
                Dictionary<string, object> item = new Dictionary<string, object>
                {
                    { "object_id", id },
                    { "found", true },
                    { "position", new float[] { p.x, p.y, p.z } }
                };

                SceneRegistry.Entry entry = registry.ById(id);
                if (entry != null && entry.HasSurface)
                {
                    item["surface_height_m"] = entry.surfaceHeight;
                }
                if (relative != null)
                {
                    Vector3 origin = relative.transform.position;
                    Vector3 delta = p - origin;
                    float ground = new Vector2(delta.x, delta.z).magnitude;
                    item["from_character"] = new Dictionary<string, object>
                    {
                        { "character", relative.Id },
                        { "distance_m", ground },
                        { "height_above_floor_m", p.y },
                        { "bearing", Bearing(relative.transform, delta) },
                        { "within_arms_reach", ground <= armsReach },
                        { "needs_walking", ground > sameStation }
                    };
                }
                items.Add(item);
            }
            return new Dictionary<string, object> { { "objects", items } };
        }

        private List<object> Filter(string category, string nameContains, string alias, string heldBy,
                                    SceneRegistry.Entry nearOf, float nearRadius,
                                    AgentCharacter reachChar, string reachEffector, int limit)
        {
            List<object> found = new List<object>();
            IReadOnlyList<SceneRegistry.Entry> all = registry.Entries;
            for (int i = 0; i < all.Count && found.Count < limit; i++)
            {
                SceneRegistry.Entry e = all[i];
                if (e.target == null) continue;
                if (!string.IsNullOrEmpty(category) && e.category != category) continue;
                // Name search checks the aliases too. Without it, asking for a "stool" missed the Chair
                // whose alias list literally contains "stool", fell through to the raw scene search,
                // and came back with an FBX asset name nothing else could resolve.
                if (!string.IsNullOrEmpty(nameContains) && !MatchesName(e, nameContains)) continue;
                if (!string.IsNullOrEmpty(alias) && registry.ByAlias(alias) != e) continue;
                if (!string.IsNullOrEmpty(heldBy) && HolderOf(e) != heldBy) continue;
                if (nearOf != null && nearOf.target != null &&
                    Vector3.Distance(nearOf.target.position, e.target.position) > nearRadius) continue;
                if (reachChar != null && !reachChar.CanReach(e.Grab.position, reachEffector)) continue;

                found.Add(Summarise(e));
            }
            return found;
        }

        /// <summary>The category vocabulary this scene actually uses, so an invented filter value can
        /// be corrected rather than read as absence.</summary>
        public List<string> Categories()
        {
            List<string> names = new List<string>();
            IReadOnlyList<SceneRegistry.Entry> all = registry.Entries;
            for (int i = 0; i < all.Count; i++)
            {
                if (all[i].target == null || string.IsNullOrEmpty(all[i].category)) continue;
                if (!names.Contains(all[i].category)) names.Add(all[i].category);
            }
            names.Sort();
            return names;
        }

        private static bool MatchesName(SceneRegistry.Entry e, string needle)
        {
            if (e.Label.IndexOf(needle, System.StringComparison.OrdinalIgnoreCase) >= 0) return true;
            if (e.target != null &&
                e.target.name.IndexOf(needle, System.StringComparison.OrdinalIgnoreCase) >= 0) return true;
            if (e.aliases == null) return false;
            for (int i = 0; i < e.aliases.Length; i++)
            {
                if (e.aliases[i] != null &&
                    e.aliases[i].IndexOf(needle, System.StringComparison.OrdinalIgnoreCase) >= 0) return true;
            }
            return false;
        }

        /// <summary>Somewhere to walk, including the places that are not objects.
        ///
        /// "Walk to the patient" names a thing. "Walk to the right of my view" names a place, relative
        /// to whoever is watching, and there is no transform in the scene for it — so a destination
        /// vocabulary that only resolves objects cannot express half of what someone would say. This
        /// takes the other half:
        ///
        ///     view:left / view:right / view:ahead / view:behind    relative to the camera
        ///     near:&lt;object_id&gt;                                     beside a thing rather than at it
        ///
        /// NO METRES CROSS THE WIRE, in either direction. The instruction picks a word; how far
        /// `viewSpread` is lives here beside the radius vocabulary, for the reason stated there — "to
        /// the right" is a judgement a model can make and "2.0 m along the camera's right axis" is one
        /// it cannot. And the answer is a point on the navigation mesh, not a raw offset, so a
        /// direction with a wall in it fails here rather than as a walk that goes nowhere.
        ///
        /// THE DEPTH IS HERS, NOT A GUESS. `view:right` keeps her the same distance into the room and
        /// moves her across it, because "to the right of my view" is about the direction from the
        /// viewer, not about walking toward or away from them. Only `ahead` and `behind` change depth,
        /// which is what those two words are for.
        ///
        /// Returns null with `why` filled in. A destination that cannot be resolved has to say which
        /// part failed — an unknown object and a direction with no floor in it are different problems.
        /// </summary>
        public Vector3? ResolvePoint(string id, Transform mover, out string why)
        {
            why = null;
            if (string.IsNullOrEmpty(id)) { why = "no destination was given"; return null; }

            // A POINT THE PLANNER COMPUTED, in world XZ. Every other form here names something in
            // the room, and that is the right shape for a destination a MODEL chose. This one is not
            // a model's: it is where the agent's compiler worked out she has to stand for a retrieved
            // sit-down clip to finish on the seat, and that is arithmetic over the clip's own root
            // travel and the seat's position. There is no object at the answer, so there is nothing
            // to name it by.
            //
            // Snapped to the navigation mesh like every other destination, because a point that is
            // not walkable is a walk that never arrives.
            if (id.StartsWith("point:"))
            {
                string[] parts = id.Substring("point:".Length).Split(',');
                float px, pz;
                if (parts.Length != 2
                    || !float.TryParse(parts[0], System.Globalization.NumberStyles.Float,
                                       System.Globalization.CultureInfo.InvariantCulture, out px)
                    || !float.TryParse(parts[1], System.Globalization.NumberStyles.Float,
                                       System.Globalization.CultureInfo.InvariantCulture, out pz))
                {
                    why = "a point destination is point:<x>,<z> in world metres, not " + id;
                    return null;
                }
                float py = mover != null ? mover.position.y : 0f;
                return OnMesh(new Vector3(px, py, pz), out why);
            }

            if (id.StartsWith("near:"))
            {
                string inner = id.Substring("near:".Length);
                SceneRegistry.Entry entry = registry == null ? null : registry.ById(inner);
                if (entry != null && entry.standAnchor != null) return entry.standAnchor.position;
                Transform thing = Resolve(inner);
                if (thing == null) { why = "there is nothing called " + inner; return null; }
                // No anchor, so beside it means the walkable ground closest to it. Sampling from the
                // object itself rather than from an offset in some direction: which side is free is a
                // question about the room, and the navigation mesh already knows the answer.
                return OnMesh(thing.position, out why);
            }

            if (id.StartsWith("view:"))
            {
                Camera eye = Camera.main;
                if (eye == null) { why = "there is no camera in this scene to be a viewpoint"; return null; }
                if (mover == null) { why = "no character to move"; return null; }

                Vector3 forward = eye.transform.forward;
                forward.y = 0f;
                if (forward.sqrMagnitude < 1e-6f) forward = Vector3.forward;
                forward.Normalize();
                Vector3 right = new Vector3(forward.z, 0f, -forward.x);

                Vector3 eyeFlat = new Vector3(eye.transform.position.x, mover.position.y,
                                              eye.transform.position.z);
                // How far into the room she already is, along the line of sight. Keeping it is what
                // makes "to the right" a sideways move rather than a walk toward the camera.
                float depth = Vector3.Dot(mover.position - eyeFlat, forward);

                string side = id.Substring("view:".Length);
                Vector3 wanted;
                if (side == "right") wanted = eyeFlat + forward * depth + right * viewSpread;
                else if (side == "left") wanted = eyeFlat + forward * depth - right * viewSpread;
                else if (side == "ahead") wanted = eyeFlat + forward * (depth + viewSpread);
                else if (side == "behind") wanted = eyeFlat + forward * (depth - viewSpread);
                else
                {
                    why = "'" + side + "' is not a direction; say left, right, ahead or behind";
                    return null;
                }
                Vector3? landed = OnMesh(wanted, out why);
                if (landed == null)
                {
                    why = "there is no walkable floor " + side + " of the view from here";
                }
                return landed;
            }

            Transform target = Resolve(id);
            if (target == null) { why = "there is nothing called " + id; return null; }
            return target.position;
        }

        /// <summary>The nearest walkable point, or null. The search radius is generous on purpose: this
        /// is answering "roughly there" and the caller's own pathfinding refuses what it cannot reach.
        /// </summary>
        private static Vector3? OnMesh(Vector3 wanted, out string why)
        {
            why = null;
            UnityEngine.AI.NavMeshHit hit;
            if (UnityEngine.AI.NavMesh.SamplePosition(wanted, out hit, 3f,
                                                      UnityEngine.AI.NavMesh.AllAreas))
            {
                return hit.position;
            }
            why = "there is no walkable floor near there";
            return null;
        }

        /// <summary>Accepts a registry id, a raw scene id from the fallback search, or an anchor.</summary>
        public Transform Resolve(string id)
        {
            SceneRegistry.Entry e = registry.ById(id);
            if (e != null && e.target != null) return e.target;
            if (id != null && id.StartsWith("scene:"))
            {
                string wanted = id.Substring("scene:".Length);
                Transform[] all = Object.FindObjectsByType<Transform>(
                    FindObjectsInactive.Include, FindObjectsSortMode.None);
                for (int i = 0; i < all.Length; i++)
                {
                    if (all[i].name.Replace(" ", "") == wanted) return all[i];
                }
            }
            return null;
        }

        private static string Bearing(Transform from, Vector3 delta)
        {
            Vector3 flat = new Vector3(delta.x, 0f, delta.z);
            if (flat.sqrMagnitude < 1e-6f) return "underfoot";
            float angle = Vector3.SignedAngle(from.forward, flat.normalized, Vector3.up);
            if (angle > -45f && angle <= 45f) return "ahead";
            if (angle > 45f && angle <= 135f) return "to the right";
            if (angle <= -45f && angle > -135f) return "to the left";
            return "behind";
        }

        public object Describe(string objectId)
        {
            Require();
            SceneRegistry.Entry e = registry.ById(objectId);
            if (e == null || e.target == null)
            {
                // An id the raw scene search handed out has to work in every tool that takes an id.
                // Returning them from find and then refusing them here is a dead end of our making.
                Transform raw = Resolve(objectId);
                if (raw != null)
                {
                    return new Dictionary<string, object>
                    {
                        { "id", objectId }, { "name", raw.name },
                        { "category", "unannotated" }, { "source", "scene" },
                        { "note", "found by raw name search, so nothing is known about it beyond where "
                                  + "it is: no contact aliases, no stand anchor, no surface height." }
                    };
                }
                throw new AgentRequestException(Protocol.Err.NotFound, "no object " + objectId);
            }

            Dictionary<string, object> data = Summarise(e);
            data["held_by"] = HolderOf(e);
            data["stand_at"] = e.standAnchor == null ? null : "anchor:" + e.standAnchor.name;
            List<object> reach = new List<object>();
            if (characters != null)
            {
                for (int i = 0; i < characters.Length; i++)
                {
                    AgentCharacter c = characters[i];
                    if (c == null) continue;
                    reach.Add(new Dictionary<string, object>
                    {
                        { "character", c.Id },
                        { "left_hand", c.CanReach(e.Grab.position, "left_hand") },
                        { "right_hand", c.CanReach(e.Grab.position, "right_hand") }
                    });
                }
            }
            data["reachable"] = reach;
            return data;
        }

        /// <summary>The characters this executor can actually drive, shaped like registry objects so a
        /// caller does not have to know they come from somewhere else. Only for a query that could have
        /// meant a character: a bare search for "chair" should not be answered with a nurse.</summary>
        private List<object> Drivable(string category, string nameContains, string alias, int room)
        {
            List<object> found = new List<object>();
            if (characters == null || room <= 0) return found;
            if (!string.IsNullOrEmpty(alias)) return found;          // aliases are contact vocabulary
            if (!string.IsNullOrEmpty(category) && category != "character") return found;

            for (int i = 0; i < characters.Length && found.Count < room; i++)
            {
                AgentCharacter c = characters[i];
                if (c == null) continue;
                string label = c.gameObject.name;
                if (!string.IsNullOrEmpty(nameContains)
                    && label.IndexOf(nameContains, System.StringComparison.OrdinalIgnoreCase) < 0
                    && c.Id.IndexOf(nameContains, System.StringComparison.OrdinalIgnoreCase) < 0)
                {
                    continue;
                }
                found.Add(new Dictionary<string, object>
                {
                    { "id", c.Id },
                    { "name", label },
                    { "category", "character" },
                    { "drivable", true },
                    { "note", "pass this id as `character` to plan_motion, move_to and check_motion" }
                });
            }
            return found;
        }

        public object Anchors()
        {
            Require();
            List<object> anchors = new List<object>();
            IReadOnlyList<SceneRegistry.Entry> all = registry.Entries;
            for (int i = 0; i < all.Count; i++)
            {
                if (all[i].category == "anchor" && all[i].target != null)
                {
                    anchors.Add(new Dictionary<string, object>
                    {
                        { "id", all[i].Id },
                        { "name", all[i].Label },
                        { "faces", all[i].faceAnchor == null ? null : all[i].faceAnchor.name }
                    });
                }
            }
            return new Dictionary<string, object> { { "anchors", anchors } };
        }

        // ---- helpers ---------------------------------------------------------------------------

        private void Require()
        {
            if (registry == null)
            {
                throw new AgentRequestException(Protocol.Err.NotReady,
                    "no SceneRegistry is wired; run Tools > Animation Agent > Rebuild Scene Registry");
            }
        }

        private Dictionary<string, object> Summarise(SceneRegistry.Entry e)
        {
            // Still no coordinates here. Find is a "what is there" question; scene.position is the
            // "where exactly" one, and keeping them apart means a broad search stays cheap.
            Dictionary<string, object> data = new Dictionary<string, object>
            {
                { "id", e.Id },
                { "name", e.Label },
                { "category", e.category },
                { "aliases", e.aliases ?? new string[0] },
                // Whether it goes with her or stays put. Reported on every object rather than only on
                // refusal, so the decision can be made while planning instead of discovered by having
                // a plan rejected -- and so the fact is visible rather than guessed from the category,
                // which is what got a laptop attached to a wrist.
                { "carriable", e.carriable },
                // Whether this object says where BOTH hands go. Two hands may be aimed at it only when
                // it does; otherwise a binding collapses them onto one point.
                { "two_handed_anchors", e.HasPerHandAnchors },
                { "near", NearestAnchorName(e) }
            };
            if (e.HasSurface) data["has_usable_surface"] = true;
            return data;
        }

        private string NearestAnchorName(SceneRegistry.Entry e)
        {
            if (e.standAnchor != null) return e.standAnchor.name;
            float best = float.MaxValue;
            string name = null;
            IReadOnlyList<SceneRegistry.Entry> all = registry.Entries;
            for (int i = 0; i < all.Count; i++)
            {
                if (all[i].category != "anchor" || all[i].target == null) continue;
                float d = Vector3.Distance(all[i].target.position, e.target.position);
                if (d < best) { best = d; name = all[i].Label; }
            }
            return best <= sameStation ? name : null;
        }

        private string HolderOf(SceneRegistry.Entry e)
        {
            if (characters == null) return null;
            for (Transform t = e.target.parent; t != null; t = t.parent)
            {
                for (int i = 0; i < characters.Length; i++)
                {
                    if (characters[i] != null && characters[i].transform == t) return characters[i].Id;
                }
            }
            return null;
        }

        private float RadiusOf(string word)
        {
            if (word == "arms_reach") return armsReach;
            if (word == "same_station") return sameStation;
            return float.MaxValue;
        }

        private AgentCharacter FindCharacter(string id)
        {
            if (characters == null) return null;
            if (string.IsNullOrEmpty(id)) return characters.Length == 1 ? characters[0] : null;
            for (int i = 0; i < characters.Length; i++)
            {
                if (characters[i] != null && characters[i].Id == id) return characters[i];
            }
            return null;
        }
    }
}
