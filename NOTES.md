# Handoff notes

Background and open work for whoever picks this up on the build machine. As of 2026-11-16.

## Why this repo exists

The starting point was [CloakBrowser](https://github.com/CloakHQ/CloakBrowser). Its public repo
holds only the Python/JS/.NET wrappers. The browser is a closed binary, and its
`BINARY-LICENSE.md` claims the build config and C++ patches as CloakHQ property. Its latest
build, `156.0.8078.47.1`, is only downloadable with a license key. CloakBrowser can't be rebuilt
or upgraded from source, and its patches can't be reused, so this repo starts from open-source
parts instead.

## Patch sources considered

| Repo | Patches | Chromium | License | Notes |
|---|---|---|---|---|
| [clearcotelabs/clearcote-browser](https://github.com/clearcotelabs/clearcote-browser) | 62 in `patches/series` (68 files incl. 3 macOS-only) | 151.0.7922.108 | BSD-3 | **Vendored here.** Most complete, active (last commit 2026-11-13). Has a paid tier with extra private patches that are not in the public repo. Linux + Windows builds; macOS only for 150. |
| [adryfish/fingerprint-chromium](https://github.com/adryfish/fingerprint-chromium) | 18 (`patches/extra/fingerprint/` on version branches) | 148.0.7778.215 (newest branch) | BSD-3 | The project Clearcote builds on. Full ungoogled fork per version branch. Useful as a second reference when a Clearcote patch is hard to rebase. |
| [pocchian/fingerprint-chromium-arm-mac](https://github.com/pocchian/fingerprint-chromium-arm-mac) | — | — | — | macOS arm64 fork of fingerprint-chromium; not inspected. Possible reference for macOS. |

Vendored from Clearcote commit `ac40bdcaeba73d71984c83656a899246a25cb5ba`. `patches/` is a
byte-for-byte copy of its `patches/` directory.

## Upstream versions (checked against GitHub tags)

- ungoogled-chromium: `157.0.8103.78-1` (newest), Chromium `157.0.8103.78`
- ungoogled-chromium-windows: `157.0.8103.78-1.1`
- ungoogled-chromium-macos: `156.0.8078.79-1.1` (newest; no 157 yet)
- Source tarball: `chromium-157.0.8103.78-lite.tar.xz`, 2.08 GB, from
  `commondatastorage.googleapis.com/chromium-browser-official` (confirmed reachable)

## How the pieces fit

1. ungoogled-chromium base patches (de-Google).
2. ungoogled-chromium-windows overlay. It is applied for **Linux too**: Clearcote's series was
   written against that tree, and `905-rc-invoked-guard` needs the overlay's
   `chrome_command_ids.h`. Its Windows-only files don't affect a Linux build.
3. `patches/series`, applied in order with `patch -p1`.

Clearcote's scripts skip ungoogled's domain-substitution step; so do ours.

## Build machine requirements

- Linux x64 recommended (Clearcote's tested path). ~32 GB RAM: the official ThinLTO link
  peaks around 31 GB. ~120 GB free disk. Many cores. Expect several hours.
- Chromium's Linux build deps (`build/install-build-deps.sh` in the source tree).
- `git python3 ninja xz curl patch`.

## What has and hasn't been verified

- Verified on macOS (arm64, 24 GB): `00-fetch-source.sh` fetches and unpacks the Chromium 156
  lite tarball plus the overlay's LLVM/Rust/node toolchains and sets up the rust/llvm/go
  symlink layout; `01-apply-patches.sh --check` lists the series; **all 60 series patches that
  apply to macOS have been rebased and apply cleanly** (900/905 are Windows cross-build and are
  skipped for macOS). Added `986-canvas-alpha-and-grey-coherence` (canvas-alpha-zero-reads-black
  + canvas-grey-stays-grey).
- Verified: `gn gen out/Default --fail-on-unused-args` succeeds (35 838 targets). A
  non-official, no-LTO/no-PGO/no-PCH `chrome` build compiles cleanly (see `config/args.mac.gn`;
  flip back to official + ThinLTO + PGO on a 32 GB machine).
- **Not yet verified at this point:** the full link to a `chrome` binary and a runtime
  fingerprint-audit pass, plus the Linux/Windows (157) rebase.
  `config/args.win.gn` needs Clearcote's xwin cross-compile setup (not ported).

## What to cover

See `COVERAGE.md`: the audit we test against, which patch covers which check group, the gaps,
and other repos to pull from (patchright, rebrowser-patches, ghost-cursor, Brave, browserforge).

## Open work, in order

1. macOS: finish the `chrome` link, then smoke-test `build/src/out/Default/chrome` against the
   Clearcote audit and record failures in `COVERAGE.md`.
2. Linux/Windows: rebase the series to 157 (the macOS rebase to 156 is the reference for
   how each conflict was resolved) and set `PATCHES_BASE=157.0.8103.78`.
3. Close the remaining gaps in `COVERAGE.md`: driver (patchright), Chrome-branding checks,
   input coalescing/raw-update engine patches.
4. Vendor Clearcote's `assets/fonts/` if we spoof another OS's fonts.
5. Windows cross-build, CI.

## Committing the Chromium source

The plan is to put the source in this repo. Before doing that, note:

- The unpacked tree is ~20 GB and tens of thousands of files. GitHub blocks files over 100 MB
  and gets slow or refuses pushes for repos that size.
- Chromium itself is BSD-3, so redistributing it is allowed. Some `third_party/` code is under
  other licenses, which ship with it.
- Alternatives: keep fetching the pinned tarball (what the scripts do now; reproducible from the
  ungoogled hashes), or keep the patched tree in a separate repo/branch, or store the tarball
  as a release asset.
