"""Informes tabulares (CSV en pantalla, descarga y envío programado por correo) y analítica."""
from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import timedelta

from sqlalchemy import case, func, select

from . import eol
from .db import (AuditLog, ChangeRequest, ComplianceResult, ConfigVersion, Device, Policy, SessionLocal,
                 Vulnerability, now)
from .drivers import get_profile
from .i18n import N_

TITLES = {
    "inventory": N_("Inventario"), "changes": N_("Cambios de configuración"), "compliance": N_("Cumplimiento"),
    "vulns": N_("Vulnerabilidades"), "audit": N_("Auditoría de usuarios"), "changes_requests": N_("Solicitudes de cambio"),
    "conflicts": N_("Conflictos startup ≠ running"), "baseline": N_("Fuera de línea base"),
    "eol": N_("Fin de vida (EOL/EOS)"), "licenses": N_("Licencias por vencer"),
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
    N_("servicio"), N_("vence"), N_("días"),
]


def _in(scope, dev_id):
    return scope is None or dev_id in scope


def build(kind: str, scope: set[int] | None = None, days: int = 30) -> tuple[list[str], list[tuple]]:
    since = now() - timedelta(days=days)
    with SessionLocal() as db:
        devs = [d for d in db.scalars(select(Device).where(Device.retired_at.is_(None)).order_by(Device.name)) if _in(scope, d.id)]
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
    if kind == "licenses":  # lo que vence en los próximos 90 días y lo ya vencido (app.licenses)
        from . import licenses
        return ["equipo", "modelo", "servicio", "vence", "días"], licenses.report(scope)
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


def _hour_key(col):
    """La fecha y la hora de una columna como texto «AAAA-MM-DD HH», para agrupar en la base (SQLite o PostgreSQL)."""
    from .db import engine
    if engine.dialect.name == "sqlite":
        return func.strftime("%Y-%m-%d %H", col)
    return func.to_char(col, "YYYY-MM-DD HH24")


def analytics(scope: set[int] | None, days: int = 30) -> dict:
    """Indicadores del panel y de Analítica. Se cuentan en la base (GROUP BY) y se leen solo las columnas que hacen
    falta: con miles de equipos y copias, traer cada copia (con su texto) para contarla era lo que más tardaba."""
    since = now() - timedelta(days=days)
    in_scope = (lambda col: col.in_(scope)) if scope is not None else None

    def scoped(stmt, col):
        return stmt if in_scope is None else stmt.where(in_scope(col))
    run = (ConfigVersion.kind == "running",)
    with SessionLocal() as db:
        dev_rows = db.execute(scoped(select(Device.device_type, Device.os_version, func.count(Device.id),
                                            func.sum(case((Device.startup_conflict.is_(True), 1), else_=0)),
                                            func.sum(case((Device.baseline_drift.is_(True), 1), else_=0)))
                                     .where(Device.retired_at.is_(None)), Device.id)
                              .group_by(Device.device_type, Device.os_version).order_by(func.min(Device.id))).all()
        hk = _hour_key(ConfigVersion.created_at)
        hours = db.execute(scoped(select(hk, ConfigVersion.trigger, func.count(ConfigVersion.id))
                                  .where(ConfigVersion.created_at >= since, *run), ConfigVersion.device_id)
                           .group_by(hk, ConfigVersion.trigger)).all()
        per_dev = db.execute(scoped(select(ConfigVersion.device_id, func.count(ConfigVersion.id))
                                    .where(ConfigVersion.created_at >= since, *run), ConfigVersion.device_id)
                             .group_by(ConfigVersion.device_id)).all()
        per_user = db.execute(scoped(select(ConfigVersion.username, func.count(ConfigVersion.id))
                                     .where(ConfigVersion.created_at >= since, *run), ConfigVersion.device_id)
                              .group_by(ConfigVersion.username)).all()
        prev_changes = db.scalar(scoped(select(func.count(ConfigVersion.id)).where(
            ConfigVersion.created_at >= since - timedelta(days=days), ConfigVersion.created_at < since, *run),
            ConfigVersion.device_id)) or 0
        res_rows = db.execute(scoped(select(ComplianceResult.policy_id, ComplianceResult.passed, func.count(ComplianceResult.id)),
                                     ComplianceResult.device_id)
                              .group_by(ComplianceResult.policy_id, ComplianceResult.passed)).all()
        top_ids = [i for i, _n in sorted(per_dev, key=lambda r: -r[1])[:10]]
        names = {i: n for i, n in db.execute(select(Device.id, Device.name).where(Device.id.in_(top_ids)))} if top_ids else {}
        per_policy: dict[int, list[int]] = {}
        for pid, passed, n in res_rows:
            t = per_policy.setdefault(pid, [0, 0])
            t[0] += n
            t[1] += n if passed else 0
        comp = [(name, round(100 * per_policy[pid][1] / per_policy[pid][0]), pid)
                for pid, name in db.execute(select(Policy.id, Policy.name).order_by(Policy.name)) if pid in per_policy]
    from datetime import datetime
    per_day, per_day_trigger, heat, by_trigger, stamps = Counter(), Counter(), Counter(), Counter(), []
    for key, trig, n in hours:
        t = datetime.strptime(str(key), "%Y-%m-%d %H")
        per_day[t.strftime("%m-%d")] += n
        per_day_trigger[(t.strftime("%m-%d"), trig)] += n
        heat[(t.weekday(), t.hour)] += n
        by_trigger[trig] += n
        stamps.append((t + timedelta(minutes=30), n))
    day_axis = [(now() - timedelta(days=i)).strftime("%m-%d") for i in range(days - 1, -1, -1)]
    by_device = Counter({names.get(i, "?"): n for i, n in per_dev if i in names})
    by_user = Counter()
    for u, n in per_user:
        by_user[u or "—"] += n
    by_vendor, by_version = Counter(), Counter()
    for dtype, osv, n, _c, _d in dev_rows:
        vendor = get_profile(dtype).vendor
        by_vendor[vendor] += n
        if osv:
            by_version[f"{vendor} {osv}"] += n
    total_res = sum(t[0] for t in per_policy.values())
    changes = sum(n for _k, _t, n in hours)
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
        "compliance_pct": round(100 * sum(t[1] for t in per_policy.values()) / total_res) if total_res else None,
        "eol_status": eol_status.most_common(),
        "by_device": by_device.most_common(10), "by_trigger": by_trigger.most_common(),
        "by_user": by_user.most_common(10), "by_vendor": by_vendor.most_common(12),
        "by_version": by_version.most_common(10), "compliance": comp, "eol": eol_counts.most_common(),
        "conflicts": sum(r[3] or 0 for r in dev_rows),
        "drift": sum(r[4] or 0 for r in dev_rows), "total": sum(r[2] for r in dev_rows), "changes": changes,
        "devices_changed": len(per_dev), "per_week": _weekly(stamps, days),
    }


def _weekly(stamps, days: int) -> list[int]:
    """Cambios por tramo (8 tramos iguales del periodo), para la minigráfica del indicador «Cambios».
    «stamps»: (hora, cuántos) ya contados en la base."""
    if not stamps:
        return [0] * 8
    start = now() - timedelta(days=days)
    out = [0] * 8
    for t, n in stamps:
        i = int((t - start).total_seconds() / (days * 86400) * 8)
        out[max(0, min(7, i))] += n
    return out
