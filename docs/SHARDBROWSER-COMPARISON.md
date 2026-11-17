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
| **Bundled fingerprint library** | 220 profiles from real-device samples (31 mac, 120 Windows, 19 Linux, 50 Android) | ⚠️ **Tooling ready, no devices yet.** `tools/fingerprint_library.py` serves a capture page for real devices, checks each capture for coherence (real GPU, OS-matching ANGLE backend, touch on phones, Chrome's memory grid, WebGPU vs WebGL vendor), stores it in `profiles/library/<os>/` in the `--fingerprint-profile` format and picks one by OS/GPU, stable per seed. The library itself is empty until real devices are captured; seed-derived personas remain the default. | Capture the devices (see `profiles/README.md`). Sampled data (e.g. browserforge) was not used: each entry should be a machine that exists. |
| **Mobile profiles (touch input, sensors, phone fonts)** | ✅ behaves like a phone: mouse becomes touch, mobile viewport, orientation, motion sensors, handset fonts | ✅ **Behaves like a phone since 992.** `--fingerprint-platform=android` gives an Android UA/UA-CH, `mobile=true`, portrait orientation, coarse pointer, touch points and mobile viewport prefs (002, 142), Android WebGL limits (976); 992 adds mouse-to-touch conversion on the main frame (also for CDP clicks), a held-phone deviceorientation/devicemotion stream, and the AOSP handset font set. Still missing: the Generic Sensor API classes (`Accelerometer`, `Gyroscope`) report no sensor, Android DRM robustness, and the window must be launched phone-sized. | Verify on a full build with `tools/fingerprint_selftest.py --platform android` and the check sites. |
| **Synchronised windows** | ✅ one window drives, the others repeat in their own profiles | ✅ **`tools/camauto.py sync`.** The first window leads; its clicks, typing, Enter/Tab, scrolls and typed URLs are recorded from an isolated world and replayed in every follower (own profile and persona via `--flags-each`) through the engine's human input, each with its own reaction delay and motion. Password fields are never mirrored. | Needs a full build to run end to end (followers use 130/993). |
| **Built-in automation** | ✅ visual projects, recording, fleet runs, WASM modules, local API + MCP | ⚠️ **Local API, MCP, recorder and replay; no visual editor or fleet runner.** `tools/camauto.py` adds a token-protected local HTTP API (launch with a library device or persona and proxy, then navigate/click/type/scroll/drag/text/screenshot/replay), an MCP server on stdio with the same tools, a recorder that writes replayable JSON flows (passwords become `${SECRET}`), and replay with recorded human timing. Page reads run in an isolated world and the recorder reports through a binding bound to that world only, so the page sees neither; `Runtime.enable` is never sent. Plus raw CDP (110) and the in-browser LLM agent (200). | A visual flow editor and a fleet scheduler would sit on the same API. |
| **Human input engine** | ✅ in the browser itself, used by hand and by scripts (pointer, typing with rhythm and typos, wheel per platform, touch gestures) | ✅ **Pointer, typing, scrolling and drags since 993.** 130 adds `Browser.humanizedClick` / `humanizedClickSelector` (Bezier path, Gaussian jitter, eased timing) and 200 routes the agent's clicks through it. 993 adds `Browser.humanizedType` (per-key rhythm, Shift handling, rollover, pauses, corrected typos), `humanizedScroll` (wheel notches on Windows/Linux, trackpad momentum on macOS, finger swipes on Android) and `humanizedDrag` (a touch swipe on Android personas, via 992). All are scripted through CDP; there is no recorder or by-hand use yet. | Verify on a full build with `tools/network_selftest.py --only page`; feed them from the automation layer. |

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
| Mobile behaviour | ✅ | 992; see section 1. |
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
2. ~~**Mobile behaviour**~~ done in 992 (touch, held-phone sensors, handset fonts; full-build run pending).
3. **macOS persona fonts on 157** (port 956–958) so cross-OS macOS personas stop leaking host fonts.
4. ~~**Humanized typing/scroll**~~ done in 993 (full-build run pending).
5. A **fingerprint library** in the `--fingerprint-profile` format: tooling done (`tools/fingerprint_library.py`), real-device captures needed.
6. WebAuthn and `x-client-data`, after deciding whether they fit an ungoogled base.
7. ~~Launcher, API, MCP, sync windows~~: `tools/camauto.py` (API, MCP, recorder, replay, sync); a GUI launcher is still open.
