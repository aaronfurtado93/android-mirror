"""Per-user locations for data, logs and downloaded tools (Linux: XDG, macOS: ~/Library)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from . import APP_ID

MAC_DIR_NAME = "AndroidMirror"  # no spaces: keeps venv shebangs and shell scripts simple

IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
HOME = Path.home()

# Repository root when running from a source checkout (src/android_mirror/paths.py -> repo).
SOURCE_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Remembered devices + settings. Override with DATA_DIR."""
    if os.environ.get("DATA_DIR"):
        return Path(os.environ["DATA_DIR"]).expanduser()
    if IS_MAC:
        return HOME / "Library" / "Application Support" / MAC_DIR_NAME
    return Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local" / "share") / APP_ID


def log_dir() -> Path:
    if os.environ.get("DATA_DIR"):
        return data_dir()
    if IS_MAC:
        return HOME / "Library" / "Logs" / MAC_DIR_NAME
    return Path(os.environ.get("XDG_STATE_HOME") or HOME / ".local" / "state") / APP_ID


def log_file() -> Path:
    return log_dir() / "server.log"


def tools_dir() -> Path:
    """Where `android-mirror install-deps --method portable` puts adb and scrcpy."""
    return data_dir() / "tools"


def venv_dir() -> Path:
    """Where install.sh puts the app's virtualenv (kept separate from user data on uninstall)."""
    return data_dir() / "venv"


def pid_file() -> Path:
    return data_dir() / "server.pid"


def bin_dir() -> Path:
    """Where the installer links the `android-mirror` command."""
    return HOME / ".local" / "bin"


def migrate_legacy_data(target: Path) -> Path | None:
    """Copy devices.json from the pre-1.0 location (<checkout>/data) on first run."""
    old = SOURCE_ROOT / "data" / "devices.json"
    new = target / "devices.json"
    if new.exists() or not old.is_file() or old.resolve() == new.resolve():
        return None
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(old, new)
    return old


def legacy_last_connection() -> Path | None:
    """Address file written by the old contrib/scrcpy-connect.sh."""
    for p in (SOURCE_ROOT / ".last-connection", data_dir() / ".last-connection"):
        if p.is_file():
            return p
    return None
