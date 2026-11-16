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
| ungoogled-chromium-macos overlay | `156.0.8078.79-1.1` | pinned; macOS lags upstream by one major |
| Fingerprint patches (`patches/`) | written for Chromium `151.0.7922.108` | **need rebasing to 157** |
| macOS build config (`config/args.mac.gn`) | — | untested |

The patches in `patches/` come from
[Clearcote](https://github.com/clearcotelabs/clearcote-browser) at commit
`ac40bdc` (BSD-3-Clause; see `LICENSE.clearcote` and `CREDITS.md`). They cover canvas, WebGL, WebGPU,
audio, fonts, GPU info, screen, timezone/locale, WebRTC, client hints, `navigator.webdriver`,
headless and CDP `Runtime.enable` signals, and more. Six Chromium majors have passed since
they were written, so some will not apply to 157 until they are rebased.

## Requirements

- Linux x64 (the tested path) or macOS with Xcode
- ~32 GB RAM for the official ThinLTO link, ~120 GB free disk, many CPU cores, several hours
- `git`, `python3`, `ninja`, `xz`, `curl`; Linux also needs Chromium's
  [build deps](https://chromium.googlesource.com/chromium/src/+/main/docs/linux/build_instructions.md#install-additional-build-dependencies)

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

## Rebasing the patches to a new Chromium

1. Bump `UG_TAG` and `UGW_TAG` (and `UGM_TAG` when the macOS overlay catches up) in `UPSTREAM`.
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
