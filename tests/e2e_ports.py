"""Puertos en la ficha del equipo: el respaldo del Cisco simulado (tests/fakedev.py, puerto 2222) lee «show interfaces
status» y la ficha muestra la franja de puertos y la pestaña «Puertos» con su estado."""
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.crypto import encrypt  # noqa: E402
from app.db import Credential, Device, SessionLocal  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


with SessionLocal() as db:
    cred = Credential(name="ports-cred", username="admin", password_enc=encrypt("cisco"), secret_enc=encrypt("cisco"))
    db.add(cred)
    db.flush()
    dev = Device(name="R-Puertos", host="127.0.0.1", port=2222, device_type="cisco_ios", credential_id=cred.id)
    db.add(dev)
    db.commit()
    dev_id, cred_id = dev.id, cred.id

s = requests.Session()
s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
page = s.get(B + f"/devices/{dev_id}").text
check("sin respaldo todavía: sin franja ni pestaña de puertos", 'id="pstrip"' not in page and 'href="#ports"' not in page)
s.post(B + f"/devices/{dev_id}/backup")
for _ in range(40):
    time.sleep(0.5)
    with SessionLocal() as db:
        d = db.get(Device, dev_id)
        if d.last_status in ("ok", "error"):
            break
pj = json.loads(d.ports or "{}")
by = {p["n"]: p for p in pj.get("ports", [])}
check("el respaldo guarda los puertos con su estado", d.last_status == "ok" and pj.get("status") is True
      and [by[n]["s"] for n in ("Gi0/0", "Gi0/1", "Gi0/2", "Gi0/3")] == ["up", "up", "off", "down"], (d.last_error, pj))
page = s.get(B + f"/devices/{dev_id}").text
check("la ficha muestra la franja de puertos", 'id="pstrip"' in page and "2 activos" in page and "de 4" in page)
check("pestaña «Puertos» con contador, justo después de Resumen",
      page.index('<a href="#resumen"') < page.index('<a href="#ports">') < page.index('<a href="#versions"')
      and '<span class="cnt2">4</span>' in page.split('<a href="#ports">')[1][:80])
check("la pestaña tiene la tabla Interfaces, filtros y Exportar CSV", 'id="prows"' in page and 'id="pfilt"' in page
      and 'id="pcsv"' in page)
check("datos de la configuración para el puerto (descripción y bloque «interface»)",
      '"d": "WAN"' in page and '"interface GigabitEthernet0/0"' in page)

with SessionLocal() as db:
    db.query(Device).filter(Device.id == dev_id).delete()
    db.query(Credential).filter(Credential.id == cred_id).delete()
    db.commit()
print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
