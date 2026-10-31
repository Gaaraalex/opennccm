"""Restaurar una copia en otro equipo del mismo modelo (tests/fakedev.py, puerto 2222): por SSH a un equipo que no está
en OpenNCCM (IP, usuario y contraseña escritos), a otro equipo de OpenNCCM y por consola serie (el navegador manda lo que
leyó). Se valida el modelo antes, se ven los comandos y el equipo de origen no se toca."""
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import services  # noqa: E402
from app.crypto import encrypt  # noqa: E402
from app.db import ConfigVersion, Credential, Device, SessionLocal, select  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def running():
    with SessionLocal() as db:
        with services.connect(db.get(Device, src_id)) as conn:
            return conn.send_command("show running-config")


with SessionLocal() as db:
    cred = Credential(name="rs-cred", username="admin", password_enc=encrypt("cisco"), secret_enc=encrypt("cisco"))
    db.add(cred)
    db.flush()
    src = Device(name="R-Origen", host="127.0.0.1", port=2222, device_type="cisco_ios", credential_id=cred.id)
    other = Device(name="R-Destino", host="127.0.0.1", port=2222, device_type="cisco_ios", credential_id=cred.id)
    db.add_all([src, other])
    db.commit()
    src_id, other_id, cred_id = src.id, other.id, cred.id

s = requests.Session()
s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
services.backup_device(src_id, "manual", "admin")
services.backup_device(other_id, "manual", "admin")
with SessionLocal() as db:
    ver = db.scalars(select(ConfigVersion).where(ConfigVersion.device_id == src_id)).first()
    ver_id, model = ver.id, db.get(Device, src_id).model
check("el origen tiene versión y modelo", ver_id and model, model)
check("la versión ofrece «Restaurar en otro equipo…»", f"/versions/{ver_id}/restore" in s.get(B + f"/versions/{ver_id}").text)
page = s.get(B + f"/versions/{ver_id}/restore").text
check("página con los tres destinos y el aviso de SSH", "Otro equipo de OpenNCCM" in page and "Equipo nuevo por IP" in page
      and "Cable de consola" in page and "SSH habilitado" in page and "R-Destino" in page)

# alguien cambia el equipo de destino: la copia lo debe devolver a como estaba
services.push_commands(src_id, ["interface GigabitEthernet0/1", " description CAMBIADO"])
check("cambio hecho en el equipo", "description CAMBIADO" in running())

r = s.post(B + f"/versions/{ver_id}/restore/check", data={"mode": "ssh", "host": "127.0.0.1", "port": "2222",
                                                         "username": "admin", "password": "cisco", "secret": "cisco"})
tok = re.search(r'name="token" value="([^"]+)"', r.text)
check("SSH: valida el mismo modelo y muestra los comandos", tok and "Mismo modelo" in r.text and "CAMBIADO" in r.text,
      r.text[r.text.find("Destino:"):][:1500].replace(chr(10), " "))
check("la contraseña escrita no aparece en la página", "cisco" not in r.text.split("<main")[-1].replace("cisco_ios", ""))
if tok:
    r = s.post(B + f"/versions/{ver_id}/restore/apply", data={"token": tok.group(1)}, allow_redirects=False)
    job = re.search(r"job=([\w-]+)", r.headers.get("location", ""))
    st = {}
    for _ in range(60):
        time.sleep(0.5)
        st = s.get(B + f"/restore/{job.group(1)}/status").json() if job else {}
        if st.get("state") in ("done", "error", "missing"):
            break
    check("SSH: restaurada", st.get("state") == "done", st)
    check("el equipo vuelve a la configuración de la copia", "description LAN" in running() and "CAMBIADO" not in running())
    r = s.post(B + f"/versions/{ver_id}/restore/apply", data={"token": tok.group(1)})
    check("el token no se puede usar dos veces", "caducó o ya se usó" in r.text)

# equipo de OpenNCCM: si ya tiene la configuración, no hay nada que restaurar
r = s.post(B + f"/versions/{ver_id}/restore/check", data={"mode": "device", "target_id": str(other_id)})
check("otro equipo de OpenNCCM: igual a la copia, nada que restaurar", "ya tiene esta configuración" in r.text)

# otro modelo: no se restaura
with SessionLocal() as db:
    db.get(Device, src_id).model = "CISCO1941/K9"
    db.commit()
r = s.post(B + f"/versions/{ver_id}/restore/check", data={"mode": "ssh", "host": "127.0.0.1", "port": "2222",
                                                         "username": "admin", "password": "cisco", "secret": "cisco"})
check("otro modelo: se rechaza antes de enviar nada", "solo se puede restaurar en un equipo del mismo modelo" in r.text
      and 'name="token"' not in r.text)
page = s.get(B + f"/versions/{ver_id}/restore").text
check("en la lista, los equipos de otro modelo no se pueden elegir", re.search(rf'<option value="{other_id}" disabled', page))
with SessionLocal() as db:
    db.get(Device, src_id).model = model
    db.commit()

# credenciales equivocadas
r = s.post(B + f"/versions/{ver_id}/restore/check", data={"mode": "ssh", "host": "127.0.0.1", "port": "2222",
                                                         "username": "admin", "password": "mala"})
check("SSH con contraseña equivocada: se explica", "No se pudo conectar por SSH" in r.text)

# consola serie: el navegador manda el inventario y la configuración tal como salen por la consola
cfg = running().replace("description LAN", "description OTRA")
r = s.post(B + f"/versions/{ver_id}/restore/serial", json={
    "inventory": "R1#show version\n" + open(os.path.join(os.path.dirname(__file__), "fakedev.py"), encoding="utf-8").read()
    .split('VERSION = """')[1].split('"""')[0] + "\nR1#",
    "config": "\x1b[0mR1#show running-config\r\n" + cfg.replace("\n", "\r\n") + "\r\nR1#"}).json()
check("serie: modelo validado y guion con entrar, comandos, salir y guardar", r.get("ok") and r["script"][0] == "configure terminal"
      and "description LAN" in "\n".join(r["script"]) and r["script"][-2:] == ["end", "write memory"]
      and not any("R1#" in x for x in r["script"]), r)
r = s.post(B + f"/versions/{ver_id}/restore/serial", json={"inventory": "nada", "config": cfg}).json()
check("serie: sin poder leer el modelo, no se restaura", not r.get("ok") and "modelo" in r.get("error", ""), r)

with SessionLocal() as db:
    check("el origen sigue en OpenNCCM con sus versiones", db.get(Device, src_id) is not None
          and db.scalar(select(ConfigVersion.id).where(ConfigVersion.device_id == src_id)) is not None)
    db.query(ConfigVersion).filter(ConfigVersion.device_id.in_([src_id, other_id])).delete(synchronize_session=False)
    db.query(Device).filter(Device.id.in_([src_id, other_id])).delete(synchronize_session=False)
    db.query(Credential).filter(Credential.id == cred_id).delete()
    db.commit()
print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
