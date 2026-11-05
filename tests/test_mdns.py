import socket
import struct

from android_mirror.mdns import T_A, T_PTR, T_SRV, MdnsBrowser, _encode_name, build_query, parse_packet

INSTANCE = "adb-RZCX71X7VQW-vWgJpq"
SERVICE = "_adb-tls-connect._tcp.local"


def _rr(name, rtype, ttl, rdata):
    return _encode_name(name) + struct.pack("!HHIH", rtype, 1, ttl, len(rdata)) + rdata


def response(port=40447, ip="192.168.29.25", ttl=120):
    full = f"{INSTANCE}.{SERVICE}"
    records = [
        _rr(SERVICE, T_PTR, ttl, _encode_name(full)),
        _rr(full, T_SRV, ttl, struct.pack("!HHH", 0, 0, port) + _encode_name("Android.local")),
        _rr("Android.local", T_A, ttl, socket.inet_aton(ip)),
    ]
    return struct.pack("!HHHHHH", 0, 0x8400, 0, len(records), 0, 0) + b"".join(records)


def test_query_is_not_parsed_as_response():
    assert parse_packet(build_query([SERVICE])) == []


def test_parse_and_track_service():
    recs = parse_packet(response())
    assert {r["type"] for r in recs} == {T_PTR, T_SRV, T_A}

    b = MdnsBrowser()
    events = []
    b.on("up", lambda s: events.append(("up", s)))
    b.on("down", lambda s: events.append(("down", s)))
    b._on_message(response(), ("192.168.29.25", 5353))
    (svc,) = b.list("connect")
    assert (svc["serial"], svc["target"], svc["kind"]) == ("RZCX71X7VQW", "192.168.29.25:40447", "connect")
    assert b.find_by_serial("RZCX71X7VQW")[0]["port"] == 40447

    b._on_message(response(port=41000), ("192.168.29.25", 5353))  # port changed -> "up" again
    b._on_message(response(port=41000, ttl=0), ("192.168.29.25", 5353))  # goodbye
    assert [e[0] for e in events] == ["up", "up", "down"]
    assert b.list() == []
