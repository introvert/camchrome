#!/usr/bin/env bash
# Rebase patches/series onto a new Chromium tree, one patch at a time.
#
#   scripts/rebase-series.sh            # apply from where it stopped; regenerate each patch
#   scripts/rebase-series.sh --status   # which patches are done, which one is stuck
#
# Run inside the build environment (it uses $SRC from common.sh) on a tree that has the
# ungoogled base and overlay applied but not the series (01-apply-patches.sh --check leaves it
# like that). For each patch in order: snapshot the files it touches, apply it with fuzz, and
# write the patch back to patches/ regenerated against the new tree (its description header is
# kept). When a hunk does not apply, the script stops and leaves <file>.rej next to the source:
# fix the source by hand (the hunk is what the patch meant), delete the .rej, and run the script
# again; it regenerates the stuck patch from your fix and carries on.
source "$(dirname "$0")/common.sh"
STATE="$WORK/rebase"
mkdir -p "$STATE/pre" "$STATE/done"

series() {
  while IFS= read -r line; do
    line="${line%%#*}"; line="$(printf '%s' "$line" | tr -d '[:space:]')"
    [ -n "$line" ] && printf '%s\n' "$line"
  done < "$REPO/patches/series"
}

targets() { # files a patch touches, relative to $SRC
  grep -E '^\+\+\+ ' "$1" | awk '{print $2}' | sed 's|^b/||' | grep -v '^/dev/null$' | sort -u
}

regenerate() { # patch-name
  local p="$1" src_patch="$REPO/patches/$1" pre="$STATE/pre/$1" out
  out="$(mktemp)"
  sed -n '/^--- /q;p' "$src_patch" > "$out"   # description header, verbatim
  while IFS= read -r f; do
    if [ -e "$pre/$f" ]; then a="$pre/$f"; la="a/$f"; else a=/dev/null; la=/dev/null; fi
    if [ -e "$SRC/$f" ]; then b="$SRC/$f"; lb="b/$f"; else b=/dev/null; lb=/dev/null; fi
    diff -u --label "$la" --label "$lb" "$a" "$b" >> "$out" || true
  done < <(targets "$src_patch")
  mv "$out" "$src_patch"
  touch "$STATE/done/$p"
}

if [ "${1:-}" = --status ]; then
  while read -r p; do
    if [ -e "$STATE/done/$p" ]; then s=done; elif [ -d "$STATE/pre/$p" ]; then s=STUCK; else s=todo; fi
    echo "$s $p"
  done < <(series)
  exit 0
fi

while read -r p; do
  [ -e "$STATE/done/$p" ] && continue
  if [ -d "$STATE/pre/$p" ]; then
    # Resuming a stuck patch: the hand fix is in the tree.
    rej="$(find "$SRC" -name '*.rej' -newer "$STATE/pre/$p" 2>/dev/null | head -1)"
    [ -n "$rej" ] && { echo "still unresolved: $rej"; exit 1; }
    regenerate "$p"; echo "fixed   $p"; continue
  fi
  mkdir -p "$STATE/pre/$p"
  while IFS= read -r f; do
    [ -e "$SRC/$f" ] && { mkdir -p "$STATE/pre/$p/$(dirname "$f")"; cp -p "$SRC/$f" "$STATE/pre/$p/$f"; }
  done < <(targets "$REPO/patches/$p")
  if patch -p1 -F3 --forward --no-backup-if-mismatch -d "$SRC" < "$REPO/patches/$p" > "$STATE/last.log" 2>&1; then
    regenerate "$p"
    if grep -q "fuzz\|offset" "$STATE/last.log"; then echo "fuzzed  $p"; else echo "clean   $p"; fi
  else
    echo "STUCK   $p"; grep -E "FAILED|rej|can't find" "$STATE/last.log" | sed 's/^/    /'
    exit 1
  fi
done < <(series)
touch "$SRC/.camchrome-patched"
echo "series rebased onto $(cat "$SRC/chrome/VERSION" | paste -sd. | sed 's/[A-Z]*=//g')"
