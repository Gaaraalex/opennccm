"""Informes › Equipos del mapa: los que se ven en el mapa de la red (vecinos LLDP/CDP/MNDP y RoMON) cruzados con
Dispositivos. Cada uno dice si ya está agregado a OpenNCCM y respaldado, agregado pero sin respaldo (o con el último
fallido) o todavía sin agregar. No muestra IP ni MAC (pedido del usuario): para los que faltan deja dónde escribir la IP,
en la página (con «Agregar») o en el PDF, que trae un campo para escribirla desde la PC."""
from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import select

from . import topology
from .auth import device_scope, require
from .db import Device, SessionLocal, get_setting, now
from .i18n import _, _f
from .web import render

router = APIRouter()
ST_OK, ST_FAIL, ST_NEVER, ST_NEW = "ok", "fail", "never", "new"


def collect(user) -> dict:
    """{"rows": [{"name", "platform", "seen", "state", "id"}], "n": {estado: cantidad}, "at"}. Los que faltan primero."""
    scope = device_scope(user)
    types = get_setting("topology_types", "")
    allowed = {f for f, _l in topology.FAMILIES} if not types else set(types.split(","))
    with SessionLocal() as db:
        devs = [d for d in db.scalars(select(Device).where(Device.retired_at.is_(None)).order_by(Device.name)) if scope is None or d.id in scope]
        hosts = [d.host.lower() for d in devs]
        by_ip = {d.host.lower(): d for d in devs if hosts.count(d.host.lower()) == 1}
        by_name = {topology.norm_name(d.name): d for d in devs}
        rows, found = [], {}
        for d in devs:
            nb = json.loads(d.neighbors) if d.neighbors else []
            kept, _c = topology.network_view(topology.lldp_only(nb))
            for n in kept + [x for x in nb if x.get("romon")]:
                t = by_name.get(topology.norm_name(n.get("remote_name"))) or by_ip.get((n.get("remote_ip") or "").lower())
                if t is not None:
                    continue  # ya está en Dispositivos (sale abajo, con su estado de respaldo)
                key = topology.norm_name(n.get("remote_name")) or n.get("remote_ip") or n.get("mac")
                if not key or (not n.get("romon") and topology.family(n) not in allowed):
                    continue
                port = n.get("local_port") or ""
                via = _f("{0} (RoMON)", d.name) if n.get("romon") else (f"{d.name} · {port}" if port else d.name)
                r = found.get(key)
                if r is None:
                    r = found[key] = {"name": (n.get("remote_name") or "").split(".")[0], "platform": (n.get("platform") or "").strip()[:80],
                                      "seen": [], "state": ST_NEW, "id": None}
                    rows.append(r)
                if via not in r["seen"]:
                    r["seen"].append(via)
                if not r["platform"] and n.get("platform"):
                    r["platform"] = n["platform"].strip()[:80]
        for r in rows:  # sin nombre anunciado: se nombra por lo que es, nunca por su IP o MAC
            if not r["name"]:
                r["name"] = _("Sin nombre") + (f" ({r['platform']})" if r["platform"] else "")
        for d in devs:
            state = ST_OK if d.last_status == "ok" and d.last_backup_at else ST_FAIL if d.last_status == "error" else ST_NEVER
            rows.append({"name": d.name, "platform": d.model or "", "seen": [], "state": state, "id": d.id,
                         "at": d.last_backup_at})
    order = {ST_NEW: 0, ST_FAIL: 1, ST_NEVER: 2, ST_OK: 3}
    rows.sort(key=lambda r: (order[r["state"]], r["name"].lower()))
    n = {k: sum(1 for r in rows if r["state"] == k) for k in order}
    return {"rows": rows, "n": n, "at": now()}


def label(state: str) -> str:
    return {ST_NEW: _("No está agregado"), ST_NEVER: _("Agregado, sin respaldo todavía"),
            ST_FAIL: _("Agregado, el último respaldo falló"), ST_OK: _("Agregado y respaldado")}[state]


@router.get("/reports/mapinv")
def mapinv_page(request: Request):
    user = require(request)
    return render(request, "mapinv.html", user, m=collect(user), label=label)


@router.get("/reports/mapinv/csv")
def mapinv_csv(request: Request):
    user = require(request)
    m = collect(user)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([_("Equipo"), _("Modelo o plataforma"), _("Estado"), _("Visto desde"), _("IP para agregarlo")])
    for r in m["rows"]:
        w.writerow([r["name"], r["platform"], label(r["state"]), "; ".join(r["seen"]), ""])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="equipos-del-mapa.csv"'})


@router.get("/reports/mapinv/pdf")
def mapinv_pdf(request: Request):
    user = require(request)
    data = pdf(collect(user), user)
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="equipos-del-mapa.pdf"'})


def pdf(m: dict, user) -> bytes:
    """PDF con un campo para escribir la IP de cada equipo que falta (se llena desde la PC con cualquier lector de PDF, o
    se imprime y se escribe a mano en la línea)."""
    from xml.sax.saxutils import escape as e

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    class IPField(Flowable):
        """Campo de texto rellenable del PDF (AcroForm), sobre una línea para escribir a mano si se imprime."""
        def __init__(self, name, w=4.2 * cm, h=0.62 * cm):
            super().__init__()
            self.name, self.width, self.height = name, w, h

        def draw(self):
            c = self.canv
            c.setStrokeColor(colors.HexColor("#9aa3b5"))
            c.line(0, 1, self.width, 1)
            c.acroForm.textfield(name=self.name, tooltip=_("IP para agregarlo"), x=0, y=2, width=self.width, height=self.height - 2,
                                 borderWidth=0, fillColor=colors.HexColor("#f3f6fd"), textColor=colors.black, fontSize=9,
                                 forceBorder=False, relative=True)

    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontName="Helvetica-Bold", fontSize=16, spaceAfter=6)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=12, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontName="Helvetica", fontSize=9, leading=12)
    cell = ParagraphStyle("c", parent=body, fontSize=8, leading=10)
    story = [Paragraph(e(_("Equipos del mapa: agregados y sin agregar")), h1),
             Paragraph(e(_f("{0} · generado por {1}", m["at"].strftime("%Y-%m-%d %H:%M"), user.username)), body),
             Paragraph(e(_("Los equipos que se ven en el mapa de la red (vecinos LLDP/CDP/MNDP y RoMON) cruzados con Dispositivos.") + " "
                         + _("Para agregar los que faltan, escriba su IP en el campo (se puede llenar desde la PC con el lector de PDF, o imprimir y escribirla a mano).")),
                       body), Spacer(1, 6)]
    counts = [(label(k), m["n"][k]) for k in (ST_NEW, ST_FAIL, ST_NEVER, ST_OK)]
    story.append(Table([[Paragraph(f"<b>{e(t)}</b>", cell) for t, _n in counts], [Paragraph(str(v), body) for _t, v in counts]],
                       colWidths=[4.3 * cm] * 4, style=TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d7dce6")),
                                                                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef1f8"))])))
    grid = TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d7dce6")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                       ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef1f8")), ("TOPPADDING", (0, 0), (-1, -1), 4),
                       ("BOTTOMPADDING", (0, 0), (-1, -1), 4)])
    new = [r for r in m["rows"] if r["state"] == ST_NEW]
    if new:
        story.append(Paragraph(e(_f("No están agregados ({0})", len(new))), h2))
        t = [[Paragraph(f"<b>{e(x)}</b>", cell) for x in (_("Equipo"), _("Modelo o plataforma"), _("Visto desde"), _("IP para agregarlo"))]]
        for i, r in enumerate(new):
            t.append([Paragraph(e(r["name"]), cell), Paragraph(e(r["platform"] or "—"), cell), Paragraph(e(", ".join(r["seen"])), cell),
                      IPField(f"ip_{i + 1}")])
        story.append(Table(t, colWidths=[4.4 * cm, 3.8 * cm, 4.6 * cm, 4.6 * cm], repeatRows=1, style=grid))
    have = [r for r in m["rows"] if r["state"] != ST_NEW]
    if have:
        story.append(Paragraph(e(_f("Ya están en Dispositivos ({0})", len(have))), h2))
        t = [[Paragraph(f"<b>{e(x)}</b>", cell) for x in (_("Equipo"), _("Modelo o plataforma"), _("Estado"), _("Último respaldo"))]]
        for r in have:
            t.append([Paragraph(e(r["name"]), cell), Paragraph(e(r["platform"] or "—"), cell), Paragraph(e(label(r["state"])), cell),
                      Paragraph(e(r["at"].strftime("%Y-%m-%d %H:%M") if r.get("at") else "—"), cell)])
        story.append(Table(t, colWidths=[4.4 * cm, 3.8 * cm, 5.2 * cm, 4.0 * cm], repeatRows=1, style=grid))
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.6 * cm, bottomMargin=1.6 * cm,
                      title=_("Equipos del mapa"), author="OpenNCCM").build(story)
    return buf.getvalue()
