from android_mirror import about


def test_libraries_from_metadata():
    libs = {lib["name"].lower(): lib for lib in about.libraries()}
    assert libs["aiohttp"]["direct"] and libs["segno"]["direct"]
    assert "Apache-2.0" in libs["aiohttp"]["license"]
    assert libs["segno"]["license"] != "see project"
    assert any(not lib["direct"] for lib in libs.values())  # indirect deps (multidict, yarl, …)
    assert "android-mirror" not in libs


def test_info_includes_tools_and_license():
    i = about.info({"scrcpy": {"version": "3.3.4", "path": "/usr/bin/scrcpy"}, "platform": "Test OS"})
    assert i["license"] == "MIT" and "Permission is hereby granted" in i["licenseText"]
    sc = next(t for t in i["tools"] if t["id"] == "scrcpy")
    assert sc["version"] == "3.3.4" and sc["license"] == "Apache-2.0"
    assert i["platform"] == "Test OS"


def test_requirement_name():
    assert about._requirement_name("aiohttp>=3.9,<4") == "aiohttp"
    assert about._requirement_name('async-timeout<6.0,>=4.0; python_version < "3.11"') == "async-timeout"
    assert about._requirement_name('pytest>=7; extra == "dev"') is None
