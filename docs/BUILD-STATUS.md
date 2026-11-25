# Build status per platform

As of 2026-11-25, Chromium `158.0.8127.88` (ungoogled-chromium tag `158.0.8127.88-1`, Windows
overlay `158.0.8127.88-1.1`), 103 patches.

| Target | Patches apply | `gn gen` | Full build links | Local tests | Clearcote audit | Where verified |
|---|---|---|---|---|---|---|
| linux-x64 | ✅ 99/99 | ✅ | ✅ official ThinLTO+PGO | fingerprint 55/61/66/59/65 (host, Linux, Windows, macOS, Android persona), UA 12/12, network 17/17, validator 5,000 personas 0 errors 0 warnings | 10 seeds per persona, persona fonts installed, three browsers at a time: Windows 249/249 and Android 206/206 with no finding; Linux 100/100 with the one finding stock Chrome 158 also gets | Ubuntu 24.04 container on a 64-core x64 server, no GPU, Xvfb + openbox via `tools/persona_display.py` |
| linux-arm64 | ✅ 99/99 | ✅ | ✅ official ThinLTO (no PGO), cross-compiled from x64 | not run (same source as x64; x64 results apply) | not run | same server |
| mac-arm64 | 157 only | — | 157 ✅ | — | 157: 100/100 | no 158 macOS overlay yet |
| mac-x64 | 157 only | — | — | — | — | — |
| windows-x64 | not ported | — | — | — | — | needs Clearcote's xwin setup |

The 157 tree (`v157.0.8103.78-camchrome.2`) and its Linux x64 build were used to develop
1000-1011; the same patches regenerated against 158 apply with no fuzz to a fresh 158 tree.

"CI" means the workflow does it but it had not run yet when this was written: the branch
was pushed with this review, so the first run is on the pull request.

## What was broken on Linux, and the fixes

The README said the Linux rebase was open; trying it found five problems, each fixed here.

1. **Patch 900 no longer applied.** Chromium 157 reformatted
   `build/toolchain/win/tool_wrapper.py` to 4-space indentation, so 900's `rc.py` hunk failed.
   The hunk is re-indented; the patch is otherwise unchanged. With `set -e` in
   `01-apply-patches.sh`, this stopped the whole Linux apply.
2. **The Windows overlay broke Linux tooling.** `01-apply-patches.sh` applies
   ungoogled-chromium-windows on Linux too (905 needs its `chrome_command_ids.h`). Twelve of its
   patches are Windows-host build tooling: `windows-fix-building-gn` makes gn's bootstrap build
   `gn.exe`, `windows-fix-python` swaps the hermetic Python, others drop Crubit or change
   compiler flags. On Linux, `gn` could not be bootstrapped at all. Those twelve are now skipped
   on Linux, as is `windows-fix-typescript-lib-dom` (its target file only exists in a Windows
   checkout). All of them were confirmed independent of the fingerprint series (each
   reverse-applies cleanly on the patched tree).
3. **`enable_nacl` was removed in 157**, so `gn gen --fail-on-unused-args` refused
   `config/args.linux.gn`. Dropped.
4. **The lite tarball has no Linux toolchain.** `00-fetch-source.sh` only fetched one for macOS.
   `scripts/linux-toolchain.sh` now fetches clang, Rust and the Debian sysroot (Chromium's own
   `update.py` / `update_rust.py` / `install-sysroot.py`), libclang and node from the GCS objects
   DEPS pins (`scripts/fetch-gcs-dep.py`, sha256-checked), tsgo and esbuild at the versions DEPS
   names (from npm), and links host `go`, `gperf` and `python3` where the build expects them.
   `02-build.sh` bootstraps gn with the tree's clang (the distro clang's libstdc++ lacks
   `std::expected` in C++23 mode) and installs it under `buildtools/` for V8's metagen step.
5. **No target-CPU switch.** `TARGET_CPU=arm64|x64` now overrides `target_cpu` (and installs
   the arm64 sysroot on Linux) so CI can cross-compile Linux arm64 and macOS x64.

## Compile check on Linux x64

`scripts/patched-sources.sh` lists the 95 C/C++ files the series edits that build on Linux;
`NINJA_TARGETS="$(scripts/patched-sources.sh)" scripts/02-build.sh` compiles each one with the
real `config/args.linux.gn` (official, ThinLTO, PGO). That takes about 10,700 ninja steps,
mostly code generation; it does not link.

**Result: all 95 compile** (`ninja: no work to do` on a re-check). The first pass hit three
missing prebuilts (Go for Dawn's generator, node `v24.12.0` exactly, and gn under `buildtools/`
for V8 metagen); each is fixed in the scripts and the second pass finished with no failures.

The build has `treat_warnings_as_errors = false`. Warnings in patched code, worth cleaning up
before turning that on:

| File | Warning | Patch |
|---|---|---|
| `components/ungoogled/persona_profile.cc:640-641` | declaration shadows a local variable | 002/987 series |
| `net/socket/socks5_client_socket.cc:310` | exit-time destructor | 984 |
| `net/socket/socks5_udp_smoke.cc:90` | unused function `PumpUntil` | 973 |
| `third_party/blink/renderer/modules/webgl/webgl_rendering_context_base.cc:6054` | `cb_substituted` set but not used | 070 (canvas bridge) |
| `content/browser/devtools/protocol/browser_handler.cc:780` | unused function `EaseInOut` (2026-11-18) | 130 |
| `third_party/blink/renderer/platform/fonts/font_cache.cc:400` | exit-time destructor for a static `std::set` (2026-11-18) | 040 |
| `third_party/blink/renderer/modules/canvas/canvas2d/base_rendering_context_2d.cc:1742` | `size_t` to `uint32_t` narrowing of a `std::hash` (2026-11-18) | 060 |
| `v8/src/inspector/v8-runtime-agent-impl.cc:1083` | unreachable code | 110 |

## macOS runners

Chromium 157's libc++ module build reads `DarwinFoundation{1,2,3}.modulemap` from the SDK,
which first ship in the macOS 26 SDK. GitHub's `macos-15` image only has the 15.x SDKs, so
`gn gen` fails there. The workflow defaults to `macos-26`, selects the newest Xcode on the
image, and stops with a clear error if the SDK lacks those files.

## Full builds

See [FULL-BUILD.md](FULL-BUILD.md) for the manual `full-build.yml` workflow (build, every local
test, optional site matrix) and what it needs.

A full build needs ~100 GB of disk and several hours on a large machine (3 h 55 min of ninja
on a 10-core Mac). GitHub's standard hosted runners can't do it (about 14 GB free disk after
cleanup and a 6-hour job limit). The `full` mode of the workflow takes runner labels as inputs
so it can run on larger GitHub runners or self-hosted machines.
