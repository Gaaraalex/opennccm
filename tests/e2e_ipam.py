"""IP y VLAN: la página reúne las IP y VLAN de las últimas configuraciones, avisa de IP repetidas y de VLAN con
nombres distintos, filtra y exporta a CSV."""
import hashlib
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.crypto import encrypt  # noqa: E402
from app.db import ConfigVersion, Device, SessionLocal  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


CFG = {
    "IPAM-SW1": ("cisco_ios", "vlan 10\n name Datos\n!\ninterface Vlan10\n ip address 10.10.0.1 255.255.255.0\n!\n"),
    "IPAM-SW2": ("cisco_ios", "vlan 10\n name DATOS-2\n!\ninterface Vlan10\n ip address 10.10.0.1 255.255.255.0\n!\n"),
    "IPAM-MK": ("mikrotik_routeros", "/interface vlan add interface=bridge name=vlan30 vlan-id=30\n"
                                     "/ip address add address=10.30.0.1/24 interface=vlan30 network=10.30.0.0\n"),
}
ids = []
with SessionLocal() as db:
    for name, (dtype, text) in CFG.items():
        d = Device(name=name, host="192.0.2.1", device_type=dtype)
        db.add(d)
        db.flush()
        db.add(ConfigVersion(device_id=d.id, kind="running", content_enc=encrypt(text),
                             sha256=hashlib.sha256(text.encode()).hexdigest(), size=len(text)))
        ids.append(d.id)
    db.commit()

s = requests.Session()
s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
check("el menú lleva a IP y VLAN", 'href="/ipam"' in s.get(B + "/").text)
p = s.get(B + "/ipam").text
check("lista las IP con su red e interfaz", "10.30.0.1/24" in p and "10.30.0.0/24" in p and "vlan30" in p)
check("avisa de la IP repetida en dos equipos", "10.10.0.1" in p and "repetida" in p and "configuradas en más de un sitio" in p)
v = s.get(B + "/ipam?tab=vlan").text
check("VLAN: la 10 con nombres distintos según el equipo", ("Datos / DATOS-2" in v or "DATOS-2 / Datos" in v) and "nombres distintos" in v)
check("VLAN: la 30 de MikroTik", ">30<" in v and "IPAM-MK" in v)
c = s.get(B + "/ipam.csv")
check("exporta a CSV", c.headers.get("content-type", "").startswith("text/csv") and "10.30.0.1" in c.text)

with SessionLocal() as db:
    db.query(ConfigVersion).filter(ConfigVersion.device_id.in_(ids)).delete(synchronize_session=False)
    db.query(Device).filter(Device.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
