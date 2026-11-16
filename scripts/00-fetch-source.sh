#!/usr/bin/env bash
# 00 — fetch the pinned ungoogled-chromium tooling and the Chromium source tarball it names,
# unpack it into build/src, and prune prebuilt binaries. ~2 GB download, ~20 GB unpacked.
source "$(dirname "$0")/common.sh"
mkdir -p "$WORK/download_cache"

if [ "$TARGET" = mac ]; then
  clone_at "$OVERLAY" "$OVERLAY_TAG"
else
  clone_at ungoogled-chromium "$UG_TAG"
  clone_at "$OVERLAY" "$OVERLAY_TAG"
fi

if [ -d "$SRC" ]; then
  echo "source tree already at $SRC (delete it to refetch)"; exit 0
fi
python3 "$UG/utils/downloads.py" retrieve -c "$WORK/download_cache" -i "$UG/downloads.ini"
python3 "$UG/utils/downloads.py" unpack -c "$WORK/download_cache" -i "$UG/downloads.ini" -- "$SRC"
python3 "$UG/utils/prune_binaries.py" "$SRC" "$UG/pruning.list"

# macOS needs a prebuilt LLVM + Rust toolchain the overlay lists separately.
if [ "$TARGET" = mac ]; then
  arch="arm64"; [ "$(uname -m)" = x86_64 ] && arch="x86-64"
  for ini in "downloads-$arch.ini" "downloads-$arch-rustlib.ini"; do
    python3 "$UG/utils/downloads.py" retrieve -c "$WORK/download_cache" -i "$WORK/$OVERLAY/$ini"
    python3 "$UG/utils/downloads.py" unpack -c "$WORK/download_cache" -i "$WORK/$OVERLAY/$ini" -- "$SRC"
  done
fi

echo "OK: Chromium $(cat "$UG/chromium_version.txt") source at $SRC"
echo "next -> scripts/01-apply-patches.sh"
