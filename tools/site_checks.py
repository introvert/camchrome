#!/usr/bin/env python3
"""Run the public fingerprint / bot-detection check sites against a camchrome build.

These are the sites ShardBrowser publishes results for (see docs/TEST-MATRIX.md). Each
one is opened in the built browser over raw CDP (no driver), given time to finish, then
its visible text is saved, a screenshot is taken and a few key values are pulled out with
regexes. The verdict is printed next to the pass condition the matrix expects, so a run
is easy to compare with the published numbers. The pages change; when a regex stops
matching the result is "unparsed" and the saved text and screenshot are the record.

    tools/site_checks.py --seed demo \\
        --proxy socks5://host:1080 --socks5-credentials user:pass --socks5-udp \\
        --webrtc-ip <proxy exit IP> --timezone Europe/Ljubljana

    tools/site_checks.py --only browserleaks-quic,twilio
    tools/site_checks.py --list

Output: build/site-checks/<site>.txt, .png and build/site-checks/summary.json.

Notes
- QUIC and WebRTC only go through the proxy when it supports SOCKS5 UDP ASSOCIATE and the
  browser runs with --socks5-udp (patch 973). Without it QUIC is off when a SOCKS proxy
  is set (stock Chromium behaviour) and browserleaks/quic reports QUIC false.
- reCAPTCHA (antcpt) and fingerprint.com score the IP as much as the browser: a datacenter
  or flagged proxy lowers the result whatever the fingerprint does.
- Run headful on a desktop with a GPU. Headless and GPU-less runs are what these sites
  are built to catch.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cdp import Browser  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


@dataclass
class Site:
    key: str
    url: str
    expect: str            # the published pass condition
    wait: float            # seconds to let the page finish its tests
    extract: dict          # name -> regex over innerText (group 1 is the value)
    click: str | None = None   # text of a button to press first, if the test needs it


SITES = [
    Site("browserleaks-quic", "https://browserleaks.com/quic",
         "QUIC true; JA4 matches Chrome of the engine's major", 15,
         {"quic": r"QUIC\s*\n?\s*(True|False|true|false)",
          "ja4": r"JA4\s*\n?\s*([a-z0-9_]+)",
          "http_version": r"HTTP Version\s*\n?\s*(\S+)"}),
    Site("twilio", "https://networktest.twilio.com/",
         "every probe Pass (TURN UDP/TCP/TLS, voice); no host IP shown", 90,
         {"passed": r"(\d+)\s+(?:tests? )?passed", "failed": r"(\d+)\s+(?:tests? )?failed",
          "verdict": r"(All tests passed|Some tests failed|Pass|Fail)"},
         click="Start"),
    Site("fingerprint-com", "https://fingerprint.com/demo/",
         "Bot, VPN, DevTools and browser tampering: Not detected", 20,
         {"bot": r"Bot\s*\n?\s*(Not detected|Detected|Bad bot|Good bot)",
          "vpn": r"VPN\s*\n?\s*(Not detected|Detected)",
          "devtools": r"Developer Tools\s*\n?\s*(Not detected|Detected)",
          "tampering": r"Browser Tampering\s*\n?\s*(Not detected|Detected)",
          "incognito": r"Incognito\s*\n?\s*(Not detected|Detected)"}),
    Site("deviceandbrowserinfo-bot", "https://deviceandbrowserinfo.com/are_you_a_bot",
         "\"You are human\"; no bot / CDP signal flagged", 15,
         {"verdict": r"(You are (?:a )?(?:human|bot)[^\n]*)",
          "is_bot": r"isBot\W+(true|false)"}),
    Site("proxyshard-checker", "https://proxyshard.com/ip-checker",
         "No issues detected in all 9 categories (audio, automation, canvas, cpu, device, "
         "gpu, network, other, performance)", 25,
         {"issues_free": r"(No issues detected)",
          "categories_clean": r"__count__No issues detected"}),
    Site("pixelscan", "https://pixelscan.net/",
         "Fingerprint consistent; no proxy or automation detected", 25,
         {"consistency": r"(Your (?:browser )?fingerprint (?:is|looks) [a-z ]+)",
          "masking": r"(No masking detected|Masking detected)",
          "automation": r"(No automation framework detected|Automation framework detected)"}),
    Site("browserscan", "https://www.browserscan.net/",
         "Authenticity 100%; locale and timezone match the IP", 20,
         {"authenticity": r"Browser fingerprint authenticity[:\s]*\n?\s*(\d+%)",
          "authenticity_alt": r"(\d+%)\s*\n?\s*authenticity"}),
    Site("antcpt-recaptcha", "https://antcpt.com/score_detector/",
         "reCAPTCHA v3 score 0.9", 20,
         {"score": r"[Yy]our score is:?\s*([01]\.\d+)"}),
    Site("clearcote-audit", "https://www.clearcotelabs.com/audit",
         "use tools/stealth_audit.py for the scored run; this only saves the landing page", 5,
         {}),
]


def run_site(browser: Browser, site: Site, out_dir: Path) -> dict:
    cdp = browser.open_page()
    result = {"site": site.key, "url": site.url, "expect": site.expect}
    try:
        cdp.call("Page.enable")
        cdp.call("Page.navigate", {"url": site.url})
        if not cdp.wait_for("document.readyState === 'complete'", timeout=60):
            result["error"] = "page did not load"
        if site.click:
            time.sleep(3)
            cdp.evaluate(
                "(() => { const b = [...document.querySelectorAll('button,a')].find("
                f"e => e.textContent.trim().startsWith({json.dumps(site.click)}));"
                " if (b) b.click(); return !!b; })()")
        time.sleep(site.wait)
        text = cdp.evaluate("document.body ? document.body.innerText : ''") or ""
        (out_dir / f"{site.key}.txt").write_text(text)
        shot = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
        (out_dir / f"{site.key}.png").write_bytes(base64.b64decode(shot["data"]))
        values = {}
        for name, pattern in site.extract.items():
            if pattern.startswith("__count__"):
                values[name] = len(re.findall(pattern[len("__count__"):], text))
                continue
            m = re.search(pattern, text)
            values[name] = m.group(1) if m else None
        result["values"] = values
        result["parsed"] = any(v not in (None, 0) for v in values.values()) if values else None
    except Exception as e:  # keep going: one broken site must not hide the others
        result["error"] = str(e)[:500]
    finally:
        cdp.close()
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--binary", default=None, help="browser executable (default: build output)")
    ap.add_argument("--seed", default="site-checks")
    ap.add_argument("--platform", default=None,
                    choices=["windows", "macos", "linux", "android"])
    ap.add_argument("--proxy", help="e.g. socks5://host:1080 or http://host:8080")
    ap.add_argument("--socks5-credentials", help="user:pass for an authenticated SOCKS5 proxy (963/984)")
    ap.add_argument("--proxy-auth", help="user:pass for an HTTP(S) proxy (978)")
    ap.add_argument("--socks5-udp", action="store_true",
                    help="relay UDP (WebRTC) through SOCKS5 UDP ASSOCIATE (973)")
    ap.add_argument("--webrtc-ip", help="the proxy exit IP, reported as the WebRTC srflx candidate (100)")
    ap.add_argument("--timezone", help="IANA zone matching the proxy exit")
    ap.add_argument("--lang", help="e.g. sl-SI, matching the proxy exit country")
    ap.add_argument("--extra-flag", action="append", default=[], help="extra browser switch")
    ap.add_argument("--proxyshard-url", default=None,
                    help="URL of the ProxyShard browser checker (not confirmed; default "
                         "https://proxyshard.com/ip-checker)")
    ap.add_argument("--only", help="comma-separated site keys")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--headless", action="store_true", help="not recommended; see notes")
    ap.add_argument("--out", default=str(REPO / "build/site-checks"))
    args = ap.parse_args()

    if args.list:
        for s in SITES:
            print(f"{s.key:26} {s.url}\n{'':26} pass: {s.expect}")
        return 0

    if args.binary is None:
        args.binary = str(REPO / ("build/src/out/Default/Chromium.app/Contents/MacOS/Chromium"
                                  if sys.platform == "darwin" else "build/src/out/Default/chrome"))
    if not Path(args.binary).exists():
        print(f"browser not found: {args.binary}", file=sys.stderr)
        return 2

    flags = [f"--fingerprint={args.seed}", "--window-size=1366,900"]
    if args.platform:
        flags.append(f"--fingerprint-platform={args.platform}")
    if args.proxy:
        flags.append(f"--proxy-server={args.proxy}")
    if args.socks5_credentials:
        flags.append(f"--socks5-credentials={args.socks5_credentials}")
    if args.proxy_auth:
        flags.append(f"--proxy-auth={args.proxy_auth}")
    if args.socks5_udp:
        flags.append("--socks5-udp")
    if args.webrtc_ip:
        flags.append(f"--webrtc-ip={args.webrtc_ip}")
    if args.timezone:
        flags.append(f"--timezone={args.timezone}")
    if args.lang:
        flags += [f"--lang={args.lang}", f"--accept-lang={args.lang}"]
    flags += args.extra_flag

    if args.proxyshard_url:
        for s in SITES:
            if s.key == "proxyshard-checker":
                s.url = args.proxyshard_url
    sites = SITES
    if args.only:
        keys = set(args.only.split(","))
        sites = [s for s in SITES if s.key in keys]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    browser = Browser(args.binary, flags=flags, headless=args.headless,
                      log_path=str(out_dir / "browser.log"))
    results = []
    try:
        print(f"browser: {browser.version.get('Browser')}  flags: {' '.join(flags)}")
        for site in sites:
            print(f"- {site.key}: {site.url}", flush=True)
            r = run_site(browser, site, out_dir)
            results.append(r)
            print(f"    values: {r.get('values')}  {('error: ' + r['error']) if r.get('error') else ''}")
            print(f"    expect: {site.expect}")
    finally:
        browser.stop()
    summary = {"browser": browser.version, "flags": flags, "results": results,
               "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"\nwritten to {out_dir}/ (text, screenshots, summary.json)")
    return 0 if not any(r.get("error") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
