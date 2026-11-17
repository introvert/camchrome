"""Minimal Chrome DevTools Protocol client (standard library only).

The stealth build is tested without Playwright/Puppeteer: the browser is
launched with exactly the flags under test and driven over CDP, so the harness
adds no automation flags, bindings or ``Runtime.enable`` side effects of its own.

Why not Playwright: every injected driver is itself detectable (``webdriver``,
Runtime.enable, exposed bindings, default flags). Engine-level stealth has to be
measured on a browser that no driver has touched. This module is deliberately
small: open a WebSocket to the page target, send JSON, read JSON.
"""

from __future__ import annotations

import base64
import json
import os
import random
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request

__all__ = ["WebSocket", "CDP", "Browser"]


class WebSocket:
    """RFC 6455 client for CDP's ws:// endpoint (no TLS, no extensions)."""

    def __init__(self, url: str, timeout: float = 60.0):
        if not url.startswith("ws://"):
            raise ValueError(f"unsupported websocket url: {url}")
        hostport, _, path = url[5:].partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("websocket handshake: connection closed")
            response += chunk
        status = response.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            raise RuntimeError(f"websocket handshake failed: {status!r}")
        self._buffer = response.split(b"\r\n\r\n", 1)[1]

    def _read(self, n: int) -> bytes:
        while len(self._buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("websocket closed")
            self._buffer += chunk
        out, self._buffer = self._buffer[:n], self._buffer[n:]
        return out

    def _frame(self):
        b1, b2 = self._read(2)
        fin = bool(b1 & 0x80)
        opcode = b1 & 0x0F
        masked = b2 & 0x80
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read(8))[0]
        mask = self._read(4) if masked else b""
        data = self._read(length)
        if masked:
            data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
        return fin, opcode, data

    def send(self, text: str) -> None:
        data = text.encode()
        header = bytearray([0x81])
        if len(data) < 126:
            header.append(0x80 | len(data))
        elif len(data) < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", len(data))
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", len(data))
        mask = os.urandom(4)
        header += mask
        self.sock.sendall(bytes(header) + bytes(c ^ mask[i % 4] for i, c in enumerate(data)))

    def recv(self) -> str:
        parts = []
        while True:
            fin, opcode, data = self._frame()
            if opcode == 0x9:  # ping -> pong
                mask = os.urandom(4)
                self.sock.sendall(bytes([0x8A, 0x80]) + mask)
                continue
            if opcode == 0x8:
                raise ConnectionError("websocket closed by peer")
            if opcode == 0xA:
                continue
            parts.append(data)
            if fin:
                break
        return b"".join(parts).decode()

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class CDP:
    """One CDP session (a page target)."""

    def __init__(self, ws_url: str, timeout: float = 60.0):
        self.ws = WebSocket(ws_url, timeout=timeout)
        self._next_id = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        request_id = self._next_id
        self.ws.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
            # Events are irrelevant to this harness; ignore them.

    def evaluate(self, expression: str, await_promise: bool = True):
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "userGesture": True,
            },
        )
        if result.get("exceptionDetails"):
            raise RuntimeError(str(result["exceptionDetails"])[:2000])
        value = result.get("result", {})
        if value.get("subtype") == "error":
            raise RuntimeError(value.get("description", "evaluation failed"))
        return value.get("value")

    def wait_for(self, expression: str, timeout: float = 30.0, interval: float = 0.2) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.evaluate(expression):
                    return True
            except Exception:
                pass
            time.sleep(interval)
        return False

    def close(self) -> None:
        self.ws.close()


class Browser:
    """Launches the built browser with an explicit flag set and no driver."""

    def __init__(
        self,
        binary: str,
        flags=(),
        profile: str | None = None,
        port: int | None = None,
        headless: bool = False,
        log_path: str | None = None,
        timeout: float = 60.0,
    ):
        self.binary = binary
        self.profile = profile or tempfile.mkdtemp(prefix="camchrome-profile-")
        self._owns_profile = profile is None
        self.port = port or random.randint(9500, 9900)
        self.log_path = log_path
        self._log = open(log_path, "wb") if log_path else subprocess.DEVNULL

        args = [
            binary,
            "--no-sandbox",
            # Without this a first navigation blocks on the macOS Keychain
            # prompt when the browser UI thread cannot show it (CI/headless).
            "--use-mock-keychain",
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile}",
            "--no-first-run",
            "--no-default-browser-check",
            # These three keep a background/occluded tab from being throttled,
            # which otherwise stretches the audit's timers by minutes.
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
        ]
        if headless:
            args.append("--headless=new")
        args += list(flags)
        args.append("about:blank")

        self.proc = subprocess.Popen(args, stdout=self._log, stderr=subprocess.STDOUT, start_new_session=True)
        self.version = None
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self._close_log()
                raise RuntimeError(f"browser exited early (code {self.proc.returncode})")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/version", timeout=1) as r:
                    self.version = json.load(r)
                break
            except Exception:
                time.sleep(0.2)
        if not self.version:
            self.stop()
            raise RuntimeError("browser did not expose a CDP endpoint")

    def open_page(self, timeout: float = 30.0) -> CDP:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=1) as r:
                    targets = json.load(r)
            except Exception:
                targets = []
            pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
            # Prefer the blank tab: chrome:// browser_ui targets reject navigation.
            pages.sort(key=lambda t: 0 if t.get("url") == "about:blank" else 1)
            for target in pages:
                if not target.get("url", "").startswith("chrome://"):
                    return CDP(target["webSocketDebuggerUrl"])
            time.sleep(0.2)
        raise RuntimeError("no page target")

    def _close_log(self) -> None:
        if self._log not in (None, subprocess.DEVNULL):
            try:
                self._log.close()
            except Exception:
                pass

    def stop(self) -> None:
        try:
            if self.proc.poll() is None:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        finally:
            self._close_log()
            if self._owns_profile:
                shutil.rmtree(self.profile, ignore_errors=True)
