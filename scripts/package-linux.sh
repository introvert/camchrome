#!/usr/bin/env bash
# Package a Linux build into camchrome-<version>-linux-<cpu>.tar.xz: the files a Chromium Linux
# archive ships (the lite source tarball drops chrome/tools/build/linux/FILES.cfg, so the list is
# here). Runs on the build output directory; optional files are included when the build made them.
#
#   scripts/package-linux.sh build/src/out/Default x64 [outdir]
set -euo pipefail
OUT="$1"
CPU="$2"
DEST="${3:-.}"
[ -x "$OUT/chrome" ] || { echo "no chrome in $OUT"; exit 1; }

version="$(sed -n 's/^MAJOR=//p;s/^MINOR=//p;s/^BUILD=//p;s/^PATCH=//p' "$OUT/../../chrome/VERSION" | paste -sd.)"
name="camchrome-$version-linux-$CPU"
stage="$(mktemp -d)/$name"
mkdir -p "$stage/locales"

required=(chrome chrome_crashpad_handler chrome_100_percent.pak chrome_200_percent.pak
          resources.pak icudtl.dat v8_context_snapshot.bin snapshot_blob.bin
          libEGL.so libGLESv2.so libvk_swiftshader.so libvulkan.so.1)
optional=(chrome-wrapper headless_command_resources.pak vk_swiftshader_icd.json libqt5_shim.so
          libqt6_shim.so product_logo_48.png)
for f in "${required[@]}"; do cp -p "$OUT/$f" "$stage/"; done
for f in "${optional[@]}"; do [ -e "$OUT/$f" ] && cp -p "$OUT/$f" "$stage/"; done
# The setuid sandbox helper is called chrome-sandbox next to the binary.
cp -p "$OUT/chrome_sandbox" "$stage/chrome-sandbox"
cp -p "$OUT"/locales/*.pak "$stage/locales/"

cat > "$stage/README.txt" <<EOF
camchrome $version for Linux $CPU (ungoogled-chromium base + camchrome fingerprint patches).

Run:   ./chrome --fingerprint=<seed>
Sandbox: Chrome uses the user-namespace sandbox where the kernel allows it. Otherwise make the
setuid helper usable:  sudo chown root:root chrome-sandbox && sudo chmod 4755 chrome-sandbox
(or run with --no-sandbox, which weakens isolation).
Docs: https://github.com/introvert/camchrome
EOF

tar -C "$(dirname "$stage")" -cJf "$DEST/$name.tar.xz" "$name"
echo "$DEST/$name.tar.xz"
