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

# Extreme EXOS 33.6 (salida real en GNS3). Antes se copiaba el nombre y la contraseña cifrada de la referencia y lo que
# sobraba se quitaba con «no …», que EXOS no tiene
XREF = '''configure snmp sysName "X-1"
configure snmp sysLocation "Sede"
create vlan "DATOS"
configure vlan DATOS tag 10
configure account admin encrypted "$5$G/00yq$aF7ncV9qx0"
configure snmpv3 engine-id 03:0c:7c:4a:d4:00:00
'''
XDEV = '''configure snmp sysName "X-3"
configure snmp sysLocation "otra ubicacion"
create vlan "SOBRA"
configure account admin encrypted "$5$SHmRwq$Pc1pSouhyW"
configure snmpv3 engine-id 03:0c:8d:b3:a6:00:00
'''
D = lambda i: type("D", (), {"host": "", "device_type": "extreme_exos", "id": i, "model": ""})()  # noqa: E731
c = refconfig.compare(D(1), XREF, D(3), XDEV, {})
keys = [k for _s, k in c["missing"] + c["extra"]]
check("EXOS: no compara el nombre, el engine-id ni la contraseña cifrada (son de cada switch)",
      not any(("sysName" in k or "engine-id" in k or "account admin" in k) for k in keys), keys)
cmds, _sk = refconfig.commands("extreme_exos", c)
lines = cmds.splitlines()
check("EXOS: igualar sin «no …»: borra lo que sobra con su orden contraria y agrega lo que falta",
      not any(x.startswith("no ") for x in lines) and 'delete vlan "SOBRA"' in lines and 'create vlan "DATOS"' in lines
      and "configure vlan DATOS tag 10" in lines and 'configure snmp sysLocation "Sede"' in lines, cmds)
check("EXOS: no cambia el nombre ni la contraseña del switch", not any("sysName" in x or "account" in x for x in lines), cmds)

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
