import pytest

from android_mirror import cli, integration, paths


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0 and "android-mirror" in capsys.readouterr().out


def test_paths_honour_data_dir(isolated_dirs, capsys):
    assert paths.data_dir() == isolated_dirs / "data"
    with pytest.raises(SystemExit):
        cli.main(["paths"])
    assert str(isolated_dirs / "data") in capsys.readouterr().out


def test_desktop_entry_quoting():
    assert integration._desktop_exec(["/opt/my app/bin/android-mirror", "open"]) == '"/opt/my app/bin/android-mirror" open'
    assert integration._desktop_exec(["/usr/bin/x", "serve", "--no-browser"]) == "/usr/bin/x serve --no-browser"
