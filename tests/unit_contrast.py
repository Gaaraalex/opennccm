"""Contraste de color de los 6 temas según WCAG 2.1 (texto normal >= 4.5:1, textos grandes/insignias >= 3:1).
Lee los colores de app/static/style.css, así que falla si un cambio futuro empeora la legibilidad."""
import os
import re
import sys

CSS = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "style.css"),
           encoding="utf-8").read()
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def tokens(selector_regex):
    m = re.search(selector_regex + r"\{([^}]*)\}", CSS)
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{3,6})", m.group(1))) if m else {}


def lum(hexcol):
    h = hexcol.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    rgb = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


base = tokens(r":root")
themes = {"claro": base}
# todos los temas definidos en style.css (los nuevos se prueban solos)
for name in sorted(set(re.findall(r':root\[data-theme="(\w+)"\]\{', CSS)) - {"auto"}):
    themes[name] = {**base, **tokens(rf':root\[data-theme="{name}"\]')}

PAIRS = [  # (primer plano, fondo, mínimo, qué es)
    ("text", "bg", 4.5, "texto sobre el fondo"),
    ("text", "panel", 4.5, "texto en tarjetas"),
    ("muted", "bg", 4.5, "texto secundario sobre el fondo"),
    ("muted", "panel", 4.5, "texto secundario en tarjetas"),
    ("accent", "panel", 4.5, "enlaces en tarjetas"),
    ("accent", "bg", 4.5, "enlaces sobre el fondo"),
    ("on-accent", "accent", 4.5, "texto de los botones"),
    ("nav-text", "nav", 4.5, "menú superior"),
    ("flash-text", "flash-bg", 4.5, "mensajes de confirmación"),
    ("flash-err-text", "flash-err-bg", 4.5, "mensajes de error"),
    # textos de estado (números del panel, +/- de líneas, avisos) sobre tarjetas y fondo
    ("t-ok", "panel", 4.5, "texto ok"), ("t-crit", "panel", 4.5, "texto error"),
    ("t-warn", "panel", 4.5, "texto aviso"), ("t-major", "panel", 4.5, "texto major"),
    ("t-info", "panel", 4.5, "texto info"), ("t-ok", "bg", 4.5, "texto ok sobre fondo"),
    ("t-crit", "bg", 4.5, "texto error sobre fondo"), ("t-warn", "bg", 4.5, "texto aviso sobre fondo"),
    # insignias de estado: texto blanco sobre su color
    ("#ffffff", "ok", 4.5, "insignia ok"), ("#ffffff", "crit", 4.5, "insignia error/crítico"),
    ("#ffffff", "badge-major", 4.5, "insignia major"), ("#ffffff", "badge-warn", 4.5, "insignia warning/pendiente"),
    ("#ffffff", "badge-info", 4.5, "insignia info"), ("#ffffff", "badge-muted", 4.5, "insignia neutra"),
]
for theme, t in themes.items():
    for fg, bg, minimum, what in PAIRS:
        fgc = fg if fg.startswith("#") else t.get(fg)
        if fgc and bg in t:
            r = ratio(fgc, t[bg])
            check(f"{theme}: {what} ({fgc} sobre {t[bg]}) {r:.2f}:1 ≥ {minimum}", r >= minimum)

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
