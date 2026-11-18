#!/usr/bin/env python3
"""User-Agent and UA client hints self-test (patch 995, with 010 and 987).

Every place a page or a server can read the browser's identity must tell the same story: the
User-Agent header and navigator.userAgent, Sec-CH-UA and Sec-CH-UA-Full-Version-List,
navigator.userAgentData (brands, fullVersionList, uaFullVersion, platform) in the page and in a
worker. This launches the built browser over raw CDP in each identity mode and checks that:

  * every Chromium / Google Chrome brand carries the UA string's Chrome major, in the page, the
    worker and the request headers, and the full-version list's majors match;
  * an Edg/OPR/Vivaldi token in the UA and the matching brand agree, and uaFullVersion is that
    brand's full version (Chrome's when there is none);
  * userAgentData.platform names the OS the UA string names;
  * with no override the claimed major is the engine's own;
  * a CDP override WITH userAgentMetadata is reported exactly as given (also in workers).

    tools/ua_selftest.py                                   # build/src/out/Default/chrome
    tools/ua_selftest.py --binary /path/to/Chromium.app/Contents/MacOS/Chromium
"""

from __future__ import annotations

import argparse
import http.server
import json
import re
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import Browser  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
ACCEPT_CH = ("Sec-CH-UA-Full-Version-List, Sec-CH-UA-Full-Version, Sec-CH-UA-Platform, "
             "Sec-CH-UA-Platform-Version")
WIN_150 = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/150.0.0.0 Safari/537.36")
ANDROID_157 = ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/157.0.0.0 Mobile Safari/537.36")
WIN_156 = WIN_150.replace("150.0.0.0", "156.0.0.0")
EXPLICIT_METADATA = {
    "brands": [{"brand": "Google Chrome", "version": "156"},
               {"brand": "Chromium", "version": "156"},
               {"brand": "Not=A?Brand", "version": "24"}],
    "fullVersionList": [{"brand": "Google Chrome", "version": "156.0.8078.79"},
                        {"brand": "Chromium", "version": "156.0.8078.79"},
                        {"brand": "Not=A?Brand", "version": "24.0.0.0"}],
    "platform": "Windows", "platformVersion": "19.0.0", "architecture": "x86", "model": "",
    "mobile": False, "bitness": "64",
}

PROBE_JS = """(async () => {
  const d = navigator.userAgentData;
  const h = await d.getHighEntropyValues(['fullVersionList', 'uaFullVersion']);
  const w = await new Promise(r => {
    const src = `(async () => { const d = navigator.userAgentData;
      const h = await d.getHighEntropyValues(['fullVersionList']);
      postMessage([navigator.userAgent, d.brands, h.fullVersionList, d.platform]); })()`;
    const wk = new Worker(URL.createObjectURL(new Blob([src], {type: 'text/javascript'})));
    wk.onmessage = e => r(e.data);
  });
  const bv = l => l.map(b => [b.brand, b.version]);
  return {ua: navigator.userAgent, brands: bv(d.brands), full: bv(h.fullVersionList),
          uaFullVersion: h.uaFullVersion, platform: d.platform,
          worker: {ua: w[0], brands: bv(w[1]), full: bv(w[2]), platform: w[3]}};
})()"""


class Server:
    def __init__(self):
        self.headers: dict[str, dict] = {}
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.headers[self.path] = {k.lower(): v for k, v in self.headers.items()}
                body = b"<!doctype html><title>ua</title>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Accept-CH", ACCEPT_CH)
                self.send_header("Critical-CH", "Sec-CH-UA-Full-Version-List")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"


def parse_sf_list(value: str | None) -> list[list[str]]:
    """'"A";v="1", "B";v="2"' -> [["A","1"], ["B","2"]]."""
    if not value:
        return []
    return [[m.group(1), m.group(2)] for m in re.finditer(r'"([^"]*)";v="([^"]*)"', value)]


def ua_os(ua: str) -> str:
    if "Android" in ua:
        return "Android"
    if "Windows NT" in ua:
        return "Windows"
    if "Macintosh" in ua:
        return "macOS"
    if "CrOS" in ua:
        return "Chrome OS"
    return "Linux" if "Linux" in ua else ""


def token(ua: str, name: str) -> str | None:
    m = re.search(re.escape(name) + r"/([\d.]+)", ua)
    return m.group(1) if m else None


SECONDARY = {"Edg": "Microsoft Edge", "OPR": "Opera", "Vivaldi": "Vivaldi"}


def check_coherent(r: dict, hdr: dict, engine_major: str | None) -> list[str]:
    """Problems with one probe result; empty when every surface agrees."""
    errs = []
    ua = r["ua"]
    major = (token(ua, "Chrome") or "").split(".")[0]
    if hdr.get("user-agent") != ua:
        errs.append(f"User-Agent header {hdr.get('user-agent')!r} != navigator.userAgent")
    if engine_major and major != engine_major:
        errs.append(f"UA claims Chrome {major}, engine is {engine_major}")
    sources = {"page brands": r["brands"], "page full": r["full"],
               "worker brands": r["worker"]["brands"], "worker full": r["worker"]["full"],
               "Sec-CH-UA": parse_sf_list(hdr.get("sec-ch-ua")),
               "Sec-CH-UA-Full-Version-List": parse_sf_list(hdr.get("sec-ch-ua-full-version-list"))}
    for name, lst in sources.items():
        if not lst:
            errs.append(f"{name} is empty")
            continue
        for brand, version in lst:
            if brand in ("Chromium", "Google Chrome") and version.split(".")[0] != major:
                errs.append(f"{name}: {brand} {version} vs UA Chrome/{major}")
        for tok, brand in SECONDARY.items():
            tv = token(ua, tok)
            have = [v for b, v in lst if b == brand]
            if tv and not have:
                errs.append(f"{name}: UA has {tok}/{tv} but no {brand} brand")
            if have and not tv:
                errs.append(f"{name}: {brand} brand but no {tok} token in the UA")
            if tv and have and have[0].split(".")[0] != tv.split(".")[0]:
                errs.append(f"{name}: {brand} {have[0]} vs UA {tok}/{tv}")
    if r["brands"] != r["worker"]["brands"]:
        errs.append(f"worker brands {r['worker']['brands']} != page {r['brands']}")
    if r["worker"]["ua"] != ua:
        errs.append("worker navigator.userAgent differs from the page")
    if [b for b, _ in r["brands"]] != [b for b, _ in sources["Sec-CH-UA"]]:
        errs.append("Sec-CH-UA brand order differs from navigator.userAgentData.brands")
    # uaFullVersion: the secondary brand's full version if there is one, else Chrome's.
    primary = next((v for b, v in r["full"] if b in SECONDARY.values()), None) or \
        next((v for b, v in r["full"] if b in ("Google Chrome", "Chromium")), None)
    if r["uaFullVersion"] != primary:
        errs.append(f"uaFullVersion {r['uaFullVersion']} != full-version list's {primary}")
    os_name = ua_os(ua)
    for where, platform in (("page", r["platform"]), ("worker", r["worker"]["platform"])):
        if os_name and platform != os_name:
            errs.append(f"{where} userAgentData.platform {platform!r} but the UA says {os_name}")
    return errs


def run_case(binary: str, server: Server, flags: list[str], override: dict | None,
             headless: bool) -> tuple[dict, dict]:
    b = Browser(binary, flags=["--lang=en-US"] + flags, headless=headless)
    try:
        cdp = b.open_page()
        if override:
            cdp.call("Emulation.setUserAgentOverride", override)
        server.headers.clear()
        cdp.call("Page.navigate", {"url": server.url + "/p"})
        cdp.wait_for("document.title === 'ua'", timeout=30)
        r = cdp.evaluate(PROBE_JS)
        cdp.close()
        return r, server.headers.get("/p", {})
    finally:
        b.stop()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--binary", default=str(REPO / "build/src/out/Default/chrome"))
    ap.add_argument("--seed", default="ua-selftest")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--json", help="write the raw results here")
    ap.add_argument("--stock", action="store_true",
                    help="stock Chromium (harness check): only the no-flags case must pass; "
                         "the others are XFAIL, since the overrides are what 995 fixes")
    args = ap.parse_args()

    server = Server()
    fp = [f"--fingerprint={args.seed}"]
    cases = [
        # name, flags, CDP override, expect the engine's own major
        ("default (no flags)", [], None, True),
        ("persona", fp, None, True),
        ("brand chrome", fp + ["--fingerprint-brand=chrome"], None, True),
        ("brand edge", fp + ["--fingerprint-brand=edge"], None, True),
        ("brand opera, no version (plain Chromium)", fp + ["--fingerprint-brand=opera"], None, True),
        ("brand opera 140", fp + ["--fingerprint-brand=opera",
                                  "--fingerprint-brand-version=140.0.6074.18"], None, True),
        ("persona, android", fp + ["--fingerprint-platform=android"], None, True),
        ("persona, windows", fp + ["--fingerprint-platform=windows"], None, True),
        ("--user-agent Windows Chrome/150", fp + [f"--user-agent={WIN_150}"], None, False),
        ("CDP UA-only override, Windows Chrome/150", fp, {"userAgent": WIN_150}, False),
        ("CDP UA-only override, Android", fp, {"userAgent": ANDROID_157}, False),
    ]
    engine_major = None
    results, failed, xfailed = {}, 0, 0

    def report(name: str, errs: list[str]) -> None:
        nonlocal failed, xfailed
        status = "PASS"
        if errs:
            status = "XFAIL" if args.stock and name != cases[0][0] else "FAIL"
        failed += status == "FAIL"
        xfailed += status == "XFAIL"
        print(f"{status:5} {name}")
        for e in errs:
            print(f"        {e}")

    for name, flags, override, own in cases:
        r, hdr = run_case(args.binary, server, flags, override, args.headless)
        if engine_major is None:
            engine_major = (token(r["ua"], "Chrome") or "").split(".")[0]
        errs = check_coherent(r, hdr, engine_major if own else None)
        results[name] = {"result": r, "headers": hdr, "errors": errs}
        report(name, errs)

    # An explicit CDP metadata override is reported exactly as given, page and worker.
    name = "CDP override with userAgentMetadata kept as given"
    r, hdr = run_case(args.binary, server, fp,
                      {"userAgent": WIN_156, "userAgentMetadata": EXPLICIT_METADATA}, args.headless)
    want_brands = [[b["brand"], b["version"]] for b in EXPLICIT_METADATA["brands"]]
    want_full = [[b["brand"], b["version"]] for b in EXPLICIT_METADATA["fullVersionList"]]
    errs = []
    for where, got in (("page brands", r["brands"]), ("worker brands", r["worker"]["brands"]),
                       ("Sec-CH-UA", parse_sf_list(hdr.get("sec-ch-ua")))):
        if got != want_brands:
            errs.append(f"{where} {got} != given {want_brands}")
    if r["full"] != want_full:
        errs.append(f"fullVersionList {r['full']} != given {want_full}")
    if r["uaFullVersion"] != "156.0.8078.79":
        errs.append(f"uaFullVersion {r['uaFullVersion']} does not follow the given list")
    results[name] = {"result": r, "headers": hdr, "errors": errs}
    report(name, errs)

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1))
    total = len(results)
    print(f"\nPASS {total - failed - xfailed}  FAIL {failed}  XFAIL {xfailed}"
          f"   (engine Chrome {engine_major})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
