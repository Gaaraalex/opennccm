"""Documento de la red (Informes › Documento de la red): un PDF o un Word (.docx) por sitio, grupo o toda la red, con lo
que OpenNCCM ya sabe: inventario, mapa (LLDP/CDP), VLAN y direcciones IP, enlaces, cumplimiento y vulnerabilidades y el
último respaldo de cada equipo.

* El PDF se arma con reportlab (mapa en vectores). El Word es un .docx real (Office Open XML escrito aquí) con el mapa
  como imagen PNG (Pillow, que viene con reportlab).
* Nunca salen contraseñas, comunidades SNMP ni claves: el documento no lleva configuraciones, solo datos de inventario,
  nombres de interfaces, VLAN y direcciones. Las IP públicas se pueden ocultar también.
* Los generados quedan en DATA_DIR/netdocs con lo que tenía la red entonces, para avisar si cambió desde entonces.
"""
from __future__ import annotations

import html
import io
import ipaddress
import json
import re
import secrets
import zipfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select

from . import impact, ipam
from .auth import device_scope
from .config import DATA_DIR
from .crypto import try_decrypt
from .db import ComplianceResult, Device, DeviceGroup, SessionLocal, Vulnerability, now
from .drivers import get_profile
from .i18n import N_, _, _f

DIR = DATA_DIR / "netdocs"
MAP_NOTE = N_("Con los vecinos LLDP/CDP del último respaldo de cada equipo. En azul, los equipos de OpenNCCM; en blanco, vecinos que no están en OpenNCCM.")  # noqa: E501
SECTIONS = [("inventory", N_("Inventario: modelo, serie y firmware")), ("map", N_("Mapa de la red")),
            ("vlans", N_("VLAN y direcciones IP")), ("links", N_("Enlaces entre equipos")),
            ("compliance", N_("Cumplimiento y vulnerabilidades")), ("backups", N_("Último respaldo de cada equipo"))]
MAX_LOGO = 2 * 1024 * 1024


class DocError(RuntimeError):
    pass


# --------------------------------------------------------------------------- opciones y datos
def options_from(form) -> dict:
    scope = form.get("scope") if form.get("scope") in ("site", "group", "all") else "all"
    return {"scope": scope, "site": (form.get("site") or "").strip(), "group": int(form.get("group") or 0),
            "fmt": "docx" if form.get("fmt") == "docx" else "pdf",
            "sections": [k for k, _l in SECTIONS if form.get(f"sec_{k}") == "on"] or [k for k, _l in SECTIONS],
            "cover": form.get("cover") == "on", "client": (form.get("client") or "").strip()[:120],
            "hide_public": form.get("hide_public") == "on"}


def _mask(ip: str, hide: bool) -> str:
    if not hide or not ip:
        return ip
    try:
        return _("IP pública oculta") if ipaddress.ip_address(ip.split("/")[0]).is_global else ip
    except ValueError:
        return ip


def collect(user, opts: dict) -> dict:
    from . import services
    scope = device_scope(user)
    hide = opts.get("hide_public")
    with SessionLocal() as db:
        devs = [d for d in db.scalars(select(Device).order_by(Device.name)) if scope is None or d.id in scope]
        groups = {g.id: g.name for g in db.scalars(select(DeviceGroup))}
        if opts["scope"] == "site":
            devs = [d for d in devs if (d.location or "") == opts["site"]]
            label = opts["site"] or _("Sin ubicación")
        elif opts["scope"] == "group":
            devs = [d for d in devs if d.group_id == opts["group"]]
            label = groups.get(opts["group"], _("Grupo"))
        else:
            label = _("Toda la red")
        if not devs:
            raise DocError(_("No hay equipos en ese alcance."))
        inv, vlans, ips, backups, comp = [], {}, [], [], []
        for d in devs:
            prof = get_profile(d.device_type)
            inv.append({"name": d.name, "ip": _mask(d.host, hide), "vendor": prof.vendor, "platform": prof.label,
                        "model": d.model or "", "serial": d.serial or "", "version": d.os_version or "",
                        "group": groups.get(d.group_id, ""), "site": d.location or ""})
            backups.append({"name": d.name, "at": d.last_backup_at.strftime("%d-%m-%Y %H:%M") if d.last_backup_at else "—",
                            "status": {"ok": _("Correcto"), "error": _("Con error"), "pending": _("Pendiente")}.get(d.last_status, d.last_status)})
            v = services.latest_version(db, d.id)
            text = (try_decrypt(v.content_enc) or "") if v else ""
            if text:
                for vl in ipam.vlans(text, d.device_type):
                    e = vlans.setdefault(str(vl["id"]), {"id": vl["id"], "names": set(), "devs": set()})
                    if vl["name"]:
                        e["names"].add(vl["name"])
                    e["devs"].add(d.name)
                for x in ipam.ips(text, d.device_type):
                    ips.append({"dev": d.name, "iface": x["interface"], "ip": _mask(f"{x['ip']}/{x['prefix']}", hide)})
            ok = db.scalar(select(func.count()).select_from(ComplianceResult).where(ComplianceResult.device_id == d.id,
                                                                                    ComplianceResult.passed.is_(True))) or 0
            tot = db.scalar(select(func.count()).select_from(ComplianceResult).where(ComplianceResult.device_id == d.id)) or 0
            sev = dict(db.execute(select(Vulnerability.severity, func.count()).where(Vulnerability.device_id == d.id)
                                  .group_by(Vulnerability.severity)).all())
            comp.append({"name": d.name, "pct": round(100 * ok / tot) if tot else None, "fails": tot - ok,
                         "crit": sev.get("CRITICAL", 0), "high": sev.get("HIGH", 0),
                         "other": sum(v for k, v in sev.items() if k not in ("CRITICAL", "HIGH"))})
        g = impact.Graph(devs)
    ids = {f"d{d.id}" for d in devs}
    links = []
    for e in g.edges:
        if e["a"] in ids or e["b"] in ids:
            links.append({"a": g.nodes[e["a"]]["name"], "pa": e["pa"], "b": g.nodes[e["b"]]["name"], "pb": e["pb"]})
    vl = [{"id": v["id"], "name": " / ".join(sorted(v["names"])), "devs": len(v["devs"])} for v in vlans.values()]
    vl.sort(key=lambda x: int(x["id"]) if str(x["id"]).isdigit() else 0)
    snap = {"devs": sorted(d.name for d in devs), "vlans": sorted(str(v["id"]) for v in vl),
            "fw": {d.name: d.os_version or "" for d in devs}}
    return {"label": label, "inv": inv, "vlans": vl, "ips": ips, "links": links, "backups": backups, "comp": comp,
            "graph": g, "ids": ids, "snap": snap, "nodata": [g.nodes[n]["name"] for n in g.nodata if n in ids]}


def layout(g: impact.Graph, ids: set[str], w: float = 500, row: float = 36, box: float = 76) -> tuple[dict, list, float]:
    """Posiciones del mapa del documento: (posiciones, enlaces, alto). Cada grupo de equipos enlazados se arma por niveles
    (arriba el núcleo: el más conectado y los vecinos casi tan conectados como él; debajo lo que cuelga de cada nivel, en
    filas que caben en el ancho). Los grupos se acomodan uno al lado del otro, como en estantes, y los equipos sin enlaces
    conocidos van al final en una grilla. El alto sale de las filas que hagan falta: nada queda encimado."""
    keep = set(ids) | {n for i in ids for n in g.adj.get(i, ()) if g.nodes[n]["dev"] is None}
    if not keep:
        return {}, [], 0
    adj = {n: g.adj.get(n, set()) & keep for n in keep}
    name = lambda n: g.nodes[n]["name"].lower()  # noqa: E731
    per_row = max(1, int(w // box))
    blocks, left = [], set(keep)
    while left:  # cada grupo conexo, del más grande al más chico
        root = max(left, key=lambda n: (len(adj[n]), name(n)))
        comp, todo = {root}, [root]
        while todo:
            x = todo.pop()
            for y in adj[x] - comp:
                comp.add(y)
                todo.append(y)
        left -= comp
        top = {root} | {n for n in adj[root] if len(adj[n]) >= max(2, len(adj[root]) * 0.6)}
        level = {n: 0 for n in top}
        todo = sorted(top, key=name)
        while todo:
            x = todo.pop(0)
            for y in sorted(adj[x], key=name):
                if y not in level:
                    level[y] = level[x] + 1
                    todo.append(y)
        rows = []
        for lv in range(max(level.values()) + 1):
            ns = sorted((n for n in comp if level[n] == lv), key=name)
            rows += [ns[k:k + per_row] for k in range(0, len(ns), per_row)]
        blocks.append([r for r in rows if r])
    linked = sorted((b for b in blocks if len(b) > 1 or len(b[0]) > 1), key=lambda b: -sum(len(r) for r in b))
    alone = sorted((b[0][0] for b in blocks if len(b) == 1 and len(b[0]) == 1), key=name)
    blocks = linked + [[alone[k:k + per_row]] for k in range(0, len(alone), per_row)]
    pos: dict[str, tuple[float, float]] = {}
    x0 = shelf_y = shelf_h = 0.0
    for b in blocks:  # estantes: se pone a la derecha del anterior si cabe; si no, en el estante de abajo
        bw = max(len(r) for r in b) * box
        if x0 and x0 + bw > w + 1:
            shelf_y, x0, shelf_h = shelf_y + shelf_h + row * 0.4, 0.0, 0.0
        for r, ns in enumerate(b):
            for k, n in enumerate(ns):
                pos[n] = (x0 + (k + 0.5) * bw / len(ns), shelf_y + (r + 0.5) * row)
        x0 += bw + box * 0.3
        shelf_h = max(shelf_h, len(b) * row)
    height = shelf_y + shelf_h
    if x0 and len(blocks) == 1:  # un solo grupo: centrado
        dx = (w - (x0 - box * 0.3)) / 2
        pos = {n: (x + dx, y) for n, (x, y) in pos.items()}
    edges = [(e["a"], e["b"]) for e in g.edges if e["a"] in pos and e["b"] in pos]
    return pos, edges, height


def mini_svg(data: dict, w: int = 172, h: int = 120) -> str:
    """El mapa chico de la vista previa (página 2)."""
    pos, edges, hh = layout(data["graph"], data["ids"], w, 22, 24)
    h = max(h, int(hh) + 2)
    if not pos:
        return '<div style="font-size:8.5px;color:#55607a">' + html.escape(_("Sin vecinos LLDP/CDP: no hay mapa.")) + "</div>"
    out = [f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true"><g stroke="#9aa6bf" stroke-width="1.2">']
    out += [f'<line x1="{pos[a][0]:.0f}" y1="{pos[a][1]:.0f}" x2="{pos[b][0]:.0f}" y2="{pos[b][1]:.0f}"/>' for a, b in edges]
    out.append("</g>")
    out += [f'<rect x="{x - 10:.0f}" y="{y - 6:.0f}" width="20" height="12" rx="3" fill="{"#2347e8" if data["graph"].nodes[k]["dev"] else "#ffffff"}" '
            f'stroke="#2347e8"/>' for k, (x, y) in pos.items()]
    out.append("</svg>")
    return "".join(out)


# --------------------------------------------------------------------------- logo
def logo_path() -> Path | None:
    for ext in ("png", "jpg"):
        p = DIR / f"logo.{ext}"
        if p.exists():
            return p
    return None


def save_logo(raw: bytes) -> str:
    """Guarda el logo del cliente (PNG o JPEG, hasta 2 MB) para la portada. '' si está bien, o el error."""
    if len(raw) > MAX_LOGO:
        return _("El logo pesa más de 2 MB.")
    ext = "png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else "jpg" if raw[:3] == b"\xff\xd8\xff" else ""
    if not ext:
        return _("El logo tiene que ser PNG o JPEG.")
    DIR.mkdir(parents=True, exist_ok=True)
    for old in DIR.glob("logo.*"):
        old.unlink()
    (DIR / f"logo.{ext}").write_bytes(raw)
    return ""


def remove_logo() -> None:
    for old in DIR.glob("logo.*") if DIR.exists() else []:
        old.unlink()


def inv_table(data: dict) -> tuple[list, list, tuple]:
    """Inventario. La columna «Sitio» solo si hay más de uno (en un documento de un sitio repetiría lo mismo)."""
    many = len({r["site"] for r in data["inv"]}) > 1
    head = [_("Equipo"), _("IP"), _("Plataforma"), _("Modelo"), _("Serie"), _("Firmware")] + ([_("Sitio")] if many else [])
    rows = [[r["name"], r["ip"], r["platform"], r["model"], r["serial"], r["version"]] + ([r["site"]] if many else [])
            for r in data["inv"]]
    return head, rows, ((25, 19, 24, 25, 25, 26, 30) if many else (32, 22, 32, 30, 30, 28))


def comp_table(data: dict) -> tuple[list, list, tuple, str]:
    """Cumplimiento y CVE. Si ningún equipo se auditó, sin las columnas de reglas (con una nota); si algunos sí, «sin
    auditar» en los que no."""
    audited = any(c["pct"] is not None for c in data["comp"])
    cves = [(c["crit"], c["high"], c["other"]) for c in data["comp"]]
    if not audited:
        return ([_("Equipo"), _("CVE críticos"), _("CVE altos"), _("Otros CVE")],
                [[c["name"], *v] for c, v in zip(data["comp"], cves)], (64, 36, 37, 37),
                _("Todavía no se auditó el cumplimiento de estos equipos (Cumplimiento › Auditar ahora)."))
    return ([_("Equipo"), _("Reglas cumplidas"), _("Infracciones"), _("CVE críticos"), _("CVE altos"), _("Otros CVE")],
            [[c["name"], _("sin auditar") if c["pct"] is None else f"{c['pct']} %", "—" if c["pct"] is None else c["fails"], *v]
             for c, v in zip(data["comp"], cves)], (44, 30, 25, 25, 25, 25), "")


# --------------------------------------------------------------------------- PDF
def _pdf(data: dict, opts: dict) -> tuple[bytes, int]:
    from reportlab.graphics.shapes import Drawing, Line, Rect, String
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontName="Helvetica-Bold", fontSize=16, spaceAfter=8, keepWithNext=1,
                        textColor=colors.HexColor("#1b2333"))
    body = ParagraphStyle("b", parent=ss["BodyText"], fontName="Helvetica", fontSize=9, leading=12,
                          textColor=colors.HexColor("#33405a"))
    cell = ParagraphStyle("c", parent=body, fontSize=8, leading=10)
    blue = colors.HexColor("#2347e8")
    e = lambda s: html.escape(str(s if s is not None else ""))  # noqa: E731
    story, n = [], 0
    if opts.get("cover"):
        lp = logo_path()
        story.append(Spacer(1, 40 * mm))
        if lp:
            story.append(Image(str(lp), width=40 * mm, height=40 * mm, kind="proportional", hAlign="LEFT"))
            story.append(Spacer(1, 10 * mm))
        story.append(Paragraph(e(_("Documento de la red")), ParagraphStyle("t", parent=h1, fontSize=28, leading=34)))
        story.append(Paragraph(e(" · ".join(x for x in (opts.get("client"), data["label"]) if x)),
                               ParagraphStyle("s", parent=body, fontSize=13, leading=18)))
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph(e(_f("Generado por OpenNCCM el {0} · {1} equipos", now().strftime("%d-%m-%Y %H:%M"),
                                    len(data["inv"]))), body))
        story.append(PageBreak())

    def table(head, rows, widths):
        t = Table([[Paragraph("<b>%s</b>" % e(h), cell) for h in head]] + [[Paragraph(e(c), cell) for c in r] for r in rows],
                  colWidths=[w * mm for w in widths], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9edf6")),
                               ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#d5dbe8")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3),
                               ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        return t

    secs = opts["sections"]
    if "inventory" in secs:
        n += 1
        story += [Paragraph(e(f"{n} · " + _("Inventario")), h1), table(*inv_table(data)), Spacer(1, 6 * mm)]
    if "map" in secs:
        n += 1
        title = Paragraph(e(f"{n} · " + _("Mapa de la red")), h1)
        pos, edges, hh = layout(data["graph"], data["ids"], 480, 36, 74)
        if pos:
            if hh > 640:  # más alto que la página: se achica para que entre
                k = 640 / hh
                pos, hh = {n: (x, y * k) for n, (x, y) in pos.items()}, 640
            H = hh + 10
            dr = Drawing(480, H)
            for a, b in edges:
                dr.add(Line(pos[a][0], H - 5 - pos[a][1], pos[b][0], H - 5 - pos[b][1], strokeColor=colors.HexColor("#9aa6bf"), strokeWidth=0.8))
            for k, (x, y) in pos.items():
                nd = data["graph"].nodes[k]
                mine = nd["dev"] is not None
                dr.add(Rect(x - 34, H - 5 - y - 9, 68, 18, rx=4, ry=4, fillColor=blue if mine else colors.white,
                            strokeColor=blue, strokeWidth=0.8))
                name = nd["name"] if len(nd["name"]) <= 14 else nd["name"][:13] + "…"
                dr.add(String(x, H - 5 - y - 3, name, fontName="Helvetica-Bold", fontSize=6.5, textAnchor="middle",
                              fillColor=colors.white if mine else blue))
            story.append(KeepTogether([title, dr, Paragraph(e(_(MAP_NOTE) + (" " + _f("Sin vecinos conocidos: {0}.", ", ".join(
                data["nodata"][:12])) if data["nodata"] else "")), body)]))
        else:
            story += [title, Paragraph(e(_("Ningún equipo de este alcance informa vecinos LLDP/CDP: no hay mapa.")), body)]
        story.append(Spacer(1, 6 * mm))
    if "vlans" in secs:
        n += 1
        story += [Paragraph(e(f"{n} · " + _("VLAN y direcciones IP")), h1)]
        story.append(table([_("VLAN"), _("Nombre"), _("Equipos")], [(v["id"], v["name"], v["devs"]) for v in data["vlans"]] or
                           [("—", _("No se encontraron VLAN en las configuraciones"), "")], (24, 120, 30)))
        story.append(Spacer(1, 4 * mm))
        story.append(table([_("Equipo"), _("Interfaz"), _("Dirección")], [(x["dev"], x["iface"], x["ip"]) for x in data["ips"]] or
                           [("—", "", "")], (50, 64, 60)))
        story.append(Spacer(1, 6 * mm))
    if "links" in secs:
        n += 1
        story += [Paragraph(e(f"{n} · " + _("Enlaces entre equipos")), h1),
                  table([_("Equipo"), _("Puerto"), _("Equipo"), _("Puerto")],
                        [(x["a"], x["pa"], x["b"], x["pb"]) for x in data["links"]] or [("—", "", "", "")], (44, 43, 44, 43)),
                  Spacer(1, 6 * mm)]
    if "compliance" in secs:
        n += 1
        head, rows, widths, note = comp_table(data)
        story += [Paragraph(e(f"{n} · " + _("Cumplimiento y vulnerabilidades")), h1)] + \
            ([Paragraph(e(note), body), Spacer(1, 3 * mm)] if note else []) + [table(head, rows, widths), Spacer(1, 6 * mm)]
    if "backups" in secs:
        n += 1
        story += [Paragraph(e(f"{n} · " + _("Último respaldo de cada equipo")), h1),
                  table([_("Equipo"), _("Último respaldo"), _("Estado")], [(b["name"], b["at"], b["status"]) for b in data["backups"]],
                        (84, 50, 40))]
    pages = [0]

    def foot(c, doc):
        pages[0] = doc.page
        c.saveState()
        c.setFont("Helvetica", 7)
        c.setFillColor(colors.HexColor("#8890a2"))
        c.drawString(18 * mm, 10 * mm, _f("OpenNCCM · Documento de la red · {0}", data["label"]))
        c.drawRightString(A4[0] - 18 * mm, 10 * mm, _f("Página {0}", doc.page))
        c.restoreState()

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                      title=_("Documento de la red") + " · " + data["label"], author="OpenNCCM").build(
        story, onFirstPage=foot, onLaterPages=foot)
    return buf.getvalue(), pages[0]


# --------------------------------------------------------------------------- Word (.docx)
def _png_map(data: dict, w: int = 1000, h: int = 680) -> bytes | None:
    from PIL import Image, ImageDraw, ImageFont
    pos, edges, hh = layout(data["graph"], data["ids"], w, 70, 152)
    if not pos:
        return None
    h = int(hh) + 20
    im = Image.new("RGB", (w, h), "white")
    dr = ImageDraw.Draw(im)
    try:
        font = ImageFont.load_default(size=15)
    except TypeError:  # Pillow viejo
        font = ImageFont.load_default()
    for a, b in edges:
        dr.line([pos[a][0], pos[a][1] + 10, pos[b][0], pos[b][1] + 10], fill="#9aa6bf", width=1)
    for k, (x, y) in pos.items():
        nd = data["graph"].nodes[k]
        mine = nd["dev"] is not None
        y += 10
        dr.rounded_rectangle([x - 70, y - 17, x + 70, y + 17], radius=8, fill="#2347e8" if mine else "white", outline="#2347e8", width=2)
        name = nd["name"] if len(nd["name"]) <= 16 else nd["name"][:15] + "…"
        dr.text((x, y), name, fill="white" if mine else "#2347e8", font=font, anchor="mm")
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def _x(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=False)


def _docx(data: dict, opts: dict) -> bytes:
    body: list[str] = []
    rels = ['<Relationship Id="rStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>']
    media: dict[str, bytes] = {}

    def p(text, style=None, bold=False, size=None, color=None):
        rpr = ("<w:b/>" if bold else "") + (f'<w:sz w:val="{size}"/>' if size else "") + (f'<w:color w:val="{color}"/>' if color else "")
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        body.append(f'<w:p>{ppr}<w:r>{"<w:rPr>" + rpr + "</w:rPr>" if rpr else ""}<w:t xml:space="preserve">{_x(text)}</w:t></w:r></w:p>')

    def img(rid, name, raw, cx_mm, cy_mm):
        media[name] = raw
        rels.append(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/{name}"/>')
        cx, cy = int(cx_mm * 36000), int(cy_mm * 36000)
        body.append(
            '<w:p><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
            f'<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{len(media)}" name="{name}"/>'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr><pic:cNvPr id="{len(media)}" name="{name}"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
            '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>')

    def table(head, rows):
        shd = '<w:shd w:val="clear" w:color="auto" w:fill="E9EDF6"/>'

        def tc(t, b=False):
            return (f'<w:tc><w:tcPr>{shd if b else ""}</w:tcPr><w:p><w:r><w:rPr>{"<w:b/>" if b else ""}<w:sz w:val="16"/></w:rPr>'
                    f'<w:t xml:space="preserve">{_x(t)}</w:t></w:r></w:p></w:tc>')
        out = ['<w:tbl><w:tblPr><w:tblStyle w:val="Tabla"/><w:tblW w:w="5000" w:type="pct"/></w:tblPr>',
               '<w:tr><w:trPr><w:tblHeader/></w:trPr>' + "".join(tc(h, True) for h in head) + "</w:tr>"]
        out += ["<w:tr>" + "".join(tc(c) for c in r) + "</w:tr>" for r in rows]
        out.append("</w:tbl>")
        body.append("".join(out))
        p("")

    def brk():
        body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

    if opts.get("cover"):
        lp = logo_path()
        for _i in range(4):
            p("")
        if lp:
            from PIL import Image as PImage
            raw = lp.read_bytes()
            with PImage.open(io.BytesIO(raw)) as im:
                ratio = im.height / max(1, im.width)
            img("rLogo", "logo." + lp.suffix[1:], raw, 40, 40 * ratio)
        p(_("Documento de la red"), "Title")
        p(" · ".join(x for x in (opts.get("client"), data["label"]) if x), size=28)
        p(_f("Generado por OpenNCCM el {0} · {1} equipos", now().strftime("%d-%m-%Y %H:%M"), len(data["inv"])), color="55607A")
        brk()
    secs, n = opts["sections"], 0
    if "inventory" in secs:
        n += 1
        p(f"{n} · " + _("Inventario"), "Heading1")
        head, rows, _w = inv_table(data)
        table(head, rows)
    if "map" in secs:
        n += 1
        p(f"{n} · " + _("Mapa de la red"), "Heading1")
        png = _png_map(data)
        if png:
            from PIL import Image as PImage
            with PImage.open(io.BytesIO(png)) as im:
                ratio = im.height / max(1, im.width)
            img("rMap", "mapa.png", png, 165, min(230, 165 * ratio))
            p(_(MAP_NOTE), color="55607A")
        else:
            p(_("Ningún equipo de este alcance informa vecinos LLDP/CDP: no hay mapa."))
    if "vlans" in secs:
        n += 1
        p(f"{n} · " + _("VLAN y direcciones IP"), "Heading1")
        table([_("VLAN"), _("Nombre"), _("Equipos")], [(v["id"], v["name"], v["devs"]) for v in data["vlans"]] or
              [("—", _("No se encontraron VLAN en las configuraciones"), "")])
        table([_("Equipo"), _("Interfaz"), _("Dirección")], [(x["dev"], x["iface"], x["ip"]) for x in data["ips"]] or [("—", "", "")])
    if "links" in secs:
        n += 1
        p(f"{n} · " + _("Enlaces entre equipos"), "Heading1")
        table([_("Equipo"), _("Puerto"), _("Equipo"), _("Puerto")], [(x["a"], x["pa"], x["b"], x["pb"]) for x in data["links"]] or [("—", "", "", "")])
    if "compliance" in secs:
        n += 1
        p(f"{n} · " + _("Cumplimiento y vulnerabilidades"), "Heading1")
        head, rows, _w, note = comp_table(data)
        if note:
            p(note, color="55607A")
        table(head, rows)
    if "backups" in secs:
        n += 1
        p(f"{n} · " + _("Último respaldo de cada equipo"), "Heading1")
        table([_("Equipo"), _("Último respaldo"), _("Estado")], [(b["name"], b["at"], b["status"]) for b in data["backups"]])
    W = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
         'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
         'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"')
    footer = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr {W}><w:p><w:pPr><w:jc w:val="right"/></w:pPr>'
              f'<w:r><w:rPr><w:sz w:val="14"/><w:color w:val="8890A2"/></w:rPr><w:t xml:space="preserve">{_x(_f("OpenNCCM · Documento de la red · {0}", data["label"]))} · </w:t></w:r>'
              '<w:r><w:rPr><w:sz w:val="14"/><w:color w:val="8890A2"/></w:rPr><w:fldChar w:fldCharType="begin"/></w:r>'
              '<w:r><w:rPr><w:sz w:val="14"/></w:rPr><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
              '<w:r><w:rPr><w:sz w:val="14"/></w:rPr><w:fldChar w:fldCharType="end"/></w:r></w:p></w:ftr>')
    rels.append('<Relationship Id="rFoot" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>')
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W}><w:body>' + "".join(body) +
           '<w:sectPr><w:footerReference w:type="default" r:id="rFoot"/><w:pgSz w:w="11906" w:h="16838"/>'
           '<w:pgMar w:top="1020" w:right="1020" w:bottom="1020" w:left="1020" w:header="567" w:footer="567" w:gutter="0"/></w:sectPr>'
           '</w:body></w:document>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/><w:sz w:val="20"/><w:lang w:val="es-ES"/></w:rPr></w:rPrDefault>'
              '<w:pPrDefault><w:pPr><w:spacing w:after="80"/></w:pPr></w:pPrDefault></w:docDefaults>'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="56"/><w:color w:val="1B2333"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
              '<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/><w:color w:val="1B2333"/></w:rPr></w:style>'
              '<w:style w:type="table" w:styleId="Tabla"><w:name w:val="Tabla"/><w:tblPr><w:tblBorders>'
              '<w:insideH w:val="single" w:sz="4" w:color="D5DBE8"/><w:bottom w:val="single" w:sz="4" w:color="D5DBE8"/></w:tblBorders>'
              '<w:tblCellMar><w:top w:w="40" w:type="dxa"/><w:left w:w="80" w:type="dxa"/><w:bottom w:w="40" w:type="dxa"/><w:right w:w="80" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>'
              '</w:styles>')
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>'
          '<Default Extension="jpg" ContentType="image/jpeg"/>'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
          '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
          '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                 '<Relationship Id="r2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>')
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{_x(_("Documento de la red") + " · " + data["label"])}</dc:title><dc:creator>OpenNCCM</dc:creator>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now().strftime("%Y-%m-%dT%H:%M:%SZ")}</dcterms:created></cp:coreProperties>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                + "".join(rels) + "</Relationships>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("docProps/core.xml", core)
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", styles)
        z.writestr("word/footer1.xml", footer)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        for name, raw in media.items():
            z.writestr(f"word/media/{name}", raw)
    return buf.getvalue()


# --------------------------------------------------------------------------- generados
def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s.encode("ascii", "ignore").decode() or "red").strip("-")[:40] or "red"


def generate(user, opts: dict) -> dict:
    data = collect(user, opts)
    if opts["fmt"] == "pdf":
        raw, pages = _pdf(data, opts)
    else:
        raw, pages = _docx(data, opts), None
    DIR.mkdir(parents=True, exist_ok=True)
    did = now().strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(3)
    fname = f"Documento-red_{_slug(data['label'])}_{now().strftime('%Y-%m-%d')}.{opts['fmt']}"
    (DIR / f"{did}.{opts['fmt']}").write_bytes(raw)
    meta = {"id": did, "file": fname, "fmt": opts["fmt"], "label": data["label"], "user": user.username,
            "created": now().isoformat(timespec="minutes"), "pages": pages, "snap": data["snap"], "opts": opts,
            "size": len(raw)}
    (DIR / f"{did}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def _metas() -> list[dict]:
    if not DIR.exists():
        return []
    out = []
    for p in DIR.glob("*.json"):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda m: m.get("created", ""), reverse=True)


def _visible(user, m: dict) -> bool:
    return user.role == "admin" or m.get("user") == user.username


def changes_since(user, m: dict) -> str:
    """«La red cambió: 3 equipos nuevos, 1 VLAN y 2 firmwares» o '' si sigue igual."""
    try:
        cur = collect(user, m["opts"])["snap"]
    except DocError:
        return _("Ya no hay equipos en ese alcance")
    old = m.get("snap") or {}
    new_devs = len(set(cur["devs"]) - set(old.get("devs", [])))
    gone = len(set(old.get("devs", [])) - set(cur["devs"]))
    vl = len(set(cur["vlans"]) ^ set(old.get("vlans", [])))
    fw = sum(1 for k, v in cur["fw"].items() if k in old.get("fw", {}) and old["fw"][k] != v)
    parts = []
    if new_devs:
        parts.append(_f("{0} equipos nuevos", new_devs) if new_devs != 1 else _("1 equipo nuevo"))
    if gone:
        parts.append(_f("{0} equipos menos", gone) if gone != 1 else _("1 equipo menos"))
    if vl:
        parts.append(_f("{0} VLAN", vl))
    if fw:
        parts.append(_f("{0} firmwares", fw) if fw != 1 else _("1 firmware"))
    if not parts:
        return ""
    return _("La red cambió:") + " " + (", ".join(parts[:-1]) + " " + _("y") + " " + parts[-1] if len(parts) > 1 else parts[0])


def listing(user, limit: int = 12) -> list[dict]:
    out = []
    for m in [m for m in _metas() if _visible(user, m)][:limit]:
        out.append({**m, "changed": changes_since(user, m), "when": datetime.fromisoformat(m["created"])})
    return out


def file_for(user, doc_id: str) -> tuple[Path, dict]:
    from .auth import Forbidden
    if not re.fullmatch(r"[0-9]{14}-[0-9a-f]{6}", doc_id or ""):
        raise Forbidden()
    mp = DIR / f"{doc_id}.json"
    if not mp.exists():
        raise Forbidden()
    m = json.loads(mp.read_text(encoding="utf-8"))
    if not _visible(user, m):
        raise Forbidden()
    return DIR / f"{doc_id}.{m['fmt']}", m


def regenerate(user, doc_id: str) -> dict:
    _p, m = file_for(user, doc_id)
    return generate(user, m["opts"])


__all__ = ["generate", "listing", "file_for", "regenerate", "options_from", "collect", "SECTIONS", "DocError"]
