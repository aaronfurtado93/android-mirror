import json

from android_mirror.store import DEFAULT_DEVICE, DEFAULT_SCRCPY, Store


def test_upsert_merges_and_persists(tmp_path):
    s = Store(str(tmp_path))
    s.load()
    s.upsert("SER1", {"model": "Pixel", "scrcpy": {"maxFps": 30}})
    s.upsert("SER1", {"nickname": "Work phone", "scrcpy": {"audio": False}})
    s.flush()
    d = json.loads((tmp_path / "devices.json").read_text())["devices"]["SER1"]
    assert d["nickname"] == "Work phone" and d["model"] == "Pixel"
    assert d["scrcpy"]["maxFps"] == 30 and d["scrcpy"]["audio"] is False
    assert d["scrcpy"]["maxSize"] == DEFAULT_SCRCPY["maxSize"]
    assert d["autoConnect"] == DEFAULT_DEVICE["autoConnect"]


def test_corrupt_file_is_kept(tmp_path):
    (tmp_path / "devices.json").write_text("{not json")
    s = Store(str(tmp_path))
    s.load()
    assert s.list() == []
    assert any(p.name.startswith("devices.json.corrupt-") for p in tmp_path.iterdir())
