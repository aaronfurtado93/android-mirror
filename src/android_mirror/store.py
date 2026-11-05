"""Remembered devices, persisted as JSON (atomic writes)."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import shutil
import time

DEFAULT_SCRCPY = {
    "preset": "balanced",
    "maxSize": 1920,
    "videoBitRate": 8,  # Mbps
    "maxFps": 60,
    "videoCodec": "h264",
    "audio": True,
    "stayAwake": True,
    "turnScreenOff": False,
    "showTouches": False,
    "alwaysOnTop": False,
    "borderless": False,
    "fullscreen": False,
    "keyboard": "sdk",
    "powerOffOnClose": False,
    "noControl": False,
    "preferUsb": True,
    "extraArgs": "",
}

DEFAULT_DEVICE = {"nickname": "", "autoConnect": True, "autoMirror": False, "keepAlive": True}


class Store:
    def __init__(self, directory: str):
        self.dir = directory
        self.file = os.path.join(directory, "devices.json")
        self.data = {"version": 1, "devices": {}, "settings": {}}
        self._save_handle = None

    def load(self):
        os.makedirs(self.dir, exist_ok=True)
        try:
            with open(self.file) as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                self.data = {"version": 1, "devices": {}, "settings": {}, **raw}
        except FileNotFoundError:
            pass
        except Exception:
            # Keep an unreadable file instead of silently losing remembered devices.
            shutil.copy(self.file, f"{self.file}.corrupt-{int(time.time())}")
        for k, d in list(self.data["devices"].items()):
            self.data["devices"][k] = self._normalise(d)

    @staticmethod
    def _normalise(d: dict) -> dict:
        return {**DEFAULT_DEVICE, **d, "scrcpy": {**DEFAULT_SCRCPY, **(d.get("scrcpy") or {})}}

    def list(self) -> list[dict]:
        return sorted(self.data["devices"].values(), key=lambda d: -(d.get("lastConnected") or 0))

    def get(self, id_: str) -> dict | None:
        return self.data["devices"].get(id_)

    def upsert(self, id_: str, patch: dict) -> dict:
        prev = self.data["devices"].get(id_) or {"id": id_, "addedAt": int(time.time() * 1000)}
        merged = {
            **prev,
            **{k: v for k, v in patch.items() if k != "scrcpy"},
            "id": id_,
            "scrcpy": {**(prev.get("scrcpy") or {}), **(patch.get("scrcpy") or {})},
        }
        self.data["devices"][id_] = self._normalise(merged)
        self.save()
        return self.data["devices"][id_]

    def remove(self, id_: str):
        self.data["devices"].pop(id_, None)
        self.save()

    @property
    def settings(self) -> dict:
        return self.data["settings"]

    def set_setting(self, k, v):
        self.data["settings"][k] = v
        self.save()

    def save(self):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return self.flush()
        if self._save_handle:
            self._save_handle.cancel()
        self._save_handle = loop.call_later(0.2, self.flush)

    def flush(self):
        if self._save_handle:
            self._save_handle.cancel()
            self._save_handle = None
        tmp = self.file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(copy.deepcopy(self.data), f, indent=2)
        os.replace(tmp, self.file)
