"""
vlm_anthropic.py — minimal stdlib client for the MotionKB PROPOSE step's VLM (ADR 0008), on Claude.

Drop-in sibling of `vlm_openai.py`: same three symbols (`MODEL`, `load_api_key`, `propose`) with the
same signatures, so `propose.py` selects a provider by import rather than by branching. The propose
step needs a vision-language model to look at rendered frames of a clip and PROPOSE the SEMANTIC
fields (action_id + display_name + intent + tags + the per-channel 5-tuple); this module is the thin
transport for that one call.

No SDK — stdlib `urllib` only, matching `vlm_openai.py` and the rest of the offline pipeline, which
`environment.yml` documents as running on a bare python3. (The official `anthropic` SDK is the
better client in general; it is not used here because this WSL interpreter has no pip, so adopting
it would mean a virtualenv plus new entry points for `check_kb.sh` and every script — an environment
migration rather than a provider swap.)

The model is `claude-opus-5`. Thinking is adaptive and on by default on this model, so `thinking` is
sent explicitly only to pin `display` off — the proposal is JSON, and a reasoning summary would just
be text the parser has to step around. `output_config.effort` controls depth.

Server-side refusal fallback is ON: the corpus contains combat, death and zombie motions, and a
policy decline on one frame set would otherwise abort that clip's proposal. With `fallbacks:
"default"` the request is re-served by a fallback model inside the same call, so a refusal costs a
different model rather than a failed clip.

The API key is read from ANTHROPIC_API_KEY, else from a git-ignored `key.env`. NEVER commit it,
NEVER log it. The proposal is gated downstream by `validate_semantic_consistency` (the model
proposes; numbers stay MEASURED; a human may accept) — see ADR 0008.
"""
import base64
import json
import os
import random
import time
import urllib.error
import urllib.request

MODEL = "claude-opus-5"
ENDPOINT = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Where key.env may live. The agent repo root is the documented home, but the file currently sits at
# the Unity project root next to the KB, so both are searched rather than failing on the first miss.
def _key_env_candidates(repo_root):
    yield os.path.join(repo_root, "key.env")
    try:
        import paths
        project_root = os.path.dirname(os.path.dirname(paths.KB_DIR))  # <project>/agent/kb -> <project>
        yield os.path.join(project_root, "key.env")
    except Exception:
        pass


def load_api_key(repo_root):
    """ANTHROPIC_API_KEY from the environment, else from a git-ignored key.env."""
    k = os.environ.get("ANTHROPIC_API_KEY")
    if k and k.strip():
        return k.strip()
    searched = []
    for env_path in _key_env_candidates(repo_root):
        searched.append(env_path)
        if not os.path.exists(env_path):
            continue
        for line in open(env_path, encoding="utf-8-sig"):
            if line.startswith("ANTHROPIC_API_KEY="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    raise RuntimeError(
        "ANTHROPIC_API_KEY not set, and no key.env defining it under:\n  %s"
        % "\n  ".join(searched))


def _image_block(png_path):
    with open(png_path, "rb") as f:
        return {"type": "image",
                "source": {"type": "base64", "media_type": "image/png",
                           "data": base64.b64encode(f.read()).decode()}}


def _extract_json(text):
    """Parse the model's reply as JSON, tolerating ```json fences / surrounding prose.

    Kept identical to the OpenAI client's parser: the proposal's shape is set by the prompt and
    checked downstream by validate_semantic_consistency, and propose.py already has a
    self-correction retry loop around it. Constraining the reply with output_config.format instead
    would be stricter, but it would also change what the retry loop is correcting.
    """
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


def _text_of(payload):
    """The reply text, skipping thinking blocks. content[0] is not reliably the answer."""
    parts = [b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text"]
    return "\n".join(p for p in parts if p)


def propose(api_key, prompt, image_paths, model=MODEL, max_tokens=20000, timeout=600,
            effort="high", max_attempts=5):
    """Call the VLM with `prompt` + the PNGs at `image_paths`; return (proposal_dict, usage_dict).

    Raises RuntimeError on a transport/API error or a refusal that the fallback did not absorb.
    429 / 529 / 5xx are retried with exponential backoff and jitter, honouring `retry-after`.
    """
    content = [{"type": "text", "text": prompt}]
    for p in image_paths:
        content.append(_image_block(p))

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content}],
        # Adaptive thinking is on by default for this model; display stays omitted so the reply is
        # the JSON proposal and nothing else.
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
        "fallbacks": "default",
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": API_VERSION,
        "anthropic-beta": FALLBACK_BETA,
        "content-type": "application/json",
    }

    last = None
    for attempt in range(max_attempts):
        req = urllib.request.Request(
            ENDPOINT, data=json.dumps(body).encode("utf-8"), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            last = "Anthropic HTTP %d: %s" % (e.code, text[:400])
            retryable = e.code in (408, 409, 429, 529) or e.code >= 500
            if not retryable or attempt == max_attempts - 1:
                raise RuntimeError(last)
            wait = None
            try:
                wait = float(e.headers.get("retry-after"))
            except (TypeError, ValueError):
                pass
            time.sleep(wait if wait else min(60.0, 2.0 ** attempt) + random.uniform(0, 1))
            continue
        except urllib.error.URLError as e:
            raise RuntimeError(
                "Cannot reach the Anthropic API (%s). Check the network / ANTHROPIC_API_KEY." % e)

        stop = payload.get("stop_reason")
        if stop == "refusal":
            details = payload.get("stop_details") or {}
            raise RuntimeError(
                "the model declined this clip's frames (category %r): %s"
                % (details.get("category"), details.get("explanation")))
        text = _text_of(payload)
        if not text.strip():
            last = "empty reply (stop_reason=%r)" % stop
            if attempt == max_attempts - 1:
                raise RuntimeError(last)
            continue
        usage = payload.get("usage", {})
        usage["stop_reason"] = stop
        usage["model"] = payload.get("model", model)
        return _extract_json(text), usage

    raise RuntimeError(last or "Anthropic call failed after retries")
