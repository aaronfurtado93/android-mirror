"""App information plus licenses and credits for the projects Android Mirror is built on.

Python libraries are read from the installed package metadata, so the list always matches
what is actually installed, including indirect dependencies. The external tools (scrcpy, adb)
are separate programs that the app runs; they are credited here but not distributed with it.
"""

from __future__ import annotations

import functools
import importlib.metadata as md
import platform
import re
import sys

from . import APP_NAME, __version__, paths

TOOLS = [
    {
        "id": "scrcpy",
        "name": "scrcpy",
        "author": "Genymobile and Romain Vimont",
        "license": "Apache-2.0",
        "url": "https://github.com/Genymobile/scrcpy",
        "role": "Mirrors and controls the phone screen in its own window.",
    },
    {
        "id": "adb",
        "name": "Android Debug Bridge (adb)",
        "author": "Google, Android Open Source Project",
        "license": "Apache-2.0 (source); Android SDK terms (Google's platform-tools downloads)",
        "url": "https://developer.android.com/tools/adb",
        "role": "Connects to the phone over Wi-Fi or USB, pairs and runs commands.",
    },
]

_CLASSIFIER_LICENSES = {
    "Apache Software License": "Apache-2.0",
    "MIT License": "MIT",
    "BSD License": "BSD",
    "Python Software Foundation License": "PSF-2.0",
    "ISC License (ISCL)": "ISC",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
}


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _license(meta) -> str:
    expr = meta.get("License-Expression")
    if expr:
        return expr
    text = (meta.get("License") or "").strip()
    if text and len(text) <= 40 and "\n" not in text:
        return re.sub(r"^Apache(?: License)?[ -]2\.0$", "Apache-2.0", text)
    for c in meta.get_all("Classifier") or []:
        if c.startswith("License :: OSI Approved :: "):
            name = c.rsplit(" :: ", 1)[-1]
            return _CLASSIFIER_LICENSES.get(name, name)
    return "see project"


def _homepage(meta) -> str | None:
    urls = {}
    for entry in meta.get_all("Project-URL") or []:
        label, _, url = entry.partition(",")
        urls[label.strip().lower()] = url.strip()
    for key in ("homepage", "home", "source", "source code", "repository", "github", "code", "documentation"):
        if urls.get(key):
            return urls[key]
    if meta.get("Home-page"):
        return meta["Home-page"]
    gh = next((u for u in urls.values() if "github.com" in u), None)
    if gh:
        return "/".join(gh.split("/")[:5])
    return next(iter(urls.values()), None)


def _requirement_name(req: str) -> str | None:
    """Name from a Requires-Dist line; None for extras-only requirements."""
    if "extra ==" in req.replace("'", '"').replace("extra==", "extra =="):
        return None
    m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", req)
    return m.group(1) if m else None


@functools.lru_cache(maxsize=1)
def libraries() -> list[dict]:
    """Every installed runtime dependency of the app (direct and indirect)."""
    seen: dict[str, dict] = {}

    def walk(name: str, direct: bool):
        key = _norm(name)
        if key in seen:
            return
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            return  # not needed on this Python version / platform
        meta = dist.metadata
        if key != "android-mirror":
            seen[key] = {
                "name": meta["Name"],
                "version": dist.version,
                "license": _license(meta),
                "url": _homepage(meta),
                "summary": meta.get("Summary") or "",
                "direct": direct,
            }
        else:
            seen[key] = {}
        for req in dist.requires or []:
            dep = _requirement_name(req)
            if dep:
                walk(dep, key == "android-mirror")

    walk("android-mirror", True)
    libs = [v for k, v in seen.items() if v]
    return sorted(libs, key=lambda x: (not x["direct"], x["name"].lower()))


@functools.lru_cache(maxsize=1)
def license_text() -> str:
    try:
        dist = md.distribution("android-mirror")
        for f in dist.files or []:
            if f.name in ("LICENSE", "LICENSE.txt"):
                return f.read_text()
    except (md.PackageNotFoundError, OSError):
        pass
    p = paths.SOURCE_ROOT / "LICENSE"
    return p.read_text() if p.is_file() else ""


def info(tools: dict | None = None) -> dict:
    tools = tools or {}
    credited = []
    for t in TOOLS:
        found = tools.get(t["id"]) or {}
        credited.append({**t, "version": found.get("version"), "path": found.get("path"), "source": found.get("source")})
    return {
        "name": APP_NAME,
        "version": __version__,
        "license": "MIT",
        "copyright": "© 2026 Aaron Furtado",
        "licenseText": license_text(),
        "python": f"{platform.python_implementation()} {platform.python_version()}",
        "pythonLicense": "PSF-2.0",
        "platform": tools.get("platform") or sys.platform,
        "dataDir": str(paths.data_dir()),
        "tools": credited,
        "libraries": libraries(),
    }
