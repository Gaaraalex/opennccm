"""Botón «Soporte profesional» de la barra superior: lo ven todos los usuarios con sesión, es solo un enlace externo que
se abre en otra pestaña (en inglés con ?lang=en) y el admin lo oculta para todos en Ajustes."""
import os
import re
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.crypto import hash_password  # noqa: E402
from app.db import SessionLocal, Setting, User, select, set_setting  # noqa: E402

B = "http://127.0.0.1:8000"
URL = "https://comunidad.opennccm.com/soporte"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


with SessionLocal() as db:
    for name, role, lang in (("sup_ro", "viewer", None), ("sup_en", "viewer", "en")):
        u = db.scalar(select(User).where(User.username == name))
        if u is None:
            db.add(User(username=name, password_hash=hash_password("Clave-sup-2026"), role=role, lang=lang))
    db.commit()
set_setting("support_button", "1")


def login(u, p):
    s = requests.Session()
    s.post(B + "/login", data={"username": u, "password": p})
    return s


def button(html):
    m = re.search(r'<a class="btn-soporte"[^>]*>', html)
    return m.group(0) if m else ""


adm, ro, en = login("admin", "Admin12345"), login("sup_ro", "Clave-sup-2026"), login("sup_en", "Clave-sup-2026")
b = button(adm.get(B + "/").text)
check("admin: el botón está en la barra superior", f'href="{URL}"' in b, b)
check("se abre en una pestaña nueva con rel=noopener", 'target="_blank"' in b and 'rel="noopener"' in b, b)
html = adm.get(B + "/").text
check("va a la izquierda de «Apoya OpenNCCM»", html.find('class="btn-soporte"') < html.find('class="donate"'))
check("dice «Soporte profesional», con el auricular", "Soporte profesional</span>" in html and "#i-headset" in button(html) + html[html.find('class="btn-soporte"'):html.find('class="btn-soporte"') + 400])
check("solo lectura: también lo ve", f'href="{URL}"' in button(ro.get(B + "/").text))
b = button(en.get(B + "/").text)
check("en inglés: ?lang=en y «Professional support»", f'href="{URL}?lang=en"' in b and "Professional support" in en.get(B + "/").text, b)
check("Ajustes tiene la casilla, encendida por omisión", re.search(r'name="support_button" checked', adm.get(B + "/settings").text) is not None)
with SessionLocal() as db:  # los demás ajustes vuelven a quedar como estaban después de guardar
    before = {r.key: r.value for r in db.scalars(select(Setting))}
page = adm.get(B + "/settings").text
form = {}  # el formulario tal como está (para no tocar los demás ajustes), salvo esta casilla
for tag in re.findall(r"<input[^>]*>", page):
    name = re.search(r'name="(\w+)"', tag)
    if not name:
        continue
    if 'type="checkbox"' in tag:
        if " checked" in tag and name.group(1) != "support_button":
            form[name.group(1)] = "on"
    elif 'type="password"' not in tag:
        v = re.search(r'value="([^"]*)"', tag)
        form[name.group(1)] = v.group(1) if v else ""
for name, body in re.findall(r'<select[^>]*name="(\w+)"[^>]*>(.*?)</select>', page, re.S):
    v = re.search(r'<option value="([^"]*)"[^>]*selected', body)
    if v:
        form[name] = v.group(1)
for name, body in re.findall(r'<textarea[^>]*name="(\w+)"[^>]*>(.*?)</textarea>', page, re.S):
    form[name] = body
adm.post(B + "/settings", data=form)
check("con la casilla apagada, nadie lo ve", 'class="btn-soporte"' not in ro.get(B + "/").text and 'class="btn-soporte"' not in adm.get(B + "/").text)
check("y la casilla queda apagada", re.search(r'name="support_button" checked', adm.get(B + "/settings").text) is None)
with SessionLocal() as db:
    for r in db.scalars(select(Setting)):
        if r.key == "support_button":
            continue
        if r.key in before:
            r.value = before[r.key]
        else:
            db.delete(r)
    db.commit()
set_setting("support_button", "1")

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
