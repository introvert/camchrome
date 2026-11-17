#!/usr/bin/env bash
# Print the C/C++ sources the fingerprint series edits that compile for TARGET, as ninja
# "<source>^" targets (build that file's object). CI compiles exactly these to check the
# series builds without a multi-hour full link:
#   NINJA_TARGETS="$(scripts/patched-sources.sh)" scripts/02-build.sh
source "$(dirname "$0")/common.sh"
while IFS= read -r line; do
  line="${line%%#*}"; line="$(printf '%s' "$line" | tr -d '[:space:]')"
  [ -n "$line" ] || continue
  if [ "$TARGET" = mac ]; then
    case "$line" in 900-*|905-*) continue ;; esac
  fi
  grep '^+++ ' "$REPO/patches/$line" | awk '{print $2}' | sed 's|^b/||'
done < "$REPO/patches/series" | sort -u | grep -E '\.(cc|cpp|mm)$' | while IFS= read -r f; do
  case "$TARGET:$f" in
    *:*_win.cc|*:*/win/*|*:*uia_client*) continue ;;   # Windows-only units
    linux:*_mac.cc|linux:*_mac.mm|linux:*.mm) continue ;;
    mac:*/linux/*|mac:*_linux.cc) continue ;;
  esac
  printf '../../%s^\n' "$f"
done
