"""HTTP API + Server-Sent Events + static UI (aiohttp)."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from aiohttp import web

from . import __version__, about, appwindow, paths
from .manager import Manager
from .mdns import MdnsBrowser
from .store import DEFAULT_SCRCPY, Store
from .util import is_ipv4

WEB_ROOT = Path(__file__).resolve().parent / "web"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
log = logging.getLogger(__name__)


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8765
    data_dir: Path = field(default_factory=paths.data_dir)

    @property
    def lan_mode(self) -> bool:
        return self.host not in ("127.0.0.1", "localhost", "::1")

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"


CONFIG = web.AppKey("config", Config)
MANAGER = web.AppKey("manager", Manager)
CLIENTS = web.AppKey("clients", set)


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _host_of(h: str | None) -> str:
    h = (h or "").lower()
    return h.rsplit(":", 1)[0] if h.count(":") == 1 or h.startswith("[") and "]:" in h else h


@web.middleware
async def guard(request: web.Request, handler):
    """Blocks DNS-rebinding and cross-site requests — this server can run adb and scrcpy."""
    try:
        host = _host_of(request.headers.get("Host"))
        lan_mode = request.app[CONFIG].lan_mode
        if host not in LOCAL_HOSTS and not (lan_mode and (is_ipv4(host) or host.endswith(".local"))):
            raise HttpError(403, "Forbidden host")
        if request.method not in ("GET", "HEAD"):
            origin = request.headers.get("Origin")
            if origin and _host_of(origin.split("://", 1)[-1]) != host:
                raise HttpError(403, "Cross-origin request blocked")
            if not request.content_type.startswith("application/json"):
                raise HttpError(415, "Use application/json")
        return await handler(request)
    except HttpError as e:
        return web.json_response({"ok": False, "error": str(e)}, status=e.status)
    except web.HTTPException:
        raise
    except (ValueError, KeyError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
    except Exception as e:
        request.app[MANAGER].log("error", f"{type(e).__name__}: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def body(request: web.Request) -> dict:
    if not request.can_read_body:
        return {}
    raw = await request.read()
    if len(raw) > 64 * 1024:
        raise HttpError(413, "Body too large")
    try:
        data = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise HttpError(400, "Invalid JSON") from None
    return data if isinstance(data, dict) else {}


def sanitize_patch(b: dict) -> dict:
    out = {}
    if isinstance(b.get("nickname"), str):
        out["nickname"] = b["nickname"][:60].strip()
    for k in ("autoConnect", "autoMirror", "keepAlive"):
        if isinstance(b.get(k), bool):
            out[k] = b[k]
    s = b.get("scrcpy")
    if isinstance(s, dict):
        o = {}

        def num(v, lo, hi):
            return isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi

        if isinstance(s.get("preset"), str):
            o["preset"] = s["preset"][:20]
        if num(s.get("maxSize"), 0, 8192):
            o["maxSize"] = int(s["maxSize"])
        if num(s.get("videoBitRate"), 0, 200):
            o["videoBitRate"] = s["videoBitRate"]
        if num(s.get("maxFps"), 0, 240):
            o["maxFps"] = int(s["maxFps"])
        if s.get("videoCodec") in ("h264", "h265", "av1"):
            o["videoCodec"] = s["videoCodec"]
        if s.get("keyboard") in ("sdk", "uhid", "aoa"):
            o["keyboard"] = s["keyboard"]
        for k, v in DEFAULT_SCRCPY.items():
            if isinstance(v, bool) and isinstance(s.get(k), bool):
                o[k] = s[k]
        if isinstance(s.get("extraArgs"), str):
            o["extraArgs"] = s["extraArgs"][:500]
        out["scrcpy"] = o
    return out


def build_app(config: Config | None = None) -> web.Application:
    config = config or Config()
    migrated = paths.migrate_legacy_data(Path(config.data_dir))
    store = Store(str(config.data_dir))
    store.load()
    mdns = MdnsBrowser()
    m = Manager(store, mdns)
    app = web.Application(middlewares=[guard])
    app[CONFIG], app[MANAGER], app[CLIENTS] = config, m, set()
    r = web.RouteTableDef()
    J = web.json_response

    @r.get("/api/state")
    async def state(req):
        return J(m.snapshot())

    @r.get("/api/logs")
    async def logs(req):
        return J(m.logs[-300:])

    @r.get("/api/version")
    async def version(req):
        return J({"name": "android-mirror", "version": __version__})

    @r.get("/api/app-settings")
    async def app_settings(req):
        return J({"openMode": store.settings.get("openMode", "window"), "window": appwindow.describe()})

    @r.patch("/api/app-settings")
    async def app_settings_patch(req):
        mode = (await body(req)).get("openMode")
        if mode not in appwindow.MODES:
            raise HttpError(400, "openMode must be 'window' or 'browser'")
        store.set_setting("openMode", mode)
        return J({"openMode": mode, "window": appwindow.describe()})

    @r.get("/api/about")
    async def about_info(req):
        return J(about.info(m.tools))

    @r.post("/api/setup/check")
    async def setup_check(req):
        return J(await m.check_tools())

    @r.post("/api/setup/install")
    async def setup_install(req):
        method = str((await body(req)).get("method") or "")
        if method not in ("brew", "system", "portable"):
            raise HttpError(400, "Unknown install method")
        return J(m.install_deps(method))

    @r.post("/api/scan")
    async def scan(req):
        m._spawn(m.scan_network())
        return J({"started": True})

    @r.post("/api/find-port")
    async def find_port(req):
        b = await body(req)
        if not is_ipv4(b.get("ip")):
            raise HttpError(400, "Enter a valid IPv4 address")
        return J(await m.find_port_on_host(b["ip"]))

    @r.post("/api/connect")
    async def connect(req):
        target = str((await body(req)).get("target") or "").strip()
        res = await m.connect_target(target if ":" in target else f"{target}:5555")
        if res["ok"]:
            m.log("ok", f"Connected to {target}.")
        return J(res)

    @r.post("/api/pair")
    async def pair(req):
        b = await body(req)
        return J(await m.pair_with_code(b.get("target"), b.get("code")))

    @r.post("/api/pair/qr")
    async def pair_qr(req):
        return J(await m.start_qr_pairing())

    @r.post("/api/pair/cancel")
    async def pair_cancel(req):
        m.cancel_pairing()
        return J({"ok": True})

    @r.post("/api/usb/{serial}/wifi")
    async def usb_wifi(req):
        return J(await m.usb_to_wifi(req.match_info["serial"]))

    @r.post("/api/adb/restart")
    async def adb_restart(req):
        await m.restart_adb()
        return J({"ok": True})

    @r.post("/api/devices/{id}/connect")
    async def dev_connect(req):
        return J(await m.ensure_connected(req.match_info["id"]))

    @r.post("/api/devices/{id}/disconnect")
    async def dev_disconnect(req):
        await m.disconnect_device(req.match_info["id"])
        return J({"ok": True})

    @r.post("/api/devices/{id}/mirror")
    async def dev_mirror(req):
        return J(await m.start_mirror(req.match_info["id"]))

    @r.post("/api/devices/{id}/stop")
    async def dev_stop(req):
        await m.stop_mirror(req.match_info["id"])
        return J({"ok": True})

    @r.get("/api/devices/{id}/command")
    async def dev_command(req):
        return J(m.mirror_command(req.match_info["id"]))

    @r.post("/api/devices/{id}/troubleshoot")
    async def dev_trouble(req):
        m.troubleshoot(req.match_info["id"])
        return J({"started": True})

    @r.patch("/api/devices/{id}")
    async def dev_patch(req):
        return J(m.update_device(req.match_info["id"], sanitize_patch(await body(req))))

    @r.delete("/api/devices/{id}")
    async def dev_delete(req):
        await m.forget(req.match_info["id"])
        return J({"ok": True})

    @r.get("/api/events")
    async def events(req):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store", "Connection": "keep-alive"})
        await resp.prepare(req)
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        app[CLIENTS].add(q)
        try:
            await resp.write(f"retry: 2000\nevent: state\ndata: {json.dumps(m.snapshot())}\n\n".encode())
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), 20)
                except asyncio.TimeoutError:
                    msg = ": ping\n\n"
                await resp.write(msg.encode())
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            app[CLIENTS].discard(q)
        return resp

    @r.get("/")
    async def index(req):
        return web.FileResponse(WEB_ROOT / "index.html", headers={"Cache-Control": "no-cache"})

    app.add_routes(r)
    app.router.add_static("/", WEB_ROOT, show_index=False)

    def broadcast(event: str, data):
        msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        for q in list(app[CLIENTS]):
            if event == "state":  # only the latest state matters; drop stale ones if a client lags
                while q.qsize() > 50:
                    q.get_nowait()
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    m.state_listeners.append(lambda s: broadcast("state", s))
    m.log_listeners.append(lambda entry: broadcast("log", entry))

    async def on_startup(app):
        await mdns.start()
        await m.start()
        if migrated:
            m.log("info", f"Copied remembered devices from {migrated} to {config.data_dir} (the old copy can be deleted).")
        legacy = paths.legacy_last_connection()
        if legacy:
            m._spawn(m.import_legacy(legacy.read_text().strip()))

    async def on_cleanup(app):
        await m.stop()
        mdns.stop()
        store.flush()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def run(config: Config):
    """Serve until interrupted. Raises SystemExit if the port is taken."""
    app = build_app(config)
    log.info("Android Mirror %s running at %s (data: %s)", __version__, config.url, config.data_dir)
    if config.lan_mode:
        log.warning("Listening on %s: anyone on your network can control adb on this computer.", config.host)
    try:
        web.run_app(app, host=config.host, port=config.port, print=None, handle_signals=True)
    except OSError as e:
        if "address already in use" in str(e).lower() or getattr(e, "errno", None) in (48, 98):
            raise SystemExit(f"Port {config.port} is in use. Is Android Mirror already running? Open {config.url}") from e
        raise
