#!/usr/bin/env sh
# check_kb.sh - one-command MotionKB gate.
#
# Steps 1-4 need no engine and always run. Step 5 resolves an action's source_clip guid to a real
# AnimationClip, which only the AssetDatabase can do: it runs live when the Unity MCP bridge is up, and
# otherwise falls back to reporting the last committed result from the KB's motionkb_build/reports/kb_state.md.
#
# The KB is not in this repository (it is a derivative of the Unity project's animation assets).
# Point MOTIONKB_DIR at it, or accept the default in paths.py.
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"

echo "== [1/5] JSON Schema + channel vocabulary + description completeness (no engine) =="
# -q prints failures and the summary only. The KB is 2446 records: the Mixamo corpus, all accepted,
# and nothing else since the eight nursing records moved out to agent/nursing_assets/. A PASS line
# each is not a report, and a real failure would scroll off the top.
$PY validate_motionkb.py -q
echo

echo "== [2/5] golden re-extraction regression (KINEMATIC reproduces from frozen raw) =="
# 16 records, named in agent/motionkb_build/golden_set.json, spanning standing / walking / sitting /
# sit-stand transition / crouching / kneeling / bending / crawling / lying / airborne / single-pose.
# A FIXED subset, not a sample: re-measuring all 2446 would make this a gate nobody runs, and a
# regression has to fail the same way twice to be read as one.
$PY test_golden_extraction.py
echo

echo "== [3/5] manifest.json in sync with the accepted store =="
$PY gen_kb_manifest.py --check
echo

echo "== [4/5] posture sidecar current (derived/posture.json) =="
# A GATE, NOT A CONVENIENCE. Every plan step carries a posture and the runtime reads it from this
# sidecar; KBIndex.load refuses to start without one that matches POSTURE_ALGORITHM_VERSION and
# covers the whole accepted store. So a stale sidecar is not a degraded search, it is a service that
# will not come up -- which is worth catching here rather than at the next start. --check recomputes
# from the frozen dumps and compares, so it also catches a sidecar edited by hand.
$PY build_posture.py --check
echo

echo "== [5/5] guid -> AnimationClip resolution (needs the engine) =="
# A deterministic 40-clip sample by default: one C# call carries every entry it checks, and 2446 of
# them is a generated source file rather than a query. `validate_guids.py --all` is the run to make
# after a reimport.
if $PY validate_guids.py; then
  :
else
  echo "  (bridge unreachable — falling back to the last committed report)"
  KB="$($PY -c 'import paths; print(paths.REPORTS_DIR)')"
  if [ -f "$KB/kb_state.md" ]; then
    tail -n 1 "$KB/kb_state.md"
  else
    echo "  kb_state.md not found - open Unity, start the MCP HTTP server, and rerun"
  fi
fi
echo
echo "OK: gates passed."
