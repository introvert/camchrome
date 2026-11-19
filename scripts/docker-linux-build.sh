#!/usr/bin/env bash
# Build camchrome for Linux inside an x86-64 Ubuntu 24.04 container. Chromium's Linux host
# toolchain is x86-64 only, so on Apple Silicon this runs under Docker Desktop's Rosetta
# emulation. The tree and the build live in a Docker volume (not the bind mount, which is far
# slower for 400k files); the repository itself is bind-mounted read-only.
#
#   scripts/docker-linux-build.sh x64            # or arm64 (cross-compiled from the x64 host)
#   docker logs -f camchrome-linux-x64           # follow
#
# Output: /work/<cpu>/src/out/Default in volume camchrome-linux-<cpu>. ARGS_FILE defaults to the
# low-memory config; pass ARGS_FILE=config/args.linux.gn on a 64 GB machine for the official build.
set -euo pipefail
CPU="${1:-x64}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
NAME="${NAME:-camchrome-linux-$CPU}"   # container and volume name; set it to keep a second tree (e.g. a rebase)
ARGS_FILE="${ARGS_FILE:-config/args.linux.lowmem.gn}"
# IMAGE: an ubuntu:24.04 derivative that already has the build packages (e.g. one saved with
# `docker commit` from an earlier run) skips the apt step, which is slow on a slow mirror.
IMAGE="${IMAGE:-ubuntu:24.04}"

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --platform linux/amd64 \
  -v "$NAME:/work" -v "$REPO:/repo:ro" \
  -e TARGET=linux -e TARGET_CPU="$CPU" -e WORK="/work/$CPU" -e ARGS_FILE="/repo/$ARGS_FILE" \
  -e NINJA_TARGETS="${NINJA_TARGETS:-chrome chrome_sandbox chrome_crashpad_handler socks5_udp_relay_selftest components/ungoogled:persona_validator_cli}" \
  "$IMAGE" bash -c '
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if ! command -v ninja >/dev/null || ! command -v rsync >/dev/null || ! command -v gperf >/dev/null; then
apt-get update -q
apt-get install -y -q python3 python3-venv npm gperf curl git xz-utils ninja-build \
  pkg-config file lsb-release ca-certificates patch rsync >/dev/null
fi
# Dawn generate-sources needs Go >= 1.26 (its go.mod); Ubuntu 24.04 ships 1.22 and Go cannot
# fetch the toolchain itself here. Pinned official release, checked by sha256.
GO_TGZ=go1.26.11.linux-amd64.tar.gz
GO_SHA=9849f4e04ee35303714e34fbc3ac8ab44ea3e448a95aa182f91e3b9b79c710ec
if [ ! -x /usr/local/go/bin/go ]; then
  curl -fsSL "https://go.dev/dl/$GO_TGZ" -o "/tmp/$GO_TGZ"
  echo "$GO_SHA  /tmp/$GO_TGZ" | sha256sum -c -
  tar -C /usr/local -xzf "/tmp/$GO_TGZ"
fi
export PATH="/usr/local/go/bin:$PATH" GOTOOLCHAIN=local
# The scripts write next to themselves only under $WORK; run them from a writable copy so
# patches/, config/ and tools/ are read from the mounted repo state at start.
rsync -a --delete --exclude build --exclude .git /repo/ /work/repo/
cd /work/repo
echo "== fetch $(date -u)"
scripts/00-fetch-source.sh   # resumable; re-run so toolchain additions reach an existing tree
echo "== patch $(date -u)"
[ -e "$WORK/src/.camchrome-patched" ] || scripts/01-apply-patches.sh
echo "== build $(date -u)"
scripts/02-build.sh
echo "== done $(date -u)"
'
echo "started $NAME; follow with: docker logs -f $NAME"
