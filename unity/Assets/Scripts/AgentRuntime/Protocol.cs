using System;
using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AgentRuntime
{
    /// <summary>
    /// The engine half of the typed message contract. The authority is
    /// <c>agent/protocol.py</c> in the animation-agent repository; change it there first, then mirror
    /// here. A version mismatch is fatal on both sides rather than best-effort, because a service that
    /// silently half-speaks an old protocol fails later as a wrong pose in the scene, not as an error.
    ///
    /// What crosses this channel: typed messages, never code. The Unity MCP bridge already exists for
    /// shipping generated C#, and it is offline-only — it builds the knowledge base. This channel is the
    /// runtime one and this side of it is pre-compiled. Merging the two would reintroduce compiling C#
    /// during a request, which the architecture forbids and which a player build cannot do at all.
    ///
    /// Unity never issues requests. It sends a hello, responses, and unsolicited events. That keeps this
    /// side a pure reactor with no pending-request table to reconcile after a domain reload — and it
    /// still holds in v3, where the instruction that starts a turn goes out as an event rather than as
    /// a request, so nothing here has to wait on a correlated answer.
    /// </summary>
    public static class Protocol
    {
        // v2 added the time axis: motion.assemble takes a `steps` array, each step being exactly the
        // shape v1 sent at the top level plus when it starts and how it fades in. A single-step sequence
        // IS v1's behaviour, so the executor keeps one code path.
        //
        // v3 puts the text input in the running scene. agent.instruct goes out from here; agent.status
        // and agent.reply come back, and they are the first messages this side receives that are not
        // requests. Dispatch classifies on the presence of `id`, the same rule the agent already uses.
        //
        // v4 puts a check in front of execution. motion.assemble takes a third mode, `validate`, which
        // plays the whole plan on a hidden duplicate of the character at fixed timestep and answers
        // with the same geometric metrics the runtime gate reports -- before anything a viewer can see
        // has changed. motion.locomote takes `preview`, which answers where a walk WOULD end without
        // taking a step, so the motion that follows a walk is judged at the place it will happen.
        //
        // THE BUMP IS NOT OPTIONAL AND THAT IS THE POINT. An executor from before this does not know
        // the word `validate`; Apply treated anything that was not "commit" as a dry run, so it would
        // answer "resolved, touched nothing" -- which reads exactly like a pass. A plan would then be
        // committed on the strength of a check that never ran. A fatal version mismatch turns that into
        // an error on the first message instead.
        // v5 ADDS `apply_root_motion` ON A LAYER, and the bump is not optional for the same reason.
        // A retrieved posture transition is a clip that TRAVELS -- a sit-down steps 0.45 m backwards
        // into the chair -- and the composer has always discarded root motion, which is right for a
        // walk cycle played under a NavMeshAgent and wrong for this. So the layer says which it is.
        // An executor from before v5 does not know the field, drops it, discards the root motion, and
        // plays the sit-down on the spot: the feet slide, the hips finish where they started, and the
        // plan reports success about a character who never reached the seat.
        public const int Version = 5;

        // engine -> agent, unsolicited
        public const string EngineHello = "engine.hello";
        public const string MotionStatus = "motion.status";
        public const string GateReport = "gate.report";
        public const string AgentInstruct = "agent.instruct";

        // agent -> engine, unsolicited
        public const string AgentStatus = "agent.status";
        public const string AgentReply = "agent.reply";

        // agent -> engine, request/response
        public const string SceneFind = "scene.find";
        public const string SceneDescribe = "scene.describe";
        public const string SceneAnchors = "scene.anchors";
        public const string ScenePosition = "scene.position";
        public const string MotionAssemble = "motion.assemble";
        public const string MotionLocomote = "motion.locomote";
        public const string GateRun = "gate.run";

        // There is deliberately no separate "play one clip" message. A full match is an assembly with a
        // single layer and no overlays, so it takes the same path through the executor as a composed
        // motion. Two code paths would let the common case and the interesting case drift apart, and the
        // interesting one is the one that gets less testing.

        public static class Err
        {
            public const string BadRequest = "bad_request";
            public const string UnknownType = "unknown_type";
            public const string NotFound = "not_found";
            public const string NotReady = "not_ready";
            public const string ExecFailed = "exec_failed";
            public const string Internal = "internal";
        }

        public static string Ok(string id, object data)
        {
            return JsonConvert.SerializeObject(new Dictionary<string, object>
            {
                { "v", Version }, { "id", id }, { "ok", true }, { "data", data ?? new object() }
            });
        }

        public static string Error(string id, string code, string message)
        {
            return JsonConvert.SerializeObject(new Dictionary<string, object>
            {
                { "v", Version }, { "id", id }, { "ok", false },
                { "err", new Dictionary<string, object> { { "code", code }, { "msg", message } } }
            });
        }

        public static string Event(string type, object data)
        {
            return JsonConvert.SerializeObject(new Dictionary<string, object>
            {
                { "v", Version }, { "type", type }, { "data", data ?? new object() }
            });
        }

        /// <summary>Parse anything the agent sends. Throws on malformed input — see the class remarks
        /// on why a version mismatch is not tolerated.
        ///
        /// A request carries an <c>id</c> to correlate its answer; an event never does. That single
        /// rule is the whole classifier, and it is the same one <c>protocol.classify()</c> uses on the
        /// other side.</summary>
        public static Inbound Parse(string json)
        {
            JObject root = JObject.Parse(json);
            int version = root.Value<int?>("v") ?? -1;
            if (version != Version)
            {
                throw new ProtocolException(string.Format(
                    "protocol version {0}, expected {1} — the agent service and this executor are out " +
                    "of step; rebuild one of them from the other's protocol definition",
                    version, Version));
            }

            string type = root.Value<string>("type");
            if (string.IsNullOrEmpty(type)) throw new ProtocolException("message without a type");
            string id = root.Value<string>("id");

            if (string.IsNullOrEmpty(id))
            {
                return new Inbound
                {
                    Type = type,
                    Data = root["data"] as JObject ?? new JObject()
                };
            }

            return new Inbound
            {
                Type = type,
                Request = new Request
                {
                    Id = id,
                    Type = type,
                    Params = root["params"] as JObject ?? new JObject()
                }
            };
        }
    }

    /// <summary>One decoded message from the agent: a request to answer, or an event to react to.</summary>
    public sealed class Inbound
    {
        public string Type;
        public Request Request;     // null for an event
        public JObject Data;        // null for a request

        public bool IsRequest { get { return Request != null; } }
    }

    public sealed class Request
    {
        public string Id;
        public string Type;
        public JObject Params;

        public string Str(string key, string fallback = null)
        {
            JToken t = Params[key];
            return t == null || t.Type == JTokenType.Null ? fallback : t.Value<string>();
        }

        public int Int(string key, int fallback)
        {
            JToken t = Params[key];
            return t == null || t.Type == JTokenType.Null ? fallback : t.Value<int>();
        }

        public bool Bool(string key, bool fallback)
        {
            JToken t = Params[key];
            return t == null || t.Type == JTokenType.Null ? fallback : t.Value<bool>();
        }

        public float Float(string key, float fallback)
        {
            JToken t = Params[key];
            return t == null || t.Type == JTokenType.Null ? fallback : t.Value<float>();
        }

        public JObject Obj(string key)
        {
            return Params[key] as JObject;
        }

        public JArray Arr(string key)
        {
            return Params[key] as JArray;
        }
    }

    public sealed class ProtocolException : Exception
    {
        public ProtocolException(string message) : base(message) { }
    }
}
