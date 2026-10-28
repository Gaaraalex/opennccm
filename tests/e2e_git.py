"""Copia de los respaldos en Git: con un repositorio «remoto» local (bare), cada respaldo nuevo es un commit con la
configuración del equipo, las contraseñas ocultas, «Probar y subir todo ahora» exporta todo y un repositorio inaccesible
genera una alarma sin romper el respaldo. Usa el Cisco simulado de tests/fakedev.py (puerto 2222)."""
import os
import subprocess
import sys
import tempfile
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.crypto import encrypt  # noqa: E402
from app.db import Credential, Device, Event, SessionLocal, set_setting  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout


bare = os.path.join(tempfile.mkdtemp(), "configs.git")
subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
for k, v in (("git_enabled", "1"), ("git_url", bare), ("git_branch", "main"), ("git_mask", "1")):
    set_setting(k, v)
time.sleep(31)  # caché de ajustes del servidor

with SessionLocal() as db:
    cred = Credential(name="git-cred", username="admin", password_enc=encrypt("cisco"), secret_enc=encrypt("cisco"))
    db.add(cred)
    db.flush()
    dev = Device(name="R-Git", host="127.0.0.1", port=2222, device_type="cisco_ios", credential_id=cred.id)
    db.add(dev)
    db.commit()
    dev_id, cred_id = dev.id, cred.id

s = requests.Session()
s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
check("Ajustes tiene la sección «Copia en Git»", 'id="git"' in s.get(B + "/settings").text)
s.post(B + f"/devices/{dev_id}/backup")
log = ""
for _ in range(40):
    time.sleep(0.5)
    log = git("--git-dir", bare, "log", "--format=%s", "main")
    if "R-Git" in log:
        break
check("el primer respaldo se sube como commit", "R-Git: primer respaldo [manual por admin]" in log, log)
cfg = git("--git-dir", bare, "show", "main:sin-grupo/R-Git.cfg")
check("un archivo por equipo, en la carpeta de su grupo", "hostname R1" in cfg, cfg[:200])
check("con las contraseñas y la comunidad SNMP ocultas",
      "$1$abc$xyz" not in cfg and "enable secret 5 <oculto>" in cfg and "snmp-server community <oculto>" in cfg, cfg)

r = s.post(B + "/settings/git-export", allow_redirects=True)
check("«Probar y subir todo ahora» funciona", "repositorio Git" in r.text, r.text[-300:])

# repositorio inaccesible: alarma, pero el respaldo sigue funcionando
set_setting("git_url", os.path.join(tempfile.mkdtemp(), "no-existe.git"))
time.sleep(31)
r = s.post(B + "/settings/git-export", allow_redirects=True)
check("repositorio inaccesible: lo explica al probar", "No se pudo subir a Git" in r.text)

set_setting("git_enabled", "0")
with SessionLocal() as db:
    db.query(Event).filter(Event.category == "Copia en Git fallida").delete()
    db.query(Device).filter(Device.id == dev_id).delete()
    db.query(Credential).filter(Credential.id == cred_id).delete()
    db.commit()
print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
