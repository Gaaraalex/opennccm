"""FortiGate en las solicitudes y la consola (lo que encontró el laboratorio FortiGate-VM 7.4.2). Sin red.
- Solicitudes desde una receta o Multi-equipo (app/consolecenter.py, strip_wrappers): se quitan las líneas que entran y
  salen del modo configuración (OpenNCCM ya entra y guarda), pero no el «end» que aplica un bloque «config …» de
  FortiOS (sin él, ningún cambio de FortiGate se aplicaba y la solicitud terminaba «bien»).
- Consultas de FortiOS (app/consolecore.py): «diagnose sys ntp status», «diagnose sniffer packet» leen; «execute
  reboot» y «execute factory-reset» cortan el servicio.
- Configuración de referencia (app/refconfig.py): no arma comandos al estilo Cisco para FortiOS."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import consolecore as cc  # noqa: E402
from app import refconfig  # noqa: E402
from app.consolecenter import strip_wrappers  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


w = strip_wrappers
check("Cisco: quita «configure terminal» y «end»", w("configure terminal\nhostname R1\nend") == "hostname R1")
check("Cisco: «conf t» y líneas vacías", w("conf t\n\ninterface Gi1\n description x\nend\n") == "interface Gi1\n description x")
check("Junos: quita «configure» y «commit»", w("configure\nset system host-name X\ncommit") == "set system host-name X")
check("Huawei: «system-view» y «return»", w("system-view\nsysname X\nreturn") == "sysname X")
check("FortiOS: conserva el «end» del bloque", w("config system global\nset hostname FGT\nend") == "config system global\nset hostname FGT\nend")
nested = "config system ntp\nset ntpsync enable\nconfig ntpserver\nedit 1\nset server 10.0.0.1\nnext\nend\nend"
check("FortiOS: bloques anidados, los dos «end»", w(nested) == nested, w(nested))
two = "config system global\nset hostname A\nend\nconfig system snmp sysinfo\nset status enable\nend"
check("FortiOS: dos bloques seguidos", w(two) == two, w(two))
check("FortiOS: un «end» de más (sin bloque abierto) se quita", w("config system global\nset hostname A\nend\nend") ==
      "config system global\nset hostname A\nend")
check("vacío", w("") == "" and w(None) == "")

for c in ("diagnose sys ntp status", "diagnose sniffer packet any 'host 10.0.0.5' 4 20", "get system status",
          "show system interface port1", "execute ping 10.0.0.1", "diagnose sys session list", "diagnose ip arp list"):
    check(f"FortiOS consulta: {c}", cc.is_query("fortios", c, "fortinet"))
for c in ("config system global", "set hostname X", "diagnose sys session clear", "execute backup config ftp x",
          "diagnose sys kill 9 100"):
    check(f"FortiOS cambio: {c}", cc.is_change("fortios", c, "fortinet"))
for c in ("execute reboot", "execute factory-reset", "execute shutdown"):
    check(f"FortiOS corta el servicio: {c}", cc.risk("fortios", c, "fortinet") == "outage")
diff = {"missing": [("Sistema", "config system global ⟩ set hostname \"A\"")],
        "extra": [("Sistema", "config router static ⟩ edit 1")], "ref_lines": [], "dev_lines": [], "skip": [], "sections": []}
cmds, skipped = refconfig.commands("fortinet", diff)
check("referencia FortiOS: sin comandos al estilo Cisco, todo a mano", cmds == "" and skipped == 2, (cmds, skipped))

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
