from android_mirror.manager import Manager
from android_mirror.store import DEFAULT_SCRCPY


def args(**over):
    return Manager._scrcpy_args({**DEFAULT_SCRCPY, **over}, "SER", "Phone", "wifi")


def test_defaults():
    a = args()
    assert a[:2] == ["-s", "SER"]
    assert "--max-size=1920" in a and "--video-bit-rate=8M" in a and "--max-fps=60" in a
    assert "--stay-awake" in a and "--no-audio" not in a


def test_view_only_drops_control_flags():
    a = args(noControl=True, turnScreenOff=True, keyboard="uhid")
    assert "--no-control" in a and "--turn-screen-off" not in a and "--keyboard=uhid" not in a


def test_aoa_only_over_usb_and_extra_args():
    assert "--keyboard=aoa" not in args(keyboard="aoa")
    a = Manager._scrcpy_args({**DEFAULT_SCRCPY, "keyboard": "aoa", "extraArgs": "--crop '100:100:0:0' --no-mipmaps"}, "S", "T", "usb")
    assert "--keyboard=aoa" in a and a[-3:] == ["--crop", "100:100:0:0", "--no-mipmaps"]
