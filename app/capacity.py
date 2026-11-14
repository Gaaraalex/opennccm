"""Página Administración › Capacidad, que dice cuánto aguanta este servidor con la red de hoy y qué conviene ampliar.

Junta lo que se sabe de este servidor (núcleos, RAM, disco, base de datos), de la red (equipos, versiones y cuánto
crecen) y de los respaldos de verdad (cada «Respaldar todos» y cada respaldo programado deja su duración: record_cycle)
para estimar cuánto dura un ciclo completo de respaldos frente a la tarea programada más frecuente, cuántos días de disco
quedan y si la base de datos sigue siendo la adecuada. Con eso da recomendaciones concretas.

Abajo muestra lo medido en la prueba de carga de la versión (tests/load_test.py en la CI, por escalones hasta 5.000
equipos simulados): queda en app/capacity_ref.json y es la referencia de qué servidor hace falta para cada tamaño."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from . import metrics
from .auth import require
from .config import DATABASE_URL, DATA_DIR, WORKERS
from .db import ConfigVersion, Device, Schedule, SessionLocal, engine, get_setting, now, set_setting
from .i18n import _, _f
from .web import flash, go, log_action, render

router = APIRouter()
REF_FILE = Path(__file__).with_name("capacity_ref.json")
B64 = 1.37            # el contenido se guarda cifrado y en base64: ocupa ~37 % más que la configuración
DEFAULT_SECS = 4.0    # segundos por equipo si aún no hay respaldos medidos
SQLITE_OK = 2000      # equipos hasta los que SQLite va bien (prueba de carga); más: PostgreSQL


def record_cycle(kind: str, total: int, ok: int, err: int, secs: float, conc: int, per_dev: float | None = None) -> None:
    """Guarda la duración de un respaldo de muchos equipos (los últimos 10) para estimar la capacidad."""
    if total < 1:
        return
    try:
        hist = json.loads(get_setting("capacity_cycles", "[]") or "[]")
    except ValueError:
        hist = []
    hist.append({"at": now().isoformat(), "kind": kind, "total": total, "ok": ok, "err": err, "secs": round(secs, 1),
                 "conc": conc, "per_dev": round(per_dev if per_dev else secs * conc / total, 2)})
    set_setting("capacity_cycles", json.dumps(hist[-10:]))


def cycles() -> list[dict]:
    try:
        return json.loads(get_setting("capacity_cycles", "[]") or "[]")
    except ValueError:
        return []


def concurrency() -> int:
    from .bulkbackup import concurrency as c
    return c()


def _db_size() -> int | None:
    if DATABASE_URL.startswith("sqlite"):
        return sum(p.stat().st_size for p in DATA_DIR.glob("opennccm.db*") if p.is_file())
    try:
        with engine.connect() as conn:
            return int(conn.exec_driver_sql("SELECT pg_database_size(current_database())").scalar() or 0)
    except Exception:  # noqa: BLE001  (otra base de datos o sin permiso)
        return None


def _interval(cron: str) -> float | None:
    """Horas entre dos ejecuciones seguidas de una tarea programada."""
    from . import cronutil
    try:
        trig = cronutil.trigger(cron)
        a = trig.get_next_fire_time(None, datetime.now().astimezone())
        b = trig.get_next_fire_time(a, a + timedelta(seconds=1))
    except (ValueError, TypeError):
        return None
    return (b - a).total_seconds() / 3600 if a and b else None


def reference() -> dict | None:
    try:
        return json.loads(REF_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def snapshot() -> dict:
    """Todo lo que la página muestra, ya calculado (también lo usa la prueba)."""
    snap = metrics.snapshot()
    since = now() - timedelta(days=30)
    with SessionLocal() as db:
        devs = db.scalar(select(func.count(Device.id)).where(Device.retired_at.is_(None))) or 0
        active = db.scalar(select(func.count(Device.id)).where(Device.retired_at.is_(None), Device.enabled.is_(True))) or 0
        versions = db.scalar(select(func.count(ConfigVersion.id))) or 0
        new_n, new_bytes = db.execute(select(func.count(ConfigVersion.id), func.coalesce(func.sum(ConfigVersion.size), 0))
                                      .where(ConfigVersion.created_at >= since)).one()
        scheds = [(s.name, s.cron, s.group_id) for s in db.scalars(select(Schedule).where(Schedule.enabled.is_(True),
                                                                                           Schedule.action == "backup"))]
    hist = cycles()
    per_dev = hist[-1]["per_dev"] if hist else DEFAULT_SECS
    conc = concurrency()
    sched = sorted(((h, n) for n, c, _g in scheds if (h := _interval(c))), key=lambda x: x[0])
    sched_h, sched_name = (sched[0] if sched else (None, None))
    # el respaldo programado usa los hilos del servicio (NCM_WORKERS); «Respaldar todos», backup_concurrency
    cycle_sched = active * per_dev / max(WORKERS, 1) / 3600
    cycle_bulk = active * per_dev / conc / 3600
    growth = new_bytes * B64 / 30  # bytes al día
    days_left = snap["disk_free"] / growth if growth > 0 else None
    dbsize = _db_size()
    engine_name = "SQLite" if DATABASE_URL.startswith("sqlite") else ("PostgreSQL" if "postgres" in DATABASE_URL else DATABASE_URL.split(":")[0])
    return {"server": snap, "devices": devs, "active": active, "versions": versions, "new_30d": new_n,
            "growth_day": growth, "days_left": days_left, "db_size": dbsize, "engine": engine_name, "workers": WORKERS,
            "conc": conc, "per_dev": per_dev, "measured": bool(hist), "cycles": hist[::-1], "cycle_bulk_h": cycle_bulk,
            "cycle_sched_h": cycle_sched, "sched_h": sched_h, "sched_name": sched_name,
            "retention": int(get_setting("retention_versions", "0") or 0)}


def advice(s: dict) -> list[dict]:
    """Recomendaciones: (gravedad, título, detalle) de más a menos urgente; si todo va bien, una que lo dice."""
    out = []
    srv = s["server"]
    # 1. respaldo programado que no termina antes del siguiente
    if s["sched_h"] and s["cycle_sched_h"] > s["sched_h"] * 0.8:
        out.append({"sev": "crit", "t": _("El respaldo programado no termina antes del siguiente"),
                    "d": _f("Respaldar los {0} equipos tarda unas {1} y la tarea «{2}» se repite cada {3}. Espacia la tarea, divídela por grupos a distintas horas o sube NCM_WORKERS en opennccm.env (hoy {4}) si el servidor tiene CPU libre.", s["active"], hours(s["cycle_sched_h"]), s["sched_name"],
                            hours(s["sched_h"]), s["workers"])})
    # 2. «Respaldar todos» largo con pocos respaldos a la vez
    if s["cycle_bulk_h"] > 0.5 and s["conc"] < 16:
        need = min(16, max(s["conc"] + 1, math.ceil(s["active"] * s["per_dev"] / 1800)))
        out.append({"sev": "major", "t": _("«Respaldar todos» tarda mucho"),
                    "d": _f("Con {0} respaldos a la vez, respaldar los {1} equipos tarda unas {2}. Con {3} a la vez tardaría unas {4} (más abajo se cambia).", s["conc"], s["active"], hours(s["cycle_bulk_h"]),
                            need, hours(s["active"] * s["per_dev"] / need / 3600)), "conc": need})
    # 3. base de datos
    if s["engine"] == "SQLite" and s["devices"] > SQLITE_OK:
        out.append({"sev": "major", "t": _("Conviene pasar a PostgreSQL"),
                    "d": _f("Con más de {0} equipos, SQLite atiende de a una escritura por vez y las páginas y los respaldos se esperan unos a otros. PostgreSQL se elige al instalar (guía de instalación, sección 15) y no hay migración automática desde SQLite: conviene planificarlo.", SQLITE_OK)})
    elif s["engine"] == "SQLite" and s["devices"] > SQLITE_OK * 0.75:
        out.append({"sev": "info", "t": _("Cerca del límite recomendado para SQLite"),
                    "d": _f("Hay {0} equipos; por encima de {1} conviene PostgreSQL. Puede planificarlo con calma.",
                            s["devices"], SQLITE_OK)})
    # 4. disco
    if s["days_left"] is not None and s["days_left"] < 90:
        sev = "crit" if s["days_left"] < 30 else "major"
        d = _f("Las versiones nuevas ocupan unos {0} al día y quedan {1} libres: alcanza para unos {2} días.",
               metrics.human_bytes(s["growth_day"]), metrics.human_bytes(srv["disk_free"]), int(s["days_left"]))
        if not s["retention"]:
            d += " " + _("Ajustes › Flujo de cambios permite conservar solo las últimas N versiones de cada equipo (las etiquetadas nunca se borran).")
        out.append({"sev": sev, "t": _("El disco se llenará pronto"), "d": d})
    # 5. memoria y CPU
    if srv["mem_pct"] >= 90:
        out.append({"sev": "major", "t": _("Poca memoria libre"),
                    "d": _f("El servidor usa el {0} % de la RAM ({1} en total). Amplíe la memoria o mueva otros servicios a otra máquina.", round(srv["mem_pct"]), metrics.human_bytes(srv["mem_total"]))})
    if srv["cpus"] < 2 and s["devices"] > 300:
        out.append({"sev": "major", "t": _("Un solo núcleo de CPU"),
                    "d": _f("Con {0} equipos, las páginas se vuelven lentas mientras hay respaldos. Use al menos 2 núcleos (4 desde 1.000 equipos).", s["devices"])})
    elif srv["cpus"] < 4 and s["devices"] > 1000:
        out.append({"sev": "info", "t": _("Más núcleos ayudarían"),
                    "d": _f("Con {0} equipos se recomiendan 4 núcleos o más; este servidor tiene {1}.", s["devices"], srv["cpus"])})
    if not s["measured"] and s["active"]:
        out.append({"sev": "info", "t": _("Aún no hay respaldos medidos"),
                    "d": _("Las estimaciones usan 4 s por equipo hasta que termine un «Respaldar todos» o un respaldo programado; después usan lo medido en esta red.")})
    if not [a for a in out if a["sev"] != "info"]:
        out.insert(0, {"sev": "ok", "t": _("El servidor va holgado con la red de hoy"),
                       "d": _f("{0} equipos, {1} núcleos, {2} de RAM y {3} de disco libre. No hace falta ampliar nada.",
                               s["devices"], srv["cpus"], metrics.human_bytes(srv["mem_total"]),
                               metrics.human_bytes(srv["disk_free"]))})
    return out


def hours(h: float) -> str:
    m = max(1, round(h * 60))
    if m < 60:
        return _f("{0} min", m)
    return _f("{0} h {1} min", m // 60, m % 60) if m % 60 else _f("{0} h", m // 60)


@router.get("/capacity")
def capacity_page(request: Request):
    user = require(request, "admin")
    s = snapshot()
    ref = reference()
    near = None
    if ref and ref.get("steps"):
        near = min(ref["steps"], key=lambda r: abs(r["devices"] - max(s["devices"], 1)))["devices"]
    return render(request, "capacity.html", user, s=s, adv=advice(s), ref=ref, near=near, hb=metrics.human_bytes,
                  hours=hours)


@router.post("/capacity/concurrency")
async def capacity_concurrency(request: Request):
    """Respaldos a la vez de «Respaldar todos» (1 a 16)."""
    user = require(request, "admin")
    form = await request.form()
    v = str(form.get("backup_concurrency") or "").strip()
    if not v.isdigit() or not 1 <= int(v) <= 16:
        flash(request, _("Elige entre 1 y 16 respaldos a la vez."), "error")
        return go("/capacity")
    set_setting("backup_concurrency", v)
    log_action(request, user, "backup_concurrency", v)
    flash(request, _f("«Respaldar todos» hará {0} respaldos a la vez.", v))
    return go("/capacity")
