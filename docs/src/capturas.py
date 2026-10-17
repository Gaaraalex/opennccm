"""Genera capturas de pantalla reales de OpenNCCM (demo local) para la documentación.

Inicia sesión, guarda el HTML de cada página con la hoja de estilos local y lo renderiza con
Microsoft Edge en modo headless. Luego recorta el espacio vacío inferior.
Uso:  python capturas.py   (con la demo corriendo en http://127.0.0.1:8000)
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import requests
from PIL import Image

B = "http://127.0.0.1:8000"
HERE = Path(__file__).resolve().parent
OUT = HERE / "img"
PAGES = HERE / "_pages"
CSS = (HERE.parent.parent / "app" / "static" / "style.css").as_uri()
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
OUT.mkdir(exist_ok=True)
PAGES.mkdir(exist_ok=True)

s = requests.Session()
s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})


def save(name: str, html: str, width=1280, height=2400):
    html = re.sub(r'href="/static/style\.css[^"]*"', f'href="{CSS}"', html)
    static = (HERE.parent.parent / "app" / "static").as_uri()
    html = re.sub(r'(src|href)="/static/([^"?]+)(\?[^"]*)?"', lambda m: f'{m.group(1)}="{static}/{m.group(2)}"', html)
    # que el navegador headless use el tema claro aunque el sistema esté en oscuro
    html = html.replace("<head>", '<head><meta name="color-scheme" content="light">', 1)
    page = PAGES / f"{name}.html"
    page.write_text(html, encoding="utf-8")
    png = OUT / f"{name}.png"
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-color-profile=srgb",
                    "--blink-settings=preferredColorScheme=1", f"--window-size={width},{height}",
                    f"--screenshot={png}", page.as_uri()], check=True, capture_output=True, timeout=60)
    crop(png)
    print("ok", name)


def crop(png: Path):
    im = Image.open(png).convert("RGB")
    w, h = im.size
    bg = im.getpixel((w - 2, h - 2))
    px = im.load()
    last = h - 1
    while last > 0 and all(px[x, last] == bg for x in range(0, w, 7)):
        last -= 1
    im.crop((0, 0, w, min(h, last + 24))).save(png, optimize=True)


def get(path):
    r = s.get(B + path)
    r.raise_for_status()
    return r.text


# ---------------------------------------------------------------- datos para las capturas
s.post(B + "/devices/1/backup")
dev = get("/devices/1")
running = re.findall(r'<td>(\d+)</td><td class="small">[^<]*</td><td>running</td>', dev)
cl = get("/configlets")
vlan = re.search(r'<b>Agregar VLAN \(IOS-like\)</b>[\s\S]*?/configlets/(\d+)/run', cl).group(1)
exec_id = re.search(r'<b>Estado de interfaces \(IOS\)</b>[\s\S]*?/configlets/(\d+)/run', cl).group(1)
s.post(B + "/settings", data={"require_approval": "on", "allow_self_approval": "on", "notify_compliance": "on",
                              "alert_min_severity": "warning", "retention_versions": "0", "dr_keep": "14",
                              "smtp_port": "587", "smtp_security": "starttls", "session_idle_minutes": "30",
                              "console_access": "admin", "update_check": "on", "org_name": "Empresa Ejemplo S.A.",
                              "org_contact": "redes@empresa-ejemplo.com"})
r = s.post(f"{B}/configlets/{vlan}/run", data={"device_ids": "1", "variables": "vlan_id=30\nvlan_nombre=CAMARAS",
                                                "title": "Agregar VLAN 30 de cámaras"})
pending_cr = re.search(r"/changes/(\d+)", r.url).group(1)
comp = get("/compliance")
pci = re.search(r'/compliance/policies/(\d+)">PCI DSS 4.0 \(IOS\)', comp).group(1)

login = requests.get(B + "/login").text
save("01-login", login, 1280, 800)
save("02-panel", get("/"))
save("03-dispositivos", get("/devices"))
save("04-dispositivo-nuevo", get("/devices/new"))
save("05-dispositivo", get("/devices/1"), height=3600)
save("06-version", get(f"/versions/{running[0]}"), height=1400)
save("07-diff-lado-a-lado", get(f"/diff?a={running[-1]}&b={running[0]}&view=side&ctx=3"))
save("08-diff-unificada", get(f"/diff?a={running[-1]}&b={running[0]}&view=unified&ctx=3"))
save("09-busqueda", get("/search?q=snmp"))
save("10-configlets", get("/configlets"))
save("11-configlet-ejecutar", get(f"/configlets/{vlan}/run"))
save("12-configlet-exec-resultado", s.post(f"{B}/configlets/{exec_id}/run", data={"device_ids": "1"}).text)
save("13-solicitudes", get("/changes"))
save("14-solicitud-pendiente", get(f"/changes/{pending_cr}"))
save("15-cumplimiento", get("/compliance"), height=3000)
save("16-politica-pci", get(f"/compliance/policies/{pci}"), height=3000)
save("17-firmware", get("/firmware"))
save("18-eol", get("/eol"))
save("19-vulnerabilidades", get("/vulns"), height=1600)
save("20-tareas", get("/schedules"))
save("21-alarmas", get("/events"), height=1800)
save("22-analisis", get("/analytics"), height=4200)
save("23-informes", get("/reports"))
save("24-informe-inventario", get("/reports/view/inventory"))
save("25-credenciales", get("/credentials"))
save("26-usuarios", get("/users"))
save("27-auditoria", get("/audit"), height=1600)
save("28-sistema", get("/system"))
save("29-ajustes", get("/settings"), height=3000)
save("30-plataformas", get("/vendors"), height=1500)
save("31-cuenta", get("/account"), 1280, 700)
save("32-configlet-form", get(f"/configlets/{vlan}/edit"))
save("34-dospasos", get("/account/2fa/setup"), 1280, 1000)
save("35-acerca", requests.get(B + "/acerca").text, height=3200)
save("36-tarea-editar", get("/schedules/" + re.search(r"/schedules/(\d+)/edit", get("/schedules")).group(1) + "/edit"))
users = get("/users")
uid = re.search(r"<b>lector</b>[\s\S]*?/users/(\d+)/edit", users)
if uid:
    save("33-usuario-editar", get(f"/users/{uid.group(1)}/edit"))
# temas: el panel con cada tema (luego se vuelve a «automático»)
for theme in ["noche", "bosque", "contraste", "oscuro"]:
    s.post(B + "/account/theme", data={"theme": theme})
    save(f"34-tema-{theme}", get("/"), 1280, 900)
s.post(B + "/account/theme", data={"theme": "auto"})
# vista móvil del panel
json.dump({"pending_cr": pending_cr}, open(HERE / "_estado.json", "w"))
sys.exit(0)
