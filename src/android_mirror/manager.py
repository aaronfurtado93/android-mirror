"""Core logic: adb transports, remembered devices, auto-reconnect, troubleshooter,
network scanning, pairing (QR + code), USB→Wi-Fi and scrcpy mirroring sessions."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shlex
import time
from typing import Callable

import segno

from . import adb, deps
from .mdns import MdnsBrowser
from .scanner import neighbours, probe, scan_host, sweep
from .store import Store
from .sysinfo import lan_interfaces
from .util import parse_target, random_string, same_subnet24, subnet_hosts

log = logging.getLogger(__name__)
_LEVELS = {"error": logging.ERROR, "warn": logging.WARNING, "info": logging.INFO, "ok": logging.INFO, "debug": logging.DEBUG}

POLL_S = 2.5
WATCHDOG_S = 5
BACKOFF_MIN = 8.0
BACKOFF_MAX = 120.0


def now_ms() -> int:
    return int(time.time() * 1000)


class Session:
    def __init__(self, proc, transport: str, restarts: int):
        self.proc = proc
        self.transport = transport
        self.started_at = now_ms()
        self.stopped_by_user = False
        self.restarts = restarts
        self.tail: list[str] = []


class Manager:
    def __init__(self, store: Store, mdns: MdnsBrowser):
        self.store = store
        self.mdns = mdns
        self.transports: dict[str, dict] = {}  # adb serial -> {serial, state, kind, model}
        self.id_cache: dict[str, str] = {}  # adb serial -> device id
        self.resolving: set[str] = set()
        self.sessions: dict[str, Session] = {}
        self.locks: dict[str, asyncio.Task] = {}
        self.backoff: dict[str, dict] = {}
        self.manual_disconnect: set[str] = set()
        self.suppress_auto_mirror: set[str] = set()
        self.last_error: dict[str, str] = {}
        self.needs_pairing: set[str] = set()  # visible on the network but refuses this computer
        self.troubles: dict[str, dict] = {}
        self.scan_results: dict[str, dict] = {}
        self.scan: dict = {"running": False}
        self.host_scan: dict | None = None
        self.pairing: dict | None = None
        self.restart_timers: dict[str, asyncio.TimerHandle] = {}
        self.restart_counts: dict[str, int] = {}
        self.logs: list[dict] = []
        self.tools: dict = {}
        self.setup: dict = {"running": False, "method": None, "result": None}
        self._tools_checked = 0.0
        self.adb_healthy = True
        self._refreshing: asyncio.Task | None = None
        self._emit_handle = None
        self._tasks: list[asyncio.Task] = []
        self.state_listeners: list[Callable[[dict], None]] = []
        self.log_listeners: list[Callable[[dict], None]] = []

    # ------------------------------------------------------------ lifecycle

    async def start(self):
        await self.check_tools(announce=True)
        self.mdns.on("up", self._on_mdns_up)
        self.mdns.on("down", lambda s: self.changed())
        self.mdns.on("seen", self._on_mdns_seen)
        await self.refresh_transports()
        self._tasks = [
            asyncio.create_task(self._loop(POLL_S, self.refresh_transports)),
            asyncio.create_task(self._loop(WATCHDOG_S, self._watchdog)),
        ]
        t = self.tools
        self.log(
            "info", f"Ready. adb {t['adb']['version'] or 'missing'}, scrcpy {t['scrcpy']['version'] or 'missing'}, mDNS {self.mdns.mode}."
        )

    async def _loop(self, seconds, fn):
        while True:
            await asyncio.sleep(seconds)
            try:
                await fn()
            except Exception as e:
                self.log("error", f"Background task error: {e}")

    async def stop(self):
        for t in self._tasks:
            t.cancel()
        for s in list(self.sessions.values()):
            s.stopped_by_user = True
            try:
                s.proc.terminate()
            except ProcessLookupError:
                pass

    # ------------------------------------------------------------ dependencies

    async def check_tools(self, announce: bool = False) -> dict:
        """Re-detect adb/scrcpy (e.g. after the user installed them in a terminal)."""
        before = self.tools
        self.tools = await deps.probe()
        self._tools_checked = time.time()
        t = self.tools
        if announce or before.get("ready") != t["ready"]:
            for name in t["missing"]:
                info = t[name]
                if info.get("outdated"):
                    self.log("warn", f"scrcpy {info['version']} is too old (needs {info['minimum']}+). Open Setup to update it.")
                else:
                    self.log("error", f"{name} not found. Open Setup (top of the page) to install it.")
            if not t["display"]:
                self.log(
                    "warn",
                    "No DISPLAY/WAYLAND_DISPLAY in environment — scrcpy windows may fail to open. Start the server from your desktop session.",
                )
            if t["ready"] and not announce:
                self.log("ok", f"All set: adb {t['adb']['version']}, scrcpy {t['scrcpy']['version']}.")
        self.changed()
        return t

    def install_deps(self, method: str) -> dict:
        if self.setup["running"]:
            return {"ok": False, "message": "An installation is already running."}
        missing = list(self.tools.get("missing") or [])
        if not missing:
            return {"ok": True, "message": "Everything is already installed."}
        self.setup = {"running": True, "method": method, "result": None}
        self.changed()

        async def run():
            self.log("info", f"Installing {' and '.join(missing)} ({method})…")
            try:
                res = await deps.install(method, missing, lambda lvl, msg: self.log(lvl, msg))
            except Exception as e:
                res = {"ok": False, "message": f"{type(e).__name__}: {e}"}
            await self.check_tools()
            if res["ok"] and not self.tools["ready"]:
                res = {"ok": False, "message": res["message"] + " But something is still missing: " + ", ".join(self.tools["missing"])}
            self.log("ok" if res["ok"] else "error", res["message"])
            self.setup = {"running": False, "method": method, "result": res}
            if res["ok"]:
                await self.refresh_transports()
            self.changed()

        self._spawn(run())
        return {"ok": True, "started": True}

    def _spawn(self, coro):
        t = asyncio.create_task(coro)
        t.add_done_callback(lambda t: (not t.cancelled() and t.exception()) and self.log("error", f"{t.exception()}"))
        return t

    # ------------------------------------------------------------ logging / state

    def log(self, level: str, msg: str, device_id: str | None = None):
        entry = {"ts": now_ms(), "level": level, "msg": msg, "deviceId": device_id}
        self.logs.append(entry)
        del self.logs[:-400]
        for fn in self.log_listeners:
            fn(entry)
        log.log(_LEVELS.get(level, logging.INFO), msg)

    def changed(self):
        if self._emit_handle:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        def fire():
            self._emit_handle = None
            snap = self.snapshot()
            for fn in self.state_listeners:
                fn(snap)

        self._emit_handle = loop.call_later(0.08, fire)

    def mirror_command(self, id_: str) -> dict:
        """The scrcpy command line Start mirroring would run now (developer options)."""
        dev = self.store.get(id_)
        if not dev:
            raise KeyError(f"Unknown device {id_}")
        t = self.live_transport(id_)
        serial = t["serial"] if t else "<not connected>"
        args = self._scrcpy_args(dev["scrcpy"], serial, self.device_name(id_), t["kind"] if t else "wifi")
        return {"command": [deps.path_of("scrcpy"), *args], "connected": bool(t), "adb": deps.path_of("adb")}

    def device_name(self, id_: str) -> str:
        d = self.store.get(id_) or {}
        return d.get("nickname") or d.get("deviceName") or " ".join(filter(None, [d.get("manufacturer"), d.get("model")])) or id_

    def transports_for(self, id_: str) -> list[dict]:
        return [t for t in self.transports.values() if self.id_cache.get(t["serial"]) == id_ or t["serial"] == id_]

    def live_transport(self, id_: str) -> dict | None:
        ready = [t for t in self.transports_for(id_) if t["state"] == "device"]
        if not ready:
            return None
        prefer_usb = ((self.store.get(id_) or {}).get("scrcpy") or {}).get("preferUsb", True)
        ready.sort(key=lambda t: 0 if (t["kind"] == "usb") == prefer_usb else 1)
        return ready[0]

    def snapshot(self) -> dict:
        now = time.time()
        devices = []
        for d in self.store.list():
            ts = self.transports_for(d["id"])
            live = self.live_transport(d["id"])
            md = self.mdns.find_by_serial(d["id"])
            pa = self.mdns.find_by_serial(d["id"], "pairing")
            sess = self.sessions.get(d["id"])
            b = self.backoff.get(d["id"])
            paused = d["id"] in self.manual_disconnect
            devices.append(
                {
                    **d,
                    "status": {
                        "connected": bool(live),
                        "via": live and live["kind"],
                        "transport": live and live["serial"],
                        "transports": [{"serial": t["serial"], "state": t["state"], "kind": t["kind"]} for t in ts],
                        "unauthorized": any(t["state"] == "unauthorized" for t in ts),
                        "onNetwork": md[0]["target"] if md else None,
                        "pairingAdvertised": pa[0]["target"] if pa else None,
                        "busy": d["id"] in self.locks,
                        "mirroring": {"since": sess.started_at, "transport": sess.transport, "restarts": sess.restarts} if sess else None,
                        "restartPending": d["id"] in self.restart_timers,
                        "lastError": self.last_error.get(d["id"]),
                        "nextRetry": int(max(0, b["next"] - now) * 1000)
                        if b and not live and d.get("autoConnect") and not paused
                        else None,
                        "paused": paused,
                        "needsPairing": d["id"] in self.needs_pairing and not live,
                    },
                    "troubleshoot": self.troubles.get(d["id"]),
                }
            )
        known = {d["id"] for d in devices}
        unknown = [
            {k: t[k] for k in ("serial", "state", "kind", "model")}
            for t in self.transports.values()
            if (self.id_cache.get(t["serial"]) or t["serial"]) not in known
        ]
        p = self.pairing
        return {
            "tools": self.tools,
            "setup": self.setup,
            "mdns": self.mdns.status(),
            "network": [{"name": i["name"], "address": i["address"], "prefix": i["prefix"]} for i in lan_interfaces()],
            "devices": devices,
            "unknownTransports": unknown,
            "discovered": self._discovered(known),
            "scan": self.scan,
            "hostScan": self.host_scan,
            "pairing": p and {k: p.get(k) for k in ("id", "status", "message", "qrSvg", "name", "expiresAt", "deviceId")},
        }

    def _discovered(self, known: set[str]) -> list[dict]:
        connected = {t["serial"] for t in self.transports.values() if t["state"] == "device"}
        items = []
        for s in self.mdns.list():
            dev_id = s["serial"] if s["serial"] in known else None
            items.append(
                {
                    "key": "mdns:" + s["key"],
                    "source": "mdns",
                    "kind": s["kind"],
                    "ip": s["ip"],
                    "port": s["port"],
                    "target": s["target"],
                    "serial": s["serial"],
                    "instance": s["instance"],
                    "deviceId": dev_id,
                    "name": self.device_name(dev_id) if dev_id else None,
                    "connected": s["target"] in connected or bool(dev_id and self.live_transport(dev_id)),
                    "lastSeen": s["lastSeen"],
                }
            )
        targets = {i["target"] for i in items}
        for r in self.scan_results.values():
            if r.get("target") and r["target"] in targets:
                continue
            c = r["target"] in connected if r.get("target") else bool(r.get("deviceId") and self.live_transport(r["deviceId"]))
            items.append({**r, "connected": c})

        def ipkey(i):
            try:
                return tuple(int(x) for x in str(i.get("ip")).split("."))
            except ValueError:
                return (999,)

        return sorted(items, key=lambda i: (0 if i.get("deviceId") else 1, ipkey(i)))

    # ------------------------------------------------------------ adb transports

    async def refresh_transports(self):
        if self._refreshing and not self._refreshing.done():
            return await self._refreshing
        self._refreshing = asyncio.create_task(self._refresh())
        return await self._refreshing

    async def _refresh(self):
        ok, lst, err = await adb.devices()
        if not ok:
            if self.adb_healthy:
                self.log("warn", f"adb not responding: {err or 'unknown error'}")
            self.adb_healthy = False
            return
        self.adb_healthy = True
        nxt = {t["serial"]: t for t in lst}
        diff = len(nxt) != len(self.transports)
        for serial, t in nxt.items():
            prev = self.transports.get(serial)
            if not prev or prev["state"] != t["state"]:
                diff = True
                if t["state"] == "device":
                    self._spawn(self._on_transport_ready(t))
                elif t["state"] == "unauthorized":
                    self.log("warn", f'{serial}: waiting for you to tap "Allow" on the phone\'s debugging prompt.')
        for serial in list(self.transports):
            if serial not in nxt:
                id_ = self.id_cache.pop(serial, None)
                if id_ and not any(t["serial"] != serial and t["serial"] in nxt for t in self.transports_for(id_)):
                    self.log("info", f"{self.device_name(id_)} disconnected.", id_)
                    self.suppress_auto_mirror.discard(id_)
        self.transports = nxt
        if diff:
            self.changed()

    async def _on_transport_ready(self, t: dict):
        if t["serial"] in self.resolving:
            return
        self.resolving.add(t["serial"])
        try:
            info = await adb.props(t["serial"])
            id_ = (info or {}).get("serialno") or adb.serial_from_instance(t["serial"]) or t["serial"]
            self.id_cache[t["serial"]] = id_
            is_new = not self.store.get(id_)
            patch = {"lastConnected": now_ms(), "lastTransport": t["kind"]}
            if info:
                patch.update({k: info[k] for k in ("manufacturer", "model", "androidVersion", "sdk", "deviceName")})
            if t["kind"] == "wifi":
                pt = parse_target(t["serial"])
                svc = None if pt else next((s for s in self.mdns.list("connect") if t["serial"].startswith(s["instance"])), None)
                ip, port = pt or (svc and (svc["ip"], svc["port"])) or (None, None)
                if ip:
                    patch.update(lastIp=ip, lastPort=port)
            wl = await adb.wlan_info(t["serial"])
            if wl.get("mac"):
                patch["mac"] = wl["mac"]
            if wl.get("ip"):
                patch["wifiIp"] = wl["ip"]
            self.store.upsert(id_, patch)
            self.backoff.pop(id_, None)
            self.last_error.pop(id_, None)
            self.needs_pairing.discard(id_)
            self.manual_disconnect.discard(id_)
            how = "USB" if t["kind"] == "usb" else t["serial"]
            self.log("ok", f"{'New device remembered' if is_new else 'Connected'}: {self.device_name(id_)} via {how}", id_)
            self.changed()
            dev = self.store.get(id_)
            if dev["autoMirror"] and id_ not in self.sessions and id_ not in self.suppress_auto_mirror:
                try:
                    await self.start_mirror(id_)
                except Exception as e:
                    self.log("error", f"Auto-mirror failed: {e}", id_)
        except Exception as e:
            self.log("error", f"Could not identify {t['serial']}: {e}")
        finally:
            self.resolving.discard(t["serial"])

    async def _wait_transport(self, serial: str, seconds: float = 5) -> str | None:
        end = time.time() + seconds
        state = None
        while time.time() < end:
            await self.refresh_transports()
            state = (self.transports.get(serial) or {}).get("state")
            if state in ("device", "unauthorized"):
                break
            await asyncio.sleep(0.4)
        for _ in range(30):  # let identification finish so callers can rely on id_cache
            if state != "device" or serial in self.id_cache:
                break
            await asyncio.sleep(0.2)
        return state

    async def _clear_stale(self, ip: str | None, keep: str | None) -> list[str]:
        removed = []
        for t in list(self.transports.values()):
            pt = parse_target(t["serial"])
            stale = t["state"] == "offline" or (pt and ip and pt[0] == ip and t["serial"] != keep)
            if stale and t["kind"] == "wifi":
                await adb.disconnect(t["serial"])
                removed.append(t["serial"])
        if removed:
            self.log("debug", f"Cleared stale connections: {', '.join(removed)}")
            await self.refresh_transports()
        return removed

    async def connect_target(self, target: str, quiet: bool = False) -> dict:
        """adb connect + wait + identify."""
        pt = parse_target(target)
        if not pt:
            return {"ok": False, "message": f'"{target}" is not a valid IP:port'}
        existing = self.transports.get(target)
        if existing and existing["state"] == "device":
            return {"ok": True, "id": self.id_cache.get(target), "state": "device"}
        await self._clear_stale(pt[0], None)
        if not quiet:
            self.log("info", f"Connecting to {target}…")
        ok, msg = await adb.connect(target)
        if not ok:
            await adb.disconnect(target)
            return {"ok": False, "message": msg}
        state = await self._wait_transport(target)
        if state == "device":
            return {"ok": True, "id": self.id_cache.get(target), "state": state}
        if state == "unauthorized":
            return {"ok": False, "state": state, "message": 'Phone has not authorised this computer yet — tap "Allow" on the phone.'}
        await adb.disconnect(target)
        return {"ok": False, "state": state, "message": f"Connected to {target} but the device stayed {state or 'unavailable'}."}

    def _with_lock(self, id_: str, fn) -> asyncio.Task:
        if id_ in self.locks:
            return self.locks[id_]

        async def wrapper():
            try:
                return await fn()
            finally:
                self.locks.pop(id_, None)
                self.changed()

        t = asyncio.create_task(wrapper())
        self.locks[id_] = t
        self.changed()
        return t

    def _candidates(self, dev: dict, extra_ips: list[str] = ()) -> list[str]:
        c = [s["target"] for s in self.mdns.find_by_serial(dev["id"])]
        if dev.get("lastIp") and dev.get("lastPort"):
            c.append(f"{dev['lastIp']}:{dev['lastPort']}")
        for ip in extra_ips:
            if dev.get("lastPort"):
                c.append(f"{ip}:{dev['lastPort']}")
        if dev.get("lastIp") and dev.get("lastPort") != 5555 and dev.get("legacyTcpip"):
            c.append(f"{dev['lastIp']}:5555")
        return list(dict.fromkeys(c))

    async def ensure_connected(self, id_: str, quiet: bool = False) -> dict:
        """Make sure a remembered device is connected, trying the quick options."""

        async def run():
            dev = self.store.get(id_)
            if not dev:
                return {"ok": False, "message": "Unknown device"}
            if self.live_transport(id_):
                return {"ok": True, "id": id_}
            self.manual_disconnect.discard(id_)
            tried: list[str] = []

            async def attempt():
                for t in self._candidates(dev):
                    if t in tried:
                        continue
                    tried.append(t)
                    r = await self.connect_target(t, quiet)
                    if r["ok"] and r.get("id") in (id_, None):
                        return r
                    if r["ok"]:
                        self.log("warn", f"{t} is a different phone ({self.device_name(r['id'])}).")
                    if r.get("state") == "unauthorized":
                        return r
                return None

            r = await attempt()
            if not r and self.mdns.mode != "off":
                await self.mdns.query_and_wait(1.8)
                r = await attempt()
            if r and r["ok"]:
                return r
            name = self.device_name(id_)
            advertised = [s["target"] for s in self.mdns.find_by_serial(id_)]
            if not r and advertised and any(t in tried for t in advertised):
                # Wireless debugging is on and announcing itself, yet adb is refused: the pairing is gone.
                self.needs_pairing.add(id_)
                message = f"{name} is on the network but no longer trusts this computer — pair it again (QR code)."
                self.last_error[id_] = message
                if not quiet:
                    self.log("warn", message, id_)
                return {"ok": False, "message": message, "needsPairing": True}
            message = (r or {}).get("message") or (
                f'Couldn\'t reach {name} (tried {", ".join(tried)}). Is Wireless debugging on? Try "Troubleshoot".'
                if tried
                else f"No known address for {name} yet. Turn on Wireless debugging or connect it by USB."
            )
            self.last_error[id_] = message
            if not quiet:
                self.log("warn", message, id_)
            return {"ok": False, "message": message}

        return await self._with_lock(id_, run)

    async def disconnect_device(self, id_: str):
        self.manual_disconnect.add(id_)
        await self.stop_mirror(id_)
        for t in self.transports_for(id_):
            if t["kind"] == "wifi":
                await adb.disconnect(t["serial"])
        await self.refresh_transports()
        self.log("info", f"Disconnected {self.device_name(id_)}. Auto-reconnect paused until you connect again.", id_)
        self.changed()

    async def forget(self, id_: str):
        await self.disconnect_device(id_)
        self.manual_disconnect.discard(id_)
        self.troubles.pop(id_, None)
        self.store.remove(id_)
        self.log("info", f"Forgot device {id_}.")
        self.changed()

    def update_device(self, id_: str, patch: dict) -> dict:
        if not self.store.get(id_):
            raise ValueError("Unknown device")
        d = self.store.upsert(id_, patch)
        if patch.get("autoConnect"):
            self.backoff.pop(id_, None)
        self.changed()
        return d

    # ------------------------------------------------------------ auto-reconnect

    def _on_mdns_up(self, s: dict):
        self.changed()
        if self.pairing and s["kind"] == "pairing" and s["instance"] == self.pairing["name"]:
            self._spawn(self._qr_pair_found(s))
        if s["kind"] == "connect" and s["serial"]:
            dev = self.store.get(s["serial"])
            if dev:
                id_ = s["serial"]
                self.log("info", f"Spotted {self.device_name(id_)} on the network at {s['target']}.", id_)
                if dev["autoConnect"] and not self.live_transport(id_) and id_ not in self.manual_disconnect:
                    self.backoff.pop(id_, None)
                    self._spawn(self._reconnect_quiet(id_))

    def _on_mdns_seen(self, s: dict):
        if self.pairing and s["kind"] == "pairing" and s["instance"] == self.pairing["name"] and self.pairing["status"] == "waiting":
            self._spawn(self._qr_pair_found(s))

    async def _reconnect_quiet(self, id_: str):
        r = await self.ensure_connected(id_, quiet=True)
        if not r["ok"]:
            self._bump_backoff(id_)

    def _bump_backoff(self, id_: str):
        b = self.backoff.get(id_) or {"delay": BACKOFF_MIN / 2, "failures": 0}
        b["delay"] = BACKOFF_MAX if id_ in self.needs_pairing else min(b["delay"] * 2, BACKOFF_MAX)
        b["failures"] += 1
        b["next"] = time.time() + b["delay"]
        self.backoff[id_] = b
        if b["failures"] == 1:
            self.log("info", f"{self.device_name(id_)} unreachable; will keep retrying in the background.", id_)
        self.changed()

    async def _watchdog(self):
        now = time.time()
        if not self.tools.get("ready") and not self.setup["running"] and now - self._tools_checked > 15:
            await self.check_tools()
        arp = None
        for dev in self.store.list():
            id_ = dev["id"]
            if not dev["autoConnect"] or id_ in self.manual_disconnect or id_ in self.locks or self.live_transport(id_):
                continue
            b = self.backoff.get(id_)
            if b and now < b["next"]:
                continue
            extra = []
            if dev.get("mac"):
                # New DHCP lease? Look the phone's MAC up in the ARP table (no network traffic).
                arp = arp if arp is not None else neighbours()
                ip = arp.get(dev["mac"])
                if ip and ip != dev.get("lastIp"):
                    self.log("info", f"{self.device_name(id_)} found at new address {ip} (matched by hardware address).", id_)
                    dev = self.store.upsert(id_, {"lastIp": ip})
                    extra.append(ip)
            if not self._candidates(dev, extra):
                continue
            await self._reconnect_quiet(id_)

    # ------------------------------------------------------------ troubleshooter

    def troubleshoot(self, id_: str):
        """Step-by-step diagnosis & repair; progress is published live in device.troubleshoot."""
        if not self.store.get(id_):
            raise ValueError("Unknown device")
        if (self.troubles.get(id_) or {}).get("running"):
            return
        report = {"running": True, "startedAt": now_ms(), "steps": [], "verdict": None, "advice": [], "action": None}
        self.troubles[id_] = report
        self.changed()

        def step(title):
            s = {"title": title, "status": "running", "detail": ""}
            report["steps"].append(s)
            self.changed()

            def done(status, detail=""):
                s.update(status=status, detail=detail)
                self.changed()

            return done

        async def run():
            dev = self.store.get(id_)
            name = self.device_name(id_)
            self.log("info", f"Troubleshooting {name}…", id_)
            self.manual_disconnect.discard(id_)

            # 1. adb server
            done = step("Check adb server")
            ok, _, _ = await adb.devices()
            if not ok:
                await adb.restart_server()
                ok, _, _ = await adb.devices()
                if not ok:
                    done("fail", "adb is not responding even after a restart.")
                    return {
                        "ok": False,
                        "verdict": "adb is broken on this computer.",
                        "advice": ["Reinstall adb from Setup (menu › Setup & dependencies) or with your package manager."],
                    }
                done("ok", "adb was stuck — restarted it.")
            else:
                done("ok", "adb server is running.")
            await self.refresh_transports()

            # 2. Existing connection actually responds?
            done = step("Check existing connection")
            live = self.live_transport(id_)
            if live:
                echo = await adb.shell(live["serial"], "echo ok", 5)
                if echo.ok and echo.stdout.strip() == "ok":
                    done("ok", f"Connected and responding via {'USB' if live['kind'] == 'usb' else live['serial']}.")
                    return {"ok": True, "verdict": f"{name} is connected and working."}
                await adb.disconnect(live["serial"])
                done("warn", f"Connection {live['serial']} was frozen — dropped it.")
            else:
                done("skip", "Not connected.")

            # 3. Stale entries
            done = step("Clear stale connections")
            removed = await self._clear_stale(dev.get("lastIp"), None)
            for t in self.transports_for(id_):
                if t["state"] != "device" and t["kind"] == "wifi":
                    await adb.disconnect(t["serial"])
                    removed.append(t["serial"])
            done("ok" if removed else "skip", f"Removed {', '.join(removed)}" if removed else "Nothing stale.")

            # 4. This computer's network
            done = step("Check this computer's network")
            ifaces = lan_interfaces()
            addrs = ", ".join(i["address"] for i in ifaces)
            if not ifaces:
                done("fail", "No network connection found on this computer.")
                return {
                    "ok": False,
                    "verdict": "This computer is offline.",
                    "advice": ["Connect this computer to the same Wi-Fi network as the phone."],
                }
            same = not dev.get("lastIp") or any(same_subnet24(i["address"], dev["lastIp"]) for i in ifaces)
            done(
                "ok" if same else "warn",
                f"On {addrs}"
                if same
                else f"This computer ({addrs}) is on a different network than the phone was last time ({dev['lastIp']}).",
            )

            # 5. mDNS
            done = step("Look for the phone by name (mDNS)")
            found, pairing_ad = [], None
            if self.mdns.mode == "off":
                done("skip", "mDNS unavailable on this computer.")
            else:
                await self.mdns.query_and_wait(3.5)
                found = self.mdns.find_by_serial(id_)
                pa = self.mdns.find_by_serial(id_, "pairing")
                pairing_ad = pa[0] if pa else None
                if found:
                    done("ok", f"Phone is advertising Wireless debugging at {found[0]['target']}.")
                else:
                    done("warn", "Phone is not advertising Wireless debugging (it may be off, the screen locked, or multicast blocked).")

            # 6. Last known address
            ip_cands = list(dict.fromkeys(f["ip"] for f in found))
            done = step("Check last known address")
            last_alive = False
            if dev.get("lastIp"):
                p = await probe(dev["lastIp"], dev.get("lastPort") or 5555, 1.2)
                last_alive = p != "timeout"
                if p == "open":
                    done("ok", f"{dev['lastIp']}:{dev['lastPort']} is open.")
                elif p == "closed":
                    done("warn", f"{dev['lastIp']} is reachable but port {dev['lastPort']} is closed — the port probably changed.")
                else:
                    done("warn", f"{dev['lastIp']} did not answer (phone asleep, off Wi-Fi, or IP changed).")
                if last_alive and dev["lastIp"] not in ip_cands:
                    ip_cands.append(dev["lastIp"])
            else:
                done("skip", "No previous Wi-Fi address on record.")

            # 7. Hardware address
            done = step("Search the network for the phone's hardware address")
            moved_ip = None
            if not dev.get("mac"):
                done("skip", "Hardware address unknown (recorded after the first successful Wi-Fi connection).")
            else:
                hosts = [h for i in ifaces for h in subnet_hosts(i)]
                await sweep(hosts, [dev.get("lastPort") or 5555], timeout=0.3)  # fills the ARP table
                moved_ip = neighbours().get(dev["mac"])
                if moved_ip:
                    if moved_ip not in ip_cands:
                        ip_cands.append(moved_ip)
                    if moved_ip != dev.get("lastIp"):
                        self.store.upsert(id_, {"lastIp": moved_ip})
                    done(
                        "ok",
                        f"Phone is at {moved_ip}."
                        if moved_ip == dev.get("lastIp")
                        else f"Phone moved to {moved_ip} (was {dev.get('lastIp')}).",
                    )
                else:
                    done("warn", f"No device with hardware address {dev['mac']} answered.")

            # 8. Quick connects
            done = step("Try known addresses")
            quick = list(dict.fromkeys([f["target"] for f in found] + [f"{ip}:{dev.get('lastPort') or 5555}" for ip in ip_cands]))
            last_msg, unauthorized = "", False
            for t in quick:
                r = await self.connect_target(t, quiet=True)
                if r["ok"] and r.get("id") == id_:
                    done("ok", f"Connected via {t}.")
                    return {"ok": True, "verdict": f"Reconnected {name}."}
                if r.get("state") == "unauthorized":
                    unauthorized = True
                last_msg = r.get("message") or last_msg
            done("warn" if quick else "skip", f"No luck with {', '.join(quick)}. {last_msg}" if quick else "No addresses to try.")
            if unauthorized:
                return {
                    "ok": False,
                    "verdict": "The phone is asking for permission.",
                    "advice": ['Unlock the phone and tap "Allow" on the debugging prompt (tick "Always allow from this computer").'],
                }

            # 9. Deep port search
            scan_ip = moved_ip or (found[0]["ip"] if found else None) or (dev.get("lastIp") if last_alive else None)
            done = step("Search for the Wireless debugging port")
            open_ports: list[int] = []
            hit = {"port": None, "stop": False}
            if not scan_ip:
                done("skip", "Phone not found on the network, so there is nothing to scan.")
            else:
                pairing_ports = {p["port"] for p in self.mdns.list("pairing") if p["ip"] == scan_ip}

                async def on_open(port):
                    nonlocal unauthorized
                    if hit["stop"] or port in pairing_ports:
                        return
                    r = await self.connect_target(f"{scan_ip}:{port}", quiet=True)
                    if r["ok"] and r.get("id") == id_:
                        hit.update(port=port, stop=True)
                    elif r.get("state") == "unauthorized":
                        unauthorized = True
                        hit["stop"] = True

                open_ports = await scan_host(
                    scan_ip,
                    should_stop=lambda: hit["stop"],
                    on_open=on_open,
                    on_progress=lambda d, t: done("running", f"Scanning {scan_ip}… {round(d / t * 100)}%"),
                )
                if hit["port"]:
                    done("ok", f"Found it on port {hit['port']} and connected.")
                    return {"ok": True, "verdict": f"Reconnected {name} on its new port."}
                done(
                    "warn",
                    f"Open ports on {scan_ip}: {', '.join(map(str, open_ports[:8]))}, but none accepted this computer."
                    if open_ports
                    else f"No open ports on {scan_ip}.",
                )

            if unauthorized:
                return {
                    "ok": False,
                    "verdict": "The phone is asking for permission.",
                    "advice": ['Unlock the phone and tap "Allow" on the debugging prompt.'],
                }
            if (scan_ip and open_ports) or found:
                return {
                    "ok": False,
                    "verdict": "The phone is on the network with Wireless debugging on, but it no longer trusts this computer.",
                    "advice": [
                        'Pairing was probably reset (e.g. "Revoke authorisations", a phone update, or toggling debugging). Pair again with the QR code — it takes 10 seconds.'
                    ],
                    "action": "pair",
                }
            if scan_ip or pairing_ad:
                return {
                    "ok": False,
                    "verdict": "The phone is on the network but Wireless debugging is off.",
                    "advice": [
                        "On the phone: Settings › Developer options › turn on Wireless debugging (or tap the Wireless debugging quick tile).",
                        "It reconnects automatically as soon as it is on.",
                    ],
                }
            return {
                "ok": False,
                "verdict": f"{name} could not be found on this network.",
                "advice": [
                    f"Make sure the phone is on the same Wi-Fi as this computer ({addrs}).",
                    "Unlock the phone and turn on Wireless debugging (Settings › Developer options).",
                    'Guest / "client isolation" Wi-Fi networks block phone-to-computer connections.',
                    'Or plug it in by USB and use "Switch to Wi-Fi".',
                ],
            }

        async def wrapped():
            try:
                result = await run()
            except Exception as e:
                result = {"ok": False, "verdict": f"Troubleshooter error: {e}"}
            for st in report["steps"]:
                if st["status"] == "running":
                    st["status"] = "skip"
            report.update(
                running=False,
                finishedAt=now_ms(),
                ok=result["ok"],
                verdict=result["verdict"],
                advice=result.get("advice") or [],
                action=result.get("action"),
            )
            if result["ok"]:
                self.backoff.pop(id_, None)
                self.last_error.pop(id_, None)
                self.needs_pairing.discard(id_)
            else:
                self.last_error[id_] = result["verdict"]
                if result.get("action") == "pair":
                    self.needs_pairing.add(id_)
            self.log("ok" if result["ok"] else "warn", f"Troubleshoot {self.device_name(id_)}: {result['verdict']}", id_)
            self.changed()
            return result

        async def start():
            if id_ in self.locks:  # let a running connect attempt finish first
                with contextlib.suppress(Exception):  # the running task reports its own errors
                    await self.locks[id_]
            await self._with_lock(id_, wrapped)

        self._spawn(start())

    # ------------------------------------------------------------ network scan

    async def scan_network(self):
        if self.scan.get("running"):
            return
        ifaces = lan_interfaces()
        self.scan = {"running": True, "phase": "Listening for phones (mDNS)…", "progress": 0, "startedAt": now_ms()}
        self.changed()
        nets = ", ".join(f"{i['address']}/{max(i['prefix'], 24)}" for i in ifaces) or "no interfaces"
        self.log("info", f"Scanning {nets}…")
        try:
            for k in [k for k in self.scan_results if k.startswith(("tcp:", "mac:"))]:
                del self.scan_results[k]
            await self.mdns.query_and_wait(2.5)

            self.scan["phase"] = "Probing the local network…"
            self.changed()
            hosts = [h for i in ifaces for h in subnet_hosts(i)]

            def prog(d, t):
                self.scan["progress"] = d / t if t else 1
                self.changed()

            opened, _ = await sweep(hosts, [5555], on_progress=prog)
            for ip, port in opened:
                target = f"{ip}:{port}"
                dev_id = (
                    self.id_cache.get(target)
                    if target in self.transports
                    else next((d["id"] for d in self.store.list() if d.get("lastIp") == ip), None)
                )
                self.scan_results["tcp:" + target] = {
                    "key": "tcp:" + target,
                    "source": "scan",
                    "kind": "tcpip",
                    "ip": ip,
                    "port": port,
                    "target": target,
                    "deviceId": dev_id,
                    "name": self.device_name(dev_id) if dev_id else None,
                    "lastSeen": now_ms(),
                }

            self.scan["phase"] = "Identifying remembered devices…"
            self.changed()
            arp = neighbours()
            for dev in self.store.list():
                if not dev.get("mac") or dev["mac"] not in arp:
                    continue
                ip = arp[dev["mac"]]
                if ip != dev.get("lastIp"):
                    self.store.upsert(dev["id"], {"lastIp": ip})
                self.scan_results["mac:" + dev["id"]] = {
                    "key": "mac:" + dev["id"],
                    "source": "arp",
                    "kind": "known-host",
                    "ip": ip,
                    "port": None,
                    "target": None,
                    "deviceId": dev["id"],
                    "name": self.device_name(dev["id"]),
                    "lastSeen": now_ms(),
                }
            found = self._discovered({d["id"] for d in self.store.list()})
            self.log("ok", f"Scan finished: {len(found)} item(s) found.")
            for dev in self.store.list():
                if (
                    dev["autoConnect"]
                    and not self.live_transport(dev["id"])
                    and dev["id"] not in self.manual_disconnect
                    and any(f.get("deviceId") == dev["id"] for f in found)
                ):
                    self.backoff.pop(dev["id"], None)
                    self._spawn(self._reconnect_quiet(dev["id"]))
        finally:
            self.scan = {"running": False, "finishedAt": now_ms()}
            self.changed()

    async def find_port_on_host(self, ip: str) -> dict:
        """Full port scan of one IP to find a connectable adb port."""
        if self.host_scan and self.host_scan.get("running"):
            raise ValueError("A port search is already running")
        self.host_scan = {"running": True, "ip": ip, "progress": 0, "found": None}
        self.changed()
        result = {}
        stop = {"v": False}

        async def on_open(port):
            if stop["v"]:
                return
            r = await self.connect_target(f"{ip}:{port}", quiet=True)
            if r["ok"] or r.get("state") == "unauthorized":
                stop["v"] = True
                result.update(r, target=f"{ip}:{port}")

        def prog(d, t):
            self.host_scan["progress"] = d / t if t else 1
            self.changed()

        try:
            await scan_host(ip, should_stop=lambda: stop["v"], on_open=on_open, on_progress=prog)
        finally:
            self.host_scan = {"running": False, "ip": ip, "found": result.get("target")}
            self.changed()
        if result.get("ok"):
            self.log("ok", f"Found adb on {result['target']}.")
            return result
        return result or {
            "ok": False,
            "message": f"No connectable adb port found on {ip}. Make sure Wireless debugging is on and this computer is paired.",
        }

    # ------------------------------------------------------------ pairing

    async def pair_with_code(self, target: str, code: str) -> dict:
        if not parse_target(target):
            raise ValueError('Enter the pairing address as IP:port (shown under "Pair device with pairing code").')
        code = str(code or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            raise ValueError("The pairing code is the 6-digit number shown on the phone.")
        self.log("info", f"Pairing with {target}…")
        ok, msg = await adb.pair(target, code)
        if not ok:
            self.log("error", f"Pairing failed: {msg}")
            return {"ok": False, "message": msg or "Pairing failed"}
        self.log("ok", f"Paired with {target}.")
        c = await self._connect_after_pair(parse_target(target)[0])
        return {"ok": True, "paired": True, **c}

    async def _connect_after_pair(self, ip: str) -> dict:
        for _ in range(8):
            await self.mdns.query_and_wait(1.2)
            svc = next((s for s in self.mdns.list("connect") if s["ip"] == ip), None)
            if svc:
                r = await self.connect_target(svc["target"])
                if r["ok"]:
                    return {"connected": True, "deviceId": r.get("id"), "target": svc["target"]}
                return {"connected": False, "message": r.get("message")}
        return {
            "connected": False,
            "needsPort": True,
            "message": 'Paired! Now enter the IP address & port from the main Wireless debugging screen (or use "Find port").',
        }

    async def start_qr_pairing(self) -> dict:
        self.cancel_pairing()
        name = "adbweb-" + random_string(6)
        password = random_string(10)
        qr = segno.make_qr(f"WIFI:T:ADB;S:{name};P:{password};;", error="m")
        svg = qr.svg_inline(scale=4, border=1, dark="#000", light="#fff", omitsize=True)
        pid = random_string(8)
        self.pairing = {
            "id": pid,
            "name": name,
            "password": password,
            "qrSvg": svg,
            "status": "waiting",
            "message": "Scan this code with your phone.",
            "expiresAt": now_ms() + 180_000,
        }

        async def watch():
            while self.pairing and self.pairing["id"] == pid:
                p = self.pairing
                if p["status"] == "waiting":
                    if now_ms() > p["expiresAt"]:
                        p.update(status="expired", message="QR code expired. Generate a new one.")
                        self.changed()
                        return
                    self.mdns.query()
                    s = next((x for x in self.mdns.list("pairing") if x["instance"] == name), None)
                    if s:
                        await self._qr_pair_found(s)
                elif p["status"] in ("done", "paired", "error", "expired"):
                    return
                await asyncio.sleep(1.5)

        self.pairing["task"] = self._spawn(watch())
        self.log("info", "QR pairing started — scan the code on your phone.")
        self.changed()
        return {"id": pid, "name": name}

    async def _qr_pair_found(self, s: dict):
        p = self.pairing
        if not p or p["status"] != "waiting":
            return
        p.update(status="pairing", message=f"Phone found at {s['ip']} — pairing…")
        self.changed()
        ok, msg = await adb.pair(s["target"], p["password"])
        if self.pairing is not p:
            return
        if not ok:
            p.update(status="error", message=f"Pairing failed: {msg}")
            self.log("error", p["message"])
            self.changed()
            return
        p.update(status="connecting", message="Paired! Connecting…")
        self.changed()
        self.log("ok", f"Paired with phone at {s['ip']}.")
        c = await self._connect_after_pair(s["ip"])
        if self.pairing is not p:
            return
        if c["connected"]:
            p.update(status="done", deviceId=c["deviceId"], message=f"Connected to {self.device_name(c['deviceId'])}.")
        else:
            p.update(status="paired", message=c["message"])
        self.changed()

    def cancel_pairing(self):
        if self.pairing and self.pairing.get("task"):
            self.pairing["task"].cancel()
        self.pairing = None
        self.changed()

    # ------------------------------------------------------------ USB

    async def usb_to_wifi(self, serial: str) -> dict:
        t = self.transports.get(serial)
        if not t or t["state"] != "device":
            raise ValueError("Device is not connected and authorised over USB.")
        wl = await adb.wlan_info(serial)
        if not wl.get("ip"):
            raise ValueError("The phone is not connected to Wi-Fi.")
        self.log("info", f"Enabling adb over Wi-Fi on {serial} ({wl['ip']}:5555)…")
        r = await adb.tcpip(serial, 5555)
        if not r.ok:
            raise ValueError(r.out or "adb tcpip failed")
        await asyncio.sleep(2)
        c = {}
        for _ in range(3):
            c = await self.connect_target(f"{wl['ip']}:5555")
            if c["ok"]:
                break
            await asyncio.sleep(1.5)
        if c.get("ok") and c.get("id"):
            self.store.upsert(c["id"], {"legacyTcpip": True, "lastIp": wl["ip"], "lastPort": 5555})
        return c

    # ------------------------------------------------------------ mirroring

    @staticmethod
    def _scrcpy_args(o: dict, serial: str, title: str, kind: str) -> list[str]:
        a = ["-s", serial, f"--window-title={title}"]
        if o.get("maxSize", 0) > 0:
            a.append(f"--max-size={int(o['maxSize'])}")
        if o.get("videoBitRate", 0) > 0:
            a.append(f"--video-bit-rate={o['videoBitRate']:g}M")
        if o.get("maxFps", 0) > 0:
            a.append(f"--max-fps={int(o['maxFps'])}")
        if o.get("videoCodec") in ("h264", "h265", "av1"):
            a.append(f"--video-codec={o['videoCodec']}")
        if not o.get("audio"):
            a.append("--no-audio")
        if o.get("noControl"):
            a.append("--no-control")
        else:
            if o.get("stayAwake"):
                a.append("--stay-awake")
            if o.get("turnScreenOff"):
                a.append("--turn-screen-off")
            if o.get("showTouches"):
                a.append("--show-touches")
            if o.get("keyboard") == "uhid" or (o.get("keyboard") == "aoa" and kind == "usb"):
                a.append(f"--keyboard={o['keyboard']}")
            if o.get("powerOffOnClose"):
                a.append("--power-off-on-close")
        if o.get("alwaysOnTop"):
            a.append("--always-on-top")
        if o.get("borderless"):
            a.append("--window-borderless")
        if o.get("fullscreen"):
            a.append("--fullscreen")
        if o.get("extraArgs"):
            try:
                a += shlex.split(o["extraArgs"])
            except ValueError:
                a += o["extraArgs"].split()
        return a

    async def start_mirror(self, id_: str) -> dict:
        if id_ in self.sessions:
            return {"ok": True, "already": True}
        if not (self.tools.get("scrcpy") or {}).get("ok"):
            raise ValueError("scrcpy is missing or too old — open Setup to install it.")
        h = self.restart_timers.pop(id_, None)
        if h:
            h.cancel()
        c = await self.ensure_connected(id_)
        if not c["ok"]:
            raise ValueError(c["message"])
        if id_ in self.sessions:
            return {"ok": True, "already": True}
        dev = self.store.get(id_)
        t = self.live_transport(id_)
        if not t:
            raise ValueError("Device disconnected")
        args = self._scrcpy_args(dev["scrcpy"], t["serial"], self.device_name(id_), t["kind"])
        self.log("info", f"Mirroring {self.device_name(id_)}: scrcpy {' '.join(args)}", id_)
        try:
            proc = await asyncio.create_subprocess_exec(
                deps.path_of("scrcpy"),
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=deps.child_env(),
            )
        except OSError as e:
            raise ValueError(f"Could not start scrcpy: {e}") from e
        session = Session(proc, t["serial"], self.restart_counts.get(id_, 0))
        self.sessions[id_] = session
        self.suppress_auto_mirror.discard(id_)

        async def pump(stream):
            async for raw in stream:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                session.tail = (session.tail + [line])[-30:]
                lvl = "error" if line.startswith("ERROR") else "warn" if line.startswith("WARN") else "debug"
                self.log(lvl, f"scrcpy: {line}", id_)

        async def waiter():
            await asyncio.gather(pump(proc.stdout), pump(proc.stderr))
            code = await proc.wait()
            self._on_mirror_exit(id_, session, code)

        self._spawn(waiter())
        self.changed()
        return {"ok": True}

    def _on_mirror_exit(self, id_: str, session: Session, code: int):
        if self.sessions.get(id_) is not session:
            return
        del self.sessions[id_]
        ran_for = now_ms() - session.started_at
        dev = self.store.get(id_)
        if session.stopped_by_user or code == 0:  # 0 = the user closed the scrcpy window
            self.restart_counts.pop(id_, None)
            self.suppress_auto_mirror.add(id_)
            self.log("info", f"Mirroring of {self.device_name(id_)} ended.", id_)
        else:
            reason = "connection lost" if code == 2 else f"scrcpy exited with code {code}"
            last_err = next((ln for ln in reversed(session.tail) if ln.startswith("ERROR")), "")
            self.log("warn", f"Mirroring of {self.device_name(id_)} stopped: {reason}. {last_err}", id_)
            if dev and dev["keepAlive"]:
                self._schedule_resume(id_, 0 if ran_for > 60_000 else self.restart_counts.get(id_, 0) + 1)
        self.changed()

    def _schedule_resume(self, id_: str, n: int):
        self.restart_counts[id_] = n
        if n > 6:
            self.restart_counts.pop(id_, None)
            self.log("error", f'Gave up resuming mirroring for {self.device_name(id_)} after several attempts. Try "Troubleshoot".', id_)
            return
        delay = min(2 * 2**n, 60)
        self.log("info", f"Will resume mirroring in {delay}s…", id_)

        async def resume():
            self.restart_timers.pop(id_, None)
            if id_ in self.sessions or not (self.store.get(id_) or {}).get("keepAlive"):
                return
            try:
                await self.start_mirror(id_)
            except Exception as e:
                self.log("warn", f"Resume failed: {e}", id_)
                self._schedule_resume(id_, n + 1)
            self.changed()

        self.restart_timers[id_] = asyncio.get_running_loop().call_later(delay, lambda: self._spawn(resume()))

    async def stop_mirror(self, id_: str):
        h = self.restart_timers.pop(id_, None)
        if h:
            h.cancel()
        s = self.sessions.get(id_)
        if not s:
            self.changed()
            return
        s.stopped_by_user = True
        try:
            s.proc.terminate()
        except ProcessLookupError:
            return

        async def ensure_dead():
            await asyncio.sleep(3)
            if s.proc.returncode is None:
                s.proc.kill()

        self._spawn(ensure_dead())

    async def restart_adb(self):
        self.log("info", "Restarting adb server…")
        for id_ in list(self.sessions):
            await self.stop_mirror(id_)
        await adb.restart_server()
        self.id_cache.clear()
        self.transports.clear()
        self.backoff.clear()
        await self.refresh_transports()
        self.log("ok", "adb server restarted. Remembered devices will reconnect automatically.")
        self.changed()

    async def import_legacy(self, target: str):
        """One-time import of the address saved by the old scrcpy-connect.sh."""
        if not parse_target(target) or self.store.settings.get("legacyImported"):
            return
        self.store.set_setting("legacyImported", True)
        self.log("info", f"Importing previous connection {target} from scrcpy-connect.sh…")
        svc = None
        r = await self.connect_target(target, quiet=True)
        if not r["ok"]:
            # The port usually changed since; look for the same phone (same IP) via mDNS.
            ip = parse_target(target)[0]
            await self.mdns.query_and_wait(2.5)
            svc = next((s for s in self.mdns.list("connect") if s["ip"] == ip), None)
            if svc:
                r = await self.connect_target(svc["target"], quiet=True)
        if r["ok"]:
            self.log("ok", f"Imported your previous phone ({self.device_name(r.get('id'))}).", r.get("id"))
        elif svc and svc.get("serial"):
            # Remember it anyway (identified by its mDNS serial) so it shows up with a Troubleshoot button.
            self.store.upsert(svc["serial"], {"lastIp": svc["ip"], "lastPort": svc["port"], "lastTransport": "wifi"})
            self.needs_pairing.add(svc["serial"])
            self.log(
                "warn",
                f"Found your previous phone ({svc['serial']}) at {svc['target']} but it refused this computer: {r.get('message')}. "
                "It probably needs pairing again — use Troubleshoot or Add device › QR code.",
                svc["serial"],
            )
            self.changed()
        else:
            self.log(
                "info",
                f'Previous device at {target} not reachable right now ({r.get("message")}). Add it via "Add device" when Wireless debugging is on.',
            )
