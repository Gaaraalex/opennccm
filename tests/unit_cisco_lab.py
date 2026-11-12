"""Lo que encontró el laboratorio con los Cisco de GNS3 (IOSv 15.6, 7200 15.3, 2026-11-12). Sin red.
- Reversión IOS: «snmp-server chassis-id» sin valor (IOSv lo muestra en cuanto hay otra línea de SNMP) no se escribe:
  la restauración se detenía en «% Incomplete command».
- Guardar en IOS: el primer «write memory» tras cambiar de imagen pide «Overwrite the previous NVRAM
  configuration?[confirm]»; se contesta, en lugar de esperar el prompt hasta fallar."""
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
