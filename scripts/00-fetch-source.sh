#!/usr/bin/env bash
# 00 — fetch the pinned ungoogled-chromium tooling and the Chromium source tarball it names,
# unpack it into build/src, and (for Linux/Windows) prune prebuilt binaries.
# ~2 GB download, ~20 GB unpacked. Resumable: each stage leaves a marker under build/.
source "$(dirname "$0")/common.sh"
mkdir -p "$WORK/download_cache"

if [ "$TARGET" = mac ]; then
  clone_at "$OVERLAY" "$OVERLAY_TAG"
else
  clone_at ungoogled-chromium "$UG_TAG"
  clone_at "$OVERLAY" "$OVERLAY_TAG"
fi

# --- Chromium source tarball ---
if [ ! -e "$SRC/.camchrome-source-fetched" ]; then
  if [ ! -d "$SRC" ] || [ ! -f "$SRC/BUILD.gn" ]; then
    python3 "$UG/utils/downloads.py" retrieve -c "$WORK/download_cache" -i "$UG/downloads.ini"
    python3 "$UG/utils/downloads.py" unpack -c "$WORK/download_cache" -i "$UG/downloads.ini" -- "$SRC"
  fi
  # ungoogled's Linux/Windows flow prunes the lite tarball. The macOS overlay skips
  # this in lite-tarball mode (the tarball is already pruned of what its
  # pruning.list targets, so prune_binaries.py would only report 14k already-absent
  # files and exit 1).
  if [ "$TARGET" != mac ]; then
    python3 "$UG/utils/prune_binaries.py" "$SRC" "$UG/pruning.list" >/dev/null 2>&1 || true
  fi
  touch "$SRC/.camchrome-source-fetched"
fi

# --- macOS prebuilt LLVM + Rust toolchains + symlink layout ---
# Faithful port of ungoogled-chromium-macos's
# retrieve_and_unpack_resource.py::_retrieve_platform_specific.
if [ "$TARGET" = mac ]; then
  host_arch="arm64"; [ "$(uname -m)" = x86_64 ] && host_arch="x86-64"

  target_cpu="$(grep -E '^target_cpu *= *"' "$REPO/config/args.mac.gn" | sed 's/^target_cpu *= *"\([^"]*\)".*/\1/' | head -1)"
  [ -z "$target_cpu" ] && { [ "$host_arch" = arm64 ] && target_cpu=arm64 || target_cpu=x64; }

  # Clean partial/previous toolchain state before unpacking.
  rm -rf "$SRC/third_party/llvm-build/Release+Asserts" \
         "$SRC/third_party/rust-toolchain" \
         "$SRC/third_party/node/mac" \
         "$SRC/third_party/node/mac_arm64"
  mkdir -p "$SRC/third_party/llvm-build/Release+Asserts"

  if [ "$host_arch" = arm64 ]; then
    node_dir="$SRC/third_party/node/mac_arm64/node-darwin-arm64"
  else
    node_dir="$SRC/third_party/node/mac/node-darwin-x64"
  fi
  mkdir -p "$node_dir"

  unpack_ini() { # ini-name
    local ini="$1"
    python3 "$UG/utils/downloads.py" retrieve -c "$WORK/download_cache" -i "$WORK/$OVERLAY/$ini"
    # ungoogled's unpack requires each output_path to already exist.
    grep '^output_path' "$WORK/$OVERLAY/$ini" | sed 's/^output_path *= *//' | \
      while IFS= read -r out; do mkdir -p "$SRC/$out"; done
    python3 "$UG/utils/downloads.py" unpack -c "$WORK/download_cache" -i "$WORK/$OVERLAY/$ini" -- "$SRC"
  }

  unpack_ini "downloads-$host_arch.ini"

  # The rust-std for the *other* target is only needed when cross-compiling.
  if [ "$target_cpu" != "$host_arch" ]; then
    other="x86-64"; [ "$target_cpu" = arm64 ] && other="arm64"
    unpack_ini "downloads-$other-rustlib.ini"
  fi

  # Rust symlink layout Chromium's rust bootstrap expects.
  rust_name="$([ "$host_arch" = arm64 ] && echo aarch64-apple-darwin || echo x86_64-apple-darwin)"
  rust_dir="$SRC/third_party/rust-toolchain"

  # Chromium 157's Crubit needs the rustc-dev component, whose archive has a
  # two-component prefix (rustc-dev-nightly-*/rustc-dev). downloads.py's generic
  # unpack leaves its sysroot lib empty here, so re-extract it explicitly.
  rustcdev_tar="$(awk '/^\[rustc-dev\]/{f=1} f&&/^download_filename/{sub(/^download_filename *= */,""); print; exit}' "$WORK/$OVERLAY/downloads-$host_arch.ini")"
  if [ -n "$rustcdev_tar" ] && [ -f "$WORK/download_cache/$rustcdev_tar" ]; then
    rustcdev_tmp="$(mktemp -d)"
    tar -xJf "$WORK/download_cache/$rustcdev_tar" -C "$rustcdev_tmp"
    rm -rf "$rust_dir/rustc-dev"
    mkdir -p "$rust_dir/rustc-dev"
    cp -R "$rustcdev_tmp"/*/rustc-dev/. "$rust_dir/rustc-dev/"
    rm -rf "$rustcdev_tmp"
  fi
  rust_bin_dir="$rust_dir/bin"
  rust_lib_dir="$rust_dir/rust-std-$rust_name/lib/rustlib/$rust_name/lib"
  rustc_lib_dir="$rust_dir/rustc/lib/rustlib/$rust_name/lib"
  mkdir -p "$rust_bin_dir" "$rust_dir/lib"
  # Relative links: the tree must survive `mv build build-...`.
  ln -sf "../rustc/bin/rustc" "$rust_bin_dir/rustc"
  ln -sf "../cargo/bin/cargo" "$rust_bin_dir/cargo"
  ln -sf "../rustfmt-preview/bin/rustfmt" "$rust_bin_dir/rustfmt"
  ln -sfn "../../../../rust-std-$rust_name/lib/rustlib/$rust_name/lib" "$rustc_lib_dir"
  ln -sfn "../rustc/lib" "$rust_dir/rustfmt-preview/lib"

  # Chromium 157: Crubit builds cc_bindings_from_rs against rustc_private, so the
  # rustc-dev crates and libLLVM must be moved into the host sysroot and the
  # toolchain root must be usable as a sysroot. The 156 overlay has neither
  # directory, so both steps are conditional.
  rm -rf "$rust_dir/lib/rustlib"
  ln -sfn "../rustc/lib/rustlib" "$rust_dir/lib/rustlib"
  if [ -d "$rust_dir/rustc-dev/lib/rustlib/$rust_name/lib" ]; then
    for f in "$rust_dir/rustc-dev/lib/rustlib/$rust_name/lib"/*; do
      [ -e "$f" ] && mv "$f" "$rust_lib_dir/"
    done
  fi
  llvm_dylib="$rust_dir/llvm-tools-preview/lib/rustlib/$rust_name/lib/libLLVM.dylib"
  [ -f "$llvm_dylib" ] && mv "$llvm_dylib" "$rust_lib_dir/libLLVM.dylib"

  # LLVM ships install_name_tool as llvm-install-name-tool.
  ln -sf llvm-install-name-tool \
    "$SRC/third_party/llvm-build/Release+Asserts/bin/install_name_tool"

  # Dawn's build needs a Go toolchain; link the system Go into the tree.
  go_bin="$(command -v go || true)"
  if [ -n "$go_bin" ]; then
    dawn_go="mac-arm64"; [ "$host_arch" = x86-64 ] && dawn_go="mac-amd64"
    mkdir -p "$SRC/third_party/dawn/tools/golang/$dawn_go/bin"
    ln -sfn "$go_bin" "$SRC/third_party/dawn/tools/golang/$dawn_go/bin/go"
  fi
fi

echo "OK: Chromium $(cat "$UG/chromium_version.txt") source at $SRC"
echo "next -> scripts/01-apply-patches.sh"
