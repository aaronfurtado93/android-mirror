import asyncio
import os
import stat

import pytest

from android_mirror import deps

MANIFEST = b"""<?xml version="1.0"?>
<sdk:sdk-repository xmlns:sdk="http://schemas.android.com/sdk/android/repo/repository2/03">
  <remotePackage path="platform-tools">
    <revision><major>37</major><minor>0</minor><micro>2</micro></revision>
    <channelRef ref="channel-2"/>
    <archives><archive><complete><size>1</size><checksum type="sha1">beta</checksum><url>pt_r37.0.2-linux.zip</url></complete>
      <host-os>linux</host-os></archive></archives>
  </remotePackage>
  <remotePackage path="platform-tools">
    <revision><major>37</major><minor>0</minor><micro>1</micro></revision>
    <channelRef ref="channel-0"/>
    <archives>
      <archive><complete><size>9</size><checksum type="sha1">aaa</checksum><url>pt_r37.0.1-linux.zip</url></complete><host-os>linux</host-os></archive>
      <archive><complete><size>16</size><checksum type="sha1">bbb</checksum><url>pt_r37.0.1-darwin.zip</url></complete><host-os>macosx</host-os></archive>
    </archives>
  </remotePackage>
</sdk:sdk-repository>"""


def test_platform_tools_picks_latest_stable():
    a = deps.platform_tools_archive(MANIFEST, "linux")
    assert a["url"].endswith("pt_r37.0.1-linux.zip") and a["sha1"] == "aaa" and a["version"] == (37, 0, 1)
    assert deps.platform_tools_archive(MANIFEST, "macosx")["sha1"] == "bbb"


RELEASE = {
    "tag_name": "v5.0.1",
    "assets": [
        {"name": "scrcpy-linux-x86_64-v5.0.1.tar.gz", "digest": "sha256:aa"},
        {"name": "scrcpy-macos-aarch64-v5.0.1.tar.gz", "digest": "sha256:bb"},
        {"name": "scrcpy-macos-x86_64-v5.0.1.tar.gz", "digest": "sha256:cc"},
        {"name": "scrcpy-server-v5.0.1"},
    ],
}


@pytest.mark.parametrize(
    "is_mac,machine,expected",
    [
        (False, "x86_64", "scrcpy-linux-x86_64-v5.0.1.tar.gz"),
        (True, "arm64", "scrcpy-macos-aarch64-v5.0.1.tar.gz"),
        (True, "x86_64", "scrcpy-macos-x86_64-v5.0.1.tar.gz"),
    ],
)
def test_scrcpy_asset(monkeypatch, is_mac, machine, expected):
    monkeypatch.setattr(deps, "IS_MAC", is_mac)
    monkeypatch.setattr(deps.platform, "machine", lambda: machine)
    a = deps.scrcpy_asset(RELEASE)
    assert a["name"] == expected and deps._sha256_for(RELEASE, a)


def test_no_linux_arm_build(monkeypatch):
    monkeypatch.setattr(deps, "IS_MAC", False)
    monkeypatch.setattr(deps.platform, "machine", lambda: "aarch64")
    with pytest.raises(RuntimeError):
        deps.scrcpy_asset(RELEASE)


def test_parse_version():
    assert deps.parse_version("3.3.4") == (3, 3, 4)
    assert deps.parse_version("2.4") >= deps.MIN_SCRCPY > deps.parse_version("1.25")


def _exe(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_find_prefers_env_then_portable(monkeypatch, tmp_path):
    portable = _exe(deps.portable_path("adb"))
    monkeypatch.delenv("ADB_PATH", raising=False)
    assert deps.find("adb") == (str(portable), "portable")
    custom = _exe(tmp_path / "custom" / "adb")
    monkeypatch.setenv("ADB_PATH", str(custom))
    assert deps.find("adb") == (str(custom), "env")
    monkeypatch.setenv("ADB_PATH", str(tmp_path / "nope"))
    assert deps.find("adb")[0] is None


def test_child_env_points_scrcpy_at_our_adb(monkeypatch):
    portable = _exe(deps.portable_path("adb"))
    monkeypatch.delenv("ADB_PATH", raising=False)
    deps.refresh_paths()
    env = deps.child_env()
    assert env["ADB"] == str(portable)
    assert "/opt/homebrew/bin" in env["PATH"].split(os.pathsep)


def test_methods_recommend_something(monkeypatch):
    methods = asyncio.run(deps.install_methods(["adb", "scrcpy"]))
    ids = [m["id"] for m in methods]
    if deps.portable_supported():
        assert "portable" in ids
    assert sum(1 for m in methods if m.get("recommended")) <= 1
