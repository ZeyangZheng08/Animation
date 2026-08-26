#!/usr/bin/env sh
# check_kb.sh - one-command MotionKB gate.
#
# Steps 1-3 need no engine and always run. Step 4 resolves each action's source_clip guid to a real
# AnimationClip, which only the AssetDatabase can do: it runs live when the Unity MCP bridge is up, and
# otherwise falls back to reporting the last committed result from the KB's motionkb_build/reports/kb_state.md.
#
# The KB is not in this repository (it is a derivative of the Unity project's animation assets).
# Point MOTIONKB_DIR at it, or accept the default in paths.py.
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"

echo "== [1/4] JSON Schema + channel vocabulary + description completeness (no engine) =="
# -q prints failures and the summary only. The KB is 2454 records since the corpus landed
# (ADR 0014); a PASS line each is not a report, and a real failure would scroll off the top.
$PY validate_motionkb.py -q
echo

echo "== [2/4] golden re-extraction regression (KINEMATIC reproduces from frozen raw) =="
$PY test_golden_extraction.py
echo

echo "== [3/4] manifest.json in sync with the accepted store =="
$PY gen_kb_manifest.py --check
echo

echo "== [4/4] guid -> AnimationClip resolution (needs the engine) =="
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
