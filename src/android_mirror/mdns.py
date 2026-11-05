"""Minimal mDNS (RFC 6762) browser for Android "Wireless debugging" services.

Android advertises:
  _adb-tls-connect._tcp  instance "adb-<serial>-<rand>"  -> current connect port
  _adb-tls-pairing._tcp  instance "adb-<serial>-<rand>" or the QR-pairing name
  _adb._tcp              legacy `adb tcpip` (some ROMs)
The serial in the instance name lets us recognise a remembered phone even after
its IP address or port changed, before connecting to it.
"""

from __future__ import annotations

import asyncio
import socket
import struct
import time
from typing import Callable

from .adb import serial_from_instance
from .sysinfo import lan_interfaces

MDNS_ADDR = "224.0.0.251"
MDNS_PORT = 5353

SERVICE_TYPES = {
    "_adb-tls-connect._tcp.local": "connect",
    "_adb-tls-pairing._tcp.local": "pairing",
    "_adb._tcp.local": "legacy",
}

T_A, T_PTR, T_TXT, T_SRV = 1, 12, 16, 33


def _encode_name(name: str) -> bytes:
    out = b""
    for part in filter(None, name.split(".")):
        b = part.encode()
        out += bytes([len(b)]) + b
    return out + b"\x00"


def build_query(names: list[str], qid: int = 0) -> bytes:
    pkt = struct.pack("!HHHHHH", qid, 0, len(names), 0, 0, 0)
    for n in names:
        pkt += _encode_name(n) + struct.pack("!HH", T_PTR, 1)
    return pkt


def _read_name(buf: bytes, off: int) -> tuple[str, int]:
    labels, jumped, nxt = [], False, off
    for _ in range(128):
        ln = buf[off]
        if ln == 0:
            if not jumped:
                nxt = off + 1
            break
        if ln & 0xC0 == 0xC0:
            ptr = ((ln & 0x3F) << 8) | buf[off + 1]
            if not jumped:
                nxt = off + 2
            jumped, off = True, ptr
            continue
        labels.append(buf[off + 1 : off + 1 + ln].decode(errors="replace"))
        off += 1 + ln
    return ".".join(labels), nxt


def parse_packet(buf: bytes) -> list[dict]:
    if len(buf) < 12:
        return []
    _, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", buf[:12])
    if not flags & 0x8000:
        return []  # a query, not a response
    off = 12
    for _ in range(qd):
        off = _read_name(buf, off)[1] + 4
    records = []
    for _ in range(an + ns + ar):
        if off >= len(buf):
            break
        name, off = _read_name(buf, off)
        rtype, _, ttl, rdlen = struct.unpack("!HHIH", buf[off : off + 10])
        rd = off + 10
        off = rd + rdlen
        rec = {"name": name, "type": rtype, "ttl": ttl}
        try:
            if rtype == T_PTR:
                rec["data"] = _read_name(buf, rd)[0]
            elif rtype == T_SRV:
                rec["data"] = {"port": struct.unpack("!H", buf[rd + 4 : rd + 6])[0], "target": _read_name(buf, rd + 6)[0]}
            elif rtype == T_A and rdlen == 4:
                rec["data"] = socket.inet_ntoa(buf[rd : rd + 4])
            else:
                continue
        except Exception:  # noqa: S112 - skip malformed records from the network
            continue
        records.append(rec)
    return records


def _type_of(name: str) -> tuple[str, str] | None:
    lower = name.lower()
    for suffix, kind in SERVICE_TYPES.items():
        if lower.endswith("." + suffix):
            return kind, suffix
    return None


class _Proto(asyncio.DatagramProtocol):
    def __init__(self, owner: MdnsBrowser):
        self.owner = owner

    def datagram_received(self, data, addr):
        self.owner._on_message(data, addr)

    def error_received(self, exc):
        self.owner.error = str(exc)


class MdnsBrowser:
    def __init__(self):
        self.services: dict[str, dict] = {}
        self.hosts: dict[str, tuple[str, float]] = {}
        self.sock: socket.socket | None = None
        self.transport = None
        self.mode = "off"
        self.error: str | None = None
        self.ifaces: list[dict] = []
        self.listeners: dict[str, list[Callable]] = {"up": [], "down": [], "seen": []}
        self._tasks: list[asyncio.Task] = []

    def on(self, event: str, fn: Callable):
        self.listeners[event].append(fn)

    def _emit(self, event: str, svc: dict):
        for fn in self.listeners[event]:
            try:
                fn(svc)
            except Exception as e:  # never let a listener break the receive loop
                self.error = f"listener error: {e}"

    def _open(self, port: int) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        s.bind(("", port))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        if port == MDNS_PORT:
            for i in self.ifaces:
                try:
                    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(MDNS_ADDR) + socket.inet_aton(i["address"]))
                except OSError:
                    pass
        s.setblocking(False)
        return s

    async def start(self):
        self.ifaces = lan_interfaces()
        try:
            self.sock = self._open(MDNS_PORT)
            self.mode = "multicast"
        except OSError:
            # 5353 taken without SO_REUSEADDR: fall back to "legacy unicast" queries from an ephemeral port.
            try:
                self.sock = self._open(0)
                self.mode = "unicast"
            except OSError as e:
                self.mode, self.error = "off", str(e)
                return
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(lambda: _Proto(self), sock=self.sock)
        self.query()
        self._tasks = [asyncio.create_task(self._every(20, self.query)), asyncio.create_task(self._every(5, self._expire))]

    @staticmethod
    async def _every(seconds: float, fn):
        while True:
            await asyncio.sleep(seconds)
            fn()

    def query(self):
        if not self.sock:
            return
        pkt = build_query(list(SERVICE_TYPES), 0x1234 if self.mode == "unicast" else 0)
        for i in self.ifaces or [{"address": None}]:
            try:
                if i["address"]:
                    self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(i["address"]))
                self.sock.sendto(pkt, (MDNS_ADDR, MDNS_PORT))
            except OSError:
                pass

    async def query_and_wait(self, seconds: float = 1.5):
        self.query()
        await asyncio.sleep(min(seconds, 0.4))
        self.query()
        await asyncio.sleep(max(0, seconds - 0.4))

    def _on_message(self, data: bytes, addr):
        try:
            records = parse_packet(data)
        except Exception:
            return
        if not records:
            return
        now = time.time()
        for r in records:
            if r["type"] == T_A:
                self.hosts[r["name"].lower()] = (r["data"], now + max(r["ttl"], 60))
        touched: dict[str, dict] = {}
        for r in records:
            if r["type"] == T_PTR and r["name"].lower() in SERVICE_TYPES:
                key = r["data"].lower()
                if r["ttl"] == 0:
                    self._remove(key)
                else:
                    touched.setdefault(key, {}).update(fullName=r["data"], ttl=r["ttl"])
        for r in records:
            if r["type"] != T_SRV or not _type_of(r["name"]):
                continue
            key = r["name"].lower()
            if r["ttl"] == 0:
                self._remove(key)
                touched.pop(key, None)
                continue
            touched.setdefault(key, {}).update(fullName=r["name"], port=r["data"]["port"], target=r["data"]["target"], ttl=r["ttl"])
        for key, info in touched.items():
            t = _type_of(info["fullName"])
            if not t:
                continue
            prev = self.services.get(key)
            port = info.get("port") or (prev and prev["port"])
            if not port:
                continue  # needs an SRV record; the next query fills it in
            target = info.get("target") or (prev and prev.get("host"))
            host = self.hosts.get((target or "").lower())
            ip = (host and host[0]) or (prev and prev["ip"]) or addr[0]
            instance = info["fullName"][: len(info["fullName"]) - len(t[1]) - 1]
            svc = {
                "key": key,
                "kind": t[0],
                "instance": instance,
                "serial": serial_from_instance(instance),
                "ip": ip,
                "port": port,
                "target": f"{ip}:{port}",
                "host": target,
                "lastSeen": int(now * 1000),
                "expires": now + max(info.get("ttl") or 120, 60),
            }
            self.services[key] = svc
            self._emit("up" if not prev or prev["ip"] != ip or prev["port"] != port else "seen", svc)

    def _remove(self, key: str):
        s = self.services.pop(key, None)
        if s:
            self._emit("down", s)

    def _expire(self):
        now = time.time()
        for k in [k for k, s in self.services.items() if s["expires"] < now]:
            self._remove(k)
        for k in [k for k, h in self.hosts.items() if h[1] < now]:
            self.hosts.pop(k, None)

    def list(self, kind: str | None = None) -> list[dict]:
        return [s for s in self.services.values() if not kind or s["kind"] == kind]

    def find_by_serial(self, serial: str, kind: str = "connect") -> list[dict]:
        return sorted((s for s in self.list(kind) if s["serial"] and s["serial"] == serial), key=lambda s: -s["lastSeen"])

    def status(self) -> dict:
        return {"mode": self.mode, "error": self.error, "interfaces": [f"{i['name']} {i['address']}/{i['prefix']}" for i in self.ifaces]}

    def stop(self):
        for t in self._tasks:
            t.cancel()
        if self.transport:
            self.transport.close()
