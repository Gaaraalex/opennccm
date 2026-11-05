"""Configuración de referencia por modelo: tipos, elegir la referencia, diferencias por sección (sin nombre, IP de gestión,
descripciones ni claves), ignorar líneas y preparar la solicitud para igualar."""
import hashlib
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import refconfig  # noqa: E402
from app.crypto import encrypt  # noqa: E402
from app.db import ChangeRequest, ConfigVersion, Device, SessionLocal, select  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


REF = """hostname ACC-S-P2
aaa new-model
aaa authentication login default group tacacs+ local
username admin privilege 15 secret 9 $9$abcREF
snmp-server community S3cr3tREF RO
ntp server 10.0.0.1
ntp server 10.0.0.2
interface Vlan1
 ip address 10.1.1.2 255.255.255.0
interface GigabitEthernet1/0/1
 description Puesto 1
 switchport mode access
 switchport access vlan 10
 spanning-tree portfast
crypto pki certificate chain TP-self-signed-1
 certificate self-signed 01
  3082022B 30820194 A0030201 02020101 300D0609
  quit
end
"""
DEV = """hostname ACC-SUC-E
aaa new-model
username admin privilege 15 secret 9 $9$xyzDEV
snmp-server community OtraClave RO
ntp server 10.0.0.1
interface Vlan1
 ip address 10.1.1.3 255.255.255.0
interface GigabitEthernet1/0/1
 description Otra descripcion
 switchport mode access
 switchport access vlan 20
 spanning-tree portfast
logging host 10.9.9.9
end
"""
with SessionLocal() as db:
    ids = []
    for name, host, text in (("ACC-S-P2", "10.1.1.2", REF), ("ACC-SUC-E", "10.1.1.3", DEV), ("ACC-SIN", "10.1.1.4", None)):
        d = Device(name=name, host=host, device_type="cisco_xe", model="C9200-48T", os_version="17.9.4")
        db.add(d)
        db.flush()
        if text:
            db.add(ConfigVersion(device_id=d.id, content_enc=encrypt(text), sha256=hashlib.sha256(text.encode()).hexdigest(), size=len(text)))
        ids.append(d.id)
    db.commit()
REF_ID, DEV_ID, SIN_ID = ids

with SessionLocal() as db:
    r, d = db.get(Device, REF_ID), db.get(Device, DEV_ID)
c = refconfig.compare(r, REF, d, DEV, {})
miss = [k for _s, k in c["missing"]]
extra = [k for _s, k in c["extra"]]
check("no se comparan: nombre, IP de gestión, descripciones, claves ni certificados", not any(
    x in " ".join(miss + extra) for x in ("hostname", "description", "10.1.1.2", "10.1.1.3", "S3cr3t", "OtraClave", "3082022B", "$9$")), (miss, extra))
check("faltan: aaa authentication, ntp server 10.0.0.2 y la VLAN 10 del puerto", set(miss) == {
    "aaa authentication login default group tacacs+ local", "ntp server 10.0.0.2",
    "interface GigabitEthernet1/0/1 ⟩ switchport access vlan 10"}, miss)
check("sobran: la VLAN 20 del puerto y logging host", set(extra) == {"interface GigabitEthernet1/0/1 ⟩ switchport access vlan 20",
                                                                     "logging host 10.9.9.9"}, extra)
check("por sección: AAA, NTP, Interfaces, Logging", c["sections"] == ["AAA", "NTP", "Interfaces", "Logging"], c["sections"])
cmds, skipped = refconfig.commands("cisco_xe", c)
check("comandos para igualar", cmds.splitlines() == ["aaa authentication login default group tacacs+ local", "ntp server 10.0.0.2",
                                                     "no logging host 10.9.9.9", "interface GigabitEthernet1/0/1",
                                                     " switchport access vlan 10", " no switchport access vlan 20"] and skipped == 0, cmds)

s = requests.Session()
s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
t = "cisco_xe|C9200-48T"
p = s.get(B + "/config-reference", params={"t": t}).text
check("sin referencia: «Todavía no hay referencia» y elegir equipo", "Todavía no hay referencia para Cisco C9200-48T" in p
      and "Usar como referencia" in p, p[:200])
s.post(B + "/config-reference/set", data={"device_id": REF_ID})
p = s.get(B + "/config-reference", params={"t": t}).text
check("con referencia: cabecera, tabla y «No se comparan»", "Referencia: ACC-S-P2" in p and "ACC-SUC-E" in p and "AAA" in p
      and "Sin respaldo todavía: se compara cuando haya uno." in p and "No se comparan" in p, p[:300])
dp = s.get(B + f"/config-reference/diff/{DEV_ID}").text
check("diferencias: título, contador, lado a lado y preparar solicitud", "ACC-SUC-E contra ACC-S-P2" in dp and "5 diferencias" in dp
      and "Preparar solicitud · 6 líneas" in dp and "REFERENCIA" in dp and "Ignorar en todos" in dp, dp[:300])
s.post(B + "/config-reference/ignore", data={"device_id": DEV_ID, "section": "Logging", "scope": "dev"})
dp = s.get(B + f"/config-reference/diff/{DEV_ID}?view=changes").text
check("«Ignorar en ACC-SUC-E»: ya no cuenta", "4 diferencias" in dp and "logging host" not in dp, dp[:300])
ig = s.get(B + "/config-reference/ignored", params={"t": t}).text
check("«No se comparan» › Editar muestra lo ignorado", "logging host 10.9.9.9" in ig)
r = s.post(B + f"/config-reference/request/{DEV_ID}", allow_redirects=False)
with SessionLocal() as db:
    cr = db.scalars(select(ChangeRequest).order_by(ChangeRequest.id.desc())).first()
check("«Preparar solicitud»: solicitud de cambio que espera aprobación", r.status_code in (302, 303) and cr is not None
      and "Igualar ACC-SUC-E a la referencia ACC-S-P2" in cr.title and "ntp server 10.0.0.2" in cr.body, (r.status_code, cr and cr.title))
fp = s.get(B + f"/devices/{DEV_ID}").text
check("ficha: ⋯ › «Usar como referencia»", "Usar como referencia" in fp)
with SessionLocal() as db:
    for i in ids:
        db.delete(db.get(Device, i))
    db.commit()
print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
