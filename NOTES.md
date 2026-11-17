# Handoff notes

Background and open work for whoever picks this up. As of 2026-11-17.

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
| [clearcotelabs/clearcote-browser](https://github.com/clearcotelabs/clearcote-browser) | 62 in `patches/series` (68 files incl. 3 macOS-only) | 151.0.7922.108 | BSD-3 | **Vendored and rebased here.** Most complete public set; active. Has a paid tier with extra private patches that are not in the public repo. |
| [adryfish/fingerprint-chromium](https://github.com/adryfish/fingerprint-chromium) | 18 (`patches/extra/fingerprint/`) | 148.0.7778.215 | BSD-3 | The project Clearcote builds on. Useful as a second reference. |
| [pocchian/fingerprint-chromium-arm-mac](https://github.com/pocchian/fingerprint-chromium-arm-mac) | — | — | — | macOS arm64 fork; not inspected. |

Vendored from Clearcote commit `ac40bdcaeba73d71984c83656a899246a25cb5ba`. `patches/` started as
a byte-for-byte copy of its `patches/` directory; every patch has since been rebased to 156 and
three were added (987–989), so 66 patches are in `patches/series` now.

## Upstream versions (checked 2026-11-17)

- ungoogled-chromium: `157.0.8103.78-1`, Chromium `157.0.8103.78`
- ungoogled-chromium-windows: `157.0.8103.78-1.1`
- ungoogled-chromium-macos: `157.0.8103.78-1.1` **(new)**; this tree is verified on its previous
  tag, `156.0.8078.79-1.1`. The 157 macOS overlay appeared after the 156 rebase was finished.
- Source tarball: `chromium-156.0.8078.79-lite.tar.xz`, 1.9 GB, from
  `commondatastorage.googleapis.com/chromium-browser-official`

## How the pieces fit

1. ungoogled-chromium base patches (de-Google).
2. ungoogled-chromium-windows overlay. It is applied for **Linux too**: Clearcote's series was
   written against that tree, and `905-rc-invoked-guard` needs the overlay's
   `chrome_command_ids.h`. Its Windows-only files don't affect a Linux build.
3. `patches/series`, applied in order with `patch -p1`.

Clearcote's scripts skip ungoogled's domain-substitution step; so do ours.

## Build machine

macOS 26.1 arm64, 10 cores, 24 GB RAM, Xcode 17. The tree is built without LTO/PGO/PCH
(`config/args.mac.gn`), which is why it links on 24 GB. A full build is ~4 hours of ninja
wall time on this machine; the linked app is `build/src/out/Default/Chromium.app`.

Chromium 156's build scripts need Python >= 3.10. macOS's `/usr/bin/python3` shim fails inside
a non-interactive build (it asks Xcode to install command line tools), so
`scripts/02-build.sh` prepends a Homebrew Python as `python3`. The same PATH is required when
re-running ninja by hand.

## What has been verified

- `00-fetch-source.sh` fetches and unpacks the Chromium 156 lite tarball plus the overlay's
  LLVM/Rust/node toolchains and sets up the rust/llvm/go symlink layout.
- `01-apply-patches.sh --check`: **all 64 series patches that apply to macOS (900/905 are
  Windows cross-build mechanics) apply cleanly**, in order.
- A fresh apply of the full series onto the ungoogled base was compared file-by-file with the
  built tree: it reproduces it exactly (only the two Windows-only files differ, as expected
  because 900/905 are skipped on macOS).
- `gn gen out/Default --fail-on-unused-args` succeeds (35,838 targets) and the full
  `chrome` target compiles and links to `Chromium.app`.
- **Stealth audit: 100/100, 237/237 scored checks agree, 0 findings**, on macOS arm64,
  using `tools/stealth_audit.py --seed testseed --interact`. The 18 input checks are scored
  because the harness drives its five optional steps with trusted CDP input. Without
  `--interact` the engine-level result is 219/219 scored (the audit reports the 18 input
  checks as not attempted, never as passed).
- The audit's findings before patches 987–989 (recorded for reference): 16 findings — a 151
  version claim on a 156 engine, a Windows Direct3D GPU under a macOS claim, malformed ANGLE
  device ids, D3D11 caps on a Metal backend, `navigator.cpuPerformance` derived from the host
  CPU model, a DPR/layout mismatch, empty voices, wire-vs-JS downlink, a readPixels sentinel
  violation, a PBO/readPixels mismatch and `getParameter` timing. All are fixed or made
  coherent by 987–989 / the macOS persona.

## Open work, in order

1. **Rebase to 157** (`UGM_TAG=157.0.8103.78-1.1`, `UG_TAG`/`UGW_TAG` already 157). The
   macOS 156 build is the reference for how each conflict was resolved. After it builds,
   update `PATCHES_BASE=157.0.8103.78` and re-run the audit.
2. Linux/Windows: the series is verified on macOS only. Apply the same 156→157 rebase on
   Linux (900/905 included there) and run the audit on both.
3. Chrome-branding checks (`widevine-vs-chrome-branding`, `google-voices-…`,
   `builtin-ai-…`, `licensed-codecs-…`): an ungoogled build has no Google services. Claim
   the Chromium brand (as now) or fill Widevine in at runtime; built-in AI has no fix.
4. Cross-OS personas: `cpu-arch-nan-*` cannot be patched (the persona's CPU arch must match
   the host's). Text-scaler/hyphenation/rare-font checks follow the host font stack. A
   Windows persona on a Mac needs Clearcote's font bundles, not vendored here.
5. Stock Playwright/Puppeteer still fails the driver-side checks (`sourceurl-leak`,
   `exposed-binding-leaks`, `main-world-execution`, …). Use
   [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright) or
   [rebrowser-patches](https://github.com/rebrowser/rebrowser-patches); the engine cannot fix
   a driver that injects itself into the page. The included audit harness uses raw CDP and
   adds nothing to the page.
6. Windows cross-build: `config/args.win.gn` needs Clearcote's xwin setup; not ported here.
7. CI: no workflow yet. `tools/stealth_audit.py --signals` is a cheap offline smoke test; the
   full audit needs network and macOS/Linux GPU access.

## Committing the Chromium source

The plan is to put the source in this repo. Before doing that, note:

- The unpacked tree is ~20 GB and tens of thousands of files. GitHub blocks files over 100 MB
  and gets slow or refuses pushes for repos that size.
- Chromium itself is BSD-3, so redistributing it is allowed. Some `third_party/` code is under
  other licenses, which ship with it.
- Alternatives: keep fetching the pinned tarball (what the scripts do now; reproducible from
  the ungoogled hashes), or keep the patched tree in a separate repo/branch, or store the
  tarball as a release asset.
