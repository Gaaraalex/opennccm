"""API con tokens: crear un token en Mi cuenta (se ve una vez), usarlo con «Authorization: Bearer», permisos del dueño,
grupos, auditoría, caducidad, revocación y que no sirva fuera de /api/. Usa el Cisco simulado (tests/fakedev.py, 2222)."""
import os
import re
import sys
from datetime import timedelta

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.crypto import encrypt, hash_password  # noqa: E402
from app.db import ApiToken, AuditLog, Credential, Device, SessionLocal, User, now, select  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def new_token(sess, name, days="0"):
    page = sess.post(B + "/account/tokens", data={"name": name, "days": days}).text  # sigue la redirección a Mi cuenta
    m = re.search(r'id="newtok"[^>]*>(ncm_[\w\-]+)<', page)
    return m.group(1) if m else "", page


with SessionLocal() as db:
    cred = Credential(name="api-cred", username="admin", password_enc=encrypt("cisco"), secret_enc=encrypt("cisco"))
    db.add(cred)
    db.commit()

admin = requests.Session()
admin.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
tok, page = new_token(admin, "script de inventario")
check("Mi cuenta crea el token y lo muestra una vez", tok.startswith("ncm_") and "no se volverá a mostrar" in page)
check("después solo se ve el prefijo", tok not in admin.get(B + "/account").text and tok[:12] in admin.get(B + "/account").text)
with SessionLocal() as db:
    t = db.scalar(select(ApiToken).where(ApiToken.name == "script de inventario"))
    check("se guarda solo el hash", t is not None and tok not in (t.token_hash, t.prefix) and len(t.token_hash) == 64)

H = {"Authorization": f"Bearer {tok}"}
r = requests.get(B + "/api/devices")
check("sin token: 401 en JSON", r.status_code == 401 and r.json().get("error") == "unauthorized")
check("token falso: 401", requests.get(B + "/api/devices", headers={"Authorization": "Bearer ncm_falso"}).status_code == 401)
r = requests.post(B + "/api/devices", headers=H, json={"name": "API-R1", "host": "127.0.0.1", "device_type": "cisco_ios",
                                                         "credential": "api-cred", "port": 2222})
check("crea un equipo (administrador)", r.status_code == 201 and r.json().get("name") == "API-R1", r.text)
dev_id = r.json().get("id")
check("nombre repetido: 409", requests.post(B + "/api/devices", headers=H, json={"name": "API-R1", "host": "x", "device_type": "cisco_ios"}).status_code == 409)
check("plataforma desconocida: 400", requests.post(B + "/api/devices", headers=H, json={"name": "API-X", "host": "x", "device_type": "nada"}).status_code == 400)
check("lista de equipos", any(d["name"] == "API-R1" for d in requests.get(B + "/api/devices", headers=H).json()))
r = requests.post(B + f"/api/devices/{dev_id}/backup", headers=H)
check("respalda y devuelve el resultado", r.status_code == 200 and r.json()["result"] == "first", r.text)
check("ficha del equipo", requests.get(B + f"/api/devices/{dev_id}", headers=H).json().get("status") == "ok")
vs = [v for v in requests.get(B + f"/api/devices/{dev_id}/versions", headers=H).json() if v["kind"] == "running"]
check("versiones (origen «api»)", len(vs) == 1 and vs[0]["trigger"] == "api", vs)
c = requests.get(B + f"/api/devices/{dev_id}/config", headers=H)
check("configuración en texto", c.status_code == 200 and "hostname R1" in c.text and c.headers["content-type"].startswith("text/plain"))
with SessionLocal() as db:
    acts = [a.action for a in db.scalars(select(AuditLog).where(AuditLog.action.like("api_%")))]
check("cada uso queda en Auditoría", {"api_device_create", "api_backup", "api_config_read"} <= set(acts), acts)
check("el token no sirve para la interfaz web", "Iniciar sesión" in requests.get(B + "/devices", headers=H).text)

# operador: no puede crear equipos
with SessionLocal() as db:
    db.add(User(username="api-oper", password_hash=hash_password("Operador12345"), role="operator"))
    db.commit()
oper = requests.Session()
oper.post(B + "/login", data={"username": "api-oper", "password": "Operador12345"})
otok, _p = new_token(oper, "operador")
r = requests.post(B + "/api/devices", headers={"Authorization": f"Bearer {otok}"}, json={"name": "API-O", "host": "x", "device_type": "cisco_ios"})
check("el token de un operador no crea equipos (403)", r.status_code == 403 and r.json()["error"] == "forbidden", r.text)

# caducado y revocado
with SessionLocal() as db:
    t = db.scalar(select(ApiToken).where(ApiToken.name == "operador"))
    t.expires_at = now() - timedelta(minutes=1)
    db.commit()
check("token caducado: 401", requests.get(B + "/api/devices", headers={"Authorization": f"Bearer {otok}"}).status_code == 401)
with SessionLocal() as db:
    tid = db.scalar(select(ApiToken).where(ApiToken.name == "script de inventario")).id
admin.post(B + f"/account/tokens/{tid}/delete")
check("token revocado: 401", requests.get(B + "/api/devices", headers=H).status_code == 401)

with SessionLocal() as db:
    db.query(Device).filter(Device.name == "API-R1").delete()
    db.query(Credential).filter(Credential.name == "api-cred").delete()
    db.query(User).filter(User.username == "api-oper").delete()
    db.commit()
print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
