"""Al iniciar se relee el inventario guardado: equipos respaldados con una versión anterior del lector
(número de serie «......», modelo y firmware vacíos) se corrigen sin esperar al próximo respaldo."""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.crypto import encrypt  # noqa: E402
from app.db import Device, SessionLocal, init_db  # noqa: E402
from app.services import refresh_inventory_from_raw  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


RAW = """Switch: 1\r
\r
System Description............................. EdgeSwitch 16 150W, 1.9.3.5263085, Linux 3.6.5-03de2dbe\r
Machine Type................................... EdgeSwitch 16 150W\r
Machine Model.................................. ES-16-150W\r
Serial Number.................................. 74ACB9ED270A\r
Part Number.................................... 13-02143\r
Burned In MAC Address.......................... 74:AC:B9:ED:27:0A\r
Software Version............................... 1.9.3\r
Hardware Revision.............................. 30\r
"""

init_db()
with SessionLocal() as db:
    db.add(Device(name="SW de Prueba", host="10.0.0.223", device_type="ubiquiti_edgeswitch", port=22,
                  serial="..............................", inventory_raw_enc=encrypt(RAW)))
    db.add(Device(name="Sin inventario", host="10.0.0.9", device_type="cisco_ios", port=22, model="C9300"))
    db.commit()

n = refresh_inventory_from_raw()
with SessionLocal() as db:
    sw = db.query(Device).filter_by(name="SW de Prueba").one()
    other = db.query(Device).filter_by(name="Sin inventario").one()
    check("se actualizó un equipo", n == 1, n)
    check("modelo ES-16-150W", sw.model == "ES-16-150W", sw.model)
    check("serie 74ACB9ED270A (antes «......»)", sw.serial == "74ACB9ED270A", sw.serial)
    check("firmware 1.9.3", sw.os_version == "1.9.3", sw.os_version)
    check("un equipo sin salida guardada no se toca", other.model == "C9300")
check("una segunda lectura no cambia nada", refresh_inventory_from_raw() == 0)

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
