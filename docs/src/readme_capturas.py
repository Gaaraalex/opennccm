"""Capturas por secciones para el README: demo local (http://127.0.0.1:8000) con Edge vía Playwright, escala 2x.

Uso:  python readme_capturas.py CARPETA [TEMA] [panel,panelg,equipo,diff,consola,...]
Luego: python readme_componer.py  (arma docs/readme/*.png)
"""
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

B = "http://127.0.0.1:8000"
OUT = Path(sys.argv[1]); OUT.mkdir(exist_ok=True)
THEME = sys.argv[2] if len(sys.argv) > 2 else "oscuro"
ONLY = sys.argv[3].split(",") if len(sys.argv) > 3 else None

SANITIZE = r"""() => {
  const rep = [[/C:\\Users\\[^)]*ncmtest/g, '/var/lib/opennccm']];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n; while ((n = w.nextNode())) { for (const [a, b] of rep) n.nodeValue = n.nodeValue.replace(a, b); }
  document.querySelectorAll('option').forEach(o => { for (const [a, b] of rep) o.textContent = o.textContent.replace(a, b); });
}"""

BOX = r"""([start, end, pad]) => {
  // top: el encabezado que contiene `start`; bottom: el final del bloque (card/grid) que contiene `end`
  const find = t => [...document.querySelectorAll('h1,h2,h3,b,small,label,th')].find(e => e.textContent.trim().startsWith(t));
  const s = find(start), e = find(end);
  const top = s.getBoundingClientRect().top + scrollY;
  const blk = e.closest('.card') || e.closest('.grid') || e;
  const bottom = blk.getBoundingClientRect().bottom + scrollY;
  return [Math.max(0, top - pad), bottom + pad];
}"""


def want(k):
    return ONLY is None or k in ONLY


with sync_playwright() as p:
    br = p.chromium.launch(channel="msedge")
    ctx = br.new_context(viewport={"width": 1280, "height": 800}, device_scale_factor=2, color_scheme="dark")
    pg = ctx.new_page()
    pg.goto(B + "/login")
    pg.fill("input[name=username]", "admin"); pg.fill("input[name=password]", "Admin12345")
    pg.click("button[type=submit]"); pg.wait_for_load_state()
    pg.request.post(B + "/account/theme", form={"theme": THEME})

    def go(path):
        pg.goto(B + path); pg.wait_for_load_state("networkidle"); pg.evaluate(SANITIZE)
        pg.add_style_tag(content="*{caret-color:transparent} ::-webkit-scrollbar{display:none}")

    def nav_h():
        return pg.evaluate("document.querySelector('header,nav').getBoundingClientRect().bottom")

    def region(name, y0, y1, x0=0, x1=1280, with_nav=False):
        """Recorte [y0,y1]; si with_nav, se antepone la barra superior (como si se hubiese desplazado)."""
        full = OUT / f"_{name}_full.png"
        pg.screenshot(path=str(full), full_page=True)
        from PIL import Image
        im = Image.open(full)
        k = 2
        crop = im.crop((x0 * k, int(y0 * k), x1 * k, int(y1 * k)))
        if with_nav:
            nh = int(nav_h() * k)
            nav = im.crop((0, 0, im.width, nh))
            out = Image.new("RGB", (crop.width, nh + crop.height))
            out.paste(nav.crop((x0 * k, 0, x1 * k, nh)), (0, 0)); out.paste(crop, (0, nh))
            crop = out
        crop.save(OUT / f"{name}.png", optimize=True)
        full.unlink()
        print("ok", name, crop.size)

    def box(start, end, pad=14):
        return pg.evaluate(BOX, [start, end, pad])

    if want("panel"):
        go("/")
        y0, y1 = box("Panel", "Cambios por aprobar", 18)
        region("panel", 0, y1)
    if want("panelg"):
        go("/")
        _, y1 = box("Rendimiento del servidor", "Encendido desde hace", 8)
        region("panelg", 0, y1)
    if want("rendimiento"):
        go("/")
        y0, y1 = box("Rendimiento del servidor", "Espacio y memoria", 14)
        region("rendimiento", y0, y1, with_nav=True)
    if want("red"):
        go("/")
        y0, _ = box("Cambios de configuración por día", "Cambios de configuración por día", 16)
        _, y1 = box("Últimos cambios", "Eventos recientes", 16)
        region("red", y0, y1, with_nav=True)
    if want("equipo"):
        go("/devices/1")
        _, y1 = box("Inventario", "Versiones de configuración", 16)
        region("equipo", 0, y1)
    if want("diff"):
        run = re.findall(r'<td>(\d+)</td><td class="small">[^<]*</td><td>running</td>', pg.request.get(B + "/devices/1").text())
        go(f"/diff?a=1&b={run[0]}&view=side&ctx=3")
        h = pg.evaluate("document.querySelector('main, .container, body').scrollHeight")
        foot = pg.evaluate("(document.querySelector('footer')||{getBoundingClientRect:()=>({top:document.body.scrollHeight})}).getBoundingClientRect().top + scrollY")
        y0, _ = box("Comparación de configuraciones", "Comparación de configuraciones", 22)
        region("diff", y0, foot - 30)
    if want("cumplimiento"):
        go("/devices/1")
        y0, _ = box("Cumplimiento", "Cumplimiento", 16)
        region("cumplimiento", y0, y0 + 560, with_nav=True)
    if want("analisis"):
        go("/analytics")
        y0, _ = box("Cuándo cambia la red", "Cuándo cambia la red", 26)
        _, y1 = box("Equipos con más cambios", "Quién hace los cambios", 14)
        region("analisis", y0, y1, with_nav=True)
    if want("tareas"):
        go("/schedules")
        region("tareas", 0, pg.evaluate("document.body.scrollHeight") - 60)
    if want("vulns"):
        go("/vulns")
        region("vulns", 0, 900)
    if want("consola"):
        pg.set_viewport_size({"width": 1280, "height": 1000}); go("/devices/1/console")
        pg.wait_for_timeout(2500)
        txt = pg.evaluate("document.body.innerText")
        if "Usuario" in txt or True:
            pg.keyboard.type("admin\n"); pg.wait_for_timeout(800)
            pg.keyboard.type("cisco\n"); pg.wait_for_timeout(1500)
            for c in ("show version", "show running-config"):
                pg.keyboard.type(c + "\n"); pg.wait_for_timeout(1200)
        region("consola", 0, 1000)
    pg.request.post(B + "/account/theme", form={"theme": "auto"})
    br.close()
