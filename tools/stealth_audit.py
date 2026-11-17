#!/usr/bin/env python3
"""Run the Clearcote fingerprint audit against the built camchrome browser.

The audit at https://www.clearcotelabs.com/audit is the acceptance test this
patch set is written for: 365 coherence checks across 16 subsystems. This
harness launches the browser WITHOUT Playwright/Puppeteer (so no driver flags or
bindings pollute the result), runs the audit and prints the score and every
finding.

Usage:
    tools/stealth_audit.py --seed demo --platform macos
    tools/stealth_audit.py --signals          # offline, no network needed
    tools/stealth_audit.py --binary /path/to/Chromium

The 18 input checks are reported as "not attempted" unless the audit's five
optional steps are completed. Pass --interact to drive those steps with trusted
CDP input (the harness aims inside each control, holds for a human interval and
reports pressure while a button is down), which scores them: 237/237.
"""

from __future__ import annotations

import argparse
import http.server
import json
import random
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cdp import Browser, CDP  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT = "https://www.clearcotelabs.com/audit"


def default_binary() -> Path:
    if sys.platform == "darwin":
        return REPO / "build/src/out/Default/Chromium.app/Contents/MacOS/Chromium"
    return REPO / "build/src/out/Default/chrome"


def default_platform() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


SIGNALS_JS = r"""
(async () => {
  const out = {};
  out.userAgent = navigator.userAgent;
  out.platform = navigator.platform;
  out.webdriver = navigator.webdriver;
  out.languages = navigator.languages;
  out.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  out.screen = [screen.width, screen.height, screen.availWidth,
                screen.availHeight, screen.colorDepth, devicePixelRatio];
  out.cores = navigator.hardwareConcurrency;
  out.deviceMemory = navigator.deviceMemory;
  out.cpuPerformance = navigator.cpuPerformance;
  out.maxTouchPoints = navigator.maxTouchPoints;
  try {
    out.userAgentData = await navigator.userAgentData.getHighEntropyValues(
        ["fullVersionList", "platformVersion", "model", "architecture",
         "bitness", "wow64"]);
  } catch (e) { out.userAgentDataError = String(e); }

  const canvas = document.createElement('canvas');
  canvas.width = 200; canvas.height = 50;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#f60'; ctx.fillRect(0, 0, 100, 50);
  ctx.fillStyle = '#069'; ctx.font = '14px Arial'; ctx.fillText('camchrome', 2, 20);
  out.canvasHash = [...ctx.getImageData(0, 0, 200, 50).data]
      .reduce((a, b) => (a * 31 + b) >>> 0, 0);

  const gl = document.createElement('canvas').getContext('webgl2');
  if (gl) {
    const dbg = gl.getExtension('WEBGL_debug_renderer_info');
    out.webgl = {
      vendor: gl.getParameter(dbg ? dbg.UNMASKED_VENDOR_WEBGL : gl.VENDOR),
      renderer: gl.getParameter(dbg ? dbg.UNMASKED_RENDERER_WEBGL : gl.RENDERER),
      version: gl.getParameter(gl.VERSION),
      maxTexture: gl.getParameter(gl.MAX_TEXTURE_SIZE),
      maxVertexUniforms: gl.getParameter(gl.MAX_VERTEX_UNIFORM_VECTORS),
      extensions: gl.getSupportedExtensions().length,
    };
  }

  if (navigator.gpu) {
    try {
      const adapter = await navigator.gpu.requestAdapter();
      out.webgpu = adapter ? {info: {
        vendor: adapter.info.vendor, architecture: adapter.info.architecture,
        description: adapter.info.description}, features: [...adapter.features]}
        : null;
    } catch (e) { out.webgpuError = String(e); }
  }

  if (navigator.connection) {
    out.connection = {rtt: navigator.connection.rtt,
                      downlink: navigator.connection.downlink,
                      effectiveType: navigator.connection.effectiveType};
  }
  if (navigator.mediaDevices) {
    out.devices = (await navigator.mediaDevices.enumerateDevices())
        .map(d => d.kind);
  }

  await new Promise(resolve => {
    const start = performance.now();
    const check = () => {
      if (speechSynthesis.getVoices().length ||
          performance.now() - start > 2000) {
        speechSynthesis.removeEventListener('voiceschanged', check);
        resolve();
      }
    };
    speechSynthesis.addEventListener('voiceschanged', check);
    setTimeout(resolve, 2200);
  });
  out.voices = speechSynthesis.getVoices().map(v => v.name).slice(0, 10);
  return out;
})()
"""


def parse_audit(text: str) -> dict:
    def search(pattern: str, cast=int):
        m = re.search(pattern, text)
        return cast(m.group(1)) if m else None

    return {
        "score": search(r"Coherence score (\d+) of 100"),
        "agree": search(r"(\d+) of \d+ scored checks agree"),
        "scored": search(r"\d+ of (\d+) scored checks agree"),
        "not_attempted": search(r"(\d+) input checks were not attempted"),
        "findings": extract_findings(text),
    }


def extract_findings(text: str) -> list[str]:
    """Pull the review list out of the rendered result, if present."""
    findings = []
    review = re.search(r"REVIEW FIRST(.*?)(?:Re-run|How it works|Want these)", text, re.S)
    if review:
        for line in review.group(1).splitlines():
            line = line.strip()
            if line and line not in ("Implausible value", "Contradiction"):
                findings.append(line)
    return findings




def _control_rect(cdp: CDP, selector: str):
    cdp.evaluate(f"document.querySelector({json.dumps(selector)})"
                 "?.scrollIntoView({block:'center',inline:'center',behavior:'instant'})")
    time.sleep(0.25)
    return cdp.evaluate(
        f"(()=>{{const e=document.querySelector({json.dumps(selector)});"
        "if(!e)return null;const r=e.getBoundingClientRect();"
        "return [r.x,r.y,r.width,r.height,innerWidth,innerHeight];})()")


def _aim(cdp: CDP, selector: str):
    """Pick a point inside the control, not its identical mathematical centre:
    a hand does not land on the same sub-pixel offset every time."""
    rect = _control_rect(cdp, selector)
    if not rect:
        raise RuntimeError(f"control not found: {selector}")
    x, y, w, h, vw, vh = rect
    px = x + w * (0.3 + 0.4 * random.random())
    py = y + h * (0.3 + 0.4 * random.random())
    if not (0 <= px <= vw and 0 <= py <= vh):
        raise RuntimeError(f"control outside the viewport: {selector}")
    return px, py


def _click(cdp: CDP, selector: str) -> None:
    x, y = _aim(cdp, selector)
    cdp.call("Input.dispatchMouseEvent",
             {"type": "mouseMoved", "x": x, "y": y, "force": 0.0})
    cdp.call("Input.dispatchMouseEvent",
             {"type": "mousePressed", "x": x, "y": y, "button": "left",
              "buttons": 1, "clickCount": 1, "force": 0.5})
    time.sleep(random.randint(60, 110) / 1000.0)
    cdp.call("Input.dispatchMouseEvent",
             {"type": "mouseReleased", "x": x, "y": y, "button": "left",
              "buttons": 0, "clickCount": 1, "force": 0.0})
    time.sleep(0.05)


def _key(cdp: CDP, key: str, code: str, vk: int, modifiers: int = 0,
         text: str | None = None) -> None:
    down = {"type": "keyDown", "key": key, "code": code,
            "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk,
            "modifiers": modifiers}
    if text is not None:
        down["text"] = text
        down["unmodifiedText"] = text
    cdp.call("Input.dispatchKeyEvent", down)
    time.sleep(random.randint(55, 110) / 1000.0)
    cdp.call("Input.dispatchKeyEvent",
             {"type": "keyUp", "key": key, "code": code,
              "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk,
              "modifiers": modifiers})
    time.sleep(0.03)


def _gate_count(cdp: CDP) -> str:
    return cdp.evaluate(
        "(()=>{const t=document.querySelector('[data-interaction-gate]');"
        "if(!t)return '5 of 5 done';"
        "const s=t.innerText;"
        "if(/All five done/i.test(s))return '5 of 5 done';"
        "const m=s.match(/[0-9] of 5 done/);return m?m[0]:'?';})()")


def perform_input_steps(cdp: CDP) -> str:
    """Complete the audit's five optional input steps with trusted CDP input.

    The audit times presses, checks PointerEvent.pressure while a button is
    held, requires input-before-change on the select, and measures whether two
    clicks land on the same sub-pixel point. A naive driver (element centre,
    instantaneous press/release, no force) fails six of those checks even
    though the engine is fine, so aim within the control, hold for a human
    interval, send force 0.5 while held, and use a real key event for the
    select instead of a scripted change.
    """
    for i in (1, 2, 3):
        _click(cdp, f'button[aria-label="Marker {i}"]')
    _click(cdp, 'input[aria-label="Type the word audit"]')
    for ch in "Audit":
        _key(cdp, ch, "Key" + ch.upper(), ord(ch.upper()),
             8 if ch.isupper() else 0, ch)
    cdp.evaluate("document.querySelector('input[type=range]').focus()")
    for _ in range(9):
        _key(cdp, "ArrowRight", "ArrowRight", 39)
    x, y = _aim(cdp, "button[data-interaction-hold]")
    cdp.call("Input.dispatchMouseEvent",
             {"type": "mouseMoved", "x": x, "y": y, "force": 0.0})
    time.sleep(0.1)
    cdp.call("Input.dispatchMouseEvent",
             {"type": "mousePressed", "x": x, "y": y, "button": "left",
              "buttons": 1, "clickCount": 1, "force": 0.5})
    for _ in range(4):
        time.sleep(0.4)
        cdp.call("Input.dispatchMouseEvent",
                 {"type": "mouseMoved", "x": x + random.uniform(-1, 1),
                  "y": y + random.uniform(-1, 1), "buttons": 1, "force": 0.5})
    cdp.call("Input.dispatchMouseEvent",
             {"type": "mouseReleased", "x": x, "y": y, "button": "left",
              "buttons": 0, "clickCount": 1, "force": 0.0})
    time.sleep(0.4)
    # Type-ahead on a focused select is a real, trusted change (input then
    # change), unlike assigning .value and dispatching a synthetic event.
    cdp.evaluate("document.querySelector('select[aria-label=\"Input device\"]').focus()")
    _key(cdp, "m", "KeyM", 77, 0, "m")
    time.sleep(0.4)
    return _gate_count(cdp)

def run_audit(args) -> int:
    binary = Path(args.binary)
    if not binary.exists():
        print(f"browser not found: {binary}", file=sys.stderr)
        return 2

    flags = [f"--fingerprint={args.seed}", f"--window-size={args.window_size}"]
    if args.platform != "auto":
        flags.append(f"--fingerprint-platform={args.platform}")
    browser = None
    cdp = None
    try:
        browser = Browser(
            str(binary),
            flags=flags,
            headless=args.headless,
            log_path=str(REPO / "build/stealth-browser.log"),
        )
        print(f"browser: {browser.version.get('Browser')} | seed={args.seed} platform={args.platform}")
        cdp = browser.open_page()
        cdp.call("Page.enable")
        cdp.call("Page.navigate", {"url": args.audit_url})
        if not cdp.wait_for("document.readyState === 'complete'", timeout=60):
            raise RuntimeError("audit page did not load")
        ready = cdp.wait_for(
            "!!Array.from(document.querySelectorAll('button'))"
            ".find(b => b.textContent.includes('Run the audit'))", timeout=60)
        if not ready:
            raise RuntimeError("could not find the 'Run the audit' button")

        if args.interact:
            progress = perform_input_steps(cdp)
            print(f"input steps: {progress}")
            if re.match(r"^[0-4] of 5", progress):
                print("warning: not all input steps registered", file=sys.stderr)

        cdp.evaluate(
            "(() => { const b = Array.from(document.querySelectorAll('button'))"
            ".find(b => b.textContent.includes('Run the audit'));"
            "if (!b) return false; b.click(); return true; })()")
        print("audit started; this takes about a minute", flush=True)

        text = ""
        deadline = time.time() + args.timeout
        last_state = None
        while time.time() < deadline:
            time.sleep(1.0)
            state = cdp.evaluate(
                "document.querySelector('[data-audit-state]')?.getAttribute('data-audit-state')")
            if state != last_state:
                print(f"  state: {state}", flush=True)
                last_state = state
            if state and state not in ("idle", "running"):
                text = cdp.evaluate(
                    "document.querySelector('[data-audit-state]')?.innerText || ''")
                break
        if not text:
            text = cdp.evaluate("document.body.innerText")

        result = parse_audit(text)
        out_txt = REPO / "build/stealth-audit.txt"
        out_json = REPO / "build/stealth-audit.json"
        out_txt.write_text(text)
        out_json.write_text(json.dumps(result, indent=2) + "\n")

        print()
        print(f"score: {result['score']}/100  "
              f"({result['agree']} of {result['scored']} scored checks agree)")
        if result["not_attempted"]:
            print(f"{result['not_attempted']} input checks not attempted (need human input)")
        if result["findings"]:
            print("findings:")
            for finding in result["findings"]:
                print(f"  - {finding}")
        else:
            print("findings: none")
        print(f"full result: {out_txt}")
        return 0 if result["score"] == 100 else 1
    finally:
        if cdp:
            cdp.close()
        if browser:
            browser.stop()


def run_signals(args) -> int:
    binary = Path(args.binary)
    if not binary.exists():
        print(f"browser not found: {binary}", file=sys.stderr)
        return 2
    browser = None
    cdp = None
    try:
        browser = Browser(
            str(binary),
            flags=([f"--fingerprint={args.seed}", f"--window-size={args.window_size}"]
                   + ([] if args.platform == "auto"
                      else [f"--fingerprint-platform={args.platform}"])),
            headless=args.headless,
            log_path=str(REPO / "build/stealth-browser.log"),
        )
        # A secure context is required for UA-CH high-entropy values, WebGPU
        # and mediaDevices; a local origin gives one without touching the net.
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<!doctype html><title>camchrome signals</title>")

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            cdp = browser.open_page()
            cdp.call("Page.enable")
            cdp.call("Page.navigate", {"url": f"http://127.0.0.1:{server.server_port}/"})
            cdp.wait_for("document.readyState === 'complete'", timeout=30)
            signals = cdp.evaluate(SIGNALS_JS)
        finally:
            server.shutdown()
            server.server_close()
        out = REPO / "build/stealth-signals.json"
        out.write_text(json.dumps(signals, indent=2) + "\n")
        print(json.dumps(signals, indent=2))
        print(f"\nwritten to {out}")
        return 0
    finally:
        if cdp:
            cdp.close()
        if browser:
            browser.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--binary", default=str(default_binary()),
                        help="browser executable (default: build/src/out/Default/...)")
    parser.add_argument("--seed", default="camchrome-audit", help="--fingerprint seed")
    parser.add_argument("--platform", default=default_platform(),
                        choices=["auto", "macos", "windows", "linux", "android"],
                        help="persona OS; auto omits --fingerprint-platform so the "
                             "host platform is used")
    parser.add_argument("--window-size", default="1280,800")
    parser.add_argument("--audit-url", default=DEFAULT_AUDIT)
    parser.add_argument("--timeout", type=float, default=600,
                        help="seconds to wait for the audit to finish")
    parser.add_argument("--headless", action="store_true",
                        help="run headless (the audit's GPU input checks prefer headful)")
    parser.add_argument("--signals", action="store_true",
                        help="offline: print the fingerprint signals instead of the audit")
    parser.add_argument("--interact", action="store_true",
                        help="complete the audit's 5 optional input steps with trusted "
                             "CDP input (adds/scored 18 input checks; ~30s longer)")
    args = parser.parse_args()
    return run_signals(args) if args.signals else run_audit(args)


if __name__ == "__main__":
    sys.exit(main())
