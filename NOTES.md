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
| [clearcotelabs/clearcote-browser](https://github.com/clearcotelabs/clearcote-browser) | 62 in `patches/series` (68 files incl. 3 macOS-only) | 151.0.7922.108 | BSD-3 | **Vendored and rebased here.** Most complete public set; active. The paid tier has extra private patches that are not in the public repo. |
| [adryfish/fingerprint-chromium](https://github.com/adryfish/fingerprint-chromium) | 18 (`patches/extra/fingerprint/`) | 148.0.7778.215 | BSD-3 | The project Clearcote builds on. Useful as a second reference. |
| [pocchian/fingerprint-chromium-arm-mac](https://github.com/pocchian/fingerprint-chromium-arm-mac) | — | — | — | macOS arm64 fork; not inspected. |

Vendored from Clearcote commit `ac40bdcaeba73d71984c83656a899246a25cb5ba`. `patches/` started as
a byte-for-byte copy of its `patches/` directory; every patch has since been rebased to 157 and
patches were added (987–999), so 76 patches are in `patches/series` now.

## Upstream versions (checked 2026-11-19)

- Google Chrome stable: 158.0.8127.36 (Linux), 159.0.8154.13 (Windows, macOS), 159.0.8154.18
  (Android).
- ungoogled-chromium: newest tag `157.0.8103.78-1`; 158 is in PR #4050 (head `76f39e0`, checks
  green), which `UPSTREAM` pins. Move to the `158.0.8127.36-1` tag when it appears and check the
  diff against `76f39e0` (reviewers mentioned the link-drag patch needing an update after #4040).
- ungoogled-chromium-windows / -macos: `157.0.8103.78-1.1`, no 158 work yet. Linux uses the
  Windows overlay only for 900/905's source changes, and the 157 overlay applies to 158.
- Edge stable: 157.0.4341.60; add the Edge 158 build to 995's table when it ships.
- `tools/upstream_watch.py --no-reference` prints all of this (docs/UPSTREAM-SOURCES.md
  section 0).

## How the pieces fit

1. ungoogled-chromium base patches (de-Google).
2. ungoogled-chromium-windows overlay. It is applied for **Linux too**: Clearcote's series was
   written against that tree, and `905-rc-invoked-guard` needs the overlay's
   `chrome_command_ids.h`. Its Windows-only files don't affect a Linux build.
3. `patches/series`, applied in order with `patch -p1`.

Chromium 157's macOS overlay adds two build prerequisites:
`third_party/rust-toolchain/rustc-dev` (its crates move into the host sysroot) and Crubit's
`cc_bindings_from_rs`, built from `third_party/rust-toolchain/crubit` and installed at
`crubit/bin/`. `00-fetch-source.sh` and `02-build.sh` handle both. Dawn's `generate-sources`
runs `go build`, so `02-build.sh` also points `GOCACHE`/`GOPATH` inside `build/` (the default
`~/Library/Caches/go-build` is not writable for a sandboxed builder).

## Build machine

macOS 26.1 arm64, 10 cores, 24 GB RAM, Xcode 17. Built without LTO/PGO/PCH
(`config/args.mac.gn`), which is why it links on 24 GB. The 157 build took ~3 h 55 min of
ninja wall time (with three restarts for the rebase fixes below); the linked app is
`build/src/out/Default/Chromium.app`.

Chromium 157's build scripts need Python >= 3.10. macOS's `/usr/bin/python3` shim fails inside
a non-interactive build (it asks Xcode to install command line tools), so
`scripts/02-build.sh` prepends a Homebrew Python as `python3`. The same PATH is required when
re-running ninja by hand.

## What has been verified

- `00-fetch-source.sh` fetches and unpacks the Chromium 157 lite tarball plus the overlay's
  LLVM/node/Rust/rustc-dev/Crubit resources and sets up the rust/llvm/go symlink layout.
- `01-apply-patches.sh --check`: **all 64 series patches that apply to macOS (900/905 are
  Windows cross-build mechanics) apply cleanly**, in order.
- A fresh apply of the full series onto the ungoogled 157 base was compared file-by-file with
  the built tree: it reproduces it exactly.
- `gn gen out/Default --fail-on-unused-args` succeeds (36,260 targets) and the full `chrome`
  target compiles and links to `Chromium.app`.
- **Stealth audit: 100/100, 236/236 scored checks agree, 0 findings**, on macOS arm64 for
  seeds `testseed` and `alpha`, using
  `tools/stealth_audit.py --seed testseed --interact`. Without `--interact`:
  218/218 and the 18 input checks are reported as not attempted.
- The 156 build reached the same 100/100 (237/237) and is tagged `v156-audit100`.
- **Linux x64 (2026-11-17, Ubuntu 24.04 container):** the full series applies in order to the
  ungoogled 157 base; `gn gen --fail-on-unused-args` succeeds (36,541 targets) and every
  patched source file compiles. Getting there needed four fixes, now in the scripts: the
  Windows overlay's host-tooling patches broke gn's bootstrap and the hermetic Python on
  Linux and are skipped; `900`'s `tool_wrapper.py` hunk was re-indented for 157;
  `enable_nacl` (removed in 157) was dropped from `config/args.linux.gn`; and the prebuilts
  the lite tarball leaves out are fetched by `scripts/linux-toolchain.sh`. No Linux browser
  has been linked or audited yet. Details in `docs/BUILD-STATUS.md`.

## 156 → 157 rebase notes

The series applied almost entirely. The changes needed:

- `010`: 157's `GetUserAgentPlatform`/`GetUnifiedPlatform` and `Navigator::platform` gained
  extra context; the switch-driven platform code was re-inserted at the top of each.
- `020`: the old audio "noise" helpers were already dead code (later patches disable the
  farble); they were not added on 157.
- `060`: canvas include block changed; the `RecordBridgeOp` calls moved to the current
  `fillRect`/`strokeRect`/`clearRect` bodies.
- `092`: 157 replaced `base::i18n::SetICUDefaultLocale` with a pass-key-only
  `SetDefaultIcuLocale`. Blink's `LocaleController::instance().SetLocaleOverride()` is the
  sanctioned owner and also flushes every isolate's cached Intl locale.
- `220`: two includes moved; the offline-audio helper gates were dropped with the helpers.
- `966`: the 156 patch file had lost its `Shader.h` hunks (the declarations were only in the
  built tree). They are restored here, so the series is reproducible from scratch.
- `974`: 157 moved `version_info::GetVersionNumber` behind
  `base/version_info/version_info.h`; the include is added.
- `987–989` applied unchanged.

## Open work, in order

1. macOS: rebuild on 158 when ungoogled-chromium-macos tags 158; Windows: not started. A
   Windows persona on a Linux host keeps four text-rendering tells (FreeType scaler and
   anti-aliasing, Noto emoji flags, localized Windows CJK names); fixing them needs Windows
   fonts on the host or a Windows host.
2. Cross-OS personas: `cpu-arch-nan-*` cannot be patched (the persona's CPU arch must match
   the host's). Text-scaler/hyphenation/rare-font checks follow the host font stack. A
   Windows persona on a Mac needs Clearcote's font bundles, not vendored here.
3. Chrome-branding checks (`widevine-vs-chrome-branding`, `google-voices-…`,
   `builtin-ai-…`, `licensed-codecs-…`): an ungoogled build has no Google services. Claim
   the Chromium brand (as now) or fill Widevine in at runtime; built-in AI has no fix.
4. Stock Playwright/Puppeteer still fails driver-side checks (`sourceurl-leak`,
   `exposed-binding-leaks`, `main-world-execution`, …). Use
   [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright) or
   [rebrowser-patches](https://github.com/rebrowser/rebrowser-patches); the engine cannot fix
   a driver that injects itself into the page. The included audit harness uses raw CDP and
   adds nothing to the page.
5. Windows cross-build: `config/args.win.gn` needs Clearcote's xwin setup; not ported here.
6. CI: `.github/workflows/build.yml` checks the repo, runs the self-test harness and
   compile-checks the series for linux-x64/arm64 and mac-arm64/x64. Full builds need larger
   or self-hosted runners (see README).
7. ShardBrowser gaps, in priority order, are in `docs/SHARDBROWSER-COMPARISON.md`; QUIC over
   SOCKS5 UDP is the first.

## Committing the Chromium source

The plan is to put the source in this repo. Before doing that, note:

- The unpacked tree is ~23 GB and tens of thousands of files. GitHub blocks files over 100 MB
  and gets slow or refuses pushes for repos that size.
- Chromium itself is BSD-3, so redistributing it is allowed. Some `third_party/` code is under
  other licenses, which ship with it.
- Alternatives: keep fetching the pinned tarball (what the scripts do now; reproducible from
  the ungoogled hashes), or keep the patched tree in a separate repo/branch, or store the
  tarball as a release asset.
