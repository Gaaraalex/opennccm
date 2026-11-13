"""Corrección automática de cumplimiento de punta a punta (servidor de run-tests.sh y el Cisco simulado del puerto
2222): una regla que se aplica, una que se revierte porque sigue sin cumplirse y una bloqueada por direcciones de
ejemplo; la espera de 24 h, «Reintentar», la pausa y los permisos. Va al final: cambia la configuración del simulado."""
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import verified  # noqa: E402  (misma base de datos que el servidor: NCM_DATA_DIR de run-tests.sh)
from app.crypto import decrypt  # noqa: E402
from app.db import AutoFixRun, Device, DeviceGroup, Policy, Rule, SessionLocal, select  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def flashes(html):
    return " | ".join(re.findall(r'<div class="flash[^"]*">(.*?)</div>', html, re.S))


def login(user, pw):
    s = requests.Session()
    r = s.post(B + "/login", data={"username": user, "password": pw})
    return s, r.url.rstrip("/") == B


def runs(dev_id):
    with SessionLocal() as db:
        return {(r.id, r.status, r.rule_ids) for r in db.scalars(select(AutoFixRun).where(AutoFixRun.device_id == dev_id))}


def wait_runs(dev_id, n, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        rs = runs(dev_id)
        if len(rs) >= n and all(s != "running" for _i, s, _r in rs):
            return rs
        time.sleep(2)
    return runs(dev_id)


adm, ok = login("admin", "Admin12345")
check("admin ingresa", ok)

# un equipo propio en un grupo propio, para que la política no toque los demás equipos de la batería
adm.post(B + "/groups", data={"name": "Autofix-prueba"})
with SessionLocal() as db:
    gid = db.scalar(select(DeviceGroup.id).where(DeviceGroup.name == "Autofix-prueba"))
    cred = db.scalar(select(Device.credential_id).where(Device.port == 2222, Device.credential_id.is_not(None)))
r = adm.post(B + "/devices/new", data={"name": "R-autofix", "host": "127.0.0.1", "device_type": "cisco_ios", "protocol": "ssh",
                                       "port": "2222", "credential_id": str(cred or 1), "group_id": str(gid), "enabled": "on"})
m = re.search(r"/devices/(\d+)", r.url)
check("equipo de prueba creado", m is not None, r.url)
dev = int(m.group(1)) if m else 0

rules = [("SSH v2", r"(?m)^ip ssh version 2$", "ip ssh version 2"),
         ("Nunca se cumple", r"(?m)^banner-imposible$", "logging buffered 64000"),
         ("Con dirección de ejemplo", r"(?m)^ntp server 192\.0\.2\.10$", "ntp server 192.0.2.10"),
         ("Sin sello", r"(?m)^service password-encryption$", "service password-encryption")]
data = {"name": "Autofix e2e", "group_id": str(gid), "device_type_filter": "cisco_ios", "description": "",
        "rule_id": [""] * 4, "rule_name": [x[0] for x in rules], "rule_kind": ["must_contain"] * 4,
        "rule_sev": ["major"] * 4, "rule_pattern": [x[1] for x in rules], "rule_fix": [x[2] for x in rules]}
adm.post(B + "/compliance/policies/save-all", data=data)
with SessionLocal() as db:
    pol = db.scalar(select(Policy).where(Policy.name == "Autofix e2e"))
    pid = pol.id
    rid = {r.name: r.id for r in pol.rules}
check("política con 4 reglas", len(rid) == 4, rid)
for name in ("SSH v2", "Nunca se cumple", "Con dirección de ejemplo"):
    verified.put("rule", rid[name], "IOS 15.2 (simulado)")
r = adm.post(f"{B}/devices/{dev}/backup")
check("primer respaldo del equipo (audita la política)", "Respaldo" in flashes(r.text) or "respaldo" in flashes(r.text), flashes(r.text))

# --- pantallas
html = adm.get(B + "/compliance").text
check("Cumplimiento enlaza a «Corrección automática»", 'href="/compliance/autofix"' in html)
html = adm.get(B + "/compliance/autofix").text
check("la política aparece apagada", re.search(r"Autofix e2e</b>[\s\S]*?Apagada", html) is not None)
html = adm.get(f"{B}/compliance/autofix/policy/{pid}").text
check("la configuración lista las reglas y no deja elegir la que no tiene sello",
      all(n in html for n in rid) and re.search(rf'value="{rid["Sin sello"]}"\s*\s*disabled', html) is not None)

# --- validaciones
r = adm.post(f"{B}/compliance/autofix/policy/{pid}", data={"autofix": "1", "day": list("0123456")})
check("no se activa sin elegir reglas", "al menos una regla" in flashes(r.text), flashes(r.text))
r = adm.post(f"{B}/compliance/autofix/policy/{pid}", data={"autofix": "1", "day": list("0123456"), "from": "22:00",
                                                           "rule": [str(rid["SSH v2"])]})
check("ni con una sola hora de la ventana", "las dos horas" in flashes(r.text), flashes(r.text))
with SessionLocal() as db:
    check("…y no guardó nada", not db.get(Policy, pid).autofix)

# --- activar: se corrige sola
r = adm.post(f"{B}/compliance/autofix/policy/{pid}", data={"autofix": "1", "day": list("0123456"),
                                                           "rule": [str(i) for i in rid.values()]})
check("activada con las 3 reglas verificadas (la sin sello no entra)", "(3 reglas)" in flashes(r.text), flashes(r.text))
with SessionLocal() as db:
    check("la regla sin sello no quedó elegida", db.get(Rule, rid["Sin sello"]).autofix_sig is None)
rs = wait_runs(dev, 3)
st = {int(re.search(r"\d+", ids).group()): s for _i, s, ids in rs}
check("SSH v2: aplicada", st.get(rid["SSH v2"]) == "applied", st)
check("la que sigue sin cumplirse: revertida", st.get(rid["Nunca se cumple"]) == "rolled_back", st)
check("la de direcciones de ejemplo: bloqueada", st.get(rid["Con dirección de ejemplo"]) == "blocked", st)
check("la sin sello: nunca", rid["Sin sello"] not in st, st)
with SessionLocal() as db:
    from app.services import latest_version
    cfg = decrypt(latest_version(db, dev).content_enc)
check("el equipo quedó con «ip ssh version 2» y sin «logging buffered 64000» (revertido)",
      "ip ssh version 2" in cfg and "logging buffered 64000" not in cfg and "ntp server 192.0.2.10" not in cfg)
html = adm.get(B + "/compliance/autofix").text
check("el historial muestra los tres resultados", all(x in html for x in ("Aplicada", "Revertida", "Bloqueada")))
check("con enlaces a las versiones de antes y después", "Versión de antes" in html and "Versión después" in html)
check("Cumplimiento marca la política", "corrección automática" in adm.get(B + "/compliance").text)

# --- 24 h de espera y «Reintentar»
r = adm.post(B + "/compliance/autofix/sweep")
check("enseguida no reintenta lo que falló (24 h)", "No hay nada que corregir" in flashes(r.text), flashes(r.text))
rb = next(i for i, s, ids in rs if s == "rolled_back")
r = adm.post(f"{B}/compliance/autofix/runs/{rb}/retry")
check("«Reintentar» la vuelve a poner en cola", "próxima revisión" in flashes(r.text), flashes(r.text))
rs2 = wait_runs(dev, 4)
check("…y se vuelve a intentar (y a revertir)", len(rs2) == 4 and sum(1 for _i, s, _r in rs2 if s == "rolled_back") == 2, rs2)

# --- pausa
r = adm.post(B + "/compliance/autofix/pause", data={"on": "1"})
check("pausar todo", "en pausa" in flashes(r.text) and "está en pausa" in r.text, flashes(r.text))
r = adm.post(B + "/compliance/autofix/sweep")
check("en pausa no revisa", "en pausa" in flashes(r.text), flashes(r.text))
r = adm.post(B + "/compliance/autofix/pause", data={"on": "0"})
check("reanudar", "reanudada" in flashes(r.text), flashes(r.text))

# --- permisos: un operador ve el historial, pero no configura
PW = "Clave-Autofix-1"
adm.post(B + "/users", data={"username": "af-op", "role": "operator", "password": PW, "password2": PW})
op, ok = login("af-op", PW)
check("operador ingresa", ok)
r = op.get(B + "/compliance/autofix")
check("el operador ve el historial, sin «Configurar» ni «Pausar»", r.status_code == 200 and "Configurar" not in r.text
      and "Pausar todo" not in r.text)
check("…y no puede configurarla ni pausarla (403)",
      op.get(f"{B}/compliance/autofix/policy/{pid}").status_code == 403
      and op.post(B + "/compliance/autofix/pause", data={"on": "1"}).status_code == 403
      and op.post(f"{B}/compliance/autofix/policy/{pid}", data={}).status_code == 403)

# --- apagar y limpiar
r = adm.post(f"{B}/compliance/autofix/policy/{pid}", data={"day": list("0123456")})
check("apagarla", "apagada" in flashes(r.text), flashes(r.text))
adm.post(f"{B}/compliance/policies/{pid}/delete")
with SessionLocal() as db:
    check("al borrar la política, el historial se conserva", db.scalar(select(AutoFixRun.id).where(AutoFixRun.device_id == dev)) is not None)
adm.post(f"{B}/devices/{dev}/delete")

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
