# camchrome

An open-source, anti-fingerprinting Chromium build: the latest
[ungoogled-chromium](https://github.com/ungoogled-software/ungoogled-chromium) plus an
engine-level fingerprint patch set, built from source you can read.

This repo holds patches, build configuration and scripts. The Chromium source itself
(~20 GB unpacked) is fetched into `build/`, which git ignores.

## Status

| Piece | Version | State |
|---|---|---|
| ungoogled-chromium base (Linux, Windows) | `157.0.8103.78-1` | pinned in `UPSTREAM` |
| ungoogled-chromium-macos overlay | `157.0.8103.78-1.1` | pinned, built and audited on macOS arm64 |
| Fingerprint patches (`patches/`) | 66 patches | verified against 157 (macOS) |
| Clearcote stealth audit | 365 checks | **100/100 · 236/236 scored checks agree · 0 findings** |

The shipped and audited binary is Chromium `157.0.8103.78` — the latest ungoogled-chromium
tag and the latest macOS overlay. The 156 macOS build is preserved at tag
`v156-audit100`. Linux/Windows use the same 157 base; their rebase is still open because the
series was verified on macOS.

The patches in `patches/` come from
[Clearcote](https://github.com/clearcotelabs/clearcote-browser) at commit
`ac40bdc` (BSD-3-Clause; see `LICENSE.clearcote` and `CREDITS.md`), rebased from 151 to
157 and extended with patches 986–989 (see below). They cover canvas, WebGL, WebGPU,
audio, fonts, GPU info, screen, timezone/locale, WebRTC, client hints, `navigator.webdriver`,
headless and CDP `Runtime.enable` signals, and more.

### What 986–989 add

| Patch | Fix |
|---|---|
| `986-canvas-alpha-and-grey-coherence` | alpha=0 pixels read black; grey stays grey |
| `987-engine-version-and-macos-persona` | every version surface derives from the engine; macOS personas get Apple Silicon GPU/screen/voices/devices |
| `988-webgl-readback-and-timing` | readPixels clips to the framebuffer, PBO and array exits agree, and spoofed `getParameter` values pay Chrome's GPU round-trip latency |
| `989-cpu-tier-and-netinfo-hints` | `navigator.cpuPerformance` follows the persona's cores; `Sec-CH-RTT/Downlink/ECT` match `navigator.connection` |

## Customizing the persona

One seed drives every surface (UA, UA-CH, canvas, WebGL/WebGPU, audio, fonts, screen,
timezone, voices, devices). The same seed produces the same machine in every process and on
every origin; a different seed produces a different but internally coherent one.

```bash
build/src/out/Default/Chromium.app/Contents/MacOS/Chromium \
  --fingerprint=demo --fingerprint-platform=macos --window-size=1280,800
```

| Flag | Effect |
|---|---|
| `--fingerprint=<seed>` | Selects the persona. Non-numeric seeds are hashed deterministically. |
| `--fingerprint-platform=windows\|linux\|macos\|android` | OS the persona claims. Defaults to the host platform. |
| `--fingerprint-brand=chrome\|edge\|opera\|vivaldi`, `--fingerprint-brand-version=<v>` | UA brand and version. The default claims the engine's own Chromium version. |
| `--fingerprint-gpu-vendor=…`, `--fingerprint-gpu-renderer=…` | Override the derived GPU. The macOS persona defaults to ANGLE/Metal; Windows to ANGLE/D3D11. |
| `--fingerprint-hardware-concurrency=<n>`, `--fingerprint-device-memory=<gb>` | CPU/RAM. A claim below the host's real cores is refused by the coherence checks (the audit measures worker parallelism). |
| `--fingerprint-screen-width/-height/-avail-width/-avail-height/-color-depth/-device-pixel-ratio/-max-touch-points` | Explicit screen/touch values. |
| `--timezone=<IANA>`, `--fingerprint-location=<lat,lng>` | Timezone and geolocation. |
| `--fingerprint-schema=2` | Versioned derivation: couples screen class and GPU class to the hardware tier and sizes the JS heap from RAM. |
| `--fingerprint-profile=<base64>` | Import a captured profile. It is authoritative over the seed; fields it omits keep the seed's values. |
| `--fingerprint-tls-profile=chrome-156` | TLS ClientHello persona; absent/native leaves BoringSSL stock. |
| `--disable-gpu-fingerprint` | Present the real GPU (strings, limits, extensions, pixels) instead of the persona. |
| `--disable-gpu-string-spoof` | Only the UNMASKED vendor/renderer strings go real; limits, extensions and noise stay on the persona. |
| `--disable-canvas-noise`, `--disable-fingerprint-noise` | Turn canvas-only, or all, farbling off. |
| `--portable-profile`, `--profile-encryption-key=<secret>` | Store the profile's cookie key in the profile, or derive it from a caller secret. |
| `--canvas-bridge-url=ws://…` | Forward canvas/WebGL rendering to a real-GPU host (see 065). |

## Requirements

- Linux x64 or macOS with Xcode + the Metal toolchain
  (`xcodebuild -downloadComponent MetalToolchain`)
- ~32 GB RAM for the official ThinLTO link; `config/args.mac.gn` builds without
  LTO/PGO/PCH so a 24 GB machine works. ~120 GB free disk, many CPU cores, several hours.
- `git`, `python3` (3.10+ for building), `ninja`, `xz`, `curl`; Linux also needs Chromium's
  [build deps](https://chromium.googlesource.com/chromium/src/+/main/docs/linux/build_instructions.md#install-additional-build-dependencies)
- macOS also needs `go`, `esbuild` and `typescript` (e.g. `brew install ninja esbuild typescript`)

## Build

```bash
scripts/00-fetch-source.sh            # ungoogled tooling + Chromium tarball -> build/src
scripts/01-apply-patches.sh --check   # ungoogled base + overlay, then list fingerprint patches that fail
scripts/01-apply-patches.sh           # apply everything
scripts/02-build.sh                   # gn gen + ninja -> build/src/out/Default/chrome
```

`TARGET` defaults to `linux` or `mac` from the host; `WORK` defaults to `./build`.
`config/args.win.gn` is Clearcote's Windows cross-compile config. It needs their xwin SDK setup,
which is not ported here.

## Stealth audit

`tools/stealth_audit.py` drives the built browser over raw CDP (no Playwright/Puppeteer,
so no driver flags or bindings pollute the result) and runs the public
[Clearcote audit](https://www.clearcotelabs.com/audit).

```bash
tools/stealth_audit.py --seed demo                      # 218/218 engine + fingerprint checks
tools/stealth_audit.py --seed demo --interact           # + the 18 input checks -> 236/236
tools/stealth_audit.py --seed demo --signals            # offline signal dump, no network
```

Without `--interact` the audit reports the 18 input checks as "not attempted" and scores
218/218. With `--interact` the harness completes its five optional input steps using
trusted CDP input (it aims inside each control, holds for a human interval, sends
`PointerEvent.pressure` 0.5 while a button is held and uses real key events), which lets
the audit score those checks too: 236/236 agree, 0 findings, score 100/100.

Chromium 157 builds Crubit's `cc_bindings_from_rs` against the rustc-dev toolchain;
`scripts/00-fetch-source.sh` sets that toolchain up and `scripts/02-build.sh` builds
Crubit before GN runs.

Results are written to `build/stealth-audit.txt` / `.json` (audit) and
`build/stealth-signals.json` (`--signals`).

## Rebasing the patches to a new Chromium

1. Bump `UG_TAG` and `UGW_TAG` (and `UGM_TAG` when moving macOS) in `UPSTREAM`.
2. `scripts/00-fetch-source.sh && scripts/01-apply-patches.sh --check` lists the failing patches.
3. Fix each in order: run `patch -p1 -f < patches/<name>` in `build/src`, resolve the `.rej` hunks
   by hand, then regenerate that one patch from the commit that contains it. Keep one git commit
   per patch (`git init && git add -A && git commit` after the ungoogled base) so a patch that
   changed can be regenerated without dragging in later patches that touch the same file.
4. Update `PATCHES_BASE` in `UPSTREAM`.

## More

`NOTES.md` has the background, the patch repos compared, what has been verified, and the open work.

`COVERAGE.md` maps the 365 checks of the [Clearcote fingerprint audit](https://www.clearcotelabs.com/audit/checks)
to the patches that cover them, lists the gaps, and names other repos to take patches from.

## Credits

See `CREDITS.md`.
