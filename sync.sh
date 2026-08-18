#!/usr/bin/env bash
# Refresh this publish copy from the two working repositories, then commit and push by hand.
#
# This repository is a one-way MIRROR. Nothing here is edited in place: everything under unity/ and
# animation-agent/ is copied from the source of truth, so an edit made here is lost on the next run.
#
# FOUR TRAPS, each already paid for once:
#
#   1. Never run this WSL git inside /mnt/f. Linux git over the 9p mount reports hundreds of files as
#      dirty on file mode and CRLF alone. Windows git decides what is tracked over there --
#      `git.exe -C "F:/..."` -- and this script only reads bytes back out of it.
#   2. Read source content out of the object store (`git show HEAD:path`), never off the Windows
#      working tree. That tree is checked out CRLF while this branch is pinned LF, so a byte compare
#      calls every text file different, copies it needlessly, and puts CRLF back. The blob is already
#      normalised to LF and is binary-safe. It also means only COMMITTED work is ever published.
#   3. Never decide what to copy by subtracting the LFS list. One FBX under Assets/Animations has a
#      Unicode apostrophe in its name, so `git ls-files` quotes the path and `git lfs ls-files` does
#      not; the difference of the two lists leaves that FBX looking like an ordinary text file. New
#      files are offered by extension whitelist instead, and never added automatically.
#   4. Test list membership with `case`, not `grep -q` down a pipe. Under `pipefail` the early exit
#      of `grep -q` hands the writing end a SIGPIPE, so the pipeline reports failure even though the
#      pattern matched -- and it is a race, so only some published files get called new.
set -euo pipefail

nl="
"   # a literal newline; the membership tests fence candidates with it

UNITY_WIN="F:/Research/AI_agent/Animation/Animation_agent/Project/Animation"
UNITY_POSIX="/mnt/f/Research/AI_agent/Animation/Animation_agent/Project/Animation"
AGENT="$HOME/Research/animation-agent"
cd "$(dirname "$0")"

# The Unity repository tracks its binaries through LFS, and `git show HEAD:path` on an LFS file returns
# the POINTER TEXT, not the file. Publishing that would put "version https://git-lfs.github.com/spec/v1"
# where a PNG belongs -- on a branch whose whole point is carrying no LFS. So the LFS paths are listed
# once here and read from the Windows working tree instead, where they are already smudged to content.
# `lfs ls-files -n` is the only safe listing: the default output truncates paths at spaces.
lfs_paths=$(git.exe -C "$UNITY_WIN" lfs ls-files -n 2>/dev/null | tr -d "")
lfs_paths="$nl$lfs_paths$nl"

blob() {  # published path -> its current committed content on stdout; non-zero if it is gone
  # `</dev/null` is load-bearing, not tidiness: this runs inside a `while read` loop, and git.exe
  # drains the loop's stdin. Without it the loop stops at the first unity/ path -- silently, with a
  # success exit and a plausible-looking "refreshed N" line. See the count guard after the loop.
  case "$1" in
    unity/*)
      rel="${1#unity/}"
      case "$lfs_paths" in
        *"$nl$rel$nl"*) cat "$UNITY_POSIX/$rel" 2>/dev/null ;;
        *)              git.exe -C "$UNITY_WIN" show "HEAD:$rel" 2>/dev/null </dev/null ;;
      esac ;;
    animation-agent/*) git -C "$AGENT" show "HEAD:${1#animation-agent/}" 2>/dev/null </dev/null ;;
    *) return 1 ;;    # sync.sh, README.md, .gitattributes are authored here, not mirrored
  esac
}

tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
expected=$(git ls-files | wc -l)
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
have=$(git ls-files -z | tr '\0' '\n')
dirs=$(printf '%s\n' "$have" | xargs -d '\n' -n1 dirname | sort -u)
have="$nl$have$nl"
dirs="$nl$dirs$nl"
{
  git.exe -C "$UNITY_WIN" ls-files 2>/dev/null | tr -d '\r' | sed 's|^|unity/|'
  git -C "$AGENT" ls-files | sed 's|^|animation-agent/|'
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
  echo "=== new upstream, in directories already published (review, then git add) ==="
  cat /tmp/pubcode-new.txt
fi

echo
git status -sb
