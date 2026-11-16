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
  python3 "$UG/utils/patches.py" apply "$SRC" "$WORK/$OVERLAY/patches"
  touch "$SRC/.ungoogled-patched"
fi

series() {
  while IFS= read -r line; do
    line="${line%%#*}"; line="$(printf '%s' "$line" | tr -d '[:space:]')"
    [ -n "$line" ] && printf '%s\n' "$line"
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
