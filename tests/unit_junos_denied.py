"""Junos con un usuario sin permiso para ver la configuración (clase de login sin «view-configuration»): «show
configuration» responde solo los nombres de las secciones con «/* ACCESS-DENIED */» (QFX5100 13.2X51-D25.3). Antes se
guardaba como una versión más (9 líneas que no se pueden restaurar); ahora el respaldo falla y dice qué permiso falta."""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import services  # noqa: E402
from app.crypto import decrypt, encrypt  # noqa: E402
from app.db import ConfigVersion, Credential, Device, Event, SessionLocal, init_db, select  # noqa: E402
from app.drivers import get_profile  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


DENIED = "\n".join(f"set {s} /* ACCESS-DENIED */;" for s in (
    "version", "system", "interfaces", "snmp", "forwarding-options", "routing-options", "protocols", "firewall", "vlans"))
GOOD = ("set version 13.2X51-D25.3\nset system host-name SW-CORE-1\nset interfaces xe-0/0/0 description uplink\n"
        "set vlans DATOS vlan-id 10\n")
STATE = {"backup": DENIED}
BACKUP = get_profile("juniper_junos").backup


class Fake:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send_command(self, cmd, **kw):
        return STATE["backup"] if cmd == BACKUP else ""


services.connect = lambda dev: Fake()
services.RETRY_WAITS = (0, 0)
services.time.sleep = lambda s: None
init_db()

check("la salida con «ACCESS-DENIED» se reconoce", "view-configuration" in services.access_denied(DENIED))
check("una configuración normal no", services.access_denied(GOOD) == "")
check("un comentario cualquiera tampoco", services.access_denied("set system /* SECRET-DATA */ x;\n" + GOOD) == "")

with SessionLocal() as db:
    c = Credential(name="SW Juniper", username="lectura", password_enc=encrypt("x"))
    db.add(c)
    db.flush()
    dev = Device(name="SW CORE 1", host="192.0.2.20", device_type="juniper_junos", credential_id=c.id)
    db.add(dev)
    db.commit()
    dev_id = dev.id


def versions():
    with SessionLocal() as db:
        return [decrypt(v.content_enc) for v in db.scalars(select(ConfigVersion).where(ConfigVersion.device_id == dev_id))]


r = services.backup_device(dev_id, "manual", "admin")
with SessionLocal() as db:
    d = db.get(Device, dev_id)
    status, err = d.last_status, d.last_error or ""
    ev = [e.message for e in db.scalars(select(Event).where(Event.category == "Respaldo fallido"))]
check("respaldo con «ACCESS-DENIED»: falla y no deja versión", r == "error" and versions() == [], (r, versions()))
check("el error dice qué permiso falta", status == "error" and "view-configuration" in err and "ACCESS-DENIED" in err, err)
check("alarma «Respaldo fallido» con el motivo", any("view-configuration" in m for m in ev), ev)

STATE["backup"] = GOOD
r = services.backup_device(dev_id, "manual", "admin")
check("con el permiso ya dado, el respaldo se guarda", r != "error" and len(versions()) == 1
      and "host-name SW-CORE-1" in versions()[0], (r, versions()))

# CLI lenta al entrar («--- JUNOS 21.4R3-S5.4 built …» un rato antes del prompt): la espera inicial ya no es de 20 s
from netmiko.juniper.juniper import JuniperBase  # noqa: E402

services.junos_slow_login()
services.junos_slow_login()  # dos veces no la envuelve dos veces
seen = {}


class Slow:
    def read_until_pattern(self, pattern, read_timeout):
        seen["t"], seen["p"] = read_timeout, pattern
        return "{master:0}\nuser@SW>"


out = JuniperBase._test_channel_read(Slow(), pattern=r"[%>$#]")
check("Junos: espera el prompt hasta 90 s al entrar", seen == {"t": 90, "p": r"[%>$#]"} and out.endswith(">"), seen)
check("…y el parche se aplica una sola vez", getattr(JuniperBase._test_channel_read, "_ncm_slow", False))

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
