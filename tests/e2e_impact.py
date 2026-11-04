"""Análisis de impacto, Puertos sin uso y Documento de la red contra la aplicación en marcha (sin equipos reales).

Red de ejemplo (en la base de datos, con sus vecinos LLDP/CDP, puertos y configuración): IMP-CORE-1 e IMP-CORE-2 enlazados;
IMP-DIST solo a IMP-CORE-1 e IMP-ACC detrás; IMP-SUC (router de otra ubicación) a IMP-CORE-1; IMP-DUAL a los dos núcleos."""
import io
import json
import os
import re
import sys
import zipfile
from datetime import timedelta

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.crypto import encrypt  # noqa: E402
from app.db import AuditLog, ChangeRequest, ConfigVersion, Device, FirmwareImage, SessionLocal, now, select  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def login(u, p):
    s = requests.Session()
    s.post(B + "/login", data={"username": u, "password": p})
    return s


LINKS = [("IMP-CORE-1", "Gi1/1/1", "IMP-CORE-2", "Gi1/1/1"), ("IMP-CORE-1", "Gi1/1/2", "IMP-DIST", "Gi1/1/1"),
         ("IMP-DIST", "Gi1/1/2", "IMP-ACC", "Gi1/1/1"), ("IMP-CORE-1", "Gi1/0/2", "IMP-SUC", "Gi0/0/0"),
         ("IMP-CORE-1", "Gi1/1/3", "IMP-DUAL", "Gi1/1/1"), ("IMP-CORE-2", "Gi1/1/3", "IMP-DUAL", "Gi1/1/2")]
T = now()
ids = {}
with SessionLocal() as db:
    for n in ("IMP-CORE-1", "IMP-CORE-2", "IMP-DIST", "IMP-ACC", "IMP-SUC", "IMP-DUAL"):
        d = db.scalar(select(Device).where(Device.name == n))
        if d is None:
            d = Device(name=n, host=f"198.18.7.{len(ids) + 1}", device_type="cisco_xe",
                       model="ISR1111-4P" if n == "IMP-SUC" else "C9300-48P", os_version="17.9.4",
                       location="Sucursal" if n == "IMP-SUC" else "Sede", last_status="ok", last_backup_at=T - timedelta(minutes=5))
            db.add(d)
            db.flush()
        ids[n] = d.id
    nb = {n: [] for n in ids}
    for a, pa, b, pb in LINKS:
        nb[a].append({"remote_name": b, "local_port": pa, "remote_port": pb, "platform": "cisco C9300-48P" if b != "IMP-SUC" else "cisco ISR1111-4P"})
        nb[b].append({"remote_name": a, "local_port": pb, "remote_port": pa, "platform": "cisco C9300-48P"})
    for n, i in ids.items():
        db.get(Device, i).neighbors = json.dumps(nb[n])
    # IMP-ACC: 8 puertos de acceso; 3 sin enlace hace 100 días, 1 «nunca», 1 apagado (libre), 1 con 802.1X, 1 troncal
    acc = db.get(Device, ids["IMP-ACC"])
    st = {1: "up", 2: "down", 3: "down", 4: "down", 5: "down", 6: "off", 7: "down", 8: "down"}
    ports = [{"n": f"Gi1/0/{k}", "k": "cu", "s": s, "sp": "", "d": f"Puesto {k}", "net": "trunk" if k == 8 else "10", "mod": "", "st": ""}
             for k, s in st.items()] + [{"n": "Gi1/1/1", "k": "sfp", "s": "up", "sp": "10G", "d": "", "net": "trunk", "mod": "", "st": ""}]
    old = (T - timedelta(days=100)).isoformat(timespec="minutes")
    seen = {"Gi1/0/1": T.isoformat(timespec="minutes"), "Gi1/0/2": old, "Gi1/0/3": old, "Gi1/0/4": old, "Gi1/0/5": None,
            "Gi1/0/6": None, "Gi1/0/7": old, "Gi1/0/8": old, "Gi1/1/1": T.isoformat(timespec="minutes")}
    acc.ports = json.dumps({"at": T.isoformat(), "status": True, "ports": ports, "rear": {}, "raw": ""})
    acc.port_seen, acc.port_seen_since = json.dumps(seen), T - timedelta(days=120)
    cfg = ("hostname IMP-ACC\nsnmp-server community SECRETO-SNMP RO\nvlan 10\n name DATOS\n!\n"
           "interface GigabitEthernet1/0/7\n description Puesto 7\n authentication port-control auto\n dot1x pae authenticator\n!\n"
           "interface GigabitEthernet1/0/8\n switchport mode trunk\n!\ninterface Vlan10\n ip address 10.77.0.1 255.255.255.0\n!\n"
           "interface GigabitEthernet0/0/0\n ip address 200.55.10.9 255.255.255.252\n!\nend\n")
    db.add(ConfigVersion(device_id=acc.id, kind="running", content_enc=encrypt(cfg), sha256="x" * 64, size=len(cfg), trigger="manual",
                         username="admin"))
    cr = ChangeRequest(title="Reiniciar IMP-CORE-1", body="reload in 1", variables="{}", device_ids=json.dumps([ids["IMP-CORE-1"]]),
                       requested_by="otro", kind="config")
    fw = ChangeRequest(title="Firmware a 3 equipos", body="", variables="{}", requested_by="otro", kind="firmware", reload=True,
                       device_ids=json.dumps([ids["IMP-CORE-1"], ids["IMP-DIST"], ids["IMP-DUAL"]]))
    db.add_all([cr, fw])
    if not db.scalar(select(FirmwareImage).where(FirmwareImage.filename == "imp-prueba.bin")):  # para ver el formulario de la ficha
        db.add(FirmwareImage(filename="imp-prueba.bin", version="17.12.3", md5="0" * 32))
    db.commit()
    cr_id, fw_id = cr.id, fw.id

adm = login("admin", "Admin12345")
# ---- análisis de impacto
j = adm.get(B + f"/api/impact/{ids['IMP-CORE-1']}").json()
check("impacto de reiniciar IMP-CORE-1: aísla IMP-DIST e IMP-ACC y corta el router de la sucursal",
      sorted(x["name"] for x in j.get("isolated", [])) == ["IMP-ACC", "IMP-DIST"] and [x["name"] for x in j.get("branches", [])] == ["IMP-SUC"]
      and j.get("level") == "high", j.get("sentence"))
check("…los que tienen dos uplinks siguen por otro camino", set(j.get("kept", [])) >= {"IMP-CORE-2", "IMP-DUAL"}, j.get("kept"))
page = adm.get(B + f"/changes/{cr_id}").text
check("solicitud: tarjeta de impacto con la frase, el mini-mapa y «Leí el impacto»",
      "Impacto alto" in page and "imp-map" in page and 'name="ack_impact"' in page and "data-need-ack" in page)
adm.post(B + f"/changes/{cr_id}/review", data={"decision": "approve", "comment": ""})
with SessionLocal() as db:
    check("sin marcar «Leí el impacto» no se aprueba", db.get(ChangeRequest, cr_id).status == "pending")
when = (now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
adm.post(B + f"/changes/{cr_id}/review", data={"decision": "approve", "comment": "", "ack_impact": "on", "when": "later", "run_at": when})
with SessionLocal() as db:
    c = db.get(ChangeRequest, cr_id)
    check("aprobada para la ventana de mantenimiento: queda aprobada con su hora y no se ejecuta antes",
          c.status == "approved" and c.run_at is not None and c.executed_at is None, (c.status, c.run_at))
    a = db.scalar(select(AuditLog).where(AuditLog.action == "change_approved", AuditLog.detail.like(f"#{cr_id} %")))
    check("Auditoría: la aprobación guarda el impacto que se mostró", a is not None and "Impacto alto" in a.detail and "IMP-DIST" in a.detail,
          a.detail if a else None)
page = adm.get(B + f"/changes/{fw_id}").text
check("firmware de varios equipos con reinicio: orden sugerido, de a uno los que aíslan",
      "Orden sugerido" in page and "imp-plan" in page, page[:0])
order = re.findall(r'<td class="ord">(\d+)</td><td><b>([^<]+)</b>', page)
check("…primero el que no aísla a nadie (IMP-DUAL), IMP-CORE-1 nunca junto con IMP-DIST",
      order and order[0][1] == "IMP-DUAL" and dict((b, a) for a, b in order).get("IMP-CORE-1") != dict((b, a) for a, b in order).get("IMP-DIST"), order)
dev = adm.get(B + f"/devices/{ids['IMP-CORE-1']}").text
check("ficha: el formulario de firmware avisa del impacto al marcar «Reiniciar»", 'fw-imp' in dev and "/api/impact/" in dev)
check("mapa de la red: «¿Qué se cae?» en el panel de cada equipo", "data-falls" in adm.get(B + "/topology").text)

# ---- puertos sin uso
p = adm.get(B + f"/reports/unused?days=90&sw={ids['IMP-ACC']}").text
check("Informes › Puertos sin uso: IMP-ACC con los 3 puertos sin enlace hace 100 días",
      "IMP-ACC" in p and all(f'value="Gi1/0/{k}"' in p for k in (2, 3, 4)) and "100 días" in p)
check("…excluye la troncal y el puerto con 802.1X, y el apagado cuenta como libre",
      'value="Gi1/0/8"' not in p and 'value="Gi1/0/7"' not in p and 'value="Gi1/0/6"' not in p)
check("…«nunca» solo con datos suficientes: Gi1/0/5 aparece con su fecha de inicio", 'value="Gi1/0/5"' in p and "datos desde" in p)
csv = adm.get(B + "/reports/unused/csv?days=90").text
check("…exportar CSV", "IMP-ACC" in csv and "Gi1/0/2" in csv)
pr = adm.post(B + "/reports/unused/preview", json={"device_id": ids["IMP-ACC"], "ports": ["Gi1/0/2", "Gi1/0/3"]}).json()
check("«shutdown»: la configuración del fabricante, con la marca SIN-USO", "interface Gi1/0/2" in pr.get("commands", "")
      and "shutdown" in pr["commands"] and "SIN-USO-" in pr["commands"] and pr.get("prev", {}).get("Gi1/0/2") == "Puesto 2", pr)
sh = adm.post(B + "/reports/unused/shutdown", json={"device_id": ids["IMP-ACC"], "ports": ["Gi1/0/2", "Gi1/0/3"], "days": 90}).json()
check("…se crea como solicitud de cambio (a aprobar)", sh.get("ok") and "/changes/" in sh.get("url", ""), sh)
with SessionLocal() as db:
    c = db.get(ChangeRequest, sh.get("id") or 0)
    check("…con la descripción anterior guardada para revertir", c is not None and json.loads(c.variables).get("_prev_desc", {}).get("Gi1/0/3") == "Puesto 3")
cp = adm.get(B + sh.get("url", "/")).text
check("…y su propio análisis de impacto (puertos sin enlace: sin impacto)", "Sin impacto" in cp and "Si apagas estos 2 puertos" in cp)
adm.post(B + "/reports/unused/except", json={"device_id": ids["IMP-ACC"], "ports": ["Gi1/0/4"]})
check("excepción: el puerto marcado ya no se propone", 'value="Gi1/0/4"' not in adm.get(B + f"/reports/unused?days=90&sw={ids['IMP-ACC']}").text)
ro = login("admin", "Admin12345")
check("Informes: tarjetas de Puertos sin uso y Documento de la red", "/reports/unused" in ro.get(B + "/reports").text
      and "/reports/netdoc" in ro.get(B + "/reports").text)

# ---- documento de la red
base = {"scope": "all", "cover": "on", "client": "Cliente de prueba", "hide_public": "on",
        **{f"sec_{k}": "on" for k in ("inventory", "map", "vlans", "links", "compliance", "backups")}}
for fmt in ("pdf", "docx"):
    r = adm.post(B + "/reports/netdoc", data={**base, "fmt": fmt})
    check(f"documento de la red en {fmt}: se genera", "Listo: Documento-red_Toda-la-red_" in r.text, r.text[-300:])
nd = adm.get(B + "/reports/netdoc").text
docs = re.findall(r"/reports/netdoc/([0-9]{14}-[0-9a-f]{6})/file", nd)
files = {}
for i in dict.fromkeys(docs):
    r = adm.get(B + f"/reports/netdoc/{i}/file")
    files[r.headers.get("content-type", "")[:15]] = r.content
pdf = files.get("application/pdf", b"")
docx = next((v for k, v in files.items() if k.startswith("application/vnd")), b"")
check("PDF real (empieza con %PDF y tiene páginas)", pdf.startswith(b"%PDF") and b"/Type /Page" in pdf)
try:
    z = zipfile.ZipFile(io.BytesIO(docx))
    body = z.read("word/document.xml").decode()
    okz = "word/media/mapa.png" in z.namelist()
except (zipfile.BadZipFile, KeyError):
    body, okz = "", False
check("Word real (.docx con el mapa como imagen)", okz and "IMP-ACC" in body and "Inventario" in body)
check("nunca salen secretos ni, si se pide, las IP públicas", "SECRETO-SNMP" not in body and "200.55.10.9" not in body
      and "IP pública oculta" in body and b"SECRETO-SNMP" not in pdf)
with SessionLocal() as db:
    db.add(Device(name="IMP-NUEVO", host="198.18.7.99", device_type="cisco_xe", last_status="ok"))
    db.commit()
check("«Documentos generados» avisa si la red cambió desde entonces", "La red cambió: 1 equipo nuevo" in adm.get(B + "/reports/netdoc").text)
check("descargar un documento ajeno a la carpeta: no", adm.get(B + "/reports/netdoc/..%2F..%2Fsecret/file").status_code in (403, 404, 422))
# ---- RoMON en el mapa: un MikroTik a dos saltos, unido al salto anterior con una línea punteada
with SessionLocal() as db:
    d = db.get(Device, ids["IMP-SUC"])
    nbx = json.loads(d.neighbors) + [
        {"local_port": "", "remote_name": "IMP-ROMON-1", "remote_port": "", "remote_ip": "", "mac": "6C:3B:6B:00:00:01",
         "platform": "MikroTik CRS326", "romon": True, "hops": 1, "via": "", "version": "7.16"},
        {"local_port": "", "remote_name": "IMP-ROMON-2", "remote_port": "", "remote_ip": "", "mac": "6C:3B:6B:00:00:02",
         "platform": "MikroTik wAP", "romon": True, "hops": 2, "via": "6C:3B:6B:00:00:01", "version": "7.16"}]
    d.neighbors = json.dumps(nbx)
    db.commit()
topo = adm.get(B + "/topology").text
check("mapa: los MikroTik que encuentra RoMON, con línea punteada y su salto anterior",
      '"romon": true' in topo and "u:imp-romon-1|u:imp-romon-2|romon" in topo and "Por RoMON (sin puerto)" in topo)
j = adm.get(B + f"/api/impact/{ids['IMP-SUC']}").json()
check("…y RoMON no cuenta para el análisis de impacto (no dice por qué puerto)",
      "IMP-ROMON-1" not in json.dumps(j), j.get("sentence"))
print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}")
sys.exit(1 if fails else 0)
