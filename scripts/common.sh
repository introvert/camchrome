# shellcheck shell=bash
# Sourced by the numbered scripts. Sets REPO, WORK, SRC, TARGET and the UPSTREAM pins.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${WORK:-$REPO/build}"
SRC="$WORK/src"

case "$(uname -s)" in
  Darwin) TARGET="${TARGET:-mac}" ;;
  *)      TARGET="${TARGET:-linux}" ;;
esac
case "$TARGET" in linux|win|mac) ;; *) echo "TARGET must be linux, win or mac"; exit 2 ;; esac

while IFS='=' read -r k v; do
  case "$k" in ''|\#*) continue ;; esac
  # The environment wins over the pinned file, so a one-off rebase can try a
  # new tag without editing the pins:
  #   WORK=./build157 UGM_TAG=157.0.8103.78-1.1 scripts/00-fetch-source.sh
  if [ -z "${!k:-}" ]; then
    export "$k=$v"
  fi
done < "$REPO/UPSTREAM"

# The overlay repo each target needs on top of the ungoogled base. Linux takes the Windows
# overlay too: the series was written against that tree (905-rc-invoked-guard needs its
# chrome_command_ids.h), and its Windows-only files don't affect a Linux build.
case "$TARGET" in
  linux|win) OVERLAY="ungoogled-chromium-windows"; OVERLAY_TAG="$UGW_TAG" ;;
  mac)   OVERLAY="ungoogled-chromium-macos";   OVERLAY_TAG="$UGM_TAG" ;;
esac

# Chromium's own tarball tooling comes from the base repo, except on macOS where the overlay
# carries the base as a submodule pinned to the matching Chromium version.
UG="$WORK/ungoogled-chromium"
[ "$TARGET" = mac ] && UG="$WORK/$OVERLAY/ungoogled-chromium"

clone_at() { # repo-name tag
  local dir="$WORK/$1"
  [ -d "$dir" ] || git clone --quiet "https://github.com/ungoogled-software/$1.git" "$dir"
  git -C "$dir" fetch --tags --quiet
  git -C "$dir" -c advice.detachedHead=false checkout --quiet "$2"
  git -C "$dir" submodule update --init --quiet
}
