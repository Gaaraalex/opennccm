"""Configuración de referencia con Extreme VOSS: los bloques no van sangrados y terminan en «exit» (configuración real de un
5520 con VOSS 9.3 en GNS3). Los comandos para igualar cierran cada bloque con «exit» y no van sueltos."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import refconfig  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


REF = """vlan create 10 name "DATOS" type port-mstprstp 0
interface Vlan 10
ip address 192.168.10.1 255.255.255.0 1
exit
interface GigabitEthernet 1/5
name "Puesto-1"
no shutdown
exit
ntp server 10.0.0.1
"""
DEV = """interface GigabitEthernet 1/5
name "Otro"
exit
"""
L = refconfig.lines_of(REF, "extreme_vsp", [])
check("VOSS: las líneas de un bloque cuelgan de su «interface»", ("Interfaces", "interface GigabitEthernet 1/5 ⟩ no shutdown") in L
      and any(k == "interface Vlan 10 ⟩ ip address 192.168.10.1 255.255.255.0 1" for _s, k in L), L)
check("VOSS: la descripción del puerto (name) no se compara", not any("Puesto-1" in k for _s, k in L), L)
c = refconfig.compare(type("D", (), {"host": "", "device_type": "extreme_vsp", "id": 1, "model": ""})(), REF,
                      type("D", (), {"host": "", "device_type": "extreme_vsp", "id": 2, "model": ""})(), DEV, {})
cmds, _sk = refconfig.commands("extreme_vsp", c)
lines = cmds.splitlines()
check("VOSS: los comandos para igualar cierran cada bloque con «exit»", "interface GigabitEthernet 1/5" in lines
      and lines[lines.index("interface GigabitEthernet 1/5") + 1:lines.index("interface GigabitEthernet 1/5") + 3] == [" no shutdown", "exit"], cmds)
check("VOSS: ningún comando de bloque va suelto", not any(x.startswith("ip address") for x in lines), cmds)

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
