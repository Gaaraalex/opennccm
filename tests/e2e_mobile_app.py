"""La app Android con servidor (app/mobile_app.py), con el Cisco simulado (tests/fakedev.py, puerto 2222): cada
pantalla lee su JSON con el token de la app, y las acciones pasan por el puente /api/mobile/act/… a la misma ruta de la
web, con los mismos permisos y la Auditoría. Rutas fuera de la lista, sin token o con un token que no es de la app: no."""
import os
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sqlalchemy import select  # noqa: E402

from app.crypto import encrypt, hash_password  # noqa: E402
from app.db import AuditLog, Credential, Device, Event, Schedule, SessionLocal, User  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


with SessionLocal() as db:
    cred = Credential(name="app-cred", username="admin", password_enc=encrypt("cisco"), secret_enc=encrypt("cisco"))
    db.add(cred)
    db.flush()
    d = Device(name="APP-SW1", host="127.0.0.1", port=2222, device_type="cisco_ios", credential_id=cred.id)
    db.add(d)
    if not db.scalar(select(User).where(User.username == "app-view")):
        db.add(User(username="app-view", password_hash=hash_password("Lectura-2026"), role="viewer"))
    db.commit()
    dev_id = d.id


def login(u, p):
    r = requests.post(B + "/api/mobile/login", json={"username": u, "password": p, "device": "prueba"})
    return r.json().get("token")


tok = login("admin", "Admin12345")
check("la app entra y recibe su token", bool(tok))
H = {"Authorization": "Bearer " + tok}
get = lambda p, **kw: requests.get(B + p, headers=H, **kw)  # noqa: E731
act = lambda p, **kw: requests.post(B + "/api/mobile/act" + p, headers=H, **kw)  # noqa: E731

# --- acciones por el puente
r = act(f"/devices/{dev_id}/backup")
j = r.json()
check("puente: Respaldar ahora responde JSON con el aviso de la web", r.status_code == 200 and j.get("ok") and j.get("messages"), j)
for _ in range(20):
    with SessionLocal() as db:
        if db.get(Device, dev_id).last_status == "ok":
            break
    time.sleep(1)
with SessionLocal() as db:
    a = db.scalar(select(AuditLog).where(AuditLog.action.like("%backup%")).order_by(AuditLog.id.desc()))
check("la acción queda en Auditoría con el usuario de la app", a is not None and a.username == "admin", a and a.action)
r = act(f"/devices/{dev_id}/compliance")
check("puente: Auditar cumplimiento", r.status_code == 200 and r.json().get("ok") is not None, r.text[:200])
r = act("/console/multi", json={"device_ids": [dev_id], "command": "show version"})
check("puente: Multi-equipo devuelve la salida (JSON de la web tal cual)", r.status_code == 200 and "groups" in r.json(), r.text[:200])
r = act("/schedules", data={"name": "Respaldo app", "action": "backup", "freq": "daily", "time": "02:00"})
with SessionLocal() as db:
    sch = db.scalar(select(Schedule).where(Schedule.name == "Respaldo app"))
check("puente: Nueva tarea crea la tarea", r.status_code == 200 and sch is not None, r.text[:200])
with SessionLocal() as db:
    e = Event(severity="warning", category="Respaldo fallido", device_id=dev_id, message="prueba")
    e2 = Event(severity="warning", category="Respaldo fallido", device_id=dev_id, message="otra")
    db.add_all([e, e2])
    db.commit()
    eid, eid2 = e.id, e2.id
r = act("/events/ack", data={"id": str(eid)})
with SessionLocal() as db:
    acked, other = db.get(Event, eid).acknowledged, db.get(Event, eid2).acknowledged
check("puente: Reconocer una alarma (solo esa)", r.status_code == 200 and acked and not other, (r.text[:200], acked, other))

nd = get("/api/mobile/newdevice").json()
check("Agregar equipo: plataformas y credenciales", len(nd.get("platforms", [])) > 100 and nd.get("credentials"), len(nd.get("platforms", [])))
r = act("/devices/new", data={"name": "APP-NUEVO", "host": "127.0.0.8", "device_type": "cisco_ios", "protocol": "ssh", "port": "22",
                              "credential_id": str(nd["credentials"][0]["id"]), "enabled": "on"})
with SessionLocal() as db:
    nuevo = db.scalar(select(Device).where(Device.name == "APP-NUEVO"))
check("puente: Agregar equipo", r.status_code == 200 and nuevo is not None and nuevo.enabled, r.text[:200])
r = act("/console/change-request", json={"title": "Receta desde la app", "body": "interface Loopback7\n description app", "device_ids": [dev_id]})
from app.db import ChangeRequest  # noqa: E402
with SessionLocal() as db:
    cr = db.scalar(select(ChangeRequest).where(ChangeRequest.title == "Receta desde la app"))
check("puente: receta → solicitud de cambio (por aprobar)", r.status_code == 200 and cr is not None and cr.status == "pending", r.text[:200])
r = act("/kb/save", json={"action": "draft", "title": "Caso desde la app", "symptom": "s", "cause": "c", "steps": [{"t": "paso"}]})
check("puente: Documentar caso (borrador)", r.status_code == 200 and r.json().get("ok") is not False, r.text[:200])

# la consola interactiva por WebSocket con el token de la app (la misma de la web)
try:
    import asyncio
    import json as _json

    import websockets

    async def _ws():
        async with websockets.connect(f"ws://127.0.0.1:8000/api/mobile/devices/{dev_id}/console/ws?v=2", additional_headers=H) as w:
            out = ""
            for _ in range(40):
                m = _json.loads(await asyncio.wait_for(w.recv(), 15))
                if m.get("t") == "p":  # usuario y contraseña (sin inicio automático)
                    await w.send(_json.dumps({"t": "c", "d": "admin" if not m.get("secret") else "cisco"}))
                if m.get("t") == "s":
                    await w.send(_json.dumps({"t": "c", "d": "show version"}))
                out += m.get("d", "") if m.get("t") == "o" else ""
                if "Cisco IOS Software" in out:
                    return out
            return out
    out = asyncio.run(_ws())
    check("Consola por WebSocket con el token de la app", "Cisco IOS Software" in out, out[-300:])
    async def _ws_bad():
        try:
            async with websockets.connect(f"ws://127.0.0.1:8000/api/mobile/devices/{dev_id}/console/ws?v=2") as w:
                m = await asyncio.wait_for(w.recv(), 10)
                return m
        except Exception as e:  # noqa: BLE001
            return repr(e)
    bad = asyncio.run(_ws_bad())
    check("Consola por WebSocket sin token: no abre", "Cisco" not in str(bad) and ("sesión" in str(bad) or "caduc" in str(bad) or "Error" in str(bad) or "1008" in str(bad) or "close" in str(bad).lower()), str(bad)[:200])
except ImportError:
    print("(sin websockets: se omite la consola)")

# --- lo que el puente no deja pasar
r = requests.post(B + "/api/mobile/act/devices/" + str(dev_id) + "/delete", headers=H)
check("puente: una ruta fuera de la lista no pasa (borrar equipo)", r.status_code == 404)
with SessionLocal() as db:
    check("…y el equipo sigue ahí", db.get(Device, dev_id) is not None)
r = requests.post(B + f"/api/mobile/act/devices/{dev_id}/backup")
check("puente: sin token, 401", r.status_code == 401)
s = requests.Session()
s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
s.post(B + "/account/tokens", data={"name": "script"})
r = requests.post(B + f"/api/mobile/act/devices/{dev_id}/backup", headers={"Authorization": "Bearer ncm_inventado"})
check("puente: con un token inventado, 401", r.status_code == 401)
vt = login("app-view", "Lectura-2026")
r = requests.post(B + f"/api/mobile/act/devices/{dev_id}/backup", headers={"Authorization": "Bearer " + vt})
check("puente: un usuario de solo lectura no respalda (403 de la web)", r.status_code in (403,) or r.json().get("ok") is False, r.status_code)

# --- cada pantalla
p = get("/api/mobile/panel").json()
check("Panel: totales, pendientes, atención y últimos cambios", p.get("total", 0) >= 1 and "attention" in p and "recent" in p and "comp_pct" in p, p)
dl = get("/api/mobile/devlist").json()
row = next((x for x in dl["devices"] if x["id"] == dev_id), {})
check("Dispositivos: lista con marca, estado y recuentos de los chips", row.get("brand") and "counts" in dl and dl["brands"], row)
f = get(f"/api/mobile/device/{dev_id}").json()
check("Ficha: las 7 pestañas", all(k in f for k in ("ports", "versions", "compliance", "vulns", "firmware", "events")) and f["versions"], list(f))
if len(f["versions"]) >= 1:
    v = f["versions"][0]["id"]
    dd = get("/api/mobile/diff", params={"a": v, "b": v}).json()
    check("Versiones: comparar dos versiones", "lines" in dd)
al = get("/api/mobile/alarms", params={"unack": 0}).json()
check("Alarmas: lista y recuentos por tipo", "items" in al and "vecinos" in al["counts"] and al["items"], al.get("counts"))
co = get("/api/mobile/console").json()
check("Consola: equipos, recetas, grabaciones y solicitudes", co.get("devices") and co.get("recipes") and "recordings" in co, list(co))
check("Consola: calculadora de subredes", get("/api/mobile/subnet", params={"cidr": "192.168.1.10/24"}).json().get("hosts") == 254)
m = get("/api/mobile/more").json()
check("Más: los recuentos del menú", "pending_changes" in m and "configlets" in m and m["is_admin"], m)
sr = get("/api/mobile/search", params={"q": "hostname"}).json()
check("Buscar en configuraciones", sr.get("hits"), sr)
check("Configlets y scripts", "items" in get("/api/mobile/configlets").json())
t = get("/api/mobile/tasks").json()
check("Tareas programadas (con la nueva)", any(x["name"] == "Respaldo app" and x["when"] for x in t["items"]), t["items"][:2])
check("Cumplimiento", "policies" in get("/api/mobile/compliance").json())
check("Vulnerabilidades", "items" in get("/api/mobile/vulns").json())
fw = get("/api/mobile/firmware").json()
check("Firmware y ciclo de vida", all(k in fw for k in ("images", "updates", "lifecycle")), list(fw))
an = get("/api/mobile/analytics").json()
check("Análisis", "per_day" in an and "by_trigger" in an, list(an))
check("Mapa de la red", "nodes" in get("/api/mobile/map").json())
check("IP y VLAN", "ips" in get("/api/mobile/ipam").json())
rp = get("/api/mobile/reports").json()
check("Informes: lista", rp.get("items"))
rr = get("/api/mobile/report/inventory").json()
check("Informes: generar (inventario)", rr.get("header") and any("APP-SW1" in " ".join(x) for x in rr["rows"]), rr.get("rows", [])[:2])
r = act("/reports/inventory.csv".replace("/reports/", "/reports/"), ) if False else requests.get(B + "/api/mobile/act/reports/inventory.csv", headers=H)
check("Informes: descargar el CSV por el puente", r.status_code == 200 and "APP-SW1" in r.text, r.status_code)
check("Base de conocimiento", "items" in get("/api/mobile/kb").json())
ad = get("/api/mobile/admin").json()
check("Administración: usuarios, credenciales, grupos, nodos, plataformas, auditoría y sistema",
      all(k in ad for k in ("users", "credentials", "groups", "nodes", "platforms", "audit", "system")), list(ad))
vh = {"Authorization": "Bearer " + vt}
check("Administración: un usuario sin rol de administrador no la ve", requests.get(B + "/api/mobile/admin", headers=vh).status_code == 403)
ac = get("/api/mobile/account").json()
check("Mi cuenta: 2 pasos y tokens (el de la app aparece)", "two_fa" in ac and any(x["client"] == "android" for x in ac["tokens"]), ac)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
