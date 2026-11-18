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
| Fingerprint patches (`patches/`) | 74 patches | verified against 157: built and audited on macOS arm64; built on Linux arm64 with the local self-tests passing; applied, `gn gen` and every patched file compiled on Linux x64 (see `docs/BUILD-STATUS.md`) |
| Clearcote stealth audit | 365 checks | **100/100 · 236/236 scored checks agree · 0 findings** |

The shipped and audited binary is Chromium `157.0.8103.78` — the latest ungoogled-chromium
tag and the latest macOS overlay. The 156 macOS build is preserved at tag
`v156-audit100`. Linux uses the same 157 base: a Linux arm64 build (official ThinLTO+PGO) links
and passes the local self-tests (fingerprint, UA, network), but the Clearcote audit has not been
run on Linux yet. Windows is not ported.

The patches in `patches/` come from
[Clearcote](https://github.com/clearcotelabs/clearcote-browser) at commit
`ac40bdc` (BSD-3-Clause; see `LICENSE.clearcote` and `CREDITS.md`), rebased from 151 to
157 and extended with patches 986–994 (see below). They cover canvas, WebGL, WebGPU,
audio, fonts, GPU info, screen, timezone/locale, WebRTC, client hints, `navigator.webdriver`,
headless and CDP `Runtime.enable` signals, and more.

### What 986–989 add

| Patch | Fix |
|---|---|
| `986-canvas-alpha-and-grey-coherence` | alpha=0 pixels read black; grey stays grey |
| `987-engine-version-and-macos-persona` | every version surface derives from the engine; macOS personas get Apple Silicon GPU/screen/voices/devices |
| `988-webgl-readback-and-timing` | readPixels clips to the framebuffer, PBO and array exits agree, and spoofed `getParameter` values pay Chrome's GPU round-trip latency |
| `989-cpu-tier-and-netinfo-hints` | `navigator.cpuPerformance` follows the persona's cores; `RTT/Downlink/ECT` client hints match `navigator.connection` on navigations and subresources alike |

### What 990–991 add (network)

| Patch | Fix |
|---|---|
| `990-quic-over-socks5-udp` | HTTP/3 through a SOCKS5 proxy's UDP relay (`--socks5-udp`), with the origin resolved by the proxy, never locally; named relay proxies; no relaying to private addresses (also behind a proxy client on 127.0.0.1); a relay address the proxy reports from behind NAT is replaced by the proxy's own; a proxy that never answers UDP ASSOCIATE fails after 15 s |
| `991-webrtc-relay-exit-ip` | WebRTC's srflx candidate is the proxy's exit IP without `--webrtc-ip`: real STUN runs through the relay, host candidates stay hidden, and a STUN/TURN server **name** is resolved by the proxy, never by this machine's DNS |

Through a SOCKS5 proxy that supports UDP ASSOCIATE:

```bash
chrome --proxy-server=socks5://proxy.example:1080 --socks5-credentials=user:pass --socks5-udp
```

QUIC and WebRTC then leave through the proxy like TCP does. If the proxy refuses UDP, QUIC falls
back to HTTP/2 over the TCP tunnel and WebRTC has no UDP path (no direct fallback). Test the relay
locally with `tools/socks5_udp_proxy.py` (a SOCKS5 proxy with UDP ASSOCIATE).

### What 992 adds (Android personas)

| Patch | Fix |
|---|---|
| `992-android-persona-mobile-behaviour` | with `--fingerprint-platform=android` the mouse becomes touch on web pages (touchstart, `pointerType: "touch"`, taps), deviceorientation/devicemotion stream a phone held in a hand, fonts answer with the handset set from Android's `fonts.xml` (on Linux and macOS hosts), `getVoices()` lists the Google TTS voices, and `outerWidth` is the phone's screen width |

The window is sized to the persona's screen (975), and since 997 Chrome's 500 px desktop minimum
no longer widens it, so a `width=device-width` page lays out at exactly `screen.width` and a page
without a viewport tag at Android's 980 px. Drive it with mouse events, CDP `Input.dispatchMouseEvent` or `Browser.humanized*`
(all become touch; browser UI pages such as chrome:// and DevTools keep the mouse). A raw CDP
`Input.dispatchTouchEvent` switches that tab to injected touch, and mouse events stay mouse events
there afterwards.

### What 993 adds (human input)

| Patch | Fix |
|---|---|
| `993-humanized-typing-scroll-drag` | `Browser.humanizedType`, `Browser.humanizedScroll` and `Browser.humanizedDrag` next to 130's `humanizedClick`: human typing rhythm with Shift handling and corrected typos, per-OS wheel/trackpad/swipe scrolling, and drags, all as trusted input without attaching to the page |
| `994-persona-validator-cli` | The persona validator as a small standalone binary that CI builds on every push, plus the persona bugs it found: macOS personas validated against Windows rules, 10 touch points under a Mac UA, imported Android and macOS captures overwritten by the platform tables, and an unset platform validated as Windows |

```python
browser.call("Browser.humanizedClickSelector", {"targetId": tid, "selector": "#email"})
browser.call("Browser.humanizedType", {"targetId": tid, "text": "me@example.com", "typoRate": 0.03})
browser.call("Browser.humanizedScroll", {"targetId": tid, "deltaY": 900})
```

### What 995–996 add (identity and switches)

| Patch | Fix |
|---|---|
| `995-ua-client-hints-coherence` | the UA string, `Sec-CH-UA`, the full-version list and `navigator.userAgentData` (page and workers) always agree: with a persona, with `--fingerprint-brand`, with `--user-agent`, and with CDP `Emulation.setUserAgentOverride` with or without `userAgentMetadata` (explicit metadata is kept exactly as given). No Chrome version is hard-coded any more |
| `996-webgl-readback-no-farble` | `--cc-no-farble` also turns off the WebGL `readPixels` perturbation |
| `997-android-persona-viewport` | an Android persona's content area is the phone's width (no 500 px desktop minimum), and viewport meta tags stay off under a desktop-site override, as on Android |

Every Chrome version claim comes from the engine (157.0.8103.78, the newest ungoogled-chromium
release; Google Chrome stable is already 158) or from `--fingerprint-brand-version`.
`--fingerprint-brand=edge` reports the Edge stable build for this Chromium major (157.0.4341.60).
Opera and Vivaldi have no reliable default, so `--fingerprint-brand=opera|vivaldi` needs
`--fingerprint-brand-version=<their version>`; without it plain Chromium is claimed.

## Automation: local API, MCP, recorder, synchronised windows

`tools/camauto.py` drives camchrome with the engine's human input and never touches the page's
main world (reads run in an isolated world; `Runtime.enable` is never sent).

```bash
tools/camauto.py api --port 7878 -- --proxy-server=socks5://proxy:1080 --socks5-udp
#   POST /v1/browsers {"pick": {"os": "windows", "seed": "c42"}}  ->  {"id": "b1", ...}
#   POST /v1/browsers/b1/actions {"action": "click", "selector": "#login"}
tools/camauto.py mcp                       # MCP server on stdio (browser_launch, _click, _type, ...)
tools/camauto.py record --out flow.json --url https://example.com -- --fingerprint=alice
tools/camauto.py replay flow.json --secrets '{"#password": "..."}' -- --fingerprint=bob
tools/camauto.py sync --count 3 --flags-each "--fingerprint=s{n}" --url https://example.com
```

The API binds to loopback and wants the bearer token it prints. Add the MCP server to a client
as a stdio command: `python3 /path/to/camchrome/tools/camauto.py --binary /path/to/chrome mcp`.

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
| `--fingerprint-profile=<base64>` | Import a captured profile. It is authoritative over the seed; fields it omits keep the seed's values. `tools/fingerprint_library.py` captures real devices into `profiles/library/` and prints these flags for one (see `profiles/README.md`). |
| `--fingerprint-tls-profile=chrome-156` | TLS ClientHello persona; absent/native leaves BoringSSL stock. |
| `--disable-gpu-fingerprint` | Present the real GPU (strings, limits, extensions, pixels) instead of the persona. |
| `--disable-gpu-string-spoof` | Only the UNMASKED vendor/renderer strings go real; limits, extensions and noise stay on the persona. |
| `--disable-canvas-noise`, `--disable-fingerprint-noise` | Turn canvas-only, or all, farbling off. |
| `--portable-profile`, `--profile-encryption-key=<secret>` | Store the profile's cookie key in the profile, or derive it from a caller secret. |
| `--canvas-bridge-url=ws://…` | Forward canvas/WebGL rendering to a real-GPU host (see 065). |
| `--socks5-udp` | With a SOCKS5 `--proxy-server`: relay QUIC (990) and WebRTC (973) UDP through the proxy's UDP ASSOCIATE. |
| `--socks5-credentials=user:pass` | RFC 1929 credentials for the SOCKS5 proxy (TCP and the UDP relay). |
| `--webrtc-ip=<ip>\|auto` | `<ip>`: fabricate the WebRTC srflx candidate at that IP, no STUN (100). `auto` or absent with `--socks5-udp`: real STUN through the relay finds the exit IP (991). |

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
`TARGET_CPU=x64|arm64` cross-compiles (Linux arm64 from an x64 host, macOS x64 from Apple
Silicon). `GN_ONLY=1 scripts/02-build.sh` stops after `gn gen`, and
`NINJA_TARGETS="$(scripts/patched-sources.sh)" scripts/02-build.sh` compiles only the files the
series touches, a quick check that a rebase builds.

On Linux, `00-fetch-source.sh` also fetches what the lite tarball leaves out
(`scripts/linux-toolchain.sh`): Chromium's clang, Rust and Debian sysroot, the DEPS-pinned
libclang and node (`scripts/fetch-gcs-dep.py`, sha256-checked), tsgo and esbuild at the versions
DEPS names, and links the host `go`, `gperf` and `python3` where the build expects them. The host
needs `python3`, `npm`, `go`, `gperf`, `ninja` and `curl`.

### Linux on a Mac (Docker)

Chromium's Linux host toolchain is x86-64 only. On Apple Silicon, `scripts/docker-linux-build.sh`
builds inside an x86-64 Ubuntu 24.04 container (Docker Desktop runs it under Rosetta), with the
tree in a Docker volume:

```bash
scripts/docker-linux-build.sh arm64      # or x64; docker logs -f camchrome-linux-arm64
```

It uses `config/args.linux.lowmem.gn` (no ThinLTO/PGO, fits ~18 GB of container memory; pass
`ARGS_FILE=config/args.linux.gn` on a 64 GB machine for the official configuration), installs a
pinned Go 1.26 for Dawn, and resumes where it stopped. Any machine can pick another args file with
`ARGS_FILE=... scripts/02-build.sh`.

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

## Tests

| Tool | What | Network |
|---|---|---|
| `scripts/check-repo.sh` | series integrity, patch syntax, dead doc links, shellcheck, Python | no |
| `tools/fingerprint_selftest.py` | one local check per patched surface, named after its patch | no |
| `tools/network_selftest.py` | proxies, QUIC and WebRTC over the SOCKS5 relay, humanized input, no-farble, portable profile | no (needs `aioquic`) |
| `tools/ua_selftest.py` | UA string, client hints and `userAgentData` agree in every identity mode (995) | no |
| `tools/stealth_audit.py` | the Clearcote audit (acceptance test) | yes |
| `tools/site_checks.py` | browserleaks QUIC, Twilio WebRTC, fingerprint.com, pixelscan, browserscan, reCAPTCHA… | yes, plus a SOCKS5 proxy with UDP |
| `tools/upstream_watch.py` | pinned upstreams vs their newest tags, Chrome stable, Clearcote patch changes, reference repos (`docs/UPSTREAM-SOURCES.md`); run weekly by `upstream-watch.yml` | yes |

```bash
tools/fingerprint_selftest.py --platform macos          # against the build
tools/fingerprint_selftest.py --stock --binary "$(command -v google-chrome)"   # harness only
```

`docs/FINGERPRINT-TESTS.md` maps every patch to its tests and lists the untested ones;
`docs/TEST-MATRIX.md` is the external-site matrix.

## CI

`.github/workflows/build.yml` runs the repository checks and the self-test harnesses on every
push, then fetches, patches, runs `gn gen` and compiles the patched files for linux-x64. The
other targets (linux-arm64, mac-arm64, mac-x64) are compile-checked when the workflow is
dispatched or a `v*` tag is pushed: hosted macOS minutes cost 10x and two ~2 h macOS jobs per
push most likely used up the Actions minutes (no run has started since 2026-11-18 03:13 UTC). `upstream-watch.yml` checks the pins weekly.
Dispatch it with `mode=full` (or push a `v*` tag) to build
and upload the browser. A full build does not fit GitHub's standard runners (disk and the 6 h
limit): set `linux_runner` / `mac_runner`, or the repository variables
`CAMCHROME_LINUX_RUNNER` / `CAMCHROME_MAC_RUNNER`, to larger or self-hosted runners.

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

`docs/SHARDBROWSER-COMPARISON.md` compares the patch set with ShardBrowser's advertised
capabilities (QUIC/WebRTC over SOCKS5, mobile personas, input, automation) and lists the gaps.

`docs/UPSTREAM-SOURCES.md` lists the upstreams to watch for releases and security fixes, and the
repositories worth mining for more fingerprint work.

`COVERAGE.md` maps the 365 checks of the [Clearcote fingerprint audit](https://www.clearcotelabs.com/audit/checks)
to the patches that cover them, lists the gaps, and names other repos to take patches from.

## Credits

See `CREDITS.md`.
