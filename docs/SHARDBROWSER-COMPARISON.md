# camchrome vs ShardBrowser: capabilities and gaps

Compared on 2026-11-17 against [ProxyShard/ShardBrowser](https://github.com/ProxyShard/ShardBrowser)
(README at the commit current on that date).

**What is being compared.** ShardBrowser is two things: an MIT-licensed desktop launcher
(Tauri/React/Rust: profiles, proxies, API, MCP, SDKs, automation) and a **closed-source**
patched Chromium 157 engine downloaded from their CDN. Its license forbids reverse
engineering the engine. So this compares camchrome's patch set with what ShardBrowser's README
*claims* for its engine, and camchrome's tooling with its launcher. None of ShardBrowser's
claims were verified here.

camchrome is the engine only: patches, build scripts and test harnesses. It has no launcher.

Legend: ✅ covered · ⚠️ partial · ❌ missing · — not applicable

## 1. The feature list Ali asked to check

These rows are ShardBrowser's own comparison table. "Status in camchrome" is what the patch
set does today, from reading the patches.

| Feature | ShardBrowser claim | camchrome today | What it would take |
|---|---|---|---|
| **QUIC / HTTP-3 over SOCKS5** | ✅ stable end to end via the proxy's UDP relay | ✅ **Implemented in 990** (needs `--socks5-udp`). A QUIC session to an origin reached through one SOCKS5 proxy goes over the proxy's UDP ASSOCIATE relay. The origin is resolved by the proxy (datagrams carry the hostname), never locally. Proxied sessions never migrate. A proxy that refuses UDP fails the QUIC job and the request stays on HTTP/2 over TCP. Checked by `socks5_udp_relay_selftest` against `tools/socks5_udp_proxy.py`; **not yet seen end to end** in a linked browser against browserleaks.com/quic. | Run the browserleaks row of the test matrix on a full build through a UDP-capable proxy. |
| **WebRTC over SOCKS5, no real-IP leak** | ✅ proxy UDP relay or synthesized candidates | ✅ **Automatic since 990/991.** 973 relays P2P UDP through SOCKS5 UDP ASSOCIATE; a named proxy is now resolved instead of falling back to a direct socket (990); real STUN through the relay reports the exit IP, so `--webrtc-ip` is no longer needed (991); host candidates and the srflx related address are hidden; the relay never delivers to private/loopback/link-local addresses (990). `--webrtc-ip=<ip>` remains for proxies without UDP. | Verify on networktest.twilio.com with a full build. |
| **Consistency of generated profiles** | ✅ coherent device (GPU, CPU, RAM, UA, fonts) | ✅ **Yes.** One seed derives the whole persona (002, 977, 980). `--clearcote-validate-persona=N` (974) checks the invariants in-engine. The Clearcote audit scored 100/100 on macOS 157. | Run the validator and the self-test for every persona OS in CI (added in `.github/workflows/build.yml` for the full build). |
| **Bundled fingerprint library** | 220 profiles from real-device samples (31 mac, 120 Windows, 19 Linux, 50 Android) | ❌ **No library.** Personas are derived from a seed using built-in tables; `--fingerprint-profile=<base64>` imports one captured profile at a time. | Collect real-device captures into a versioned JSON library (the `--fingerprint-profile` format), e.g. from [browserforge](https://github.com/daijro/browserforge) (Apache-2.0) data, and a small selector by OS/GPU class. Keep GPL datasets (chrome-fingerprints) out. |
| **Mobile profiles (touch input, sensors, phone fonts)** | ✅ behaves like a phone: mouse becomes touch, mobile viewport, orientation, motion sensors, handset fonts | ⚠️ **Answers like a phone, doesn't behave like one.** `--fingerprint-platform=android` gives an Android UA/UA-CH, `mobile=true`, portrait orientation, coarse pointer and touch points (002, 142), Android WebGL limits (976) and Roboto in the font list (040). Missing: mouse-to-touch conversion, mobile viewport/layout (meta viewport is ignored on desktop), DeviceMotion/DeviceOrientation sensor data, the handset font set as a whole, Android DRM robustness. | Turn on Blink's mobile emulation paths (the ones DevTools device mode uses: `WebPreferences` viewport meta, touch event emulation) from the persona; a sensor provider that reports a held device; an Android font group in 040. |
| **Synchronised windows** | ✅ one window drives, the others repeat in their own profiles | ❌ **None.** | Launcher feature: a controller that mirrors trusted input from one window to many over CDP, each replayed with its own motor seed. Belongs in a launcher, not the engine. |
| **Built-in automation** | ✅ visual projects, recording, fleet runs, WASM modules, local API + MCP | ⚠️ **Engine pieces only.** Raw CDP (with `Runtime.enable` hidden, 110) and an in-browser LLM agent (200, `--agent-llm-*`). No recorder, project format, fleet runner, local API or MCP server. Recommended driver: patchright. | Launcher feature. A thin local API + MCP server over CDP would cover most of it. |
| **Human input engine** | ✅ in the browser itself, used by hand and by scripts (pointer, typing with rhythm and typos, wheel per platform, touch gestures) | ⚠️ **Pointer clicks only.** 130 adds `Browser.humanizedClick` / `humanizedClickSelector` (Bezier path, Gaussian jitter, eased timing) and 200 routes the agent's clicks through it. No humanized typing, scrolling, drag or touch gestures; trusted-input timing for typing lives in the test harness (`tools/stealth_audit.py --interact`), not the engine. | Extend 130 with `typeText` (per-key timing, optional corrections), `wheel` (detents on Windows/Linux, trackpad on macOS) and touch gestures for Android personas. |

## 2. Engine fingerprint surfaces

| ShardBrowser engine surface | camchrome | Notes |
|---|---|---|
| UA, platform, vendor, cores, RAM, touch points, full Sec-CH-UA with stable GREASE | ✅ 010, 030, 150, 977, 987 | Version always derives from the engine (987). |
| WebGL vendor/renderer/extensions/limits | ✅ 070, 071, 955, 966–969, 976, 985, 988 | |
| WebGPU adapter + limits | ✅ 075, 967, 970, 982, 983 | ShardBrowser returns **no adapter** for a Linux persona (Chrome on Linux ships WebGPU off for most GPUs). camchrome serves a coherent adapter. **Check** which matches real Chrome 157 on Linux before changing. |
| Canvas noise per profile | ✅ 001, 060, 986 | |
| DOMRect / ClientRects noise | — by design | camchrome removed rect noise (050): rects are outputs of fonts + DPR + viewport, and noising them breaks coherence. |
| Color gamut, HDR | ✅ 141 (`color-gamut`, `dynamic-range`) | |
| Audio sample rate, channel count, noise | ⚠️ 020, 981 | Sample rate and latencies yes; `maxChannelCount` not personified. |
| Screen, available area, DPR, colour depth, window cap | ✅ 140, 961, 962, 972, 975 | |
| Timezone, ICU locale, Accept-Language **from the proxy's country** | ⚠️ 090, 092 | Engine honours the flags; nothing derives them from the proxy exit. Launcher-side (COVERAGE.md design rule 5). |
| Geolocation manual or from proxy IP | ⚠️ 180 | Manual only. |
| Connection, downlink, RTT, save-data, storage quota, JS heap, battery, media devices | ✅ 145, 146, 148, 150, 979, 981, 989 | |
| TLS ClientHello / JA4 matching the version | ✅ BoringSSL is real Chrome TLS; 210 for claiming another major | ShardBrowser says Chrome **156** JA4 on a 157 engine; camchrome claims the built major. |
| HTTP/3 over the proxy's UDP relay | ✅ 990 | See QUIC above. |
| WebRTC policy block / tcp_only / auto | ✅ 100, 973, 990, 991 + stock `--force-webrtc-ip-handling-policy` | The relay drops datagrams to private, loopback and link-local destinations (990). |
| Speech voices per OS | ✅ 170, 987 | Windows set is SAPI only (no Google network voices, as in ungoogled). |
| Fonts: declared set only, host fonts hidden | ⚠️ 040, 971 | Windows persona list is complete. The macOS font patches (956–958) were 150-only and are **not** in the 157 series, so a macOS persona on a non-Mac host sees host fonts. |
| Mobile behaviour | ⚠️ | See section 1. |
| Google validation headers (`x-client-data`) | ❌ | Ungoogled strips them. ShardBrowser calls its absence a reCAPTCHA bot signal. Adding it back means generating a variations header coherent with the claimed version; weigh against ungoogled's purpose. |
| WebAuthn platform authenticator matches device | ❌ | Not patched. `isUserVerifyingPlatformAuthenticatorAvailable()` reflects the host. |
| Widevine | ❌ | Ungoogled ships no CDM. See COVERAGE.md for the runtime-fetch option. |
| Headless markers, CDP side channels | ✅ 110, 120 | V8 inspector preview-getter side channels: not specifically patched; no Clearcote audit finding. |
| Virtual camera (video/image/screen as webcam) | ❌ | 148 only fakes the device list. |
| `Motion.*` CDP domain (hidden from `/json/protocol`) | ⚠️ 130 | camchrome's `Browser.humanizedClick` is visible in the protocol schema; a page can't enumerate it, but a different domain name avoids a known-tool fingerprint. |

## 3. Launcher features (ShardBrowser launcher, MIT)

camchrome has none of these; they are listed so it's clear they are out of the engine's scope:
profile workspace and folders, fingerprint editor, proxy manager with UDP_ASSOCIATE probe,
auto-download of engine and library, team sync, local HTTP API (JWT), MCP server, Python/Node/Rust
SDKs, cookie import/export, extension library, bookmarks, trash, form filler, per-profile icons.
Two camchrome engine patches cover launcher-adjacent needs: 964 portable cookie key (copying a
profile keeps its cookies) and 963/978/984 proxy authentication without CDP interception.

## 4. Network side

ShardBrowser leans on ProxyShard proxies for two things the engine cannot do alone: SOCKS5 UDP
relay (needed for QUIC and WebRTC) and p0f TCP/IP fingerprint spoofing at the exit (so the SYN
looks like the claimed OS). Both are properties of the proxy. With any proxy, camchrome's TCP
SYN is the proxy's, so p0f consistency is a proxy-selection question, not an engine one.

## 5. Priorities

1. ~~**QUIC over SOCKS5 UDP**~~ done in 990 (end-to-end site run pending). The biggest functional gap and the first row
   of the published test matrix.
2. **Mobile behaviour** for Android personas: touch emulation, viewport, sensors, fonts.
3. **macOS persona fonts on 157** (port 956–958) so cross-OS macOS personas stop leaking host fonts.
4. **Humanized typing/scroll** in 130.
5. A **fingerprint library** in the `--fingerprint-profile` format.
6. WebAuthn and `x-client-data`, after deciding whether they fit an ungoogled base.
7. Launcher, API, MCP, sync windows: a separate project on top of the engine.
