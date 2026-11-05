"""Comandos favoritos de la consola: guardar, ordenar, quitar con «Deshacer», compartir (solo administradores), los que
cambian la configuración y los sugeridos. Requiere el usuario admin / Admin12345."""
import sys

import requests

sys.path.insert(0, ".")
from app import favorites  # noqa: E402
from app.db import Device, SessionLocal, select, set_setting  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def login(user, pw):
    s = requests.Session()
    s.post(B + "/login", data={"username": user, "password": pw})
    return s


check("familias: Cisco, MikroTik y WatchGuard", (favorites.family_of("cisco_xe"), favorites.family_of("mikrotik_routeros"),
                                                 favorites.family_of("watchguard_fireware")) == ("ios", "ros", "watchguard"))
check("cambia la configuración: conf t, copy, write, /ip address add, set", all(
    favorites.is_change(f, c) for f, c in (("ios", "conf t"), ("ios", "copy run start"), ("ios", "write memory"),
                                           ("ros", "/ip address add address=1.1.1.1/32 interface=lo"), ("watchguard", "set ip"))))
check("no cambia: show ip interface brief, /interface print", not favorites.is_change("ios", "show ip interface brief")
      and not favorites.is_change("ros", "/interface print"))

adm = login("admin", "Admin12345")
PW = "Fav-Oper-2026x"
adm.post(B + "/users", data={"username": "favoper", "role": "operator", "password": PW, "password2": PW})
r = login("favoper", PW).post(B + "/api/console/favorites", json={"name": "x", "command": "show clock", "family": "ios"})
check("sin acceso a la consola no hay favoritos", r.status_code == 403, r.status_code)
set_setting("console_role_operator", "rw")
__import__("time").sleep(31)  # la aplicación relee los ajustes como mucho cada 30 s
op = login("favoper", PW)

r = adm.post(B + "/api/console/favorites", json={"name": "Interfaces", "command": "show ip interface brief", "family": "ios", "mode": "send"})
a = r.json().get("item", {})
check("guardar un favorito", r.ok and a.get("name") == "Interfaces" and not a.get("change") and not a.get("shared"), r.text)
b = adm.post(B + "/api/console/favorites", json={"name": "Guardar", "command": "write memory", "family": "ios", "mode": "send"}).json()["item"]
check("un comando que cambia la configuración queda marcado", b["change"], b)
c = adm.post(B + "/api/console/favorites", json={"name": "Versión", "command": "show version", "family": "*", "mode": "type",
                                                 "shared": True}).json()["item"]
check("compartido por un administrador, para todos los equipos", c["shared"] and c["family"] == "*" and c["mode"] == "type", c)
j = op.post(B + "/api/console/favorites", json={"name": "Mío", "command": "show clock", "family": "ios", "shared": True}).json()
check("un operador no puede compartir", j.get("item", {}).get("shared") is False, j)
mine = j["item"]

lst = op.get(B + "/api/console/favorites?family=ios").json()
names = [i["name"] for i in lst["items"]]
check("el operador ve los suyos y los compartidos, no los de otro", names == ["Mío", "Versión"], names)
check("los compartidos no los cambia un operador", not next(i for i in lst["items"] if i["name"] == "Versión")["can_edit"])
r = op.post(B + f"/api/console/favorites/{c['id']}/delete")
check("un operador no quita uno compartido", r.status_code == 403, r.status_code)
r = op.post(B + f"/api/console/favorites/{a['id']}/delete")
check("ni uno de otro usuario", r.status_code == 403, r.status_code)

adm.post(B + "/api/console/favorites/order", json={"ids": [b["id"], a["id"]]})
names = [i["name"] for i in adm.get(B + "/api/console/favorites?family=ios").json()["items"]]
check("arrastrar cambia el orden; los de todos los equipos van al final", names == ["Guardar", "Interfaces", "Versión"], names)

d = adm.post(B + f"/api/console/favorites/{a['id']}/delete").json()
check("quitar devuelve sus datos", d.get("item", {}).get("command") == "show ip interface brief", d)
adm.post(B + "/api/console/favorites/restore", json=d["item"])
names = [i["name"] for i in adm.get(B + "/api/console/favorites?family=ios").json()["items"]]
check("«Deshacer» lo vuelve a poner en su lugar", names == ["Guardar", "Interfaces", "Versión"], names)

ck = adm.get(B + "/api/console/favorites/check", params={"family": "ros", "command": "/system reboot"}).json()
check("el formulario avisa si cambia la configuración", ck.get("change") is True, ck)
sug = adm.post(B + "/api/console/favorites/suggested", json={"family": "ros"}).json()
check("los 4 sugeridos de MikroTik", [i["command"] for i in sug["items"] if i["family"] == "ros"] ==
      ["/interface print", "/ip neighbor print", '/log print where topics~"error"', "/system resource print"], sug)

page = adm.get(B + "/console/favorites?family=ios")
check("ventana «Comandos favoritos» con pestañas y cantidad", page.ok and "Comandos favoritos" in page.text
      and "Todos los equipos" in page.text and "Arrastra para cambiar el orden" in page.text, page.status_code)
with SessionLocal() as db:
    dev = Device(name="fav-ros", host="192.0.2.77", device_type="mikrotik_routeros")
    db.add(dev)
    db.commit()
if dev:
    cons = adm.get(B + f"/devices/{dev.id}/console?embed=1")
    check("la consola trae la barra de favoritos y «Guardar como favorito»", 'id="cx-favs"' in cons.text
          and "Guardar como favorito" in cons.text and "Favoritos · MikroTik RouterOS" in cons.text, cons.status_code)
    with SessionLocal() as db:
        db.delete(db.get(Device, dev.id))
        db.commit()
check("Operar › Consola: «Administrar favoritos»", "Administrar favoritos" in adm.get(B + "/console").text)
r = requests.get(B + "/api/console/favorites", allow_redirects=False)
check("sin sesión no hay favoritos", r.status_code in (302, 303, 401, 403), r.status_code)
op.post(B + f"/api/console/favorites/{mine['id']}/delete")

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
