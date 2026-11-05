"""Desktop integration: app-menu / Applications entry and start-at-login.

Linux  ~/.local/share/applications/android-mirror.desktop + hicolor icon,
       ~/.config/autostart/android-mirror.desktop (XDG autostart, runs inside the desktop
       session so scrcpy can open windows)
macOS  ~/Applications/Android Mirror.app (a small launcher bundle) and
       ~/Library/LaunchAgents/local.android-mirror.plist (keeps the server running)
"""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import APP_ID, APP_NAME, __version__, paths
from .deps import EXTRA_PATHS

WEB = Path(__file__).resolve().parent / "web"
LAUNCH_LABEL = "local.android-mirror"

XDG_DATA = Path(os.environ.get("XDG_DATA_HOME") or paths.HOME / ".local" / "share")
XDG_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or paths.HOME / ".config")
LINUX_DESKTOP = XDG_DATA / "applications" / f"{APP_ID}.desktop"
LINUX_ICON = XDG_DATA / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"
LINUX_AUTOSTART = XDG_CONFIG / "autostart" / f"{APP_ID}.desktop"
MAC_APP = paths.HOME / "Applications" / f"{APP_NAME}.app"
MAC_AGENT = paths.HOME / "Library" / "LaunchAgents" / f"{LAUNCH_LABEL}.plist"


def command() -> list[str]:
    """How to launch this installation of the app (prefers the venv's console script)."""
    script = Path(sys.executable).parent / APP_ID
    if script.is_file():
        return [str(script)]
    return [sys.executable, "-m", "android_mirror"]


def _desktop_exec(args: list[str]) -> str:
    # Desktop Entry spec: quote args containing reserved characters with double quotes.
    def q(a: str) -> str:
        if not any(c in a for c in " \t\"'\\><~|&;$*?#()`"):
            return a
        return '"' + a.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$") + '"'

    return " ".join(q(a) for a in args)


def installed() -> dict[str, bool]:
    if paths.IS_MAC:
        return {"menu": MAC_APP.exists(), "autostart": MAC_AGENT.exists()}
    return {"menu": LINUX_DESKTOP.exists(), "autostart": LINUX_AUTOSTART.exists()}


# ------------------------------------------------------------------ Linux


def _linux_install(autostart: bool) -> list[str]:
    done = []
    LINUX_ICON.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(WEB / "icon.svg", LINUX_ICON)
    cmd = command()
    entry = (
        "[Desktop Entry]\nType=Application\nVersion=1.0\n"
        f"Name={APP_NAME}\nComment=Mirror and control your Android phone over Wi-Fi (scrcpy)\n"
        f"Exec={_desktop_exec(cmd + ['open'])}\nIcon={APP_ID}\nTerminal=false\n"
        "Categories=Utility;\nKeywords=android;scrcpy;adb;mirror;phone;screen;\nStartupNotify=false\n"
        f"StartupWMClass={APP_ID}\n"
    )
    LINUX_DESKTOP.parent.mkdir(parents=True, exist_ok=True)
    LINUX_DESKTOP.write_text(entry)
    done.append(f"App menu entry: {LINUX_DESKTOP}")
    if autostart:
        LINUX_AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
        LINUX_AUTOSTART.write_text(
            "[Desktop Entry]\nType=Application\n"
            f"Name={APP_NAME} (background)\nComment=Keeps remembered phones connected\n"
            f"Exec={_desktop_exec(cmd + ['serve', '--no-browser'])}\nIcon={APP_ID}\nTerminal=false\n"
            "NoDisplay=true\nX-GNOME-Autostart-enabled=true\n"
        )
        done.append(f"Start at login: {LINUX_AUTOSTART}")
    else:
        LINUX_AUTOSTART.unlink(missing_ok=True)
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(LINUX_DESKTOP.parent)], capture_output=True)
    return done


def _linux_remove() -> list[str]:
    removed = []
    for p in (LINUX_DESKTOP, LINUX_AUTOSTART, LINUX_ICON):
        if p.exists():
            p.unlink()
            removed.append(str(p))
    return removed


# ------------------------------------------------------------------ macOS


def _mac_icns(dest: Path) -> bool:
    """Build AppIcon.icns from the bundled PNG with the stock sips + iconutil tools."""
    if not (shutil.which("sips") and shutil.which("iconutil")):
        return False
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = size * scale
                name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
                r = subprocess.run(
                    ["sips", "-z", str(px), str(px), str(WEB / "icon.png"), "--out", str(iconset / name)], capture_output=True
                )
                if r.returncode:
                    return False
        return subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(dest)], capture_output=True).returncode == 0


def _mac_install(autostart: bool) -> list[str]:
    done = []
    cmd = command()
    contents = MAC_APP / "Contents"
    shutil.rmtree(MAC_APP, ignore_errors=True)
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    launcher = contents / "MacOS" / APP_ID
    launcher.write_text(f'#!/bin/sh\nexec {shlex.join(cmd)} open "$@"\n')
    launcher.chmod(0o755)
    info = {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": "local.android-mirror.app",
        "CFBundleExecutable": APP_ID,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "LSMinimumSystemVersion": "11.0",
        "LSUIElement": True,  # launcher only: opens the browser and exits, no Dock icon
        "NSHighResolutionCapable": True,
    }
    if _mac_icns(contents / "Resources" / "AppIcon.icns"):
        info["CFBundleIconFile"] = "AppIcon"
    with open(contents / "Info.plist", "wb") as f:
        plistlib.dump(info, f)
    done.append(f"Application: {MAC_APP}")
    _mac_agent_unload()
    if autostart:
        paths.log_dir().mkdir(parents=True, exist_ok=True)
        MAC_AGENT.parent.mkdir(parents=True, exist_ok=True)
        with open(MAC_AGENT, "wb") as f:
            plistlib.dump(
                {
                    "Label": LAUNCH_LABEL,
                    "ProgramArguments": cmd + ["serve", "--no-browser"],
                    "RunAtLoad": True,
                    "KeepAlive": {"SuccessfulExit": False},
                    "ProcessType": "Interactive",
                    "EnvironmentVariables": {"PATH": os.pathsep.join(EXTRA_PATHS)},
                    "StandardOutPath": str(paths.log_dir() / "launchd.log"),
                    "StandardErrorPath": str(paths.log_dir() / "launchd.log"),
                },
                f,
            )
        _launchctl("bootstrap", f"gui/{os.getuid()}", str(MAC_AGENT))
        done.append(f"Start at login: {MAC_AGENT}")
    else:
        MAC_AGENT.unlink(missing_ok=True)
    return done


def _launchctl(*args: str) -> bool:
    return subprocess.run(["launchctl", *args], capture_output=True).returncode == 0


def _mac_agent_unload():
    if MAC_AGENT.exists():
        _launchctl("bootout", f"gui/{os.getuid()}/{LAUNCH_LABEL}") or _launchctl("unload", str(MAC_AGENT))


def mac_kickstart() -> bool:
    """Start the LaunchAgent now (used by `android-mirror start` when it is installed)."""
    return paths.IS_MAC and MAC_AGENT.exists() and _launchctl("kickstart", f"gui/{os.getuid()}/{LAUNCH_LABEL}")


def _mac_remove() -> list[str]:
    removed = []
    _mac_agent_unload()
    if MAC_AGENT.exists():
        MAC_AGENT.unlink()
        removed.append(str(MAC_AGENT))
    if MAC_APP.exists():
        shutil.rmtree(MAC_APP)
        removed.append(str(MAC_APP))
    return removed


# ------------------------------------------------------------------ public


def install(autostart: bool = True) -> list[str]:
    return _mac_install(autostart) if paths.IS_MAC else _linux_install(autostart)


def remove() -> list[str]:
    return _mac_remove() if paths.IS_MAC else _linux_remove()
