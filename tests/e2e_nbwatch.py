"""Aviso de vecino nuevo o perdido: la primera lectura es conocida, nuevo avisa enseguida, perdido tras N lecturas,
volver cierra la alarma con la duración del corte, teléfonos y puntos de acceso no avisan; la ficha del vecino, «Es
conocido», el mapa (nuevo y perdido), la lista de conocidos y «Preparar shutdown del puerto»."""
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import nbwatch  # noqa: E402
from app.db import Device, Event, NeighborState, SessionLocal, select, set_setting  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def nb(name, port, rport="Gi0/48", caps=None, proto="cdp", ip=""):
    return {"local_port": port, "remote_name": name, "remote_port": rport, "remote_ip": ip, "mac": "", "platform": "cisco WS-C2960",
            "caps": caps or ["bridge"], "proto": proto}


A, Bn, C = nb("SW-A", "Gi0/1"), nb("SW-B", "Gi0/2"), nb("ESP-LAB", "Gi0/3", proto="lldp", ip="10.10.9.50")
PHONE = nb("SEP001122", "Gi0/4", caps=["phone", "bridge"], proto="lldp")


def read(*nbrs):
    with SessionLocal() as db:
        d = db.get(Device, DEV)
        d.neighbors = json.dumps(list(nbrs))
        r = nbwatch.update(db, d, list(nbrs), "interface GigabitEthernet0/5\n dot1x pae authenticator\n")
        db.commit()
        return r


def events():
    with SessionLocal() as db:
        return [(e.category, e.message, e.acknowledged, e.severity) for e in
                db.scalars(select(Event).where(Event.device_id == DEV, Event.category.in_((nbwatch.CAT_NEW, nbwatch.CAT_LOST)))
                           .order_by(Event.id))]


with SessionLocal() as db:
    d = Device(name="R-NB", host="192.0.2.90", device_type="cisco_ios", location="Casa matriz")
    db.add(d)
    db.commit()
    DEV = d.id
for k, v in (("nb_alerts", "1"), ("nb_new_on", "1"), ("nb_lost_on", "1"), ("nb_lost_reads", "2"), ("nb_new_sev", "warning"),
             ("nb_lost_sev", "major"), ("nb_protos", "lldp,cdp,mndp,romon"), ("nb_skip_phones", "1"), ("nb_skip_dot1x", "1")):
    set_setting(k, v)

r = read(A, Bn)
check("primera lectura: todo se toma como conocido, sin alarmas", r == {"new": 0, "lost": 0, "back": 0} and events() == [], (r, events()))
r = read(A, Bn, C)
ev = events()
check("vecino nuevo: alarma enseguida, nivel Aviso", r["new"] == 1 and len(ev) == 1 and ev[0][0] == "Vecino nuevo"
      and ev[0][1] == "Vecino nuevo en R-NB · Gi0/3\nEquipo desconocido «ESP-LAB» · LLDP" and ev[0][3] == "warning", ev)
r = read(A, C)
check("falta en 1 lectura: todavía no avisa", r["lost"] == 0 and len(events()) == 1, events())
r = read(A, C)
ev = events()
check("falta en 2 lecturas seguidas: vecino perdido, nivel Alta", r["lost"] == 1 and len(ev) == 2
      and ev[1][1] == "Vecino perdido: SW-B\nYa no se ve desde R-NB Gi0/2 · CDP" and ev[1][3] == "major", ev)
with SessionLocal() as db:
    lost_id = db.scalar(select(NeighborState.id).where(NeighborState.device_id == DEV, NeighborState.name == "SW-B"))
    new_id = db.scalar(select(NeighborState.id).where(NeighborState.device_id == DEV, NeighborState.name == "ESP-LAB"))

s = requests.Session()
s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
t = s.get(B + "/topology").text
check("mapa: el perdido se dibuja aparte con su enlace en rojo y el nuevo resaltado",
      '"chg": "lost"' in t and '"chg": "new"' in t and "Resaltar cambios de vecinos" in t and "Cambios de vecinos · últimas 24 h" in t)
page = s.get(B + f"/neighbors/{lost_id}").text
check("ficha del perdido: visto por última vez, faltó en, botones", "Vecino perdido: SW-B" in page and "Faltó en" in page
      and "2 lecturas seguidas" in page and "Ya no existe, quitar del mapa" in page and "Ver en el mapa" in page, page[:300])

r = read(A, Bn, C)
ev = events()
check("volvió: la alarma se cierra sola con la duración del corte", r["back"] == 1 and ev[1][2] is True and "Volvió a verse" in ev[1][1], ev)
r = read(A, Bn, C, PHONE)
check("un teléfono IP nuevo no avisa (No avisar por teléfonos)", r["new"] == 1 and len(events()) == 2, events())
r = read(A, Bn, C, PHONE, nb("PC-X", "Gi0/5", caps=["station"], proto="lldp"))
check("un puerto con 802.1X no avisa", r["new"] == 1 and len(events()) == 2, events())

al = s.get(B + "/events?cat=vecinos&unack=0").text
check("Alarmas: filtro «Vecinos», subtítulo y «Ver detalle»", "Vecinos" in al and "Equipo desconocido «ESP-LAB» · LLDP" in al
      and f"/neighbors/{new_id}" in al and "Un vecino perdido se avisa recién cuando falta en 2 lecturas seguidas" in al)
page = s.get(B + f"/neighbors/{new_id}").text
check("ficha del nuevo: puerto, nombre, IP y botones", "Apareció un equipo desconocido en R-NB · Gi0/3" in page and "ESP-LAB" in page
      and "10.10.9.50" in page and "Es conocido, no avisar más" in page and "Preparar «shutdown» del puerto" in page, page[:300])
r = s.post(B + f"/neighbors/{new_id}/shutdown", allow_redirects=False)
check("«Preparar shutdown del puerto» crea una solicitud de cambio", r.status_code in (302, 303) and "/changes/" in r.headers.get("location", ""), r.status_code)
s.post(B + f"/neighbors/{new_id}/known")
with SessionLocal() as db:
    st = db.get(NeighborState, new_id)
    e = db.get(Event, ev[0][0] and db.scalar(select(Event.id).where(Event.device_id == DEV).order_by(Event.id)))
check("«Es conocido»: queda en la lista y la alarma se reconoce", st.known and st.status == "ok" and e.acknowledged, (st.known, st.status))
kn = s.get(B + "/neighbors").text
check("lista de vecinos conocidos", "Vecinos conocidos" in kn and "ESP-LAB" in kn and "SW-A" in kn)
st_ = s.get(B + "/settings").text
check("Ajustes › Mapa de la red: aviso de cambios de vecinos", "Avisar cuando cambian los vecinos" in st_ and "Vecinos conocidos ·" in st_
      and "Protocolos que se vigilan" in st_ and "Teléfonos IP y puntos de acceso" in st_)

set_setting("nb_alerts", "0")
time.sleep(0.2)
r = read(A, Bn, C, PHONE, nb("SW-Z", "Gi0/6"))
check("con el aviso apagado: se registra el cambio pero no hay alarma", r["new"] == 1 and len(events()) == 2, events())
with SessionLocal() as db:
    db.delete(db.get(Device, DEV))
    db.commit()
print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
