# Full build, tests and the site matrix

`.github/workflows/full-build.yml` builds the whole browser, runs every local test against it and,
if asked, runs the public check sites ([TEST-MATRIX.md](TEST-MATRIX.md)) through a residential
SOCKS5 proxy. It runs only when started by hand (Actions → **full-build** → Run workflow). The everyday `build.yml` keeps doing the cheap checks and the compile check on every push.

Nothing in this file has run yet: it needs a machine and a proxy that only you can provide.

## What you need to provide

### 1. A build runner

GitHub's standard runners can't do this (about 14 GB free disk and a 6-hour job cap). Use either:

| Option | Spec | Notes |
|---|---|---|
| **Self-hosted Linux runner** (recommended) | x86-64, Ubuntu 22.04/24.04, **16+ cores, 64 GB RAM, 250 GB free SSD**, passwordless `sudo` for `apt-get` | No time cap. A 16-core box takes roughly 4–6 h for the official ThinLTO+PGO build; later runs reuse `build/` if the runner keeps its workspace. [Adding a self-hosted runner](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/adding-self-hosted-runners). |
| GitHub larger runner | `ubuntu-24.04-32core` or bigger, with an added disk | Still capped at 6 h per job, so 16 cores is borderline. Needs a paid plan with larger runners enabled. |

Give the runner a label and either type it into the `runner` input when starting the workflow, or
set the repository variable **`CAMCHROME_FULL_RUNNER`** to it (Settings → Secrets and variables →
Actions → Variables). The workflow refuses to start the build with less than 110 GB free.

macOS full builds stay in `build.yml` (`mode=full`, `mac_runner` input): they need an Apple
Silicon machine with Xcode 26 and the Metal toolchain.

### 2. For the site matrix: a desktop session and a proxy

The `sites` job runs only when you tick **run_sites**. It needs:

- **A runner with outbound internet** to the check sites, and a display: a desktop session, or
  `xvfb` installed (the job uses `xvfb-run` when `DISPLAY` is unset). A GPU makes the render
  checks realistic; without one some sites flag software rendering. Set **`CAMCHROME_SITES_RUNNER`**
  (or the `sites_runner` input) if this is a different machine from the build runner.
- **A residential SOCKS5 proxy that supports UDP ASSOCIATE.** Without UDP, browserleaks reports
  QUIC false and WebRTC has no UDP path. Quick check: `tools/socks5_udp_proxy.py` documents the
  protocol; a proxy that answers UDP ASSOCIATE with reply code 7 ("command not supported") will
  not do. A datacenter or abused IP fails fingerprint.com, pixelscan and reCAPTCHA whatever the
  engine does.
- These **repository secrets** (Settings → Secrets and variables → Actions → Secrets):

  | Secret | Example | Used as |
  |---|---|---|
  | `CAMCHROME_SOCKS5_PROXY` | `socks5://gate.example.net:1080` | `--proxy-server` (a hostname is fine since 990) |
  | `CAMCHROME_SOCKS5_CREDENTIALS` | `user:password` | `--socks5-credentials` (omit for an IP-allowlisted proxy) |

- These **repository variables**, matching the proxy's exit country (sites compare them with the IP):

  | Variable | Example |
  |---|---|
  | `CAMCHROME_PROXY_TIMEZONE` | `Europe/Ljubljana` |
  | `CAMCHROME_PROXY_LANG` | `sl-SI` |

## What the workflow runs

| Step | Tool | Passes when |
|---|---|---|
| Persona validator | `chrome --clearcote-validate-persona=64` (974) | 64 derived personas satisfy every invariant |
| Relay self-test | `socks5_udp_relay_selftest` via `tools/socks5_udp_proxy.py` (990) | named origins and proxies round-trip; refusing or unreachable proxies fail the connect |
| Fingerprint self-test | `tools/fingerprint_selftest.py` for the host, Windows and Android personas | no FAIL |
| Network self-test | `tools/network_selftest.py` | SOCKS5 and HTTP proxy auth, QUIC over the relay, WebRTC exit IP via the relay, humanized click, rect/shadow checks, no-farble, portable profile |
| Site matrix (optional) | `tools/site_checks.py --socks5-udp` | the pass conditions in [TEST-MATRIX.md](TEST-MATRIX.md) |

Reports are uploaded as workflow artifacts: `camchrome-linux-x64` (the browser plus
`build/reports/*.json`) and `site-checks` (each site's text, screenshot and `summary.json`). Copy the
site results into the "Recorded result" column of TEST-MATRIX.md.

## Running it by hand instead

On any Linux machine with the spec above:

```bash
scripts/00-fetch-source.sh && scripts/01-apply-patches.sh && scripts/02-build.sh
NINJA_TARGETS=socks5_udp_relay_selftest scripts/02-build.sh
tools/socks5_udp_proxy.py --run-selftest-binary build/src/out/Default/socks5_udp_relay_selftest
pip install aioquic   # for the QUIC checks
tools/fingerprint_selftest.py && tools/network_selftest.py
tools/site_checks.py --proxy socks5://HOST:PORT --socks5-credentials USER:PASS --socks5-udp \
  --timezone Europe/Ljubljana --lang sl-SI
```
