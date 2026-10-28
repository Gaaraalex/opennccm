"""MikroTik SwitchOS: alta (puerto 80, protocolo HTTP), respaldo por HTTP con Digest, inventario decodificado, sin cambios
por comandos y reversión explicada a mano. Usa tests/fakeswos.py."""
import os
import re
import subprocess
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from app.db import Device, SessionLocal  # noqa: E402

B, PORT = "http://127.0.0.1:8000", 18080
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


proc = subprocess.Popen([sys.executable, os.path.join(HERE, "fakeswos.py"), str(PORT)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
s = requests.Session()
try:
    time.sleep(1.5)
    s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
    for name, pw in (("swos-ok", "Clave-SwOS"), ("swos-mal", "otra")):
        s.post(B + "/credentials", data={"name": name, "username": "admin", "password": pw})
    creds = dict(re.findall(r'data-cred="(\d+)" data-name="([^"]+)"', s.get(B + "/credentials").text))
    cid = {v: k for k, v in creds.items()}
    check("SwitchOS aparece al crear un equipo", ">MikroTik SwitchOS (SwOS / SwOS Lite)</option>" in s.get(B + "/devices/new").text)
    r = s.post(B + "/devices/new", data={"name": "SW-SwOS", "host": "127.0.0.1", "device_type": "mikrotik_swos",
                                         "protocol": "ssh", "port": "22", "credential_id": cid["swos-ok"], "enabled": "on"})
    with SessionLocal() as db:
        d = db.query(Device).filter(Device.name == "SW-SwOS").first()
        check("se guarda como HTTP en el puerto 80 aunque el formulario diga SSH 22", d.protocol == "http" and d.port == 80)
        d.port = PORT  # el simulado escucha en otro puerto
        db.commit()
        dev_id = d.id
    r = s.post(B + f"/devices/{dev_id}/backup", allow_redirects=True)
    for _ in range(20):
        time.sleep(0.5)
        with SessionLocal() as db:
            d = db.get(Device, dev_id)
            if d.last_status != "pending":
                break
    page = s.get(B + f"/devices/{dev_id}").text
    check("respaldo por HTTP con Digest", d.last_status == "ok", d.last_error)
    check("inventario decodificado de sys.b (modelo, versión, serie)",
          d.model == "CRS326-24G-2S+" and d.os_version == "2.17" and d.serial == "HCN08Q4XYZ", (d.model, d.os_version, d.serial))
    check("sin botón de consola SSH", 'href="/devices/%d/console"' % dev_id not in page)
    with SessionLocal() as db:
        db.get(Device, dev_id).credential_id = int(cid["swos-mal"])
        db.commit()
    s.post(B + f"/devices/{dev_id}/backup")
    for _ in range(20):
        time.sleep(0.5)
        with SessionLocal() as db:
            d = db.get(Device, dev_id)
            if d.last_status == "error":
                break
    check("contraseña incorrecta: lo explica", d.last_status == "error" and "usuario o la contraseña" in (d.last_error or ""), d.last_error)
    from app import services  # noqa: E402
    try:
        services.push_commands(dev_id, ["vlan 30"])
        pushed = True
    except RuntimeError as e:
        pushed, msg = False, str(e)
    check("no se envían cambios por comandos", not pushed and "interfaz web" in msg)
    with SessionLocal() as db:
        db.query(Device).filter(Device.id == dev_id).delete()
        db.commit()
finally:
    proc.kill()

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
