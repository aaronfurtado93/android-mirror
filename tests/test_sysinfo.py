import json

from android_mirror.sysinfo import parse_arp_an, parse_ifconfig, parse_ip_json, parse_proc_arp

MAC_IFCONFIG = """\
lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tether a4:83:e7:12:34:56
\tinet6 fe80::1c%en0 prefixlen 64 secured scopeid 0x6
\tinet 192.168.1.23 netmask 0xffffff00 broadcast 192.168.1.255
\tstatus: active
en1: flags=8822<BROADCAST,SMART,SIMPLEX,MULTICAST> mtu 1500
\tinet 10.9.9.9 netmask 0xffffff00 broadcast 10.9.9.255
utun3: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1380
\tinet 100.64.0.2 --> 100.64.0.2 netmask 0xffffffff
bridge100: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 192.168.2.1 netmask 0xffffff00 broadcast 192.168.2.255
"""

LINUX_IFCONFIG = """\
wlp2s0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500
        inet 192.168.29.10  netmask 255.255.255.0  broadcast 192.168.29.255
        ether 3c:22:fb:aa:bb:cc  txqueuelen 1000  (Ethernet)
docker0: flags=4099<UP,BROADCAST,MULTICAST>  mtu 1500
        inet 172.17.0.1  netmask 255.255.0.0  broadcast 172.17.255.255
"""


def test_ifconfig_macos():
    assert parse_ifconfig(MAC_IFCONFIG) == [{"name": "en0", "address": "192.168.1.23", "prefix": 24, "mac": "a4:83:e7:12:34:56"}]


def test_ifconfig_linux():
    assert parse_ifconfig(LINUX_IFCONFIG) == [{"name": "wlp2s0", "address": "192.168.29.10", "prefix": 24, "mac": "3c:22:fb:aa:bb:cc"}]


def test_ip_json():
    data = [
        {
            "ifname": "lo",
            "flags": ["LOOPBACK", "UP"],
            "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8, "scope": "host"}],
        },
        {
            "ifname": "wlp2s0",
            "flags": ["UP"],
            "address": "3c:22:fb:aa:bb:cc",
            "addr_info": [{"family": "inet", "local": "192.168.29.10", "prefixlen": 24, "scope": "global"}],
        },
        {
            "ifname": "tailscale0",
            "flags": ["UP"],
            "addr_info": [{"family": "inet", "local": "100.1.2.3", "prefixlen": 32, "scope": "global"}],
        },
    ]
    assert parse_ip_json(json.dumps(data)) == [{"name": "wlp2s0", "address": "192.168.29.10", "prefix": 24, "mac": "3c:22:fb:aa:bb:cc"}]


def test_arp_parsers():
    proc = (
        "IP address       HW type     Flags       HW address            Mask     Device\n"
        "192.168.29.25    0x1         0x2         aa:bb:cc:dd:ee:ff     *        wlp2s0\n"
        "192.168.29.30    0x1         0x0         00:00:00:00:00:00     *        wlp2s0\n"
    )
    assert parse_proc_arp(proc) == {"aa:bb:cc:dd:ee:ff": "192.168.29.25"}
    bsd = (
        "? (192.168.1.1) at a4:2b:b0:1:2:3 on en0 ifscope [ethernet]\n"
        "? (192.168.1.40) at (incomplete) on en0 ifscope [ethernet]\n"
        "? (224.0.0.251) at 1:0:5e:0:0:fb on en0 ifscope permanent [ethernet]\n"
    )
    assert parse_arp_an(bsd) == {"a4:2b:b0:01:02:03": "192.168.1.1", "01:00:5e:00:00:fb": "224.0.0.251"}
