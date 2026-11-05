"""Async wrappers around the adb command line (always argv lists, never a shell)."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from . import deps
from .util import parse_target


@dataclass
class Result:
    ok: bool
    code: int | str
    stdout: str
    stderr: str

    @property
    def out(self) -> str:
        return (self.stdout + self.stderr).strip()


async def run(args: list[str], timeout: float = 15) -> Result:
    try:
        proc = await asyncio.create_subprocess_exec(
            deps.path_of("adb"), *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL
        )
    except OSError:
        return Result(False, "ENOENT", "", "adb not found")
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return Result(False, "TIMEOUT", "", "")
    return Result(proc.returncode == 0, proc.returncode, out.decode(errors="replace"), err.decode(errors="replace"))


async def version() -> dict | None:
    r = await run(["version"], 5)
    if not r.ok:
        return None
    v = re.search(r"Android Debug Bridge version ([\d.]+)", r.out)
    pt = re.search(r"Version ([\w.\-]+)", r.out)
    return {"version": v and v.group(1), "platformTools": pt and pt.group(1)}


def transport_kind(serial: str) -> str:
    if parse_target(serial) or "._adb-tls-connect._tcp" in serial or "._adb._tcp" in serial:
        return "wifi"
    if re.match(r"^emulator-\d+$", serial):
        return "emulator"
    return "usb"


def serial_from_instance(name: str | None) -> str | None:
    """'adb-28131FDH2001KZ-vWgJpq' -> '28131FDH2001KZ'."""
    m = re.match(r"^adb-(.+)-[A-Za-z0-9]{6}(?:\.|$)", str(name or ""))
    return m.group(1) if m else None


async def devices() -> tuple[bool, list[dict], str]:
    r = await run(["devices", "-l"], 8)
    if not r.ok:
        return False, [], r.out
    out = []
    for line in r.stdout.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        parts = line.split()
        serial, state, rest = parts[0], (parts[1] if len(parts) > 1 else "unknown"), parts[2:]
        if state == "no" and rest[:1] == ["permissions"]:
            state, rest = "no permissions", rest[1:]
        info = dict(m.groups() for m in re.finditer(r"(\w+):(\S+)", " ".join(rest)))
        out.append(
            {"serial": serial, "state": state, "kind": transport_kind(serial), "model": (info.get("model") or "").replace("_", " ") or None}
        )
    return True, out, ""


async def connect(target: str, timeout: float = 9) -> tuple[bool, str]:
    r = await run(["connect", target], timeout)
    o = r.out.lower()
    ok = r.ok and ("connected to" in o) and not re.search(r"failed|unable|cannot|refused", o)
    msg = f"Timed out connecting to {target}" if r.code == "TIMEOUT" else (r.out or "No response from adb")
    return ok, msg


async def disconnect(target: str | None = None) -> Result:
    return await run(["disconnect", target] if target else ["disconnect"], 5)


async def pair(target: str, code: str) -> tuple[bool, str]:
    r = await run(["pair", target, str(code)], 25)
    return bool(re.search(r"successfully paired", r.out, re.I)), ("Pairing timed out" if r.code == "TIMEOUT" else r.out)


async def shell(serial: str, cmd: str, timeout: float = 8) -> Result:
    return await run(["-s", serial, "shell", cmd], timeout)


_PROP_CMD = '; echo "~~"; '.join(
    [
        "getprop ro.serialno",
        "getprop ro.product.manufacturer",
        "getprop ro.product.model",
        "getprop ro.build.version.release",
        "getprop ro.build.version.sdk",
        "settings get global device_name",
    ]
)


async def props(serial: str) -> dict | None:
    r = await shell(serial, _PROP_CMD)
    if not r.ok:
        return None
    vals = [s.strip() for s in r.stdout.split("~~")] + [""] * 6
    serialno, manu, model, release, sdk, dname = vals[:6]
    return {
        "serialno": serialno if serialno and serialno != "unknown" else None,
        "manufacturer": manu or None,
        "model": model or None,
        "androidVersion": release or None,
        "sdk": int(sdk) if sdk.isdigit() else None,
        "deviceName": dname if dname and dname != "null" else None,
    }


async def wlan_info(serial: str) -> dict:
    """Phone's Wi-Fi IPv4 and MAC (best effort; MAC can be hidden on recent Android)."""
    r = await shell(serial, "ip addr show wlan0 2>/dev/null || ip addr show", 6)
    if not r.ok:
        return {}
    ip = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/\d+", r.stdout)
    mac = re.search(r"link/ether ([0-9a-f:]{17})", r.stdout, re.I)
    m = mac.group(1).lower() if mac else None
    if m in ("00:00:00:00:00:00", "02:00:00:00:00:00"):
        m = None
    return {"ip": ip.group(1) if ip else None, "mac": m}


async def tcpip(serial: str, port: int = 5555) -> Result:
    return await run(["-s", serial, "tcpip", str(port)], 10)


async def restart_server() -> Result:
    await run(["kill-server"], 8)
    return await run(["start-server"], 15)
