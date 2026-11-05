"""Buscar una MAC o una IP: formatos de MAC, tablas MAC y ARP de varias plataformas."""
import sys

sys.path.insert(0, ".")
from app.macfind import classify, norm_mac, parse_arp, parse_mac, show_mac  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


check("MAC en los tres formatos", {norm_mac(x) for x in ("f4:39:09:1a:2b:3c", "f439.091a.2b3c", "F4-39-09-1A-2B-3C")} == {"F439091A2B3C"})
check("se muestra con dos puntos", show_mac("F439091A2B3C") == "F4:39:09:1A:2B:3C")
check("IP, MAC, incompleta y texto", [classify(x)[0] for x in ("10.10.9.200", "f439.091a.2b3c", "f4:39:09:1a", "10.10.9", "R-CORE", "show vlan")]
      == ["ip", "mac", "bad", "bad", "", ""], [classify(x) for x in ("10.10.9.200", "f439.091a.2b3c", "f4:39:09:1a", "10.10.9", "R-CORE", "show vlan")])

IOS = """          Mac Address Table
-------------------------------------------

Vlan    Mac Address       Type        Ports
----    -----------       --------    -----
 All    0100.0ccc.cccc    STATIC      CPU
  10    f439.091a.2b3c    DYNAMIC     Gi1/0/17
  20    0050.56aa.bbcc    DYNAMIC     Po1
Total Mac Addresses for this criterion: 3
"""
check("Cisco IOS: tabla MAC (sin la CPU)", parse_mac(IOS) == [("F439091A2B3C", "10", "Gi1/0/17"), ("005056AABBCC", "20", "Po1")], parse_mac(IOS))
ARP = """Protocol  Address          Age (min)  Hardware Addr   Type   Interface
Internet  10.10.9.200            12   f439.091a.2b3c  ARPA   Vlan10
Internet  10.10.9.7               0   Incomplete      ARPA
"""
check("Cisco IOS: tabla ARP", parse_arp(ARP) == [("10.10.9.200", "F439091A2B3C", "Vlan10")], parse_arp(ARP))
NX = """Legend:
        * - primary entry, G - Gateway MAC, (R) - Routed MAC, O - Overlay MAC
   VLAN     MAC Address      Type      age     Secure NTFY Ports
---------+-----------------+--------+---------+------+----+------------------
*   10     f439.091a.2b3c   dynamic  0         F      F    Eth1/12
"""
check("NX-OS: tabla MAC", parse_mac(NX) == [("F439091A2B3C", "10", "Eth1/12")], parse_mac(NX))
ROS = """Flags: X - disabled, I - invalid, D - dynamic, L - local, E - external
 0   D   mac-address=F4:39:09:1A:2B:3C vid=10 on-interface=ether5 bridge=bridge
 1  DL   mac-address=48:A9:8A:52:94:88 on-interface=bridge bridge=bridge
"""
check("MikroTik: hosts del bridge (sin los propios)", parse_mac(ROS) == [("F439091A2B3C", "10", "ether5")], parse_mac(ROS))
ROSARP = """Flags: X - disabled, I - invalid, H - DHCP, D - dynamic, P - published, C - complete
 0 DC address=10.10.9.200 mac-address=F4:39:09:1A:2B:3C interface=bridge published=no
"""
check("MikroTik: tabla ARP", parse_arp(ROSARP) == [("10.10.9.200", "F439091A2B3C", "bridge")], parse_arp(ROSARP))
HW = """-------------------------------------------------------------------------------
MAC Address    VLAN/VSI/BD                       Learned-From        Type
-------------------------------------------------------------------------------
f439-091a-2b3c 10/-/-                            GE0/0/3             dynamic
"""
check("Huawei: tabla MAC", parse_mac(HW) == [("F439091A2B3C", "10", "GE0/0/3")], parse_mac(HW))
LNX = "10.10.9.200 dev eth0 lladdr f4:39:09:1a:2b:3c REACHABLE\n10.10.9.9 dev eth0 FAILED\n"
check("Linux: ip neigh", parse_arp(LNX) == [("10.10.9.200", "F439091A2B3C", "eth0")], parse_arp(LNX))

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
