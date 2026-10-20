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
sysp0 = adm.get(B + "/system").text
check("Sistema: sección de rendimiento del servidor (sale del Panel)",
      "Rendimiento del servidor de OpenNCCM" in sysp0 and "Rendimiento del servidor" not in html)
check("Sistema: CPU, memoria, disco y tiempo encendido",
      all(t in sysp0 for t in ("CPU ahora", "Memoria RAM", "Disco usado", "Encendido desde hace")))
check("Sistema: barras de disco y memoria accesibles", sysp0.count('role="meter"') >= 2)
check("Sistema: gráficas de CPU y memoria (o aviso de que reúnen datos)",
      sysp0.count("últimas 24 h") >= 2 and ("<svg" in sysp0 or "Reuniendo datos" in sysp0))

# --- Panel «Señal»: salud, 4 indicadores, bandeja de atención y actividad
check("panel: puntuación de salud de 0 a 100", "Salud de la red" in html and 'class="health-ring"' in html
      and any(f in html for f in ("Tu red está bien", "Casi todo en orden", "Hay trabajo pendiente", "Tu red necesita atención")))
check("panel: 4 indicadores", len(re.findall(r'class="kpi(?: alert)?"', html)) == 4)
check("panel: bandeja de atención con una acción por fila (o celebración)",
      "Qué hacer ahora" in html and ('class="queue"' in html or "Nada pendiente" in html))
check("panel: como máximo 6 filas en la bandeja", html.count('<li class="q-') <= 6)
check("panel: actividad reciente con tiempo relativo", "Actividad reciente" in html and ("hace " in html or "ahora mismo" in html))
check("panel: gráfica de cambios por día", '<svg class="viz"' in html)

adm.post(B + "/users", data={"username": "lector9", "role": "viewer", "password": "Lector-2026xy",
                             "password2": "Lector-2026xy"})
lec = login("lector9", "Lector-2026xy")
lp = lec.get(B + "/").text
check("un lector ve el Panel sin botones de acción", "Salud de la red" in lp and "/backup/all" not in lp)
dev = adm.get(B + "/devices/1").text
check("equipo: eventos con desplazamiento (3 visibles)", 'class="scrollbox" data-rows="3"' in dev)
check("un lector no ve Sistema", lec.get(B + "/system").status_code == 403)

# --- Dispositivos: chips de estado, selección múltiple y acciones masivas
import html as htmlmod  # noqa: E402


def flashes(text):
    return [htmlmod.unescape(x) for x in re.findall(r'<div class="flash[^"]*">(.*?)</div>', text, re.S)]


dl = adm.get(B + "/devices").text
check("lista: chips de estado con recuento", 'class="chip on"' in dl and re.search(r'Todos <span class="num">\d+</span>', dl))
check("lista: casillas para selección múltiple", 'name="ids"' in dl and 'id="sel-all"' in dl)
check("lista: barra de acciones masivas", 'action="/devices/bulk"' in dl and 'value="backup"' in dl and 'value="move"' in dl)
check("lista: «Agregar» ofrece importar CSV", 'href="#importar"' in dl and 'id="importar"' in dl)
r = adm.post(B + "/devices/bulk", data={"action": "backup", "ids": ["1"]})
check("masivo: respaldar los seleccionados", any("Respaldo de 1 equipos" in f for f in flashes(r.text)), flashes(r.text))
r = adm.post(B + "/devices/bulk", data={"action": "audit", "ids": ["1"]})
check("masivo: auditar los seleccionados", any("Auditoría de cumplimiento lanzada para 1" in f for f in flashes(r.text)), flashes(r.text))
adm.post(B + "/groups", data={"name": "Masivo-UI"})
gid = re.search(r'<option value="(\d+)">Masivo-UI</option>', adm.get(B + "/devices").text)
before = re.search(r"<dt>Grupo</dt><dd>(.*?)</dd>", adm.get(B + "/devices/1").text, re.S).group(1)
r = adm.post(B + "/devices/bulk", data={"action": "move", "ids": ["1"], "group_id": gid.group(1) if gid else "0"})
check("masivo: mover a un grupo", gid and "Masivo-UI" in adm.get(B + "/devices/1").text, flashes(r.text))
check("masivo: sin selección avisa", "Seleccione al menos un dispositivo" in " ".join(flashes(adm.post(B + "/devices/bulk", data={"action": "backup"}).text)))
check("masivo: el lector no puede", lec.post(B + "/devices/bulk", data={"action": "backup", "ids": ["1"]}).status_code == 403)
# devolver R1 a su grupo original (otras pruebas lo usan)
orig = (re.search(r">([^<]+)</a>", before) or re.search(r"(—)", before)).group(1).strip()
for m in re.finditer(r'<option value="(\d+)"[^>]*>([^<]+)</option>', adm.get(B + "/devices").text):
    if orig and orig != "—" and m.group(2).strip() == orig:
        adm.post(B + "/devices/bulk", data={"action": "move", "ids": ["1"], "group_id": m.group(1)})
        break
else:
    adm.post(B + "/devices/bulk", data={"action": "move", "ids": ["1"], "group_id": "0"})

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
check("Sistema: ventana «Novedades de cada versión» con el historial", 'id="novedades"' in sysp
      and "Versión 1.6.0" in sysp and "<b>Consola SSH web</b>" in sysp)
check("Sistema: enlace a todas las versiones en GitHub", "github.com/Gaaraalex/opennccm/releases" in sysp)
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
