"""Pruebas sin red: inventario y reversión de Broadcom FastPath (Ubiquiti EdgeSwitch)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.drivers import FASTPATH, clean_config, get_profile, parse_inventory  # noqa: E402
from app.rollback import build_rollback  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


VERSION = """
Switch: 1

System Description............................. EdgeSwitch 16-Port 150W, 1.9.3.5263085, Linux 3.6.5-03de2dbe
Machine Type................................... EdgeSwitch 16-Port 150W
Machine Model.................................. ES-16-150W
Serial Number.................................. 7483C2ABCDEF
Burned In MAC Address.......................... 74:83:C2:AB:CD:EF
Software Version............................... 1.9.3.5263085
"""

BASE = """!Current Configuration:
!
!System Description "EdgeSwitch 16-Port 150W, 1.9.3.5263085, Linux 3.6.5-03de2dbe, 1.0.0.4857129"
!System Software Version "1.9.3.5263085"
!System Up Time          "0 days 7 hrs 21 mins 3 secs"
!Additional Packages     QOS,IPv6 Management,Routing
!Current SNTP Synchronized Time: SNTP Client Mode Is Disabled
!
network protocol none
network parms 10.0.0.223 255.255.255.0 10.0.0.1
vlan database
vlan 10
vlan name 10 "Datos"
exit

configure
username "admin" password 0123456789abcdef level 15 encrypted
line console
exit

line telnet
exit

snmp-server sysname "SW-Prueba"
!
interface 0/1
description 'Uplink'
vlan participation include 10
vlan tagging 10
exit

interface 0/2
description 'PC'
exit

exit
"""

CHANGED = (BASE
           .replace('network parms 10.0.0.223 255.255.255.0 10.0.0.1', 'network parms 10.0.0.250 255.255.255.0 10.0.0.1')
           .replace('vlan 10\n', 'vlan 10,20,30\n')
           .replace('vlan name 10 "Datos"', 'vlan name 10 "Oficina"\nvlan name 20 "Camaras"')
           .replace('username "admin" password 0123456789abcdef level 15 encrypted',
                    'username "admin" password ffffffffffffffff level 15 encrypted\n'
                    'username "tecnico" password 1111 level 1 encrypted')
           .replace("snmp-server sysname \"SW-Prueba\"", "snmp-server sysname \"SW-Nuevo\"")
           .replace("interface 0/2\ndescription 'PC'\nexit", "interface 0/2\ndescription 'Camara'\n"
                    "vlan participation include 20\nvlan tagging 20\nvlan pvid 20\nexit\n\ninterface 0/3\n"
                    "description 'AP'\nshutdown\nexit")
           .replace("vlan participation include 10\nvlan tagging 10", "vlan participation exclude 10"))

prof = get_profile("ubiquiti_edgeswitch")
check("perfil EdgeSwitch usa la familia FastPath", prof.family == FASTPATH)
inv = parse_inventory(VERSION, prof)
check("inventario: modelo", inv["model"] == "ES-16-150W", inv)
check("inventario: serie", inv["serial"] == "7483C2ABCDEF", inv)
check("inventario: versión", inv["os_version"] == "1.9.3.5263085", inv)

later = BASE.replace("0 days 7 hrs 21 mins 3 secs", "0 days 9 hrs 2 mins 44 secs").replace(
    "SNTP Client Mode Is Disabled", "Oct 16 12:00:00 2026 UTC")
check("tiempo encendido y hora SNTP no generan cambios", clean_config(BASE, prof) == clean_config(later, prof))

cmds = build_rollback(clean_config(CHANGED, prof), clean_config(BASE, prof), FASTPATH)
print("\n".join("    " + c for c in cmds))
cfg_at = cmds.index("configure") if "configure" in cmds else -1


def before_configure(c):
    return c in cmds and cmds.index(c) < cfg_at


check("VLAN y modo privilegiado van antes de volver a «configure»", cmds[0] == "exit" and cfg_at > 0)
check("vlan database fuera de configure", before_configure("vlan database"))
check("borra las VLAN añadidas (no vlan 20,30)", before_configure("no vlan 20,30"))
check("restaura el nombre de la VLAN 10 sin borrarla", 'vlan name 10 "Datos"' in cmds and "no vlan name 10" not in cmds)
check("borra el nombre de la VLAN 20", "no vlan name 20" in cmds)
check("NO toca la IP de gestión (network parms)", not any(c.startswith("network ") for c in cmds))
check("usuario admin: se reescribe, no se borra", 'no username "admin"' not in cmds and
      'username "admin" password 0123456789abcdef level 15 encrypted' in cmds)
check("usuario añadido se elimina", 'no username "tecnico"' in cmds)
check("sysname se reescribe", 'snmp-server sysname "SW-Prueba"' in cmds and "no snmp-server sysname" not in cmds)
i1 = cmds.index("interface 0/1")
blk1 = cmds[i1:cmds.index("exit", i1) + 1]
check("interface 0/1: vuelve a include 10 y tagging 10", "vlan participation include 10" in blk1 and
      "vlan tagging 10" in blk1 and not any("auto" in c or c.startswith("no ") for c in blk1), blk1)
i2 = cmds.index("interface 0/2")
blk2 = cmds[i2:cmds.index("exit", i2) + 1]
check("interface 0/2: participación auto, sin tagging/pvid y descripción original",
      "vlan participation auto 20" in blk2 and "no vlan tagging 20" in blk2 and "no vlan pvid" in blk2 and
      "description 'PC'" in blk2 and "no description" not in blk2, blk2)
i3 = cmds.index("interface 0/3")
blk3 = cmds[i3:cmds.index("exit", i3) + 1]
check("interface 0/3 nueva: se devuelve a valores por omisión (no se borra el puerto)",
      "no interface 0/3" not in cmds and "no description" in blk3 and "no shutdown" in blk3, blk3)
check("sin cambios no genera comandos", build_rollback(clean_config(BASE, prof), clean_config(later, prof),
                                                        FASTPATH) == [])

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
