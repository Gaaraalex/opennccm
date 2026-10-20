"""Gráficas SVG generadas en el servidor (sin librerías externas).

Reglas de diseño (iguales en todas las gráficas):
- marcas finas, extremos redondeados anclados a la línea base, 2 px de separación entre segmentos;
- color por función: identidad (--cat-1…4, en orden fijo), magnitud (un solo tono, --series-1, de claro a oscuro)
  y estado (--ok / --warn / --crit, siempre con icono y texto, nunca solo color);
- cuadrícula y ejes tenues; los textos usan los colores de texto del tema, no el de la serie;
- cada marca lleva `data-tip` (información emergente de static/viz.js) y la gráfica un resumen en aria-label.
Los colores salen de variables CSS de style.css, con valores propios para cada tema.
"""
from __future__ import annotations

import math
from html import escape

from .i18n import N_, _, _f


def _nice_max(v: float) -> float:
    if v <= 0:
        return 1
    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * exp:
            return m * exp
    return 10 * exp


def _num(v: float) -> str:
    return f"{v:g}" if abs(v) < 10000 else f"{v:,.0f}"


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


_WORDS = {"cambios": (N_("cambio"), N_("cambios")), "equipos": (N_("equipo"), N_("equipos"))}


def _unit(v: float, unit: str) -> str:
    """Unidad en singular o plural: unit es la palabra en plural («cambios», «equipos») o «%»."""
    if unit.strip() in ("%", ""):
        return unit.strip()
    one, many = _WORDS.get(unit.strip(), (unit.strip(), unit.strip()))
    return " " + _(one if v == 1 else many)


def _attr(s: str) -> str:
    return escape(s, quote=True)


def _empty() -> str:
    return f'<p class="muted viz-empty">{escape(_("Sin datos en este periodo."))}</p>'


def _grid(parts, left, top, w_plot, h_plot, vmax):
    for i in range(5):
        val = vmax * i / 4
        y = top + h_plot - h_plot * i / 4
        parts.append(f'<line class="{"axis" if i == 0 else "grid"}" x1="{left}" x2="{left + w_plot}" '
                     f'y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="tick" x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">{val:g}</text>')


# --------------------------------------------------------------------------- columnas (una serie)
def columns(data: list[tuple[str, float]], unit: str = "", height: int = 220, label_every: int = 5,
            width: int = 800) -> str:
    """Serie temporal / categorías ordenadas como columnas verticales."""
    if not data or not any(v for _k, v in data):
        return _empty()
    left, top, bottom = 36, 10, 28
    w_plot = width - left - 4
    h_plot = height - top - bottom
    vmax = _nice_max(max(v for _k, v in data))
    n = len(data)
    slot = w_plot / n
    bw = max(2.0, min(28.0, slot - 2))
    total = sum(v for _k, v in data)
    parts = [f'<svg class="viz" viewBox="0 0 {left + w_plot + 4} {height}" role="img" '
             f'aria-label="{_attr(_f("{0} en total; máximo {1} en un día", f"{_num(total)}{_unit(total, unit)}", _num(max(v for _k, v in data))))}" '
             f'preserveAspectRatio="xMidYMid meet">']
    _grid(parts, left, top, w_plot, h_plot, vmax)
    for i, (label, v) in enumerate(data):
        h = h_plot * v / vmax if vmax else 0
        x = left + i * slot + (slot - bw) / 2
        tip = f"{label}: {_num(v)}{_unit(v, unit)}"
        parts.append(f'<g class="mark" data-tip="{_attr(tip)}"><rect class="hit" x="{left + i * slot:.1f}" y="{top}" '
                     f'width="{slot:.1f}" height="{h_plot}"/>')
        if v > 0:
            parts.append(f'<path class="bar" d="{_bar_v(x, top + h_plot - h, bw, h)}"/>')
        parts.append("</g>")
        if (i % label_every == 0 and n - 1 - i >= label_every // 2) or i == n - 1:
            parts.append(f'<text class="tick" x="{x + bw / 2:.1f}" y="{height - 8}" text-anchor="middle">'
                         f"{escape(label)}</text>")
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------- columnas apiladas (identidad)
def stacked_columns(axis: list[str], series: list[tuple[str, str, list[float]]], unit: str = " cambios",
                    height: int = 250, label_every: int = 5, width: int = 1180) -> str:
    """Columnas apiladas por categoría. series: [(etiqueta, clase css c1…c4, valores por día)]."""
    totals = [sum(s[2][i] for s in series) for i in range(len(axis))]
    if not axis or not any(totals):
        return _empty()
    left, top, bottom = 36, 12, 28
    w_plot = width - left - 4
    h_plot = height - top - bottom
    vmax = _nice_max(max(totals))
    n = len(axis)
    slot = w_plot / n
    bw = max(3.0, min(34.0, slot * 0.7))
    gap = 2 if bw >= 6 else 1
    summary = _f("{0} en total; máximo {1} en un día", f"{_num(sum(totals))}{_unit(sum(totals), unit)}",
                 _num(max(totals)))
    parts = [f'<svg class="viz" viewBox="0 0 {left + w_plot + 4} {height}" role="img" aria-label="{_attr(summary)}" '
             f'preserveAspectRatio="xMidYMid meet">']
    _grid(parts, left, top, w_plot, h_plot, vmax)
    for i, label in enumerate(axis):
        x = left + i * slot + (slot - bw) / 2
        tip = [f"{label} · {_num(totals[i])}{_unit(totals[i], unit)}"]
        segs = [(lbl, cls, vals[i]) for lbl, cls, vals in series if vals[i] > 0]
        tip += [f"{lbl}: {_num(v)}" for lbl, _c, v in segs]
        parts.append(f'<g class="mark" data-tip="{_attr(chr(10).join(tip))}"><rect class="hit" '
                     f'x="{left + i * slot:.1f}" y="{top}" width="{slot:.1f}" height="{h_plot}"/>')
        base = top + h_plot
        for j, (_lbl, cls, v) in enumerate(segs):
            h = h_plot * v / vmax
            last = j == len(segs) - 1
            hh = max(h - (0 if last else gap), 1)  # separación de 2 px entre segmentos
            if last:
                parts.append(f'<path class="seg {cls}" d="{_bar_v(x, base - h, bw, h)}"/>')
            else:
                parts.append(f'<rect class="seg {cls}" x="{x:.1f}" y="{base - h + (h - hh):.1f}" width="{bw:.1f}" '
                             f'height="{hh:.1f}"/>')
            base -= h
        parts.append("</g>")
        if i % label_every == 0 or i == n - 1:
            parts.append(f'<text class="tick" x="{x + bw / 2:.1f}" y="{height - 8}" text-anchor="middle">'
                         f"{escape(label)}</text>")
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------- anillo (parte del todo)
def donut(parts_in: list[tuple[str, str, float]], center_label: str, unit: str = " cambios") -> str:
    """Anillo con el total en el centro. parts_in: [(etiqueta, clase c1…c4, valor)]. Incluye leyenda con valores."""
    total = sum(v for *_x, v in parts_in)
    if not total:
        return _empty()
    size, r, sw = 200, 74, 26
    cx = cy = size / 2
    circ = 2 * math.pi * r
    gap = 2.5 if len([p for p in parts_in if p[2]]) > 1 else 0
    out = [f'<div class="donut"><svg class="viz" viewBox="0 0 {size} {size}" role="img" '
           f'aria-label="{_attr(", ".join(f"{lbl}: {_num(v)}" for lbl, _c, v in parts_in))}">',
           f'<circle class="track-ring" cx="{cx}" cy="{cy}" r="{r}" stroke-width="{sw}" fill="none"/>']
    start = 0.0
    for lbl, cls, v in parts_in:
        if not v:
            continue
        frac = v / total
        length = max(circ * frac - gap, 0.5)
        tip = f"{lbl}: {_num(v)}{_unit(v, unit)} ({100 * frac:.0f}%)"
        out.append(f'<circle class="arc {cls}" data-tip="{_attr(tip)}" cx="{cx}" cy="{cy}" r="{r}" fill="none" '
                   f'stroke-width="{sw}" stroke-dasharray="{length:.2f} {circ - length:.2f}" '
                   f'stroke-dashoffset="{-start:.2f}" transform="rotate(-90 {cx} {cy})"/>')
        start += circ * frac
    out.append(f'<text class="donut-num" x="{cx}" y="{cy + 4}" text-anchor="middle">{_num(total)}</text>'
               f'<text class="donut-cap" x="{cx}" y="{cy + 24}" text-anchor="middle">{escape(center_label)}</text></svg>')
    out.append('<ul class="legend">')
    for lbl, cls, v in parts_in:
        out.append(f'<li><i class="lg-sw {cls}"></i><span>{escape(lbl)}</span><b>{_num(v)}</b>'
                   f'<small>{100 * v / total:.0f}%</small></li>')
    out.append("</ul></div>")
    return "".join(out)


# --------------------------------------------------------------------------- mapa de calor (magnitud)
def heatmap(matrix: list[list[int]], rows: list[str], unit: str = " cambios") -> str:
    """Matriz día de la semana × hora con un solo tono (más oscuro = más cambios)."""
    vmax = max((v for row in matrix for v in row), default=0)
    if not vmax:
        return _empty()
    cw, ch, gap, left, top = 24, 22, 3, 40, 4
    width = left + 24 * cw + 4
    height = top + len(rows) * ch + 26
    out = [f'<svg class="viz heat" viewBox="0 0 {width} {height}" role="img" '
           f'aria-label="{_attr(_("Cambios por día de la semana y hora"))}" preserveAspectRatio="xMidYMid meet">']
    for r, (name, row) in enumerate(zip(rows, matrix)):
        y = top + r * ch
        out.append(f'<text class="tick" x="{left - 8}" y="{y + ch / 2 + 4:.1f}" text-anchor="end">{escape(name[:3])}</text>')
        for h, v in enumerate(row):
            x = left + h * cw
            tip = f"{name} {h:02d}:00–{(h + 1) % 24:02d}:00 · {_num(v)}{_unit(v, unit)}"
            if v:
                level = 0.18 + 0.82 * (v / vmax) ** 0.7  # escala suave: los valores bajos siguen viéndose
                out.append(f'<rect class="cell" data-tip="{_attr(tip)}" x="{x:.1f}" y="{y:.1f}" width="{cw - gap}" '
                           f'height="{ch - gap}" rx="4" fill-opacity="{level:.2f}"/>')
            else:
                out.append(f'<rect class="cell0" data-tip="{_attr(tip)}" x="{x:.1f}" y="{y:.1f}" width="{cw - gap}" '
                           f'height="{ch - gap}" rx="4"/>')
    for h in range(0, 24, 3):
        out.append(f'<text class="tick" x="{left + h * cw + (cw - gap) / 2:.1f}" y="{height - 6}" '
                   f'text-anchor="middle">{h:02d}h</text>')
    out.append("</svg>")
    scale = "".join(f'<i style="opacity:{o}"></i>' for o in (0.18, 0.4, 0.62, 0.82, 1))
    out.append(f'<div class="heat-legend small muted">{escape(_("menos"))} <span class="steps">{scale}</span> '
               f'{escape(_("más"))} · {escape(_f("máximo {0} en una hora", _num(vmax)))}</div>')
    return "".join(out)


# --------------------------------------------------------------------------- KPI: minigráfica y anillo de estado
def sparkline(values: list[float], width: int = 120, height: int = 34) -> str:
    if len(values) < 2 or not any(values):
        return ""
    vmax = max(values) or 1
    step = width / (len(values) - 1)
    pts = [(i * step, height - 3 - (height - 6) * v / vmax) for i, v in enumerate(values)]
    d = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" aria-hidden="true" preserveAspectRatio="none">'
            f'<path class="area" d="{d} L{width},{height} L0,{height} Z"/><path class="line" d="{d}"/></svg>')


def status_of(pct: float) -> tuple[str, str]:
    """Estado de un porcentaje de cumplimiento: (clase, icono)."""
    if pct >= 90:
        return "ok", "✓"
    if pct >= 70:
        return "warn", "⚠"
    return "crit", "✕"


def ring(pct: float | None, size: int = 64) -> str:
    if pct is None:
        return ""
    cls, _icon = status_of(pct)
    r, sw = size / 2 - 6, 8
    circ = 2 * math.pi * r
    length = circ * max(min(pct, 100), 0) / 100
    c = size / 2
    return (f'<svg class="ring" viewBox="0 0 {size} {size}" aria-hidden="true">'
            f'<circle class="track-ring" cx="{c}" cy="{c}" r="{r:.1f}" stroke-width="{sw}" fill="none"/>'
            f'<circle class="ring-{cls}" cx="{c}" cy="{c}" r="{r:.1f}" stroke-width="{sw}" fill="none" '
            f'stroke-linecap="round" stroke-dasharray="{length:.2f} {circ:.2f}" transform="rotate(-90 {c} {c})"/></svg>')


# --------------------------------------------------------------------------- barras horizontales
def bars(data: list[tuple[str, float]], unit: str = "", vmax: float | None = None, link: list[str] | None = None,
         label_width: int = 175, status: bool = False) -> str:
    """Barras horizontales ordenadas con etiqueta y valor directo. Con status=True (porcentajes de cumplimiento)
    cada barra toma el color de su estado y el valor lleva icono (✓ ⚠ ✕)."""
    if not data:
        return _empty()
    row, gap, w_plot = 24, 8, 300
    height = len(data) * (row + gap) + 2
    vmax = vmax or _nice_max(max(v for _k, v in data))
    width = label_width + w_plot + 110
    parts = [f'<svg class="viz hbars" viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="{_attr(", ".join(f"{lbl}: {_num(v)}{_unit(v, unit)}" for lbl, v in data))}">']
    for i, (label, v) in enumerate(data):
        y = 1 + i * (row + gap)
        w = w_plot * v / vmax if vmax else 0
        lbl = escape(label if len(label) <= 24 else label[:23] + "…")
        tip = f"{label}: {_num(v)}{_unit(v, unit)}"
        cls, icon = status_of(v) if status else ("", "")
        open_a = f'<a href="{escape(link[i])}">' if link else ""
        close_a = "</a>" if link else ""
        parts.append(f'{open_a}<g class="mark" data-tip="{_attr(tip)}"><rect class="hit" x="0" y="{y}" '
                     f'width="{width}" height="{row}"/><text class="label" x="{label_width - 10}" y="{y + 16}" '
                     f'text-anchor="end">{lbl}</text>')
        parts.append(f'<rect class="track" x="{label_width}" y="{y + 4}" width="{w_plot}" height="{row - 8}" rx="8"/>')
        if v > 0:
            parts.append(f'<path class="bar {("st-" + cls) if cls else ""}" '
                         f'd="{_bar_h(label_width, y + 4, max(w, 4), row - 8, r=8)}"/>')
        val = f"{icon} {_num(v)}{_unit(v, unit)}" if icon else f"{_num(v)}{_unit(v, unit)}"
        parts.append(f'<text class="value{(" t-" + cls) if cls else ""}" x="{label_width + w + 8:.1f}" '
                     f'y="{y + 16}">{escape(val)}</text></g>{close_a}')
    parts.append("</svg>")
    return "".join(parts)


def segmented(parts_in: list[tuple[str, str, float]], unit: str = " equipos") -> str:
    """Una barra al 100 % dividida por estado (ciclo de vida) con leyenda; cada estado tiene su icono en la leyenda."""
    total = sum(v for *_x, v in parts_in)
    if not total:
        return _empty()
    out = ['<div class="segbar" role="img" aria-label="'
           + _attr(", ".join(f"{lbl}: {_num(v)}" for lbl, _c, v in parts_in)) + '">']
    for lbl, cls, v in parts_in:
        if v:
            out.append(f'<i class="{cls}" style="flex:{v}" data-tip="{_attr(f"{lbl}: {_num(v)}{_unit(v, unit)}")}"></i>')
    out.append('</div><ul class="legend">')
    for lbl, cls, v in parts_in:
        out.append(f'<li><i class="lg-sw {cls}"></i><span>{escape(lbl)}</span><b>{_num(v)}</b>'
                   f'<small>{100 * v / total:.0f}%</small></li>')
    out.append("</ul>")
    return "".join(out)


# --------------------------------------------------------------------------- línea (rendimiento del servidor)
def line(points: list[tuple[str, float]], unit: str = "%", vmax: float = 100, height: int = 180,
         label_every: int = 48, title: str = "") -> str:
    """Serie temporal como línea (2 px) con área tenue; cada punto tiene una zona de hover con su valor."""
    if len(points) < 2:
        return f'<p class="muted small">{escape(_("Reuniendo datos: la gráfica aparece tras unos minutos de funcionamiento."))}</p>'
    w_plot, left, top, bottom = 760, 36, 10, 28
    h_plot = height - top - bottom
    n = len(points)
    step = w_plot / (n - 1)

    def xy(i, v):
        return left + i * step, top + h_plot - h_plot * min(v, vmax) / vmax
    vals = [v for _k, v in points]
    summary = _f("{0}: ahora {1}, máximo {2}, promedio {3} ({4} a {5})", title, f"{vals[-1]:.0f}{unit}",
                 f"{max(vals):.0f}{unit}", f"{sum(vals) / len(vals):.0f}{unit}", points[0][0], points[-1][0])
    parts = [f'<svg class="viz" viewBox="0 0 {left + w_plot + 4} {height}" role="img" '
             f'aria-label="{_attr(summary)}" preserveAspectRatio="xMidYMid meet">']
    _grid(parts, left, top, w_plot, h_plot, vmax)
    coords = [xy(i, v) for i, (_k, v) in enumerate(points)]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(coords))
    parts.append(f'<path class="area" d="{path} L{coords[-1][0]:.1f},{top + h_plot} L{left},{top + h_plot} Z"/>')
    parts.append(f'<path class="line" d="{path}"/>')
    for i, (label, v) in enumerate(points):
        x, y = coords[i]
        parts.append(f'<g class="mark" data-tip="{_attr(f"{label}: {v:.0f}{unit}")}"><rect class="hit" '
                     f'x="{x - step / 2:.1f}" y="{top}" width="{max(step, 2):.1f}" height="{h_plot}"/>'
                     f'<circle class="dot" cx="{x:.1f}" cy="{y:.1f}" r="4"/></g>')
        if i in (0, n - 1) or (i % label_every == 0 and n - 1 - i > label_every // 3):
            anchor = "start" if i == 0 else "end" if i == n - 1 else "middle"  # que no se corten en los bordes
            parts.append(f'<text class="tick" x="{x:.1f}" y="{height - 8}" text-anchor="{anchor}">{escape(label)}</text>')
    lx, ly = coords[-1]  # valor actual como etiqueta directa
    parts.append(f'<text class="value" x="{min(lx, left + w_plot - 2):.1f}" y="{max(ly - 8, 12):.1f}" '
                 f'text-anchor="end">{points[-1][1]:.0f}{unit}</text>')
    parts.append("</svg>")
    return "".join(parts)


def meter(used: float, total: float, label: str, detail: str, warn_at: float = 90) -> str:
    """Barra de uso (usado / total) con texto; el aviso por encima del umbral lleva icono y texto, no solo color."""
    pct = 100 * used / total if total else 0
    cls = "warn" if pct >= warn_at else ""
    note = f' <span class="badge b-warning">⚠ {escape(_("casi lleno"))}</span>' if cls else ""
    return (f'<div class="meter-row"><div class="meter-head"><b>{escape(label)}</b>{note}'
            f'<span class="value">{pct:.0f}%</span></div>'
            f'<div class="meter {cls}" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{pct:.0f}" '
            f'aria-label="{escape(_f("{0}: {1}% usado", label, f"{pct:.0f}"))}"><i style="width:{min(pct, 100):.1f}%"></i></div>'
            f'<div class="small muted">{escape(detail)}</div></div>')
