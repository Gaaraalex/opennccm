"""Convierte las capturas del asistente (texto ANSI tomado con tmux en Ubuntu, ver tests/tui_drive.py)
en imágenes PNG para la documentación.  Uso:  python tui_png.py CARPETA_CAPTURAS
"""
import subprocess
import sys
from pathlib import Path

from ansi2html import Ansi2HTMLConverter
from PIL import Image

HERE = Path(__file__).resolve().parent
SRC = Path(sys.argv[1])
OUT = HERE / "img"
TMP = HERE / "_pages"
TMP.mkdir(exist_ok=True)
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
conv = Ansi2HTMLConverter(dark_bg=True, scheme="xterm", inline=True)

CSS = """
html,body{margin:0;background:#1e2430}
.win{display:inline-block;margin:18px;border-radius:10px;overflow:hidden;box-shadow:0 8px 28px rgba(0,0,0,.45);background:#0c0c0c}
.bar{height:30px;background:#2d3340;display:flex;align-items:center;padding:0 12px;gap:7px;font:12px 'Segoe UI',sans-serif;color:#aab4c3}
.dot{width:11px;height:11px;border-radius:50%}
pre{margin:0;padding:8px 10px;font:15px/1.18 'Cascadia Mono',Consolas,monospace;color:#d0d0d0;background:#000}
"""

for ans in sorted(SRC.glob("*.ans")):
    body = conv.convert(ans.read_text(encoding="utf-8"), full=False)
    html = (f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>"
            "<div class='win'><div class='bar'><span class='dot' style='background:#ff5f57'></span>"
            "<span class='dot' style='background:#febc2e'></span><span class='dot' style='background:#28c840'></span>"
            f"&nbsp;&nbsp;usuario@servidor: ~</div><pre>{body}</pre></div></body></html>")
    page = TMP / f"tui-{ans.stem}.html"
    page.write_text(html, encoding="utf-8")
    png = OUT / f"tui-{ans.stem}.png"
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=1000,760",
                    f"--screenshot={png}", page.as_uri()], check=True, capture_output=True, timeout=60)
    im = Image.open(png).convert("RGB")
    bg = im.getpixel((im.width - 2, im.height - 2))
    px = im.load()
    # recortar el fondo sobrante a la derecha y abajo
    right = im.width - 1
    while right > 0 and all(px[right, y] == bg for y in range(0, im.height, 5)):
        right -= 1
    bottom = im.height - 1
    while bottom > 0 and all(px[x, bottom] == bg for x in range(0, im.width, 5)):
        bottom -= 1
    im.crop((0, 0, min(im.width, right + 18), min(im.height, bottom + 18))).save(png, optimize=True)
    print("ok", png.name, Image.open(png).size)
