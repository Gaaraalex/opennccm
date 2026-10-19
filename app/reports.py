"""Informes tabulares (CSV en pantalla, descarga y envío programado por correo) y analítica."""
from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import timedelta

from sqlalchemy import select

from . import eol
from .db import (AuditLog, ChangeRequest, ComplianceResult, ConfigVersion, Device, Policy, SessionLocal,
                 Vulnerability, now)
from .drivers import get_profile
from .i18n import N_

TITLES = {
    "inventory": N_("Inventario"), "changes": N_("Cambios de configuración"), "compliance": N_("Cumplimiento"),
    "vulns": N_("Vulnerabilidades"), "audit": N_("Actividad de usuarios"), "changes_requests": N_("Solicitudes de cambio"),
    "conflicts": N_("Conflictos startup/running"), "baseline": N_("Desviación de línea base"),
    "eol": N_("Fin de vida (EOL/EOS)"),
}


# Encabezados de los informes: el CSV los mantiene en español (integraciones estables); la vista en
# pantalla los traduce (report_view.html)
HEADERS = [
    N_("nombre"), N_("ip"), N_("fabricante"), N_("tipo"), N_("modelo"), N_("serie"),
    N_("versión"), N_("grupo"), N_("ubicación"), N_("estado"), N_("último respaldo"), N_("conflicto startup"),
    N_("desvío baseline"), N_("fecha"), N_("equipo"), N_("origen"), N_("usuario"), N_("+líneas"),
    N_("-líneas"), N_("sha256"), N_("política"), N_("regla"), N_("severidad"), N_("resultado"),
    N_("CVE"), N_("CVSS"), N_("enlace"), N_("acción"), N_("detalle"), N_("id"),
    N_("título"), N_("solicitante"), N_("revisor"), N_("ejecutada"), N_("versión baseline"), N_("desvío"),
    N_("fin de venta"), N_("fin de parches"), N_("fin de soporte"), N_("reemplazo"),
]


def _in(scope, dev_id):
    return scope is None or dev_id in scope


def build(kind: str, scope: set[int] | None = None, days: int = 30) -> tuple[list[str], list[tuple]]:
    since = now() - timedelta(days=days)
    with SessionLocal() as db:
        devs = [d for d in db.scalars(select(Device).order_by(Device.name)) if _in(scope, d.id)]
        if kind == "inventory":
            return (["nombre", "ip", "fabricante", "tipo", "modelo", "serie", "versión", "grupo", "ubicación",
                     "estado", "último respaldo", "conflicto startup", "desvío baseline"],
                    [(d.name, d.host, get_profile(d.device_type).vendor, d.device_type, d.model, d.serial,
                      d.os_version, d.group.name if d.group else "", d.location, d.last_status, d.last_backup_at,
                      "sí" if d.startup_conflict else "no", "sí" if d.baseline_drift else "no") for d in devs])
        if kind == "changes":
            return (["fecha", "equipo", "tipo", "origen", "usuario", "+líneas", "-líneas", "sha256"],
                    [(v.created_at, v.device.name, v.kind, v.trigger, v.username, v.added, v.removed, v.sha256)
                     for v in db.scalars(select(ConfigVersion).where(ConfigVersion.created_at >= since)
                                         .order_by(ConfigVersion.id.desc())) if _in(scope, v.device_id)])
        if kind == "compliance":
            return (["equipo", "política", "regla", "severidad", "resultado", "fecha"],
                    [(r.device.name, r.policy.name, r.rule.name, r.rule.severity, "OK" if r.passed else "FALLA",
                      r.checked_at) for r in db.scalars(select(ComplianceResult)
                                                        .order_by(ComplianceResult.device_id))
                     if _in(scope, r.device_id)])
        if kind == "vulns":
            return (["equipo", "versión", "CVE", "CVSS", "severidad", "enlace"],
                    [(v.device.name, v.device.os_version, v.cve_id, v.score, v.severity, v.url)
                     for v in db.scalars(select(Vulnerability).order_by(Vulnerability.score.desc().nulls_last()))
                     if _in(scope, v.device_id)])
        if kind == "audit":
            return (["fecha", "usuario", "acción", "detalle", "ip"],
                    [(a.created_at, a.username, a.action, a.detail, a.ip)
                     for a in db.scalars(select(AuditLog).where(AuditLog.created_at >= since)
                                         .order_by(AuditLog.id.desc()))])
        if kind == "changes_requests":
            return (["id", "fecha", "tipo", "título", "solicitante", "revisor", "estado", "ejecutada"],
                    [(c.id, c.created_at, c.kind, c.title, c.requested_by, c.reviewed_by, c.status, c.executed_at)
                     for c in db.scalars(select(ChangeRequest).order_by(ChangeRequest.id.desc()))])
        if kind == "conflicts":
            return (["equipo", "ip", "tipo", "último respaldo"],
                    [(d.name, d.host, d.device_type, d.last_backup_at) for d in devs if d.startup_conflict])
        if kind == "baseline":
            return (["equipo", "ip", "versión baseline", "desvío"],
                    [(d.name, d.host, d.baseline_version_id or "—", "sí" if d.baseline_drift else "no")
                     for d in devs])
    if kind == "eol":
        rows = eol.report(scope)
        return (["equipo", "modelo", "estado", "fin de venta", "fin de parches", "fin de soporte", "reemplazo"],
                [(d.name, d.model, eol.STATUS_LABEL[st], e.end_of_sale.date() if e and e.end_of_sale else "",
                  e.end_of_sw_maintenance.date() if e and e.end_of_sw_maintenance else "",
                  e.end_of_support.date() if e and e.end_of_support else "", e.replacement if e else "")
                 for d, e, st in rows])
    raise KeyError(kind)


def to_csv(header, rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return ("﻿" + buf.getvalue()).encode("utf-8")


def email_reports(kinds: list[str], days: int = 7) -> list[str]:
    from .services import send_alert
    attachments, lines = [], []
    for k in kinds:
        header, rows = build(k, None, days)
        attachments.append((f"{k}.csv", to_csv(header, rows)))
        lines.append(f"- {TITLES[k]}: {len(rows)} filas")
    return send_alert(f"[OpenNCCM] Informe programado ({now():%Y-%m-%d})",
                      "Se adjuntan los informes:\n" + "\n".join(lines), attachments, email_only=True)


def analytics(scope: set[int] | None, days: int = 30) -> dict:
    since = now() - timedelta(days=days)
    with SessionLocal() as db:
        devs = [d for d in db.scalars(select(Device)) if _in(scope, d.id)]
        versions = [v for v in db.scalars(select(ConfigVersion).where(ConfigVersion.created_at >= since,
                                                                      ConfigVersion.kind == "running"))
                    if _in(scope, v.device_id)]
        names = {d.id: d.name for d in devs}
        per_day = Counter(v.created_at.strftime("%m-%d") for v in versions)
        day_axis = [(now() - timedelta(days=i)).strftime("%m-%d") for i in range(days - 1, -1, -1)]
        per_day_trigger = Counter((v.created_at.strftime("%m-%d"), v.trigger) for v in versions)
        heat = Counter((v.created_at.weekday(), v.created_at.hour) for v in versions)
        prev_changes = sum(1 for v in db.scalars(select(ConfigVersion).where(
            ConfigVersion.created_at >= since - timedelta(days=days), ConfigVersion.created_at < since,
            ConfigVersion.kind == "running")) if _in(scope, v.device_id))
        all_res = [r for r in db.scalars(select(ComplianceResult)) if _in(scope, r.device_id)]
        by_device = Counter(names.get(v.device_id, "?") for v in versions)
        by_trigger = Counter(v.trigger for v in versions)
        by_user = Counter(v.username or "—" for v in versions)
        by_vendor = Counter(get_profile(d.device_type).vendor for d in devs)
        by_version = Counter(f"{get_profile(d.device_type).vendor} {d.os_version}" for d in devs if d.os_version)
        comp = []
        for p in db.scalars(select(Policy).order_by(Policy.name)):
            res = [r for r in db.scalars(select(ComplianceResult).where(ComplianceResult.policy_id == p.id))
                   if _in(scope, r.device_id)]
            if res:
                comp.append((p.name, round(100 * sum(r.passed for r in res) / len(res)), p.id))
    eol_status = Counter(st for _, _, st in eol.report(scope))
    eol_counts = Counter({eol.STATUS_LABEL[k]: n for k, n in eol_status.items()})
    triggers = [t for t in ("manual", "scheduled", "syslog", "push") if by_trigger.get(t)]
    triggers += [t for t, _n in by_trigger.most_common() if t not in triggers]
    return {
        "per_day": [(d, per_day.get(d, 0)) for d in day_axis],
        "day_axis": day_axis,
        "by_day_trigger": [(t, [per_day_trigger.get((d, t), 0) for d in day_axis]) for t in triggers],
        "heat": [[heat.get((wd, h), 0) for h in range(24)] for wd in range(7)],
        "prev_changes": prev_changes,
        "compliance_pct": round(100 * sum(r.passed for r in all_res) / len(all_res)) if all_res else None,
        "eol_status": eol_status.most_common(),
        "by_device": by_device.most_common(10), "by_trigger": by_trigger.most_common(),
        "by_user": by_user.most_common(10), "by_vendor": by_vendor.most_common(12),
        "by_version": by_version.most_common(10), "compliance": comp, "eol": eol_counts.most_common(),
        "conflicts": sum(1 for d in devs if d.startup_conflict),
        "drift": sum(1 for d in devs if d.baseline_drift), "total": len(devs), "changes": len(versions),
    }
