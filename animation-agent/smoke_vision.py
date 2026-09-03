#!/usr/bin/env python3
"""smoke_vision.py -- can the chosen models actually SEE a rendered frame?

`read` on a rendered frame hands the model an image and expects it to read a pose off it. That is an
assumption about the model, not about our code, so it gets tested against the live API before anything
is built on it. Two things are being checked, and they are different:

  1. does the endpoint ACCEPT image input at all (a 400 here kills the tool), and
  2. having accepted it, does the answer track the image (a model that always says "sitting" is useless).

So each model is asked the same question about two frames with known, opposite answers: Typing frame 0
(seated) and Idle (standing). Agreeing with both is the only pass.

Realtime and Chat are asked over their own transports, because the Realtime endpoint rejects a chat
model outright and image content is shaped differently on each.
"""
import argparse
import asyncio
import base64
import json
import os
import sys
import urllib.error
import urllib.request

from agent import keys

CHAT_URL = "https://api.openai.com/v1/chat/completions"
REALTIME_URL = "wss://api.openai.com/v1/realtime"
QUESTION = ("Look at this rendered character. Answer with exactly one word: "
            "SITTING or STANDING. No other text.")


def data_uri(path):
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    with open(path, "rb") as fh:
        return "data:%s;base64,%s" % (mime, base64.b64encode(fh.read()).decode("ascii"))


def ask_chat(model, api_key, image_path):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": QUESTION},
            {"type": "image_url", "image_url": {"url": data_uri(image_path)}},
        ]}],
    }
    req = urllib.request.Request(
        CHAT_URL, data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            doc = json.load(resp)
    except urllib.error.HTTPError as e:
        return None, "HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:300])
    text = (doc["choices"][0]["message"]["content"] or "").strip()
    usage = doc.get("usage", {})
    return text, "prompt=%s completion=%s" % (usage.get("prompt_tokens"), usage.get("completion_tokens"))


async def ask_realtime(model, api_key, image_path):
    import websockets
    url = "%s?model=%s" % (REALTIME_URL, model)
    headers = {"Authorization": "Bearer " + api_key}
    try:
        ws = await websockets.connect(url, additional_headers=headers, max_size=None)
    except TypeError:                       # older websockets spells it extra_headers
        ws = await websockets.connect(url, extra_headers=headers, max_size=None)
    except Exception as e:
        return None, "connect failed: %s" % e

    try:
        await ws.send(json.dumps({"type": "session.update",
                                  "session": {"type": "realtime", "output_modalities": ["text"]}}))
        await ws.send(json.dumps({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user", "content": [
                {"type": "input_text", "text": QUESTION},
                {"type": "input_image", "image_url": data_uri(image_path)},
            ]}}))
        await ws.send(json.dumps({"type": "response.create"}))

        chunks = []
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=120)
            ev = json.loads(raw)
            kind = ev.get("type", "")
            if kind == "error":
                return None, "error event: %s" % json.dumps(ev.get("error", ev))[:300]
            if kind.endswith("delta") and isinstance(ev.get("delta"), str):
                chunks.append(ev["delta"])
            if kind == "response.done":
                status = (ev.get("response") or {}).get("status")
                if status == "failed":
                    detail = (ev.get("response") or {}).get("status_details") or {}
                    return None, "response failed: %s" % json.dumps(detail)[:300]
                return "".join(chunks).strip(), "status=%s" % status
    except asyncio.TimeoutError:
        return None, "timed out waiting for a response"
    finally:
        await ws.close()


def verdict(answer, expected):
    if answer is None:
        return "REJECTED"
    return "ok" if expected.lower() in answer.lower() else "WRONG(said %r)" % answer[:40]


async def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="gpt-5.6-luna,gpt-realtime-2.1-mini")
    ap.add_argument("--seated", default=None, help="defaults to the KB's Typing front frame")
    ap.add_argument("--standing", default=None, help="defaults to the KB's Idle front frame")
    args = ap.parse_args(argv)

    from paths import FRAMES_DIR
    import unity_sampler

    def kb_front(clip):
        """A `front` frame of one clip, whatever it is called. Frame filenames carry the view, the
        ordinal and the percent, and the percent moves whenever pose-coverage picks different moments,
        so naming one here would go stale on the next re-render."""
        got = [p for p in unity_sampler.frame_paths(os.path.join(FRAMES_DIR, clip))
               if os.path.basename(p).startswith("front_t")]
        return got[0] if got else os.path.join(FRAMES_DIR, clip, "front_t0.jpg")

    seated = args.seated or kb_front("Typing")
    standing = args.standing or kb_front("Idle")
    cases = [("Typing seated", seated, "SITTING"), ("Idle standing", standing, "STANDING")]
    for _, path, _ in cases:
        if not os.path.exists(path):
            print("missing image: %s" % path)
            return 1

    api_key = keys.load_openai_key()
    failures = 0
    for model in args.models.split(","):
        model = model.strip()
        print("\n== %s" % model)
        for label, path, expected in cases:
            if "realtime" in model:
                answer, note = await ask_realtime(model, api_key, path)
            else:
                answer, note = ask_chat(model, api_key, path)
            v = verdict(answer, expected)
            if v != "ok":
                failures += 1
            print("   %-10s expect %-8s -> %-22s (%s)" % (label, expected, v, note))
    print("\n%s" % ("all models see the frames" if not failures
                    else "%d check(s) failed -- those models must use motion_timing, not the frames" % failures))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
