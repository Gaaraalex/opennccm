"""Botón «Soporte profesional» de la barra superior: lo ven todos los usuarios con sesión, es solo un enlace externo que
se abre en otra pestaña (en inglés con ?lang=en) y no hay forma de ocultarlo: Ajustes ya no tiene la casilla y un
«support_button» apagado que haya quedado de una versión anterior no lo quita."""
import os
import re
import sys
import time

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
# otras pruebas guardan Ajustes sin esta casilla (la apagan) y el servidor guarda los ajustes 30 s: se espera a que se vea
for _ in range(40):
    if 'class="btn-soporte"' in adm.get(B + "/").text:
        break
    time.sleep(1)
b = button(adm.get(B + "/").text)
check("admin: el botón está en la barra superior", f'href="{URL}"' in b, b)
check("se abre en una pestaña nueva con rel=noopener", 'target="_blank"' in b and 'rel="noopener"' in b, b)
html = adm.get(B + "/").text
check("va a la izquierda de «Apoya OpenNCCM»", html.find('class="btn-soporte"') < html.find('class="donate"'))
check("dice «Soporte profesional», con el auricular", "Soporte profesional</span>" in html and "#i-headset" in button(html) + html[html.find('class="btn-soporte"'):html.find('class="btn-soporte"') + 400])
check("y en el menú lateral, con el texto completo (se ve en el teléfono)", 'class="btn-soporte rail-soporte"' in html
      and html.count(f'href="{URL}"') == 2)
check("solo lectura: también lo ve", f'href="{URL}"' in button(ro.get(B + "/").text))
b = button(en.get(B + "/").text)
check("en inglés: ?lang=en y «Professional support»", f'href="{URL}?lang=en"' in b and "Professional support" in en.get(B + "/").text, b)
check("Ajustes no tiene casilla para ocultarlo", 'name="support_button"' not in adm.get(B + "/settings").text)
set_setting("support_button", "0")  # como lo dejaba apagado una versión anterior
time.sleep(31)  # el servidor guarda los ajustes 30 s
check("un ajuste apagado de antes no lo oculta (ni en la barra ni en el menú)", 'class="btn-soporte"' in ro.get(B + "/").text
      and 'class="btn-soporte rail-soporte"' in adm.get(B + "/").text)
set_setting("support_button", "1")

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
