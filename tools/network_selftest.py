#!/usr/bin/env python3
"""Local self-test for camchrome's network, input and profile patches.

tools/fingerprint_selftest.py covers the persona surfaces. This covers the patches it cannot
reach with one page load: the ones that need a proxy, a QUIC server, a STUN server, trusted
input or a second launch. Everything runs on loopback; no network is needed.

    tools/network_selftest.py                      # build/src/out/Default/chrome
    tools/network_selftest.py --binary /path/chrome --stock    # harness check on stock Chrome
    tools/network_selftest.py --only quic-over-socks5,webrtc-relay-exit-ip

Checks (each names the patch it covers):
  socks5-auth                 963, 984  CONNECT through a SOCKS5 proxy with RFC 1929 credentials
  socks5-auth-wrong-password  963, 984  the same proxy refuses a wrong password (control)
  http-proxy-auth             978       --proxy-auth answers an HTTP proxy's Basic challenge
  quic-over-socks5            990       HTTP/3 to a local aioquic server through the SOCKS5 UDP relay,
                                        the origin sent to the proxy by NAME
  quic-socks5-no-relay        990       control: without --socks5-udp no QUIC packet leaves at all
  webrtc-relay-exit-ip        973, 991  STUN through the relay; srflx present, no host candidate
  humanized-click             130       Browser.humanizedClickSelector: trusted click after a path
  no-fake-shadow-root         960       Element.prototype has no fakeShadowRoot
  rects-not-noised            050       getClientRects()[0] equals getBoundingClientRect()
  no-farble-switch            220       --cc-no-farble: canvas reads equal across sites
  portable-profile            964       a copied --portable-profile keeps its cookies

The QUIC checks need the `aioquic` Python package (pip install aioquic); without it they SKIP.
WebRTC in a headless browser on a server may gather no candidates at all; that is a SKIP.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import datetime
import hashlib
import http.server
import json
import os
import shutil
import socket
import socketserver
import ssl
import struct
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cdp import CDP, Browser  # noqa: E402
from socks5_udp_proxy import Proxy, serve  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

PAGE = """<!doctype html><title>camchrome network self-test</title>
<body style="margin:0">
<div id="box" style="position:absolute;left:37.3px;top:41.7px;width:100.4px;height:20.2px">x</div>
<button id="btn" style="position:absolute;left:400px;top:300px;width:120px;height:40px">go</button>
<script>
window.moves = 0; window.clicks = [];
addEventListener('mousemove', () => window.moves++);
document.getElementById('btn').addEventListener('click', e =>
  window.clicks.push({trusted: e.isTrusted, x: e.clientX, y: e.clientY, moves: window.moves}));
</script>"""


# ---- small servers ---------------------------------------------------------------------------

class Loop:
    """An asyncio loop in a background thread for the proxies and UDP servers."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def run(self, coro, timeout=30):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)


class HttpServer:
    def __init__(self):
        self.requests: list[dict] = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append({"path": self.path, "headers": dict(self.headers)})
                body = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class HttpAuthProxy:
    """A forward HTTP proxy that demands Basic credentials (407) before relaying a GET."""

    def __init__(self, user: str, password: str):
        self.expected = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        self.challenged = 0
        self.authorized = 0
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.headers.get("Proxy-Authorization") != outer.expected:
                    outer.challenged += 1
                    self.send_response(407)
                    self.send_header("Proxy-Authenticate", 'Basic realm="selftest"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                outer.authorized += 1
                import urllib.request
                req = urllib.request.Request(self.path)
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req) as r:
                    body = r.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Stun(asyncio.DatagramProtocol):
    """Answers STUN binding requests with XOR-MAPPED-ADDRESS (RFC 5389)."""

    def __init__(self):
        self.seen: list[tuple] = []

    def connection_made(self, t):
        self.t = t

    def datagram_received(self, data, addr):
        if len(data) < 20 or data[0:2] != b"\x00\x01" or data[4:8] != b"\x21\x12\xa4\x42":
            return
        self.seen.append(addr)
        txid = data[8:20]
        ip = socket.inet_aton(addr[0])
        xport = addr[1] ^ 0x2112
        xip = bytes(b ^ m for b, m in zip(ip, b"\x21\x12\xa4\x42"))
        attr = struct.pack("!HHBBH", 0x0020, 8, 0, 1, xport) + xip
        self.t.sendto(struct.pack("!HHI", 0x0101, len(attr), 0x2112A442) + txid + attr, addr)


def make_cert(tmp: Path) -> tuple[Path, Path, str]:
    """Self-signed cert for localhost; returns (cert, key, SPKI sha256 base64)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=10))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
            .sign(key, hashes.SHA256()))
    cp, kp = tmp / "cert.pem", tmp / "key.pem"
    cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kp.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                     serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    spki = cert.public_key().public_bytes(serialization.Encoding.DER,
                                          serialization.PublicFormat.SubjectPublicKeyInfo)
    return cp, kp, base64.b64encode(hashlib.sha256(spki).digest()).decode()


async def start_h3(cert: Path, key: Path, hits: list):
    """A minimal HTTP/3 server on 127.0.0.1 that serves PAGE and counts requests."""
    from aioquic.asyncio import QuicConnectionProtocol, serve as quic_serve
    from aioquic.h3.connection import H3_ALPN, H3Connection
    from aioquic.h3.events import HeadersReceived
    from aioquic.quic.configuration import QuicConfiguration

    class P(QuicConnectionProtocol):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.h3 = H3Connection(self._quic)

        def quic_event_received(self, event):
            for ev in self.h3.handle_event(event):
                if isinstance(ev, HeadersReceived):
                    hits.append(dict(ev.headers).get(b":path", b"").decode())
                    body = PAGE.encode()
                    self.h3.send_headers(ev.stream_id, [
                        (b":status", b"200"), (b"content-type", b"text/html"),
                        (b"content-length", str(len(body)).encode())])
                    self.h3.send_data(ev.stream_id, body, end_stream=True)
            self.transmit()

    cfg = QuicConfiguration(is_client=False, alpn_protocols=H3_ALPN)
    cfg.load_cert_chain(str(cert), str(key))
    server = await quic_serve("127.0.0.1", 0, configuration=cfg, create_protocol=P)
    port = server._transport.get_extra_info("sockname")[1]
    return server, port


# ---- reporting -------------------------------------------------------------------------------

@dataclass
class Check:
    id: str
    patches: str
    status: str
    detail: str = ""


@dataclass
class Report:
    stock: bool
    checks: list[Check] = field(default_factory=list)

    def add(self, cid, patches, ok, detail="", skip=False):
        status = "SKIP" if skip else "PASS" if ok else "XFAIL" if self.stock else "FAIL"
        self.checks.append(Check(cid, patches, status, str(detail)[:400]))


# ---- helpers ---------------------------------------------------------------------------------

def page_loads(cdp: CDP, url: str, timeout=20) -> bool:
    cdp.call("Page.navigate", {"url": url})
    return cdp.wait_for("document.title === 'camchrome network self-test'", timeout=timeout)


def launch(args, flags, profile=None):
    return Browser(args.binary, flags=["--lang=en-US", "--window-size=1280,800"] + flags,
                   headless=args.headless, profile=profile)


def proxy_flags(url: str) -> list[str]:
    # Loopback is bypassed by default; "<-loopback>" sends it through the proxy too.
    return [f"--proxy-server={url}", "--proxy-bypass-list=<-loopback>"]


# ---- checks ----------------------------------------------------------------------------------

def check_socks5_auth(args, rep, loop, http):
    proxy = Proxy(("u", "pw"), False, None, False)
    srv = loop.run(serve("127.0.0.1", 0, proxy))
    port = srv.sockets[0].getsockname()[1]
    try:
        for cid, creds, want in (("socks5-auth", "u:pw", True),
                                 ("socks5-auth-wrong-password", "u:bad", False)):
            before = proxy.connects
            b = launch(args, proxy_flags(f"socks5://127.0.0.1:{port}") +
                       [f"--socks5-credentials={creds}"])
            try:
                cdp = b.open_page()
                cdp.call("Page.enable")
                loaded = page_loads(cdp, f"http://localhost:{http.port}/", timeout=10)
                cdp.close()
            finally:
                b.stop()
            through = proxy.connects > before
            if want:
                rep.add(cid, "963, 984", loaded and through,
                        f"loaded={loaded} connects through proxy={proxy.connects - before}")
            else:
                rep.add(cid, "963, 984", not loaded and not through,
                        f"loaded={loaded} connects={proxy.connects - before}")
    finally:
        srv.close()


def check_http_proxy_auth(args, rep, http):
    proxy = HttpAuthProxy("u", "pw")
    try:
        b = launch(args, proxy_flags(f"http://127.0.0.1:{proxy.port}") + ["--proxy-auth=u:pw"])
        try:
            cdp = b.open_page()
            cdp.call("Page.enable")
            loaded = page_loads(cdp, f"http://localhost:{http.port}/", timeout=10)
            cdp.close()
        finally:
            b.stop()
        rep.add("http-proxy-auth", "978", loaded and proxy.authorized > 0,
                f"loaded={loaded} challenged={proxy.challenged} authorized={proxy.authorized}")
    finally:
        proxy.stop()


def check_quic(args, rep, loop, tmp):
    try:
        import aioquic  # noqa: F401
    except ImportError:
        for cid in ("quic-over-socks5", "quic-socks5-no-relay"):
            rep.add(cid, "990", True, "aioquic not installed", skip=True)
        return
    cert, key, spki = make_cert(tmp)
    hits: list = []
    h3, h3_port = loop.run(start_h3(cert, key, hits))
    proxy = Proxy(None, False, None, False)
    srv = loop.run(serve("127.0.0.1", 0, proxy))
    pport = srv.sockets[0].getsockname()[1]
    origin = f"localhost:{h3_port}"
    base = proxy_flags(f"socks5://127.0.0.1:{pport}") + [
        f"--origin-to-force-quic-on={origin}",
        f"--ignore-certificate-errors-spki-list={spki}"]
    try:
        for cid, relay in (("quic-over-socks5", True), ("quic-socks5-no-relay", False)):
            n_hits, n_assoc = len(hits), len(proxy.closed)
            b = launch(args, base + (["--socks5-udp"] if relay else []))
            try:
                cdp = b.open_page()
                cdp.call("Page.enable")
                loaded = page_loads(cdp, f"https://{origin}/", timeout=15)
                proto = cdp.evaluate(
                    "performance.getEntriesByType('navigation')[0]?.nextHopProtocol || ''") \
                    if loaded else ""
                cdp.close()
            finally:
                b.stop()
            time.sleep(0.5)
            assocs = proxy.closed[n_assoc:]
            named = any("localhost" in a["names"] and a["to_remote_packets"] for a in assocs)
            unnamed = [a for a in assocs if a["to_remote_packets"] and not a["names"]]
            if relay:
                rep.add(cid, "990", loaded and proto == "h3" and named and not unnamed,
                        f"loaded={loaded} protocol={proto!r} h3 requests={len(hits) - n_hits} "
                        f"associations={assocs}")
            else:
                # Stock behaviour: QUIC refuses a SOCKS5 proxy, and nothing may reach the
                # server directly either.
                rep.add(cid, "990", not loaded and len(hits) == n_hits,
                        f"loaded={loaded} h3 requests={len(hits) - n_hits}")
    finally:
        srv.close()
        h3.close()


def check_webrtc(args, rep, loop, http):
    stun = Stun()
    t, _ = loop.run(loop.loop.create_datagram_endpoint(lambda: stun,
                                                       local_addr=("127.0.0.1", 0)))
    stun_port = t.get_extra_info("sockname")[1]
    proxy = Proxy(None, False, None, False)
    srv = loop.run(serve("127.0.0.1", 0, proxy))
    pport = srv.sockets[0].getsockname()[1]
    try:
        b = launch(args, proxy_flags(f"socks5://127.0.0.1:{pport}") + ["--socks5-udp"])
        try:
            cdp = b.open_page()
            cdp.call("Page.enable")
            page_loads(cdp, f"http://localhost:{http.port}/", timeout=10)
            cands = cdp.evaluate("""new Promise(resolve => {
              const pc = new RTCPeerConnection({iceServers: [{urls: 'stun:127.0.0.1:%d'}]});
              const out = [];
              pc.createDataChannel('x');
              pc.onicecandidate = e => { if (e.candidate) out.push(e.candidate.candidate);
                                         else resolve(out); };
              pc.createOffer().then(o => pc.setLocalDescription(o));
              setTimeout(() => resolve(out), 8000);
            })""" % stun_port)
            cdp.close()
        finally:
            b.stop()
        time.sleep(0.3)
        cands = cands or []
        host = [c for c in cands if " typ host" in c]
        srflx = [c for c in cands if " typ srflx" in c]
        relayed = any(f"127.0.0.1:{stun_port}" in a["destinations"] for a in proxy.closed)
        if not cands:
            rep.add("webrtc-relay-exit-ip", "973, 991", True,
                    "no ICE candidates gathered (headless/no interfaces)", skip=True)
        else:
            rep.add("webrtc-relay-exit-ip", "973, 991",
                    bool(srflx) and not host and relayed and stun.seen,
                    f"srflx={srflx} host={host} stun requests={len(stun.seen)} "
                    f"relayed via proxy={relayed}")
    finally:
        srv.close()
        t.close()


def check_page_and_input(args, rep, http, seed):
    flags = [] if args.stock else [f"--fingerprint={seed}"]
    b = launch(args, flags)
    try:
        cdp = b.open_page()
        cdp.call("Page.enable")
        page_loads(cdp, f"http://127.0.0.1:{http.port}/")
        rep.add("no-fake-shadow-root", "960",
                cdp.evaluate("!('fakeShadowRoot' in Element.prototype)"))
        r = cdp.evaluate("""(() => { const e = document.getElementById('box');
            const a = e.getClientRects()[0], b = e.getBoundingClientRect();
            return [a.x, a.y, a.width, a.height, b.x, b.y, b.width, b.height]; })()""")
        rep.add("rects-not-noised", "050", r[:4] == r[4:], r)

        browser_cdp = CDP(b.version["webSocketDebuggerUrl"])
        try:
            targets = browser_cdp.call("Target.getTargets")["targetInfos"]
            tid = next(t["targetId"] for t in targets
                       if t["type"] == "page" and str(http.port) in t["url"])
            try:
                browser_cdp.call("Browser.humanizedClickSelector",
                                 {"targetId": tid, "selector": "#btn", "duration": 0.5})
                err = ""
            except RuntimeError as e:
                err = str(e)
            time.sleep(0.5)
            clicks = cdp.evaluate("window.clicks")
            ok = (not err and len(clicks) == 1 and clicks[0]["trusted"]
                  and clicks[0]["moves"] >= 5
                  and 400 <= clicks[0]["x"] <= 520 and 300 <= clicks[0]["y"] <= 340)
            rep.add("humanized-click", "130", ok, err or clicks)
        finally:
            browser_cdp.close()
        cdp.close()
    finally:
        b.stop()


def canvas_reads(args, flags, http) -> list[str]:
    b = launch(args, flags)
    out = []
    try:
        cdp = b.open_page()
        cdp.call("Page.enable")
        for host in ("127.0.0.1", "localhost"):
            page_loads(cdp, f"http://{host}:{http.port}/")
            out.append(cdp.evaluate("""(() => { const c = document.createElement('canvas');
              c.width = 200; c.height = 50; const x = c.getContext('2d');
              x.font = '20px sans-serif'; x.fillStyle = '#f60'; x.fillRect(10, 5, 80, 30);
              x.fillStyle = '#069'; x.fillText('camchrome ✓', 4, 30);
              return c.toDataURL(); })()"""))
        cdp.close()
    finally:
        b.stop()
    return out


def check_no_farble(args, rep, http, seed):
    if args.stock:
        rep.add("no-farble-switch", "220", False, "needs a persona")
        return
    noisy = canvas_reads(args, [f"--fingerprint={seed}"], http)
    plain = canvas_reads(args, [f"--fingerprint={seed}", "--cc-no-farble"], http)
    rep.add("no-farble-switch", "220", noisy[0] != noisy[1] and plain[0] == plain[1],
            f"with noise differs across sites={noisy[0] != noisy[1]}, "
            f"--cc-no-farble equal={plain[0] == plain[1]}")


def check_portable_profile(args, rep, http, tmp):
    src, dst = tmp / "profile-a", tmp / "profile-b"
    flags = ["--portable-profile", "--password-store=basic"]
    b = launch(args, flags, profile=str(src))
    try:
        cdp = b.open_page()
        cdp.call("Page.enable")
        page_loads(cdp, f"http://127.0.0.1:{http.port}/")
        cdp.evaluate("document.cookie = 'camchrome=kept; max-age=86400; path=/'")
        cdp.close()
        # A clean shutdown flushes the cookie store; a signal may not.
        bc = CDP(b.version["webSocketDebuggerUrl"])
        try:
            bc.call("Browser.close")
        except Exception:
            pass
        bc.close()
        b.proc.wait(timeout=15)
    finally:
        b.stop()
    shutil.copytree(src, dst, symlinks=True, ignore=shutil.ignore_patterns("Singleton*"))
    b = launch(args, flags, profile=str(dst))
    try:
        cdp = b.open_page()
        cdp.call("Page.enable")
        page_loads(cdp, f"http://127.0.0.1:{http.port}/")
        cookie = cdp.evaluate("document.cookie")
        cdp.close()
    finally:
        b.stop()
    rep.add("portable-profile", "964", "camchrome=kept" in (cookie or ""), cookie)


CHECKS = ["socks5-auth", "http-proxy-auth", "quic", "webrtc", "page", "no-farble",
          "portable-profile"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--binary", default=str(REPO / "build/src/out/Default/chrome"))
    ap.add_argument("--seed", default="selftest-net")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--stock", action="store_true",
                    help="stock Chromium: engine-only failures are XFAIL")
    ap.add_argument("--only", help="comma-separated groups: " + ",".join(CHECKS))
    ap.add_argument("--json", default=str(REPO / "build/network-selftest.json"))
    args = ap.parse_args()
    if not Path(args.binary).exists():
        print(f"browser not found: {args.binary}", file=sys.stderr)
        return 2
    want = set(args.only.split(",")) if args.only else set(CHECKS)

    rep = Report(stock=args.stock)
    loop = Loop()
    http = HttpServer()
    tmp = Path(tempfile.mkdtemp(prefix="camchrome-netst-"))
    groups = {
        "socks5-auth": lambda: check_socks5_auth(args, rep, loop, http),
        "http-proxy-auth": lambda: check_http_proxy_auth(args, rep, http),
        "quic": lambda: check_quic(args, rep, loop, tmp),
        "webrtc": lambda: check_webrtc(args, rep, loop, http),
        "page": lambda: check_page_and_input(args, rep, http, args.seed),
        "no-farble": lambda: check_no_farble(args, rep, http, args.seed),
        "portable-profile": lambda: check_portable_profile(args, rep, http, tmp),
    }
    try:
        for name in CHECKS:
            if name not in want:
                continue
            try:
                groups[name]()
            except Exception as e:  # one broken group must not hide the rest
                rep.add(name, "?", False, f"harness error: {type(e).__name__}: {e}")
    finally:
        http.stop()
        loop.stop()
        shutil.rmtree(tmp, ignore_errors=True)

    width = max(len(c.id) for c in rep.checks)
    for c in rep.checks:
        line = f"{c.status:5}  {c.id:<{width}}  [{c.patches}]"
        if c.status != "PASS" and c.detail:
            line += f"  {c.detail}"
        print(line)
    counts = {s: sum(c.status == s for c in rep.checks) for s in ("PASS", "FAIL", "XFAIL", "SKIP")}
    print("\n" + "  ".join(f"{k} {v}" for k, v in counts.items()))
    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([c.__dict__ for c in rep.checks], indent=1) + "\n")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
