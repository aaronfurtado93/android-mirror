import asyncio

from android_mirror.adb import serial_from_instance, transport_kind
from android_mirror.util import is_ipv4, parse_target, pool, same_subnet24, subnet_hosts


def test_parse_target():
    assert parse_target("192.168.1.5:5555") == ("192.168.1.5", 5555)
    assert parse_target(" 10.0.0.1:40447 ") == ("10.0.0.1", 40447)
    for bad in ("192.168.1.5", "300.1.1.1:5555", "1.2.3.4:0", "1.2.3.4:70000", "host:5555", None, ""):
        assert parse_target(bad) is None


def test_ipv4_and_subnets():
    assert is_ipv4("192.168.29.25") and not is_ipv4("192.168.29")
    assert same_subnet24("192.168.29.25", "192.168.29.1")
    assert not same_subnet24("192.168.29.25", "192.168.30.1")
    hosts = subnet_hosts({"address": "192.168.29.10", "prefix": 16})
    assert len(hosts) == 253 and "192.168.29.10" not in hosts  # capped to the local /24


def test_serial_and_transport_kind():
    assert serial_from_instance("adb-RZCX71X7VQW-vWgJpq") == "RZCX71X7VQW"
    assert serial_from_instance("adb-RZCX71X7VQW-vWgJpq._adb-tls-connect._tcp") == "RZCX71X7VQW"
    assert serial_from_instance("studio-abc") is None
    assert transport_kind("192.168.1.2:5555") == "wifi"
    assert transport_kind("adb-X-abcdef._adb-tls-connect._tcp") == "wifi"
    assert transport_kind("emulator-5554") == "emulator"
    assert transport_kind("RZCX71X7VQW") == "usb"


def test_pool_respects_stop():
    seen = []

    async def fn(i):
        seen.append(i)
        await asyncio.sleep(0)

    asyncio.run(pool(range(100), 4, fn, should_stop=lambda: len(seen) >= 10))
    assert 10 <= len(seen) < 20
