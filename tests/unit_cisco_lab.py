"""Lo que encontró el laboratorio con los Cisco de GNS3 (IOSv 15.6, 7200 15.3, 2026-11-12). Sin red.
- Reversión IOS: «snmp-server chassis-id» sin valor (IOSv lo muestra en cuanto hay otra línea de SNMP) no se escribe:
  la restauración se detenía en «% Incomplete command».
- Guardar en IOS: el primer «write memory» tras cambiar de imagen pide «Overwrite the previous NVRAM
  configuration?[confirm]»; se contesta, en lugar de esperar el prompt hasta fallar."""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import services  # noqa: E402
from app.drivers import get_profile  # noqa: E402
from app.rollback import IOS, build_rollback  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


cur = "hostname R1\n!\nntp server 1.1.1.1\n"
tgt = "hostname R1\n!\nsnmp-server location Sala\nsnmp-server chassis-id \nntp server 1.1.1.1\n"
cmds = build_rollback(cur, tgt, IOS)
check("restaurar: no escribe «snmp-server chassis-id» sin valor", cmds == ["snmp-server location Sala"], cmds)
cmds = build_rollback(tgt, cur, IOS)
check("…ni lo niega al volver atrás", cmds == ["no snmp-server location Sala"], cmds)
cmds = build_rollback(cur, "hostname R1\nsnmp-server chassis-id SW-SALA\n", IOS)
check("un chassis-id con valor sí se restaura", "snmp-server chassis-id SW-SALA" in cmds, cmds)


# ASAv 9.9.2: comentarios «: …», «ASA Version …» y el «no snmp-server location» de fábrica
asa_cur = ("ASA Version 9.9(2)\n!\nhostname K-1\nno snmp-server location\nno snmp-server contact\n"
           ": Serial Number: 9AWUX3J\n: Hardware:   ASAv, 2048 MB RAM\n: end\n")
asa_tgt = ("ASA Version 9.9(2)\n!\nhostname K-1\nsnmp-server location Sala\nsnmp-server contact NOC\n"
           ": Serial Number: 9ADAMFA5C82\n: Hardware:   ASAv, 2048 MB RAM\nobject network DATOS\n subnet 10.1.0.0 255.255.255.0\n: end\n")
cmds = build_rollback(asa_cur, asa_tgt, IOS)
check("ASA: no manda los comentarios «: Serial Number…» ni «ASA Version»", not any(c.lstrip("no ").startswith((":", "ASA Version")) for c in cmds), cmds)
check("ASA: «no snmp-server location» de fábrica no se niega (lo pone la línea con valor)",
      "snmp-server location" not in cmds and "snmp-server location Sala" in cmds and "snmp-server contact NOC" in cmds, cmds)
check("ASA: el resto se restaura", "object network DATOS" in cmds and " subnet 10.1.0.0 255.255.255.0" in cmds, cmds)

from types import SimpleNamespace  # noqa: E402

from app import refconfig  # noqa: E402

ref = ("ASA Version 9.9(2)\nhostname K-1\ninterface GigabitEthernet0/2\n nameif puesto\n security-level 50\n"
       " ip address 192.168.10.1 255.255.255.0\nobject network DATOS\n subnet 192.168.10.0 255.255.255.0\nmtu puesto 1500\n"
       "route puesto 172.20.0.0 255.255.0.0 192.168.10.254 1\n: Serial Number: AAA\n")
dev = "ASA Version 9.9(2)\nhostname K-2\ninterface GigabitEthernet0/2\n shutdown\n no nameif\n no ip address\n: Serial Number: BBB\n"
ra = SimpleNamespace(id=1, name="K-1", device_type="cisco_asa", host="10.0.0.240", model="ASAv")
rb = SimpleNamespace(id=2, name="K-2", device_type="cisco_asa", host="10.0.0.239", model="ASAv")
lines = refconfig.commands("cisco_asa", refconfig.compare(ra, ref, rb, dev, {}))[0].splitlines()
check("referencia ASA: los bloques primero, cada uno con «exit»", lines.index("mtu puesto 1500") > lines.index(" nameif puesto")
      and lines.count("exit") == 2 and lines.index("mtu puesto 1500") > max(i for i, x in enumerate(lines) if x == "exit"), lines)
check("referencia ASA: sin comentarios «: Serial Number»", not any(x.startswith((":", "no :")) for x in lines), lines)
check("referencia: la cabecera de un bloque nuevo no sale también suelta", lines.count("object network DATOS") == 1, lines)

# tercer nivel: «policy-map ⟩ class ⟩ inspect» (ASA) y «router bgp ⟩ address-family ⟩ neighbor» (IOS)
pm_a = "policy-map global_policy\n class inspection_default\n  inspect dns migrated_dns_map_1\n  inspect ftp\n!\n"
pm_b = "policy-map global_policy\n class inspection_default\n  inspect ftp\n  inspect dns preset_dns_map\n!\n"
cmds = build_rollback(pm_a, pm_b, IOS)
i_cls = cmds.index(" class inspection_default")
check("tercer nivel: entra en «class» antes de «no inspect …»", 0 < i_cls < cmds.index("  no inspect dns migrated_dns_map_1"), cmds)
check("…y escribe la nueva también dentro de «class»", cmds.index("  inspect dns preset_dns_map") > max(
    i for i, c in enumerate(cmds) if c == " class inspection_default"), cmds)
bgp_a = "router bgp 65000\n neighbor 10.0.0.2 remote-as 65001\n address-family ipv4\n  neighbor 10.0.0.2 activate\n  network 10.1.0.0 mask 255.255.0.0\n!\n"
bgp_b = "router bgp 65000\n neighbor 10.0.0.2 remote-as 65001\n address-family ipv4\n  neighbor 10.0.0.2 activate\n!\n"
cmds = build_rollback(bgp_a, bgp_b, IOS)
check("BGP: «no network …» dentro de address-family", cmds[:3] == ["router bgp 65000", " address-family ipv4", "  no network 10.1.0.0 mask 255.255.0.0"], cmds)
check("dos niveles: igual que antes", build_rollback("interface Gi1\n description A\n", "interface Gi1\n description B\n", IOS)
      == ["interface Gi1", " description B", " exit"])
ra2 = SimpleNamespace(id=3, name="K-1", device_type="cisco_asa", host="10.0.0.240", model="ASAv")
rb2 = SimpleNamespace(id=4, name="K-2", device_type="cisco_asa", host="10.0.0.239", model="ASAv")
ref_lines = refconfig.commands("cisco_asa", refconfig.compare(ra2, pm_b, rb2, pm_a, {}))[0].splitlines()
check("referencia: tercer nivel dentro de su sub-bloque", ref_lines[:2] == ["policy-map global_policy", " class inspection_default"]
      and "  inspect dns preset_dns_map" in ref_lines and "  no inspect dns migrated_dns_map_1" in ref_lines, ref_lines)

from app import consolecore as cc  # noqa: E402
check("ASA: «packet-tracer» es una consulta", cc.is_query(cc.os_of("cisco_asa"), "packet-tracer input inside tcp 10.0.0.5 1 8.8.8.8 443 detailed", "cisco_asa"))
check("ASA: «show running-config» consulta y «write erase» corta el servicio",
      cc.is_query("asa", "show running-config", "cisco_asa") and cc.risk("asa", "write erase", "cisco_asa") == "outage")
check("ASA: «object network X» es un cambio", cc.is_change("asa", "object network X", "cisco_asa"))


class FakeIos:
    def __init__(self, ask):
        self.sent, self.ask = [], ask

    def send_command(self, cmd, **_k):
        self.sent.append(cmd)
        if cmd == "write memory" and self.ask:
            return ("write memory\nWarning: Attempting to overwrite an NVRAM configuration previously written\n"
                    "by a different version of the system image.\nOverwrite the previous NVRAM configuration?[confirm]")
        return "Building configuration...\n[OK]\nR1#"

    def save_config(self):
        self.sent.append("save_config")
        return "[OK]"


c = FakeIos(ask=True)
out = services.save_config(c, get_profile("cisco_ios"))
check("guardar con confirmación: contesta y termina en [OK]", c.sent == ["write memory", "\n"] and "[OK]" in out, c.sent)
c = FakeIos(ask=False)
services.save_config(c, get_profile("cisco_ios"))
check("sin confirmación: un solo «write memory»", c.sent == ["write memory"], c.sent)
c = FakeIos(ask=False)
services.save_config(c, get_profile("cisco_asa"))
check("otras plataformas: el guardado de netmiko", c.sent == ["save_config"], c.sent)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
