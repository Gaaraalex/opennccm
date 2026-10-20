"""Prueba extremo a extremo de OpenNCCM vía HTTP contra el equipo simulado."""
import re
import sys
import time

import requests

B = "http://127.0.0.1:8000"
s = requests.Session()
fails = []



def tl_ids(page, kind=None):
    """Ids de la línea de tiempo de versiones de la ficha (más reciente primero), opcionalmente de un tipo."""
    rows = re.findall(r'class="tl-row[^"]*" data-id="(\d+)"(?:(?!data-id=).)*?class="tl-kind">(\w+)<', page, re.S)
    return list(dict.fromkeys(i for i, k in rows if kind is None or k == kind))

def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def get(path, **kw):
    r = s.get(B + path, **kw)
    return r


def post(path, data=None, **kw):
    return s.post(B + path, data=data or {}, **kw)


def flashes(html):
    return re.findall(r'<div class="flash[^"]*">(.*?)</div>', html, re.S)


r = post("/login", {"username": "admin", "password": "mala"})
check("login incorrecto rechazado", "incorrectos" in r.text)
r = post("/login", {"username": "admin", "password": "Admin12345"})
check("login correcto", r.url.endswith("/") and "Panel" in r.text, r.url)

for p in ["/", "/devices", "/devices/new", "/search", "/changes", "/configlets", "/compliance", "/vulns",
          "/schedules", "/events", "/reports", "/credentials", "/users", "/audit", "/settings", "/account",
          "/configlets/new", "/api/devices", "/healthz"]:
    r = get(p)
    check(f"GET {p} -> 200", r.status_code == 200, r.status_code)

r = post("/credentials", {"name": "lab", "username": "admin", "password": "cisco"})
check("crear credencial", "Credencial guardada" in r.text)
r = post("/groups", {"name": "Core"})
r = post("/devices/new", {"name": "R1", "host": "127.0.0.1", "device_type": "cisco_ios", "protocol": "ssh",
                          "port": "2222", "credential_id": "1", "group_id": "1", "enabled": "on"})
check("crear dispositivo", "/devices/1" in r.url, r.url)
time.sleep(1)
r = post("/devices/1/backup")
f = flashes(r.text)
check("respaldo manual", any("Respaldo realizado" in x or "Primer respaldo" in x for x in f), f)
html = get("/devices/1").text
check("inventario: versión 15.2(4)M7", "15.2(4)M7" in html)
check("inventario: serie FTX1234ABCD", "FTX1234ABCD" in html)
check("inventario: modelo CISCO2911/K9", "CISCO2911/K9" in html)
check("cumplimiento detecta infracciones", "Infracción" in html)
check("línea volátil 'Last configuration change' eliminada",
      "Last configuration change" not in get("/versions/1").text)

# segundo respaldo sin cambios
r = post("/devices/1/backup")
check("respaldo sin cambios", any("sin cambios" in x for x in flashes(r.text)), flashes(r.text))

# configlet de cambio -> solicitud -> aprobación (habilitar autoaprobación para admin)
post("/settings", {"require_approval": "on", "allow_self_approval": "on", "notify_compliance": "on",
                   "alert_min_severity": "critical", "retention_versions": "0"})
cid = re.search(r'/configlets/(\d+)/run[^>]*>Ejecutar</a>[\s\S]*?', get("/configlets").text)
# usar configlet "Agregar VLAN (IOS-like)"
cl = get("/configlets").text
m = re.search(r'<b>Agregar VLAN \(IOS-like\)</b>[\s\S]*?/configlets/(\d+)/run', cl)
vid = m.group(1)
r = get(f"/configlets/{vid}/run")
check("formulario configlet detecta variables", "vlan_id=" in r.text and "vlan_nombre=" in r.text)
r = post(f"/configlets/{vid}/run", {"device_ids": "1", "variables": "vlan_id=20\nvlan_nombre=VOZ",
                                     "title": "VLAN 20"})
crm = re.search(r"/changes/(\d+)", r.url)
check("solicitud de cambio creada", crm is not None, r.url)
cr = crm.group(1)
check("vista previa renderizada", "vlan 20" in r.text and "name VOZ" in r.text)
r = post(f"/changes/{cr}/review", {"decision": "approve", "comment": "ok"})
for _ in range(30):
    time.sleep(1)
    t = get(f"/changes/{cr}").text
    if "b-done" in t or "b-failed" in t or "b-partial" in t:
        break
check("cambio ejecutado (done)", 'badge b-done' in t, re.findall(r'badge b-(\w+)', t)[:3])
time.sleep(4)
html = get("/devices/1").text
vers = tl_ids(html)
check("nueva versión tras el cambio", len(vers) >= 3, vers)
run = get("/search?q=vlan+20").text
check("búsqueda encuentra 'vlan 20'", "1 equipos con coincidencias" in run)

# diff entre primera y última running
running_ids = tl_ids(html, "running")
last, first = running_ids[0], running_ids[-1]
d = get(f"/diff?a={first}&b={last}&view=unified").text
check("diff muestra +vlan 20", '<div class="a">+vlan 20</div>' in d)

# reversión a la primera versión
r = post(f"/versions/{first}/rollback")
crm = re.search(r"/changes/(\d+)", r.url)
check("solicitud de reversión creada", crm is not None, r.url)
t = r.text
check("reversión genera 'no vlan 20'", "no vlan 20" in t, re.findall(r"<pre>([\s\S]*?)</pre>", t)[:1])
post(f"/changes/{crm.group(1)}/review", {"decision": "approve"})
for _ in range(30):
    time.sleep(1)
    t = get(f"/changes/{crm.group(1)}").text
    if "b-done" in t or "b-failed" in t:
        break
check("reversión ejecutada", "badge b-done" in t, re.findall(r"<pre>([\s\S]*?)</pre>", t)[-1:])
time.sleep(4)
check("vlan 20 ya no está", "0 equipos" in get("/search?q=vlan+20").text or
      "coincidencias" not in get("/search?q=vlan+20").text)

# comando rechazado por el equipo -> failed
bad_cfg = post("/configlets/new", {"name": "Malo", "kind": "config", "body": "bogus command"})
cl = get("/configlets").text
m = re.search(r'<b>Malo</b>[\s\S]*?/configlets/(\d+)/run', cl)
r = post(f"/configlets/{m.group(1)}/run", {"device_ids": "1", "variables": ""})
crb = re.search(r"/changes/(\d+)", r.url).group(1)
post(f"/changes/{crb}/review", {"decision": "approve"})
for _ in range(30):
    time.sleep(1)
    t = get(f"/changes/{crb}").text
    if "b-done" in t or "b-failed" in t:
        break
check("comando inválido -> failed", "badge b-failed" in t)

# exec configlet
cl = get("/configlets").text
m = re.search(r'<b>Estado de interfaces \(IOS\)</b>[\s\S]*?/configlets/(\d+)/run', cl)
r = post(f"/configlets/{m.group(1)}/run", {"device_ids": "1", "variables": ""})
check("configlet exec devuelve salida", "salida simulada" in r.text)

# corrección de cumplimiento (no ip http server)
c = get("/compliance").text
m = re.search(r'/compliance/remediate/(\d+)', c)
check("hay botón Corregir", m is not None)

# tareas
r = post("/schedules", {"name": "Nocturno", "action": "backup", "cron": "0 2 * * *"})
check("tarea programada creada con próxima ejecución", "Nocturno" in r.text and "deshabilitada" not in r.text)
r = post("/schedules", {"name": "Mala", "action": "backup", "cron": "99 x"})
check("cron inválido rechazado", "inválida" in r.text)
r = post("/schedules/1/run")
check("ejecutar tarea", "Tarea lanzada" in r.text)

# informes
for k in ["inventory", "changes", "compliance", "vulns", "audit", "changes_requests"]:
    r = get(f"/reports/{k}.csv")
    check(f"informe {k}.csv", r.status_code == 200 and r.headers["content-type"].startswith("text/csv"))
check("inventario CSV contiene R1", "R1" in get("/reports/inventory.csv").text)

# usuarios / RBAC
post("/users", {"username": "lector", "role": "viewer", "password": "Lectura-2026x", "active": "on"})
s2 = requests.Session()
s2.post(B + "/login", data={"username": "lector", "password": "Lectura-2026x"})
check("viewer puede ver dispositivos", s2.get(B + "/devices").status_code == 200)
check("viewer NO puede respaldar", s2.post(B + "/devices/1/backup").status_code == 403)
check("viewer NO ve ajustes", s2.get(B + "/settings").status_code == 403)

# descarga y auditoría
r = get("/versions/1?raw=1")
check("descarga de configuración", "hostname R1" in r.text and "attachment" in r.headers.get("content-disposition", ""))
check("auditoría registra acciones", "change_approved" in get("/audit").text)

# importación CSV
csvdata = "name,host,device_type,credential,group,port,protocol,location\nSW2,10.9.9.9,huawei,lab,Acceso,22,ssh,Piso 2\nX,1.1.1.1,noexiste,lab,,22,ssh,\n"
r = post("/devices/import", files={"file": ("d.csv", csvdata)})
check("importación CSV (1 ok, 1 error)", "1 dispositivos importados" in r.text and "desconocido" in r.text)

# alarmas
r = post("/events/ack")
check("reconocer alarmas", r.status_code == 200)

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
