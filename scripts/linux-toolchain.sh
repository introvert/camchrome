#!/usr/bin/env bash
# Sourced by 00-fetch-source.sh for TARGET=linux. The lite tarball leaves out the
# prebuilt tools gclient would normally sync from DEPS; this fetches the ones a Linux
# build of chrome needs, each pinned by the tree itself (DEPS, update.py, or a version
# string in a DEPS file) and checked where the source gives a hash.
#
# Verified on Chromium 157.0.8103.78 (Ubuntu 24.04 x64): after this, gn gen succeeds
# (36,541 targets) and the patched translation units compile. Host needs: python3,
# npm (for tsgo/esbuild), go, gperf, curl.

linux_toolchain() {
  local cpu="${TARGET_CPU:-x64}"
  local sysroot_arch="amd64"; [ "$cpu" = arm64 ] && sysroot_arch="arm64"

  # Prebuilt clang + Rust that this Chromium revision pins, and the Debian sysroot.
  [ -x "$SRC/third_party/llvm-build/Release+Asserts/bin/clang" ] ||
    python3 "$SRC/tools/clang/scripts/update.py"
  [ -x "$SRC/third_party/rust-toolchain/bin/rustc" ] ||
    python3 "$SRC/tools/rust/update_rust.py"
  # The host sysroot is always amd64 (build tools run on the host); add arm64 to cross.
  python3 "$SRC/build/linux/sysroot_scripts/install-sysroot.py" --arch=amd64
  [ "$sysroot_arch" = arm64 ] &&
    python3 "$SRC/build/linux/sysroot_scripts/install-sysroot.py" --arch=arm64

  # GCS prebuilts pinned (object + sha256) in DEPS: libclang for V8's metagen step and
  # the exact node version third_party/node/check_version.py insists on.
  [ -f "$SRC/third_party/llvm-libclang/bindings/python/clang/cindex.py" ] ||
    python3 "$REPO/scripts/fetch-gcs-dep.py" "$SRC" src/third_party/llvm-libclang --match Linux_x64/
  [ -x "$SRC/third_party/node/linux/node-linux-x64/bin/node" ] ||
    python3 "$REPO/scripts/fetch-gcs-dep.py" "$SRC" src/third_party/node/linux
  # clang-format: Crubit's generated bindings (rs_std/rs_alloc.h, ...) are formatted with it
  # during the full build, so the browser does not link without it.
  [ -x "$SRC/buildtools/linux64-format/clang-format" ] ||
    python3 "$REPO/scripts/fetch-gcs-dep.py" "$SRC" src/buildtools/linux64-format

  # TypeScript (tsgo) for the WebUI/devtools build. DEPS pins the CIPD package
  # 'version:2@<v>'; the same release is on npm as @typescript/typescript-linux-x64.
  local ts_dir="$SRC/third_party/typescript/linux-amd64/src"
  if [ ! -x "$ts_dir/lib/tsc" ]; then
    local ts_ver
    ts_ver="$(grep -A6 "'src/third_party/typescript/linux-amd64/src'" "$SRC/DEPS" |
              sed -n "s/.*'version': 'version:[0-9]*@\([0-9.]*\)'.*/\1/p" | head -1)"
    local tmp; tmp="$(mktemp -d)"
    (cd "$tmp" && npm pack --silent "@typescript/typescript-linux-x64@$ts_ver" >/dev/null &&
       tar -xzf ./*.tgz)
    mkdir -p "$ts_dir"; cp -R "$tmp/package/." "$ts_dir/"; rm -rf "$tmp"
    # Chromium's own lib.dom.d.ts modifications (the Windows overlay carries them as
    # windows-fix-typescript-lib-dom.patch for its copy).
    local p="$WORK/$OVERLAY/patches/ungoogled-chromium/windows/windows-fix-typescript-lib-dom.patch"
    [ -f "$p" ] && sed 's|windows-amd64|linux-amd64|g' "$p" |
      patch -p1 -s --forward --no-backup-if-mismatch -d "$SRC" || true
  fi

  # esbuild for devtools-frontend, pinned in its DEPS as <version>.chromium.N.
  local esb="$SRC/third_party/devtools-frontend/src/third_party/esbuild/esbuild"
  if [ ! -x "$esb" ]; then
    local esb_ver arch=x64
    esb_ver="$(grep -A4 "tools/esbuild" "$SRC/third_party/devtools-frontend/src/DEPS" |
               sed -n "s/.*'version': 'version:[0-9]*@\([0-9.]*[0-9]\)\.chromium.*/\1/p" | head -1)"
    [ "$(uname -m)" = aarch64 ] && arch=arm64
    local tmp; tmp="$(mktemp -d)"
    (cd "$tmp" && npm pack --silent "@esbuild/linux-$arch@$esb_ver" >/dev/null && tar -xzf ./*.tgz)
    mkdir -p "$(dirname "$esb")"; cp "$tmp/package/bin/esbuild" "$esb"; rm -rf "$tmp"
  fi

  # Host tools Chromium expects at CIPD paths: link the system ones.
  link_tool() { # path-in-tree command
    mkdir -p "$SRC/$(dirname "$1")"
    command -v "$2" >/dev/null || { echo "missing host tool: $2" >&2; return 1; }
    ln -sf "$(command -v "$2")" "$SRC/$1"
  }
  # Dawn's generate-sources step runs `go run`; Chromium's CIPD Go is not in the tarball.
  link_tool third_party/dawn/tools/golang/linux-amd64/bin/go go
  link_tool third_party/cpython3/host/bin/python3 python3
  link_tool third_party/gperf/cipd/bin/gperf gperf
}
