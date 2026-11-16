#!/usr/bin/env bash
# 02 — bootstrap gn, write out/Default/args.gn from config/args.<target>.gn, and build chrome.
# Official ThinLTO + PGO build: ~32 GB RAM, ~100 GB disk, many hours. This repo's default
# macOS args (config/args.mac.gn) disable LTO/PGO/PCH so a 24 GB machine can build it.
source "$(dirname "$0")/common.sh"
[ -e "$SRC/.camchrome-patched" ] || { echo "run scripts/01-apply-patches.sh first"; exit 1; }
ARGS="$REPO/config/args.$TARGET.gn"
[ -f "$ARGS" ] || { echo "no $ARGS"; exit 1; }
cd "$SRC"

# Chromium 156+ build scripts need Python 3.10+; macOS's system python3 is 3.9.
# Prefer a Homebrew Python and expose it as `python3` for gn/ninja via a private
# bin dir we prepend to PATH.
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
  found=""
  for p in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /opt/homebrew/Cellar/python@3.15/*/bin/python3 /opt/homebrew/bin/python3.14; do
    if [ -x "$p" ] && "$p" -c 'import pyexpat' 2>/dev/null; then found="$p"; break; fi
  done
  if [ -n "$found" ]; then
    mkdir -p "$WORK/python-bin"
    ln -sf "$found" "$WORK/python-bin/python3"
    export PATH="$WORK/python-bin:$PATH"
    echo "using Python: $found"
  else
    echo "WARNING: no Python >= 3.10 found; the build may fail" >&2
  fi
fi

# Chromium's PGO profile name changes every release; use the one this tree ships with.
case "$TARGET" in
  linux) pgo_txt=chrome/build/linux.pgo.txt ;;
  win)   pgo_txt=chrome/build/win64.pgo.txt ;;
  mac)   pgo_txt=chrome/build/mac-arm.pgo.txt; [ "$(uname -m)" = x86_64 ] && pgo_txt=chrome/build/mac.pgo.txt ;;
esac
mkdir -p out/Default
if [ -f "$pgo_txt" ] && [ -f "chrome/build/pgo_profiles/$(cat "$pgo_txt")" ]; then
  sed "s|^pgo_data_path *=.*|pgo_data_path = \"//chrome/build/pgo_profiles/$(cat "$pgo_txt")\"|" \
    "$ARGS" > out/Default/args.gn
else
  echo "PGO profile for $pgo_txt is not in the tree; building without PGO"
  sed -e 's|^chrome_pgo_phase *=.*|chrome_pgo_phase = 0|' -e '/^pgo_data_path/d' \
    "$ARGS" > out/Default/args.gn
fi

# macOS tooling the ungoogled overlay builds out-of-band (see 00-fetch-source.sh).
if [ "$TARGET" = mac ]; then
  if [ ! -x third_party/rust-toolchain/bin/bindgen ]; then
    echo "building bindgen"
    python3 tools/rust/build_bindgen.py --skip-test
  fi
fi

if [ ! -x out/Default/gn ]; then
  python3 tools/gn/bootstrap/bootstrap.py --skip-generate-buildfiles -j "$(getconf _NPROCESSORS_ONLN)" -o out/Default/gn
fi
out/Default/gn gen out/Default --fail-on-unused-args

case "$TARGET" in
  linux) exec ninja -C out/Default chrome chrome_sandbox chrome_crashpad_handler ;;
  *)     exec ninja -C out/Default chrome ;;
esac
