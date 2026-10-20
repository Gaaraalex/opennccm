"""Panel: puntuación de salud de la red y bandeja de atención.

Puntuación (0-100): 40 % equipos respaldados en las últimas 24 h, 30 % reglas de cumplimiento que se cumplen,
15 % equipos sin CVE crítico o alto y 15 % equipos con soporte del fabricante vigente. Los componentes sin datos
(p. ej. sin auditorías todavía) no cuentan y su peso se reparte entre los demás.

Bandeja de atención: una lista de tareas (no de eventos), agrupada por equipo y ordenada por gravedad, con una sola
acción por fila que resuelve o lleva a resolver.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select

from . import eol
from .db import ChangeRequest, ComplianceResult, Device, SessionLocal, Vulnerability, now
from .i18n import N_, _, _f

SEV_ORDER = {"crit": 0, "major": 1, "info": 2, "warn": 3}
PHRASES = [(90, N_("Tu red está bien")), (70, N_("Casi todo en orden")), (50, N_("Hay trabajo pendiente")),
           (0, N_("Tu red necesita atención"))]


def _in(scope, dev_id):
    return scope is None or dev_id in scope


def summary(scope: set[int] | None, is_admin: bool = False, server: dict | None = None) -> dict:
    since = now() - timedelta(hours=24)
    with SessionLocal() as db:
        devs = [d for d in db.scalars(select(Device).order_by(Device.name)) if _in(scope, d.id) and d.enabled]
        ids = {d.id for d in devs}
        results = [r for r in db.scalars(select(ComplianceResult)) if r.device_id in ids]
        fails = defaultdict(list)
        for r in results:
            if not r.passed:
                fails[r.device_id].append(r.rule.severity if r.rule else "major")
        vuln_devs = {v.device_id for v in db.scalars(select(Vulnerability).where(Vulnerability.severity.in_(["CRITICAL", "HIGH"])))
                     if v.device_id in ids}
        pending = sum(1 for _c in db.scalars(select(ChangeRequest.id).where(ChangeRequest.status == "pending")))
    unsupported = {d.id for d, _e, st in eol.report(scope) if st in ("sin_soporte", "fin_mantenimiento") and d.id in ids}
    total = len(devs)
    backed = sum(1 for d in devs if d.last_status == "ok" and d.last_backup_at and d.last_backup_at >= since)
    parts = []  # (peso, fracción)
    if total:
        parts += [(40, backed / total), (15, 1 - len(vuln_devs) / total), (15, 1 - len(unsupported) / total)]
    if results:
        parts.append((30, sum(r.passed for r in results) / len(results)))
    score = round(sum(w * fr for w, fr in parts) / sum(w for w, _fr in parts) * 100) if parts else None
    phrase = next(_(p) for limit, p in PHRASES if score is None or score >= limit) if score is not None else None
    errors = [d for d in devs if d.last_status == "error"]
    drift = [d for d in devs if d.baseline_drift]
    conflict = [d for d in devs if d.startup_conflict]

    # chips del héroe: solo lo que no está a cero, por gravedad
    chips = []
    if errors:
        chips.append(("hot", _f("{0} sin respaldo", len(errors)) if len(errors) != 1 else _("1 sin respaldo"), "/devices?status=error"))
    crit_devs = [i for i, sev in fails.items() if "critical" in sev]
    if crit_devs:
        chips.append(("", _f("{0} con reglas críticas", len(crit_devs)), "/compliance"))
    if pending:
        chips.append(("", _f("{0} cambios por aprobar", pending) if pending != 1 else _("1 cambio por aprobar"), "/changes?status=pending"))
    if drift:
        chips.append(("", _f("{0} fuera de su línea base", len(drift)), "/devices?status=drift"))

    # bandeja de atención
    queue = []
    names = {d.id: d for d in devs}
    for d in errors:
        queue.append({"sev": "crit", "icon": "alert", "title": _f("El respaldo de {0} falló", d.name),
                      "sub": (d.last_error or "").strip().splitlines()[0][:140] if d.last_error else d.host,
                      "action": _("Reintentar"), "post": f"/devices/{d.id}/backup", "href": f"/devices/{d.id}"})
    for dev_id, sevs in sorted(fails.items(), key=lambda kv: (-kv[1].count("critical"), -len(kv[1]))):
        d = names.get(dev_id)
        if not d:
            continue
        crit = sevs.count("critical")
        sub = _f("{0} críticas · {1} en total", crit, len(sevs)) if crit else _f("{0} reglas sin cumplir", len(sevs))
        queue.append({"sev": "crit" if crit else "major", "icon": "shield",
                      "title": _f("{0} incumple {1} reglas", d.name, len(sevs)) if len(sevs) != 1 else _f("{0} incumple 1 regla", d.name),
                      "sub": sub, "action": _("Revisar"), "href": f"/devices/{d.id}#compliance"})
    if pending:
        queue.append({"sev": "info", "icon": "branch",
                      "title": _f("{0} cambios esperan aprobación", pending) if pending != 1 else _("1 cambio espera aprobación"),
                      "sub": _("Solicitudes de cambio pendientes"), "action": _("Revisar"), "href": "/changes?status=pending"})
    if vuln_devs:
        queue.append({"sev": "major", "icon": "bug", "title": _f("{0} equipos con CVE críticos o altos", len(vuln_devs))
                      if len(vuln_devs) != 1 else _("1 equipo con CVE críticos o altos"),
                      "sub": _("Vulnerabilidades de firmware publicadas en NVD"), "action": _("Revisar"), "href": "/vulns"})
    for d in drift:
        queue.append({"sev": "warn", "icon": "flag", "title": _f("{0} difiere de su línea base", d.name),
                      "sub": _("La configuración actual no coincide con la versión de referencia"),
                      "action": _("Ver diferencias"), "href": f"/devices/{d.id}/baseline-diff"})
    for d in conflict:
        queue.append({"sev": "warn", "icon": "save", "title": _f("{0} tiene cambios sin guardar en arranque", d.name),
                      "sub": "running ≠ startup", "action": _("Revisar"), "href": f"/devices/{d.id}"})
    if unsupported:
        queue.append({"sev": "warn", "icon": "clock", "title": _f("{0} equipos sin soporte del fabricante", len(unsupported))
                      if len(unsupported) != 1 else _("1 equipo sin soporte del fabricante"),
                      "sub": _("Fin de soporte o de parches de software"), "action": _("Revisar"), "href": "/eol?status=sin_soporte"})
    if is_admin and server:
        snap = server["snap"]
        if snap["disk_pct"] >= 90:
            queue.append({"sev": "warn", "icon": "db", "title": _f("El disco del servidor está al {0}%", round(snap["disk_pct"])),
                          "sub": _("Libere espacio o amplíe el disco"), "action": _("Revisar"), "href": "/system#servidor"})
        if snap["mem_pct"] >= 90:
            queue.append({"sev": "warn", "icon": "chip", "title": _f("La memoria del servidor está al {0}%", round(snap["mem_pct"])),
                          "sub": _("OpenNCCM o el sistema usan casi toda la RAM"), "action": _("Revisar"), "href": "/system#servidor"})
    queue.sort(key=lambda q: SEV_ORDER[q["sev"]])
    return {"score": score, "phrase": phrase, "total": total, "backed": backed, "chips": chips[:3],
            "queue": queue[:6], "queue_total": len(queue), "perfect": score == 100}
