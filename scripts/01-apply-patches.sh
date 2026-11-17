#!/usr/bin/env bash
# 01 — apply, in order: the ungoogled base patches, the target's overlay patches, then
# patches/series (the fingerprint set).
#
#   --check   apply base + overlay if needed, then dry-run each series patch and list the ones
#             that no longer apply. Use this while rebasing patches/ onto a new Chromium; it
#             leaves the series unapplied.
source "$(dirname "$0")/common.sh"
CHECK=0; [ "${1:-}" = "--check" ] && CHECK=1
[ -d "$SRC" ] || { echo "no source tree at $SRC — run scripts/00-fetch-source.sh first"; exit 1; }
[ -e "$SRC/.camchrome-patched" ] && { echo "tree fully patched already; refetch to start over"; exit 1; }

if [ ! -e "$SRC/.ungoogled-patched" ]; then
  echo "applying ungoogled-chromium base"
  python3 "$UG/utils/patches.py" apply "$SRC" "$UG/patches"
  echo "applying $OVERLAY"
  overlay_dir="$WORK/$OVERLAY/patches"
  if [ "$TARGET" = linux ]; then
    # On Linux the Windows overlay is only needed for its source changes (905 depends on
    # its chrome_command_ids.h, 900 on its rc.py change). Its Windows-host tooling
    # patches are skipped (they break gn's bootstrap and the hermetic Python on Linux),
    # and so are patches that edit files only a Windows checkout has (e.g.
    # third_party/typescript/windows-amd64/src/lib/lib.dom.d.ts).
    overlay_dir="$WORK/overlay-linux"
    rm -rf "$overlay_dir"; mkdir -p "$overlay_dir"
    : > "$overlay_dir/series"
    while IFS= read -r p; do
      case "$p" in ''|\#*) continue ;; esac
      # Windows-host build tooling: these retarget gn's bootstrap at gn.exe, swap the
      # hermetic Python, drop Crubit, change compiler flags, etc. They break a Linux
      # build and no fingerprint patch depends on them.
      case "$(basename "$p" .patch)" in
        windows-fix-building-gn|windows-fix-python|windows-fix-building-without-crubit|\
        windows-fix-building-with-rust|windows-fix-complex-builtins|\
        windows-disable-clang-version-check|windows-fix-unsupported-llvm-flags|\
        windows-fix-rc-terminating-error|windows-fix-clang-format-exe|\
        windows-fix-licenses-gn-path|windows-disable-win-build-output|\
        windows-no-unknown-warnings)
          echo "  skipping $p (Windows-host build tooling)"; continue ;;
      esac
      missing=0
      while IFS= read -r f; do
        [ "$f" = /dev/null ] && continue
        [ -e "$SRC/${f#a/}" ] || missing=1
      done < <(grep '^--- ' "$WORK/$OVERLAY/patches/$p" | awk '{print $2}')
      if [ "$missing" = 1 ]; then
        echo "  skipping $p (targets a Windows-only file)"
        continue
      fi
      mkdir -p "$overlay_dir/$(dirname "$p")"
      ln -sf "$WORK/$OVERLAY/patches/$p" "$overlay_dir/$p"
      printf '%s\n' "$p" >> "$overlay_dir/series"
    done < "$WORK/$OVERLAY/patches/series"
  fi
  python3 "$UG/utils/patches.py" apply "$SRC" "$overlay_dir"
  touch "$SRC/.ungoogled-patched"
fi

series() {
  while IFS= read -r line; do
    line="${line%%#*}"; line="$(printf '%s' "$line" | tr -d '[:space:]')"
    [ -n "$line" ] || continue
    # 900/905 are Windows cross-build mechanics (rc.py wrapper, .rc branding,
    # rc-invoked guards). They are irrelevant and do not apply to a macOS tree.
    if [ "$TARGET" = mac ]; then
      case "$line" in
        900-windows-build-fixes.patch|905-rc-invoked-guard.patch) continue ;;
      esac
    fi
    printf '%s\n' "$line"
  done < "$REPO/patches/series"
}

if [ "$CHECK" = 1 ]; then
  ok=0; bad=0
  while read -r p; do
    if patch -p1 -s -f --dry-run -d "$SRC" < "$REPO/patches/$p" >/dev/null 2>&1; then
      ok=$((ok + 1))
    else
      bad=$((bad + 1)); echo "FAILS  $p"
    fi
  done < <(series)
  # Each patch is tried alone, so one that builds on an earlier patch's new file shows as
  # failing until that earlier patch is applied.
  echo "$ok apply cleanly, $bad fail"
  exit 0
fi

echo "applying fingerprint series"
while read -r p; do
  echo "  $p"
  patch -p1 -s -d "$SRC" < "$REPO/patches/$p"
done < <(series)
touch "$SRC/.camchrome-patched"

echo "OK: patched $SRC"
echo "next -> scripts/02-build.sh"
