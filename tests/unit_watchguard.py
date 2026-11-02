"""WatchGuard Fireware: el respaldo usa «export config to console» (Fireware no tiene «show running-config»), un error del
equipo en la segunda línea («^» y «% Invalid input…») no se guarda como versión y la feature key se lee y se guarda UNA
sola vez (los respaldos siguientes ya no la piden). Con un equipo simulado, sin red."""
import os
import sys
import tempfile

D = tempfile.mkdtemp()
open(os.path.join(D, "secret.key"), "wb").write(os.urandom(32))
os.environ.update(NCM_DATA_DIR=D, NCM_KEY_FILE=os.path.join(D, "secret.key"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import services  # noqa: E402
from app.crypto import decrypt, encrypt  # noqa: E402
from app.db import ConfigVersion, Credential, Device, SessionLocal, init_db, select  # noqa: E402
from app.drivers import get_profile  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


XML = '<?xml version="1.0"?>\n<profile>\n<product-grade>2</product-grade>\n<system-parameters>\n</system-parameters>\n</profile>\n'
SYSINFO = "system name: fw-prueba\nmodel: T40\nserial number: D02807134F41D\nsoftware version: 12.8.1.B663143\n"
FEATURE = "Serial Number: D02807134F41D\nLicense ID: D02807134F41D\nName: fw-prueba\nModel: T40\nFeature: AUTH_DEVICE#1\n"
INVALID = "^\n% Invalid input detected at '^' marker."
SENT = []
STATE = {"backup": XML, "feature": FEATURE}


class Fake:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send_command(self, cmd, **kw):
        SENT.append(cmd)
        if cmd == "export config to console":
            return STATE["backup"]
        if cmd == "show sysinfo":
            return SYSINFO
        if cmd == "show feature-key":
            if STATE["feature"] is None:
                raise TimeoutError("sin respuesta")
            return STATE["feature"]
        return INVALID


services.connect = lambda dev: Fake()
init_db()
p = get_profile("watchguard_fireware")
check("perfil: «export config to console» y feature key una vez", p.backup == "export config to console"
      and p.license_cmd == "show feature-key" and p.license_label == "Feature key")
check("un error en la segunda línea se reconoce", services.device_error(INVALID))
check("una configuración XML no se toma por error", not services.device_error(XML))

with SessionLocal() as db:
    c = Credential(name="wg", username="read", password_enc=encrypt("x"))
    db.add(c)
    db.flush()
    dev = Device(name="fw-prueba", host="10.0.0.254", port=4118, device_type="watchguard_fireware", credential_id=c.id)
    db.add(dev)
    db.commit()
    dev_id = dev.id


def versions():
    with SessionLocal() as db:
        return [decrypt(v.content_enc) for v in db.scalars(select(ConfigVersion).where(ConfigVersion.device_id == dev_id))]


def device():
    with SessionLocal() as db:
        return db.get(Device, dev_id)


# 1) un comando que el equipo no entiende: no queda una «versión» con el mensaje de error
STATE["backup"] = INVALID
r = services.backup_device(dev_id, "manual", "admin")
check("salida «% Invalid input…»: respaldo fallido, sin versión", r == "error" and versions() == [], (r, versions()))

# 2) primer respaldo bueno: configuración XML, inventario y feature key
STATE["backup"] = XML
SENT.clear()
r = services.backup_device(dev_id, "manual", "admin")
d = device()
check("primer respaldo: guarda la configuración XML",
      r == "first" and len(versions()) == 1 and versions()[0].startswith("<?xml"), (r, versions()))
check("inventario desde «show sysinfo»: modelo, serie y versión",
      (d.model, d.serial, d.os_version) == ("T40", "D02807134F41D", "12.8.1.B663143"), (d.model, d.serial, d.os_version))
check("pide la feature key en el primer respaldo", "show feature-key" in SENT, SENT)
check("feature key guardada cifrada, con su fecha", d.license_at is not None and decrypt(d.license_enc).startswith("Serial Number")
      and "AUTH_DEVICE" not in (d.license_enc or ""))

# 3) los respaldos siguientes ya no la piden ni la cambian
first_at = d.license_at
STATE["feature"] = "Serial Number: OTRA\nFeature: CAMBIADA#1\n"
SENT.clear()
r = services.backup_device(dev_id, "scheduled", None)
d = device()
check("segundo respaldo: no vuelve a pedir la feature key", "show feature-key" not in SENT and r == "unchanged", (r, SENT))
check("la feature key guardada no cambia", d.license_at == first_at and "CAMBIADA" not in decrypt(d.license_enc))

# 4) si el equipo no la da, el respaldo sigue bien y se intenta de nuevo la próxima vez
with SessionLocal() as db:
    db.get(Device, dev_id).license_enc = None
    db.get(Device, dev_id).license_at = None
    db.commit()
STATE["feature"] = None
r = services.backup_device(dev_id, "scheduled", None)
d = device()
check("sin respuesta a «show feature-key»: el respaldo no falla y no guarda nada", r == "unchanged" and d.license_enc is None
      and d.last_status == "ok", (r, d.last_status))
STATE["feature"] = INVALID
services.backup_device(dev_id, "scheduled", None)
check("un error del equipo no se guarda como feature key", device().license_enc is None)
STATE["feature"] = FEATURE
services.backup_device(dev_id, "scheduled", None)
check("en el respaldo siguiente que la consigue, se guarda", device().license_enc is not None)

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} FALLOS")
sys.exit(1 if fails else 0)
