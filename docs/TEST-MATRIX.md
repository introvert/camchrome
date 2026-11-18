# External check sites: test matrix

The public sites ShardBrowser publishes results for, what each one tests, the result to aim
for, and what camchrome has recorded. Run them with `tools/site_checks.py`; it saves each
page's text, a screenshot and the extracted values under `build/site-checks/`.

## How to run

You need a camchrome build, a desktop session (headful, with a GPU) and, for the network
rows, a residential proxy that supports **SOCKS5 UDP ASSOCIATE** (an IP literal or a hostname,
since 990).

```bash
tools/site_checks.py --list                      # sites and pass conditions

tools/site_checks.py --seed demo \
  --proxy socks5://203.0.113.10:1080 --socks5-credentials user:pass --socks5-udp \
  --timezone Europe/Ljubljana --lang sl-SI      # match the exit country

tools/site_checks.py --only browserleaks-quic,twilio ...
```

Rules that decide most of these results, whatever the engine does:

- Timezone, language and geolocation must match the proxy's exit country (`--timezone`,
  `--lang`, `--fingerprint-location`). camchrome does not derive them from the proxy.
- fingerprint.com, pixelscan and reCAPTCHA score the IP. A datacenter or abused proxy
  fails them with a perfect fingerprint.
- Headless or GPU-less runs fail the render and automation checks by design.
- With `--socks5-udp` (991) WebRTC learns the proxy's exit IP itself; pass `--webrtc-ip` only
  for a proxy without UDP, and then it must be the proxy's real exit IP.
- First check that the proxy relays UDP at all: `tools/socks5_udp_proxy.py` documents the
  protocol, and a proxy that answers UDP ASSOCIATE with "command not supported" gives QUIC false
  and no WebRTC UDP whatever the engine does.
- The persona's CPU architecture must match the host's (COVERAGE.md).

## Matrix

| # | Site | What it tests | Pass condition (ShardBrowser's published result) | camchrome patches involved | Expected for camchrome today | Recorded result |
|---|---|---|---|---|---|---|
| 1 | [browserleaks.com/quic](https://browserleaks.com/quic) | Whether the page loads over HTTP/3 and the QUIC/TLS fingerprint (JA4) | QUIC `True`; JA4 matches real Chrome of the claimed version; MTU 1232 over the SOCKS5 UDP relay | 210 (TLS), 973 + **990** (QUIC over the UDP relay) | **Expected QUIC True** with `--socks5-udp` through a proxy that relays UDP (990). The first visit loads over TCP and learns `Alt-Svc: h3`; the page's own HTTP/3 probe and later loads use QUIC through the relay. JA4/QUIC transport parameters are BoringSSL/quiche stock, so they match Chrome 157. MTU: camchrome sends Chrome's normal 1250-byte initial packets; the relay adds 7+len(host) bytes of SOCKS5 header between browser and proxy only, so what the site measures is the proxy's path. A proxy that refuses UDP gives QUIC False (TCP fallback). | Not run |
| 2 | [networktest.twilio.com](https://networktest.twilio.com) | WebRTC connectivity: TURN UDP, TCP, TLS, voice; host IP exposure | Every probe `Pass`, no real-IP leak | 100 (candidates), 973 (UDP via SOCKS5), **990** (named proxies, no private relaying), **991** (exit IP via STUN) | Should pass with `--socks5-udp` through a proxy that allows UDP ASSOCIATE, named or IP literal. The srflx candidate is the exit IP (991), host candidates are hidden. TCP/TLS TURN go through the normal proxied TCP path. | Not run |
| 3 | [fingerprint.com/demo](https://fingerprint.com/demo) | Bot, VPN, DevTools, browser tampering, incognito | Bot / VPN / DevTools / tampering all `Not detected` | all persona patches; 110, 120 | Bot and tampering: expected to pass (engine-level, no JS injection). VPN depends on the proxy and on timezone/locale matching the exit. DevTools: don't open DevTools during the run. | Not run |
| 4 | [deviceandbrowserinfo.com/are_you_a_bot](https://deviceandbrowserinfo.com/are_you_a_bot) | Fingerprint and CDP-automation bot tests | Human; no CDP or automation signal | 010, 110, 120, 130 | Expected human when driven by raw CDP (`tools/cdp.py`) or patchright. Stock Playwright/Puppeteer add their own tells. | Not run |
| 5 | ProxyShard browser checker ([docs](https://docs.proxyshard.com/eng/our-products/ip-checker)) | 9 categories: audio, automation, canvas, cpu, device, gpu, network, other, performance | `No issues detected` in all 9 | 020, 060, 070/075, 030, 140–150, 146, 989 | Expected clean on a host-OS persona. The exact URL was not confirmed (the site was unreachable from here); `site_checks.py` defaults to `https://proxyshard.com/ip-checker`; pass `--proxyshard-url` with the right one. | Not run |
| 6 | [pixelscan.net](https://pixelscan.net) | Fingerprint consistency, masking, proxy, automation | Fingerprint **consistent**; no proxy / automation detected | all; 040 fonts, 987 version | Consistency expected (same invariants as the Clearcote audit). "Proxy detected" depends on the IP and on timezone/language matching. | Not run |
| 7 | [browserscan.net](https://www.browserscan.net) | Authenticity score; locale vs IP | Authenticity **100%**, locale honoured | 090, 092, all persona patches | Expected 100% with matching timezone/language. Ungoogled-specific signals (no Widevine, no Google voices) may cost points. | Not run |
| 8 | [antcpt.com/score_detector](https://antcpt.com/score_detector/) | reCAPTCHA v3 score | **0.9** | all; humanized input 130 | IP reputation and Google cookies dominate. ShardBrowser attributes part of its score to the `x-client-data` header, which camchrome (ungoogled) does not send; expect 0.7 or lower on a fresh profile until that's measured. | Not run |
| — | [clearcotelabs.com/audit](https://www.clearcotelabs.com/audit) | 365 coherence checks | 100/100 | all | **100/100, 236/236** on macOS arm64 157 (AUDIT.md, 2026-11-17). | Recorded in AUDIT.md |

## Recorded run: macOS arm64, 2026-11-18

camchrome 157.0.8103.78 (`v157.0.8103.78-camchrome.1` plus 995/996), headful on an Apple M4 Mac,
`--fingerprint=demo`, macOS persona, through a residential SOCKS5 proxy with UDP ASSOCIATE
(Geonode, sticky sessions exiting in Brazil), `--socks5-udp --timezone=America/Sao_Paulo
--lang=pt-BR --fingerprint-location=<exit city>`. The sticky sessions rotate their exit IP after a
few minutes, so the IP differs between sites.

| # | Site | Result | Notes |
|---|---|---|---|
| 1 | browserleaks.com/quic | **QUIC True**, over HTTP/3 through the relay; exit IP shown is the proxy's | JA4 `q13d0312h3_55b375c5d22e_178839b6cec1`; MTU estimate 1280 |
| 2 | networktest.twilio.com | Pass | skipped from later runs on request |
| 3 | fingerprint.com/demo | not judged | the Smart Signals are not in the page text; needs a dedicated extractor |
| 4 | deviceandbrowserinfo.com/are_you_a_bot | **"You are human!"**, isBot false | |
| 5 | ShardX / ProxyShard IP checker | audio, automation, device, performance clean; see below | not used as a pass criterion |
| 6 | pixelscan.net/fingerprint-check | "No proxy detected", "No automated behavior detected"; **"Masking detected"** with any persona | dropped on request; see below |
| 7 | browserscan.net | **Authenticity 100%** | |
| 8 | antcpt.com/score_detector | not run | the site returned a PHP error ("Failed to open stream") |
| — | Clearcote audit | **100/100**, 236/236, no findings | `tools/stealth_audit.py --seed testseed --interact` |

**ShardX findings, and what was done about each:**

- *canvas: measureText width vs DOM width.* A checker bug: its DOM probe inherits the site's own
  CSS `letter-spacing: -0.32px` (20 characters × −0.32 px is exactly the reported gap). Stock
  Google Chrome gets the same finding on that page.
- *canvas: putImageData/getImageData round trip, 1×1 mosaic; gpu: WebGL gradient readback.* These
  are camchrome's per-site noise (farbling) and go away with `--cc-no-farble`. The WebGL readback
  path ignored that switch until 996.
- *other: brands lack "Google Chrome".* camchrome claims the Chromium brand by default (an
  ungoogled build has no Widevine or Google services). `--fingerprint-brand=chrome` claims
  Google Chrome; 995 keeps every surface consistent with that.
- *other: no Google TTS voices, network: no Widevine.* Ungoogled-build properties.
- *other: form validation text in English, Intl locale pt-BR.* On macOS, Chrome takes its UI
  language from the system, not `--lang`; launch with `-AppleLanguages '(pt-BR)'`.
- *cpu: hardwareConcurrency 12 vs measured ~8* (a "weak signal"): the persona claimed more cores
  than the 10-core host. Use `--fingerprint-hardware-concurrency`.
- *network: p0f TCP fingerprint says Linux, browser says macOS.* The proxy server's own TCP stack;
  no browser can change it.

**Pixelscan "Masking detected"** appears with any `--fingerprint` persona and not without one
(camchrome with no flags is "consistent", like stock Chrome). It stays with the real core count,
GPU string, WebGL backend and screen, and with all noise off (`--cc-no-farble
--disable-fingerprint-noise --disable-canvas-noise`), so it is none of those. Open item.

## Running it in CI

`.github/workflows/full-build.yml` builds the browser and, with **run_sites** ticked, runs this
matrix through the proxy in the `CAMCHROME_SOCKS5_PROXY` secret. [FULL-BUILD.md](FULL-BUILD.md)
lists the runner and secrets it needs.

## Why nothing is recorded yet

This review ran in a Linux cloud container on 2026-11-17. The sites could not be run from
there, for three separate reasons:

1. **No camchrome binary.** A full Chromium build takes hours on a large machine; the
   container (4 cores, ~20 GB free disk) compiled the patched files to prove the series
   builds on Linux, but did not link a browser. The repo has no release build either.
2. **Network policy.** The container's outbound proxy refused every one of these hosts
   (HTTP 403 on CONNECT for browserleaks.com, networktest.twilio.com, fingerprint.com,
   deviceandbrowserinfo.com, pixelscan.net, browserscan.net, antcpt.com, proxyshard.com and
   clearcotelabs.com).
3. **No UDP and no residential SOCKS5 proxy.** Rows 1 and 2 need UDP through a SOCKS5
   relay; the container has neither.

The harness itself was checked: `tools/site_checks.py` drove a stock Chromium through a local
page with the same open → click → wait → extract → screenshot path and parsed the values.

When you run it, fill in the "Recorded result" column with the date, seed, persona OS, proxy
type and the values from `build/site-checks/summary.json`.
