"""«Apoya OpenNCCM»: botón solo para administradores, tarjeta en Acerca de y en Sistema, enlaces seguros y aviso tras
actualizar una sola vez por versión. Requiere haber ejecutado antes e2e.py (usuario admin) y los enlaces de prueba
NCM_DONATE_* que exporta run-tests.sh."""
import os
import re
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import __version__  # noqa: E402
from app.db import get_setting, set_setting  # noqa: E402

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


adm = login("admin", "Admin12345")
for u, role in (("don_lector", "viewer"), ("don_oper", "operator"), ("don_admin2", "admin")):
    adm.post(B + "/users", data={"username": u, "role": role, "password": "Donar-Prueba-2026", "password2": "Donar-Prueba-2026"})
lec, ope, adm2 = (login(u, "Donar-Prueba-2026") for u in ("don_lector", "don_oper", "don_admin2"))

panel = adm.get(B + "/").text
check("botón «Apoya OpenNCCM» en la barra para administradores",
      'class="donate"' in panel and 'aria-label="Apoya OpenNCCM: donar"' in panel)
check("el lector no ve el botón", 'class="donate"' not in lec.get(B + "/").text)
check("el operador no ve el botón", 'class="donate"' not in ope.get(B + "/").text)
check("desplegable con GitHub Sponsors y Ko-fi", "GitHub Sponsors" in panel and "Invitar un café (Ko-fi)" in panel
      and 'id="don-dlg"' in panel)
acerca = lec.get(B + "/acerca").text
check("tarjeta en Acerca de (visible para todos)", 'id="apoya"' in acerca and "Open Collective" in acerca)
check("privacidad: los enlaces de donación solo se abren si se pulsan", "Donaciones:" in acerca)
links = re.findall(r'<a class="btn"[^>]*href="(https://[^"]+)"[^>]*>', acerca)
check("enlaces en pestaña nueva y sin datos de origen",
      len(re.findall(r'href="https://[^"]+" target="_blank" rel="noopener noreferrer"', acerca)) >= 3, links)
check("tarjeta en Sistema", 'id="apoya"' in adm.get(B + "/system").text)

# ---- aviso tras actualizar: una sola vez por versión y solo a un administrador
set_setting("donate_pending", __version__)
set_setting("donate_shown", "")
check("el lector no lo ve ni lo gasta", 'id="don-toast"' not in lec.get(B + "/").text
      and get_setting("donate_pending") == __version__)
first = adm.get(B + "/").text
check("el primer administrador ve el aviso tras actualizar",
      'id="don-toast"' in first and f"OpenNCCM {__version__} instalado" in first and "data-donate" in first
      and "Ahora no" in first)
check("no se vuelve a mostrar en la siguiente página", 'id="don-toast"' not in adm.get(B + "/devices").text)
check("ni a otro administrador (una vez por versión)", 'id="don-toast"' not in adm2.get(B + "/").text)
check("queda guardada la versión ya mostrada", get_setting("donate_shown") == __version__)

# ---- al arrancar tras una actualización se deja pendiente; con la misma versión, no
from app.main import _note_version  # noqa: E402
set_setting("donate_pending", "")
set_setting("donate_shown", "")
set_setting("installed_version", "0.9.0")
_note_version()
check("versión nueva al arrancar: aviso pendiente", get_setting("donate_pending") == __version__
      and get_setting("installed_version") == __version__)
set_setting("donate_pending", "")
_note_version()
check("misma versión: no se repite", get_setting("donate_pending") == "")
set_setting("donate_shown", __version__)
set_setting("installed_version", "0.9.0")
_note_version()
check("versión ya mostrada antes: no se repite", get_setting("donate_pending") == "")

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
