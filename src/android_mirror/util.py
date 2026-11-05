"""Small helpers: IP parsing, subnets, random strings, bounded concurrency."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import secrets
from collections.abc import Awaitable, Iterable
from typing import Callable

_TARGET = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})$")


def is_ipv4(ip: str | None) -> bool:
    try:
        ipaddress.IPv4Address(str(ip or ""))
        return True
    except ValueError:
        return False


def parse_target(t: str | None) -> tuple[str, int] | None:
    """'ip:port' -> (ip, port) or None."""
    m = _TARGET.match(str(t or "").strip())
    if not m or not is_ipv4(m.group(1)):
        return None
    port = int(m.group(2))
    return (m.group(1), port) if 1 <= port <= 65535 else None


def subnet_hosts(iface: dict) -> list[str]:
    """Hosts to sweep on an interface, capped at the local /24 so scans stay fast."""
    prefix = max(iface["prefix"], 24)
    net = ipaddress.IPv4Network(f"{iface['address']}/{prefix}", strict=False)
    return [str(h) for h in net.hosts() if str(h) != iface["address"]]


def same_subnet24(a: str | None, b: str | None) -> bool:
    return is_ipv4(a) and is_ipv4(b) and a.rsplit(".", 1)[0] == b.rsplit(".", 1)[0]


_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"


def random_string(n: int) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


async def pool(items: Iterable, concurrency: int, fn: Callable[..., Awaitable], should_stop: Callable[[], bool] = lambda: False):
    """Run fn(item) over items with bounded concurrency; stops early when should_stop() is true."""
    items = list(items)
    it = iter(items)

    async def worker():
        while not should_stop():
            try:
                item = next(it)
            except StopIteration:
                return
            await fn(item)

    await asyncio.gather(*(worker() for _ in range(min(concurrency, len(items)) or 0)))
