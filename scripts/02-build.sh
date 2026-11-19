#!/usr/bin/env bash
# 02 — bootstrap gn, write out/Default/args.gn from config/args.<target>.gn, and build chrome.
# Official ThinLTO + PGO build: ~32 GB RAM, ~100 GB disk, many hours. This repo's default
# macOS args (config/args.mac.gn) disable LTO/PGO/PCH so a 24 GB machine can build it.
source "$(dirname "$0")/common.sh"
[ -e "$SRC/.camchrome-patched" ] || { echo "run scripts/01-apply-patches.sh first"; exit 1; }
# ARGS_FILE picks another args file, e.g. config/args.linux.lowmem.gn on a machine below the
# official build's ~32-64 GB peak.
ARGS="${ARGS_FILE:-$REPO/config/args.$TARGET.gn}"
[ -f "$ARGS" ] || { echo "no $ARGS"; exit 1; }
cd "$SRC"

# Go's default cache/GOPATH live under the user's home; a sandboxed or
# non-interactive builder may not be allowed to write there (Dawn's
# generate-sources action runs `go build`). Keep both inside WORK.
export GOCACHE="${GOCACHE:-$WORK/go-cache}"
export GOPATH="${GOPATH:-$WORK/go-path}"
mkdir -p "$GOCACHE" "$GOPATH"

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
# TARGET_CPU=x64|arm64 overrides the config's target_cpu (Linux arm64 and macOS x64
# are cross-compiled from an x64 / arm64 host). Linux drops PGO for arm64: the tree
# ships an x64 profile only.
ARGS_IN="$ARGS"
if [ -n "${TARGET_CPU:-}" ]; then
  ARGS_IN="$WORK/args.$TARGET.$TARGET_CPU.gn"
  if grep -q '^target_cpu' "$ARGS"; then
    sed "s|^target_cpu *=.*|target_cpu = \"$TARGET_CPU\"|" "$ARGS" > "$ARGS_IN"
  else
    { cat "$ARGS"; echo "target_cpu = \"$TARGET_CPU\""; } > "$ARGS_IN"
  fi
  if [ "$TARGET" = linux ] && [ "$TARGET_CPU" = arm64 ]; then pgo_txt=/nonexistent; fi
fi
if [ -f "$pgo_txt" ] && [ -f "chrome/build/pgo_profiles/$(cat "$pgo_txt")" ]; then
  sed "s|^pgo_data_path *=.*|pgo_data_path = \"//chrome/build/pgo_profiles/$(cat "$pgo_txt")\"|" \
    "$ARGS_IN" > out/Default/args.gn
else
  echo "PGO profile for $pgo_txt is not in the tree; building without PGO"
  sed -e 's|^chrome_pgo_phase *=.*|chrome_pgo_phase = 0|' -e '/^pgo_data_path/d' \
    "$ARGS_IN" > out/Default/args.gn
fi

# macOS tooling the ungoogled overlay builds out-of-band (see 00-fetch-source.sh).
if [ "$TARGET" = mac ]; then
  if [ ! -x third_party/rust-toolchain/bin/bindgen ]; then
    echo "building bindgen"
    python3 tools/rust/build_bindgen.py --skip-test
  fi

  # Chromium 157 builds Crubit's cc_bindings_from_rs from the rustc-dev
  # toolchain; GN expects it at rust_crubit_root/bin. The 156 toolchain has no
  # crubit sources, so this is conditional.
  if [ -f third_party/rust-toolchain/crubit/lib/third_party/crubit/Cargo.toml ] && \
     [ ! -x third_party/rust-toolchain/crubit/bin/cc_bindings_from_rs ]; then
    echo "building crubit"
    CARGO_HOME="$SRC/out/crubit/cargo_home" \
    RUSTC="$SRC/third_party/rust-toolchain/bin/rustc" \
    RUSTFLAGS="-Clink-args=-Wl,-rpath,@loader_path/../../rustc/lib" \
      "$SRC/third_party/rust-toolchain/bin/cargo" build --release --locked \
        -p cc_bindings_from_rs --bin cc_bindings_from_rs \
        --manifest-path \
        "$SRC/third_party/rust-toolchain/crubit/lib/third_party/crubit/Cargo.toml" \
        --target-dir "$SRC/out/crubit"
    mkdir -p third_party/rust-toolchain/crubit/bin
    cp out/crubit/release/cc_bindings_from_rs \
       third_party/rust-toolchain/crubit/bin/
  fi
fi

if [ ! -x out/Default/gn ]; then
  if [ "$TARGET" = linux ]; then
    # gn needs C++23 <expected>; the distro clang may not provide it with the system
    # libstdc++, so bootstrap with the tree's own clang.
    llvm="$SRC/third_party/llvm-build/Release+Asserts/bin"
    export CC="$llvm/clang" CXX="$llvm/clang++" AR="$llvm/llvm-ar" LDFLAGS="-fuse-ld=lld"
  fi
  python3 tools/gn/bootstrap/bootstrap.py --skip-generate-buildfiles -j "$(getconf _NPROCESSORS_ONLN)" -o out/Default/gn
fi
# V8's metagen step (157+) looks for gn under buildtools/<plat>/ or on PATH; the lite
# tarball ships neither, so put the bootstrapped one where gclient would.
case "$TARGET" in
  linux) gn_plat=linux64 ;;
  mac)   gn_plat=mac ;;
  *)     gn_plat="" ;;
esac
if [ -n "$gn_plat" ] && [ ! -x "buildtools/$gn_plat/gn" ]; then
  mkdir -p "buildtools/$gn_plat"; cp out/Default/gn "buildtools/$gn_plat/gn"
fi
out/Default/gn gen out/Default --fail-on-unused-args
unset CC CXX AR LDFLAGS
[ "${GN_ONLY:-0}" = 1 ] && { echo "OK: gn gen done (GN_ONLY=1, not building)"; exit 0; }

# bindgen (Rust FFI generation) refuses to run with TARGET in the environment ("saw TARGET
# in environment, remove to avoid bindgen failures"). Keep the shell variables, drop the export.
export -n TARGET TARGET_CPU 2>/dev/null || true

# NINJA_TARGETS overrides what is built (CI uses it to compile only the patched files,
# e.g. NINJA_TARGETS="../../components/ungoogled/persona_profile.cc^ ...").
if [ -n "${NINJA_TARGETS:-}" ]; then
  # shellcheck disable=SC2086
  exec ninja -C out/Default ${NINJA_JOBS:+-j "$NINJA_JOBS"} ${NINJA_FLAGS:-} $NINJA_TARGETS
fi
case "$TARGET" in
  linux) exec ninja -C out/Default chrome chrome_sandbox chrome_crashpad_handler ;;
  *)     exec ninja -C out/Default chrome ;;
esac
