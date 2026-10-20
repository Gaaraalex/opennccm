"""Contraste de color según WCAG 2.1 de los tokens del diseño «Señal»: temas Claro, Sala de control (oscuro) y Alto
contraste, y los 7 colores de acento en los dos modos (texto ≥ 4.5:1; bordes de control y foco ≥ 3:1).
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
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{3,6})\b", m.group(1))) if m else {}


def rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def lum(hexcol):
    c = [v / 255 for v in rgb(hexcol)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def mix(a, b, pa):
    """color-mix(in srgb, a pa%, b)"""
    return "#" + "".join(f"{round(x * pa + y * (1 - pa)):02x}" for x, y in zip(rgb(a), rgb(b)))


light = tokens(r":root")
themes = {"claro": light,
          "oscuro": {**light, **tokens(r':root\[data-theme="oscuro"\]')},
          "contraste": {**light, **tokens(r':root\[data-theme="contraste"\]')}}
auto_dark = tokens(r'@media \(prefers-color-scheme:dark\)\{:root\[data-theme="auto"\]')
check("automático en modo oscuro = Sala de control", auto_dark and all(themes["oscuro"].get(k) == v for k, v in auto_dark.items()))

PAIRS = [  # (primer plano, fondo, mínimo, qué es)
    ("ink", "bg", 4.5, "texto sobre el fondo"), ("ink", "surface", 4.5, "texto en tarjetas"),
    ("ink", "surface-2", 4.5, "texto en zonas hundidas"),
    ("ink-muted", "bg", 4.5, "texto secundario sobre el fondo"), ("ink-muted", "surface", 4.5, "texto secundario en tarjetas"),
    ("ink-muted", "surface-2", 4.5, "texto secundario en zonas hundidas"),
    ("brand", "surface", 4.5, "enlaces en tarjetas"), ("brand", "bg", 4.5, "enlaces sobre el fondo"),
    ("on-brand", "brand", 4.5, "texto de los botones"), ("line-strong", "surface", 3, "borde de los campos"),
    ("brand", "surface", 3, "anillo de foco"),
    ("rail-ink", "rail", 4.5, "menú lateral"), ("rail-ink", "rail-2", 4.5, "menú lateral (al pasar)"),
    ("on-signal", "signal", 4.5, "texto sobre lima"),
    ("ok", "surface", 4.5, "texto ok"), ("crit", "surface", 4.5, "texto crítico"), ("warn", "surface", 4.5, "texto aviso"),
    ("major", "surface", 4.5, "texto mayor"), ("ok", "bg", 4.5, "texto ok sobre el fondo"),
    ("crit", "bg", 4.5, "texto crítico sobre el fondo"), ("warn", "bg", 4.5, "texto aviso sobre el fondo"),
    ("ok", "ok-soft", 4.5, "pastilla ok"), ("warn", "warn-soft", 4.5, "pastilla aviso"),
    ("major", "major-soft", 4.5, "pastilla mayor"), ("crit", "crit-soft", 4.5, "pastilla crítica"),
    ("ink", "ok-soft", 4.5, "aviso de éxito"), ("ink", "crit-soft", 4.5, "aviso de error"),
    ("on-crit", "crit", 4.5, "botón eliminar"),
]
for theme, t in themes.items():
    for fg, bg, minimum, what in PAIRS:
        if fg in t and bg in t:
            r = ratio(t[fg], t[bg])
            check(f"{theme}: {what} ({t[fg]} sobre {t[bg]}) {r:.2f}:1 ≥ {minimum}", r >= minimum)
    if "brand" in t and "surface" in t:
        soft = mix(t["brand"], t["surface"], 0.10)
        check(f"{theme}: enlace sobre su fondo suave ({t['brand']} sobre {soft})", ratio(t["brand"], soft) >= 4.5,
              f"{ratio(t['brand'], soft):.2f}")

# acentos: --brand por modo (claro: :root[data-accent=x]; oscuro: :root[data-theme="oscuro"][data-accent=x])
accents = sorted(set(re.findall(r':root\[data-accent="(\w+)"\]', CSS)))
check("hay 6 acentos además del cobalto", len(accents) == 6, accents)
for acc in accents:
    for mode, sel in (("claro", rf':root\[data-accent="{acc}"\]'), ("oscuro", rf':root\[data-theme="oscuro"\]\[data-accent="{acc}"\]')):
        t = {**themes[mode], **tokens(sel)}
        auto = tokens(rf':root\[data-theme="auto"\]\[data-accent="{acc}"\]') if mode == "oscuro" else {}
        if mode == "oscuro":
            check(f"acento {acc}: automático oscuro igual que Sala de control", auto.get("brand") == t["brand"])
        worst = min(ratio(t["brand"], t[k]) for k in ("bg", "surface", "surface-2"))
        soft = ratio(t["brand"], mix(t["brand"], t["surface"], 0.10))
        check(f"acento {acc} ({mode}, {t['brand']}): enlaces {worst:.2f}:1, sobre fondo suave {soft:.2f}:1 ≥ 4.5",
              worst >= 4.5 and soft >= 4.5)
        check(f"acento {acc} ({mode}): texto de botón {ratio(t['on-brand'], t['brand']):.2f}:1 ≥ 4.5",
              ratio(t["on-brand"], t["brand"]) >= 4.5)

# sin restos de los 10 temas anteriores
check("sin bloques de los temas anteriores", not re.search(r'data-theme="(noche|bosque|arena|oceano|atardecer|lavanda|grafito)"', CSS))

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
