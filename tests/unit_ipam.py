"""Inventario de IP y VLAN sacado de configuraciones reales de cada formato."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.ipam import ips, vlans  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def ip_set(lst):
    return {(x["interface"], f"{x['ip']}/{x['prefix']}") for x in lst}


CISCO = """hostname R1
vlan 10
 name Datos
vlan 20
 name Voz
!
interface GigabitEthernet0/0
 description WAN
 ip address 203.0.113.2 255.255.255.252
!
interface Vlan10
 ip address 10.10.0.1 255.255.255.0
 ip address 10.10.1.1 255.255.255.0 secondary
!
interface Loopback0
 ip address 10.255.0.1 255.255.255.255
!
ip route 0.0.0.0 0.0.0.0 203.0.113.1
"""
check("Cisco: IP por interfaz, con la secundaria y la loopback", ip_set(ips(CISCO, "cisco_ios")) == {
    ("GigabitEthernet0/0", "203.0.113.2/30"), ("Vlan10", "10.10.0.1/24"), ("Vlan10", "10.10.1.1/24"), ("Loopback0", "10.255.0.1/32")},
    ips(CISCO, "cisco_ios"))
check("Cisco: VLAN con nombre", vlans(CISCO, "cisco_ios") == [{"id": 10, "name": "Datos"}, {"id": 20, "name": "Voz"}])

EDGE = """vlan database
vlan 10,20-21
vlan name 10 "Datos"
exit
network parms 10.0.0.16 255.255.255.0 10.0.0.1
configure
interface 0/1
description 'uplink'
exit
interface vlan 10
ip address 10.10.0.2 255.255.255.0
exit
"""
check("EdgeSwitch: IP de gestión y de la VLAN (bloques sin sangría)", ip_set(ips(EDGE, "ubiquiti_edgeswitch")) == {
    ("network parms", "10.0.0.16/24"), ("vlan 10", "10.10.0.2/24")}, ips(EDGE, "ubiquiti_edgeswitch"))
check("EdgeSwitch: VLAN en rango y su nombre", [v["id"] for v in vlans(EDGE, "ubiquiti_edgeswitch")] == [10, 20, 21]
      and vlans(EDGE, "ubiquiti_edgeswitch")[0]["name"] == "Datos")

MT = """/interface vlan add interface=bridge1 name=vlan20 vlan-id=20
/interface bridge vlan add bridge=bridge1 tagged=ether1 vlan-ids=30,40-41
/ip address add address=192.168.88.1/24 interface=bridge1 network=192.168.88.0
/ip address add address=10.20.0.1/24 interface=vlan20 network=10.20.0.0
"""
check("MikroTik: IP", ip_set(ips(MT, "mikrotik_routeros")) == {("bridge1", "192.168.88.1/24"), ("vlan20", "10.20.0.1/24")})
check("MikroTik: VLAN de interfaces y del bridge", [v["id"] for v in vlans(MT, "mikrotik_routeros")] == [20, 30, 40, 41])

JUNOS = """set interfaces ge-0/0/0 unit 0 family inet address 198.51.100.2/30
set interfaces irb unit 10 family inet address 10.10.0.1/24
set vlans DATOS vlan-id 10
set vlans DATOS l3-interface irb.10
"""
check("Junos: IP con su unidad", ip_set(ips(JUNOS, "juniper_junos")) == {("ge-0/0/0.0", "198.51.100.2/30"), ("irb.10", "10.10.0.1/24")})
check("Junos: VLAN", vlans(JUNOS, "juniper_junos") == [{"id": 10, "name": "DATOS"}])

HW = """vlan batch 10 20 to 22
#
interface Vlanif10
 ip address 10.10.0.1 255.255.255.0
#
"""
check("Huawei: IP y «vlan batch» con «to»", ip_set(ips(HW, "huawei")) == {("Vlanif10", "10.10.0.1/24")}
      and [v["id"] for v in vlans(HW, "huawei")] == [10, 20, 21, 22])

EXOS = """create vlan "Datos"
configure vlan Datos tag 20
configure vlan Datos ipaddress 10.20.0.1 255.255.255.0
"""
check("EXOS: IP y VLAN por su etiqueta", ip_set(ips(EXOS, "extreme_exos")) == {("vlan Datos", "10.20.0.1/24")}
      and vlans(EXOS, "extreme_exos") == [{"id": 20, "name": "Datos"}])

VOSS = """vlan create 10 name "Datos" type port-mstprstp 0
interface Vlan 10
ip address 10.10.0.1 255.255.255.0 0
exit
"""
check("VOSS: IP y VLAN", ip_set(ips(VOSS, "extreme_vsp")) == {("Vlan 10", "10.10.0.1/24")}
      and vlans(VOSS, "extreme_vsp") == [{"id": 10, "name": "Datos"}])

FG = """config system interface
    edit "port1"
        set ip 192.0.2.1 255.255.255.0
        set allowaccess ping https ssh
    next
    edit "lan"
        set ip 10.1.0.1 255.255.255.0
    next
end
"""
check("FortiGate: IP por interfaz", ip_set(ips(FG, "fortinet")) == {("port1", "192.0.2.1/24"), ("lan", "10.1.0.1/24")}, ips(FG, "fortinet"))

AIR = "netconf.1.devname=br0\nnetconf.1.ip=192.168.1.20\nnetconf.1.netmask=255.255.255.0\n"
check("airOS: IP de netconf", ip_set(ips(AIR, "ubiquiti_airos")) == {("br0", "192.168.1.20/24")})

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
