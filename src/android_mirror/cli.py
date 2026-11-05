"""Command line: `android-mirror [open|start|serve|stop|status|doctor|install-deps|desktop|uninstall|paths]`."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import APP_NAME, __version__, appwindow, deps, integration, paths

DEFAULT_PORT = int(os.environ.get("PORT", "8765"))
DEFAULT_HOST = os.environ.get("HOST", "127.0.0.1")


# ------------------------------------------------------------------ helpers


def _url(port: int) -> str:
    return f"http://localhost:{port}"


def running_version(port: int) -> str | None:
    """Version of the Android Mirror server answering on this port, if any."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=1.5) as r:
            data = json.loads(r.read())
        return data.get("version") if data.get("name") == "android-mirror" else None
    except Exception:
        return None


def _read_pid() -> int | None:
    try:
        pid = int(paths.pid_file().read_text().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def open_mode(port: int, override: str | None = None) -> str:
    """'window' or 'browser': the --window/--browser flag, else the setting saved in the app."""
    if override:
        return override
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/app-settings", timeout=1.5) as r:
            mode = json.loads(r.read()).get("openMode")
        return mode if mode in appwindow.MODES else "window"
    except Exception:
        return "window"


def _open_ui(port: int, override: str | None = None):
    url = _url(port)
    if appwindow.open_ui(url, open_mode(port, override)) == "none":
        print(f"Open {url} in your browser.")


def _confirm(question: str, default: bool = True, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        return default
    ans = input(f"{question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    return default if not ans else ans.startswith("y")


def setup_logging(verbose: bool, to_file: bool):
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)-7s %(message)s", "%H:%M:%S"))
    root.addHandler(console)
    if to_file:
        paths.log_dir().mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(paths.log_file(), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        root.addHandler(fh)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def start_background(port: int, wait: float = 12) -> bool:
    """Make sure a server is running (detached from this terminal). True when it answers."""
    if running_version(port):
        return True
    if not integration.mac_kickstart():
        paths.log_dir().mkdir(parents=True, exist_ok=True)
        with open(os.devnull, "rb") as devnull, open(paths.log_dir() / "background.log", "ab") as out:
            subprocess.Popen(
                [*integration.command(), "serve", "--no-browser", "--port", str(port)],
                stdin=devnull,
                stdout=out,
                stderr=out,
                start_new_session=True,
                close_fds=True,
            )
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if running_version(port):
            return True
        time.sleep(0.25)
    return False


# ------------------------------------------------------------------ commands


def cmd_serve(a) -> int:
    from .server import Config, run

    setup_logging(a.verbose, to_file=True)
    other = running_version(a.port)
    if other:
        print(f"{APP_NAME} {other} is already running at {_url(a.port)}")
        if not a.no_browser:
            _open_ui(a.port)
        return 0
    cfg = Config(host=a.host, port=a.port, data_dir=Path(a.data_dir).expanduser() if a.data_dir else paths.data_dir())
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    if not a.data_dir:
        paths.pid_file().write_text(str(os.getpid()))
    if not a.no_browser:
        import threading

        def opener():
            for _ in range(60):
                if running_version(a.port):
                    _open_ui(a.port)
                    return
                time.sleep(0.25)

        threading.Thread(target=opener, daemon=True).start()
    try:
        run(cfg)
    finally:
        if not a.data_dir and _read_pid() == os.getpid():
            paths.pid_file().unlink(missing_ok=True)
    return 0


def cmd_open(a) -> int:
    if not start_background(a.port):
        print(f"The server did not start. See {paths.log_dir()}", file=sys.stderr)
        return 1
    _open_ui(a.port, a.mode)
    return 0


def cmd_start(a) -> int:
    ok = start_background(a.port)
    print(f"{APP_NAME} is running at {_url(a.port)}" if ok else f"The server did not start. See {paths.log_dir()}")
    return 0 if ok else 1


def cmd_stop(a) -> int:
    pid = _read_pid()
    if paths.IS_MAC and integration.MAC_AGENT.exists():
        integration._mac_agent_unload()  # otherwise launchd restarts it; it loads again at next login
    if not pid:
        print("Not running." if not running_version(a.port) else f"A server is answering on port {a.port} but its PID is unknown.")
        return 0
    os.kill(pid, signal.SIGTERM)
    for _ in range(40):
        if not _read_pid():
            break
        time.sleep(0.1)
    print("Stopped.")
    return 0


def _print_status(st: dict):
    def line(name, info):
        if info["ok"]:
            mark, detail = "✓", f"{info['version']}  ({info['path']}, {info['source']})"
        elif info.get("outdated"):
            mark, detail = "!", f"{info['version']} is too old, needs {info['minimum']}+  ({info['path']})"
        else:
            mark, detail = "✗", "not found"
        print(f"  {mark} {name:<7} {detail}")

    print(f"{APP_NAME} {__version__} on {st['platform']}")
    line("adb", st["adb"])
    line("scrcpy", st["scrcpy"])
    if not st["display"]:
        print("  ! no desktop session (DISPLAY/WAYLAND_DISPLAY unset): mirror windows cannot open from here")


def cmd_doctor(a) -> int:
    st = asyncio.run(deps.probe())
    if a.json:
        print(json.dumps(st, indent=2))
        return 0 if st["ready"] else 1
    _print_status(st)
    v = running_version(a.port)
    print(f"  {'✓' if v else '·'} server  {'running at ' + _url(a.port) if v else 'not running'}")
    print(f"\n  data   {paths.data_dir()}\n  logs   {paths.log_dir()}")
    if st["missing"]:
        print("\nTo install what's missing:")
        for m in st["methods"]:
            if not m["covers"]:
                print(f"  • {m['label']}: {m['note']}")
                continue
            rec = " (recommended)" if m.get("recommended") else ""
            print(f"  • {m['label']}{rec}: {m['terminal']}")
            if m.get("note"):
                print(f"      {m['note']}")
        print("\nOr run: android-mirror install-deps")
    return 0 if st["ready"] else 1


def cmd_install_deps(a) -> int:
    st = asyncio.run(deps.probe())
    _print_status(st)
    if st["ready"] and not a.force:
        print("\nEverything is installed.")
        return 0
    missing = st["missing"] or ["adb", "scrcpy"]
    methods = [m for m in asyncio.run(deps.install_methods(missing)) if m["covers"]]
    if not methods:
        print("\nNo automatic install method for this system. Install adb (android-tools) and scrcpy with your package manager.")
        return 1
    if a.method:
        m = next((m for m in methods if m["id"] == a.method), None)
        if not m:
            print(f"Method '{a.method}' is not available here. Options: {', '.join(x['id'] for x in methods)}")
            return 2
    elif a.yes or not sys.stdin.isatty():
        m = next((x for x in methods if x.get("recommended")), methods[0])
    else:
        print("\nHow should the missing tools be installed?")
        for i, x in enumerate(methods, 1):
            rec = " (recommended)" if x.get("recommended") else ""
            print(f"  {i}) {x['label']}{rec}: installs {', '.join(x['covers'])}\n     {x['description']}")
            if x.get("note"):
                print(f"     {x['note']}")
        default = next((i for i, x in enumerate(methods, 1) if x.get("recommended")), 1)
        choice = input(f"Choose [1-{len(methods)}, default {default}, q to skip]: ").strip().lower()
        if choice == "q":
            return 1
        m = methods[int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(methods) else default - 1]

    def log(level, msg):
        if level != "debug" or a.verbose:
            print(("  " if level == "debug" else "") + msg, flush=True)

    if m["id"] == "system":
        # In a terminal use sudo directly (the UI uses pkexec instead).
        argv = shlex.split(m["terminal"])
        print(f"$ {m['terminal']}")
        code = subprocess.call(argv)
        ok = code == 0
        print("Done." if ok else f"Failed (exit code {code}).")
    else:
        r = asyncio.run(deps.install(m["id"], m["covers"], log))
        ok = r["ok"]
        print(r["message"])
    final = asyncio.run(deps.probe())
    print()
    _print_status(final)
    if final["missing"]:
        print("\nStill missing: " + ", ".join(final["missing"]) + ". Run `android-mirror install-deps` again to pick another method.")
    return 0 if ok and final["ready"] else 1


def cmd_desktop(a) -> int:
    if a.action == "remove":
        removed = integration.remove()
        print("\n".join(f"Removed {p}" for p in removed) or "Nothing to remove.")
        return 0
    for line in integration.install(autostart=not a.no_autostart):
        print(f"  {line}")
    return 0


def cmd_uninstall(a) -> int:
    venv = paths.venv_dir()
    managed = Path(sys.prefix).resolve() == venv.resolve()
    print(f"This removes {APP_NAME}'s app-menu entry, login item" + (f", command and program files ({venv})" if managed else "") + ".")
    if a.purge:
        print(f"--purge also deletes remembered devices, downloaded tools and logs:\n  {paths.data_dir()}\n  {paths.log_dir()}")
    if not _confirm("Continue?", default=False, assume_yes=a.yes):
        return 1
    cmd_stop(argparse.Namespace(port=a.port))
    for p in integration.remove():
        print(f"Removed {p}")
    link = paths.bin_dir() / "android-mirror"
    if link.is_symlink() and (not managed or str(venv) in os.path.realpath(link)):
        link.unlink()
        print(f"Removed {link}")
    if managed:
        shutil.rmtree(venv, ignore_errors=True)
        print(f"Removed {venv}")
    if a.purge:
        for d in (paths.data_dir(), paths.log_dir()):
            shutil.rmtree(d, ignore_errors=True)
            print(f"Removed {d}")
    elif not managed:
        print("Installed with pip/pipx? Remove the package with: pipx uninstall android-mirror")
    print("Uninstalled." + ("" if a.purge else f" Your remembered devices are kept in {paths.data_dir()}."))
    return 0


def cmd_paths(a) -> int:
    for k, v in {
        "data": paths.data_dir(),
        "logs": paths.log_dir(),
        "tools": paths.tools_dir(),
        "venv": paths.venv_dir(),
        "web": integration.WEB,
        "python": sys.executable,
    }.items():
        print(f"{k:<7}{v}")
    return 0


# ------------------------------------------------------------------ parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="android-mirror", description=f"{APP_NAME}: wireless Android screen mirroring (adb + scrcpy) from your browser."
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND")

    def add(name, fn, help_, port=True):
        sp = sub.add_parser(name, help=help_, description=help_)
        sp.set_defaults(fn=fn)
        if port:
            sp.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"HTTP port (default {DEFAULT_PORT}, env PORT)")
        return sp

    o = add("open", cmd_open, "start the server in the background if needed and open the app window (default)")
    o.add_argument("--window", dest="mode", action="store_const", const="window", help="open in a dedicated app window")
    o.add_argument("--browser", dest="mode", action="store_const", const="browser", help="open in a normal browser tab")
    add("start", cmd_start, "start the server in the background")
    s = add("serve", cmd_serve, "run the server in the foreground")
    s.add_argument("--host", default=DEFAULT_HOST, help="bind address (default 127.0.0.1; 0.0.0.0 exposes adb control to your network)")
    s.add_argument("--data-dir", help=f"where remembered devices are stored (default {paths.data_dir()})")
    s.add_argument("--no-browser", action="store_true", help="don't open the web UI")
    s.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    add("stop", cmd_stop, "stop the background server")
    d = add("doctor", cmd_doctor, "check adb, scrcpy and the desktop session; show how to fix problems")
    d.add_argument("--json", action="store_true")
    st = add("status", cmd_doctor, "same as doctor")
    st.add_argument("--json", action="store_true")
    i = add("install-deps", cmd_install_deps, "install adb and/or scrcpy", port=False)
    i.add_argument("--method", choices=["brew", "system", "portable"], help="skip the menu")
    i.add_argument("-y", "--yes", action="store_true", help="use the recommended method without asking")
    i.add_argument("--force", action="store_true", help="reinstall even if everything is present")
    i.add_argument("-v", "--verbose", action="store_true", help="show installer output")
    dk = add("desktop", cmd_desktop, "add or remove the app-menu entry and start-at-login item", port=False)
    dk.add_argument("action", choices=["install", "remove"], nargs="?", default="install")
    dk.add_argument("--no-autostart", action="store_true", help="don't start the server at login")
    u = add("uninstall", cmd_uninstall, "remove the app (keeps remembered devices unless --purge)")
    u.add_argument("--purge", action="store_true", help="also delete remembered devices, downloaded tools and logs")
    u.add_argument("-y", "--yes", action="store_true")
    add("paths", cmd_paths, "print where data, logs and tools are stored", port=False)
    return p


def main(argv: list[str] | None = None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        args = parser.parse_args(["open", *(argv if argv is not None else sys.argv[1:])])
    try:
        sys.exit(args.fn(args))
    except KeyboardInterrupt:
        sys.exit(130)
