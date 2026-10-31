"""API de la app Android (/api/mobile/…): entrar con usuario y contraseña o con el QR de Mi cuenta, panel, equipos con
puertos, respaldar, alarmas, cambios con aprobación, avisos y desvincular; con el Cisco simulado (tests/fakedev.py)."""
import os
import re
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.crypto import encrypt  # noqa: E402
from app.db import ChangeRequest, Credential, Device, Event, SessionLocal  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


with SessionLocal() as db:
    cred = Credential(name="mob-cred", username="admin", password_enc=encrypt("cisco"), secret_enc=encrypt("cisco"))
    db.add(cred)
    db.flush()
    dev = Device(name="R-Movil", host="127.0.0.1", port=2222, device_type="cisco_ios", credential_id=cred.id)
    db.add(dev)
    db.flush()
    db.add(Event(severity="critical", category="Respaldo fallido", device_id=dev.id, message="R-Movil: prueba"))
    db.add(ChangeRequest(title="Cambio de prueba (móvil)", body="interface GigabitEthernet0/1\n description MOVIL",
                         device_ids=f"[{dev.id}]", requested_by="otro"))
    db.commit()
    dev_id, cred_id = dev.id, cred.id

r = requests.post(B + "/api/mobile/login", json={"username": "admin", "password": "mala", "device": "Pixel"})
check("contraseña equivocada: 401", r.status_code == 401 and r.json().get("error") == "invalid", r.text)
r = requests.post(B + "/api/mobile/login", json={"username": "admin", "password": "Admin12345", "device": "Pixel 8"})
tok = r.json().get("token", "")
check("entrar con dirección, usuario y contraseña devuelve el token del teléfono", r.ok and tok.startswith("ncm_")
      and r.json()["user"]["role"] == "admin", r.text)
H = {"Authorization": "Bearer " + tok}
check("sin token: 401", requests.get(B + "/api/mobile/summary").status_code == 401)
sm = requests.get(B + "/api/mobile/summary", headers=H).json()
check("panel: equipos, alarmas abiertas y cambios por aprobar", sm["devices"]["total"] >= 1 and sm["alarms"]["open"] >= 1
      and sm["changes_pending"] >= 1, sm)
lst = requests.get(B + "/api/mobile/devices?q=R-Movil", headers=H).json()
check("equipos con búsqueda", [d["name"] for d in lst] == ["R-Movil"], lst)
r = requests.post(B + f"/api/mobile/devices/{dev_id}/backup", headers=H).json()
check("respaldar ahora desde la app", r["result"] in ("first", "changed", "unchanged"), r)
d = requests.get(B + f"/api/mobile/devices/{dev_id}", headers=H).json()
check("ficha con puertos, disposición del frente, atrás y versiones", d["ports"] and len(d["ports"]["items"]) == 4
      and d["ports"]["layout"]["rows"] == 1 and d["ports"]["rear"]["psu"] and d["versions"], str(d)[:400])
v = requests.get(B + f"/api/mobile/versions/{d['versions'][0]['id']}", headers=H).json()
check("ver una versión", "hostname" in v.get("text", ""), str(v)[:200])
ev = requests.get(B + "/api/mobile/events", headers=H).json()
mine = [e for e in ev if e["device_id"] == dev_id and e["message"] == "R-Movil: prueba"]
check("alarmas abiertas", mine, ev[:3])
if mine:
    check("reconocer una alarma", requests.post(B + f"/api/mobile/events/{mine[0]['id']}/ack", headers=H).json()["ok"])
ch = [c for c in requests.get(B + "/api/mobile/changes", headers=H).json() if c["title"] == "Cambio de prueba (móvil)"]
check("cambios por aprobar con sus equipos", ch and ch[0]["status"] == "pending" and ch[0]["devices"] == ["R-Movil"], ch)
if ch:
    r = requests.post(B + f"/api/mobile/changes/{ch[0]['id']}/review", headers=H, json={"decision": "reject", "comment": ""})
    check("rechazar sin motivo no se permite (mismas reglas que la web)", r.status_code == 409, r.text)
    r = requests.post(B + f"/api/mobile/changes/{ch[0]['id']}/review", headers=H, json={"decision": "reject", "comment": "prueba"})
    check("rechazar con motivo", r.ok and r.json()["ok"], r.text)
n0 = requests.get(B + "/api/mobile/notifications", headers=H).json()
check("avisos: el primer sondeo devuelve el último id sin avisos viejos", n0["last"] >= 1 and n0["items"] == [], n0)
with SessionLocal() as db:
    db.add(Event(severity="critical", category="Respaldo fallido", device_id=dev_id, message="R-Movil: caído"))
    db.commit()
n1 = requests.get(B + f"/api/mobile/notifications?since={n0['last']}", headers=H).json()
check("avisos nuevos desde el último", any("caído" in i["text"] for i in n1["items"]), n1)

# vincular con el QR de Mi cuenta (o el código escrito)
s = requests.Session()
s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
page = s.get(B + "/account/phones").text
check("Mi cuenta › App Android: teléfono vinculado en la lista", "Android · Pixel 8" in page)
page = s.post(B + "/account/phones/pair").text
code = re.search(r'id="pair-code">([^<]+)<', page)
check("QR de vinculación con su código", code and "data:image/svg+xml" in page)
if code:
    r = requests.post(B + "/api/mobile/pair", json={"code": code.group(1), "device": "Galaxy"})
    check("vincular con el código del QR", r.ok and r.json()["token"].startswith("ncm_"), r.text)
    r = requests.post(B + "/api/mobile/pair", json={"code": code.group(1), "device": "Otro"})
    check("el código no vale dos veces", r.status_code == 401)
check("los tokens de teléfonos no salen en Tokens de API", "Android · Pixel 8" not in s.get(B + "/account").text)
lab = s.get(B + f"/devices/{dev_id}/label").text
check("etiqueta QR del equipo", "R-Movil" in lab and "data:image/svg+xml" in lab)
r = s.get(B + "/app/android.apk", allow_redirects=False)
check("descargar la APK: la entrega o explica que esta instalación no la trae", r.status_code in (200, 303), r.status_code)

r = requests.post(B + "/api/mobile/logout", headers=H)
check("cerrar sesión en el teléfono revoca su token", r.ok and requests.get(B + "/api/mobile/me", headers=H).status_code == 401)

with SessionLocal() as db:
    db.query(Event).filter(Event.device_id == dev_id).delete()
    db.query(ChangeRequest).filter(ChangeRequest.title == "Cambio de prueba (móvil)").delete()
    db.query(Device).filter(Device.id == dev_id).delete()
    db.query(Credential).filter(Credential.id == cred_id).delete()
    db.commit()
print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
