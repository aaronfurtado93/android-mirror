"""Finding, checking and installing the external tools: adb (Android platform-tools) and scrcpy.

Lookup order for each tool:
  1. ADB_PATH / SCRCPY_PATH environment variables
  2. the app's own copy in <data dir>/tools (installed with the "portable" method)
  3. PATH, plus the usual Homebrew / Android SDK / snap locations (GUI apps on macOS
     start with a minimal PATH, so these are added explicitly)

Install methods offered depend on the platform:
  macOS  Homebrew (`brew install scrcpy` + `--cask android-platform-tools`) or portable
  Linux  the system package manager (via pkexec from the UI, or sudo in a terminal) or
         portable (official static builds; x86_64 only)
"Portable" downloads Google's platform-tools and the official scrcpy release from GitHub
and verifies their checksums before installing them into the per-user tools directory.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import stat
import tarfile
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Callable

from . import __version__
from .paths import HOME, IS_MAC, tools_dir
from .sysinfo import has_display, platform_label

MIN_SCRCPY = (2, 4)  # --video-bit-rate, --video-codec, --keyboard=uhid
SCRCPY_RELEASES = "https://api.github.com/repos/Genymobile/scrcpy/releases/latest"
SDK_REPO = "https://dl.google.com/android/repository/"
SDK_MANIFEST = SDK_REPO + "repository2-3.xml"
SDK_LICENSE = "https://developer.android.com/studio/releases/platform-tools"
USER_AGENT = f"android-mirror/{__version__}"

Log = Callable[[str, str], None]  # (level, message)

EXTRA_PATHS = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/snap/bin",
    str(HOME / ".local" / "bin"),
    str(HOME / "Library" / "Android" / "sdk" / "platform-tools"),
    str(HOME / "Android" / "Sdk" / "platform-tools"),
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
]

ENV_OVERRIDE = {"adb": "ADB_PATH", "scrcpy": "SCRCPY_PATH"}


def portable_path(tool: str) -> Path:
    return tools_dir() / ("platform-tools" if tool == "adb" else "scrcpy") / tool


def search_path() -> str:
    parts = os.environ.get("PATH", "").split(os.pathsep)
    return os.pathsep.join(dict.fromkeys(p for p in parts + EXTRA_PATHS if p))


def find(tool: str) -> tuple[str | None, str]:
    """(path, source) where source is 'env', 'portable', 'system' or 'missing'."""
    env = os.environ.get(ENV_OVERRIDE[tool])
    if env:
        resolved = shutil.which(env, path=search_path()) if os.sep not in env else env
        return (resolved if resolved and os.access(resolved, os.X_OK) else None), "env"
    p = portable_path(tool)
    if p.is_file() and os.access(p, os.X_OK):
        return str(p), "portable"
    found = shutil.which(tool, path=search_path())
    return (found, "system") if found else (None, "missing")


_cache: dict[str, str | None] = {}


def refresh_paths():
    for t in ("adb", "scrcpy"):
        _cache[t] = find(t)[0]


def path_of(tool: str) -> str:
    """Resolved executable, or the bare name (so errors read naturally) if it is missing."""
    if tool not in _cache:
        refresh_paths()
    return _cache.get(tool) or tool


def child_env() -> dict[str, str]:
    """Environment for scrcpy: make it use *our* adb (two different adb versions kill each other's server)."""
    env = dict(os.environ)
    env["PATH"] = search_path()
    adb = _cache.get("adb")
    if adb:
        env["ADB"] = adb
    return env


def parse_version(v: str | None) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])


async def _scrcpy_version(path: str | None) -> str | None:
    if not path:
        return None
    try:
        p = await asyncio.create_subprocess_exec(
            path, "--version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=child_env()
        )
        out, _ = await asyncio.wait_for(p.communicate(), 8)
    except (OSError, asyncio.TimeoutError):
        return None
    m = re.search(r"scrcpy v?([\d.]+)", out.decode(errors="replace"))
    return m.group(1) if m else None


# ------------------------------------------------------------------ status


async def probe() -> dict:
    """Current state of both tools plus the install methods that would fix anything missing."""
    from . import adb as adbmod

    refresh_paths()
    adb_path, adb_src = find("adb")
    sc_path, sc_src = find("scrcpy")
    av, sv = await asyncio.gather(adbmod.version() if adb_path else _none(), _scrcpy_version(sc_path))
    outdated = bool(sv) and parse_version(sv) < MIN_SCRCPY
    status = {
        "adb": {"ok": bool(av), "path": adb_path, "source": adb_src, "version": av and (av.get("platformTools") or av.get("version"))},
        "scrcpy": {
            "ok": bool(sv) and not outdated,
            "path": sc_path,
            "source": sc_src,
            "version": sv,
            "outdated": outdated,
            "minimum": ".".join(map(str, MIN_SCRCPY)),
        },
        "display": has_display(),
        "platform": platform_label(),
        "os": "macos" if IS_MAC else "linux",
    }
    status["missing"] = [t for t in ("adb", "scrcpy") if not status[t]["ok"]]
    status["ready"] = not status["missing"]
    status["methods"] = await install_methods(status["missing"]) if status["missing"] else []
    return status


async def _none():
    return None


# ------------------------------------------------------------------ install methods


def _machine() -> str:
    m = platform.machine().lower()
    return {"amd64": "x86_64", "arm64": "aarch64"}.get(m, m)


def portable_supported() -> bool:
    return IS_MAC or _machine() == "x86_64"


LINUX_PMS = [
    # (binary, display name, install argv prefix, {tool: package}, scrcpy available in the default repos)
    ("apt-get", "APT", ["apt-get", "install", "-y"], {"adb": "adb", "scrcpy": "scrcpy"}, True),
    ("dnf", "DNF", ["dnf", "install", "-y"], {"adb": "android-tools"}, False),
    ("pacman", "pacman", ["pacman", "-S", "--needed", "--noconfirm"], {"adb": "android-tools", "scrcpy": "scrcpy"}, True),
    ("zypper", "zypper", ["zypper", "--non-interactive", "install"], {"adb": "android-tools", "scrcpy": "scrcpy"}, True),
]


def brew_path() -> str | None:
    return shutil.which("brew", path=search_path())


async def _apt_candidate(pkg: str) -> str | None:
    try:
        p = await asyncio.create_subprocess_exec(
            "apt-cache", "policy", pkg, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env={**os.environ, "LC_ALL": "C"}
        )
        out, _ = await asyncio.wait_for(p.communicate(), 8)
    except (OSError, asyncio.TimeoutError):
        return None
    m = re.search(r"Candidate:\s*(?:\d+:)?([\d.]+)", out.decode(errors="replace"))
    return m.group(1) if m else None


async def install_methods(missing: list[str]) -> list[dict]:
    methods: list[dict] = []
    if IS_MAC:
        brew = brew_path()
        cmds = []
        if "scrcpy" in missing:
            cmds.append([brew or "brew", "install", "scrcpy"])
        if "adb" in missing:
            cmds.append([brew or "brew", "install", "--cask", "android-platform-tools"])
        methods.append(
            {
                "id": "brew",
                "label": "Homebrew",
                "covers": missing,
                "commands": cmds,
                "runnable": bool(brew),
                "terminal": " && ".join(shlex.join(["brew", *c[1:]]) for c in cmds),
                "description": "Installs with Homebrew and keeps them updated with `brew upgrade`.",
                "note": None if brew else "Homebrew is not installed: see https://brew.sh, or use the download option.",
            }
        )
    else:
        for binary, name, prefix, pkgs, has_scrcpy in LINUX_PMS:
            if not shutil.which(binary):
                continue
            covers = [t for t in missing if t in pkgs]
            note = None
            if "scrcpy" in covers and binary == "apt-get":
                cand = await _apt_candidate("scrcpy")
                if not cand or parse_version(cand) < MIN_SCRCPY:
                    covers.remove("scrcpy")
                    note = (
                        f"Your distribution's scrcpy ({cand}) is too old; it needs {'.'.join(map(str, MIN_SCRCPY))}+."
                        if cand
                        else "scrcpy is not available from your distribution's repositories."
                    )
            elif "scrcpy" in missing and not has_scrcpy:
                note = f"scrcpy is not in the default {name} repositories."
            if not covers:
                if note:
                    methods.append(
                        {
                            "id": "system",
                            "label": f"System packages ({name})",
                            "covers": [],
                            "commands": [],
                            "runnable": False,
                            "terminal": "",
                            "description": "",
                            "note": note,
                        }
                    )
                break
            argv = [*prefix, *[pkgs[t] for t in covers]]
            pkexec = shutil.which("pkexec") if has_display() else None
            methods.append(
                {
                    "id": "system",
                    "label": f"System packages ({name})",
                    "covers": covers,
                    "commands": [[pkexec, *argv]] if pkexec else [["sudo", *argv]],
                    "runnable": bool(pkexec),
                    "terminal": shlex.join(["sudo", *argv]),
                    "description": "Installs from your distribution and updates with the rest of the system. Asks for your password.",
                    "note": note,
                }
            )
            break
    if portable_supported():
        methods.append(
            {
                "id": "portable",
                "label": "Download official builds",
                "covers": missing,
                "commands": [],
                "runnable": True,
                "terminal": "android-mirror install-deps --method portable",
                "description": f"Latest scrcpy from GitHub and Google's platform-tools, checksum-verified, into {tools_dir()}. "
                "No admin rights needed.",
                "note": "adb from Google's platform-tools is covered by the Android SDK terms: " + SDK_LICENSE
                if "adb" in missing
                else None,
            }
        )
    # Recommend the first method that fixes everything (Homebrew / system packages integrate better).
    for m in methods:
        if m["runnable"] and set(m["covers"]) >= set(missing):
            m["recommended"] = True
            break
    else:
        for m in methods:
            if m["runnable"] and m["covers"]:
                m["recommended"] = True
                break
    return methods


async def install(method_id: str, missing: list[str], log: Log) -> dict:
    """Run an install method. Returns {ok, message}."""
    methods = {m["id"]: m for m in await install_methods(missing)}
    m = methods.get(method_id)
    if not m:
        return {"ok": False, "message": f"Install method '{method_id}' is not available on this computer."}
    if not m["runnable"]:
        return {"ok": False, "message": m.get("note") or f"Run this in a terminal: {m['terminal']}"}
    if method_id == "portable":
        loop = asyncio.get_running_loop()

        def tlog(level, msg):
            loop.call_soon_threadsafe(log, level, msg)

        try:
            if "adb" in m["covers"]:
                await asyncio.to_thread(install_platform_tools, tlog)
            if "scrcpy" in m["covers"]:
                await asyncio.to_thread(install_scrcpy, tlog)
        except Exception as e:
            return {"ok": False, "message": f"Download failed: {e}"}
    else:
        for argv in m["commands"]:
            log("info", f"$ {shlex.join(argv)}")
            code = await _stream(argv, log)
            if code != 0:
                hint = " (cancelled or wrong password?)" if code in (126, 127) and "pkexec" in argv[0] else ""
                return {
                    "ok": False,
                    "message": f"`{shlex.join(argv)}` failed with exit code {code}{hint}. "
                    f"You can also run it in a terminal: {m['terminal']}",
                }
    refresh_paths()
    return {"ok": True, "message": "Installed " + " and ".join(m["covers"]) + "."}


async def _stream(argv: list[str], log: Log) -> int:
    env = {**os.environ, "PATH": search_path(), "DEBIAN_FRONTEND": "noninteractive", "HOMEBREW_NO_AUTO_UPDATE": "1"}
    try:
        p = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env
        )
    except OSError as e:
        log("error", str(e))
        return 127
    assert p.stdout
    async for raw in p.stdout:
        line = raw.decode(errors="replace").rstrip()
        if line:
            log("debug", line)
    return await p.wait()


# ------------------------------------------------------------------ portable downloads


def _fetch(url: str, accept: str = "*/*") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _download(url: str, dest: Path, log: Log, label: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done, last = 0, 0.0
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total and time.monotonic() - last > 1.5:
                last = time.monotonic()
                log("info", f"Downloading {label}… {done * 100 // total}%")
    log("info", f"Downloaded {label} ({done / 1e6:.1f} MB).")


def _verify(path: Path, algo: str, expected: str):
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest().lower() != expected.lower():
        raise RuntimeError(f"checksum mismatch for {path.name} — download corrupted or tampered with; nothing was installed")


def _within(base: Path, name: str) -> bool:
    target = (base / name).resolve()
    return target == base.resolve() or base.resolve() in target.parents


def _strip_single_root(src: Path) -> Path:
    entries = [p for p in src.iterdir() if not p.name.startswith(".")]
    return entries[0] if len(entries) == 1 and entries[0].is_dir() else src


def _swap_into_place(src: Path, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    old = dest.with_name(dest.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if dest.exists():
        dest.rename(old)
    shutil.move(str(src), str(dest))
    shutil.rmtree(old, ignore_errors=True)


def platform_tools_archive(manifest_xml: bytes, host: str) -> dict:
    """Latest *stable* platform-tools archive for host ('linux' | 'macosx') from Google's SDK manifest."""
    root = ET.fromstring(manifest_xml)  # noqa: S314 - fetched over HTTPS from dl.google.com
    best = None
    for pkg in root.iter():
        if not pkg.tag.endswith("remotePackage") or pkg.get("path") != "platform-tools":
            continue
        channel = next((c.get("ref") for c in pkg if c.tag.endswith("channelRef")), "channel-0")
        if channel != "channel-0":
            continue
        rev = next((c for c in pkg if c.tag.endswith("revision")), None)
        version = tuple(int(x.text or 0) for x in rev if x.tag.split("}")[-1] in ("major", "minor", "micro")) if rev is not None else ()
        for arch in pkg.iter():
            if not arch.tag.endswith("archive"):
                continue
            os_ = next((c.text for c in arch if c.tag.endswith("host-os")), None)
            comp = next((c for c in arch if c.tag.endswith("complete")), None)
            if os_ != host or comp is None:
                continue
            info = {c.tag.split("}")[-1]: c for c in comp}
            cand = {
                "version": version,
                "url": SDK_REPO + info["url"].text.strip(),
                "sha1": info["checksum"].text.strip(),
                "size": int(info["size"].text),
            }
            if not best or version > best["version"]:
                best = cand
    if not best:
        raise RuntimeError("platform-tools not found in Google's SDK manifest")
    return best


def install_platform_tools(log: Log) -> Path:
    host = "macosx" if IS_MAC else "linux"
    log("info", "Looking up the latest Android platform-tools (adb)…")
    a = platform_tools_archive(_fetch(SDK_MANIFEST), host)
    with tempfile.TemporaryDirectory(prefix="am-pt-") as tmp:
        zpath = Path(tmp) / "platform-tools.zip"
        _download(a["url"], zpath, log, f"platform-tools {'.'.join(map(str, a['version']))}")
        _verify(zpath, "sha1", a["sha1"])
        out = Path(tmp) / "x"
        with zipfile.ZipFile(zpath) as z:
            for info in z.infolist():
                if not _within(out, info.filename):
                    raise RuntimeError(f"unsafe path in archive: {info.filename}")
                z.extract(info, out)
                mode = (info.external_attr >> 16) & 0o777
                if mode and not info.is_dir():
                    os.chmod(out / info.filename, mode)
        src = out / "platform-tools"
        adb = src / "adb"
        adb.chmod(adb.stat().st_mode | stat.S_IXUSR)
        dest = tools_dir() / "platform-tools"
        _swap_into_place(src, dest)
    log("ok", f"Installed adb to {dest}.")
    return dest


def scrcpy_asset(release: dict) -> dict:
    """Pick the official static build for this OS/CPU from a GitHub release JSON."""
    if IS_MAC:
        pattern = rf"^scrcpy-macos-{'aarch64' if _machine() == 'aarch64' else 'x86_64'}-v[\d.]+\.tar\.gz$"
    else:
        pattern = rf"^scrcpy-linux-{_machine()}-v[\d.]+\.tar\.gz$"
    for a in release.get("assets", []):
        if re.match(pattern, a["name"]):
            return a
    raise RuntimeError(
        f"no official scrcpy build for {platform_label()} in {release.get('tag_name')}; install it with your package manager instead"
    )


def _sha256_for(release: dict, asset: dict) -> str:
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:"):
        return digest.split(":", 1)[1]
    sums = next((a for a in release.get("assets", []) if a["name"] == "SHA256SUMS.txt"), None)
    if sums:
        for line in _fetch(sums["browser_download_url"]).decode().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("*") == asset["name"]:
                return parts[0]
    raise RuntimeError("the scrcpy release has no checksum to verify against")


def install_scrcpy(log: Log) -> Path:
    log("info", "Looking up the latest scrcpy release…")
    release = json.loads(_fetch(SCRCPY_RELEASES, "application/vnd.github+json"))
    asset = scrcpy_asset(release)
    sha = _sha256_for(release, asset)
    with tempfile.TemporaryDirectory(prefix="am-sc-") as tmp:
        tpath = Path(tmp) / asset["name"]
        _download(asset["browser_download_url"], tpath, log, f"scrcpy {release.get('tag_name')}")
        _verify(tpath, "sha256", sha)
        out = Path(tmp) / "x"
        out.mkdir()
        with tarfile.open(tpath) as t:
            members = []
            for mem in t.getmembers():
                if not _within(out, mem.name) or mem.issym() or mem.islnk() or mem.isdev():
                    continue
                members.append(mem)
            kwargs = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
            t.extractall(out, members=members, **kwargs)  # noqa: S202 - members vetted above
        src = _strip_single_root(out)
        for exe in ("scrcpy", "adb"):
            p = src / exe
            if p.exists():
                p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if not (src / "scrcpy").is_file():
            raise RuntimeError("unexpected archive layout (no scrcpy executable)")
        dest = tools_dir() / "scrcpy"
        _swap_into_place(src, dest)
    log("ok", f"Installed scrcpy {release.get('tag_name')} to {dest}.")
    return dest


def remove_portable():
    shutil.rmtree(tools_dir(), ignore_errors=True)
    refresh_paths()
