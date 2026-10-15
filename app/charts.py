"""Gráficas SVG generadas en el servidor (sin JavaScript ni librerías externas).

Una sola serie por gráfica (color --series-1), cuadrícula tenue, extremos de barra redondeados
anclados a la línea base, tooltip nativo (<title>) en cada barra y etiquetas directas selectivas.
Los colores salen de variables CSS de style.css, con valores propios para modo oscuro.
"""
from __future__ import annotations

import math
from html import escape


def _nice_max(v: float) -> float:
    if v <= 0:
        return 1
    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * exp:
            return m * exp
    return 10 * exp


def _bar_v(x, y, w, h, r=4):
    """Barra vertical con esquinas superiores redondeadas y base recta."""
    r = min(r, w / 2, h)
    return (f"M{x:.1f},{y + h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
            f"H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} V{y + h:.1f} Z")


def _bar_h(x, y, w, h, r=4):
    """Barra horizontal con extremo derecho redondeado y base (izquierda) recta."""
    r = min(r, h / 2, w)
    return (f"M{x:.1f},{y:.1f} H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} "
            f"V{y + h - r:.1f} Q{x + w:.1f},{y + h:.1f} {x + w - r:.1f},{y + h:.1f} H{x:.1f} Z")


def _unit(v: float, unit: str) -> str:
    """'1 cambios' -> '1 cambio'."""
    return unit[:-1] if v == 1 and unit.endswith("s") else unit


def columns(data: list[tuple[str, float]], unit: str = "", height: int = 220, label_every: int = 5) -> str:
    """Serie temporal / categorías ordenadas como columnas verticales."""
    if not data:
        return '<p class="muted">Sin datos.</p>'
    w_plot, left, top, bottom = 760, 36, 10, 28
    h_plot = height - top - bottom
    vmax = _nice_max(max(v for _, v in data))
    n = len(data)
    slot = w_plot / n
    bw = max(2.0, min(28.0, slot - 2))  # 2px de separación entre barras
    parts = [f'<svg class="viz" viewBox="0 0 {left + w_plot + 4} {height}" role="img" '
             f'preserveAspectRatio="xMidYMid meet">']
    for i in range(5):  # cuadrícula y eje
        val = vmax * i / 4
        y = top + h_plot - h_plot * i / 4
        parts.append(f'<line class="{"axis" if i == 0 else "grid"}" x1="{left}" x2="{left + w_plot}" '
                     f'y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="tick" x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">{val:g}</text>')
    for i, (label, v) in enumerate(data):
        h = h_plot * v / vmax if vmax else 0
        x = left + i * slot + (slot - bw) / 2
        tip = escape(f"{label}: {v:g}{_unit(v, unit)}")
        parts.append(f'<g class="mark"><rect class="hit" x="{left + i * slot:.1f}" y="{top}" width="{slot:.1f}" '
                     f'height="{h_plot}"/>')
        if v > 0:
            parts.append(f'<path class="bar" d="{_bar_v(x, top + h_plot - h, bw, h)}"/>')
        parts.append(f"<title>{tip}</title></g>")
        if i % label_every == 0 or i == n - 1:
            parts.append(f'<text class="tick" x="{x + bw / 2:.1f}" y="{height - 8}" text-anchor="middle">'
                         f"{escape(label)}</text>")
    parts.append("</svg>")
    return "".join(parts)


def bars(data: list[tuple[str, float]], unit: str = "", vmax: float | None = None, link: list[str] | None = None,
         label_width: int = 220) -> str:
    """Barras horizontales con etiqueta de categoría y valor directo al final."""
    if not data:
        return '<p class="muted">Sin datos.</p>'
    row, gap, w_plot = 22, 6, 420
    height = len(data) * (row + gap) + 4
    vmax = vmax or _nice_max(max(v for _, v in data))
    parts = [f'<svg class="viz" viewBox="0 0 {label_width + w_plot + 110} {height}" role="img">']
    for i, (label, v) in enumerate(data):
        y = 2 + i * (row + gap)
        w = w_plot * v / vmax if vmax else 0
        lbl = escape(label if len(label) <= 34 else label[:33] + "…")
        tip = escape(f"{label}: {v:g}{_unit(v, unit)}")
        open_a = f'<a href="{escape(link[i])}">' if link else ""
        close_a = "</a>" if link else ""
        parts.append(f'{open_a}<g class="mark"><rect class="hit" x="0" y="{y}" width="{label_width + w_plot + 110}" '
                     f'height="{row}"/><text class="label" x="{label_width - 8}" y="{y + 15}" text-anchor="end">'
                     f'{lbl}</text>')
        parts.append(f'<rect class="track" x="{label_width}" y="{y + 3}" width="{w_plot}" height="{row - 6}" rx="3"/>')
        if v > 0:
            parts.append(f'<path class="bar" d="{_bar_h(label_width, y + 3, max(w, 3), row - 6)}"/>')
        parts.append(f'<text class="value" x="{label_width + w + 6:.1f}" y="{y + 15}">{v:g}{escape(_unit(v, unit))}</text>'
                     f"<title>{tip}</title></g>{close_a}")
    parts.append("</svg>")
    return "".join(parts)
