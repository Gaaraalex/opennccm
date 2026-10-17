"""Panel con rendimiento del servidor, aviso emergente de actualización y botón de actualizar.
Requiere haber ejecutado antes e2e.py (usuario admin). Usa la misma base que la aplicación (NCM_DATA_DIR)."""
import re
import sys
import time

import requests

sys.path.insert(0, ".")
from app.db import set_setting  # noqa: E402

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
html = adm.get(B + "/").text
check("panel: sección de rendimiento del servidor", "Rendimiento del servidor de OpenNCCM" in html)
check("panel: CPU, memoria, disco y tiempo encendido",
      all(t in html for t in ("CPU ahora", "Memoria RAM", "Disco usado", "Encendido desde hace")))
check("panel: barras de disco y memoria accesibles", html.count('role="meter"') >= 2)
check("panel: gráficas de CPU y memoria (o aviso de que reúnen datos)",
      html.count("últimas 24 h") >= 2 and ("<svg" in html or "Reuniendo datos" in html))

adm.post(B + "/users", data={"username": "lector9", "role": "viewer", "password": "Lector-2026xy",
                             "password2": "Lector-2026xy"})
lec = login("lector9", "Lector-2026xy")
check("un lector no ve el rendimiento del servidor", "Rendimiento del servidor" not in lec.get(B + "/").text)

# ---- aviso de actualización (sin consultar GitHub: se simula una versión publicada mayor)
adm.post(B + "/settings", data={"require_approval": "on", "allow_self_approval": "on", "console_access": "admin"})
set_setting("update_latest", "99.0.0")
set_setting("update_notes", "Novedades de prueba")
time.sleep(1)
html = adm.get(B + "/").text
check("aviso emergente de actualización para administradores", 'id="upd-popup"' in html and "99.0.0" in html)
check("el lector no ve el aviso", 'id="upd-popup"' not in lec.get(B + "/").text)
r = adm.post(B + "/system/update/dismiss")
check("«Más tarde» cierra el aviso", r.status_code == 204)
check("tras «Más tarde» no vuelve a salir en esta sesión", 'id="upd-popup"' not in adm.get(B + "/").text)
sysp = adm.get(B + "/system").text
check("Sistema: tarjeta de actualizaciones", "actualización disponible" in sysp and "Novedades de prueba" in sysp)
check("Sistema: botón para buscar actualizaciones", "/system/update/check" in sysp)
r = adm.post(B + "/system/update")
if "/system/updating" in r.url:  # servidor con el servicio de actualización web (systemd)
    check("actualizar desde la web abre la página de progreso", "Actualizando OpenNCCM" in r.text)
else:
    check("sin el servicio de systemd explica cómo actualizar", "sudo opennccm update" in r.text)
check("el lector no puede actualizar", lec.post(B + "/system/update").status_code == 403)
set_setting("update_latest", "")
set_setting("update_notes", "")

m = re.search(r"<b>lector9</b>[\s\S]*?/users/(\d+)/edit", adm.get(B + "/users").text)
if m:
    adm.post(f"{B}/users/{m.group(1)}/delete")
print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
