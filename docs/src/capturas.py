"""Genera capturas de pantalla reales de OpenNCCM (demo local) para la documentación.

Abre cada página en Microsoft Edge (Playwright) con la sesión del administrador, en el tema claro, y recorta el
espacio vacío inferior. Las páginas que son respuesta de un POST se muestran en el mismo origen con set_content,
para que carguen los iconos, las fuentes y los scripts de la interfaz.
Uso:  python capturas.py   (con la demo corriendo en http://127.0.0.1:8000)
"""
import json
import re
import sys
from pathlib import Path

import requests
from PIL import Image
from playwright.sync_api import sync_playwright

B = "http://127.0.0.1:8000"
HERE = Path(__file__).resolve().parent
OUT = HERE / "img"
OUT.mkdir(exist_ok=True)

s = requests.Session()
s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
s.get(B + "/lang/es")
s.post(B + "/account/theme", data={"theme": "claro", "accent": "cobalto"})


def crop(png: Path):
    im = Image.open(png).convert("RGB")
    w, h = im.size
    bg = im.getpixel((w - 2, h - 2))
    px = im.load()
    last = h - 1
    while last > 0 and all(px[x, last] == bg for x in range(0, w, 7)):
        last -= 1
    im = im.crop((0, 0, w, min(h, last + 24)))
    # paleta de 256 colores sin tramado: en capturas de interfaz no se nota y los PDF pesan la mitad
    im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(png, optimize=True)


def get(path):
    r = s.get(B + path)
    r.raise_for_status()
    return r.text


def tl_ids(page, kind=None):
    """Ids de la línea de tiempo de versiones de la ficha (más reciente primero)."""
    rows = re.findall(r'class="tl-row[^"]*" data-id="(\d+)"(?:(?!data-id=).)*?class="tl-kind">(\w+)<', page, re.S)
    return list(dict.fromkeys(i for i, k in rows if kind is None or k == kind))


pw = sync_playwright().start()
browser = pw.chromium.launch(channel="msedge")


def _page(width, height, anon=False):
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme="light", reduced_motion="reduce")
    if not anon:
        ctx.add_cookies([{"name": c.name, "value": c.value, "url": B} for c in s.cookies])
    return ctx.new_page()


def save(name: str, path: str = None, width=1280, height=900, html: str = None, anon=False, full=True):
    """Captura `path` (GET en el navegador) o el HTML dado (respuesta de un POST) en el mismo origen."""
    pg = _page(width, height, anon)
    if html is None:
        pg.goto(B + path)
    else:
        pg.goto(B + (path or "/"))
        pg.set_content(html)
    pg.wait_for_load_state("networkidle")
    pg.evaluate("document.fonts.ready")
    pg.evaluate("window.scrollTo(0, 0)")  # las pestañas con #hash desplazan la página
    pg.wait_for_timeout(400)
    png = OUT / f"{name}.png"
    pg.screenshot(path=str(png), full_page=full)
    pg.context.close()
    crop(png)
    print("ok", name)


# ---------------------------------------------------------------- datos para las capturas
s.post(B + "/devices/1/backup")
dev = get("/devices/1")
running = tl_ids(dev, "running")
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
pci = re.search(r'/compliance/policies/(\d+)"[^>]*>PCI DSS 4.0 \(IOS\)', comp).group(1)

save("01-login", "/login", 1280, 800, anon=True, full=False)
save("02-panel", "/")
save("03-dispositivos", "/devices")
save("04-dispositivo-nuevo", "/devices/new")
save("04b-importar-csv", "/devices/import")
save("05-dispositivo", "/devices/1")
save("05b-dispositivo-versiones", "/devices/1#versions")
save("05c-dispositivo-cumplimiento", "/devices/1#compliance", height=1200, full=False)
save("06-version", f"/versions/{running[0]}")
save("07-diff-lado-a-lado", f"/diff?a={running[-1]}&b={running[0]}&view=side&ctx=3")
save("08-diff-unificada", f"/diff?a={running[-1]}&b={running[0]}&view=unified&ctx=3")
save("09-busqueda", "/search?q=snmp")
save("10-configlets", "/configlets")
save("11-configlet-ejecutar", f"/configlets/{vlan}/run")
save("12-configlet-exec-resultado", f"/configlets/{exec_id}/run",
     html=s.post(f"{B}/configlets/{exec_id}/run", data={"device_ids": "1"}).text)
save("13-solicitudes", "/changes")
save("14-solicitud-pendiente", f"/changes/{pending_cr}")
save("15-cumplimiento", "/compliance", height=1400, full=False)
save("16-politica-pci", f"/compliance/policies/{pci}")
save("17-firmware", "/firmware")
save("18-eol", "/eol")
save("19-vulnerabilidades", "/vulns", height=1400, full=False)
save("20-tareas", "/schedules")
save("21-alarmas", "/events", height=1400, full=False)
save("22-analisis", "/analytics")
save("23-informes", "/reports")
save("24-informe-inventario", "/reports/view/inventory")
save("25-credenciales", "/credentials")
save("26-usuarios", "/users")
save("27-auditoria", "/audit", height=1400, full=False)
save("28-sistema", "/system")
save("29-ajustes", "/settings")
save("30-plataformas", "/vendors", height=1400, full=False)
save("31-cuenta", "/account")
save("32-configlet-form", f"/configlets/{vlan}/edit")
save("34-dospasos", "/account/2fa/setup")
save("35-acerca", "/acerca")
save("36-tarea-editar", "/schedules/" + re.search(r"/schedules/(\d+)/edit", get("/schedules")).group(1) + "/edit")
pg = _page(1280, 760)  # paleta Ctrl K: buscar un equipo o ir a una página
pg.goto(B + "/devices"); pg.wait_for_load_state("networkidle")
pg.keyboard.press("Control+k"); pg.keyboard.type("r1"); pg.wait_for_timeout(600)
pg.screenshot(path=str(OUT / "37-ctrl-k.png")); pg.context.close(); print("ok", "37-ctrl-k")
users = get("/users")
uid = re.search(r"<b>lector</b>[\s\S]*?/users/(\d+)/edit", users)
if uid:
    save("33-usuario-editar", f"/users/{uid.group(1)}/edit")
# temas: el panel con cada tema y dos acentos de ejemplo (luego se vuelve a «automático»)
for name, theme, accent in [("34-tema-claro", "claro", "cobalto"), ("34-tema-oscuro", "oscuro", "cobalto"),
                            ("34-tema-contraste", "contraste", "cobalto"), ("34-acento-bosque", "claro", "bosque"),
                            ("34-acento-atardecer", "oscuro", "atardecer")]:
    s.post(B + "/account/theme", data={"theme": theme, "accent": accent})
    save(name, "/", 1280, 900, full=False)
s.post(B + "/account/theme", data={"theme": "auto", "accent": "cobalto"})
browser.close()
pw.stop()
json.dump({"pending_cr": pending_cr}, open(HERE / "_estado.json", "w"))
sys.exit(0)
