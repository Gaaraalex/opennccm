"""Correlación con Zabbix (2.44) contra un Zabbix simulado 7.0: Ajustes › Zabbix (token cifrado, «Probar conexión»),
la tarjeta «Cambios y problemas en Zabbix» de la ficha y los datos que pide (host buscado por dirección y por nombre,
problemas del periodo y los abiertos de antes, el cambio justo antes de cada problema, métricas con historial o
tendencias), los errores (desactivada, host que no está, Zabbix caído) y los permisos. Requiere e2e.py (admin)."""
import hashlib
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from app.crypto import encrypt, hash_password  # noqa: E402
from app.db import (ConfigVersion, Device, DeviceGroup, SessionLocal, Setting, User, UserGroup, get_setting,  # noqa: E402
                    select, set_setting)

B = "http://127.0.0.1:8000"
ZP = 18170
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def version(dev_id, when, added, removed, trigger="scheduled", who="scheduler"):
    text = f"! {when}\nhostname x\n"
    return ConfigVersion(device_id=dev_id, kind="running", content_enc=encrypt(text), size=len(text),
                         sha256=hashlib.sha256(text.encode()).hexdigest(), trigger=trigger, username=who,
                         added=added, removed=removed, created_at=when.replace(microsecond=0))


zbx = subprocess.Popen([sys.executable, os.path.join(HERE, "fakezabbix.py"), str(ZP), "7.0.5"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
URL = f"http://127.0.0.1:{ZP}/zabbix/"
adm = requests.Session()
ids = {}
try:
    time.sleep(2)
    adm.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
    nowd = datetime.now()
    with SessionLocal() as db:
        g = DeviceGroup(name="ZC-Grupo")
        db.add(g)
        db.flush()
        for name, host in (("ZC-Borde", "192.0.2.11"), ("SW-Core", "10.9.9.9"), ("ZC-Nadie", "10.1.1.1"), ("ZC-Caido", "10.1.1.2")):
            d = Device(name=name, host=host, device_type="mikrotik_routeros", last_status="ok",
                       group_id=g.id if name == "ZC-Nadie" else None)
            db.add(d)
            db.flush()
            ids[name] = d.id
        b = ids["ZC-Borde"]
        db.add(version(b, nowd - timedelta(days=5), 0, 0, "manual", "admin"))           # v1, inicial
        db.add(version(b, nowd - timedelta(hours=3, minutes=15), 3, 1, "manual", "ana"))  # v2, 15 min antes del problema
        db.add(version(b, nowd - timedelta(hours=1), 0, 0))                               # v3, sin diferencias
        viewer = User(username="zc-lector", password_hash=hash_password("Lectura-ZC-2026"), role="viewer")
        db.add(viewer)
        db.flush()
        db.add(UserGroup(user_id=viewer.id, group_id=g.id))
        gid = g.id
        db.commit()

    # 1. desactivada: ni tarjeta ni datos
    page = adm.get(B + f"/devices/{ids['ZC-Borde']}").text
    check("sin la integración, la ficha no tiene la tarjeta de Zabbix", 'id="zbx"' not in page and "zbxcorr.js" not in page)
    j = adm.get(B + f"/devices/{ids['ZC-Borde']}/zabbix").json()
    check("sin la integración, los datos lo dicen", j.get("ok") is False and j.get("reason") == "off")

    # 2. Ajustes › Zabbix
    page = adm.get(B + "/settings").text
    check("Ajustes tiene la sección Zabbix", 'id="zabbix"' in page and 'name="zabbix_token"' in page and 'href="#zabbix"' in page)
    r = adm.post(B + "/settings/zabbix-test", data={"zabbix_url": URL, "zabbix_token": "tok-ok"}).json()
    check("Probar conexión: versión y cuántos hosts ve el token", r["ok"] and "7.0.5" in r["msg"] and "7 hosts" in r["msg"], r)
    r = adm.post(B + "/settings/zabbix-test", data={"zabbix_url": URL, "zabbix_token": "malo"}).json()
    check("Probar conexión con un token malo: el error de Zabbix", not r["ok"] and "Not authorized" in r["msg"], r)
    r = adm.post(B + "/settings/zabbix-test", data={"zabbix_url": URL}).json()
    check("Probar conexión sin token ni token guardado: lo pide", not r["ok"] and "token" in r["msg"], r)
    set_setting("zabbix_url", URL)
    set_setting("zabbix_verify", "1")
    set_setting("zabbix_token", "tok-ok")
    set_setting("zabbix_link", "1")
    with SessionLocal() as db:
        raw = db.get(Setting, "zabbix_token").value
    check("el token se guarda cifrado", raw != "tok-ok" and get_setting("zabbix_token") == "tok-ok")
    r = adm.post(B + "/settings/zabbix-test", data={"zabbix_url": URL}).json()
    check("Probar conexión sin escribir el token usa el guardado", r["ok"], r)
    page = adm.get(B + "/settings").text
    check("el token guardado no se muestra en Ajustes", "tok-ok" not in page and "(guardada)" in page.split('id="s-zbx-tok"')[1][:300])

    # 3. ficha y datos
    page = adm.get(B + f"/devices/{ids['ZC-Borde']}").text
    check("la ficha tiene la tarjeta, sus textos y el script", 'id="zbx"' in page and "zbxcorr.js" in page
          and 'id="zbx-i18n"' in page and "tok-ok" not in page)
    j = adm.get(B + f"/devices/{ids['ZC-Borde']}/zabbix?days=7").json()
    check("encuentra el host por la dirección (aunque el nombre sea otro)", j.get("ok") and j["host"]["name"] == "RT-Borde"
          and "hostids%5B%5D=10101" in j["host"]["url"], j.get("msg"))
    probs = {p["id"]: p for p in j.get("problems", [])}
    check("problemas del periodo y el abierto que empezó antes", set(probs) == {"9001", "9003", "9005"}, sorted(probs))
    p1 = probs.get("9001", {})
    check("problema resuelto: inicio, fin, gravedad y enlace", p1.get("end") and p1["end"] - p1["start"] == 3600
          and p1["sev"] == 4 and p1["sev_label"] == "Alta" and "tr_events.php?triggerid=500&eventid=9001" in p1["url"])
    check("el problema que empezó 15 min después de v2 la señala", p1.get("after", {}).get("n") == 2
          and p1["after"]["min"] == 15 and p1["after"]["prev"], p1.get("after"))
    check("un cambio sin diferencias (v3) no se toma como causa, ni uno de hace casi 3 h",
          "after" not in probs.get("9003", {}), probs.get("9003", {}).get("after"))
    check("el abierto sigue sin fin", probs.get("9003", {}).get("end") is None and probs.get("9005", {}).get("end") is None)
    ch = j.get("changes", [])
    check("los cambios del periodo, numerados como en la línea de tiempo", [c["n"] for c in ch] == [1, 2, 3]
          and ch[1]["who"] == "ana" and ch[1]["src"] == "Manual" and ch[0]["first"], ch)
    ms = {m["key"]: m for m in j.get("metrics", [])}
    check("métricas: latencia en ms y CPU; sin las de texto", set(ms) == {"icmppingsec", "system.cpu.util"}, sorted(ms))
    lat = ms.get("icmppingsec", {}).get("points", [])
    check("latencia en milisegundos y como mucho 360 puntos", lat and 0 < len(lat) <= 360 and 1 <= min(v for _t, v in lat) <= 3
          and max(v for _t, v in lat) >= 50, (len(lat), lat[:2]))
    j30 = adm.get(B + f"/devices/{ids['ZC-Borde']}/zabbix?days=30").json()
    check("30 días: con tendencias", j30.get("ok") and j30["metrics"] and j30["till"] - j30["from"] == 30 * 86400)
    j1 = adm.get(B + f"/devices/{ids['ZC-Borde']}/zabbix?days=1").json()
    check("24 h: el problema abierto desde hace 10 días sigue apareciendo", "9005" in {p["id"] for p in j1.get("problems", [])}
          and "9001" in {p["id"] for p in j1["problems"]} and [c["n"] for c in j1["changes"]] == [2, 3])
    jbad = adm.get(B + f"/devices/{ids['ZC-Borde']}/zabbix?days=99").json()
    check("un periodo que no se ofrece usa 7 días", jbad.get("till", 0) - jbad.get("from", 0) == 7 * 86400)
    j = adm.get(B + f"/devices/{ids['SW-Core']}/zabbix").json()
    check("si la dirección no está, lo busca por el nombre", j.get("ok") and j["host"]["id"] == "10102"
          and j["problems"] == [] and j["metrics"] == [])
    j = adm.get(B + f"/devices/{ids['ZC-Nadie']}/zabbix").json()
    check("equipo que no está en Zabbix: lo dice con la dirección y el nombre", j.get("reason") == "nohost"
          and "10.1.1.1" in j["msg"] and "ZC-Nadie" in j["msg"])

    # 4. permisos
    anon = requests.get(B + f"/devices/{ids['ZC-Borde']}/zabbix", allow_redirects=False)
    check("sin sesión no hay datos", anon.status_code in (302, 303, 401, 403))
    vw = requests.Session()
    vw.post(B + "/login", data={"username": "zc-lector", "password": "Lectura-ZC-2026"})
    r = vw.get(B + f"/devices/{ids['ZC-Borde']}/zabbix", allow_redirects=False)
    check("un lector de otro grupo no ve los datos de un equipo ajeno", r.status_code in (302, 303, 403))
    r = vw.get(B + f"/devices/{ids['ZC-Nadie']}/zabbix")
    check("el lector sí ve los de su grupo", r.status_code == 200 and r.json().get("reason") == "nohost")
    r = vw.post(B + "/settings/zabbix-test", data={"zabbix_url": URL, "zabbix_token": "tok-ok"}, allow_redirects=False)
    check("un lector no puede probar la conexión", r.status_code in (302, 303, 403))

    # 5. Zabbix caído
    set_setting("zabbix_url", "http://127.0.0.1:1/zabbix/")
    r = adm.get(B + f"/devices/{ids['ZC-Caido']}/zabbix")
    check("Zabbix caído: lo dice sin error del servidor", r.status_code == 200 and r.json().get("reason") == "error"
          and "No se pudo conectar" in r.json().get("msg", ""), r.text[:200])
    page = adm.get(B + f"/devices/{ids['ZC-Caido']}")
    check("Zabbix caído: la ficha abre igual", page.status_code == 200 and 'id="zbx"' in page.text)
finally:
    zbx.kill()
    for k in ("zabbix_link", "zabbix_token"):
        set_setting(k, "0" if k == "zabbix_link" else "")
    with SessionLocal() as db:
        for i in ids.values():
            d = db.get(Device, i)
            if d:
                db.delete(d)
        for u in db.scalars(select(User).where(User.username == "zc-lector")):
            db.delete(u)
        for g in db.scalars(select(DeviceGroup).where(DeviceGroup.name == "ZC-Grupo")):
            db.delete(g)
        db.commit()

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
