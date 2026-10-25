"""Compone las imágenes del README (portada, galería y mosaico de temas) con HTML/CSS y Edge.

Espera las capturas de readme_capturas.py en _readme/shots y _readme/temas/<tema>/ (una pasada por tema con «panel»).
Uso:  python readme_componer.py [hero,panel,equipo,diff,consola,cumplimiento,analisis,temas]
"""
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent / "_readme"
SH = HERE / "shots"
TEM = HERE / "temas"
OUT = HERE.parent.parent / "readme"; OUT.mkdir(parents=True, exist_ok=True)
LOGO = (HERE.parent.parent.parent / "app" / "static" / "icon-512.png").as_uri()
WORK = HERE / "html"; WORK.mkdir(parents=True, exist_ok=True)

# la consola: se quita el área vacía bajo el prompt
c = Image.open(SH / "consola.png")
c.crop((0, 0, c.width, 1790)).save(SH / "consola-c.png")

BASE = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,sans-serif;color:#e8eef8;-webkit-font-smoothing:antialiased}
.win{border-radius:18px;overflow:hidden;background:#0d1117;
     box-shadow:0 40px 80px -20px rgba(0,0,0,.65),0 0 0 1px rgba(255,255,255,.09)}
.bar{height:44px;display:flex;align-items:center;gap:9px;padding:0 18px;background:#1b2230;
     border-bottom:1px solid rgba(255,255,255,.06)}
.bar i{width:13px;height:13px;border-radius:50%;display:block}
.bar i:nth-child(1){background:#ff5f57}.bar i:nth-child(2){background:#febc2e}.bar i:nth-child(3){background:#28c840}
.url{margin-left:16px;flex:1;max-width:520px;height:26px;border-radius:13px;background:#0f1520;color:#8b98ab;
     font-size:14px;display:flex;align-items:center;padding:0 14px;gap:8px}
.url b{color:#56d364;font-weight:600}
.shot{display:block;width:100%}
"""


def frame(img, url, height=None, fade=True):
    style = ""
    if height:
        h = Image.open(img).height * 1.0
        style = f"max-height:{height}px;overflow:hidden"
        if fade and h / Image.open(img).width * 1456 > height:
            style += ";-webkit-mask-image:linear-gradient(#000 84%,transparent)"
    return (f'<div class="win"><div class="bar"><i></i><i></i><i></i><div class="url"><b>🔒</b>{url}</div></div>'
            f'<div style="{style}"><img class="shot" src="{img.as_uri()}"></div></div>')


def render(pg, name, html, w, h):
    f = WORK / f"{name}.html"
    f.write_text(f"<!doctype html><meta charset=utf-8><style>{BASE}</style>{html}", encoding="utf-8")
    pg.set_viewport_size({"width": w, "height": h})
    pg.goto(f.as_uri()); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(300)
    png = OUT / f"{name}.png"
    pg.screenshot(path=str(png))
    Image.open(png).convert("RGB").save(png, optimize=True)  # sin canal alfa: pesa menos
    print("ok", name, png.stat().st_size // 1024, "KB")


HERO = f"""
<style>
.hero{{width:1760px;height:860px;position:relative;overflow:hidden;
  background:radial-gradient(900px 600px at 85% 20%,#1f4fd166,transparent 70%),
             radial-gradient(700px 500px at 10% 100%,#7c3aed44,transparent 70%),
             linear-gradient(135deg,#070b16,#0d1630 55%,#0a1222)}}
.grid{{position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px),
  linear-gradient(90deg,rgba(255,255,255,.035) 1px,transparent 1px);background-size:44px 44px;
  -webkit-mask-image:radial-gradient(circle at 30% 40%,#000,transparent 75%)}}
.txt{{position:absolute;left:96px;top:150px;width:640px}}
.logo{{display:flex;align-items:center;gap:22px}}
.logo img{{width:96px;height:96px;border-radius:22px;box-shadow:0 12px 40px #2347e866}}
.logo h1{{font-size:84px;font-weight:800;letter-spacing:-2px}}
.logo h1 span{{color:#c6f432}}
.tag{{margin-top:34px;font-size:33px;line-height:1.35;color:#c9d6ea;font-weight:400}}
.tag b{{color:#fff;font-weight:600}}
.chips{{margin-top:40px;display:flex;flex-wrap:wrap;gap:12px}}
.chips span{{font-size:20px;padding:9px 18px;border-radius:999px;background:rgba(255,255,255,.06);
  border:1px solid rgba(255,255,255,.12);color:#dce6f5}}
.chips span:before{{content:'';display:inline-block;width:8px;height:8px;border-radius:50%;background:#4c9bff;
  margin-right:10px;vertical-align:middle;position:relative;top:-2px}}
.chips span:nth-child(2n):before{{background:#3fb950}}.chips span:nth-child(3n):before{{background:#d29922}}
.w1{{position:absolute;left:820px;top:96px;width:1060px;transform:perspective(2200px) rotateY(-14deg) rotateX(4deg);
  transform-origin:left center}}
.w2{{position:absolute;left:1000px;top:470px;width:820px;transform:perspective(2200px) rotateY(-14deg) rotateX(4deg);
  transform-origin:left center}}
</style>
<div class="hero"><div class="grid"></div>
<div class="w1">{frame(SH / "panel.png", "opennccm.empresa.local")}</div>
<div class="w2">{frame(SH / "diff.png", "opennccm.empresa.local/diff", height=420)}</div>
<div class="txt">
 <div class="logo"><img src="{LOGO}"><h1>Open<span>NCCM</span></h1></div>
 <p class="tag">Respaldos, cambios y cumplimiento de tu red, <b>en tu propio servidor</b>. Libre y en español.</p>
 <div class="chips"><span>126 plataformas</span><span>Diff y rollback</span><span>Cumplimiento</span>
 <span>CVE y EOL</span><span>Consola SSH web</span><span>2FA</span></div>
</div></div>
"""

CARDS = [  # nombre, imagen, título, url, color del brillo
    ("panel", "panelg.png", "Panel de control", "/", "#2347e8"),
    ("equipo", "equipo.png", "Inventario y versiones", "/devices/1", "#7c3aed"),
    ("diff", "diff.png", "Comparación lado a lado", "/diff", "#16a34a"),
    ("consola", "consola-c.png", "Consola SSH en el navegador", "/devices/1/console", "#0d9488"),
    ("cumplimiento", "cumplimiento.png", "Cumplimiento con corrección", "/devices/1", "#dc2626"),
    ("analisis", "analisis.png", "Análisis de cambios", "/analytics", "#d97706"),
]


def card(img, title, url, glow):
    return f"""
<style>
.card{{width:1600px;height:1000px;position:relative;overflow:hidden;padding:64px 72px 0;
  background:radial-gradient(1100px 700px at 100% 0%,{glow}55,transparent 70%),
             radial-gradient(900px 600px at 0% 100%,{glow}22,transparent 70%),
             linear-gradient(160deg,#080c18,#0e1526)}}
.card h2{{font-size:64px;font-weight:700;letter-spacing:-1px;margin-bottom:44px;display:flex;align-items:center;gap:22px}}
.card h2:before{{content:'';width:14px;height:58px;border-radius:7px;background:{glow}}}
</style>
<div class="card"><h2>{title}</h2>{frame(SH / img, "opennccm.empresa.local" + url, height=760)}</div>"""


THEMES = [("claro", "Claro"), ("oscuro", "Sala de control"), ("contraste", "Alto contraste"),
          ("claro-bosque", "Claro · acento bosque"), ("oscuro-atardecer", "Oscuro · acento atardecer")]
tiles = "".join(f'<figure><div class="t"><img src="{(TEM / t / "panel.png").as_uri()}"></div>'
                f'<figcaption>{n}</figcaption></figure>' for t, n in THEMES)
MOSAIC = f"""
<style>
.m{{width:1760px;height:470px;padding:64px 72px;
  background:radial-gradient(900px 500px at 50% 0%,#4c1d9555,transparent 70%),linear-gradient(160deg,#080c18,#0e1526)}}
.m h2{{font-size:56px;font-weight:700;letter-spacing:-1px}}
.m h2 span{{color:#c6f432}}
.m p{{font-size:26px;color:#9fb0c8;margin:10px 0 40px}}
.g{{display:grid;grid-template-columns:repeat(5,1fr);gap:26px}}
figure .t{{border-radius:14px;overflow:hidden;aspect-ratio:16/8.4;box-shadow:0 18px 40px -12px #000c,0 0 0 1px #ffffff1c}}
figure img{{width:100%;height:100%;object-fit:cover;object-position:left top;display:block}}
figcaption{{text-align:center;font-size:22px;margin-top:12px;color:#dbe4f2;font-weight:600}}
</style>
<div class="m"><h2><span>3 temas y 7 acentos</span> para elegir</h2><p>Claro, Sala de control (oscuro) y alto contraste, con el color de acento de cada usuario. Todos cumplen WCAG AA.</p>
<div class="g">{tiles}</div></div>"""

with sync_playwright() as p:
    br = p.chromium.launch(channel="msedge")
    pg = br.new_page(device_scale_factor=1)
    only = sys.argv[1].split(",") if len(sys.argv) > 1 else None
    if not only or "hero" in only:
        render(pg, "hero", HERO, 1760, 860)
    for name, img, title, url, glow in CARDS:
        if not only or name in only:
            render(pg, name, card(img, title, url, glow), 1600, 1000)
    if not only or "temas" in only:
        render(pg, "temas", MOSAIC, 1760, 470)
    br.close()
