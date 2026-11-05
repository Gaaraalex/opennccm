"""Buscar una MAC o una IP: el respaldo del Cisco simulado (tests/fakedev.py, puerto 2222) lee «show mac address-table» y
«show ip arp»; la búsqueda de la barra superior dice en qué switch y puerto está, varios puertos, no encontrado, formato
no válido y «Leer las tablas ahora»."""
import os
import sys
import time
from datetime import timedelta

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.crypto import encrypt  # noqa: E402
from app.db import Credential, Device, MacSeen, SessionLocal, now, select  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


with SessionLocal() as db:
    cred = Credential(name="mac-cred", username="admin", password_enc=encrypt("cisco"), secret_enc=encrypt("cisco"))
    db.add(cred)
    db.flush()
    dev = Device(name="ACC-MAC", host="127.0.0.1", port=2222, device_type="cisco_ios", credential_id=cred.id)
    db.add(dev)
    db.commit()
    dev_id = dev.id

s = requests.Session()
s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
w = s.get(B + "/api/where", params={"q": "10.10.9.200"}).json()
check("sin tablas leídas: no se encuentra y lo dice", w["kind"] == "ip" and not w.get("found") and not w["cov"]["read_ago"], w)
s.post(B + f"/devices/{dev_id}/backup")
for _ in range(60):
    time.sleep(0.5)
    with SessionLocal() as db:
        if db.get(Device, dev_id).tables_at:
            break
with SessionLocal() as db:
    rows = list(db.scalars(select(MacSeen).where(MacSeen.device_id == dev_id)))
check("el respaldo guarda la tabla MAC (sin la CPU) y la ARP", sorted((r.kind, r.mac, r.port) for r in rows) == [
    ("arp", "001122334401", "Vlan10"), ("arp", "F439091A2B3C", "Vlan10"), ("mac", "001122334401", "Gi0/1"),
    ("mac", "F439091A2B3C", "Gi0/3")], [(r.kind, r.mac, r.port) for r in rows])

w = s.get(B + "/api/where", params={"q": "10.10.9.200"}).json()
f = w.get("found") or [{}]
check("por IP: switch, puerto de acceso y VLAN", len(w.get("found", [])) == 1 and f[0].get("device") == "ACC-MAC"
      and f[0].get("port") == "Gi0/3" and f[0].get("vlan") == "10", w)
check("por IP: la MAC sale de la tabla ARP", w.get("mac") == "F4:39:09:1A:2B:3C" and w.get("arp", {}).get("device") == "ACC-MAC", w)
check("cobertura: tablas leídas en 1 de 1 equipos", w["cov"]["read_n"] == 1 and w["cov"]["total_n"] >= 1 and w["cov"]["read_ago"], w["cov"])
for q in ("f4:39:09:1a:2b:3c", "f439.091a.2b3c", "F4-39-09-1A-2B-3C"):
    w2 = s.get(B + "/api/where", params={"q": q}).json()
    check(f"por MAC ({q})", w2["kind"] == "mac" and [x["port"] for x in w2.get("found", [])] == ["Gi0/3"]
          and w2.get("ip") == "10.10.9.200", w2)
w = s.get(B + "/api/where", params={"q": "10.10.9.1"}).json()
check("una MAC que solo se ve en un uplink (con vecino CDP) no se muestra como puerto de acceso", w.get("found") == [], w)

with SessionLocal() as db:  # el mismo equipo visto también en otro switch: dos puertos de acceso
    d2 = Device(name="ACC-B", host="192.0.2.80", device_type="cisco_ios", tables_at=now())
    db.add(d2)
    db.flush()
    db.add(MacSeen(device_id=d2.id, kind="mac", mac="F439091A2B3C", port="Gi1/0/5", vlan="10", first_seen=now(), last_seen=d2.tables_at))
    db.add(MacSeen(device_id=d2.id, kind="mac", mac="AABBCC000001", port="Gi1/0/9", vlan="20",
                   first_seen=now() - timedelta(days=3), last_seen=now() - timedelta(days=2)))
    db.commit()
    d2_id = d2.id
w = s.get(B + "/api/where", params={"q": "10.10.9.200"}).json()
check("aparece en 2 puertos de acceso, el más reciente primero", [x["device"] for x in w.get("found", [])] == ["ACC-B", "ACC-MAC"], w)
w = s.get(B + "/api/where", params={"q": "aa:bb:cc:00:00:01"}).json()
check("no conectado ahora: «Visto por última vez»", w.get("found") == [] and w.get("last", {}).get("port") == "Gi1/0/9", w)
w = s.get(B + "/api/where", params={"q": "f4:39:09:1a"}).json()
check("MAC incompleta: formato no válido", w["kind"] == "bad", w)
w = s.get(B + "/api/where", params={"q": "SW-CORE"}).json()
check("texto normal: no es MAC ni IP", w["kind"] == "", w)
check("ACC-B (sin credencial) sale en «Sin tablas MAC» solo si falla; aquí tiene tablas", all(m["name"] != "ACC-B" for m in w.get("cov", {}).get("missing", [])))

r = s.post(B + "/api/where/refresh").json()
check("«Leer las tablas ahora» lee los equipos que las entregan", r.get("ok") and r.get("n", 0) >= 1 and r.get("ok", 0), r)
page = s.get(B + "/").text
check("barra superior: «Buscar equipo, IP, MAC o texto…»", "Buscar equipo, IP, MAC o texto…" in page)

with SessionLocal() as db:
    for i in (dev_id, d2_id):
        db.delete(db.get(Device, i))
    db.commit()
print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
