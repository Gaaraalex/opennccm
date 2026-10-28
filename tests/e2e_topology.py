"""Mapa de la red: el respaldo lee los vecinos CDP del Cisco simulado (tests/fakedev.py, puerto 2222), el mapa los
dibuja, enlaza con el equipo si ya está en OpenNCCM y, si no, ofrece agregarlo con nombre e IP propuestos."""
import json
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.crypto import encrypt  # noqa: E402
from app.db import Credential, Device, SessionLocal, set_setting  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def wait_backup(dev_id, before):
    for _ in range(40):
        time.sleep(0.5)
        with SessionLocal() as db:
            d = db.get(Device, dev_id)
            if d.last_backup_at and d.last_backup_at != before or d.last_status == "error":
                return d
    return d


# otras pruebas guardan el formulario de Ajustes sin este interruptor (y entonces queda apagado)
set_setting("topology_enabled", "1")
time.sleep(31)  # caché de ajustes del servidor
with SessionLocal() as db:
    cred = Credential(name="topo-cred", username="admin", password_enc=encrypt("cisco"), secret_enc=encrypt("cisco"))
    db.add(cred)
    db.flush()
    dev = Device(name="R-Topo", host="127.0.0.1", port=2222, device_type="cisco_ios", credential_id=cred.id)
    db.add(dev)
    db.commit()
    dev_id, cred_id = dev.id, cred.id

s = requests.Session()
s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
check("el menú lleva al Mapa de la red", 'href="/topology"' in s.get(B + "/").text)
s.post(B + f"/devices/{dev_id}/backup")
d = wait_backup(dev_id, None)
nb = json.loads(d.neighbors or "[]")
check("el respaldo guarda los vecinos CDP (y LLDP desactivado no rompe nada)",
      d.last_status == "ok" and len(nb) == 1 and nb[0]["remote_name"] == "SW-Vecino.empresa.local"
      and nb[0]["remote_ip"] == "192.0.2.77", (d.last_status, d.last_error, nb))
if not nb:  # diagnóstico: qué devolvió el equipo a las órdenes de vecinos
    from app import services
    with SessionLocal() as db:
        with services.connect(db.get(Device, dev_id)) as conn:
            print("SALIDA VECINOS:", repr(services._read_neighbors(conn, "cisco_ios"))[:1500])
page = s.get(B + "/topology").text
m = re.search(r"const NODES = (\[.*?\]), LINKS = (\[.*?\]),", page, re.S)
nodes, links = (json.loads(m.group(1)), json.loads(m.group(2))) if m else ([], [])
new = [n for n in nodes if n["kind"] == "new"]
links = [e for e in links if f"d{dev_id}" in (e["a"], e["b"])]  # en la suite, otros equipos del simulado también lo ven
check("el mapa dibuja el equipo, el vecino nuevo y el enlace con sus puertos",
      len(links) == 1 and links[0]["pa"] == "GigabitEthernet0/1" and links[0]["pb"] == "GigabitEthernet1/0/48"
      and new and new[0]["ip"] == "192.0.2.77" and new[0]["name"] == "SW-Vecino", (nodes, links))
form = s.get(B + "/devices/new?name=SW-Vecino&host=192.0.2.77").text
check("«Agregar a OpenNCCM» propone nombre e IP", 'value="SW-Vecino"' in form and 'value="192.0.2.77"' in form)

# si el vecino ya está en OpenNCCM (misma IP), el enlace va a ese equipo y no aparece como nuevo
with SessionLocal() as db:
    db.add(Device(name="Otro-Nombre", host="192.0.2.77", device_type="cisco_ios"))
    db.commit()
page = s.get(B + "/topology").text
m = re.search(r"const NODES = (\[.*?\]), LINKS = (\[.*?\]),", page, re.S)
nodes = json.loads(m.group(1))
check("vecino que ya está en OpenNCCM: enlazado por su IP, no como nuevo",
      not [n for n in nodes if n["kind"] == "new"] and any(n["label"] == "Otro-Nombre" for n in nodes), nodes)

# desactivado en Ajustes: no se leen vecinos
set_setting("topology_enabled", "0")
time.sleep(31)  # caché de ajustes
with SessionLocal() as db:
    before = db.get(Device, dev_id).last_backup_at
    db.get(Device, dev_id).neighbors = None
    db.commit()
s.post(B + f"/devices/{dev_id}/backup")
d = wait_backup(dev_id, before)
check("con el mapa desactivado en Ajustes, el respaldo no lee vecinos", d.neighbors is None, d.neighbors)
set_setting("topology_enabled", "1")

with SessionLocal() as db:
    db.query(Device).filter(Device.name.in_(["R-Topo", "Otro-Nombre"])).delete(synchronize_session=False)
    db.query(Credential).filter(Credential.id == cred_id).delete()
    db.commit()
print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
