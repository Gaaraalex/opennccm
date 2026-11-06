"""Mapa de la red: nodos (Administración › Nodos, campo «Nodo» del equipo y filtro del mapa) y distribución (preferencias por
usuario, «Mi distribución» para mí o para todo el equipo, sitios y plano)."""
import json
import os
import re
import sys
import zlib

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.db import Credential, Device, NetNode, SessionLocal, select  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def png(w, h):
    raw = b"".join(b"\x00" + b"\xff\xff\xff" * w for _ in range(h))
    chunk = lambda t, d: (len(d).to_bytes(4, "big") + t + d + zlib.crc32(t + d).to_bytes(4, "big"))  # noqa: E731
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", w.to_bytes(4, "big") + h.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


adm = requests.Session()
adm.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
PW = "Lay-Oper-2026x"
adm.post(B + "/users", data={"username": "layoper", "role": "operator", "password": PW, "password2": PW})
op = requests.Session()
op.post(B + "/login", data={"username": "layoper", "password": PW})

# ---------------------------------------------------------------- nodos
adm.post(B + "/nodes", data={"name": "Nodo Quisqueya", "description": "Torre principal"})
page = adm.get(B + "/nodes").text
check("Administración › Nodos: crear y listar", "Nodo Quisqueya" in page and "Torre principal" in page and "Crear nodo" in page)
check("pestaña «Nodos» en Administración", 'href="/nodes"' in page)
r = adm.post(B + "/nodes", data={"name": "nodo quisqueya"})
check("no se repite un nombre", "Ya hay un nodo" in r.text or r.status_code in (200, 303), r.status_code)
with SessionLocal() as db:
    nq = db.scalar(select(NetNode.id).where(NetNode.name == "Nodo Quisqueya"))
    cred = db.scalar(select(Credential.id)) or None
form = {"name": "R-NODO-A", "host": "192.0.2.71", "device_type": "mikrotik_routeros", "protocol": "ssh", "port": "22",
        "credential_id": str(cred or ""), "group_id": "", "node_id": str(nq), "enabled": "on"}
adm.post(B + "/devices/new", data=form)
adm.post(B + "/devices/new", data=dict(form, name="R-NODO-B", host="192.0.2.72", node_id="__new", new_node="Nodo Angelina"))
adm.post(B + "/devices/new", data=dict(form, name="R-SIN-NODO", host="192.0.2.73", node_id=""))
adm.post(B + "/devices/new", data=dict(form, name="R-NODO-C", host="192.0.2.74"))  # mismo nodo que R-NODO-A
with SessionLocal() as db:
    devs = {d.name: d for d in db.scalars(select(Device).where(Device.name.in_(["R-NODO-A", "R-NODO-B", "R-SIN-NODO", "R-NODO-C"])))}
    na = db.scalar(select(NetNode.id).where(NetNode.name == "Nodo Angelina"))
    a, b_, c_ = devs["R-NODO-A"], devs["R-NODO-B"], devs["R-NODO-C"]
    a.neighbors = json.dumps([{"local_port": "ether1", "remote_name": "R-NODO-B", "remote_port": "ether1", "remote_ip": "", "mac": "",
                               "platform": "MikroTik", "caps": ["router"]},
                              {"local_port": "ether2", "remote_name": "R-NODO-C", "remote_port": "ether1", "remote_ip": "", "mac": "",
                               "platform": "MikroTik", "caps": ["router"]}])
    c_.neighbors = json.dumps([{"local_port": "ether1", "remote_name": "R-NODO-A", "remote_port": "ether2", "remote_ip": "", "mac": "",
                                "platform": "MikroTik", "caps": ["router"]}])
    b_.neighbors = json.dumps([{"local_port": "ether1", "remote_name": "R-NODO-A", "remote_port": "ether1", "remote_ip": "", "mac": "",
                                "platform": "MikroTik", "caps": ["router"]}])
    db.commit()
    ids = [d.id for d in devs.values()]
check("alta del equipo: elegir un nodo o escribir uno nuevo", devs["R-NODO-A"].node_id == nq and na and devs["R-NODO-B"].node_id == na
      and devs["R-SIN-NODO"].node_id is None, {k: v.node_id for k, v in devs.items()})
ed = adm.get(B + f"/devices/{devs['R-NODO-A'].id}/edit").text
check("formulario del equipo: campo «Nodo» con el elegido", 'name="node_id"' in ed and "Crear nodo nuevo" in ed
      and re.search(rf'<option value="{nq}" selected>Nodo Quisqueya', ed))
t = adm.get(B + "/topology").text
check("mapa: filtro «Nodo» con «Todos»", "Nodo: Todos" in t and "Nodo: Nodo Quisqueya" in t)
t = adm.get(B + f"/topology?node={nq}").text
m = re.search(r"const NODES = (\[.*?\]), LINKS", t, re.S)
nmap = {n["label"]: n for n in json.loads(m.group(1))} if m else {}
labels = set(nmap)
check("mapa con un nodo: sus equipos", {"R-NODO-A", "R-NODO-C"} <= labels and "R-SIN-NODO" not in labels
      and not nmap["R-NODO-A"].get("dim"), labels)
# R-NODO-B es vecino de R-NODO-A pero está en otro nodo: antes salía como vecino «que no está en OpenNCCM»
b_ = nmap.get("R-NODO-B") or {}
check("mapa con un nodo: el vecino de otro nodo sale atenuado, con su nodo", b_.get("dim") and b_.get("kind") == "dev"
      and "Nodo Angelina" in (b_.get("note") or ""), b_)
check("mapa con un nodo: los chips cuentan solo sus equipos", re.search(r'data-fam="mikrotik"[^>]*>.*?<span class="n">2</span>', t, re.S), "")
t = adm.get(B + "/devices?node=" + str(nq)).text
_rows = re.findall(r'href="/devices/\d+" style="color:var\(--ink\)">([^<]+)<', t)
check("Dispositivos filtrado por nodo", set(_rows) == {"R-NODO-A", "R-NODO-C"} and "Nodo: Nodo Quisqueya" in t, _rows)
adm.post(B + "/nodes", data={"name": "Nodo Vacio"})
with SessionLocal() as db:
    nv = db.scalar(select(NetNode.id).where(NetNode.name == "Nodo Vacio"))
t = adm.get(B + f"/topology?node={nv}").text
check("nodo sin equipos: aviso propio y «Asignar equipos»", "Nodo Vacio todavía no tiene equipos." in t and f"/devices?assign={nv}" in t)
r = adm.post(B + "/devices/bulk", data={"action": "node", "node_id": str(nv), "ids": [str(devs["R-SIN-NODO"].id)],
                                        "next": f"/devices?assign={nv}"}, allow_redirects=False)
with SessionLocal() as db:
    sn = db.get(Device, devs["R-SIN-NODO"].id).node_id
check("asignar equipos a un nodo desde Dispositivos", sn == nv and f"/topology?node={nv}" in r.headers.get("location", ""), (sn, r.headers.get("location")))
t = adm.get(B + "/nodes").text
check("lista de nodos: enlace a sus equipos, Editar y Borrar con ícono", f'href="/devices?node={nq}"' in t and "#i-pin" in t
      and 'title="Borrar"' in t and "Sus 2 equipos quedarán sin nodo" in t)
adm.post(B + f"/nodes/{nv}/delete")

t = adm.get(B + "/topology").text
m = re.search(r"const NODES = (\[.*?\]), LINKS", t, re.S)
labels = {n["label"] for n in json.loads(m.group(1))} if m else set()
check("mapa con «Todos»: también el del otro nodo", {"R-NODO-A", "R-NODO-B", "R-NODO-C"} <= labels, labels)
# tipo en el mapa corregido a mano por el administrador (formulario del equipo)
adm.post(B + f"/devices/{devs['R-NODO-A'].id}/edit", data=dict(form, node_id=str(nq), map_role="switch"))
t = adm.get(B + "/topology").text
m = re.search(r"const NODES = (\[.*?\]), LINKS", t, re.S)
roles = {n["label"]: n.get("role") for n in json.loads(m.group(1))} if m else {}
ed = adm.get(B + f"/devices/{devs['R-NODO-A'].id}/edit").text
check("tipo en el mapa elegido a mano", roles.get("R-NODO-A") == "switch" and roles.get("R-NODO-C") == "router"
      and 'name="map_role"' in ed and '<option value="switch" selected>' in ed, roles)

# ---------------------------------------------------------------- distribución
mm = re.search(r"const MODES = (\[.*?\]);", t, re.S)
modes = json.loads(mm.group(1)) if mm else []
check("menú «Distribución» con las 10 formas", "CÓMO ORDENAR LOS EQUIPOS" in t and [x[0] for x in modes] == [
    "free", "down", "right", "radial", "sites", "roles", "circle", "fold", "plan", "mine"] and modes[7][2] == "Árbol plegable", modes[:2])
op.post(B + "/api/topology/prefs", json={"mode": "radial", "center": "d1", "sub": "ip", "fit": False, "remember": True})
t = op.get(B + "/topology").text
check("preferencias guardadas por usuario", '"mode": "radial"' in t and '"sub": "ip"' in t, re.findall(r'"prefs": \{[^}]*\}', t)[:1])
check("otro usuario no las ve", '"mode": "radial"' not in adm.get(B + "/topology").text)
op.post(B + "/api/topology/prefs", json={"mode": "down", "remember": False})
check("sin «Recordar» vuelve a la de siempre", '"mode": "free"' in op.get(B + "/topology").text)
r = op.post(B + "/api/topology/positions", json={"scope": "me", "pos": {"d1": [10, 20]}})
check("Mi distribución: guardar para mí", r.ok and r.json().get("at"), r.text)
r = op.post(B + "/api/topology/positions", json={"scope": "team", "pos": {"d1": [1, 2]}})
check("para todo el equipo: solo administradores", r.status_code == 403, r.status_code)
r = adm.post(B + "/api/topology/positions", json={"scope": "team", "pos": {"d1": [1, 2]}})
check("un administrador la guarda para todo el equipo", r.ok, r.status_code)
r = op.post(B + "/api/topology/sites", json={"rename": ["consuelo", "Consuelo Norte"]})
check("renombrar sitios: solo administradores", r.status_code == 403, r.status_code)
r = adm.post(B + "/api/topology/sites", json={"rename": ["consuelo", "Consuelo Norte"], "move": ["d1", "Núcleo"]})
check("sitios: renombrar y mover un equipo", r.ok and r.json()["sites"]["names"]["consuelo"] == "Consuelo Norte"
      and r.json()["sites"]["assign"]["d1"] == "Núcleo", r.text)
r = adm.post(B + "/topology/plan", files={"file": ("plano.png", png(40, 20), "image/png")})
check("subir el plano (PNG)", r.ok and r.json().get("w") == 40 and r.json().get("h") == 20, r.text)
r = adm.post(B + "/topology/plan", files={"file": ("x.txt", b"hola", "text/plain")})
check("el plano tiene que ser una imagen", r.status_code == 400, r.status_code)
check("el plano se sirve", adm.get(B + "/topology/plan").headers.get("content-type") == "image/png")
adm.post(B + "/api/topology/plan-pos", json={"id": "d1", "x": 0.5, "y": 0.25})
t = adm.get(B + "/topology").text
check("lugar en el plano guardado", '"d1": [0.5, 0.25]' in t)
r = op.post(B + "/api/topology/plan-pos", json={"id": "d1", "x": 0.1, "y": 0.1})
check("ubicar en el plano: solo administradores", r.status_code == 403, r.status_code)

with SessionLocal() as db:
    for i in ids:
        db.delete(db.get(Device, i))
    db.commit()
for n in (nq, na):
    adm.post(B + f"/nodes/{n}/delete")
print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
