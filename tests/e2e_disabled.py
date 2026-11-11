"""Equipo desactivado = aislado: con el Cisco simulado (tests/fakedev.py, puerto 2222), «Desactivar» en lote deja el
equipo sin ninguna conexión (respaldo, Multi-equipo, solicitud de cambio, API, conexiones internas por SSH y HTTP) y
«Activar» lo devuelve a la normalidad. Solo un administrador activa o desactiva, y queda en Auditoría."""
import json
import os
import sys
import time
from types import SimpleNamespace

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import fireware_web, services, swos  # noqa: E402
from app.crypto import encrypt  # noqa: E402
from app.db import AuditLog, ConfigVersion, Credential, Device, SessionLocal, User  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


with SessionLocal() as db:
    cred = Credential(name="off-cred", username="admin", password_enc=encrypt("cisco"), secret_enc=encrypt("cisco"))
    db.add(cred)
    db.flush()
    dev = Device(name="ACC-OFF", host="127.0.0.1", port=2222, device_type="cisco_ios", credential_id=cred.id)
    db.add(dev)
    db.commit()
    dev_id = dev.id


def versions():
    with SessionLocal() as db:
        return db.scalar(select(func.count(ConfigVersion.id)).where(ConfigVersion.device_id == dev_id))


def enabled():
    with SessionLocal() as db:
        return db.get(Device, dev_id).enabled


adm = requests.Session()
adm.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
r = adm.post(f"{B}/devices/{dev_id}/backup", allow_redirects=False)
v0 = versions()
check("activo: el respaldo funciona", v0 >= 1, v0)

# un operador no puede desactivar
with SessionLocal() as db:
    if not db.scalar(select(User).where(User.username == "op-off")):
        from app.crypto import hash_password
        db.add(User(username="op-off", password_hash=hash_password("Operador-2026"), role="operator"))
        db.commit()
op = requests.Session()
op.post(B + "/login", data={"username": "op-off", "password": "Operador-2026"})
r = op.post(B + "/devices/bulk", data={"action": "disable", "ids": str(dev_id)}, allow_redirects=False)
check("un operador no puede desactivar equipos", r.status_code in (302, 303, 403) and enabled(), (r.status_code, enabled()))

page = adm.get(B + "/devices").text
check("la lista ofrece Activar y Desactivar en lote", 'value="enable"' in page and 'value="disable"' in page)
r = adm.post(B + "/devices/bulk", data={"action": "disable", "ids": str(dev_id), "next": "/devices"}, allow_redirects=True)
check("Desactivar en lote lo desactiva", not enabled() and "desactivados" in r.text, enabled())
with SessionLocal() as db:
    a = db.scalar(select(AuditLog).where(AuditLog.action == "devices_disable").order_by(AuditLog.id.desc()))
check("queda en Auditoría quién lo desactivó", a is not None and "ACC-OFF" in (a.detail or "") and a.username == "admin")

ficha = adm.get(f"{B}/devices/{dev_id}").text
head = ficha.split("devph", 1)[1].split('id="dev-menu"', 1)[0]  # la cabecera de la ficha, con sus botones
check("la ficha dice «Desactivado», sin Consola, Interfaz web ni Respaldar ahora, y ofrece Activar",
      "Desactivado" in head and f"/devices/{dev_id}/console" not in head and "Interfaz web" not in head
      and f"/devices/{dev_id}/backup" not in head and 'value="enable"' in head and "En línea" not in head, head[:600])

r = adm.post(f"{B}/devices/{dev_id}/backup", allow_redirects=True)
check("Respaldar ahora no se conecta y lo explica", versions() == v0 and "desactivado" in r.text.lower())
j = adm.post(f"{B}/api/devices/{dev_id}/backup").json()
check("la API de respaldo responde «disabled»", j.get("result") == "disabled", j)
j = adm.post(B + "/console/multi", json={"device_ids": [dev_id], "command": "show version"}).json()
txt = str(j)
check("Multi-equipo no se conecta", "desactivado" in txt.lower() and "Cisco IOS Software" not in txt, txt[:300])
cons = adm.get(B + "/console").text
data = json.loads(cons.split('id="cc-data">', 1)[1].split("</script>", 1)[0])
check("Multi-equipo y Recetas no lo ofrecen", data["devs"] and all(d["id"] != dev_id for d in data["devs"]), [d["name"] for d in data["devs"]])

# las conexiones internas se cortan antes de tocar la red
with SessionLocal() as db:
    d = db.get(Device, dev_id)
    for what, fn in (("SSH (services.connect)", lambda: services.connect(d)),
                     ("HTTP de SwitchOS", lambda: swos._get(d, ("admin", "x"), "/sys.b")),
                     ("Web UI de Fireware", lambda: fireware_web.read_summary(d, "admin", "x"))):
        try:
            fn()
            ok, err = False, "se conectó"
        except services.DeviceDisabled as e:
            ok, err = True, str(e)
        except Exception as e:  # noqa: BLE001
            ok, err = False, repr(e)
        check(f"{what}: no se conecta con un equipo desactivado", ok, err)
try:  # la imagen .fxi usa un objeto aparte: también lleva «enabled»
    services.connect(SimpleNamespace(name="x", device_type="cisco_ios", protocol="ssh", host="127.0.0.1", port=2222,
                                     credential=None, enabled=False))
    check("FXI: no se conecta", False)
except services.DeviceDisabled:
    check("FXI: no se conecta", True)

# una solicitud de cambio sobre el equipo desactivado no lo toca
with SessionLocal() as db:
    from app.db import ChangeRequest
    cr = ChangeRequest(title="off", body="interface Loopback9\n description off", device_ids=json.dumps([dev_id]),
                       variables="{}", requested_by="admin", status="approved")
    db.add(cr)
    db.commit()
    cr_id = cr.id
services.execute_change_request(cr_id)
with SessionLocal() as db:
    cr = db.get(ChangeRequest, cr_id)
    res = (cr.result or "") + (cr.status or "")
check("una solicitud de cambio no se aplica y explica por qué", "desactivado" in res.lower() and versions() == v0, res[:300])

r = adm.post(B + "/devices/bulk", data={"action": "enable", "ids": str(dev_id), "next": "/devices"}, allow_redirects=True)
check("Activar en lote lo vuelve a activar", enabled() and "activados" in r.text, enabled())
adm.post(f"{B}/devices/{dev_id}/backup")
time.sleep(1)
check("activado otra vez: el respaldo funciona", adm.post(f"{B}/api/devices/{dev_id}/backup").json().get("result") in ("unchanged", "changed"))

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
