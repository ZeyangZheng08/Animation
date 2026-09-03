#!/usr/bin/env bash
# Refresh this publish copy from the two working repositories, then commit and push by hand.
#
# This repository is a one-way MIRROR. Nothing here is edited in place: everything under unity/ and
# animation-agent/ is copied from the source of truth, so an edit made here is lost on the next run.
#
# SIX TRAPS, each already paid for once. Every one of them fails QUIETLY -- a plausible count, exit 0,
# nothing in red -- which is why the two guards below exist.
#
#   1. Never run this WSL git inside /mnt/d. Linux git over the 9p mount reports hundreds of files as
#      dirty on file mode and CRLF alone. Windows git decides what is tracked over there --
#      `git.exe -C "D:/..."` -- and this script only reads bytes back out of it.
#   2. `git.exe` drains the stdin of the `while read` loop it is called from. Without `</dev/null` the
#      loop stops at the first unity/ path, having done the animation-agent files and none of the 571
#      Unity ones, and still prints a refreshed count. The count guard after the loop catches it.
#   3. `git show HEAD:path` on an LFS-tracked file returns the POINTER TEXT, not the file. Publishing
#      that writes "version https://git-lfs.github.com/spec/v1" where a PNG belongs, on the one branch
#      whose point is carrying no LFS. LFS paths are listed once and read from the Windows working tree
#      instead. The LFS-count guard catches an unreachable git.exe, which would look like "no LFS here".
#   4. For everything else, read content from the object store rather than the Windows working tree.
#      That tree is checked out CRLF while this branch is pinned LF, so a byte compare calls every text
#      file different, copies it needlessly, and re-injects CRLF. It also means only COMMITTED work is
#      ever published, which is the right rule anyway.
#   5. Never pick files by subtracting `git lfs ls-files` from `git ls-files`. One FBX under
#      Assets/Animations has a Unicode apostrophe in its name, so the first quotes that path and the
#      second does not, and the difference smuggles it through as if it were text. New files are offered
#      by extension whitelist. Also: plain `git lfs ls-files` truncates paths at spaces -- use `-n`.
#   6. Test list membership with `case`, not `grep -q` down a pipe. Under `pipefail` the early exit of
#      `grep -q` SIGPIPEs the writer and the pipeline reports failure though the pattern matched. It is
#      a race, so it misfires on only some entries and reads like a real finding.
#
# Editing note: this file contains no literal CR. If you patch it with a script, open it in BINARY mode
# -- Python's text mode converts a lone CR to LF, which once turned `tr -d CR` into `tr -d newline` and
# collapsed the whole LFS path list onto one line.
set -euo pipefail

UNITY_WIN="D:/Research/AI_agent/Animation_agent/Animation"
UNITY_POSIX="/mnt/d/Research/AI_agent/Animation_agent/Animation"
AGENT="$HOME/Research/animation_agent"
cd "$(dirname "$0")"

# --adopt copies the reported additions in; --ext adds extensions to the candidate filter.
#
# WHY --ext EXISTS. Candidates are filtered to extensions the branch ALREADY carries, which is what
# stops an .fbx being smuggled in (trap 5). The cost of deriving that list from the branch is that it
# cannot bootstrap: when a published artefact changes format -- the KB render frames went from PNG to
# JPEG -- the new files match no carried extension, so the report says nothing at all and the old ones
# are listed as gone. Naming the extension on the command line is the human decision the report was
# there to inform, made explicitly.
adopt=; extra_exts=
while [ $# -gt 0 ]; do
  case "$1" in
    --adopt) adopt=1 ;;
    --ext)   shift; extra_exts="${1:-}" ;;
    --ext=*) extra_exts="${1#--ext=}" ;;
    *) echo "usage: sync.sh [--adopt] [--ext jpg[,webp...]]" >&2; exit 2 ;;
  esac
  shift
done
extra_exts=$(printf '%s' "$extra_exts" | tr ',' '|')

nl="
"   # a literal newline; the membership tests fence candidates with it

# `|| true` matters: set -e aborts on a failing assignment, so without it git.exe exiting 128
# kills the script here and the guard below never gets to say why.
lfs_paths=$(git.exe -C "$UNITY_WIN" lfs ls-files -n 2>/dev/null | tr -d '\015' || true)
lfs_count=$(printf '%s' "$lfs_paths" | grep -c . || true)
if [ "$lfs_count" -lt 100 ]; then
  echo "ABORT: the Unity repository listed $lfs_count LFS files; it tracks about 800." >&2
  echo "git.exe is probably not reachable from here. Publishing now would write LFS pointer" >&2
  echo "text where the images belong." >&2
  exit 1
fi
lfs_paths="$nl$lfs_paths$nl"

blob() {  # published path -> its current committed content on stdout; non-zero if it is gone
  case "$1" in
    unity/*)
      rel="${1#unity/}"
      case "$lfs_paths" in
        *"$nl$rel$nl"*) cat "$UNITY_POSIX/$rel" 2>/dev/null ;;
        *)              git.exe -C "$UNITY_WIN" show "HEAD:$rel" 2>/dev/null </dev/null ;;
      esac ;;
    animation-agent/_traces/*)
      # The run traces are deliberately untracked upstream (.gitignore) and published here as
      # evidence, so they come from the working tree: plain text, LF, no LFS, about 400 KB.
      cat "$AGENT/${1#animation-agent/}" 2>/dev/null ;;
    animation-agent/*) git -C "$AGENT" show "HEAD:${1#animation-agent/}" 2>/dev/null </dev/null ;;
    *) return 1 ;;    # sync.sh, .pubignore, README.md, .gitattributes are authored here, not mirrored
  esac
}

tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
expected=$(git ls-files | grep -c .)
seen=0; refreshed=0; missing=()
while IFS= read -r -d '' f; do
  seen=$((seen+1))
  case "$f" in unity/*|animation-agent/*) ;; *) continue ;; esac
  if ! blob "$f" > "$tmp"; then missing+=("$f"); continue; fi
  cmp -s "$tmp" "$f" || { cp "$tmp" "$f"; refreshed=$((refreshed+1)); echo "  updated $f"; }
done < <(git ls-files -z)

if [ "$seen" -ne "$expected" ]; then
  echo "ABORT: examined $seen of $expected tracked files -- something consumed the loop's stdin." >&2
  echo "Whatever this run reported is incomplete. Do not commit it." >&2
  exit 1
fi

echo "refreshed $refreshed of $seen file(s)"
if [ ${#missing[@]} -gt 0 ]; then
  echo
  echo "=== gone upstream, or not committed there (delete by hand if that is intended) ==="
  printf '  %s\n' "${missing[@]}"
fi

# Candidate additions: committed upstream, in a directory this branch already publishes, with an
# extension this branch already carries. Reported only -- adding is a judgement call, not a default.
shopt -s extglob globstar
ignore=()
while IFS= read -r pat; do
  case "$pat" in ''|'#'*) continue ;; esac
  ignore+=("$pat")
done < .pubignore

exts=$(git ls-files | sed -n 's|.*\.\([A-Za-z0-9]\+\)$|\1|p' | sort -u | paste -sd'|')
[ -z "$extra_exts" ] || exts="$exts|$extra_exts"
have=$(git ls-files -z | tr '\0' '\n')
dirs=$(printf '%s\n' "$have" | xargs -d '\n' -n1 dirname | sort -u)
have="$nl$have$nl"
dirs="$nl$dirs$nl"
{
  git.exe -C "$UNITY_WIN" ls-files 2>/dev/null </dev/null | tr -d '\015' | sed 's|^|unity/|'
  git -C "$AGENT" ls-files </dev/null | sed 's|^|animation-agent/|'
} | grep -Ei "\.($exts)\$" | while IFS= read -r f; do
  case "$have" in *"$nl$f$nl"*) continue ;; esac
  case "$dirs" in *"$nl$(dirname "$f")$nl"*) ;; *) continue ;; esac
  skip=
  for pat in "${ignore[@]}"; do
    case "$f" in $pat) skip=1; break ;; esac
  done
  [ -n "$skip" ] || echo "  $f"
done | sort > /tmp/pubcode-new.txt || true
if [ -s /tmp/pubcode-new.txt ]; then
  echo
  if [ -n "$adopt" ]; then
    # TAKEN THROUGH `blob`, NEVER COPIED FROM THE WINDOWS WORKING TREE. Hand-copying is how trap 4
    # gets paid for a second time: that tree is checked out CRLF while this branch is pinned LF, so a
    # file carried across by hand arrives with the wrong endings and every later run calls it changed.
    # Reusing the same function also means an LFS-tracked addition would come from the right place.
    #
    # Still not the default. Which new files belong on a published branch is a judgement, and the
    # report above is what it is made from; this only removes the copying from the human part of it.
    echo "=== adopting new upstream files ==="
    while IFS= read -r f; do
      f="${f#"${f%%[![:space:]]*}"}"      # the report indents; the path does not
      [ -n "$f" ] || continue
      mkdir -p "$(dirname "$f")"
      if blob "$f" > "$tmp"; then
        cp "$tmp" "$f"
        git add -- "$f"
        echo "  added $f"
      else
        echo "  SKIPPED (no committed content upstream) $f" >&2
      fi
    done < /tmp/pubcode-new.txt
  else
    echo "=== new upstream, in directories already published (review, then re-run with --adopt) ==="
    cat /tmp/pubcode-new.txt
  fi
fi

echo
git status -sb
