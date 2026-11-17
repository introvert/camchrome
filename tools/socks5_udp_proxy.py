#!/usr/bin/env python3
"""A small SOCKS5 proxy with UDP ASSOCIATE (RFC 1928 section 7), for testing camchrome's relay.

It supports what patches 973/990 use and what a residential SOCKS5 proxy offers:

  * CONNECT (TCP), with IPv4, IPv6 and DOMAINNAME destinations
  * UDP ASSOCIATE, relaying datagrams in both directions, including DOMAINNAME destinations
    (which is how 990 sends QUIC so the proxy, not the browser, resolves the origin)
  * optional RFC 1929 username/password authentication
  * --refuse-udp: answer UDP ASSOCIATE with "command not supported", like many proxies do
  * --stats FILE: write per-association counters as JSON when each association closes, so a
    test can prove traffic went through the relay and nowhere else

It is a test tool: one process, asyncio, no access control beyond the optional password.

    tools/socks5_udp_proxy.py --port 1080
    tools/socks5_udp_proxy.py --port 1080 --auth user:pass --stats /tmp/relay.json

Then run camchrome with
    --proxy-server=socks5://127.0.0.1:1080 --socks5-udp [--socks5-credentials=user:pass]

Self-test (starts the proxy and a UDP echo server, relays a datagram by IP and by name, checks
auth and refusal):
    tools/socks5_udp_proxy.py --selftest

Run the engine-side relay checks (patch 990) against this proxy:
    tools/socks5_udp_proxy.py --run-selftest-binary build/src/out/Default/socks5_udp_relay_selftest
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import socket
import struct
import sys
from dataclasses import dataclass, field

ATYP_V4, ATYP_NAME, ATYP_V6 = 1, 3, 4
CMD_CONNECT, CMD_UDP = 1, 3
REP_OK, REP_FAIL, REP_NOT_ALLOWED, REP_CMD_UNSUPPORTED = 0, 1, 2, 7


def encode_addr(host: str, port: int) -> bytes:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raw = host.encode("idna")
        return bytes([ATYP_NAME, len(raw)]) + raw + struct.pack("!H", port)
    if ip.version == 4:
        return bytes([ATYP_V4]) + ip.packed + struct.pack("!H", port)
    return bytes([ATYP_V6]) + ip.packed + struct.pack("!H", port)


def decode_addr(buf: bytes, off: int) -> tuple[str, int, int]:
    """Returns (host, port, next offset). Raises ValueError on a malformed address."""
    atyp = buf[off]
    off += 1
    if atyp == ATYP_V4:
        host = str(ipaddress.IPv4Address(buf[off:off + 4]))
        off += 4
    elif atyp == ATYP_V6:
        host = str(ipaddress.IPv6Address(buf[off:off + 16]))
        off += 16
    elif atyp == ATYP_NAME:
        n = buf[off]
        host = buf[off + 1:off + 1 + n].decode("idna")
        if len(host) != n or n == 0:
            raise ValueError("bad name")
        off += 1 + n
    else:
        raise ValueError(f"atyp {atyp}")
    if off + 2 > len(buf):
        raise ValueError("short")
    (port,) = struct.unpack("!H", buf[off:off + 2])
    return host, port, off + 2


@dataclass
class Assoc:
    id: int
    client: tuple | None = None
    to_remote_packets: int = 0
    to_remote_bytes: int = 0
    to_client_packets: int = 0
    to_client_bytes: int = 0
    destinations: set = field(default_factory=set)
    names: set = field(default_factory=set)


class Proxy:
    def __init__(self, auth: tuple[str, str] | None, refuse_udp: bool, stats: str | None,
                 verbose: bool):
        self.auth = auth
        self.refuse_udp = refuse_udp
        self.stats_path = stats
        self.verbose = verbose
        self.closed: list[dict] = []
        self.next_id = 1
        self.connects = 0

    def log(self, *a):
        if self.verbose:
            print("[socks5]", *a, file=sys.stderr, flush=True)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            await self._handle(reader, writer)
        except (asyncio.IncompleteReadError, ConnectionError, ValueError) as e:
            self.log("client error:", e)
        finally:
            writer.close()

    async def _handle(self, reader, writer):
        ver, n = await reader.readexactly(2)
        if ver != 5:
            return
        methods = await reader.readexactly(n)
        want = 2 if self.auth else 0
        if want not in methods:
            writer.write(b"\x05\xff")
            return
        writer.write(bytes([5, want]))
        if self.auth:
            v, ulen = await reader.readexactly(2)
            user = (await reader.readexactly(ulen)).decode()
            (plen,) = await reader.readexactly(1)
            pw = (await reader.readexactly(plen)).decode()
            ok = (user, pw) == self.auth
            writer.write(bytes([1, 0 if ok else 1]))
            if not ok:
                self.log("auth failed for", user)
                return
        ver, cmd, _rsv = await reader.readexactly(3)
        head = await reader.readexactly(1)
        if head[0] == ATYP_V4:
            rest = await reader.readexactly(6)
        elif head[0] == ATYP_V6:
            rest = await reader.readexactly(18)
        elif head[0] == ATYP_NAME:
            ln = await reader.readexactly(1)
            rest = ln + await reader.readexactly(ln[0] + 2)
        else:
            return
        host, port, _ = decode_addr(head + rest, 0)
        if cmd == CMD_CONNECT:
            await self._connect(reader, writer, host, port)
        elif cmd == CMD_UDP:
            await self._associate(reader, writer, host, port)
        else:
            writer.write(bytes([5, REP_CMD_UNSUPPORTED, 0]) + encode_addr("0.0.0.0", 0))

    async def _connect(self, reader, writer, host, port):
        self.connects += 1
        try:
            r2, w2 = await asyncio.open_connection(host, port)
        except OSError:
            writer.write(bytes([5, REP_FAIL, 0]) + encode_addr("0.0.0.0", 0))
            return
        bound = w2.get_extra_info("sockname")
        writer.write(bytes([5, REP_OK, 0]) + encode_addr(bound[0], bound[1]))

        async def pipe(a, b):
            try:
                while data := await a.read(65536):
                    b.write(data)
                    await b.drain()
            except ConnectionError:
                pass
            finally:
                b.close()

        await asyncio.gather(pipe(reader, w2), pipe(r2, writer))

    async def _associate(self, reader, writer, host, port):
        if self.refuse_udp:
            writer.write(bytes([5, REP_CMD_UNSUPPORTED, 0]) + encode_addr("0.0.0.0", 0))
            return
        loop = asyncio.get_running_loop()
        local_ip = writer.get_extra_info("sockname")[0]
        a = Assoc(self.next_id)
        self.next_id += 1
        relay = self

        class RelayProto(asyncio.DatagramProtocol):
            def connection_made(self, transport):
                self.t = transport

            def datagram_received(self, data, addr):
                if a.client is None or addr[:2] == a.client[:2] and addr[1] == a.client[1]:
                    # From the client: strip the header and forward.
                    if a.client is None:
                        a.client = addr
                    try:
                        if data[0:3] != b"\x00\x00\x00":
                            return
                        dhost, dport, off = decode_addr(data, 3)
                    except (ValueError, IndexError):
                        return
                    payload = data[off:]
                    if data[3] == ATYP_NAME:
                        a.names.add(dhost)
                    asyncio.ensure_future(self.forward(dhost, dport, payload))
                    return
                # From a remote: add the header and send to the client.
                hdr = b"\x00\x00\x00" + encode_addr(addr[0], addr[1])
                a.to_client_packets += 1
                a.to_client_bytes += len(data)
                self.t.sendto(hdr + data, a.client)

            async def forward(self, dhost, dport, payload):
                try:
                    infos = await loop.getaddrinfo(dhost, dport, type=socket.SOCK_DGRAM)
                except OSError:
                    return
                want = self.t.get_extra_info("socket").family
                match = [i for i in infos if i[0] == want]
                if not match:
                    return
                sa = match[0][4]
                a.to_remote_packets += 1
                a.to_remote_bytes += len(payload)
                a.destinations.add(f"{sa[0]}:{sa[1]}")
                self.t.sendto(payload, sa)

        transport, _ = await loop.create_datagram_endpoint(RelayProto, local_addr=(local_ip, 0))
        bound = transport.get_extra_info("sockname")
        relay.log(f"assoc {a.id} relay at {bound[0]}:{bound[1]}")
        writer.write(bytes([5, REP_OK, 0]) + encode_addr(bound[0], bound[1]))
        try:
            # The association lives as long as the TCP control connection.
            while await reader.read(4096):
                pass
        finally:
            transport.close()
            rec = {
                "id": a.id,
                "to_remote_packets": a.to_remote_packets,
                "to_remote_bytes": a.to_remote_bytes,
                "to_client_packets": a.to_client_packets,
                "to_client_bytes": a.to_client_bytes,
                "destinations": sorted(a.destinations),
                "names": sorted(a.names),
            }
            relay.closed.append(rec)
            relay.log("assoc closed", rec)
            if relay.stats_path:
                with open(relay.stats_path, "w") as f:
                    json.dump(relay.closed, f, indent=1)


async def serve(host, port, proxy: Proxy):
    server = await asyncio.start_server(proxy.handle, host, port)
    return server


# ---- self-test ------------------------------------------------------------------------------

class Echo(asyncio.DatagramProtocol):
    def connection_made(self, t):
        self.t = t

    def datagram_received(self, data, addr):
        self.t.sendto(b"echo:" + data, addr)


async def socks_handshake(port, auth=None, cmd=CMD_UDP):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(b"\x05\x01" + (b"\x02" if auth else b"\x00"))
    ver, m = await r.readexactly(2)
    if m == 0xFF:
        return None, None, "no method"
    if auth:
        u, p = auth
        w.write(bytes([1, len(u)]) + u.encode() + bytes([len(p)]) + p.encode())
        _, st = await r.readexactly(2)
        if st != 0:
            return None, None, "auth failed"
    w.write(bytes([5, cmd, 0]) + encode_addr("0.0.0.0", 0))
    ver, rep, _ = await r.readexactly(3)
    head = await r.readexactly(1)
    rest = await r.readexactly(6 if head[0] == ATYP_V4 else 18)
    bhost, bport, _ = decode_addr(head + rest, 0)
    if rep != 0:
        w.close()
        return None, None, f"rep {rep}"
    return w, (bhost, bport), None


async def selftest() -> int:
    loop = asyncio.get_running_loop()
    echo_t, _ = await loop.create_datagram_endpoint(Echo, local_addr=("127.0.0.1", 0))
    echo_port = echo_t.get_extra_info("sockname")[1]
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))

    for label, auth in (("noauth", None), ("auth", ("u", "p"))):
        proxy = Proxy(auth, False, None, False)
        srv = await serve("127.0.0.1", 0, proxy)
        pport = srv.sockets[0].getsockname()[1]
        w, relay, err = await socks_handshake(pport, auth)
        check(f"{label}: UDP ASSOCIATE accepted", w is not None, err or str(relay))
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        for how, host in (("by IP", "127.0.0.1"), ("by name", "localhost")):
            pkt = b"\x00\x00\x00" + encode_addr(host, echo_port) + b"hello"
            await loop.sock_sendto(sock, pkt, relay)
            try:
                data = await asyncio.wait_for(loop.sock_recv(sock, 2048), 3)
            except asyncio.TimeoutError:
                data = b""
            got = b""
            if data:
                _, _, off = decode_addr(data, 3)
                got = data[off:]
            check(f"{label}: datagram relayed {how}", got == b"echo:hello", repr(got))
        sock.close()
        w.close()
        await asyncio.sleep(0.2)
        check(f"{label}: association accounted on close",
              proxy.closed and proxy.closed[0]["to_remote_packets"] == 2,
              json.dumps(proxy.closed))
        check(f"{label}: DOMAINNAME destination seen",
              proxy.closed and "localhost" in proxy.closed[0]["names"])
        if auth:
            _, _, err = await socks_handshake(pport, ("u", "wrong"))
            check("auth: wrong password refused", err == "auth failed", err)
        srv.close()

    proxy = Proxy(None, True, None, False)
    srv = await serve("127.0.0.1", 0, proxy)
    pport = srv.sockets[0].getsockname()[1]
    _, _, err = await socks_handshake(pport)
    check("refuse-udp: ASSOCIATE answered 'command not supported'", err == "rep 7", err)
    srv.close()
    echo_t.close()
    return 0 if ok else 1


async def run_binary(binary: str) -> int:
    """Starts a relaying proxy, a refusing proxy and a UDP echo server, then runs `binary`."""
    loop = asyncio.get_running_loop()
    echo_t, _ = await loop.create_datagram_endpoint(Echo, local_addr=("127.0.0.1", 0))
    echo_port = echo_t.get_extra_info("sockname")[1]
    good = Proxy(None, False, None, False)
    good_srv = await serve("127.0.0.1", 0, good)
    bad_srv = await serve("127.0.0.1", 0, Proxy(None, True, None, False))
    args = [binary,
            f"--proxy-port={good_srv.sockets[0].getsockname()[1]}",
            f"--refusing-proxy-port={bad_srv.sockets[0].getsockname()[1]}",
            f"--echo-port={echo_port}"]
    print("running:", " ".join(args), flush=True)
    proc = await asyncio.create_subprocess_exec(*args)
    rc = await proc.wait()
    await asyncio.sleep(0.2)
    # Every association the binary opened must have carried only NAMED destinations: the client
    # never resolved the origin itself.
    leaked = [a for a in good.closed if a["to_remote_packets"] and not a["names"]]
    if leaked:
        print("FAIL: an association relayed datagrams to an IP the client resolved:", leaked)
        rc = rc or 1
    else:
        print(f"proxy saw {len(good.closed)} associations, all addressed by name")
    good_srv.close()
    bad_srv.close()
    echo_t.close()
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=1080)
    ap.add_argument("--auth", help="user:pass (RFC 1929); default: no authentication")
    ap.add_argument("--refuse-udp", action="store_true",
                    help="refuse UDP ASSOCIATE (reply 7), like a TCP-only proxy")
    ap.add_argument("--stats", help="write per-association counters (JSON) here")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--selftest", action="store_true", help="run the built-in checks and exit")
    ap.add_argument("--run-selftest-binary", metavar="PATH",
                    help="run socks5_udp_relay_selftest against a local proxy and echo server")
    args = ap.parse_args()

    if args.selftest:
        return asyncio.run(selftest())
    if args.run_selftest_binary:
        return asyncio.run(run_binary(args.run_selftest_binary))

    auth = tuple(args.auth.split(":", 1)) if args.auth else None

    async def run():
        proxy = Proxy(auth, args.refuse_udp, args.stats, args.verbose)
        srv = await serve(args.host, args.port, proxy)
        print(f"SOCKS5 (UDP {'refused' if args.refuse_udp else 'relayed'}) on "
              f"{args.host}:{srv.sockets[0].getsockname()[1]}", flush=True)
        async with srv:
            await srv.serve_forever()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
