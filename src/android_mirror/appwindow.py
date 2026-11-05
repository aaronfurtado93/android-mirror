"""Open the web UI in a dedicated app window (Chromium-based browser in --app mode).

The window uses its own browser profile (<data dir>/window-profile) so it behaves like a
separate application: no tabs or address bar, its own window class / taskbar entry, and it
never mixes with the user's normal browsing windows. Falls back to the default browser
when no Chromium-based browser is installed (or when the user prefers a tab).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import webbrowser
from pathlib import Path

from . import APP_ID
from .deps import search_path
from .paths import HOME, IS_MAC, data_dir

LINUX_BROWSERS = [
    ("google-chrome", "Google Chrome"),
    ("google-chrome-stable", "Google Chrome"),
    ("chromium", "Chromium"),
    ("chromium-browser", "Chromium"),
    ("microsoft-edge", "Microsoft Edge"),
    ("microsoft-edge-stable", "Microsoft Edge"),
    ("brave-browser", "Brave"),
    ("brave", "Brave"),
    ("vivaldi", "Vivaldi"),
]

MAC_BROWSERS = [
    ("Google Chrome.app/Contents/MacOS/Google Chrome", "Google Chrome"),
    ("Chromium.app/Contents/MacOS/Chromium", "Chromium"),
    ("Microsoft Edge.app/Contents/MacOS/Microsoft Edge", "Microsoft Edge"),
    ("Brave Browser.app/Contents/MacOS/Brave Browser", "Brave"),
    ("Vivaldi.app/Contents/MacOS/Vivaldi", "Vivaldi"),
]

WINDOW_SIZE = (1240, 880)
MODES = ("window", "browser")


def find_browser() -> tuple[str, str] | None:
    """(executable, display name) of a Chromium-based browser, or None. Override with APP_BROWSER."""
    env = os.environ.get("APP_BROWSER")
    if env:
        path = shutil.which(env, path=search_path()) or (env if os.access(env, os.X_OK) else None)
        return (path, Path(env).name) if path else None
    if IS_MAC:
        for base in (Path("/Applications"), HOME / "Applications"):
            for rel, name in MAC_BROWSERS:
                p = base / rel
                if os.access(p, os.X_OK):
                    return str(p), name
        return None
    for exe, name in LINUX_BROWSERS:
        p = shutil.which(exe, path=search_path())
        if p:
            return p, name
    return None


def window_command(browser: str, url: str) -> list[str]:
    w, h = WINDOW_SIZE
    return [
        browser,
        f"--app={url}",
        f"--user-data-dir={data_dir() / 'window-profile'}",
        f"--class={APP_ID}",  # X11 WM_CLASS / Wayland app id: matches StartupWMClass in the .desktop entry
        f"--window-size={w},{h}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
    ]


def open_ui(url: str, mode: str = "window") -> str:
    """Open the UI; returns 'window', 'browser' or 'none' (nothing could be opened)."""
    if mode == "window":
        found = find_browser()
        if found:
            try:
                with open(os.devnull, "rb") as devnull, open(os.devnull, "wb") as null:
                    subprocess.Popen(
                        window_command(found[0], url), stdin=devnull, stdout=null, stderr=null, start_new_session=True, close_fds=True
                    )
                return "window"
            except OSError:
                pass
    return "browser" if webbrowser.open(url) else "none"


def describe() -> dict:
    found = find_browser()
    return {"available": bool(found), "browser": found[1] if found else None}
