"""Configuración cortada sin marca de final (2.44.1). En el SW-Core-02 del usuario (Junos 21.4, formato «set») un
«Respaldar todos» guardó una versión con las 70 últimas líneas de menos (SNMP, routing-options, protocols y vlans).
Comprueba: la relectura cuando lo leído es el principio de la copia anterior, que una relectura igual se acepta (cambio
real), y el recuento de Junos («show configuration | display set | count»). Sin red."""
import os
import sys
import tempfile
from types import SimpleNamespace

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import services  # noqa: E402
from app.drivers import get_profile  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


FULL = "\n".join(["set version 21.4R3-S5.4", "set system host-name SW-Core-02"]
                 + [f"set interfaces ge-0/0/{i} unit 0 family ethernet-switching storm-control default" for i in range(30)]
                 + ["set snmp location \"DATA CENTER\""] + [f"set vlans v{i} vlan-id {i}" for i in range(2, 72)])
CUT = "\n".join(FULL.splitlines()[:33])


class Conn:
    """Equipo simulado: entrega en orden las salidas de cada lectura de la configuración y el recuento de Junos."""
    def __init__(self, reads, count=None):
        self.reads, self.count, self.sent = list(reads), count, []

    def send_command(self, cmd, read_timeout=None):
        self.sent.append(cmd)
        if cmd == services.JUNOS_COUNT:
            if isinstance(self.count, Exception):
                raise self.count
            return self.count or ""
        return self.reads.pop(0)


junos = get_profile("juniper_junos")
cisco = get_profile("cisco_ios")
dev = SimpleNamespace(name="SW-Core-02", custom_backup_cmd=None)

check("cortada al final: es el principio de la anterior", services.looks_cut(CUT, FULL))
check("igual o con cambios en medio: no es un corte", not services.looks_cut(FULL, FULL)
      and not services.looks_cut(CUT.replace("ge-0/0/3 ", "ge-0/0/9 "), FULL))
check("faltan menos de 3 líneas al final: no se sospecha", not services.looks_cut("\n".join(FULL.splitlines()[:-2]), FULL))
check("sin copia anterior no hay con qué comparar", not services.looks_cut(CUT, ""))

c = Conn([CUT, FULL], f"Count: {len(FULL.splitlines())} lines")
out = services._read_running(c, dev, junos, FULL)
check("cortada y la relectura llega entera: se guarda la entera", out == FULL and c.sent.count(junos.backup) == 2, c.sent)

c = Conn([CUT, CUT])
out = services._read_running(c, dev, cisco, FULL)
check("dos lecturas iguales (cambio real al final): se acepta", out == CUT and len(c.sent) == 2, c.sent)

c = Conn([FULL])
out = services._read_running(c, dev, cisco, FULL)
check("sin corte: una sola lectura", out == FULL and len(c.sent) == 1, c.sent)

c = Conn([CUT, CUT], f"Count: {len(FULL.splitlines())} lines")
try:
    services._read_running(c, dev, junos, FULL)
    check("Junos con menos líneas que su recuento: no se guarda", False)
except RuntimeError as e:
    check("Junos con menos líneas que su recuento: no se guarda", "incompleta" in str(e) and "33 de 103" in str(e), str(e))
    check("ese error se reintenta solo (como un corte de red)", bool(services.RETRY_NETWORK.search(str(e))))

c = Conn([CUT], "Count: 33 lines")
check("Junos sin copia anterior y recuento igual: se guarda", services._read_running(c, dev, junos, None) == CUT)
c = Conn([FULL], RuntimeError("syntax error"))
check("un Junos que no acepta «count» no deja de respaldarse", services._read_running(c, dev, junos, FULL) == FULL)
check("recuento con una línea («Count: 1 line»)", services.junos_missing("", "Count: 1 line") == (0, 1))
dev2 = SimpleNamespace(name="x", custom_backup_cmd="show configuration")
c = Conn(["a\nb"], "Count: 9 lines")
check("comando de respaldo propio: no se cuenta", services._read_running(c, dev2, junos, None) == "a\nb"
      and services.JUNOS_COUNT not in c.sent)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
