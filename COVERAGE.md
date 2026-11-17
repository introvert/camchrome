# What we need to cover

The target is the [Clearcote fingerprint audit](https://www.clearcotelabs.com/audit/checks):
365 checks across 16 groups. **Verified 2026-11-17 on macOS 157: 100/100, 236/236 scored
checks agree, 0 findings** (`tools/stealth_audit.py --seed testseed --interact`). Without
`--interact` the audit scores 218/218 and reports the 18 input checks as not attempted. Each check has its own page at
`https://www.clearcotelabs.com/audit/checks/<slug>`, e.g.
[`canvas-alpha-zero-reads-black`](https://www.clearcotelabs.com/audit/checks/canvas-alpha-zero-reads-black).

Most checks are **self-consistency tests**: they ask the browser the same question through two
APIs and fail if the answers can't both be true. Spoofing one surface without the others fails, so the
patches need one coherent persona feeding every surface. Clearcote's `001-farble-seed-core` and
`002-persona-profile` do this. JavaScript injection also fails the *Native function integrity*
and *Error subsystem* groups, so every change has to be in C++.

Caveats:
- Clearcote wrote both this audit and the patches in `patches/`, so the patches were built to pass
  it. Its paid build also has private patches. Some checks may only pass with those.
- The group-to-patch mapping below is based on patch descriptions (`patches/README.md`) and the
  measured audit result. The 2026-11-17 run scored every check the audit could measure; the
  findings that remained before 987–989 are listed at the end.
- The check list came from the index page on 2026-11-16.

## Groups, what covers them, and gaps

| Group | Checks | Covered by (patches/) | Gaps and where to look |
|---|---|---|---|
| HTTP request vs the runtime | 10 | 010 UA/UA-CH, 092 locale, 961 Sec-CH-DPR, **987/989** | The claimed Chrome major now always equals the engine (987), and the Sec-CH-RTT/Downlink/ECT values match `navigator.connection` (989). Header order, Sec-Fetch and cache headers come from Chromium itself. |
| Screen & display | 24 | 140 screen, 141 media queries, 142 touch/pointer, 962 DPR, 972 window frame, 975 work area | — |
| Navigator identity | 44 | 010, 030 cores/memory, 092, 150 keyboard/connection/battery, 160, 170 voices, **987 engine version + macOS persona** | **Chrome-branding checks** (`widevine-vs-chrome-branding`, `google-voices-…`, `builtin-ai-…`, `licensed-codecs-…`, `webcodecs-aac-…`): an ungoogled build has no Google services. Either claim the Chromium brand, not Google Chrome, or fill them in: Widevine at runtime (see the MIT `fetch-widevine.py`/`widevine.py` in [CloakBrowser](https://github.com/CloakHQ/CloakBrowser)), `proprietary_codecs=true` is already set. Built-in AI has no fix. |
| Engine & OS oracles | 14 | 965 Math.tanh, 040 fonts, 971 system-ui font, 987 macOS persona | `cpu-arch-nan-*` can't be patched: **the persona's CPU arch must match the host's.** Text-scaler, hyphenation and rare-font checks follow the host font stack. Run a persona of the host OS, or ship font bundles; Clearcote's `assets/fonts/` is not vendored here yet. |
| Worker realms | 5 | Persona engine feeds workers (002, 070) | Verify after build. |
| Network & WebRTC | 18 | 100 WebRTC, 973 SOCKS5 UDP, 150 connection, 146 heap, **989 wire hints** | `Sec-CH-RTT/Downlink/ECT` now carry the same persona values `navigator.connection` reports. `emoji-font-vs-os`, `glyph-aa-levels-vs-os`, `monospace-default-vs-os` follow the host OS (see above). WebRTC exit IP needs the proxy set up properly. |
| Clocks & ledgers | 6 | — (stock Chromium behaviour) | Pass by default; keep it that way (no timer patches). |
| Automation surface | 63 | 010 webdriver, 110 Runtime.enable, 120 headless | **Driver-side leaks** (`sourceurl-leak`, `exposed-binding-leaks`, `no-unnamed-native-globals`, `main-world-execution`, `console-preview-proxy-trap`, `automation-globals`, `popup-blocked-without-gesture`, `csp-bypass`) come from Playwright/Puppeteer and their default flags, not the browser. Use **patchright** or **rebrowser-patches**. |
| Error subsystem | 8 | — | Pass as long as nothing is injected in JS. |
| Native function integrity | 17 | — | Same: engine-level patches only, no `addInitScript` stealth. |
| Render & GPU | 65 | 060 canvas, 065 canvas bridge, 070/071 WebGL, 075/967/970/982/983 WebGPU, 955/966/968/969/976/985 WebGL parity, **986 canvas-alpha/grey**, **987 macOS Metal persona**, **988 readback + timing** | All 62 render checks pass. `986` closes `canvas-alpha-zero-reads-black` and `canvas-grey-stays-grey`. `987` makes a macOS persona an Apple Metal device, so ANGLE's shader dialect, caps and limit table match the strings. `988` clips readPixels noise to the framebuffer (sentinel bytes stay untouched), makes PBO and array readback agree, and pays Chrome's ~100 us GPU round trip for the spoofed `getParameter` values. |
| Capability surfaces | 26 | 145 storage, 147 media caps, 148 media devices, 150 sensors, 170 voices, 180 geolocation | `window-globals-vs-baseline` / `flag-only-surfaces-absent`: ungoogled's flag changes may add or remove globals compared with stock Chrome. Compare against a stock Chrome 157. |
| Environment & locale | ~41 | 090 timezone, 092 locale, 170 voices, 160, **987 macOS voices** | macOS personas enumerate the real macOS voice set; empty voices are implausible on a macOS claim and Windows SAPI names are a contradiction. |
| TLS & the network layer | 10 | 210 TLS persona, **987** | BoringSSL is real Chrome TLS and the claimed major now always equals the built major. 210 is only needed when deliberately claiming another major. |
| Connection & locale | 2 | — | IP vs timezone/country is launcher config: derive timezone and locale from the proxy's exit IP. CloakBrowser's MIT `geoip.py` does this. |
| Input & behaviour | 13 | 130 humanized CDP input, 200 agent input path, trusted-input harness | All 18 input checks pass when driven with trusted CDP input that aims inside controls, holds for a human interval, reports `PointerEvent.pressure` 0.5 while a button is held and uses real key events (`tools/stealth_audit.py --interact`). A naive driver (element centre, instantaneous press/release, scripted `change`) fails six of them; use the harness or patchright. |

## Other repos worth taking from

Checked on 2026-11-16 (license, last push).

**Driver side (the automation-surface gaps)**
- [Kaliiiiiiiiii-Vinyzu/patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright) — Apache-2.0, active (2026-11-13). Drop-in Playwright that fixes `Runtime.enable`, console, exposed bindings, isolated-world execution and leaky default flags. **Recommended** as the driver.
- [rebrowser/rebrowser-patches](https://github.com/rebrowser/rebrowser-patches) — MIT, active (2026-11-02). The same class of fixes as patches to stock Puppeteer/Playwright.

**Input & behaviour**
- [CloakHQ/CloakBrowser](https://github.com/CloakHQ/CloakBrowser) `cloakbrowser/human/` — the **wrapper is MIT** (only the binary is proprietary). Mouse curves, keyboard timing, scroll; plus `geoip.py` and `widevine.py`.
- [Xetera/ghost-cursor](https://github.com/Xetera/ghost-cursor) — MIT, active. Human-like mouse paths (Bezier + Fitts's law) for Puppeteer.
- [daijro/camoufox](https://github.com/daijro/camoufox) — MPL-2.0, Firefox-based. Its humanized cursor and persona design are a good reference; the code doesn't port to Chromium directly.

**Engine patches (reference / second source)**
- [adryfish/fingerprint-chromium](https://github.com/adryfish/fingerprint-chromium) — BSD-3. 18 patches on Chromium 148; the base Clearcote grew from.
- [brave/brave-core](https://github.com/brave/brave-core) — MPL-2.0. "Farbling" for canvas, audio, WebGL, fonts, hardwareConcurrency: the original design Clearcote's seed model follows. MPL is file-level copyleft: copied code keeps MPL notices.
- [uazo/cromite](https://github.com/uazo/cromite) — **GPL-3.0**. Many anti-fingerprinting patches, but GPL code would make a distributed binary GPL. Read as reference; don't copy into this BSD repo.

**Persona data**
- [daijro/browserforge](https://github.com/daijro/browserforge) — Apache-2.0. Generates realistic, internally consistent header and fingerprint sets from real-world distributions; useful for building personas.
- [Vinyzu/chrome-fingerprints](https://github.com/Vinyzu/chrome-fingerprints) — GPL-3.0 data, last updated 2024. Clearcote can import it; keep it out of this repo.

**Not usable**
- [pocchian/fingerprint-chromium-arm-mac](https://github.com/pocchian/fingerprint-chromium-arm-mac) — no license, so its code can't be reused. Read only.
- [arman-bd/chromiumfish](https://github.com/arman-bd/chromiumfish) — license unclear on GitHub. Check before using.
- [imputnet/helium](https://github.com/imputnet/helium) — GPL-3.0 and privacy-focused, not anti-fingerprinting.

## Findings fixed in 986–989

Before these patches the audit reported 16 findings (score 93) on the 156 engine. They were:

- Chrome 151 claimed on the engine (156 at the time; now the version is derived from the engine itself, so the same class of drift cannot recur).
- Windows ANGLE/D3D11 GPU strings, caps, extensions and shader dialect under a macOS claim.
- `0xA780` device id (ANGLE zero-pads to 8 hex digits).
- `navigator.cpuPerformance` "ultra" from the host CPU model while the persona reported 6 cores.
- `hardwareConcurrency` below the host's measured worker parallelism.
- `devicePixelRatio` 1 while layout rasterised at 2.
- Windows SAPI voices under a macOS claim.
- `Sec-CH-Downlink` 1.55 on the wire vs 10 in JS.
- readPixels rewriting sentinel bytes outside the framebuffer.
- readPixels into a `PIXEL_PACK_BUFFER` disagreeing with the array exit.
- `getParameter` answering spoofed values from memory (7,000+/ms vs Chrome's 8–13/ms).
- WebGPU architecture spelling `gen-12-lp` and Intel subgroup size 32.

## Design rules that come out of this

1. One persona feeds every surface. Never spoof one API on its own.
2. C++ only. No JS injection, no `addInitScript` stealth.
3. Claimed Chrome major = built major. Claimed CPU arch = host arch. Prefer persona OS = host OS
   unless the 96x cross-OS patches and font bundles are in place.
4. Use patchright (or rebrowser-patches) as the driver, never stock Playwright.
5. Derive timezone, locale and geolocation from the proxy's exit IP.
6. Acceptance test: run the full audit against each build and record the failing checks here.
