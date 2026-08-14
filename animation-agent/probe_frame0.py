#!/usr/bin/env python3
"""probe_frame0.py -- render the OPENING frames of one clip, into a scratch dir.

Why this exists and is not `extract.py render`: `select_fracs` deliberately samples inside the ACTION
WINDOW, which excludes the idle transitions at the clip ends, so frame 0 has never been rendered for
any action. `render` also clears the target directory first, so pointing it at a different time range
would destroy the accepted review frames. This writes elsewhere and touches nothing in the KB.
"""
import argparse
import base64
import json
import os
import sys

import metrics
import paths
import unity_sampler
from paths import KB_DIR

OUT = os.path.expanduser("~/render_probe")


def clip_entry(clip_name):
    for fn in sorted(os.listdir(KB_DIR)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(KB_DIR, fn)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            try:
                doc = json.load(fh)
            except ValueError:
                continue
        src = doc.get("source_clip") or {}
        if src.get("clip_name") == clip_name:
            return {"id": doc.get("action_id"), "guid": src["guid"], "file_id": src["file_id"]}
    return None


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("--fracs", default="0.0,0.02,0.06,0.12",
                    help="clip fractions to render; 0.0 is the first frame")
    ap.add_argument("--host", default=unity_sampler.DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=unity_sampler.DEFAULT_PORT)
    ap.add_argument("--instance", default=None)
    args = ap.parse_args(argv)

    clip = clip_entry(args.clip)
    if not clip:
        print("no accepted record with clip_name %r" % args.clip)
        return 1
    if not unity_sampler.bridge_healthy(args.host, args.port):
        print("Unity MCP bridge not reachable at %s:%d" % (args.host, args.port))
        return 1

    views = unity_sampler.RENDER_VIEWS
    raw_path = os.path.join(KB_DIR, "_raw", args.clip + ".json")
    if os.path.exists(raw_path):
        with open(raw_path, encoding="utf-8") as fh:
            raw = json.load(fh)
        views = unity_sampler.select_views(metrics.channel_blocks(raw), raw.get("root_fwd"))
        n = raw["frames"]
        print("clip %s: %d frames" % (args.clip, n))

    fracs = [float(x) for x in args.fracs.split(",")]
    print("views: %s" % ", ".join(n for n, _ in views))
    print("fracs: %s" % fracs)

    cs = unity_sampler.build_render_csharp(clip, views=views, fracs=fracs)
    ok, text, _ = unity_sampler.run_csharp_over_http(cs, host=args.host, port=args.port,
                                                     instance=args.instance)
    if not ok:
        print("Unity error:\n%s" % (text or "")[:600])
        return 1

    out_dir = os.path.join(OUT, args.clip)
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    written = 0
    for line in (text or "").splitlines():
        name, sep, b64 = line.partition("|")
        if not sep or not name.endswith(".png") or not b64:
            continue
        with open(os.path.join(out_dir, name), "wb") as fh:
            fh.write(base64.b64decode(b64))
        written += 1
        print("  %s" % os.path.join(out_dir, name))
    print("%d frames -> %s" % (written, out_dir))
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
