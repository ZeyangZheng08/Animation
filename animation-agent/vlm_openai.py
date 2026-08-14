"""
vlm_openai.py — minimal stdlib client for the MotionKB PROPOSE step's VLM (ADR 0008).

The propose step needs a vision-language model to look at rendered frames of a clip and PROPOSE the
SEMANTIC fields (action_id + display_name + intent + tags + the per-channel 5-tuple). This
module is the thin transport for that one call: it POSTs to the OpenAI Chat Completions API with the
rendered PNGs as image inputs and returns the parsed JSON proposal. No SDK — stdlib `urllib` only, matching
the rest of `agent/motionkb/`.

The model is `gpt-5.5-2026-04-23` (a reasoning model — give it generous `max_completion_tokens`). The API
key is read from the OPENAI_API_KEY env var, else from `key.env` at the repo root (which is git-ignored —
NEVER commit it, NEVER log the key). The proposal is gated downstream by `validate_semantic_consistency`
(the model proposes; numbers stay MEASURED; a human accepts) — see ADR 0008.
"""
import base64
import json
import os
import urllib.error
import urllib.request

MODEL = "gpt-5.5-2026-04-23"
ENDPOINT = "https://api.openai.com/v1/chat/completions"


def load_api_key(repo_root):
    """OPENAI_API_KEY from the environment, else from the git-ignored key.env at the repo root."""
    k = os.environ.get("OPENAI_API_KEY")
    if k and k.strip():
        return k.strip()
    env_path = os.path.join(repo_root, "key.env")
    if os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("OPENAI_API_KEY not set and no key.env at %s" % repo_root)


def _data_url(png_path):
    with open(png_path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def _extract_json(text):
    """Parse the model's reply as JSON, tolerating ```json fences / surrounding prose."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        a, b = text.find("{"), text.rfind("}")
        if a >= 0 and b > a:
            return json.loads(text[a:b + 1])
        raise


def propose(api_key, prompt, image_paths, model=MODEL, max_tokens=20000, timeout=300):
    """Call the VLM with `prompt` + the PNGs at `image_paths`; return (proposal_dict, usage_dict).

    Raises RuntimeError on a transport/API error. gpt-5.x param quirks (max_tokens vs
    max_completion_tokens, response_format/temperature support) are handled by a small adjust-and-retry
    loop, mirroring the sibling LLMR server's `adjust_unsupported_openai_parameters`.
    """
    content = [{"type": "text", "text": prompt}]
    for p in image_paths:
        content.append({"type": "image_url", "image_url": {"url": _data_url(p)}})
    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_completion_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    last = None
    for _ in range(4):
        data = json.dumps(kwargs).encode("utf-8")
        req = urllib.request.Request(
            ENDPOINT, data=data,
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read().decode("utf-8"))
            msg = resp["choices"][0]["message"]["content"]
            return _extract_json(msg), resp.get("usage", {})
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            last = "OpenAI HTTP %d: %s" % (e.code, body[:400])
            if "Unsupported parameter" in body or "Unsupported value" in body or "unsupported" in body:
                if "max_completion_tokens" in body:
                    kwargs["max_tokens"] = kwargs.pop("max_completion_tokens", max_tokens)
                elif "response_format" in body:
                    kwargs.pop("response_format", None)
                elif "temperature" in body:
                    kwargs.pop("temperature", None)
                else:
                    raise RuntimeError(last)
                continue
            raise RuntimeError(last)
        except urllib.error.URLError as e:
            raise RuntimeError("Cannot reach the OpenAI API (%s). Check the network / OPENAI_API_KEY." % e)
    raise RuntimeError(last or "OpenAI call failed after retries")
