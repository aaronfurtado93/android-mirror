# Android Mirror

A local web control panel for wireless Android screen mirroring on **Linux and macOS**.
A Python backend drives `adb` + `scrcpy`; the mirror opens in scrcpy's native window on this computer.

- **Easy setup**: pair once with a QR code (or pairing code, USB cable or IP address).
- **Sets up its own dependencies**: detects missing or outdated `adb` / `scrcpy` and installs them with one click
  (Homebrew, your distribution's packages, or checksum-verified official downloads, no admin rights needed).
- **Remembers devices**: name, model, last address and scrcpy settings per phone.
- **Auto-reconnect**: listens for phones announcing *Wireless debugging* (mDNS) and reconnects remembered ones,
  even after their port or IP changed. Follows DHCP changes by MAC address and retries with backoff.
- **Network scan**: mDNS discovery plus a sweep of your subnet for `adb tcpip` (5555) devices.
- **Troubleshooter**: per-device step-by-step diagnosis (adb health, stale connections, network, mDNS, last address,
  hardware-address search, port search) that fixes what it can and says what to do on the phone if it can't.
- **Keep alive**: resumes mirroring automatically if the connection drops.
- **Its own window**: opens as a standalone app window (via Chrome, Chromium, Edge or Brave), or in a browser tab if you prefer.

## Install

Requirements: Linux or macOS 11+, Python 3.9+ (the installer offers to install it if missing).

```bash
./install.sh            # from a source checkout or an unpacked release bundle
```

The installer works per user and needs no root:

1. creates a private virtualenv and installs the app into it,
2. links the `android-mirror` command into `~/.local/bin`,
3. adds **Android Mirror** to the app menu (Linux) or `~/Applications` (macOS) and starts the server at login,
4. checks `adb` and `scrcpy` and offers to install anything missing,
5. starts the server and opens <http://localhost:8765>.

Options: `--yes` (no questions), `--no-deps`, `--no-desktop`, `--no-autostart`, `--uninstall [--purge]`.

| | Linux | macOS |
| --- | --- | --- |
| App | `~/.local/share/android-mirror/venv` | `~/Library/Application Support/AndroidMirror/venv` |
| Data (remembered devices) | `~/.local/share/android-mirror` | `~/Library/Application Support/AndroidMirror` |
| Downloaded adb / scrcpy | `…/android-mirror/tools` | `…/AndroidMirror/tools` |
| Logs | `~/.local/state/android-mirror` | `~/Library/Logs/AndroidMirror` |
| Menu entry | `~/.local/share/applications/android-mirror.desktop` | `~/Applications/Android Mirror.app` |
| Start at login | `~/.config/autostart/android-mirror.desktop` | `~/Library/LaunchAgents/local.android-mirror.plist` |

Uninstall with `android-mirror uninstall` (add `--purge` to also delete remembered devices, downloaded tools and logs).

Alternatively, install just the package with `pipx install .` (or from the wheel), then run `android-mirror desktop install` for the menu entry.

### adb and scrcpy

`android-mirror doctor` shows what is installed. Missing tools can be installed from the web UI
(**⋯ › Setup & dependencies**, which also opens by itself when something is missing) or with `android-mirror install-deps`:

| Method | Platform | What it does |
| --- | --- | --- |
| Homebrew | macOS | `brew install scrcpy` + `brew install --cask android-platform-tools` |
| System packages | Linux (apt, pacman, zypper, dnf) | e.g. `sudo apt-get install adb scrcpy`. From the UI it asks for your password via a system dialog (pkexec). Skipped for scrcpy if the distribution's version is older than 2.4. |
| Official builds | macOS, Linux x86_64 | Latest scrcpy release from GitHub and Google's platform-tools, verified against published SHA-256/SHA-1 checksums, installed into the app's `tools` folder. adb from platform-tools is covered by the Android SDK terms. |

Tool lookup order: `ADB_PATH` / `SCRCPY_PATH` → the app's `tools` folder → `PATH` plus Homebrew and Android SDK locations.
scrcpy is always started with the same adb as the app (two different adb versions would keep killing each other's server).

## Use

```bash
android-mirror            # start in the background if needed and open the app window
android-mirror serve      # run in the foreground (logs to the terminal)
android-mirror status     # same as doctor: tools, server, paths
android-mirror stop
```

The app opens in its own window when Google Chrome, Chromium, Microsoft Edge, Brave or Vivaldi is installed
(it uses a separate profile, so it never mixes with your normal browsing). Switch to a normal browser tab under
**⋯ › Open in its own window**, or for one launch with `android-mirror open --browser` / `--window`.
Without one of those browsers it opens in your default browser, where Chrome/Edge's **Install app** also works.

All commands: `open`, `start`, `serve`, `stop`, `status`, `doctor`, `install-deps`, `desktop install|remove`, `uninstall`, `paths`.
Run `android-mirror <command> --help` for options.

### First-time phone setup

1. Settings › About phone › tap **Build number** 7× to enable Developer options.
2. Developer options › **Wireless debugging** → on → **Pair device with QR code**.
3. In the app: **Add device › QR code**, then scan it with the phone. After that, turning on Wireless debugging is enough.

Tip: Developer options › *Quick settings developer tiles* › **Wireless debugging** adds a one-tap toggle.

## Build from source

```bash
make dev        # .venv with an editable install + pytest, ruff, build
make run        # run from source (same as ./start.sh)
make check      # ruff + tests
make build      # wheel + sdist in dist/
make dist       # dist/android-mirror-<version>.tar.gz: wheel + install.sh (the release bundle)
```

CI (`.github/workflows/ci.yml`) runs lint and tests on Ubuntu and macOS with Python 3.9 and 3.13,
then builds the release bundle and installs and uninstalls it in a throwaway home directory.

### Layout

| Path | What |
| --- | --- |
| `src/android_mirror/cli.py` | `android-mirror` command line |
| `src/android_mirror/server.py` | aiohttp HTTP API, Server-Sent Events, static UI, localhost/CSRF guard |
| `src/android_mirror/manager.py` | devices, auto-reconnect watchdog, troubleshooter, scan, pairing, scrcpy sessions, dependency setup |
| `src/android_mirror/deps.py` | finding, checking and installing adb + scrcpy |
| `src/android_mirror/appwindow.py` | opening the UI in a dedicated app window |
| `src/android_mirror/integration.py` | app-menu entry, macOS app bundle, start at login |
| `src/android_mirror/about.py` | version, license and credits (read from installed package metadata) |
| `src/android_mirror/paths.py` | per-user data / log / tools locations |
| `src/android_mirror/sysinfo.py` | LAN interfaces and ARP table (Linux + macOS) |
| `src/android_mirror/mdns.py` | small mDNS browser for `_adb-tls-connect/_adb-tls-pairing/_adb._tcp` |
| `src/android_mirror/scanner.py` | TCP probes, subnet sweep, port search |
| `src/android_mirror/adb.py` | async adb wrappers |
| `src/android_mirror/store.py` | `devices.json` persistence |
| `src/android_mirror/web/` | web UI (plain HTML/CSS/JS, no build step) |
| `tests/` | pytest suite |
| `LICENSE` | MIT license |
| `install.sh`, `start.sh`, `Makefile` | installer, run-from-source script, developer tasks |
| `contrib/` | the original `scrcpy-connect.sh` / `scrcpy-menu.sh` shell helpers |

## Configuration

Environment variables: `PORT` (8765), `HOST` (127.0.0.1; `0.0.0.0` lets anyone on your network control adb on this
computer), `DATA_DIR`, `ADB_PATH`, `SCRCPY_PATH`, `APP_BROWSER` (the Chromium-based browser used for the app window). `serve` also takes `--port`, `--host`, `--data-dir`, `-v`.

## Notes

- **macOS**: the first network scan may trigger a *Local Network* permission prompt; allow it, or auto-discovery
  can't see phones. Mirror windows need the server to run in your login session, which the LaunchAgent and the app do.
- **Linux**: mirror windows need the server to run inside your desktop session (the autostart entry does this).
  USB connections may need Android udev rules (Debian/Ubuntu: `android-sdk-platform-tools-common`).
- Guest Wi-Fi and "AP/client isolation" block phone↔computer traffic, and so can a VPN on the phone.

## License and credits

[MIT](LICENSE) © 2026 Aaron Furtado.

Android Mirror is a control panel; the hard work is done by:

- [scrcpy](https://github.com/Genymobile/scrcpy) by Genymobile and Romain Vimont (Apache-2.0): screen mirroring and control
- [adb](https://developer.android.com/tools/adb), Android Open Source Project (Apache-2.0 source; Google's platform-tools
  downloads are under the Android SDK terms)
- [aiohttp](https://github.com/aio-libs/aiohttp) (Apache-2.0) and the aio-libs packages it uses: web server
- [segno](https://github.com/heuer/segno) (BSD-3-Clause): QR codes for pairing
- [Python](https://www.python.org) (PSF-2.0)

adb and scrcpy are separate programs that the app runs; they are not included in this package. The full, current list
with versions and licenses (including indirect dependencies) is in the app under **⋯ › About & credits**.
