"""Usuarios (crear/editar separados, protecciones), contraseña propia, temas, icono, plataformas y fecha/hora.
Requiere haber ejecutado antes e2e.py (usuario admin / Admin12345 y el equipo R1 con respaldos)."""
import re
import subprocess
import sys

import requests

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def flashes(html):
    return " | ".join(re.findall(r'<div class="flash[^"]*">(.*?)</div>', html, re.S))


def login(user, pw):
    s = requests.Session()
    r = s.post(B + "/login", data={"username": user, "password": pw})
    return s, r.url.rstrip("/") == B


adm, ok = login("admin", "Admin12345")
check("admin ingresa", ok)

# --- icono y botón de contraseña en la barra
html = adm.get(B + "/").text
check("pestaña con icono (favicon)", 'rel="icon"' in html and "/static/icon.svg" in html)
check("botón «Cambiar contraseña» junto al usuario", "Cambiar contraseña" in html and 'href="/account#clave"' in html)
fav = adm.get(B + "/favicon.ico")
check("/favicon.ico responde con una imagen", fav.status_code == 200 and fav.headers["content-type"].startswith("image/"))
check("/static/icon.svg", adm.get(B + "/static/icon.svg").status_code == 200)
check("menú con enlace a Plataformas", 'href="/vendors"' in html)

# --- crear usuario
r = adm.post(B + "/users", data={"username": "ana", "role": "operator", "password": "Clave-Inicial-1", "password2": "Clave-Inicial-1"})
check("crear usuario nuevo", "«ana» creado" in flashes(r.text), flashes(r.text))
r = adm.post(B + "/users", data={"username": "ANA", "role": "viewer", "password": "OtraClave123", "password2": "OtraClave123"})
check("no permite duplicados (sin distinguir mayúsculas)", "Ya existe un usuario" in flashes(r.text), flashes(r.text))
r = adm.post(B + "/users", data={"username": "a b", "role": "viewer", "password": "OtraClave123"})
check("rechaza nombres con espacios", "no válido" in flashes(r.text), flashes(r.text))
r = adm.post(B + "/users", data={"username": "beto", "role": "viewer", "password": "Distinto-Uno-1", "password2": "Distinta-Dos-2"})
check("rechaza contraseñas que no coinciden", "no coinciden" in flashes(r.text), flashes(r.text))
users_html = adm.get(B + "/users").text
check("crear NO modifica a admin (sigue existiendo)", "<b>admin</b>" in users_html and "<b>ana</b>" in users_html)
_, ok = login("admin", "Admin12345")
check("la contraseña de admin no cambió", ok)
_, ok = login("ana", "Clave-Inicial-1")
check("el usuario nuevo puede ingresar", ok)

# --- editar
ana_id = re.search(r'<b>ana</b>[\s\S]*?/users/(\d+)/edit', users_html).group(1)
r = adm.get(f"{B}/users/{ana_id}/edit")
check("formulario de edición con el usuario correcto", "Editar «ana»" in r.text)
r = adm.post(f"{B}/users/{ana_id}/edit", data={"role": "viewer", "active": "on", "password": "Clave-Nueva-22", "password2": "Clave-Nueva-22"})
check("editar: mensaje explícito con los cambios", "«ana» actualizado" in flashes(r.text) and "contraseña cambiada" in flashes(r.text), flashes(r.text))
_, ok = login("ana", "Clave-Nueva-22")
check("ana ingresa con la contraseña nueva", ok)

admin_id = re.search(r'<b>admin</b>[\s\S]*?/users/(\d+)/edit', adm.get(B + "/users").text).group(1)
r = adm.post(f"{B}/users/{admin_id}/edit", data={"role": "viewer", "active": "on"})
check("no puede quitarse el rol de administrador", "No puede quitarse" in flashes(r.text), flashes(r.text))
r = adm.post(f"{B}/users/{admin_id}/edit", data={"role": "admin"})  # sin 'active' = bloquear
check("no puede bloquearse a sí mismo", "No puede quitarse" in flashes(r.text), flashes(r.text))
r = adm.post(f"{B}/users/{admin_id}/delete")
check("no puede eliminarse a sí mismo", "No puede eliminarse" in flashes(r.text), flashes(r.text))

# --- contraseña propia
ana, _ = login("ana", "Clave-Nueva-22")
r = ana.post(B + "/account", data={"current": "mala", "new": "Clave-Final-33", "new2": "Clave-Final-33"})
check("cambio propio: rechaza contraseña actual incorrecta", "no es correcta" in flashes(r.text), flashes(r.text))
r = ana.post(B + "/account", data={"current": "Clave-Nueva-22", "new": "Clave-Final-33", "new2": "Otra"})
check("cambio propio: rechaza confirmación distinta", "no coinciden" in flashes(r.text), flashes(r.text))
r = ana.post(B + "/account", data={"current": "Clave-Nueva-22", "new": "Clave-Final-33", "new2": "Clave-Final-33"})
check("cambio propio: correcto", "Contraseña actualizada" in flashes(r.text), flashes(r.text))
_, ok = login("ana", "Clave-Final-33")
check("ingresa con su nueva contraseña", ok)

# --- temas
r = ana.post(B + "/account/theme", data={"theme": "noche"})
check("aplicar tema", 'data-theme="noche"' in r.text, re.findall(r'data-theme="[^"]*"', r.text))
check("el tema se conserva entre páginas", 'data-theme="noche"' in ana.get(B + "/devices").text)
for tema in ("arena", "oceano", "atardecer", "lavanda", "grafito"):
    r = ana.post(B + "/account/theme", data={"theme": tema})
    check(f"tema nuevo «{tema}» se aplica", f'data-theme="{tema}"' in r.text)
check("otro usuario mantiene su tema", 'data-theme="auto"' in adm.get(B + "/").text)
r = ana.post(B + "/account/theme", data={"theme": "inexistente"})
check("tema desconocido vuelve a automático", 'data-theme="auto"' in r.text)

# --- plataformas
v = adm.get(B + "/vendors").text
row = re.search(r"<code>cisco_ios</code>.*?</tr>", v, re.S)
check("plataformas: comando de arranque visible", row and "show startup-config" in row.group(0))
check("plataformas: comando de inventario visible", row and "show version" in row.group(0))
check("plataformas: cisco_ios comprobado en su red", row and "✓ 1 equipo" in row.group(0), row.group(0)[-300:] if row else "")
check("plataformas: buscador", 'id="q"' in v and 'id="v"' in v)
check("plataformas: sin equipos no se marca", re.search(r"<code>yamaha</code>.*?aún sin equipos", v, re.S) is not None)

# --- fecha y hora
sysp = adm.get(B + "/system").text
check("Sistema: sección de fecha, hora y zona horaria", "Fecha, hora y zona horaria del servidor" in sysp)

# --- borrar usuario y comando de consola
r = adm.post(f"{B}/users/{ana_id}/delete")
check("eliminar usuario", "«ana» eliminado" in flashes(r.text), flashes(r.text))
out = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, '.'); from app.cli import main; "
                      "sys.argv = ['opennccm', 'list-users']; main()"], capture_output=True, text=True)
check("opennccm users: lista usuarios", "admin" in out.stdout and "administrador" in out.stdout and "ana" not in out.stdout,
      out.stdout + out.stderr)

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
