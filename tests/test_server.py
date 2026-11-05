import asyncio

from aiohttp.test_utils import TestClient, TestServer

from android_mirror import server


def _run(coro):
    return asyncio.run(coro)


async def _with_client(fn, monkeypatch):
    async def fake_probe():
        return {
            "adb": {"ok": False},
            "scrcpy": {"ok": False},
            "missing": ["adb", "scrcpy"],
            "ready": False,
            "methods": [],
            "display": True,
            "platform": "test",
            "os": "linux",
        }

    monkeypatch.setattr(server.Manager, "start", lambda self: asyncio.sleep(0))
    monkeypatch.setattr(server.MdnsBrowser, "start", lambda self: asyncio.sleep(0))
    monkeypatch.setattr("android_mirror.deps.probe", fake_probe)
    app = server.build_app(server.Config())
    async with TestClient(TestServer(app)) as c:
        await fn(c)


def test_guard_and_api(monkeypatch):
    async def checks(c):
        r = await c.get("/api/version")
        assert r.status == 200 and (await r.json())["name"] == "android-mirror"
        r = await c.get("/api/state", headers={"Host": "evil.example:8765"})
        assert r.status == 403
        r = await c.post("/api/scan", data="x", headers={"Content-Type": "text/plain"})
        assert r.status == 415
        r = await c.post("/api/scan", json={}, headers={"Origin": "http://evil.example"})
        assert r.status == 403
        r = await c.post("/api/setup/install", json={"method": "curl|sh"})
        assert r.status == 400
        r = await c.get("/")
        assert r.status == 200 and "Android Mirror" in await r.text()

    _run(_with_client(checks, monkeypatch))


def test_sanitize_patch():
    out = server.sanitize_patch(
        {
            "nickname": " x " * 40,
            "autoConnect": "yes",
            "scrcpy": {"maxFps": 999, "maxSize": 1024, "videoCodec": "vp9", "audio": False, "extraArgs": "--no-mipmaps"},
        }
    )
    assert len(out["nickname"]) <= 60 and "autoConnect" not in out
    assert out["scrcpy"] == {"maxSize": 1024, "audio": False, "extraArgs": "--no-mipmaps"}


def test_mirror_command(monkeypatch):
    async def checks(c):
        m = c.server.app[server.MANAGER]
        m.store.upsert("SER1", {"model": "Pixel", "scrcpy": {"maxFps": 30}})
        r = await c.get("/api/devices/SER1/command")
        data = await r.json()
        assert r.status == 200 and not data["connected"]
        assert "--max-fps=30" in data["command"] and data["command"][0].endswith("scrcpy")
        r = await c.get("/api/devices/NOPE/command")
        assert r.status == 400

    _run(_with_client(checks, monkeypatch))


def test_app_settings(monkeypatch):
    async def checks(c):
        r = await c.get("/api/app-settings")
        assert (await r.json())["openMode"] == "window"
        r = await c.patch("/api/app-settings", json={"openMode": "browser"})
        assert r.status == 200 and (await r.json())["openMode"] == "browser"
        r = await c.patch("/api/app-settings", json={"openMode": "fullscreen"})
        assert r.status == 400

    _run(_with_client(checks, monkeypatch))
