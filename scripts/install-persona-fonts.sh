#!/usr/bin/env bash
# Install the open fonts a cross-OS persona renders through on a Linux host.
#
# A Windows, macOS or Android persona on Linux answers font probes with its own OS's families
# (040, 971, 992) and renders each through a metric-compatible clone (font_cache.cc
# MetricCompatibleSubstitute): Arimo/Tinos/Cousine for Arial/Times New Roman/Courier New,
# Carlito/Caladea for Calibri/Cambria, Selawik for Segoe UI, WenQuanYi and IPA for the CJK
# faces, Noto for the complex-script UI fonts, and so on. Without the clones a family the persona
# lists falls back to a generic, which pages can measure.
#
#   scripts/install-persona-fonts.sh --system      # apt packages + extras (needs root)
#   scripts/install-persona-fonts.sh --user        # no root: ~/.local/share/fonts/camchrome
#   scripts/install-persona-fonts.sh --dir DIR     # scoped: DIR + DIR/fonts.conf, then run the
#                                                  # browser with FONTCONFIG_FILE=DIR/fonts.conf
#
# Ubuntu/Debian packages, plus four OFL families Ubuntu does not package (Selawik from
# microsoft/Selawik 1.01; Anton, Oswald, Gelasio from google/fonts), pinned by SHA-256.
set -euo pipefail

PACKAGES=(fonts-croscore fonts-crosextra-carlito fonts-crosextra-caladea fonts-wqy-zenhei
          fonts-ipafont-gothic fonts-noto-core fonts-noto-extra fonts-noto-color-emoji
          fonts-comic-neue fonts-lato fonts-inconsolata fonts-liberation fonts-dejavu-core)
GOOGLE_FONTS=cc5661d43cd4f9aad5567033a8e15ca28dc35c86
EXTRAS=(
  "https://raw.githubusercontent.com/google/fonts/$GOOGLE_FONTS/ofl/anton/Anton-Regular.ttf Anton-Regular.ttf a4ba3a92350ebb031da0cb47630ac49eb265082ca1bc0450442f4a83ab947cab"
  "https://raw.githubusercontent.com/google/fonts/$GOOGLE_FONTS/ofl/oswald/Oswald%5Bwght%5D.ttf Oswald-wght.ttf 5b38c246e255a12f5712d640d56bcced0472466fc68983d2d0410ec0457c2817"
  "https://raw.githubusercontent.com/google/fonts/$GOOGLE_FONTS/ofl/gelasio/Gelasio%5Bwght%5D.ttf Gelasio-wght.ttf 4daecea457258c9ebeb8bc99ed3fd24353618bfad3ea4b93fa0b5d0468fc04e4"
  "https://raw.githubusercontent.com/google/fonts/$GOOGLE_FONTS/ofl/gelasio/Gelasio-Italic%5Bwght%5D.ttf Gelasio-Italic-wght.ttf 52559e845a4d33514e5f93bb9ae7dbeae1894a53f2c565a15f18af40cd337c09"
)
SELAWIK="https://github.com/microsoft/Selawik/releases/download/1.01/Selawik_Release.zip"
SELAWIK_SHA=3f62c51e05e3b5a1e6241cf92a371f0be2ea1183aa87b30718bbd40832a8d423

mode="${1:-}"
case "$mode" in
  --system) dest=/usr/local/share/fonts/camchrome ;;
  --user)   dest="${XDG_DATA_HOME:-$HOME/.local/share}/fonts/camchrome" ;;
  --dir)    dest="${2:?--dir needs a directory}" ;;
  *) sed -n '2,20p' "$0"; exit 2 ;;
esac

fetch_extras() { # dir
  local dir="$1" tmp
  mkdir -p "$dir"
  for e in "${EXTRAS[@]}"; do
    read -r url name sha <<< "$e"
    curl -fsSL -o "$dir/$name" "$url"
    echo "$sha  $dir/$name" | sha256sum -c --quiet -
  done
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/selawik.zip" "$SELAWIK"
  echo "$SELAWIK_SHA  $tmp/selawik.zip" | sha256sum -c --quiet -
  python3 -c 'import sys, zipfile; z = zipfile.ZipFile(sys.argv[1]); [z.extract(n, sys.argv[2]) for n in z.namelist() if n.lower().endswith(".ttf")]' \
    "$tmp/selawik.zip" "$dir"
}

if [ "$mode" = --system ]; then
  apt-get install -y "${PACKAGES[@]}"
  fetch_extras "$dest"
  fc-cache -f >/dev/null
else
  # No root: unpack the packages' files instead of installing them.
  tmp="$(mktemp -d)"
  (cd "$tmp" && apt-get download "${PACKAGES[@]}" >/dev/null)
  mkdir -p "$dest/pkg"
  for deb in "$tmp"/*.deb; do dpkg-deb -x "$deb" "$dest/pkg"; done
  fetch_extras "$dest/extra"
  if [ "$mode" = --dir ]; then
    cat > "$dest/fonts.conf" <<EOF
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>
  <dir>$(cd "$dest" && pwd)/pkg/usr/share/fonts</dir>
  <dir>$(cd "$dest" && pwd)/extra</dir>
  <cachedir>$(cd "$dest" && pwd)/cache</cachedir>
</fontconfig>
EOF
    FONTCONFIG_FILE="$dest/fonts.conf" fc-cache >/dev/null 2>&1 || true
    echo "run the browser with FONTCONFIG_FILE=$dest/fonts.conf"
  else
    fc-cache >/dev/null 2>&1 || true
  fi
fi
echo "persona fonts installed in $dest"
