"""Arma app/static/vendor-logos.svg (un símbolo «vl-<clave>» por marca) con los logos de app/static/vendor-logos/*.svg.

Los logos de Diseño UX traen texto (nombre o iniciales de la marca). Aquí se convierte a trazos con la fuente Geist de la
app, así se ven igual en cualquier navegador y el archivo se puede guardar en caché. Las fuentes de sistema que piden
algunos (Arial Black, Georgia) no viajan con la app: se usa Geist, que es la fuente de reserva que declaran.

Solo hace falta al cambiar los logos (herramienta de desarrollo): pip install fonttools brotli
Uso: python scripts/build_vendor_logos.py
"""
import io
import os
import re
import sys
import xml.etree.ElementTree as ET

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SRC = os.path.join(ROOT, "app", "static", "vendor-logos")
OUT = os.path.join(ROOT, "app", "static", "vendor-logos.svg")
FONT = os.path.join(ROOT, "app", "static", "fonts", "geist-latin-wght-normal.woff2")
NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)
_fonts: dict[int, TTFont] = {}


def font(weight: int) -> TTFont:
    if weight not in _fonts:
        f = TTFont(FONT)
        axis = next(a for a in f["fvar"].axes if a.axisTag == "wght")
        _fonts[weight] = instantiateVariableFont(f, {"wght": max(axis.minValue, min(axis.maxValue, weight))})
    return _fonts[weight]


def text_paths(el) -> list[str]:
    """<text> (con o sin <tspan>) → <path> con el mismo color, tamaño, peso, espaciado y alineación."""
    size = float(el.get("font-size", "10"))
    weight = int(el.get("font-weight", "400"))
    ls = float(el.get("letter-spacing", "0") or 0)
    x, y = float(el.get("x", "0")), float(el.get("y", "0"))
    runs = [(el.text or "", el.get("fill"))] + [(t.text or "", t.get("fill") or el.get("fill")) for t in el]
    runs = [(s, f) for s, f in runs if s]
    f = font(weight)
    upm, cmap, hmtx, gs = f["head"].unitsPerEm, f.getBestCmap(), f["hmtx"], f.getGlyphSet()
    k = size / upm
    chars = [(ch, fill) for s, fill in runs for ch in s]
    width = sum(hmtx[cmap.get(ord(ch), ".notdef")][0] * k + ls for ch, _ in chars)
    anchor = el.get("text-anchor", "start")
    cx = x - (width / 2 if anchor == "middle" else width if anchor == "end" else 0)
    out = []
    for ch, fill in chars:
        g = cmap.get(ord(ch), ".notdef")
        if ch.strip():
            pen = SVGPathPen(gs, ntos=lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
            gs[g].draw(TransformPen(pen, (k, 0, 0, -k, cx, y)))
            out.append(f'<path d="{pen.getCommands()}" fill="{fill or "#000"}"/>')
        cx += hmtx[g][0] * k + ls
    return out


def symbol(key: str, svg: str) -> str:
    root = ET.fromstring(svg)
    parts = []
    for el in root:
        tag = el.tag.split("}")[1]
        if tag == "text":
            parts += text_paths(el)
        else:
            s = ET.tostring(el, encoding="unicode")
            parts.append(re.sub(r'\sxmlns(:\w+)?="[^"]+"', "", s))
    return f'<symbol id="vl-{key}" viewBox="0 0 24 24">' + "".join(parts) + "</symbol>"


def main():
    keys = sorted(f[:-4] for f in os.listdir(SRC) if f.endswith(".svg"))
    body = "".join(symbol(k, open(os.path.join(SRC, k + ".svg"), encoding="utf-8").read()) for k in keys)
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write('<svg xmlns="http://www.w3.org/2000/svg">' + body + "</svg>\n")
    print(f"{len(keys)} logos → {os.path.relpath(OUT, ROOT)} ({os.path.getsize(OUT) // 1024} KB)")


if __name__ == "__main__":
    sys.exit(main())
