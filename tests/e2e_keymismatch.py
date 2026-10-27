"""Versiones cifradas con otra clave (p. ej. una copia restaurada sin su secret.key): la ficha del equipo se abre con un
aviso, la versión ilegible explica qué pasa (sin «Internal Server Error») y la comparación no rompe."""
import base64
import hashlib
import os
import sys

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.crypto import encrypt  # noqa: E402
from app.db import ConfigVersion, Device, SessionLocal  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def other_key(text):
    nonce = os.urandom(12)
    return base64.b64encode(nonce + AESGCM(os.urandom(32)).encrypt(nonce, text.encode(), None)).decode()


with SessionLocal() as db:
    dev = Device(name="Clave-Ajena", host="192.0.2.77", device_type="cisco_ios")
    db.add(dev)
    db.flush()
    old = ConfigVersion(device_id=dev.id, kind="running", content_enc=other_key("hostname viejo\n"),
                        sha256=hashlib.sha256(b"a").hexdigest(), size=15)
    db.add(old)
    db.flush()
    new = ConfigVersion(device_id=dev.id, kind="running", content_enc=encrypt("hostname nuevo\n"),
                        sha256=hashlib.sha256(b"b").hexdigest(), size=15)
    db.add(new)
    db.commit()
    dev_id, old_id = dev.id, old.id

s = requests.Session()
s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
r = s.get(B + f"/devices/{dev_id}")
check("la ficha del equipo se abre (sin error 500)", r.status_code == 200, r.status_code)
check("y avisa de que hay versiones cifradas con otra clave", "secret.key" in r.text)
r = s.get(B + f"/versions/{old_id}")
check("la versión ilegible explica el motivo en vez de «Internal Server Error»",
      r.status_code == 409 and "otra clave" in r.text, (r.status_code, r.text[:120]))

with SessionLocal() as db:
    db.query(ConfigVersion).filter(ConfigVersion.device_id == dev_id).delete()
    db.query(Device).filter(Device.id == dev_id).delete()
    db.commit()
print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
