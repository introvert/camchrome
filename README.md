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
| ungoogled-chromium-macos overlay | `156.0.8078.79-1.1` | pinned, built and audited on macOS arm64 |
| Fingerprint patches (`patches/`) | 66 patches | verified against 156 (macOS); 157 rebase open |
| Clearcote stealth audit | 365 checks | **100/100 · 237/237 scored checks agree · 0 findings** |

The macOS overlay published `157.0.8103.78-1.1` while this tree was verified, so 157 is
the next rebase target (see `NOTES.md`). The shipped/verified binary is Chromium
`156.0.8078.79`, the newest macOS tag whose patch series has been fully rebased here.
Linux/Windows use the 157 base; their rebase is open.

The patches in `patches/` come from
[Clearcote](https://github.com/clearcotelabs/clearcote-browser) at commit
`ac40bdc` (BSD-3-Clause; see `LICENSE.clearcote` and `CREDITS.md`), rebased from 151 to
156 and extended with patches 986–989 (see below). They cover canvas, WebGL, WebGPU,
audio, fonts, GPU info, screen, timezone/locale, WebRTC, client hints, `navigator.webdriver`,
headless and CDP `Runtime.enable` signals, and more.

### What 986–989 add

| Patch | Fix |
|---|---|
| `986-canvas-alpha-and-grey-coherence` | alpha=0 pixels read black; grey stays grey |
| `987-engine-version-and-macos-persona` | every version surface derives from the engine; macOS personas get Apple Silicon GPU/screen/voices/devices |
| `988-webgl-readback-and-timing` | readPixels clips to the framebuffer, PBO and array exits agree, and spoofed `getParameter` values pay Chrome's GPU round-trip latency |
| `989-cpu-tier-and-netinfo-hints` | `navigator.cpuPerformance` follows the persona's cores; `Sec-CH-RTT/Downlink/ECT` match `navigator.connection` |

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
tools/stealth_audit.py --seed demo                      # 219/219 engine + fingerprint checks
tools/stealth_audit.py --seed demo --interact           # + the 18 input checks -> 237/237
tools/stealth_audit.py --seed demo --signals            # offline signal dump, no network
```

Without `--interact` the audit reports the 18 input checks as "not attempted" and scores
219/219. With `--interact` the harness completes its five optional input steps using
trusted CDP input (it aims inside each control, holds for a human interval, sends
`PointerEvent.pressure` 0.5 while a button is held and uses real key events), which lets
the audit score those checks too: 237/237 agree, 0 findings, score 100/100.

Results are written to `build/stealth-audit.txt` / `.json` (audit) and
`build/stealth-signals.json` (`--signals`).

## Rebasing the patches to a new Chromium

1. Bump `UG_TAG` and `UGW_TAG` (and `UGM_TAG` when moving macOS) in `UPSTREAM`.
2. `scripts/00-fetch-source.sh && scripts/01-apply-patches.sh --check` lists the failing patches.
3. Fix each in order: apply it with `patch -p1 --merge`, resolve the conflicts in `build/src`, then
   regenerate the patch with `git diff` (run `git init && git add -A && git commit` in
   `build/src` after step 2 so you have a baseline to diff against).
4. Update `PATCHES_BASE` in `UPSTREAM`.

## More

`NOTES.md` has the background, the patch repos compared, what has been verified, and the open work.

`COVERAGE.md` maps the 365 checks of the [Clearcote fingerprint audit](https://www.clearcotelabs.com/audit/checks)
to the patches that cover them, lists the gaps, and names other repos to take patches from.

## Credits

See `CREDITS.md`.
