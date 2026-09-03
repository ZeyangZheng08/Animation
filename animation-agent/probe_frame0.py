#!/usr/bin/env python3
"""probe_frame0.py -- render the OPENING frames of one clip, into a scratch dir.

Why this exists and is not `extract.py render`: `render` samples where `select_fracs` says the poses
are, and clears its target directory first, so pointing it at a chosen time range would destroy the
accepted review frames. This writes elsewhere and touches nothing in the KB.

It was written when `select_fracs` sampled inside an "action window" that structurally excluded frame 0,
which meant no frame 0 had ever been rendered for any action. Coverage-based selection picks frame 0
whenever the opening pose is the only thing representing it -- on a clip that opens on its held pose it now does -- so this
is no longer the only way to see one. It is still the way to see a RANGE of opening frames at a chosen
resolution, which is what an import artefact at the head of a clip looks like.
"""
import argparse
import base64
import json
import os
import sys

import paths
import unity_sampler
from paths import KB_DIR

OUT = os.path.expanduser("~/render_probe")


def clip_entry(clip_name):
    path = paths.records_by_clip_name().get(clip_name)
    if not path:
        return None
    src = paths.read_json(path).get("source_clip") or {}
    return {"id": os.path.splitext(os.path.basename(path))[0], "guid": src["guid"], "file_id": src["file_id"]}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("--fracs", default="0.0,0.02,0.06,0.12",
                    help="clip fractions to render; 0.0 is the first frame")
    ap.add_argument("--views", default="front,front_left",
                    help="comma-separated ring view names, or 'all' for the whole eight-view ring")
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
    raw_path = os.path.join(paths.RAW_DIR, args.clip + ".json")
    if os.path.exists(raw_path):
        with open(raw_path, encoding="utf-8") as fh:
            raw = json.load(fh)
        views = unity_sampler.view_ring(raw.get("root_fwd"))
        n = raw["frames"]
        print("clip %s: %d frames" % (args.clip, n))
    # `render` shoots the whole ring; this probe shoots a RANGE of times, so the default is a couple of
    # angles -- eight angles x a dozen opening frames is a lot of pictures to look at an import artefact.
    if args.views.strip().lower() != "all":
        want = [v.strip() for v in args.views.split(",") if v.strip()]
        views = [v for v in views if v[0] in want] or views

    fracs = [float(x) for x in args.fracs.split(",")]
    print("views: %s" % ", ".join(n for n, _ in views))
    print("fracs: %s" % fracs)

    ok, text = unity_sampler.render_clip_frames(clip, views, fracs, host=args.host, port=args.port,
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
        if not sep or not name.lower().endswith(unity_sampler.FRAME_SUFFIXES) or not b64:
            continue
        with open(os.path.join(out_dir, name), "wb") as fh:
            fh.write(base64.b64decode(b64))
        written += 1
        print("  %s" % os.path.join(out_dir, name))
    print("%d frames -> %s" % (written, out_dir))
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
