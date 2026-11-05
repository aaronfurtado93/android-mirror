import stat

from android_mirror import appwindow, cli


def test_window_command_uses_own_profile_and_class(isolated_dirs):
    cmd = appwindow.window_command("/usr/bin/chromium", "http://localhost:8765")
    assert cmd[0] == "/usr/bin/chromium" and "--app=http://localhost:8765" in cmd
    assert f"--user-data-dir={isolated_dirs / 'data' / 'window-profile'}" in cmd
    assert "--class=android-mirror" in cmd and "--no-first-run" in cmd


def test_app_browser_override(monkeypatch, tmp_path):
    exe = tmp_path / "my-chrome"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("APP_BROWSER", str(exe))
    assert appwindow.find_browser() == (str(exe), "my-chrome")
    monkeypatch.setenv("APP_BROWSER", str(tmp_path / "missing"))
    assert appwindow.find_browser() is None


def test_open_ui_falls_back_to_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(appwindow, "find_browser", lambda: None)
    monkeypatch.setattr(appwindow.webbrowser, "open", lambda url: opened.append(url) or True)
    assert appwindow.open_ui("http://localhost:1", "window") == "browser" and opened == ["http://localhost:1"]


def test_open_mode_flag_wins():
    assert cli.open_mode(1, "browser") == "browser"
    assert cli.open_mode(1) == "window"  # nothing listening on port 1 -> default
