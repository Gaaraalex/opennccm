"""Capturas para el README: demo local (http://127.0.0.1:8000) con Edge vía Playwright, escala 2x.

Uso:  python readme_capturas.py CARPETA [TEMA[:ACENTO]] [panel,equipo,diff,cumplimiento,analisis,consola]
Luego: python readme_componer.py  (arma docs/readme/*.png)
"""
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

B = "http://127.0.0.1:8000"
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
THEME, _, ACCENT = (sys.argv[2] if len(sys.argv) > 2 else "oscuro").partition(":")
ONLY = sys.argv[3].split(",") if len(sys.argv) > 3 else None

SANITIZE = r"""() => {
  const rep = [[/C:\\Users\\[^)]*ncm\w*/g, '/var/lib/opennccm']];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n; while ((n = w.nextNode())) { for (const [a, b] of rep) n.nodeValue = n.nodeValue.replace(a, b); }
}"""


def want(k):
    return ONLY is None or k in ONLY


def tl_ids(page, kind=None):
    rows = re.findall(r'class="tl-row[^"]*" data-id="(\d+)"(?:(?!data-id=).)*?class="tl-kind">(\w+)<', page, re.S)
    return list(dict.fromkeys(i for i, k in rows if kind is None or k == kind))


with sync_playwright() as p:
    br = p.chromium.launch(channel="msedge")
    ctx = br.new_context(viewport={"width": 1280, "height": 800}, device_scale_factor=2,
                         color_scheme="dark" if THEME == "oscuro" else "light", reduced_motion="reduce")
    pg = ctx.new_page()
    pg.goto(B + "/login")
    pg.fill("input[name=username]", "admin"); pg.fill("input[name=password]", "Admin12345")
    pg.click("button[type=submit]"); pg.wait_for_load_state()
    pg.request.get(B + "/lang/es")
    pg.request.post(B + "/account/theme", form={"theme": THEME, "accent": ACCENT or "cobalto"})

    def go(path):
        pg.goto(B + path); pg.wait_for_load_state("networkidle"); pg.evaluate(SANITIZE)
        pg.add_style_tag(content="*{caret-color:transparent} ::-webkit-scrollbar{display:none}")
        pg.evaluate("document.fonts.ready"); pg.evaluate("window.scrollTo(0, 0)"); pg.wait_for_timeout(400)

    def shot(name, height=800):
        """Parte superior de la página (con la barra lateral), height px de alto."""
        pg.set_viewport_size({"width": 1280, "height": height})
        pg.evaluate("window.scrollTo(0, 0)"); pg.wait_for_timeout(200)
        pg.screenshot(path=str(OUT / f"{name}.png"))
        pg.set_viewport_size({"width": 1280, "height": 800})
        print("ok", name)

    if want("panel"):
        go("/")
        shot("panel", 860)
        shot("panelg", 860)
    if want("equipo"):
        go("/devices/1")
        shot("equipo", 900)
    if want("diff"):
        run = tl_ids(pg.request.get(B + "/devices/1").text(), "running")
        go(f"/diff?a={run[-1]}&b={run[0]}&view=side&ctx=3")
        shot("diff", 760)
    if want("cumplimiento"):
        go("/devices/1#compliance")
        shot("cumplimiento", 860)
    if want("analisis"):
        go("/analytics")
        shot("analisis", 900)
    if want("consola"):
        pg.set_viewport_size({"width": 1280, "height": 1000}); go("/devices/1/console")
        pg.wait_for_timeout(2500)
        pg.keyboard.type("admin\n"); pg.wait_for_timeout(800)
        pg.keyboard.type("cisco\n"); pg.wait_for_timeout(1500)
        for c in ("show version", "show running-config"):
            pg.keyboard.type(c + "\n"); pg.wait_for_timeout(1200)
        pg.screenshot(path=str(OUT / "consola.png")); print("ok consola")
    pg.request.post(B + "/account/theme", form={"theme": "auto", "accent": "cobalto"})
    br.close()
