"""Equipos retirados (app/retire.py) con el Cisco simulado (tests/fakedev.py, puerto 2222): retirar con reemplazo,
renombrado «NOMBRE (n)» y sin IP, aislado y fuera de lo activo, con su historial; el reemplazo con los mismos datos y
«Comparar»; la numeración; buscar por el nombre y la IP originales; «Reincorporar» y sus conflictos; permisos."""
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sqlalchemy import func, select  # noqa: E402

from app.crypto import encrypt, hash_password  # noqa: E402
from app.db import AuditLog, ConfigVersion, Credential, Device, Event, SessionLocal, User  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def dev(i):
    with SessionLocal() as db:
        d = db.get(Device, i)
        db.expunge(d)
        return d


def by_name(n):
    with SessionLocal() as db:
        return db.scalar(select(Device.id).where(Device.name == n))


def versions(i):
    with SessionLocal() as db:
        return db.scalar(select(func.count(ConfigVersion.id)).where(ConfigVersion.device_id == i)) or 0


with SessionLocal() as db:
    cred = Credential(name="ret-cred", username="admin", password_enc=encrypt("cisco"), secret_enc=encrypt("cisco"))
    db.add(cred)
    db.flush()
    # «localhost»: en la suite otros equipos simulados usan 127.0.0.1:2222
    d = Device(name="ACC-RET", host="localhost", port=2222, device_type="cisco_ios", credential_id=cred.id,
               location="Rack 4", notes="Núcleo")
    db.add(d)
    if not db.scalar(select(User).where(User.username == "op-ret")):
        db.add(User(username="op-ret", password_hash=hash_password("Operador-2026"), role="operator"))
    db.commit()
    old_id = d.id

adm = requests.Session()
adm.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
op = requests.Session()
op.post(B + "/login", data={"username": "op-ret", "password": "Operador-2026"})

adm.post(f"{B}/devices/{old_id}/backup")
v_old = versions(old_id)
check("antes: el equipo tiene respaldo", v_old >= 1, v_old)
with SessionLocal() as db:
    db.add(Event(severity="major", category="prueba", device_id=old_id, message="alarma abierta"))
    db.commit()

# --- el formulario y los permisos
f = adm.get(f"{B}/devices/{old_id}/retire").text
check("el formulario explica el nuevo nombre «ACC-RET (1)» y ofrece el reemplazo", "ACC-RET (1)" in f and 'name="replace"' in f)
ficha = adm.get(f"{B}/devices/{old_id}").text
check("la ficha ofrece «Retirar o reemplazar…» al administrador", f"/devices/{old_id}/retire" in ficha)
r = op.post(f"{B}/devices/{old_id}/retire", data={"confirm": "on", "reason": "damaged"}, allow_redirects=False)
check("un operador no puede retirar", dev(old_id).retired_at is None and r.status_code in (302, 303, 403), r.status_code)
adm.post(f"{B}/devices/{old_id}/retire", data={"reason": "damaged", "replace": "on"})
check("sin marcar la confirmación no se retira", dev(old_id).retired_at is None)

# --- retirar con reemplazo
r = adm.post(f"{B}/devices/{old_id}/retire", data={"confirm": "on", "reason": "damaged", "note": "Fuente quemada", "replace": "on"})
o = dev(old_id)
check("retirado: «ACC-RET (1)», sin IP, desactivado, con el nombre y la IP originales",
      o.retired_at is not None and o.name == "ACC-RET (1)" and o.host == "" and not o.enabled
      and o.orig_name == "ACC-RET" and o.orig_host == "localhost" and o.retired_reason == "damaged"
      and o.retired_note == "Fuente quemada" and o.retired_by == "admin", (o.name, o.host, o.enabled, o.orig_name))
new_id = by_name("ACC-RET")
n = dev(new_id) if new_id else None
check("el reemplazo tiene el nombre, la IP, el tipo, la credencial, la ubicación y las notas",
      n is not None and n.host == "localhost" and n.port == 2222 and n.device_type == "cisco_ios" and n.credential_id == o.credential_id
      and n.location == "Rack 4" and n.notes == "Núcleo" and n.enabled and n.replaces_id == old_id and o.replaced_by_id == new_id)
check("tras retirar se abre la ficha del reemplazo", r.url.endswith(f"/devices/{new_id}"), r.url)
check("el retirado conserva sus versiones (historial)", versions(old_id) == v_old)
with SessionLocal() as db:
    open_ev = db.scalar(select(func.count(Event.id)).where(Event.device_id == old_id, Event.acknowledged.is_(False))) or 0
    rec = db.scalar(select(Event).where(Event.device_id == old_id, Event.category == "retirado"))
    a = db.scalar(select(AuditLog).where(AuditLog.action == "device_retire").order_by(AuditLog.id.desc()))
check("sus alarmas abiertas se cierran", open_ev == 0, open_ev)
check("el retiro queda en sus eventos (como registro, no como alarma)", rec is not None and "ACC-RET (1)" in rec.message)
check("queda en Auditoría", a is not None and "ACC-RET" in (a.detail or "") and a.username == "admin")

# --- aislado y fuera de lo activo
res = adm.post(f"{B}/api/devices/{old_id}/backup").json()
check("aislado: el respaldo no se conecta", res.get("result") == "disabled" and versions(old_id) == v_old, res)
lista = adm.get(B + "/devices").text
check("la lista de Dispositivos no lo muestra y ofrece «Retirados (1)»",
      "ACC-RET (1)" not in lista and "/retired" in lista and re.search(r"Retirados \(\d+\)", lista))
busca = adm.get(B + "/devices", params={"q": "ACC-RET"}).text
check("buscar «ACC-RET» en Dispositivos avisa del retirado que coincide", "Ver en Retirados" in busca)
api = adm.get(B + "/api/devices").json()
check("la API no lo lista por omisión", all(x["id"] != old_id for x in api))
api = adm.get(B + "/api/devices", params={"retired": 1}).json()
row = next((x for x in api if x["id"] == old_id), {})
check("con ?retired=1 sale, con su nombre y su IP originales (la búsqueda Ctrl K)",
      row.get("retired") is True and row.get("orig_name") == "ACC-RET" and row.get("orig_host") == "localhost", row)
ret = adm.get(B + "/retired").text
check("Retirados lo muestra con su nombre e IP originales, el motivo y el reemplazo",
      "ACC-RET (1)" in ret and "localhost" in ret and "Dañado" in ret and f"/devices/{new_id}" in ret)
check("Retirados encuentra por la IP original", "ACC-RET (1)" in adm.get(B + "/retired", params={"q": "localhost"}).text)
check("Retirados: una búsqueda sin coincidencias no lo muestra", "ACC-RET (1)" not in adm.get(B + "/retired", params={"q": "zzz-nada"}).text)

ficha = adm.get(f"{B}/devices/{old_id}").text
head = ficha.split("devph", 1)[1].split('id="dev-menu"', 1)[0]
menu = ficha.split('id="dev-menu"', 1)[1].split("</div></span>", 1)[0]
check("la ficha del retirado: «Retirado», IP original, «Reincorporar» y sin Consola ni Respaldar",
      "Retirado" in head and "localhost" in head and f"/devices/{old_id}/reinstate" in head
      and f"/devices/{old_id}/console" not in head and f"/devices/{old_id}/backup" not in head, head[:400])
check("su menú no ofrece Editar ni volver a retirarlo", f"/devices/{old_id}/edit" not in menu and f"/devices/{old_id}/retire" not in menu)
check("su ficha dice quién lo reemplaza", "Lo reemplaza" in ficha and f"/devices/{new_id}" in ficha and "Fuente quemada" in ficha)
r = adm.get(f"{B}/devices/{old_id}/edit", allow_redirects=False)
check("Editar un retirado no se permite (hay que reincorporarlo)", r.status_code in (302, 303))
adm.post(f"{B}/devices/{old_id}/edit", data={"name": "X", "host": "10.9.9.9", "device_type": "cisco_ios", "enabled": "on"})
check("ni siquiera enviando el formulario a mano", dev(old_id).host == "" and not dev(old_id).enabled and dev(old_id).name == "ACC-RET (1)")
adm.post(B + "/devices/bulk", data={"action": "enable", "ids": str(old_id)})
check("«Activar» en lote no activa un retirado", not dev(old_id).enabled)

# --- el reemplazo: respaldo y «Comparar»
adm.post(f"{B}/devices/{new_id}/backup")
for _ in range(20):
    if versions(new_id):
        break
    time.sleep(1)
nf = adm.get(f"{B}/devices/{new_id}").text
m = re.search(r'href="(/diff\?a=\d+&amp;b=\d+)"', nf) or re.search(r'href="(/diff\?a=\d+&b=\d+)"', nf)
check("la ficha del reemplazo dice a quién reemplaza y ofrece comparar sus respaldos", "Reemplaza a" in nf and m is not None)
if m:
    check("la comparación se abre", adm.get(B + m.group(1).replace("&amp;", "&")).status_code == 200)

# --- la numeración sigue: retirar el reemplazo (sin otro reemplazo) le da «(2)»
adm.post(f"{B}/devices/{new_id}/retire", data={"confirm": "on", "reason": "returned"})
check("el segundo retirado con ese nombre es «ACC-RET (2)»", dev(new_id).name == "ACC-RET (2)" and by_name("ACC-RET") is None,
      dev(new_id).name)

# --- reincorporar
f = adm.get(f"{B}/devices/{old_id}/reinstate").text
check("el formulario de reincorporar propone el nombre y la IP originales", 'value="ACC-RET"' in f and 'value="localhost"' in f)
adm.post(f"{B}/devices/{old_id}/reinstate", data={"name": "ACC-RET", "host": "localhost", "enabled": "on"})
o = dev(old_id)
check("reincorporado con su nombre y su IP, activado y sin marcas de retiro",
      o.retired_at is None and o.name == "ACC-RET" and o.host == "localhost" and o.enabled and o.orig_name is None
      and o.replaced_by_id is None, (o.name, o.host, o.enabled))
check("vuelve a la lista de Dispositivos", "ACC-RET" in adm.get(B + "/devices").text)
res = adm.post(f"{B}/api/devices/{old_id}/backup").json()
check("y vuelve a respaldarse", res.get("result") in ("unchanged", "changed"), res)
# conflictos: el (2) no puede volver con el nombre ni con la IP que ya usa «ACC-RET»
r = adm.post(f"{B}/devices/{new_id}/reinstate", data={"name": "ACC-RET", "host": "10.250.0.9"}, allow_redirects=True)
check("no se reincorpora con un nombre en uso", dev(new_id).retired_at is not None and "Ya hay otro equipo" in r.text)
r = adm.post(f"{B}/devices/{new_id}/reinstate", data={"name": "ACC-RET-B", "host": "localhost"}, allow_redirects=True)
check("ni con una IP en uso por un equipo en servicio", dev(new_id).retired_at is not None and "la usa" in r.text)
adm.post(f"{B}/devices/{new_id}/reinstate", data={"name": "ACC-RET-B", "host": "127.0.0.9"})
b = dev(new_id)
check("con otro nombre y otra IP sí, y desactivado si no se marca «Activado»",
      b.retired_at is None and b.name == "ACC-RET-B" and b.host == "127.0.0.9" and not b.enabled)
with SessionLocal() as db:
    a = db.scalar(select(AuditLog).where(AuditLog.action == "device_reinstate").order_by(AuditLog.id.desc()))
check("reincorporar queda en Auditoría", a is not None and "ACC-RET-B" in (a.detail or ""))

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
