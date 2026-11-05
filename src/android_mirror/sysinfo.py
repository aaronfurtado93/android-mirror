"""OS-specific facts: LAN interfaces, the ARP table and whether a desktop session is available.

Linux uses `ip -j addr` and /proc/net/arp (falling back to ifconfig / `arp -an`);
macOS uses `ifconfig` and `arp -an`. Parsers are pure functions so they are unit-tested
on every platform.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import subprocess

from .paths import IS_MAC

# Docker/VM bridges, VPN tunnels and macOS virtual links (AirDrop, Personal Hotspot, Sidecar…).
_VIRTUAL_IFACE = re.compile(
    r"^(lo|docker|br-|virbr|veth|vmnet|vboxnet|tun|tap|wg|zt|tailscale|lxc|lxd|cni|flannel|kube|podman"
    r"|utun|awdl|llw|bridge|gif|stf|anpi|ap\d|p2p|vnic|feth)",
    re.I,
)


def _run(args: list[str]) -> str:
    if not shutil.which(args[0]) and not os.path.exists(args[0]):
        return ""
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# ------------------------------------------------------------------ interfaces


def parse_ip_json(out: str) -> list[dict]:
    """`ip -j addr show` (Linux iproute2)."""
    try:
        data = json.loads(out or "[]")
    except ValueError:
        return []
    result = []
    for link in data:
        name = link.get("ifname", "")
        if _VIRTUAL_IFACE.match(name) or "UP" not in link.get("flags", []):
            continue
        for a in link.get("addr_info", []):
            if a.get("family") == "inet" and a.get("scope") == "global":
                result.append({"name": name, "address": a["local"], "prefix": int(a.get("prefixlen", 24)), "mac": link.get("address")})
    return result


def _netmask_prefix(mask: str) -> int:
    try:
        if mask.lower().startswith("0x"):
            return bin(int(mask, 16)).count("1")
        return ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
    except ValueError:
        return 24


def parse_ifconfig(out: str) -> list[dict]:
    """BSD/macOS and net-tools ifconfig output."""
    result, name, up, mac, addrs = [], None, False, None, []

    def flush():
        if name and up and not _VIRTUAL_IFACE.match(name):
            result.extend({"name": name, "address": a, "prefix": p, "mac": mac} for a, p in addrs)

    for line in out.splitlines():
        if line and not line[0].isspace():
            flush()
            name, mac, addrs = line.split()[0].rstrip(":"), None, []
            flags = re.search(r"flags=\S*?<([^>]*)>", line)
            up = bool(flags and "UP" in flags.group(1).split(","))
            m = re.search(r"HWaddr\s+([0-9a-fA-F:]{17})", line)  # old net-tools layout
            if m:
                mac = m.group(1).lower()
            continue
        s = line.strip()
        if re.match(r"^UP\b", s):  # old net-tools: flags on their own line
            up = True
        m = re.match(r"ether\s+([0-9a-fA-F:]{17})", s)
        if m:
            mac = m.group(1).lower()
        m = re.match(r"inet (?:addr:)?(\d+\.\d+\.\d+\.\d+)\s+.*?(?:netmask|Mask:)\s*(\S+)", s)
        if m and not m.group(1).startswith("127."):
            addrs.append((m.group(1), _netmask_prefix(m.group(2))))
    flush()
    return result


def lan_interfaces() -> list[dict]:
    """Physical-looking IPv4 LAN interfaces (Wi-Fi / Ethernet), skipping docker, VPN and VM links."""
    if not IS_MAC:
        out = _run(["ip", "-j", "addr", "show"])
        if out:
            return parse_ip_json(out)
    return parse_ifconfig(_run(["ifconfig"]) or _run(["/sbin/ifconfig"]))


# ------------------------------------------------------------------ ARP / neighbours


def _norm_mac(mac: str) -> str | None:
    parts = mac.lower().split(":")
    if len(parts) != 6:
        return None
    try:
        norm = ":".join(f"{int(p, 16):02x}" for p in parts)
    except ValueError:
        return None
    return None if norm in ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff") else norm


def parse_proc_arp(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines()[1:]:
        p = line.split()
        if len(p) >= 4 and p[2] != "0x0":
            mac = _norm_mac(p[3])
            if mac:
                out[mac] = p[0]
    return out


def parse_arp_an(text: str) -> dict[str, str]:
    """`arp -an`: '? (192.168.1.1) at a4:2b:b0:1:2:3 on en0 ifscope [ethernet]'."""
    out = {}
    for m in re.finditer(r"\((\d+\.\d+\.\d+\.\d+)\) at ([0-9a-fA-F:]+)", text):
        mac = _norm_mac(m.group(2))
        if mac:
            out[mac] = m.group(1)
    return out


def arp_table() -> dict[str, str]:
    """{mac: ip} for hosts this computer talked to recently."""
    if not IS_MAC:
        try:
            with open("/proc/net/arp") as f:
                return parse_proc_arp(f.read())
        except OSError:
            pass
    return parse_arp_an(_run(["arp", "-an"]) or _run(["/usr/sbin/arp", "-an"]))


# ------------------------------------------------------------------ desktop


def has_display() -> bool:
    """Can scrcpy open a window from this process?"""
    if IS_MAC:
        return True  # any process in the user's login session can open windows
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def platform_label() -> str:
    import platform

    if IS_MAC:
        return f"macOS {platform.mac_ver()[0]} ({platform.machine()})"
    try:
        with open("/etc/os-release") as f:
            info = dict(line.rstrip().split("=", 1) for line in f if "=" in line)
        name = info.get("PRETTY_NAME", "Linux").strip('"')
    except OSError:
        name = "Linux"
    return f"{name} ({platform.machine()})"
