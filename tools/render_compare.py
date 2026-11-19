#!/usr/bin/env python3
"""Load the same pages in stock Chrome and in camchrome, desktop and phone, and compare.

The bar is "indistinguishable from stock Chrome". For a desktop persona the reference is stock
Chrome itself (Chrome for Testing of the same version). For an Android persona there is no stock
Android Chrome on a Linux host, so the reference is stock Chrome with DevTools' device emulation
of a real phone (the same Emulation.* calls the DevTools device toolbar makes): that is how Chrome
itself lays a page out for a phone, and what sites see from it is close to a real handset except
for the things emulation does not change (GPU, fonts, sensors, voices), which camchrome's persona
does change.

For every (configuration, page) pair the tool records:
  - what the server decided: the final URL after redirects (m.wikipedia, m.youtube, ...), title;
  - how the page was laid out: viewport meta, innerWidth, visualViewport, documentElement width,
    horizontal overflow, scrollbar width, the font size of the body;
  - what the page could detect: UA, userAgentData (mobile/platform/model), touch, pointer/hover
    media queries, orientation, screen and DPR;
  - for detection sites, the verdicts pulled from the page text (regexes; unparsed = see text);
  - a screenshot of the viewport and the page text.

Then it prints, per page, every probe where a camchrome configuration differs from its reference,
and writes build/render-compare/report.html with the screenshots side by side.

    xvfb-run -a -s "-screen 0 1920x1080x24" tools/render_compare.py \\
        --stock build/ref/157.0.8103.78/chrome-linux64/chrome \\
        --camchrome build/src/out/Default/chrome

    tools/render_compare.py --only-config stock-desktop,stock-android --pages detect  # harness check

Reads run with Runtime.evaluate (no Runtime.enable); the stock phone reference needs Emulation.*
calls, which camchrome configurations never make.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cdp import Browser  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


@dataclass
class Page:
    key: str
    url: str
    kind: str                  # "detect": a detection/info site; "render": a real site
    wait: float = 6.0
    extract: dict = field(default_factory=dict)


# Detection and device-info sites: what they say this browser is.
DETECT = [
    Page("whatismybrowser", "https://www.whatismybrowser.com/", "detect", 8,
         {"browser": r"(Chrome \d+ on [^\n]+)", "mobile": r"(on (?:Android|Windows|Linux|macOS)[^\n]*)"}),
    Page("deviceinfo", "https://www.deviceinfo.me/", "detect", 12,
         {"device_type": r"Device Type\s*\n?\s*([^\n]+)", "touch": r"Touch Screen\s*\n?\s*([^\n]+)",
          "os": r"Operating System\s*\n?\s*([^\n]+)"}),
    Page("dabi-device", "https://deviceandbrowserinfo.com/info_device", "detect", 10,
         {"is_mobile": r"[Ii]s mobile\W+(true|false|yes|no)", "os": r"OS\s*\n?\s*([^\n]+)"}),
    Page("dabi-bot", "https://deviceandbrowserinfo.com/are_you_a_bot", "detect", 12,
         {"verdict": r"(You are (?:a )?(?:human|bot)[^\n]*)"}),
    Page("browserleaks-ch", "https://browserleaks.com/client-hints", "detect", 8,
         {"mobile": r"Sec-CH-UA-Mobile\s*\n?\s*(\?[01])", "platform": r"Sec-CH-UA-Platform\s*\n?\s*(\"[^\"]*\")",
          "model": r"Sec-CH-UA-Model\s*\n?\s*(\"[^\"]*\")"}),
    Page("browserleaks-js", "https://browserleaks.com/javascript", "detect", 8, {}),
    Page("sannysoft", "https://bot.sannysoft.com/", "detect", 8,
         {"failed": r"__count__\bfailed\b"}),
    Page("creepjs", "https://abrahamjuliot.github.io/creepjs/", "detect", 25,
         {"lies": r"lies?\s*\(?(\d+)\)?", "trust": r"trust score:?\s*([\d.]+%?)",
          "headless": r"(\d+% like headless)", "stealth": r"(\d+% stealth)"}),
    Page("browserscan", "https://www.browserscan.net/", "detect", 15,
         {"authenticity": r"(\d+%)"}),
    Page("pixelscan", "https://pixelscan.net/fingerprint-check", "detect", 30,
         {"consistency": r"(Your (?:browser )?fingerprint (?:is|looks) [a-z ]+)",
          "masking": r"(No masking detected|Masking detected)",
          "automation": r"(No automation framework detected|Automation framework detected)"}),
    Page("amiunique", "https://amiunique.org/fingerprint", "detect", 15, {}),
]

# Real sites that serve a different page to phones (by UA, client hints or viewport).
RENDER = [
    Page("google", "https://www.google.com/search?q=chromium+browser&hl=en", "render", 4),
    Page("wikipedia", "https://en.wikipedia.org/wiki/Chromium_(web_browser)", "render", 3),
    Page("youtube", "https://www.youtube.com/", "render", 6),
    Page("github", "https://github.com/", "render", 4),
    Page("bbc", "https://www.bbc.com/news", "render", 5),
    Page("amazon", "https://www.amazon.com/", "render", 5),
    Page("reddit", "https://www.reddit.com/r/chrome/", "render", 6),
    Page("duckduckgo", "https://duckduckgo.com/?q=chromium", "render", 4),
]

# One Pixel 7 as DevTools' device toolbar emulates it (Chrome 157 UA, reduced form).
PIXEL = {"width": 412, "height": 915, "dpr": 2.625, "model": "Pixel 7", "android": "14"}

PROBE = r"""(async () => {
  const mq = q => matchMedia(q).matches;
  const meta = document.querySelector('meta[name=viewport]');
  const sb = (() => { try {
    const d = document.createElement('div');
    d.style.cssText = 'width:100px;height:100px;overflow:scroll;position:absolute;top:-999px';
    document.body.appendChild(d); const w = d.offsetWidth - d.clientWidth; d.remove(); return w;
  } catch (e) { return null; } })();
  let he = null;
  try { he = await navigator.userAgentData.getHighEntropyValues(
      ['model', 'platformVersion', 'architecture', 'formFactors', 'fullVersionList']); } catch (e) {}
  const de = document.documentElement;
  return {
    url: location.href, title: document.title.slice(0, 120),
    viewportMeta: meta ? meta.content : null,
    innerWidth, innerHeight, outerWidth, outerHeight,
    vvWidth: visualViewport && Math.round(visualViewport.width),
    vvScale: visualViewport && visualViewport.scale,
    docClientWidth: de.clientWidth, docScrollWidth: de.scrollWidth,
    hOverflow: de.scrollWidth > de.clientWidth + 1,
    scrollbar: sb,
    bodyFont: document.body ? getComputedStyle(document.body).fontSize : null,
    screen: [screen.width, screen.height, screen.availWidth, screen.availHeight],
    dpr: devicePixelRatio,
    orientation: screen.orientation && screen.orientation.type,
    ua: navigator.userAgent, platform: navigator.platform,
    uaMobile: navigator.userAgentData && navigator.userAgentData.mobile,
    uaPlatform: navigator.userAgentData && navigator.userAgentData.platform,
    uaModel: he && he.model, uaPlatformVersion: he && he.platformVersion,
    uaFormFactors: he && he.formFactors,
    maxTouchPoints: navigator.maxTouchPoints, ontouchstart: 'ontouchstart' in window,
    pointer: mq('(pointer: coarse)') ? 'coarse' : mq('(pointer: fine)') ? 'fine' : 'none',
    hover: mq('(hover: hover)'), anyPointerFine: mq('(any-pointer: fine)'),
    anyHover: mq('(any-hover: hover)'),
    deviceWidthMq: mq(`(device-width: ${screen.width}px)`),
  };
})()"""

# Probes that legitimately differ between any two runs, or between emulation and a persona.
VOLATILE = {"title", "url"}


def chrome_ua(major: str, android: bool) -> str:
    if android:
        return (f"Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Chrome/{major}.0.0.0 Mobile Safari/537.36")
    return (f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{major}.0.0.0 Safari/537.36")


def emulate_phone(cdp, full_version: str) -> None:
    """What the DevTools device toolbar does for a Pixel 7, applied before navigation."""
    major = full_version.split(".")[0]
    brands = [{"brand": "Chromium", "version": major}, {"brand": "Google Chrome", "version": major},
              {"brand": "Not.A/Brand", "version": "99"}]
    full = [{"brand": b["brand"], "version": full_version if b["version"] == major else "99.0.0.0"}
            for b in brands]
    cdp.call("Emulation.setUserAgentOverride", {
        "userAgent": chrome_ua(major, True), "platform": "Linux armv81",
        "userAgentMetadata": {"brands": brands, "fullVersionList": full, "fullVersion": full_version,
                              "platform": "Android", "platformVersion": PIXEL["android"],
                              "architecture": "", "model": PIXEL["model"], "mobile": True,
                              "bitness": "", "wow64": False, "formFactors": ["Mobile"]}})
    cdp.call("Emulation.setDeviceMetricsOverride", {
        "width": PIXEL["width"], "height": PIXEL["height"], "deviceScaleFactor": PIXEL["dpr"],
        "mobile": True, "screenWidth": PIXEL["width"], "screenHeight": PIXEL["height"],
        "screenOrientation": {"type": "portraitPrimary", "angle": 0}})
    cdp.call("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})
    cdp.call("Emulation.setEmitTouchEventsForMouse", {"enabled": True, "configuration": "mobile"})


@dataclass
class Config:
    key: str
    binary: str
    flags: list
    phone_emulation: bool = False
    reference: str | None = None     # the config this one must match


def visit(browser: Browser, cfg: Config, page: Page, out: Path) -> dict:
    cdp = browser.open_page()
    rec = {"page": page.key, "config": cfg.key, "request": page.url}
    try:
        cdp.call("Page.enable")
        if cfg.phone_emulation:
            emulate_phone(cdp, browser.version["Browser"].split("/")[-1])
        cdp.call("Page.navigate", {"url": page.url})
        if not cdp.wait_for("document.readyState === 'complete'", timeout=45):
            rec["error"] = "did not finish loading"
        time.sleep(page.wait)
        rec["probe"] = cdp.evaluate(PROBE)
        text = cdp.evaluate("document.body ? document.body.innerText : ''") or ""
        stem = f"{page.key}--{cfg.key}"
        (out / f"{stem}.txt").write_text(text)
        shot = cdp.call("Page.captureScreenshot", {"format": "jpeg", "quality": 70})
        (out / f"{stem}.jpg").write_bytes(base64.b64decode(shot["data"]))
        rec["shot"] = f"{stem}.jpg"
        verdicts = {}
        for name, rx in page.extract.items():
            if rx.startswith("__count__"):
                verdicts[name] = len(re.findall(rx[9:], text, re.I))
            else:
                m = re.search(rx, text, re.I)
                verdicts[name] = m.group(1).strip() if m else None
        rec["verdicts"] = verdicts
    except Exception as e:  # one broken page must not stop the run
        rec["error"] = str(e)[:400]
    finally:
        try:
            cdp.call("Page.navigate", {"url": "about:blank"})
        except Exception:
            pass
        cdp.close()
    return rec


def diff(ref: dict, got: dict) -> dict:
    a, b = ref.get("probe") or {}, got.get("probe") or {}
    out = {k: (a.get(k), b.get(k)) for k in sorted(set(a) | set(b))
           if k not in VOLATILE and a.get(k) != b.get(k)}
    # The server's decision matters: compare the final host (m.wikipedia vs en.wikipedia).
    ha = re.sub(r"^https?://([^/]+).*", r"\1", a.get("url") or "")
    hb = re.sub(r"^https?://([^/]+).*", r"\1", b.get("url") or "")
    if ha != hb:
        out["finalHost"] = (ha, hb)
    va, vb = ref.get("verdicts") or {}, got.get("verdicts") or {}
    for k in set(va) | set(vb):
        if va.get(k) != vb.get(k):
            out[f"verdict.{k}"] = (va.get(k), vb.get(k))
    return out


# Probes whose difference from the reference is expected and not a tell: values a persona
# deliberately changes (a different but coherent machine), and Chrome's own reduced UA strings.
EXPECTED = {"ua", "screen", "outerWidth", "outerHeight", "innerHeight", "dpr", "uaModel",
            "uaPlatformVersion", "platform"}


def write_report(out: Path, configs: list, pages: list, records: dict, diffs: dict) -> None:
    rows = []
    for p in pages:
        cells = []
        for c in configs:
            r = records.get((p.key, c.key), {})
            pr = r.get("probe") or {}
            d = diffs.get((p.key, c.key))
            dtxt = ""
            if d:
                dtxt = "<br>".join(f"<b>{html.escape(k)}</b>: {html.escape(str(v[0]))} → "
                                   f"{html.escape(str(v[1]))}" for k, v in d.items())
            img = f'<img src="{r["shot"]}" loading="lazy">' if r.get("shot") else ""
            cells.append(
                f"<td><div class=meta>{html.escape(pr.get('url', r.get('error', '')))[:90]}<br>"
                f"inner {pr.get('innerWidth')} · doc {pr.get('docScrollWidth')} · "
                f"meta {html.escape(str(pr.get('viewportMeta')))[:40]}<br>"
                f"{html.escape(json.dumps(r.get('verdicts') or {}))}</div>{img}"
                f"<div class=diff>{dtxt}</div></td>")
        rows.append(f"<tr><th>{p.key}</th>{''.join(cells)}</tr>")
    head = "".join(f"<th>{c.key}{' (vs ' + c.reference + ')' if c.reference else ''}</th>"
                   for c in configs)
    (out / "report.html").write_text(f"""<!doctype html><meta charset=utf-8>
<title>Render compare</title><style>
body{{font:13px system-ui;margin:16px;background:#fff;color:#111}}
table{{border-collapse:collapse}} td,th{{border:1px solid #ccc;padding:6px;vertical-align:top}}
img{{max-width:320px;max-height:640px;display:block;margin-top:4px}} .meta{{color:#555}}
.diff{{color:#a00;max-width:320px;word-break:break-word}}</style>
<h1>Render compare</h1><table><tr><th>page</th>{head}</tr>{''.join(rows)}</table>""")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stock", default=str(REPO / "build/ref/157.0.8103.78/chrome-linux64/chrome"))
    ap.add_argument("--camchrome", default=str(REPO / "build/src/out/Default/chrome"))
    ap.add_argument("--seed", default="render-compare")
    ap.add_argument("--pages", default="detect,render", help="detect, render or both")
    ap.add_argument("--only", help="comma-separated page keys")
    ap.add_argument("--only-config", help="comma-separated configuration keys")
    ap.add_argument("--extra-flag", action="append", default=[])
    ap.add_argument("--out", default=str(REPO / "build/render-compare"))
    args = ap.parse_args()

    pages = [p for kind in args.pages.split(",") for p in {"detect": DETECT, "render": RENDER}[kind]]
    if args.only:
        pages = [p for p in pages if p.key in set(args.only.split(","))]
    desktop = ["--window-size=1366,900"]
    seed = f"--fingerprint={args.seed}"
    configs = [
        Config("stock-desktop", args.stock, desktop),
        Config("stock-android", args.stock, desktop, phone_emulation=True),
        Config("cam-desktop", args.camchrome, [seed, *desktop], reference="stock-desktop"),
        Config("cam-windows", args.camchrome, [seed, "--fingerprint-platform=windows", *desktop],
               reference="stock-desktop"),
        Config("cam-android", args.camchrome, [seed, "--fingerprint-platform=android"],
               reference="stock-android"),
    ]
    if args.only_config:
        configs = [c for c in configs if c.key in set(args.only_config.split(","))]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    records: dict = {}
    for cfg in configs:
        if not Path(cfg.binary).exists():
            print(f"skip {cfg.key}: {cfg.binary} not found")
            continue
        browser = Browser(cfg.binary, flags=cfg.flags + args.extra_flag,
                          log_path=str(out / f"browser-{cfg.key}.log"))
        try:
            print(f"== {cfg.key}: {browser.version.get('Browser')}", flush=True)
            for p in pages:
                r = visit(browser, cfg, p, out)
                records[(p.key, cfg.key)] = r
                pr = r.get("probe") or {}
                print(f"  {p.key:18} {pr.get('url', '')[:60]:60} inner={pr.get('innerWidth')} "
                      f"doc={pr.get('docScrollWidth')} {r.get('verdicts') or ''} "
                      f"{r.get('error', '')}", flush=True)
        finally:
            browser.stop()

    diffs: dict = {}
    print("\n== differences from the reference (expected persona changes hidden)")
    for cfg in configs:
        if not cfg.reference:
            continue
        for p in pages:
            ref, got = records.get((p.key, cfg.reference)), records.get((p.key, cfg.key))
            if not ref or not got:
                continue
            d = diff(ref, got)
            diffs[(p.key, cfg.key)] = d
            shown = {k: v for k, v in d.items() if k not in EXPECTED}
            if shown:
                print(f"  {cfg.key:12} {p.key:18} {shown}")
    (out / "results.json").write_text(json.dumps(
        {"records": [dict(v) for v in records.values()],
         "diffs": [{"page": k[0], "config": k[1], "diff": v} for k, v in diffs.items()]},
        indent=1, default=str) + "\n")
    write_report(out, configs, pages, records, diffs)
    print(f"\nreport: {out / 'report.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
