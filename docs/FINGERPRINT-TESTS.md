# Fingerprint surfaces and how each is tested

Review of 2026-11-17. For every patch in `patches/series`: what surface it changes, which
test covers it, and where nothing does.

## The three test layers

| Layer | What | Needs | Run |
|---|---|---|---|
| **Self-test** (`tools/fingerprint_selftest.py`, new) | Up to 53 local checks (47 on a host-platform persona; the rest depend on `--platform`, `--cores` and the like), each named after the patch it covers. Three launches (seed A twice, seed B once), two sites (`127.0.0.1` and `localhost`), probes in the window, an iframe and a dedicated worker, plus request headers recorded by its own server. | A built binary. No network. | `tools/fingerprint_selftest.py [--platform windows]` |
| **Clearcote audit** (`tools/stealth_audit.py`) | 365 remote coherence checks, 236 scored. The acceptance test. | Network, headful, ideally a GPU. | `tools/stealth_audit.py --seed demo --interact` |
| **UA self-test** (`tools/ua_selftest.py`) | 12 identity modes (default, persona, each brand, Android/Windows personas, `--user-agent`, CDP overrides with and without metadata): the UA string, `Sec-CH-UA`, the full-version list and `navigator.userAgentData` in page and worker must agree, and explicit CDP metadata must be kept as given (995). | A camchrome build; loopback only. | `tools/ua_selftest.py` |
| **Network self-test** (`tools/network_selftest.py`) | 17 local checks for the patches one page load can't reach: SOCKS5 and HTTP proxy auth, QUIC over the SOCKS5 relay (with a no-relay control), WebRTC exit IP via the relay, a STUN server name resolved by the proxy, no relaying to private addresses, humanized click, typing, drag and wheel scroll, rects, shadow root, no-farble, portable profile. Needs `aioquic` for QUIC. | A camchrome build; loopback only. | `tools/network_selftest.py` |
| **Automation self-test** (`tools/camauto_selftest.py`) | 11 checks for `tools/camauto.py`: the recorder is invisible to the page, records clicks/typing across a navigation, hides passwords, folds link navigations into the click; isolated reads; MCP handshake and tools; API token and actions. | Any Chromium (CI: stock Chrome). | `tools/camauto_selftest.py` |
| **Relay self-test** (`socks5_udp_relay_selftest`) | Net-level checks of the 990 relay socket against `tools/socks5_udp_proxy.py`. Built and run in CI (`build.yml`, compile linux-x64). | Only `//net`, no browser. | `tools/socks5_udp_proxy.py --run-selftest-binary …` |
| **Public check sites** (`tools/site_checks.py`, new) | browserleaks QUIC/JA4, Twilio WebRTC, fingerprint.com, deviceandbrowserinfo, ProxyShard checker, pixelscan, browserscan, antcpt reCAPTCHA. See [TEST-MATRIX.md](TEST-MATRIX.md). | Network, a residential proxy with SOCKS5 UDP for the QUIC/WebRTC rows. | `tools/site_checks.py --proxy socks5://… --socks5-udp …` |

Plus `--clearcote-validate-persona=<N>` (patch 974), which derives N personas and checks the
cross-surface invariants inside the engine. The self-test runs it.

`tools/fingerprint_selftest.py --stock --binary <any Chrome>` runs the harness on a stock
Chrome: checks that need a persona are reported as XFAIL instead of FAIL. CI runs it this way
on every push so the harness itself stays working. On Chrome for Testing 153 headless in this
review it gave PASS 37, XFAIL 8, SKIP 2, FAIL 0.

## Review result

- **What's tested:** the persona surfaces (identity, screen, render, audio, devices,
  locale) are covered twice: by the Clearcote audit (100/100 on macOS 157 per `AUDIT.md`)
  and now by a local check per patch.
- **What isn't:** before this review, the only proof per patch was the remote audit, which
  names checks rather than patches and needs network. Nothing tested the network patches
  (SOCKS5 auth and UDP, proxy auth, TLS persona), the input/agent patches, or a Linux or
  Windows persona. The self-test closes part of that. What still has no automated test is
  listed in the last column below.
- **Build:** the series had only been verified on macOS. In this review it was applied and
  compiled on Linux. See [BUILD-STATUS.md](BUILD-STATUS.md).

## Patch by patch

✓ = automated check exists. **Gap** = no automated check yet.

| Patch | Surface | Self-test check(s) | Clearcote audit group | Gap |
|---|---|---|---|---|
| 000 switches | flag plumbing to child processes | every check depends on it; `iframe-*`, `worker-*` prove renderer and worker processes see the flags | all | — |
| 001 farble seed | per-eTLD+1 noise seed | ✓ `canvas-same-seed-same-site-stable`, `canvas-differs-per-site`, `canvas-differs-per-seed` | Render & GPU | — |
| 002 persona | one coherent persona; profile import | ✓ `iframe/worker-identity-matches-window`, `persona-platform-honoured`; library entries checked by `tools/fingerprint_library.py check` (CI) | Identity coherence | `--fingerprint-profile` import is not exercised in a browser yet. |
| 010 UA / UA-CH / webdriver | `navigator.userAgent`, `userAgentData`, `Sec-CH-UA*`, `webdriver` | ✓ `webdriver-false`, `ua-no-headless`, `header-ua-equals-js`, `header-platform-equals-uadata`, `navigator-platform-coherent`, `uach-brand-equals-ua`, `header-full-version-list-sent` | HTTP vs runtime, Navigator identity | — |
| 020 audio | AudioContext metadata, offline render | ✓ `audio-same-seed-stable`, `audio-sample-rate-plausible` | Render & GPU | Per-site audio difference is not asserted (020's noise was retired on 157; the render is deterministic). |
| 030 cores / memory | `hardwareConcurrency`, `deviceMemory` | ✓ `device-memory-on-chrome-grid`, `cores-override` (with `--cores`) | Navigator identity | — |
| 040 fonts | font allowlist | ✓ `fonts-no-foreign-os-families` (with `--platform windows\|linux\|macos`): another OS's signature families are not visible; `android-handset-fonts` | Engine & OS oracles | Host-platform persona not asserted (the host's fonts are the persona's). |
| 050 shadow DOM / client rects | closed shadow access, no rect noise | ✓ `rects-not-noised` (network_selftest) | Automation surface | — |
| 060 canvas | 2D noise, export paths, `measureText` | ✓ `canvas-stable-within-page`, `measuretext-nonzero` | Render & GPU | — |
| 065 canvas bridge | remote GPU rendering | — | — | **Gap.** Opt-in feature with no test and no host in the repo. |
| 070 / 071 WebGL | GPU strings, parameters, extensions | ✓ `webgl-session-constant-across-sites`, `iframe/worker-webgl-matches-window` | Render & GPU | — |
| 075 WebGPU | adapter info and limits | ✓ `webgpu-vendor-matches-webgl` (skipped when no adapter) | Render & GPU | GPU-less CI has no adapter, so this only runs on a GPU machine. |
| 090 timezone | `--timezone` | ✓ `timezone-pinned` | Environment & locale | — |
| 092 locale | ICU locale follows `Accept-Language` | ✓ `accept-language-equals-navigator`, `intl-locale-equals-navigator` | Environment & locale | — |
| 100 WebRTC | no host IP, synthesized srflx | ✓ `webrtc-no-private-ip` | Network & WebRTC | `--webrtc-ip` srflx value needs a STUN server; covered by the Twilio row in the test matrix. |
| 110 Runtime.enable | CDP tell | indirectly (the harness uses `Runtime.evaluate`) | Automation surface | — |
| 120 headless | headless tell | ✓ `ua-no-headless` (when run `--headless`) | Automation surface | — |
| 130 humanized input | `Browser.humanizedClick*` CDP commands | ✓ `humanized-click` (network_selftest): trusted click on the target after ≥5 moves | Input & behaviour | — |
| 993 humanized typing, scroll, drag | `Browser.humanizedType`, `humanizedScroll`, `humanizedDrag` | ✓ `humanized-type`, `humanized-type-corrects-typos`, `humanized-drag`, `humanized-scroll-wheel` (network_selftest) | Input & behaviour | — |
| 140 screen | `screen.*`, outer window | ✓ `avail-within-screen`, `window-fits-screen`, `color-depth-24-or-30` | Screen & display | — |
| 141 / 142 media queries, touch | pointer/hover, device-size, colour scheme | ✓ `mq-device-size-equals-screen`, `no-touch-means-fine-pointer`, `color-scheme-header-equals-media` (`Sec-CH-Prefers-Color-Scheme` = `prefers-color-scheme`) | Screen & display | Colour scheme is the host's, not the persona's (see UPSTREAM-SOURCES.md §4). |
| 145 storage quota | `storage.estimate()` | ✓ `storage-quota-present` | Capability surfaces | — |
| 146 JS heap | `performance.memory` | ✓ `js-heap-limit-plausible` | Network & WebRTC | — |
| 147 media capabilities | `decodingInfo()` | ✓ `media-capabilities-agree-with-mse`: `decodingInfo().supported` equals `MediaSource.isTypeSupported` for H.264, VP9, AV1, HEVC, AAC, Opus | Capability surfaces | — |
| 148 media devices | `enumerateDevices()` | ✓ `media-devices-present`, `media-device-labels-empty` | Capability surfaces | — |
| 150 sensors | battery, connection, keyboard, touch points | ✓ `battery-plausible`, `keyboard-layout-map` | Capability surfaces | — |
| 160 OS details | `new URL("C:/")`, `navigator.share` | ✓ `url-drive-letter-is-file` (Windows persona) | Engine & OS oracles | — |
| 170 voices | `speechSynthesis.getVoices()` | ✓ `voices-not-empty` | Environment & locale | — |
| 180 geolocation | `--fingerprint-location` | ✓ `geolocation-from-flag` | Capability surfaces | — |
| 200 agent | in-browser LLM agent | — | — | **Gap.** Needs an LLM endpoint; no test. |
| 210 TLS persona | ClientHello per claimed major | — | TLS & network layer | **Gap locally.** Covered by the browserleaks JA4 row in the test matrix. |
| 220 no-farble switch | `--cc-no-farble` | ✓ `no-farble-switch` (network_selftest): canvas differs across sites with noise, equal with the switch | — | — |
| 900 / 905 | Windows cross-build | — | — | Build-only. Applies on Linux (verified). |
| 955 WebGL parity | advertised = returned extensions | — | Render & GPU | **Gap locally**; audit covers it. |
| 960 | `fakeShadowRoot` unexposed | ✓ `no-fake-shadow-root` (network_selftest) | Automation surface | — |
| 961 Sec-CH-DPR | header = `devicePixelRatio` | ✓ `sec-ch-dpr-equals-dpr` | HTTP vs runtime | — |
| 962 DPR coherence | `resolution` media query | ✓ `mq-resolution-equals-dpr` | Screen & display | — |
| 963 / 984 SOCKS5 auth | RFC 1929 | ✓ `socks5-auth`, `socks5-auth-wrong-password` (network_selftest, against `tools/socks5_udp_proxy.py --auth`) | — | — |
| 964 portable profile key | cookie key | ✓ `portable-profile` (network_selftest): a copied profile keeps its cookie. On Linux with `--password-store=basic` stock Chrome passes too; the cross-machine case (Keychain/DPAPI) needs macOS/Windows. | — | — |
| 965 `Math.tanh` | Windows-exact result | records `tanh` in the raw output | Engine & OS oracles | Expected Windows value not pinned yet. |
| 966 / 968 / 969 | D3D11 shader dialect, precision, extensions on a non-D3D host | — | Render & GPU | **Gap locally**; audit covers it with a Windows persona. |
| 967 / 982 / 983 / 970 WebGPU | limits, features, architecture, preferred format | ✓ `webgpu-preferred-format-windows` (Windows persona) | Render & GPU | GPU machine only. |
| 971 system-ui font (Linux) | `system-ui` resolves | — | Engine & OS oracles | **Gap.** |
| 972 / 975 window | frame coherence, fits work area | ✓ `window-fits-screen` | Screen & display | — |
| 973 SOCKS5 UDP, 990 QUIC relay, 991 WebRTC exit IP | WebRTC and QUIC UDP via ASSOCIATE | — | Network & WebRTC | `socks5_udp_relay_selftest` (net-level, against `tools/socks5_udp_proxy.py`, run by CI's `relay-selftest` job) covers the relay socket, named origins and proxies, refusal and fallback. The browser path is covered by the browserleaks and Twilio rows of the test matrix. |
| 974 validator | in-engine invariants | ✓ `persona-validator-clean` | — | — |
| 976 Android uniforms | WebGL2 limits on Android personas | run by `full-build.yml` (`fingerprint_selftest.py --platform android`) | Render & GPU | — |
| 977 activation rule | persona on/off/profile | ✓ `uach-full-version-equals-engine` | Identity coherence | — |
| 978 proxy auth | HTTP proxy Basic/Digest | ✓ `http-proxy-auth` (network_selftest, Basic) | — | Digest not exercised. |
| 979 battery dispatcher | events vs getters | ✓ `battery-plausible` | Capability surfaces | Events not exercised. |
| 980 / 981 schema 2 | coupled draws | — | — | Run the self-test with `--extra-flag=--fingerprint-schema=2`; no schema-2-specific assertion yet. |
| 985 stencil masks | WebGL stencil | — | Render & GPU | **Gap locally**; audit covers it. |
| 986 canvas alpha / grey | alpha-0 reads black, grey stays grey | ✓ `canvas-alpha-zero-reads-black`, `canvas-grey-stays-grey` | Render & GPU | — |
| 987 engine version, macOS persona | version from engine; Metal persona | ✓ `ua-major-equals-engine`, `uach-full-version-equals-engine`, `webgpu-vendor-matches-webgl` | HTTP vs runtime, Render & GPU | — |
| 988 readback and timing | `readPixels` sentinel, PBO, `getParameter` latency | ✓ `readpixels-sentinel-untouched` | Render & GPU | PBO and timing only in the audit. |
| 989 CPU tier, network hints | `cpuPerformance`, `Sec-CH-RTT/Downlink/ECT` | ✓ `rtt-header-equals-connection`, `downlink-header-equals-connection` | HTTP vs runtime | — |
| 990 / 991 QUIC and WebRTC via the SOCKS5 relay | HTTP/3 and STUN through UDP ASSOCIATE | ✓ `quic-over-socks5`, `quic-socks5-no-relay`, `webrtc-relay-exit-ip`, `webrtc-stun-name-via-proxy`, `webrtc-private-destination-dropped` (network_selftest) | Network & WebRTC | End to end through a real proxy only in the site matrix. |
| 992 Android behaviour | touch from mouse, sensors, handset fonts | ✓ `android-*` (with `--platform android`), `desktop-mouse-stays-mouse` | Input, Capability surfaces | — |
| 994 validator CLI | persona derivation without a browser | ✓ `fingerprint_library.py selftest --validator` (CI compile job) | — | — |
| 995 UA / client hints | UA, `Sec-CH-UA*`, `userAgentData` in every identity mode | ✓ `tools/ua_selftest.py` (12 modes; full-build; CI runs it with `--stock` as a harness check) | HTTP vs runtime, Navigator identity | — |
| 996 WebGL no-farble | `--cc-no-farble` covers `readPixels` | ✓ `no-farble-switch` (network_selftest) | — | — |
| 997 Android viewport | content width = phone width | ✓ `android-device-width-equals-screen`, `android-no-meta-layout-980` | Screen & display | — |
| (none) scrollbar geometry | classic vs overlay scrollbar width | ✓ `scrollbar-width-matches-persona-os` (with `--platform windows\|linux\|android`) | — | **No patch.** A cross-OS persona keeps the host's scrollbar; this check is expected to fail for a Windows or Linux persona on a macOS host until one exists. |

## What to add next

Done since the first review: a local SOCKS5 server with UDP ASSOCIATE and auth
(`tools/socks5_udp_proxy.py`, used by network_selftest), per-persona-OS runs in `full-build.yml`
(host, Windows, macOS, Android), and the one-line checks for 960, 050, 040, 147 and 220.

1. A scrollbar patch for cross-OS personas, then `scrollbar-width-matches-persona-os` passes on
   every host.
2. Pin the Windows `Math.tanh` result (965) from a real Windows Chrome and assert it.
3. A `--fingerprint-profile` import round trip in a browser (002): capture with
   `fingerprint_library.py`, launch with the printed flags, compare.
