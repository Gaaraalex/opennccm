"""Una solicitud de cambio con direcciones de ejemplo (192.0.2.x, 198.51.100.x, 203.0.113.x) no se puede aprobar ni se
ejecuta sola aunque la aprobación esté desactivada; sí se puede rechazar. Requiere haber ejecutado antes e2e.py."""
import json
import os
import re
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.db import ChangeRequest, Device, SessionLocal, get_setting, select, set_setting  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def flashes(html):
    return " ".join(re.sub(r"<[^>]+>", "", m) for m in re.findall(r'class="flash[^"]*"[^>]*>(.*?)</div>', html, re.S))


adm = requests.Session()
adm.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
with SessionLocal() as db:
    dev_id = db.scalars(select(Device).order_by(Device.id)).first().id
    cr = ChangeRequest(title="Corrección 'Envío de logs a syslog' en prueba", requested_by="otro",
                       body="syslog host 1 address 192.0.2.10\nsyslog host 1 enable", variables="{}",
                       device_ids=json.dumps([dev_id]), status="pending")
    db.add(cr)
    db.commit()
    cr_id = cr.id

page = adm.get(f"{B}/changes/{cr_id}").text
check("la solicitud avisa de la dirección de ejemplo", "Usa direcciones de ejemplo" in page and "192.0.2.10" in page)
check("el botón «Aprobar y ejecutar» está desactivado",
      re.search(r'value="approve" disabled', page) is not None)
r = adm.post(f"{B}/changes/{cr_id}/review", data={"decision": "approve"})
with SessionLocal() as db:
    st = db.get(ChangeRequest, cr_id).status
check("aprobarla igualmente (sin el botón) se rechaza con una explicación",
      st == "pending" and "No se puede aprobar" in flashes(r.text), (st, flashes(r.text)))
r = adm.post(f"{B}/changes/{cr_id}/review", data={"decision": "reject", "comment": "Dirección de ejemplo"})
with SessionLocal() as db:
    st = db.get(ChangeRequest, cr_id).status
check("sí se puede rechazar", st == "rejected", st)

# un cambio que apaga Telnet en un equipo al que OpenNCCM entra por Telnet no se puede aprobar
with SessionLocal() as db:
    d = db.get(Device, dev_id)
    old_proto = d.protocol
    d.protocol = "telnet"
    cr = ChangeRequest(title="Corrección 'Sin Telnet' en prueba", requested_by="otro", body="no ip telnet server enable",
                       variables="{}", device_ids=json.dumps([dev_id]), status="pending")
    db.add(cr)
    db.commit()
    tel_id = cr.id
try:
    page = adm.get(f"{B}/changes/{tel_id}").text
    check("aviso: apagar Telnet dejaría a OpenNCCM sin acceso", "Dejaría a OpenNCCM sin acceso" in page and "por Telnet" in page)
    check("y el botón de aprobar está desactivado", re.search(r'value="approve" disabled', page) is not None)
    r = adm.post(f"{B}/changes/{tel_id}/review", data={"decision": "approve"})
    with SessionLocal() as db:
        st = db.get(ChangeRequest, tel_id).status
    check("aprobarlo igualmente se rechaza", st == "pending" and "No se puede aprobar" in flashes(r.text), (st, flashes(r.text)))
    adm.post(f"{B}/changes/{tel_id}/review", data={"decision": "reject", "comment": "Entra por Telnet"})
finally:
    with SessionLocal() as db:
        db.get(Device, dev_id).protocol = old_proto
        db.commit()

# con la aprobación desactivada, una corrección con dirección de ejemplo no se ejecuta sola
old = get_setting("require_approval", "1")
set_setting("require_approval", "0")
try:
    from app.main import _create_cr

    class U:
        username = "admin"
    new_id = _create_cr(U(), "Prueba", "logging host 203.0.113.7", {}, [dev_id])
    with SessionLocal() as db:
        st = db.get(ChangeRequest, new_id).status
    check("con la aprobación desactivada, queda pendiente en lugar de ejecutarse", st == "pending", st)
finally:
    set_setting("require_approval", old)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
