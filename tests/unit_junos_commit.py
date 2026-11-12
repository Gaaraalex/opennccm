"""Lo que encontró el laboratorio con el Juniper vQFX 19.4R1.10 (GNS3, 2026-11-12). Sin red.
- Reversión «set»: «family inet dhcp vendor-id X» (de fábrica en cada puerto xe- del vQFX) se borra desde «dhcp»; borrar
  la línea entera quitaba solo «vendor-id» y el commit de la copia con IP fija fallaba.
- Un commit que falla descarta la configuración candidata («rollback 0»): en Junos es compartida y el siguiente cambio de
  cualquiera la arrastraba y fallaba igual."""
import os
import sys
import tempfile
from contextlib import contextmanager
from types import SimpleNamespace

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import services  # noqa: E402
from app.rollback import set_deletes  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


FACT = "set interfaces xe-0/0/3 unit 0 family inet dhcp vendor-id Juniper-qfx10002-72q"
IP = "set interfaces xe-0/0/3 unit 0 family inet address 192.168.10.1/24"
d = set_deletes([FACT], [IP, "set interfaces xe-0/0/3 description Puesto-1"])
check("vQFX: «dhcp vendor-id …» se borra desde «dhcp»", d == ["delete interfaces xe-0/0/3 unit 0 family inet dhcp"], d)
d = set_deletes(["set interfaces xe-0/0/3 unit 0 family inet dhcp vendor-id A"],
                ["set interfaces xe-0/0/3 unit 0 family inet dhcp vendor-id B"])
check("…pero si el destino también tiene «dhcp», solo cambia la opción",
      d == ["delete interfaces xe-0/0/3 unit 0 family inet dhcp vendor-id A"], d)
d = set_deletes(["set policy-options prefix-list X 192.0.2.1/32"], [])
check("los objetos con nombre se siguen borrando enteros", d == ["delete policy-options prefix-list X"], d)


class FakeJunos:
    """Sesión de Netmiko de un Junos cuyo commit falla."""

    def __init__(self):
        self.sent = []
        self.base_prompt = "admin@Q-1"

    def config_mode(self):
        return ""

    def send_command(self, cmd, **_k):
        self.sent.append(cmd)
        return f"{cmd}\n\n{{master:0}}[edit]\nadmin@Q-1# "

    def find_prompt(self, **_k):
        return "admin@Q-1#"

    def commit(self, **_k):
        raise ValueError("Commit failed with the following errors:\n'dhcp'\n  Incompatible with interface assigned with address")

    def exit_config_mode(self):
        return ""


fake = FakeJunos()


@contextmanager
def fake_connect(_dev):
    yield fake


services.connect = fake_connect
dev = SimpleNamespace(device_type="juniper_junos", host="10.0.0.1", name="Q-1")
try:
    services.apply_config(dev, ["set system host-name Q-1-REC"])
    err = ""
except RuntimeError as e:
    err = str(e)
check("commit fallido: la solicitud falla con el motivo del equipo", "Incompatible with interface" in err, err)
check("…y se descarta la configuración candidata («rollback 0»)", "rollback 0" in fake.sent, fake.sent)
check("…y lo dice", "se descartaron los cambios pendientes" in err, err)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
