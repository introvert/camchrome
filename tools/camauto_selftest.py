#!/usr/bin/env python3
"""Self-test for tools/camauto.py: recorder, isolation, replay coalescing, API and MCP.

Runs on any Chromium (CI uses stock Chrome): the parts that need camchrome's humanized input
(clicks, typing and scrolling on replay) are covered by tools/network_selftest.py instead.

    tools/camauto_selftest.py --binary "$(command -v google-chrome)"
"""

from __future__ import annotations

import argparse
import http.server
import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import camauto  # noqa: E402

PAGE = b"""<!doctype html><title>one</title><body>
<input id="q" name="q"><a id="go" href="/two">next</a><input type=password id=pw>
<script>window.pageSawRecorder = () => typeof __camautoRecord + typeof __camautoInstalled;</script>
</body>"""
PAGE2 = b"<!doctype html><title>two</title><p>second page</p><button class=b>ok</button>"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail="") -> None:
    results.append((name, bool(ok), str(detail)[:300]))


def serve() -> str:
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = PAGE if self.path == "/" else PAGE2
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/"


def recorder(binary: str, url: str) -> None:
    b = camauto.Camchrome(binary, ["--window-size=1000,800"], headless=True)
    try:
        rec = camauto.Recorder(b)
        stop, steps = threading.Event(), []
        t = threading.Thread(target=lambda: steps.extend(rec.steps(stop)), daemon=True)
        t.start()
        b.navigate(url)
        seen = b.page.call("Runtime.evaluate", {"expression": "pageSawRecorder()",
                                                "returnByValue": True})["result"]["value"]
        check("recorder-invisible-to-page", seen == "undefinedundefined", seen)

        def click(sel: str) -> None:
            r = b.rect(sel)
            for kind in ("mousePressed", "mouseReleased"):
                b.page.call("Input.dispatchMouseEvent", {"type": kind, "x": r["x"], "y": r["y"],
                                                         "button": "left", "clickCount": 1})

        click("#q")
        time.sleep(0.1)
        b.page.call("Input.insertText", {"text": "hello"})
        click("#pw")
        b.page.call("Input.insertText", {"text": "hunter2"})
        click("#go")
        time.sleep(1.0)
        click("button.b")
        time.sleep(1.0)
        stop.set()
        t.join(5)
        flow = camauto.coalesce_navigation(steps)
        kinds = [(s["action"], s.get("selector"), s.get("text")) for s in flow]
        check("recorder-click-and-type", ("click", "#q", None) in kinds
              and ("type", "#q", "hello") in kinds, kinds)
        check("recorder-hides-passwords", ("type", "#pw", "${SECRET}") in kinds
              and not any(s.get("text") == "hunter2" for s in flow), kinds)
        nav = [s for s in flow if s.get("selector") == "#go"]
        check("recorder-link-click-carries-navigation",
              nav and nav[0].get("expect_navigation", "").endswith("/two")
              and not any(s["action"] == "navigate" and s["url"].endswith("/two") for s in flow),
              nav)
        check("recorder-follows-into-next-page", ("click", "button.b", None) in kinds, kinds)
        check("isolated-text-read", "second page" in b.text(), b.text()[:80])
    finally:
        b.close()


def mcp(binary: str) -> None:
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "selftest", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "browser_launch", "arguments": {"headless": True}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "browser_navigate",
                    "arguments": {"id": "b1", "url": "data:text/html,<h1>mcp ok</h1>"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "browser_text", "arguments": {"id": "b1"}}},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
         "params": {"name": "browser_close", "arguments": {"id": "b1"}}},
    ]
    p = subprocess.run([sys.executable, str(Path(__file__).parent / "camauto.py"),
                        "--binary", binary, "mcp"],
                       input="\n".join(json.dumps(m) for m in msgs) + "\n",
                       capture_output=True, text=True, timeout=120)
    replies = {r["id"]: r for r in map(json.loads, p.stdout.splitlines())}
    tools = [t["name"] for t in replies.get(2, {}).get("result", {}).get("tools", [])]
    check("mcp-initialize", replies.get(1, {}).get("result", {}).get("serverInfo"), replies.get(1))
    check("mcp-tools-listed", {"browser_launch", "browser_click", "browser_type"} <= set(tools),
          tools)
    text = (replies.get(5, {}).get("result", {}).get("content") or [{}])[0].get("text")
    check("mcp-launch-navigate-read", text == "mcp ok", text or p.stderr[-300:])


def api(binary: str) -> None:
    port, token = 7899, "selftest-token"
    proc = subprocess.Popen([sys.executable, str(Path(__file__).parent / "camauto.py"),
                             "--binary", binary, "api", "--port", str(port), "--token", token],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        def req(method, path, body=None, auth=True):
            r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                       data=json.dumps(body).encode() if body is not None
                                       else None)
            if auth:
                r.add_header("Authorization", f"Bearer {token}")
            try:
                with urllib.request.urlopen(r, timeout=60) as resp:
                    return resp.status, json.load(resp)
            except urllib.error.HTTPError as e:
                return e.code, json.load(e)

        for _ in range(50):
            try:
                req("GET", "/v1/browsers")
                break
            except OSError:
                time.sleep(0.2)
        check("api-needs-token", req("GET", "/v1/browsers", auth=False)[0] == 401)
        code, b = req("POST", "/v1/browsers", {"headless": True})
        bid = b.get("id")
        req("POST", f"/v1/browsers/{bid}/actions",
            {"action": "navigate", "url": "data:text/html,<p>api ok</p>"})
        code, t = req("POST", f"/v1/browsers/{bid}/actions", {"action": "text"})
        check("api-launch-navigate-read", t.get("result", {}).get("text") == "api ok", t)
        req("DELETE", f"/v1/browsers/{bid}")
    finally:
        proc.terminate()
        proc.wait(10)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--binary", default=str(camauto.DEFAULT_BINARY))
    args = ap.parse_args()
    url = serve()
    for part in (lambda: recorder(args.binary, url), lambda: mcp(args.binary),
                 lambda: api(args.binary)):
        try:
            part()
        except Exception as e:  # noqa: BLE001 -- report and carry on with the next group
            check("harness", False, repr(e))
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL':5} {name:42} {'' if ok else detail}")
    failed = sum(not ok for _, ok, _ in results)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
