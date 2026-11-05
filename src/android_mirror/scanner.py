"""TCP probing, subnet sweeps, single-host port search and ARP lookups."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from typing import Callable

from .sysinfo import arp_table
from .util import pool


async def probe(ip: str, port: int, timeout: float = 0.4) -> str:
    """'open', 'closed' (RST: the host is alive) or 'timeout'."""
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
        w.close()
        return "open"
    except ConnectionRefusedError:
        return "closed"
    except (asyncio.TimeoutError, OSError):
        return "timeout"


async def sweep(
    hosts: list[str], ports: list[int], timeout: float = 0.45, concurrency: int = 192, on_progress: Callable[[int, int], None] | None = None
) -> tuple[list[tuple[str, int]], set[str]]:
    jobs = [(h, p) for h in hosts for p in ports]
    opened: list[tuple[str, int]] = []
    alive: set[str] = set()
    done = 0

    async def job(j):
        nonlocal done
        r = await probe(j[0], j[1], timeout)
        if r == "open":
            opened.append(j)
        if r != "timeout":
            alive.add(j[0])
        done += 1
        if on_progress and done % 32 == 0:
            on_progress(done, len(jobs))

    await pool(jobs, concurrency, job)
    if on_progress:
        on_progress(len(jobs), len(jobs))
    return opened, alive


# Most likely Wireless-debugging ports first.
WIRELESS_DEBUG_RANGES = [(5555, 5555), (30000, 49999), (50000, 65535), (1024, 29999)]


async def scan_host(
    ip: str,
    ranges=WIRELESS_DEBUG_RANGES,
    timeout: float = 0.35,
    concurrency: int = 256,
    on_progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    on_open: Callable[[int], Awaitable[None]] | None = None,
) -> list[int]:
    ports, seen = [], set()
    for a, b in ranges:
        for p in range(a, b + 1):
            if p not in seen:
                seen.add(p)
                ports.append(p)
    opened: list[int] = []
    done = 0
    last = 0.0

    async def job(port):
        nonlocal done, last
        r = await probe(ip, port, timeout)
        done += 1
        if r == "open":
            opened.append(port)
            if on_open:
                await on_open(port)
        now = time.monotonic()
        if on_progress and now - last > 0.25:
            last = now
            on_progress(done, len(ports))

    await pool(ports, concurrency, job, should_stop)
    if on_progress:
        on_progress(done, len(ports))
    return opened


def neighbours() -> dict[str, str]:
    """ARP table: {mac: ip}."""
    return arp_table()
