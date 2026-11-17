#!/usr/bin/env python3
"""camchrome automation: a local API, an MCP server, a recorder, replay and synchronised windows.

Everything here drives camchrome over CDP with the engine's own human input
(Browser.humanizedClick/Type/Scroll/Drag, patches 130 and 993) and never touches the page's
main world: page reads run in an isolated world, the recorder listens from an isolated world
and reports through a binding bound to that world only, and Runtime.enable is never sent.

    # Local HTTP API (bearer token printed at start; loopback only by default)
    tools/camauto.py api --port 7878
    curl -H "Authorization: Bearer $TOKEN" -d '{"pick": {"os": "windows"}}' localhost:7878/v1/browsers
    curl -H "Authorization: Bearer $TOKEN" -d '{"action": "navigate", "url": "https://example.com"}' \
         localhost:7878/v1/browsers/b1/actions

    # MCP server over stdio, for Claude and other MCP clients
    tools/camauto.py mcp

    # Record what you do in a window, then replay it with human timing
    tools/camauto.py record --out flow.json -- --fingerprint=alice
    tools/camauto.py replay flow.json -- --fingerprint=bob

    # Synchronised windows: drive the first, the others follow with their own jitter
    tools/camauto.py sync --count 3 --flags-each "--fingerprint=s{n}"

Browser flags after "--" are passed to every launched browser (persona, proxy, ...).
"""

from __future__ import annotations

import argparse
import base64
import collections
import http.server
import json
import os
import random
import secrets
import select
import shlex
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import CDP, Browser  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DEFAULT_BINARY = REPO / "build/src/out/Default/chrome"
WORLD = "camauto"
BINDING = "__camautoRecord"


# ---- CDP session that keeps events -----------------------------------------------------------

class Session(CDP):
    """A CDP session that queues events instead of dropping them."""

    def __init__(self, ws_url: str, timeout: float = 60.0):
        super().__init__(ws_url, timeout=timeout)
        self.events: collections.deque = collections.deque()
        self.lock = threading.RLock()

    def call(self, method: str, params: dict | None = None) -> dict:
        with self.lock:
            self._next_id += 1
            request_id = self._next_id
            self.ws.send(json.dumps({"id": request_id, "method": method,
                                     "params": params or {}}))
            while True:
                message = json.loads(self.ws.recv())
                if message.get("id") == request_id:
                    if "error" in message:
                        raise RuntimeError(f"{method}: {message['error']}")
                    return message.get("result", {})
                if "method" in message:
                    self.events.append(message)

    def next_event(self, timeout: float) -> dict | None:
        """The next queued or incoming event, or None after `timeout` seconds."""
        deadline = time.time() + timeout
        while True:
            with self.lock:
                if self.events:
                    return self.events.popleft()
                # Only start reading a frame once bytes are there, so a timeout never splits one.
                if self.ws._buffer or select.select([self.ws.sock], [], [], 0)[0]:
                    message = json.loads(self.ws.recv())
                    if "method" in message:
                        return message
                    continue
            left = deadline - time.time()
            if left <= 0:
                return None
            select.select([self.ws.sock], [], [], min(left, 0.1))


# ---- one controlled browser ------------------------------------------------------------------

def gauss(mean: float, sigma: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, random.gauss(mean, sigma)))


class Camchrome:
    """A launched camchrome with its page and browser-level CDP sessions."""

    def __init__(self, binary: str, flags: list[str], profile: str | None = None,
                 headless: bool = False):
        self.browser = Browser(binary, flags=flags, profile=profile, headless=headless)
        self.browser.open_page().close()  # waits until the first tab is up
        self.root = Session(self.browser.version["webSocketDebuggerUrl"])
        targets = self.root.call("Target.getTargets")["targetInfos"]
        page = next(t for t in targets if t["type"] == "page"
                    and not t.get("url", "").startswith("chrome://"))
        self.target_id = page["targetId"]
        self.page = Session(f"ws://127.0.0.1:{self.browser.port}/devtools/page/{self.target_id}")
        self.page.call("Page.enable")
        self.flags = flags

    # -- page reads, isolated from the page's own JavaScript --
    def frame_id(self) -> str:
        return self.page.call("Page.getFrameTree")["frameTree"]["frame"]["id"]

    def world(self) -> int:
        return self.page.call("Page.createIsolatedWorld",
                              {"frameId": self.frame_id(), "worldName": WORLD})[
            "executionContextId"]

    def eval(self, expression: str):
        r = self.page.call("Runtime.evaluate", {"expression": expression, "contextId": self.world(),
                                                "returnByValue": True, "awaitPromise": True})
        if r.get("exceptionDetails"):
            raise RuntimeError(str(r["exceptionDetails"])[:500])
        return r.get("result", {}).get("value")

    def wait_ready(self, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.eval("document.readyState") == "complete":
                    return True
            except RuntimeError:
                pass
            time.sleep(0.25)
        return False

    # -- actions --
    def navigate(self, url: str) -> dict:
        self.page.call("Page.navigate", {"url": url})
        time.sleep(0.3)
        return {"loaded": self.wait_ready(), "url": self.eval("location.href")}

    def rect(self, selector: str) -> dict | None:
        return self.eval(
            "(() => { const e = document.querySelector(%s); if (!e) return null;"
            " const r = e.getBoundingClientRect(); return {x: r.left + r.width / 2,"
            " y: r.top + r.height / 2, top: r.top, bottom: r.bottom, vh: innerHeight}; })()"
            % json.dumps(selector))

    def scroll_into_view(self, selector: str) -> dict:
        r = self.rect(selector)
        if r is None:
            raise RuntimeError(f"no element matches {selector}")
        if r["top"] < 0 or r["bottom"] > r["vh"]:
            dy = r["top"] - r["vh"] * gauss(0.35, 0.08, 0.15, 0.55)
            self.scroll(dy)
            time.sleep(gauss(0.35, 0.1, 0.15, 0.8))
            r = self.rect(selector)
        return r

    def click(self, selector: str | None = None, x: float | None = None,
              y: float | None = None) -> dict:
        if selector:
            self.scroll_into_view(selector)
            r = self.root.call("Browser.humanizedClickSelector",
                               {"targetId": self.target_id, "selector": selector,
                                "duration": gauss(0.55, 0.12, 0.3, 1.0)})
            return {"x": r.get("x"), "y": r.get("y")}
        self.root.call("Browser.humanizedClick",
                       {"targetId": self.target_id, "x": x, "y": y,
                        "duration": gauss(0.55, 0.12, 0.3, 1.0)})
        return {"x": x, "y": y}

    def type(self, text: str, selector: str | None = None, replace: bool = False,
             typo_rate: float = 0.02, cpm: float | None = None) -> dict:
        if selector:
            focused = self.eval("document.activeElement === document.querySelector(%s)"
                                % json.dumps(selector))
            if not focused:
                self.click(selector)
            if replace:
                self.eval("(() => { const e = document.querySelector(%s);"
                          " if (e && e.select) e.select(); })()" % json.dumps(selector))
            time.sleep(gauss(0.25, 0.08, 0.1, 0.6))
        params = {"targetId": self.target_id, "text": text, "typoRate": typo_rate}
        if cpm:
            params["cpm"] = cpm
        self.root.call("Browser.humanizedType", params)
        return {"typed": len(text)}

    def scroll(self, delta_y: float, delta_x: float = 0, style: str = "auto") -> dict:
        self.root.call("Browser.humanizedScroll",
                       {"targetId": self.target_id, "deltaY": delta_y, "deltaX": delta_x,
                        "style": style})
        return {"scrollY": self.eval("scrollY")}

    def drag(self, from_xy, to_xy, duration: float = 0.5) -> dict:
        self.root.call("Browser.humanizedDrag",
                       {"targetId": self.target_id, "fromX": from_xy[0], "fromY": from_xy[1],
                        "toX": to_xy[0], "toY": to_xy[1], "duration": duration})
        return {}

    def text(self, limit: int = 20000) -> str:
        return self.eval(f"(document.body ? document.body.innerText : '').slice(0, {limit})")

    def screenshot(self) -> str:
        return self.page.call("Page.captureScreenshot", {"format": "png"})["data"]

    def close(self) -> None:
        for s in (self.page, self.root):
            try:
                s.close()
            except Exception:
                pass
        self.browser.stop()


def act(b: Camchrome, step: dict):
    """Runs one action dict (the API, MCP and replay share this)."""
    a = step.get("action")
    if a == "navigate":
        return b.navigate(step["url"])
    if a == "click":
        return b.click(step.get("selector"), step.get("x"), step.get("y"))
    if a == "type":
        return b.type(step["text"], step.get("selector"), step.get("replace", False),
                      step.get("typoRate", 0.02), step.get("cpm"))
    if a == "key":
        keys = {"Enter": "\n", "Tab": "\t"}
        if step.get("key") not in keys:
            raise ValueError(f"key {step.get('key')!r}: only Enter and Tab")
        return b.type(keys[step["key"]], typo_rate=0)
    if a == "scroll":
        return b.scroll(step.get("dy", 0), step.get("dx", 0), step.get("style", "auto"))
    if a == "drag":
        return b.drag(step["from"], step["to"], step.get("duration", 0.5))
    if a == "wait":
        time.sleep(min(step.get("ms", 500), 120000) / 1000)
        return {}
    if a == "text":
        return {"text": b.text(step.get("limit", 20000))}
    if a == "screenshot":
        return {"png_base64": b.screenshot()}
    if a == "eval":
        # Isolated world: the page cannot see or intercept this.
        return {"value": b.eval(step["expression"])}
    raise ValueError(f"unknown action {a!r}")


# ---- launching -------------------------------------------------------------------------------

def library_flags(pick: dict) -> list[str]:
    from fingerprint_library import load_library, launch_flags, select as lib_select
    entry = lib_select([e for _, e in load_library()], pick.get("os"), pick.get("gpu"),
                       pick.get("seed"))
    return launch_flags(entry)


def launch(spec: dict, base_flags: list[str], binary: str) -> Camchrome:
    flags = list(base_flags) + list(spec.get("flags", []))
    if spec.get("pick"):
        flags += library_flags(spec["pick"])
    if spec.get("proxy"):
        flags.append(f"--proxy-server={spec['proxy']}")
        if spec.get("proxy_credentials"):
            flags.append(f"--socks5-credentials={spec['proxy_credentials']}")
        if spec["proxy"].startswith("socks5"):
            flags.append("--socks5-udp")
    return Camchrome(spec.get("binary", binary), flags, spec.get("profile_dir"),
                     spec.get("headless", False))


class Manager:
    def __init__(self, binary: str, base_flags: list[str]):
        self.binary, self.base_flags = binary, base_flags
        self.browsers: dict[str, Camchrome] = {}
        self.locks: dict[str, threading.Lock] = {}
        self.counter = 0
        self.lock = threading.Lock()

    def launch(self, spec: dict) -> dict:
        b = launch(spec, self.base_flags, self.binary)
        with self.lock:
            self.counter += 1
            bid = f"b{self.counter}"
            self.browsers[bid] = b
            self.locks[bid] = threading.Lock()
        return {"id": bid, "cdp": b.browser.version["webSocketDebuggerUrl"]}

    def get(self, bid: str) -> Camchrome:
        if bid not in self.browsers:
            raise KeyError(f"no browser {bid}")
        return self.browsers[bid]

    def act(self, bid: str, step: dict):
        b = self.get(bid)
        with self.locks[bid]:
            return act(b, step)

    def close(self, bid: str) -> None:
        b = self.browsers.pop(bid)
        self.locks.pop(bid, None)
        b.close()

    def list(self) -> list[dict]:
        return [{"id": k, "cdp": v.browser.version["webSocketDebuggerUrl"]}
                for k, v in self.browsers.items()]

    def close_all(self) -> None:
        for bid in list(self.browsers):
            self.close(bid)


# ---- local HTTP API --------------------------------------------------------------------------

def cmd_api(args, base_flags) -> int:
    mgr = Manager(args.binary, base_flags)
    token = args.token or secrets.token_urlsafe(24)

    class Handler(http.server.BaseHTTPRequestHandler):
        def _reply(self, code: int, body) -> None:
            out = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def _authorised(self) -> bool:
            if self.headers.get("Authorization") == f"Bearer {token}":
                return True
            self._reply(401, {"error": "missing or wrong bearer token"})
            return False

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length", 0) or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def _route(self, method: str) -> None:
            if not self._authorised():
                return
            parts = [p for p in self.path.split("?")[0].split("/") if p]
            try:
                if parts == ["v1", "browsers"] and method == "GET":
                    return self._reply(200, mgr.list())
                if parts == ["v1", "browsers"] and method == "POST":
                    return self._reply(201, mgr.launch(self._body()))
                if len(parts) == 3 and parts[:2] == ["v1", "browsers"] and method == "DELETE":
                    mgr.close(parts[2])
                    return self._reply(200, {"closed": parts[2]})
                if len(parts) == 4 and parts[:2] == ["v1", "browsers"] and method == "POST":
                    bid, what = parts[2], parts[3]
                    if what == "actions":
                        return self._reply(200, {"result": mgr.act(bid, self._body())})
                    if what == "replay":
                        steps = self._body().get("steps", [])
                        return self._reply(200, {"result": [mgr.act(bid, s) for s in steps]})
                return self._reply(404, {"error": "unknown route"})
            except KeyError as e:
                return self._reply(404, {"error": str(e)})
            except (ValueError, RuntimeError) as e:
                return self._reply(400, {"error": str(e)})

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_DELETE(self):
            self._route("DELETE")

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer((args.bind, args.port), Handler)
    print(f"camauto API on http://{args.bind}:{args.port}/v1  token: {token}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        mgr.close_all()
    return 0


# ---- MCP server (stdio, JSON-RPC 2.0) --------------------------------------------------------

MCP_TOOLS = [
    {"name": "browser_launch",
     "description": "Launch a camchrome window. Optional: pick a library device "
                    "(os, gpu, seed), a proxy URL, extra flags, a profile directory.",
     "inputSchema": {"type": "object", "properties": {
         "os": {"type": "string", "enum": ["windows", "macos", "linux", "android"]},
         "gpu": {"type": "string"}, "seed": {"type": "string"},
         "proxy": {"type": "string"}, "proxy_credentials": {"type": "string"},
         "flags": {"type": "array", "items": {"type": "string"}},
         "profile_dir": {"type": "string"}, "headless": {"type": "boolean"}}}},
    {"name": "browser_list", "description": "List open camchrome windows.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "browser_close", "description": "Close a window.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}},
                     "required": ["id"]}},
    {"name": "browser_navigate", "description": "Open a URL and wait for it to load.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}, "url": {"type": "string"}}, "required": ["id", "url"]}},
    {"name": "browser_click",
     "description": "Click an element (CSS selector) or a point with human pointer motion.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}, "selector": {"type": "string"},
         "x": {"type": "number"}, "y": {"type": "number"}}, "required": ["id"]}},
    {"name": "browser_type",
     "description": "Type text with human rhythm, into `selector` if given, else into the "
                    "focused element. replace=true selects the field's text first.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}, "text": {"type": "string"},
         "selector": {"type": "string"}, "replace": {"type": "boolean"}},
         "required": ["id", "text"]}},
    {"name": "browser_key", "description": "Press Enter or Tab.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}, "key": {"type": "string", "enum": ["Enter", "Tab"]}},
         "required": ["id", "key"]}},
    {"name": "browser_scroll",
     "description": "Scroll by dy CSS pixels (positive is down) the way the persona's OS does.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string"}, "dy": {"type": "number"}}, "required": ["id", "dy"]}},
    {"name": "browser_text", "description": "The page's visible text (first 20k characters).",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}},
                     "required": ["id"]}},
    {"name": "browser_screenshot", "description": "A PNG screenshot of the page.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}},
                     "required": ["id"]}},
]


def mcp_call(mgr: Manager, name: str, a: dict) -> list[dict]:
    def text(v) -> list[dict]:
        return [{"type": "text", "text": v if isinstance(v, str) else json.dumps(v)}]
    if name == "browser_launch":
        spec = {k: a[k] for k in ("proxy", "proxy_credentials", "flags", "profile_dir",
                                  "headless") if k in a}
        if any(k in a for k in ("os", "gpu", "seed")):
            spec["pick"] = {k: a.get(k) for k in ("os", "gpu", "seed")}
        return text(mgr.launch(spec))
    if name == "browser_list":
        return text(mgr.list())
    if name == "browser_close":
        mgr.close(a["id"])
        return text({"closed": a["id"]})
    actions = {"browser_navigate": "navigate", "browser_click": "click", "browser_type": "type",
               "browser_key": "key", "browser_scroll": "scroll", "browser_text": "text",
               "browser_screenshot": "screenshot"}
    if name not in actions:
        raise ValueError(f"unknown tool {name}")
    step = {k: v for k, v in a.items() if k != "id"}
    step["action"] = actions[name]
    result = mgr.act(a["id"], step)
    if name == "browser_screenshot":
        return [{"type": "image", "data": result["png_base64"], "mimeType": "image/png"}]
    if name == "browser_text":
        return text(result["text"])
    return text(result)


def cmd_mcp(args, base_flags) -> int:
    mgr = Manager(args.binary, base_flags)

    def send(msg: dict) -> None:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                send({"jsonrpc": "2.0", "id": None,
                      "error": {"code": -32700, "message": "parse error"}})
                continue
            rid, method = req.get("id"), req.get("method")
            if rid is None:
                continue  # notifications (initialized, cancelled) need no answer
            try:
                if method == "initialize":
                    result = {"protocolVersion": req.get("params", {}).get(
                                  "protocolVersion", "2025-06-18"),
                              "capabilities": {"tools": {}},
                              "serverInfo": {"name": "camchrome", "version": "1.0"}}
                elif method == "tools/list":
                    result = {"tools": MCP_TOOLS}
                elif method == "tools/call":
                    p = req.get("params", {})
                    try:
                        result = {"content": mcp_call(mgr, p.get("name"),
                                                      p.get("arguments") or {})}
                    except (KeyError, ValueError, RuntimeError) as e:
                        result = {"content": [{"type": "text", "text": str(e)}],
                                  "isError": True}
                elif method == "ping":
                    result = {}
                else:
                    send({"jsonrpc": "2.0", "id": rid,
                          "error": {"code": -32601, "message": f"no method {method}"}})
                    continue
                send({"jsonrpc": "2.0", "id": rid, "result": result})
            except Exception as e:  # noqa: BLE001 -- never kill the server on one request
                send({"jsonrpc": "2.0", "id": rid,
                      "error": {"code": -32603, "message": str(e)}})
    finally:
        mgr.close_all()
    return 0


# ---- recorder --------------------------------------------------------------------------------

RECORDER_JS = r"""
(() => {
  // Runs in the camauto isolated world: the page's JavaScript cannot see these listeners.
  if (globalThis.__camautoInstalled) return;
  globalThis.__camautoInstalled = true;
  const queue = [];
  const send = ev => {
    ev.t = Date.now();
    if (typeof globalThis.%BINDING% === 'function') {
      while (queue.length) globalThis.%BINDING%(JSON.stringify(queue.shift()));
      globalThis.%BINDING%(JSON.stringify(ev));
    } else {
      queue.push(ev);
    }
  };
  globalThis.__camautoFlush = () => { send({type: 'ping'}); };
  const unique = sel => { try { return document.querySelectorAll(sel).length === 1; }
                          catch (e) { return false; } };
  const selectorFor = el => {
    if (!(el instanceof Element)) return null;
    if (el.id && unique('#' + CSS.escape(el.id))) return '#' + CSS.escape(el.id);
    for (const a of ['data-testid', 'data-test', 'name', 'aria-label', 'placeholder']) {
      const v = el.getAttribute(a);
      if (v) {
        const s = `${el.localName}[${a}="${CSS.escape(v)}"]`;
        if (unique(s)) return s;
      }
    }
    for (const c of el.classList) {
      const s = `${el.localName}.${CSS.escape(c)}`;
      if (unique(s)) return s;
    }
    const parts = [];
    let e = el;
    while (e && e.nodeType === 1 && e !== document.body && parts.length < 8) {
      if (e.id && unique('#' + CSS.escape(e.id))) { parts.unshift('#' + CSS.escape(e.id)); break; }
      let i = 1, s = e;
      while ((s = s.previousElementSibling)) if (s.localName === e.localName) i++;
      parts.unshift(`${e.localName}:nth-of-type(${i})`);
      e = e.parentElement;
    }
    if (e === document.body) parts.unshift('body');
    return parts.join(' > ');
  };
  const editable = el => el && (el.isContentEditable ||
      (el.localName === 'input' && !/^(button|submit|checkbox|radio|file|range|color)$/.test(el.type)) ||
      el.localName === 'textarea');
  const startValue = new WeakMap();
  addEventListener('focusin', e => {
    if (editable(e.target)) startValue.set(e.target, e.target.value ?? e.target.textContent);
  }, true);
  const flushInput = el => {
    if (!editable(el) || !startValue.has(el)) return;
    const before = startValue.get(el), now = el.value ?? el.textContent;
    if (now === before) return;
    const sensitive = el.type === 'password';
    if (before && now.startsWith(before))
      send({type: 'type', selector: selectorFor(el), text: now.slice(before.length), sensitive});
    else
      send({type: 'type', selector: selectorFor(el), text: now, replace: !!before, sensitive});
    startValue.set(el, now);
  };
  addEventListener('focusout', e => flushInput(e.target), true);
  addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === 'Tab') {
      flushInput(e.target);
      send({type: 'key', key: e.key});
    }
  }, true);
  addEventListener('click', e => {
    if (!e.isTrusted) return;
    send({type: 'click', selector: selectorFor(e.target), x: e.clientX, y: e.clientY});
  }, true);
  let lastY = scrollY, timer = 0;
  addEventListener('scroll', () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const dy = scrollY - lastY;
      lastY = scrollY;
      if (Math.abs(dy) > 4) send({type: 'scroll', dy});
    }, 350);
  }, true);
  addEventListener('pagehide', () => {
    const el = document.activeElement;
    if (el) flushInput(el);
  }, true);
})();
""".replace("%BINDING%", BINDING)


class Recorder:
    """Records a leader window's actions as replayable steps."""

    def __init__(self, b: Camchrome):
        self.b = b
        b.page.call("Page.addScriptToEvaluateOnNewDocument",
                    {"source": RECORDER_JS, "worldName": WORLD})
        self.last_t: float | None = None
        self.bind()

    def bind(self) -> None:
        """Installs the reporting binding into the current document's camauto world only.
        The engine does not carry bindings into new contexts, so this runs per navigation."""
        try:
            ctx = self.b.world()
            self.b.page.call("Runtime.addBinding", {"name": BINDING, "executionContextId": ctx})
            # The script is auto-injected into new documents only; this covers the document
            # that was already open (the script skips itself where it already ran).
            self.b.page.call("Runtime.evaluate", {"expression": RECORDER_JS, "contextId": ctx})
            self.b.page.call("Runtime.evaluate", {"expression": "__camautoFlush()",
                                                  "contextId": ctx})
        except RuntimeError:
            pass

    def steps(self, stop: threading.Event):
        """Yields recorded steps until `stop` is set."""
        while not stop.is_set():
            ev = self.b.page.next_event(0.2)
            if ev is None:
                continue
            m, p = ev["method"], ev.get("params", {})
            if m == "Page.frameNavigated" and not p["frame"].get("parentId"):
                url = p["frame"]["url"]
                yield self._step({"action": "navigate", "url": url})
            elif m == "Page.domContentEventFired":
                self.bind()
            elif m == "Runtime.bindingCalled" and p.get("name") == BINDING:
                d = json.loads(p["payload"])
                kind = d.pop("type")
                if kind == "ping":
                    continue
                if d.get("sensitive"):
                    d["text"] = "${SECRET}"
                d.pop("sensitive", None)
                d.pop("t", None)
                if kind == "scroll":
                    yield self._step({"action": "scroll", "dy": d["dy"]})
                else:
                    yield self._step({"action": kind, **d})

    def _step(self, step: dict) -> dict:
        now = time.time()
        step["delay_ms"] = 0 if self.last_t is None else int((now - self.last_t) * 1000)
        self.last_t = now
        return step


def coalesce_navigation(steps: list[dict]) -> list[dict]:
    """A navigation caused by a click or Enter is replayed by that action, not by a URL jump."""
    out = []
    for s in steps:
        if (s["action"] == "navigate" and out and out[-1]["action"] in ("click", "key")
                and s["delay_ms"] < 3000):
            out[-1]["expect_navigation"] = s["url"]
            continue
        out.append(s)
    return out


def replay(b: Camchrome, steps: list[dict], speed: float = 1.0, secrets_map=None) -> None:
    for s in steps:
        time.sleep(min(s.get("delay_ms", 0), 15000) / 1000 / speed)
        step = dict(s)
        if step.get("text") == "${SECRET}":
            step["text"] = (secrets_map or {}).get(step.get("selector"), "")
        act(b, step)
        if step.get("expect_navigation"):
            time.sleep(0.5)
            b.wait_ready()


def cmd_record(args, base_flags) -> int:
    b = launch({}, base_flags, args.binary)
    rec = Recorder(b)
    if args.url:
        b.navigate(args.url)
    stop = threading.Event()
    steps: list[dict] = []
    print("recording; use the window, then press Ctrl-C here to stop", file=sys.stderr)
    try:
        for step in rec.steps(stop):
            steps.append(step)
            print(json.dumps(step), file=sys.stderr)
    except KeyboardInterrupt:
        pass
    finally:
        b.close()
    steps = coalesce_navigation(steps)
    Path(args.out).write_text(json.dumps({"version": 1, "steps": steps}, indent=1) + "\n")
    print(f"wrote {len(steps)} steps to {args.out}", file=sys.stderr)
    return 0


def cmd_replay(args, base_flags) -> int:
    flow = json.loads(Path(args.flow).read_text())
    b = launch({}, base_flags, args.binary)
    try:
        replay(b, flow["steps"], args.speed, json.loads(args.secrets) if args.secrets else None)
        if args.keep_open:
            input("replayed; press Enter to close ")
    finally:
        b.close()
    return 0


# ---- synchronised windows --------------------------------------------------------------------

def cmd_sync(args, base_flags) -> int:
    windows = []
    for n in range(args.count):
        each = shlex.split(args.flags_each.replace("{n}", str(n))) if args.flags_each else []
        windows.append(launch({"flags": each}, base_flags, args.binary))
    leader, followers = windows[0], windows[1:]
    rec = Recorder(leader)
    queues = [collections.deque() for _ in followers]
    stop = threading.Event()

    def follow(b: Camchrome, q: collections.deque) -> None:
        while not stop.is_set():
            if not q:
                time.sleep(0.05)
                continue
            step = q.popleft()
            # Each follower lags by its own human reaction time.
            time.sleep(gauss(0.35, 0.15, 0.1, 0.9))
            try:
                act(b, step)
            except Exception as e:  # noqa: BLE001 -- one window failing must not stop the rest
                print(f"follower: {step['action']} failed: {e}", file=sys.stderr)

    threads = [threading.Thread(target=follow, args=(f, q), daemon=True)
               for f, q in zip(followers, queues)]
    for t in threads:
        t.start()
    if args.url:
        for q in queues:
            q.append({"action": "navigate", "url": args.url})
        leader.navigate(args.url)
    print(f"{args.count} windows; drive the first, Ctrl-C to stop", file=sys.stderr)
    last = None
    try:
        for step in rec.steps(stop):
            prev, last = last, step
            if (step["action"] == "navigate" and prev and prev["action"] in ("click", "key")
                    and step["delay_ms"] < 3000):
                continue  # the follower's own click or Enter makes this navigation
            if step.get("text") == "${SECRET}":
                continue  # never mirror password fields
            for q in queues:
                q.append(step)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        for w in windows:
            w.close()
    return 0


def main() -> int:
    argv = sys.argv[1:]
    base_flags: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, base_flags = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--binary", default=os.environ.get("CAMCHROME_BINARY", str(DEFAULT_BINARY)))
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("api", help="local HTTP API")
    a.add_argument("--bind", default="127.0.0.1")
    a.add_argument("--port", type=int, default=7878)
    a.add_argument("--token", help="bearer token (default: random, printed)")
    sub.add_parser("mcp", help="MCP server on stdio")
    r = sub.add_parser("record", help="record a window into a replayable flow")
    r.add_argument("--out", default="flow.json")
    r.add_argument("--url")
    p = sub.add_parser("replay", help="replay a recorded flow with human input")
    p.add_argument("flow")
    p.add_argument("--speed", type=float, default=1.0, help="2 = twice as fast between steps")
    p.add_argument("--secrets", help='JSON {selector: text} for recorded password fields')
    p.add_argument("--keep-open", action="store_true")
    s = sub.add_parser("sync", help="synchronised windows: the first leads, the rest follow")
    s.add_argument("--count", type=int, default=2)
    s.add_argument("--url")
    s.add_argument("--flags-each", help='extra flags per window; "{n}" is the window number')
    args = ap.parse_args(argv)
    return {"api": cmd_api, "mcp": cmd_mcp, "record": cmd_record, "replay": cmd_replay,
            "sync": cmd_sync}[args.cmd](args, base_flags)


if __name__ == "__main__":
    sys.exit(main())
