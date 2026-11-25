# Upstream sources: what to watch and what to mine

As of 2026-11-18. `tools/upstream_watch.py` prints the live state of everything in sections 1
and 3 (pins vs newest tags, Chrome stable, Clearcote movement, reference repos), and
`.github/workflows/upstream-watch.yml` runs it every Monday and fails when a pin is behind.

## 0. Update checklist (run this first)

One command answers most of it:

```bash
tools/upstream_watch.py --no-reference     # pins, next-major PRs, Chrome/CfT/Edge stable, tarball, Clearcote
```

Then, by hand, in this order:

| # | Question | Where to look | Command / URL |
|---|---|---|---|
| 1 | Is there a new ungoogled tag? | ungoogled-chromium tags | `gh api repos/ungoogled-software/ungoogled-chromium/tags --jq '.[0:3][].name'` |
| 2 | Is the next major in progress, and is it buildable? | Open "Update to Chromium N" PRs and their comments (reviewers post build failures there) | `gh pr list -R ungoogled-software/ungoogled-chromium --search "Update to Chromium"`, then `gh pr view <n> -R ungoogled-software/ungoogled-chromium --comments` |
| 3 | Do the overlays have the same version? | Windows overlay (applied on Linux too) and macOS overlay: tags and update PRs | `gh api repos/ungoogled-software/ungoogled-chromium-windows/tags --jq '.[0].name'` (same for `-macos`) |
| 4 | What is Chrome stable per OS? | Version history API | `curl -s "https://versionhistory.googleapis.com/v1/chrome/platforms/win/channels/stable/versions?pageSize=1"` (`mac`, `linux`, `android`) |
| 5 | Does the source exist? | Chromium official tarballs | `curl -sI https://commondatastorage.googleapis.com/chromium-browser-official/chromium-<ver>-lite.tar.xz` |
| 6 | What security fixes are in it? | [Chrome Releases blog](https://chromereleases.googleblog.com/) | Read every stable post since the pinned version; note "exploit in the wild" CVEs |
| 7 | Stock reference binary for the comparison tests | Chrome for Testing | `https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json`; download `.../chrome-for-testing-public/<ver>/linux64/chrome-linux64.zip` into `build/ref/<ver>/` |
| 8 | Edge build for `--fingerprint-brand=edge` (995) | Edge updates API | `curl -s https://edgeupdates.microsoft.com/api/products` (Product `Stable`, Platform `Windows`) |
| 9 | Did the vendored patch source move? | Clearcote `patches/` | `git log ac40bdc..HEAD -- patches` in a clone of clearcotelabs/clearcote-browser |
| 10 | New detection vectors? | BotBrowser CHANGELOG, Clearcote audit check index, CreepJS commits | links in section 2 |
| 11 | Faster rebases ahead of ungoogled | Helium (tracks stable on an ungoogled base; GPL, read only) | https://github.com/imputnet/helium/commits |

After moving a version: edit `UPSTREAM`, run `scripts/01-apply-patches.sh --check`, rebuild, then
`tools/fingerprint_selftest.py`, `tools/ua_selftest.py` and `tools/render_compare.py` against
the matching Chrome for Testing binary.

## 1. Pinned upstreams (a change here means a rebase or a patch pull)

| Source | Pinned | Newest on 2026-11-18 | Watch for | How |
|---|---|---|---|---|
| [ungoogled-chromium](https://github.com/ungoogled-software/ungoogled-chromium) | `157.0.8103.78-1` | `157.0.8103.78-1` | A new tag. It pins the Chromium tarball for Linux and Windows, so every security release arrives through it. | Tags; `tools/upstream_watch.py` |
| [ungoogled-chromium-windows](https://github.com/ungoogled-software/ungoogled-chromium-windows) | `157.0.8103.78-1.1` | same | Tag matching the base; changes to the Windows-host tooling patches that `01-apply-patches.sh` skips on Linux. | Tags |
| [ungoogled-chromium-macos](https://github.com/ungoogled-software/ungoogled-chromium-macos) | `157.0.8103.78-1.1` | same | Tag matching the base; new build prerequisites (157 added rustc-dev and Crubit). | Tags |
| Chromium tarball (`commondatastorage.googleapis.com/chromium-browser-official`) | `157.0.8103.78-lite` | — | Follows the ungoogled tag; nothing to do separately. | — |
| [Clearcote](https://github.com/clearcotelabs/clearcote-browser) `patches/` | `ac40bdc` | `c364b78`: 11 commits since the pin, **none in `patches/`** (last patch change 2026-10-28; their tree is still Chromium 151) | Any commit touching `patches/`. Their engine features since r51 (persona service) are in the licensed build only, so expect few public patch changes. Their SDK READMEs still announce engine switches (`--fingerprint-noise-per-profile` r60, flat-area noise, WebGL read margin r57, persona colour scheme, persona off the command line), which are useful as a feature list. | `git log ac40bdc..HEAD -- patches` |

**Google Chrome stable** is 158.0.8127.88 on Linux and 159.0.8154.34 on Windows and macOS
(2026-11-25); the engine is 158.0.8127.88. A claimed major one behind stable is common and not a
tell by itself, but the gap widens every release: rebase as soon as ungoogled-chromium tags 159. Sources:
[Chrome Releases blog](https://chromereleases.googleblog.com/) (security fixes per release,
read it for each stable update) and the version history API
`versionhistory.googleapis.com/v1/chrome/platforms/<win|mac|linux|android>/channels/stable/versions`.

**Edge**: `--fingerprint-brand=edge` reports a hard-coded Edge build per Chromium major
(157 → `157.0.4341.60`, patch 995). Check it against
[Edge release notes](https://learn.microsoft.com/en-us/deployedge/microsoft-edge-relnote-stable-channel)
on every rebase.

**Security fixes between rebases.** ungoogled-chromium only re-tags when Google ships a new
stable build. Between tags, read the Chrome Releases blog; if a stable update fixes an
in-the-wild exploit and ungoogled has not tagged within a few days, consider cherry-picking the
fix (the blog links each CVE's crbug, and the commit is in the Chromium release branch).

## 2. Detection side (what checks the engine must keep passing)

| Source | What it gives | Use |
|---|---|---|
| [Clearcote audit](https://www.clearcotelabs.com/audit/checks) | 365 coherence checks; the acceptance test | Re-read the check index after each rebase; new slugs are new checks (`COVERAGE.md` maps the 2026-11-16 list). |
| [CreepJS](https://github.com/abrahamjuliot/creepjs) (MIT) | Lie detection (prototype tampering, worker vs window, font and emoji metrics) | Source of probes for `tools/fingerprint_selftest.py`. Last commit 2026-06. |
| [FingerprintJS](https://github.com/fingerprintjs/fingerprintjs) (MIT) | The signal list a common library hashes | Each source in `src/sources/` is a surface that must be persona-coherent. |
| [BotD](https://github.com/fingerprintjs/BotD) | Automation tells | `src/detectors/`; cross-check against 110/120 and `camauto.py`. |
| browserscan, pixelscan, fingerprint.com, deviceandbrowserinfo, browserleaks | Live sites | `tools/site_checks.py` / `docs/TEST-MATRIX.md`. |
| [BotBrowser CHANGELOG](https://github.com/botswin/BotBrowser/blob/main/CHANGELOG.md) | A closed engine, but its changelog names every inconsistency it fixed, release by release (it is on Chromium 158.0.8127.18 since 2026-11-17) | Read each entry as a candidate check. See section 4 for the ones camchrome does not cover yet. |

## 3. Repositories to analyze for further fingerprint work

Checked 2026-11-18 (newest tag, last commit). License decides whether code can be copied into
this BSD-3 repo or only read.

**Engine patch sets (Chromium)**

| Repo | Latest | License | Why |
|---|---|---|---|
| [adryfish/fingerprint-chromium](https://github.com/adryfish/fingerprint-chromium) | `150.0.7871.186`, 2026-09-27 | BSD-3 | Clearcote's base. Now on 150 (NOTES.md had 148). Diff its `patches/extra/fingerprint/` against ours for anything Clearcote dropped. |
| [arman-bd/chromiumfish](https://github.com/arman-bd/chromiumfish) | `v151.0.7922.170`, 2026-08-19 | MIT (COVERAGE.md had it as unclear; the repo now carries an MIT LICENSE) | TLS ClientHello pinning to the claimed version (compare with 210), canvas bridge (compare with 065), GPS persona in agent mode. |
| [brave/brave-core](https://github.com/brave/brave-core) | `v1.102.19`, daily | MPL-2.0 (file-level) | Farbling reference for every surface; `browser/farbling/*_browsertest.cc` are good test ideas. Copied files keep MPL headers. |
| [uazo/cromite](https://github.com/uazo/cromite) | `v153`, 2026-09-19 | GPL-3.0 | Read only. Many anti-fingerprinting patches (Android-first, useful for the Android persona). |
| [imputnet/helium](https://github.com/imputnet/helium) | `0.22.1`, 2026-11-18 | GPL-3.0 | Read only. Tracks Chromium stable quickly on an ungoogled base: its rebase notes are useful when moving to a new major before ungoogled does. |

**Other engines and closed products (read their docs, not code)**

| Repo | Latest | License | Why |
|---|---|---|---|
| [daijro/camoufox](https://github.com/daijro/camoufox) | `v159.0-beta.42`, 2026-11-16 | MPL-2.0 | Firefox, so the code does not port, but its surface list (MaskConfig) is the widest public checklist, and its human cursor is a good reference. |
| [botswin/BotBrowser](https://github.com/botswin/BotBrowser) | Chromium 158.0.8127.18, 2026-11-17 | closed engine | Changelog = list of detection vectors (see section 4). |
| [ProxyShard/ShardBrowser](https://github.com/ProxyShard/ShardBrowser) | `v2.1.5`, 2026-11-11 | MIT launcher, closed engine | Feature parity (`docs/SHARDBROWSER-COMPARISON.md`). |
| [CloakHQ/CloakBrowser](https://github.com/CloakHQ/CloakBrowser) | `v0.7.2`, 2026-11-17 | MIT wrapper, closed engine | `human/` input timing, `geoip.py` (timezone and locale from the exit IP), `widevine.py`. |

**Drivers (the automation surface the engine cannot fix)**

| Repo | Latest | License | Why |
|---|---|---|---|
| [Kaliiiiiiiiii-Vinyzu/patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright) | `v1.64.1`, 2026-11-12 | Apache-2.0 | Recommended driver; each release's patch list is a list of CDP leaks. |
| [rebrowser/rebrowser-patches](https://github.com/rebrowser/rebrowser-patches) | `v1.1.3`, 2026-11-02 | MIT | Same class of fixes for Puppeteer/Playwright. |
| [ultrafunkamsterdam/nodriver](https://github.com/ultrafunkamsterdam/nodriver) | 2026-10-26 | AGPL-3.0 | Raw-CDP driver design (like `tools/cdp.py`). Read only. |

**Persona data (for `profiles/library/` and seed distributions)**

| Repo | Latest | License | Why |
|---|---|---|---|
| [apify/fingerprint-suite](https://github.com/apify/fingerprint-suite) | `v2.1.90`, 2026-11-18 | Apache-2.0 | Header and fingerprint generators trained on real traffic, updated almost daily; its Bayesian network data is the best open source of realistic screen/GPU/memory/core combinations to check the seed derivation against. Not in COVERAGE.md before. |
| [daijro/browserforge](https://github.com/daijro/browserforge) | `v1.2.4`, 2026-10-29 | Apache-2.0 | Same idea, Python. |
| [Vinyzu/chrome-fingerprints](https://github.com/Vinyzu/chrome-fingerprints) | 2024-12 | GPL-3.0 data | Stale; keep out of the repo. |

## 4. Gaps these sources point at (not covered by any patch yet)

From BotBrowser's and Clearcote's 2026-10/11 release notes, checked against `patches/`:

| Gap | Seen in | State in camchrome | Test |
|---|---|---|---|
| **Scrollbar geometry per persona OS.** Windows/Linux have 15-17 px classic scrollbars; macOS (trackpad) and Android use overlay scrollbars (0 px). A cross-OS persona keeps the host's, which `innerWidth - clientWidth` shows. | BotBrowser 2026-11-13 | 999 gives Android personas overlay scrollbars on Linux/Windows hosts. A desktop persona on a Mac host with overlay scrollbars is still open (Mac-side change). | `scrollbar-width-matches-persona-os` in `fingerprint_selftest.py` (runs with `--platform`). |
| **Colour scheme from the persona.** `prefers-color-scheme`, system colours and `Sec-CH-Prefers-Color-Scheme` follow the host theme. Not incoherent (all three agree), but every persona on a light host is light. | Clearcote r32, BotBrowser 2026-11-06 | Host value. | New `color-scheme-header-equals-media` guards the coherence; a persona colour scheme would need a patch. |
| **Persona off the command line.** `--fingerprint=…`, proxy credentials (`--socks5-credentials`) and the profile blob are readable by any local user in `/proc/<pid>/cmdline`. | Clearcote r32 (`CLEARCOTE_PERSONA_ARGS`) | On the command line. | — |
| **Window focus across synced windows.** Followers in `camauto.py sync` are background windows: `document.hasFocus()` false and `visibilitychange` events a real user would not produce. | BotBrowser 2026-11-17 | Not handled. | — |
| **Fake camera from a file with persona device names.** | BotBrowser 2026-10-23, ShardBrowser | 148 fakes the device list only. | — |
| **Noise per profile instead of per site** (one canvas/WebGL result on every site). | Clearcote r60 | Per eTLD+1 only (001). A switch would be small. | — |
| **WebGL2 multisample limits** (`MAX_SAMPLES`, `getInternalformatParameter(SAMPLES)`) agreeing across contexts. | BotBrowser 2026-11-16 | `MAX_SAMPLES` is in the persona; `getInternalformatParameter` is not patched. | — |
