"""Interfaz en varios idiomas, nuevo inicio de sesión y gráficas de Análisis.
Requiere haber ejecutado antes e2e.py (usuario admin)."""
import html as htmlmod
import re
import sys

import requests

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def flashes(text):
    return [htmlmod.unescape(x) for x in re.findall(r'<div class="flash[^"]*">(.*?)</div>', text, re.S)]


def login(lang_header=None):
    s = requests.Session()
    if lang_header:
        s.headers["Accept-Language"] = lang_header
    r = s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
    return s, r


# --- pantalla de ingreso
r = requests.get(B + "/login")
check("ingreso en español por omisión", "Iniciar sesión" in r.text and 'lang="es"' in r.text)
check("ingreso con selector de idioma", 'href="/lang/en?next=/login"' in r.text)
check("ingreso: mostrar contraseña y aviso de Bloq Mayús", 'id="peek"' in r.text and 'id="caps"' in r.text)
check("ingreso conserva los campos que usan los gestores de contraseñas",
      'autocomplete="username"' in r.text and 'autocomplete="current-password"' in r.text)
r = requests.get(B + "/login", headers={"Accept-Language": "en-US,en;q=0.9"})
check("navegador en inglés: ingreso en inglés", "Sign in" in r.text and 'lang="en"' in r.text, r.text[:200])
check("idioma no disponible: español", "Iniciar sesión" in requests.get(
    B + "/login", headers={"Accept-Language": "fr-FR,fr;q=0.9"}).text)
s = requests.Session()
r = s.get(B + "/lang/en?next=/login")
check("elegir inglés en la pantalla de ingreso", r.url.endswith("/login") and "Sign in" in r.text)
r = s.post(B + "/login", data={"username": "admin", "password": "mala"})
check("aviso de error traducido", "Wrong username or password" in " ".join(flashes(r.text)), flashes(r.text))
check("/lang no redirige a otros sitios", s.get(B + "/lang/en?next=//evil.example", allow_redirects=False)
      .headers.get("location") == "/")

# --- con sesión: el idioma elegido queda guardado en el usuario
adm, r = login()
check("panel en español", ">Panel<" in r.text and ">Dispositivos<" in r.text)
r = adm.get(B + "/lang/en?next=/devices")
check("cambiar a inglés desde la sesión", ">Devices<" in r.text and ">Dashboard<" in r.text)
check("menú sin restos en español", not re.search(r">(Panel|Dispositivos|Cambios|Tareas|Ajustes)<", r.text))
adm2, r = login()  # sesión nueva, navegador sin preferencia: usa la del usuario
check("la preferencia se recuerda al volver a ingresar", ">Dashboard<" in r.text)
r = adm2.post(B + "/account/theme", data={"theme": "auto"})
check("avisos en el idioma del usuario", any("applied" in f for f in flashes(r.text)), flashes(r.text))
r = adm2.get(B + "/schedules")
check("textos generados en Python traducidos (frecuencias)", "Every day" in r.text or "Every hour" in r.text
      or "No tasks" in r.text, r.text[:100])
acc = adm2.get(B + "/account").text
check("Mi cuenta: selector de idioma", 'href="/lang/es?next=/account%23idioma"' in acc and "Language" in acc)
r = adm2.get(B + "/lang/es?next=/account")
check("volver a español", "Mi cuenta" in r.text)

# --- idioma predeterminado del sistema
adm.get(B + "/lang/es")
st = adm.get(B + "/settings").text
check("Ajustes: idioma predeterminado", 'name="default_lang"' in st)

# --- Análisis
a = adm.get(B + "/analytics?days=30").text
check("análisis: indicadores con comparación del periodo anterior",
      "Cambios en 30 días" in a and ("anteriores" in a))
check("análisis: actividad apilada por origen", 'class="seg c' in a or "Sin datos en este periodo" in a)
check("análisis: mapa de calor día × hora", 'class="viz heat"' in a or "Sin datos en este periodo" in a)
check("análisis: anillo de origen con leyenda", 'class="donut"' in a or "Sin datos en este periodo" in a)
check("análisis: información emergente (data-tip) y viz.js", "data-tip=" in a and "/static/viz.js" in a)
check("análisis: cumplimiento con icono de estado", re.search(r"[✓⚠✕] \d+%", a) is not None
      or "Sin auditorías" in a)
for d in (7, 90, 365):
    check(f"análisis de {d} días", adm.get(f"{B}/analytics?days={d}").status_code == 200)
adm.get(B + "/lang/en")
a = adm.get(B + "/analytics?days=30").text
check("análisis en inglés", "Change activity" in a and "When the network changes" in a)
adm.get(B + "/lang/es")

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
