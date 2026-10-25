"""Clave de la API de NVD desde Ajustes: se guarda cifrada, no se vuelve a mostrar y dejar el campo vacío no la borra.
Requiere haber ejecutado antes e2e.py."""
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.db import SessionLocal, Setting, get_setting, set_setting  # noqa: E402
from e2e_remember import settings_form  # noqa: E402  (todos los valores actuales del formulario de Ajustes)

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


s = requests.Session()
s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
page = s.get(B + "/settings").text
check("Ajustes: sección «Vulnerabilidades (NVD)» con el campo de la clave",
      'id="nvd"' in page and 'name="nvd_api_key"' in page and 'type="password"' in page[page.find('name="nvd_api_key"') - 200:page.find('name="nvd_api_key"') + 60])
check("con enlace para pedir la clave gratis", "https://nvd.nist.gov/developers/request-an-api-key" in page)

data = settings_form(s)
data["nvd_api_key"] = "e2e-clave-nvd-0001"
s.post(B + "/settings", data=data)
with SessionLocal() as db:
    raw = db.get(Setting, "nvd_api_key").value
check("se guarda la clave", get_setting("nvd_api_key") == "e2e-clave-nvd-0001")
check("cifrada en la base de datos", "e2e-clave-nvd-0001" not in raw)
page = s.get(B + "/settings").text
check("la página no vuelve a mostrar la clave, solo que está guardada", "e2e-clave-nvd-0001" not in page and "(guardada)" in page)
data = settings_form(s)
data["nvd_api_key"] = ""
s.post(B + "/settings", data=data)
check("guardar los ajustes con el campo vacío no la borra", get_setting("nvd_api_key") == "e2e-clave-nvd-0001")

set_setting("nvd_api_key", "")
print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
