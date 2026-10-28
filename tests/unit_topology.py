"""Vecinos LLDP/CDP leídos de la salida real de cada plataforma (mapa de la red)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.topology import MARK, norm_name, parse, short_port  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


CDP = """
-------------------------
Device ID: SW-ACCESO-2.empresa.local
Entry address(es):
  IP address: 10.0.0.12
Platform: cisco WS-C2960X-48FPD-L,  Capabilities: Switch IGMP
Interface: GigabitEthernet0/1,  Port ID (outgoing port): GigabitEthernet1/0/48
Holdtime : 150 sec
-------------------------
Device ID: AP-Sala
Entry address(es):
  IP address: 10.0.0.40
Platform: cisco AIR-AP2802I-E-K9,  Capabilities: Trans-Bridge
Interface: GigabitEthernet0/3,  Port ID (outgoing port): GigabitEthernet0
"""
LLDP_CISCO = """
------------------------------------------------
Local Intf: Gi0/1
Chassis id: 7483.c2aa.bb01
Port id: Gi1/0/48
System Name: SW-ACCESO-2.empresa.local
------------------------------------------------
Local Intf: Gi0/2
Chassis id: 7483.c2aa.bb01
Port id: 0/5
Port Description: Port 5
System Name: EdgeSW16

System Description:
EdgeSwitch 16-Port 150W, 1.9.3.5384843, Linux 3.6.5

Management Addresses:
    IP: 10.0.0.16
"""
n = parse(f"{MARK}show cdp neighbors detail\n{CDP}{MARK}show lldp neighbors detail\n{LLDP_CISCO}", "cisco_ios")
by = {x["remote_name"]: x for x in n}
check("Cisco: CDP y LLDP juntos, sin duplicar el mismo enlace", len(n) == 3, n)
sw = by.get("SW-ACCESO-2.empresa.local", {})
check("Cisco CDP: puerto local, puerto remoto, IP y modelo",
      sw.get("local_port") == "GigabitEthernet0/1" and sw.get("remote_port") == "GigabitEthernet1/0/48"
      and sw.get("remote_ip") == "10.0.0.12" and "WS-C2960X" in sw.get("platform", ""), sw)
check("Cisco LLDP: un EdgeSwitch con su IP de gestión", by.get("EdgeSW16", {}).get("remote_ip") == "10.0.0.16"
      and by["EdgeSW16"]["remote_port"] == "0/5" and by["EdgeSW16"]["local_port"] == "Gi0/2", by.get("EdgeSW16"))

MT = """Flags: X - disabled
 0 interface=ether2,bridge address=10.0.0.20 address4=10.0.0.20 mac-address=48:A9:8A:00:00:02 identity=SW-Pasillo platform=MikroTik version=7.16 (stable) interface-name=ether1 board=CRS326-24G-2S+ ipv6=no
 1 interface=ether1 address=10.0.0.1 mac-address=00:0C:29:00:00:01 identity="Core 1" platform=Cisco interface-name=Gi0/24
"""
m = parse(MARK + "/ip neighbor print detail terse without-paging\n" + MT, "mikrotik_routeros")
check("MikroTik: vecinos de /ip neighbor (identidad, IP, puertos, placa)",
      len(m) == 2 and m[0]["remote_name"] == "SW-Pasillo" and m[0]["local_port"] == "ether2" and m[0]["remote_port"] == "ether1"
      and m[0]["remote_ip"] == "10.0.0.20" and "CRS326" in m[0]["platform"] and m[1]["remote_name"] == "Core 1", m)

HW = """Local Intf       Neighbor Dev             Neighbor Intf             Exptime(s)
GE0/0/1          SW-Core                  GE0/0/24                  101
GE0/0/2          AR-Borde                 GE0/0/0                   99
"""
h = parse(MARK + "display lldp neighbor brief\n" + HW, "huawei")
check("Huawei: tabla «brief»", [(x["local_port"], x["remote_name"], x["remote_port"]) for x in h]
      == [("GE0/0/1", "SW-Core", "GE0/0/24"), ("GE0/0/2", "AR-Borde", "GE0/0/0")], h)

ES = """       LLDP Remote Device Summary

Local
Interface RemID   Chassis ID          Port ID           System Name
--------- ------- ------------------- ----------------- -----------------
0/1       1       74:83:C2:00:00:01   0/24              SW-Core
"""
e = parse(MARK + "show lldp remote-device all\n" + ES, "ubiquiti_edgeswitch")
check("EdgeSwitch: tabla de vecinos", e and e[0]["local_port"] == "0/1" and e[0]["remote_name"] == "SW-Core"
      and e[0]["remote_port"] == "0/24", e)

EXOS = """-----------------------------------------------------------------------------
LLDP Port 1 detected 1 neighbor
  Neighbor: 10.0.0.30/00:04:96:aa:bb:cc, age 26 seconds
    - Chassis ID type: MAC address (4); Chassis ID: 00:04:96:aa:bb:cc
      Port ID type: ifName (5); Port ID: "1:24"
    - Time To Live: 120 seconds
    - System Name: "SW-EXOS-2"
    - System Description: "ExtremeXOS (X460-G2-24p-10G4) version 31.7.1.4"
    - Management Address Subtype: IPv4 (1)
      Management Address        : 10.0.0.30
"""
x = parse(MARK + "show lldp neighbors detailed\n" + EXOS, "extreme_exos")
check("EXOS: «LLDP Port 1 detected», nombre, puerto «1:24» e IP", x and x[0]["local_port"] == "1" and x[0]["remote_name"] == "SW-EXOS-2"
      and x[0]["remote_port"] == "1:24" and x[0]["remote_ip"] == "10.0.0.30", x)

JN = """Local Interface    Parent Interface    Chassis Id          Port info          System Name
ge-0/0/0           -                   00:05:86:aa:bb:cc   ge-0/0/1           SRX-Borde
"""
j = parse(MARK + "show lldp neighbors\n" + JN, "juniper_junos")
check("Junos: tabla de vecinos", j and j[0]["local_port"] == "ge-0/0/0" and j[0]["remote_name"] == "SRX-Borde"
      and j[0]["remote_port"] == "ge-0/0/1", j)

check("orden no admitida: sin vecinos (y sin error)", parse(MARK + "show lldp neighbors detail\n% Invalid input detected at '^' marker.\n", "cisco_ios") == [])
check("nombres comparables sin dominio", norm_name("SW-2.empresa.local") == "sw-2" == norm_name("sw-2"))
check("puertos CDP y LLDP equivalentes", short_port("GigabitEthernet0/1") == short_port("Gi0/1"))

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
