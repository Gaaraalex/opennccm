"""Informes › Equipos del mapa: los vecinos que se ven en el mapa cruzados con Dispositivos (agregados y respaldados o
no), sin IP ni MAC, y un PDF con un campo para escribir la IP de los que faltan."""
import base64
import json
import os
import re
import sys
import tempfile
import zlib

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import mapreport  # noqa: E402
from app.db import Device, SessionLocal, init_db, now  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


class U:
    username, role, id = "admin", "admin", 1


init_db()
NB = [{"remote_name": "SW-NUEVO.lan", "remote_ip": "10.9.9.50", "mac": "AA:BB:CC:00:00:01", "local_port": "ether2",
       "platform": "MikroTik CRS326-24S+2Q+", "proto": "mndp"},
      {"remote_name": "R2", "remote_ip": "10.9.9.2", "local_port": "ether3", "platform": "MikroTik"},
      {"remote_name": "AP-OFICINA", "remote_ip": "10.9.9.60", "local_port": "ether4", "platform": "MikroTik RouterOS"},
      {"remote_name": "", "remote_ip": "10.9.9.70", "local_port": "ether5", "platform": "MikroTik hAP"},
      {"remote_name": "R-ROMON", "mac": "AA:BB:CC:00:00:09", "romon": True, "platform": "RouterOS"}]
with SessionLocal() as d:
    d.add(Device(name="R1", host="10.9.9.1", device_type="mikrotik_routeros", neighbors=json.dumps(NB), last_status="ok",
                 last_backup_at=now(), model="RB5009"))
    d.add(Device(name="R2", host="10.9.9.2", device_type="mikrotik_routeros", last_status="error"))
    d.add(Device(name="R3", host="10.9.9.3", device_type="mikrotik_routeros",
                 neighbors=json.dumps([{"remote_name": "SW-NUEVO", "local_port": "sfp1", "platform": "MikroTik"}])))
    d.commit()

m = mapreport.collect(U())
by = {r["name"]: r for r in m["rows"]}
check("vecino que no está en Dispositivos: «No está agregado», visto desde quien lo ve (con el puerto)",
      by.get("SW-NUEVO", {}).get("state") == "new" and by["SW-NUEVO"]["seen"] == ["R1 · ether2", "R3 · sfp1"], by.get("SW-NUEVO"))
check("el que ya está en Dispositivos no se repite como nuevo", sum(1 for r in m["rows"] if r["name"] == "R2") == 1 and by["R2"]["state"] == "fail")
check("los de RoMON también cuentan", by.get("R-ROMON", {}).get("state") == "new" and by["R-ROMON"]["seen"] == ["R1 (RoMON)"])
check("uno sin nombre se nombra por lo que es, no por su IP", any(r["name"].startswith("Sin nombre (MikroTik hAP)") for r in m["rows"]),
      [r["name"] for r in m["rows"]])
check("estados de los agregados: respaldado, último respaldo fallido y sin respaldo",
      by["R1"]["state"] == "ok" and by["R2"]["state"] == "fail" and by["R3"]["state"] == "never")
check("los que faltan van primero", m["rows"][0]["state"] == "new" and m["n"] == {"new": 4, "fail": 1, "never": 1, "ok": 1}, m["n"])
check("ni IP ni MAC en el informe", "10.9.9" not in json.dumps(m["rows"], default=str) and "AA:BB" not in json.dumps(m["rows"], default=str))

pdf = mapreport.pdf(m, U())
# sin dependencias: los campos del formulario (/T (ip_N)) y el texto de las páginas (sus flujos comprimidos con zlib)
fields = {x.decode() for x in re.findall(rb"/T \((ip_\d+)\)", pdf)}
streams = []
for raw in re.findall(rb"stream\r?\n(.*?)endstream", pdf, re.S):
    data = raw.strip()
    try:  # reportlab: ASCII85 y después zlib
        data = base64.a85decode(data[:-2] if data.endswith(b"~>") else data)
    except ValueError:
        pass
    try:
        streams.append(zlib.decompress(data).decode("latin-1"))
    except zlib.error:
        streams.append(data.decode("latin-1"))
text = "".join(re.findall(r"\((.*?)\) Tj", "".join(streams)))
check("PDF: un campo para escribir la IP de cada uno de los que faltan", sorted(fields) == ["ip_1", "ip_2", "ip_3", "ip_4"], sorted(fields))
check("PDF: sin IP ni MAC de los equipos", "10.9.9" not in text and "AA:BB" not in text)
check("PDF: dice el estado de los agregados", "Agregado y respaldado" in text and "Agregado, el " in text)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
