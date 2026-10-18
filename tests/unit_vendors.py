"""Pruebas sin red de lo aprendido con el EdgeSwitch, aplicado a todas las marcas:
mensajes de error, confirmaciones, líneas volátiles, secretos que cambian en cada lectura y reversión."""
import os
import re
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.drivers import HUAWEI, IOS, clean_config, compare_text, get_profile  # noqa: E402
from app.rollback import build_rollback, fastpath_style  # noqa: E402
from app.services import CONFIRM, DEVICE_ERROR, PROMPT_END  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


# ---------------------------------------------------------------- mensajes de error por fabricante
ERRORS = {
    "Cisco IOS": "        ^\n% Invalid input detected at '^' marker.",
    "Cisco incompleto": "% Incomplete command.",
    "Cisco IOS-XR": "!!% Invalid argument: Interface not found",
    "Arista/Extreme (%%)": "%% Invalid input detected at '^' marker.",
    "HP Comware / Mellanox": "% Unrecognized command found at '^' position.",
    "Dell OS10": "% Error: Unrecognized command.",
    "Huawei": "Error: Unrecognized command found at '^' position.",
    "Juniper sintaxis": "syntax error, expecting <command>.",
    "Juniper comando": "                ^\nunknown command.",
    "Juniper error:": "error: configuration check-out failed",
    "MikroTik comando": "bad command name foo (line 1 column 1)",
    "MikroTik fin": "expected end of command (line 1 column 9)",
    "MikroTik failure": "failure: already have such address",
    "MikroTik valor": "input does not match any value of interface",
    "FortiGate fallo": "Command fail. Return code -61",
    "FortiGate acción": "Unknown action 0",
    "FortiGate entrada": "entry not found in datasource",
    "FortiGate valor": "value parse error before 'abc'",
    "Palo Alto": "Invalid syntax.",
    "Palo Alto servidor": "Server error : set failed, may need to override template object first",
    "Nokia SR OS": "MINOR: CLI Command not allowed for this user.",
    "VyOS": "  Set failed",
    "VyOS comando": "  Invalid command: set foo",
    "Check Point": "CLINFR0329  Invalid command:'set foo'.",
    "EdgeSwitch/Netgear/Dell N": "Failed to set VLAN participation. Please refer system log for more information.",
    "FastPath inválido": "An invalid interface has been used for this function.",
    "HP ProCurve": "Invalid input: foo",
    "Cisco WLC": "Incorrect usage. Use the '?' or <TAB> key to list commands.",
}
for vendor, msg in ERRORS.items():
    check(f"error detectado: {vendor}", DEVICE_ERROR.search(msg) is not None, msg)

BENIGN = [
    "Enter configuration commands, one per line.  End with CNTL/Z.",
    "Building configuration...\n[OK]",
    "Warning: The current configuration will be written to the device.",
    "Info: Succeeded in setting the configuration.",
    "Configuration Saved!",
    "commit complete",
    "[admin@MikroTik] /ip address>",
    "This operation may take a few minutes.\nManagement interfaces will not be available during this time.",
    "Interface GigabitEthernet0/1 description changed",
    "FGT60F (interface) #",
]
for msg in BENIGN:
    check(f"no es error: «{msg.splitlines()[0][:45]}»", DEVICE_ERROR.search(msg) is None)

# ---------------------------------------------------------------- fin de respuesta y confirmaciones
for prompt in ["R1(config-if)#", "admin@PA-220# ", "[admin@MikroTik] > ", "<HUAWEI>", "[~HUAWEI-Vlanif10]",
               "FGT60F (interface) # ", "user@mx960# ", "(UBNT EdgeSwitch) (Vlan)#", "vyos@vyos# ", "$ "]:
    check(f"prompt reconocido: {prompt.strip()}", re.search(PROMPT_END, "salida\n" + prompt) is not None)
for q in ["Proceed with reload? [confirm]", "Are you sure you want to save? (y/n) ", "Continue? [Y/N]:",
          "Do you want to continue? [yes/no]: "]:
    check(f"confirmación reconocida: {q.strip()}", CONFIRM.search(q) is not None and re.search(PROMPT_END, q))

# ---------------------------------------------------------------- líneas volátiles y secretos
VOLATILE = {
    "mikrotik_routeros": ("# oct/16/2026 10:00:00 by RouterOS 6.49.10\n# software id = ABCD-1234\n/ip address\n",
                          "# oct/16/2026 11:30:12 by RouterOS 6.49.10\n# software id = ABCD-1234\n/ip address\n"),
    "juniper_junos": ("## Last commit: 2026-10-16 10:00:00 UTC by admin\nset system host-name mx\n",
                      "## Last commit: 2026-10-16 12:00:00 UTC by ops\nset system host-name mx\n"),
    "huawei": ("!Last configuration was updated at 2026-10-16 10:00:00\nsysname SW\n",
               "!Last configuration was updated at 2026-10-16 11:00:00\nsysname SW\n"),
    "nokia_sros": ("# Generated THU OCT 16 10:00:00 2026 UTC\nconfigure\n", "# Generated THU OCT 16 11:00:00 2026 UTC\nconfigure\n"),
    "checkpoint_gaia": ("# Exported by admin on Thu Oct 16 10:00:00 2026\nset hostname fw\n",
                        "# Exported by admin on Thu Oct 16 11:00:00 2026\nset hostname fw\n"),
    "dell_os6": ("!System Up Time          \"0 days 1 hrs\"\nvlan 10\n", "!System Up Time          \"0 days 9 hrs\"\nvlan 10\n"),
    "arista_eos": ("! Startup-config last modified at Thu Oct 16 10:00:00 2026 by admin\nhostname sw\n",
                   "! Startup-config last modified at Thu Oct 16 11:00:00 2026 by admin\nhostname sw\n"),
    "extreme_vsp": ("*" * 60 + "\n        Command Execution Time: Sun Oct 18 01:30:01 2026 UTC\n" + "*" * 60 +
                    "\nPreparing to Display Configuration...\n#\n# Sun Oct 18 01:30:01 2026 UTC\n"
                    "# box type             : 5520-24T-FabricEngine\n# software version     : 9.3.0.0\n",
                    "*" * 60 + "\n        Command Execution Time: Sun Oct 18 02:57:37 2026 UTC\n" + "*" * 60 +
                    "\nPreparing to Display Configuration...\n#\n# Sun Oct 18 02:57:37 2026 UTC\n"
                    "# box type             : 5520-24T-FabricEngine\n# software version     : 9.3.0.0\n"),
    "zyxel_os": ("! Last configuration change at 10:00:00 UTC Thu Oct 16 2026\nhostname z\n",
                 "! Last configuration change at 11:00:00 UTC Thu Oct 16 2026\nhostname z\n"),
}
for dtype, (a, b) in VOLATILE.items():
    p = get_profile(dtype)
    check(f"sin falso cambio por marca de tiempo: {dtype}", clean_config(a, p) == clean_config(b, p),
          clean_config(a, p) + " ≠ " + clean_config(b, p))
fg1 = 'config system admin\n    edit "admin"\n        set password ENC SH2Xq9y1kZcAbC==\n    next\nend\n'
fg2 = 'config system admin\n    edit "admin"\n        set password ENC SH2PpQ0rTzLmNo==\n    next\nend\n'
check("FortiGate: la contraseña re-cifrada no cuenta como cambio", compare_text(fg1) == compare_text(fg2))
check("FortiGate: la versión guardada conserva el valor cifrado real", "SH2Xq9y1kZcAbC==" in clean_config(fg1, get_profile("fortinet")))
check("FortiGate: un cambio real sí se detecta", compare_text(fg1) != compare_text(fg1.replace('"admin"', '"ops"')))

# ---------------------------------------------------------------- reversión Cisco (sangría)
IOS_CUR = """hostname R1-NUEVO
!
vlan 10
 name Datos
!
vlan 30
 name Camaras
!
interface GigabitEthernet0/1
 description Enlace-nuevo
 switchport trunk allowed vlan 10,30
 switchport mode trunk
!
interface Vlan1
 ip address 10.0.0.250 255.255.255.0
!
end
"""
IOS_TGT = """hostname R1
!
vlan 10
 name Datos
!
vlan 20
 name Voz
!
interface GigabitEthernet0/1
 description Enlace
 switchport trunk allowed vlan 10,20
 switchport mode trunk
!
interface Vlan1
 ip address 10.0.0.1 255.255.255.0
!
end
"""
cmds = build_rollback(IOS_CUR, IOS_TGT, IOS)
print("\n".join("    " + c for c in cmds))
check("Cisco: no detecta estilo FastPath", not fastpath_style(IOS_CUR))
check("Cisco: hostname se reescribe (sin «no hostname»)", "hostname R1" in cmds and not any(c.startswith("no hostname") for c in cmds))
check("Cisco: descripción y VLAN permitidas se reescriben sin negar",
      " description Enlace" in cmds and " switchport trunk allowed vlan 10,20" in cmds
      and not any("no description" in c or "no switchport trunk allowed" in c for c in cmds))
check("Cisco: la IP de la interfaz se reescribe sin «no ip address» (no corta la gestión)",
      " ip address 10.0.0.1 255.255.255.0" in cmds and not any("no ip address" in c for c in cmds))
check("Cisco: la VLAN 20 se crea antes de tocar las interfaces",
      cmds.index("vlan 20") < cmds.index("interface GigabitEthernet0/1"))
check("Cisco: la VLAN 30 se borra al final", cmds[-1] == "no vlan 30", cmds[-3:])

SEC_CUR = "interface Vlan1\n ip address 10.0.0.1 255.255.255.0\n ip address 10.9.9.1 255.255.255.0 secondary\n!\nend\n"
SEC_TGT = "interface Vlan1\n ip address 10.0.0.1 255.255.255.0\n!\nend\n"
check("Cisco: dirección secundaria sobrante se quita", " no ip address 10.9.9.1 255.255.255.0 secondary" in
      build_rollback(SEC_CUR, SEC_TGT, IOS))

# ---------------------------------------------------------------- reversión Huawei
HW_CUR = "sysname SW-B\n#\nvlan batch 10 20 30 to 32\n#\ninterface GigabitEthernet0/0/1\n port link-type access\n port default vlan 31\n#\nreturn\n"
HW_TGT = "sysname SW-A\n#\nvlan batch 10 20 40\n#\ninterface GigabitEthernet0/0/1\n port link-type access\n port default vlan 40\n#\nreturn\n"
hw = build_rollback(HW_CUR, HW_TGT, HUAWEI)
print("\n".join("    " + c for c in hw))
check("Huawei: vlan batch como conjunto (crea 40 primero)", hw[0] == "vlan batch 40", hw[:2])
check("Huawei: borra 30 a 32 al final, sin tocar 10 y 20", hw[-1] == "undo vlan batch 30 to 32", hw[-2:])
check("Huawei: sysname y VLAN del puerto se reescriben sin «undo»",
      "sysname SW-A" in hw and " port default vlan 40" in hw and not any(c.strip().startswith("undo sysname") or
                                                                            "undo port default vlan" in c for c in hw))

# ---------------------------------------------------------------- reversión Dell N-series / Netgear (FastPath sin «configure»)
DELL_CUR = """!Current Configuration:
!System Description "Dell Networking N1548, 6.6.0.2"
!
hostname "N1548-nuevo"
vlan 10
name "Datos"
exit
vlan 30
exit
interface Gi1/0/1
description "PC"
switchport access vlan 30
exit
interface Gi1/0/2
switchport mode trunk
switchport trunk allowed vlan 10,30
exit
"""
DELL_TGT = """!Current Configuration:
!System Description "Dell Networking N1548, 6.6.0.2"
!
hostname "N1548"
vlan 10
name "Datos"
exit
vlan 20
name "Voz"
exit
interface Gi1/0/1
description "PC"
switchport access vlan 20
exit
interface Gi1/0/2
switchport mode trunk
switchport trunk allowed vlan 10,20
exit
"""
check("Dell N-series: se detecta el estilo FastPath con perfil de familia IOS", fastpath_style(DELL_CUR))
dl = build_rollback(DELL_CUR, DELL_TGT, get_profile("dell_os6").family)
print("\n".join("    " + c for c in dl))
check("Dell: la VLAN 20 se crea antes de los puertos", dl.index("vlan 20") < dl.index("interface Gi1/0/1"))
check("Dell: la VLAN 30 se borra al final", dl[-1] == "no vlan 30", dl[-3:])
check("Dell: bloques cerrados con «exit» (sin sangría)", "exit" in dl and not any(c.startswith(" ") for c in dl))
check("Dell: VLAN del puerto y hostname se reescriben sin negar",
      "switchport access vlan 20" in dl and 'hostname "N1548"' in dl and
      not any(c.startswith(("no switchport access", "no hostname")) for c in dl))

# ---------------------------------------------------------------- inventario Extreme VOSS / ERS («show sys-info»)
from app.drivers import parse_inventory  # noqa: E402

VOSS_HW = """General Info :

        SysDescr     : VSP-8284XSQ (8.1.0.0) BoxType: VSP-8284XSQ
        SysName      : VSP-8284XSQ
        SysUpTime    : 4 day(s), 03:17:56

Chassis Info:

        Chassis            : 8284XSQ
        ModelName          : 8284XSQ
        BrandName          : Extreme Networks.
        Serial#            : 14JP2550H0BA
        H/W Revision       : 01
"""
VOSS_GNS3 = """General Info :

        SysDescr     : VOSSGNS3 (8.10.0.0) BoxType: VOSSGNS3
        SysName      : VOSS1
        SysUpTime    : 0 day(s), 00:42:10

Chassis Info:

        Chassis            : VOSSGNS3
        ModelName          : VOSSGNS3
        BrandName          : Extreme Networks.
        Serial#            : SDNIPM9F0CA7
"""
ERS = """Operation Mode:                Switch
Serial Number:                 12JP3C4X1234
Firmware Version:              7.0.0.11
Software Version:              v7.6.0.021
sysDescr:                      Ethernet Routing Switch 4826GTS-PWR+  HW:AA  FW:7.0.0.11  SW:v7.6.0.021
"""
vsp, ers = get_profile("extreme_vsp"), get_profile("extreme_ers")
check("Extreme VOSS usa «show sys-info» (no tiene «show version»)", vsp.inventory == "show sys-info")
check("Extreme ERS usa «show sys-info»", ers.inventory == "show sys-info")
inv = parse_inventory(VOSS_HW, vsp)
check("VOSS (VSP 8284): modelo, serie y versión",
      inv == {"os_version": "8.1.0.0", "model": "8284XSQ", "serial": "14JP2550H0BA"}, inv)
inv = parse_inventory(VOSS_GNS3, vsp)
check("VOSS (máquina virtual GNS3): modelo, serie y versión",
      inv == {"os_version": "8.10.0.0", "model": "VOSSGNS3", "serial": "SDNIPM9F0CA7"}, inv)
inv = parse_inventory(ERS, ers)
check("ERS 4800: modelo, serie y versión",
      inv == {"os_version": "7.6.0.021", "model": "4826GTS-PWR+", "serial": "12JP3C4X1234"}, inv)

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
