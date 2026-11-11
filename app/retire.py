"""Equipos retirados (Dispositivos › Retirados): un equipo dañado, reemplazado por otro o devuelto se retira en lugar de
borrarlo. Queda desactivado y aislado (OpenNCCM no se conecta con él), con todo su historial para auditarlo (versiones,
cumplimiento, CVE, eventos, puertos) y fuera de todo lo activo (panel, mapa, alarmas, informes, tareas).

Al retirarlo se renombra «NOMBRE (n)» (n = 1, 2, 3… por cada retirado que tuvo ese nombre) y se le quita la dirección,
para que el equipo que lo reemplaza use el mismo nombre y la misma IP; el nombre y la IP originales se guardan y se
puede buscar por ellos. En el mismo paso se puede dar de alta el reemplazo con sus datos. «Reincorporar» lo devuelve.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from .auth import require
from .db import Device, Event, SessionLocal, now
from .i18n import N_, _, _f
from .web import flash, go, log_action, render

router = APIRouter()

REASONS = {"damaged": N_("Dañado"), "replaced": N_("Reemplazado por otro"), "returned": N_("Devuelto o dado de baja"),
           "other": N_("Otro motivo")}
# lo que pasa del retirado al equipo que lo reemplaza (lo propio del equipo físico: inventario, respaldos, licencia,
# imagen .fxi, puertos o vecinos, no)
COPY = ("port", "protocol", "device_type", "credential_id", "group_id", "node_id", "map_role", "location", "notes",
        "auto_rollback", "custom_backup_cmd", "web_url", "web_enabled")


def active():
    """Condición de los equipos en servicio (no retirados), para las listas, el panel, el mapa y las tareas."""
    return Device.retired_at.is_(None)


def retired_name(db, name: str) -> str:
    """«NOMBRE (n)»: n sigue la secuencia de los retirados que tuvieron ese nombre, y nunca choca con otro equipo."""
    base = name.strip()
    used = {(d or "").lower() for d in db.scalars(select(Device.name))}
    n = db.scalar(select(func.count(Device.id)).where(func.lower(Device.orig_name) == base.lower(),
                                                      Device.retired_at.is_not(None))) or 0
    while True:
        n += 1
        cand = f"{base} ({n})"
        if cand.lower() not in used:
            return cand[:128]


def retire(db, dev: Device, user: str, reason: str, note: str = "", replace: bool = False) -> Device | None:
    """Retira el equipo y, si se pide, da de alta su reemplazo con el nombre y la IP que tenía. Devuelve el reemplazo."""
    if dev.retired_at is not None:
        raise ValueError(_("El equipo ya está retirado."))
    name, host = dev.name, dev.host
    dev.orig_name, dev.orig_host = name, host
    dev.name = retired_name(db, name)
    dev.host = ""  # la IP queda libre para el reemplazo (se guarda en orig_host)
    dev.enabled = False  # aislado: services.ensure_enabled() corta toda conexión
    dev.retired_at, dev.retired_by = now(), user
    dev.retired_reason = reason if reason in REASONS else "other"
    dev.retired_note = (note or "").strip()[:2000] or None
    dev.maintenance_until = None
    # sus alarmas abiertas se cierran: ya no es un equipo en servicio
    for ev in db.scalars(select(Event).where(Event.device_id == dev.id, Event.acknowledged.is_(False))):
        ev.acknowledged = True
    db.flush()  # libera el nombre antes de dar de alta el reemplazo
    new = None
    if replace:
        new = Device(name=name, host=host, enabled=True, replaces_id=dev.id)
        for f in COPY:
            setattr(new, f, getattr(dev, f))
        db.add(new)
        db.flush()
        dev.replaced_by_id = new.id
    db.add(Event(severity="info", category="retirado", device_id=dev.id, acknowledged=True,
                 message=_f("{0} retirado ({1}) por {2}: ahora «{3}», sin dirección.", name, _(REASONS[dev.retired_reason]),
                            user, dev.name) + (" " + _f("Lo reemplaza «{0}».", name) if new else "")))
    db.commit()
    return new


def reinstate(db, dev: Device, user: str, name: str, host: str, enable: bool) -> None:
    """Devuelve un retirado al servicio con el nombre y la IP indicados (por omisión, los que tenía)."""
    if dev.retired_at is None:
        raise ValueError(_("El equipo no está retirado."))
    name, host = (name or "").strip()[:128], (host or "").strip()[:255]
    if not name or not host:
        raise ValueError(_("Indica el nombre y la dirección con los que vuelve."))
    if db.scalar(select(Device.id).where(func.lower(Device.name) == name.lower(), Device.id != dev.id)):
        raise ValueError(_f("Ya hay otro equipo llamado «{0}». Elige otro nombre.", name))
    # misma IP y mismo puerto en un equipo en servicio (con otro puerto puede ser otro equipo detrás de un NAT)
    other = db.scalar(select(Device).where(func.lower(Device.host) == host.lower(), Device.port == dev.port,
                                           Device.id != dev.id, active()))
    if other is not None:
        raise ValueError(_f("La dirección {0} la usa «{1}». Elige otra dirección.", host, other.name))
    old = dev.name
    if dev.replaced_by_id:  # el reemplazo deja de apuntar a él
        rep = db.get(Device, dev.replaced_by_id)
        if rep is not None and rep.replaces_id == dev.id:
            rep.replaces_id = None
    dev.name, dev.host, dev.enabled = name, host, bool(enable)
    dev.retired_at = dev.retired_by = dev.retired_reason = dev.retired_note = None
    dev.orig_name = dev.orig_host = None
    dev.replaced_by_id = None
    db.add(Event(severity="info", category="reincorporado", device_id=dev.id, acknowledged=True,
                 message=_f("{0} reincorporado por {1} como «{2}» ({3}){4}.", old, user, name, host,
                            "" if enable else " " + _("desactivado"))))
    db.commit()


def matches(d: Device, q: str) -> bool:
    """¿El texto busca este retirado? Por su nombre actual, el original o su IP original."""
    q = q.strip().lower()
    return bool(q) and any(q in (x or "").lower() for x in (d.name, d.orig_name, d.orig_host, d.model, d.serial))


# --------------------------------------------------------------------------- páginas
def _get(db, dev_id: int) -> Device | None:
    return db.get(Device, dev_id)


@router.get("/devices/{dev_id}/retire")
def retire_form(request: Request, dev_id: int):
    user = require(request, "admin")
    with SessionLocal() as db:
        dev = _get(db, dev_id)
        if dev is None:
            return go("/devices")
        if dev.retired_at is not None:
            flash(request, _("El equipo ya está retirado."), "error")
            return go(f"/devices/{dev_id}")
        return render(request, "device_retire.html", user, device=dev, reasons=REASONS, new_name=retired_name(db, dev.name))


@router.post("/devices/{dev_id}/retire")
async def retire_post(request: Request, dev_id: int):
    user = require(request, "admin")
    form = await request.form()
    if form.get("confirm") != "on":
        flash(request, _("Marca la casilla para confirmar que quieres retirar el equipo."), "error")
        return go(f"/devices/{dev_id}")
    with SessionLocal() as db:
        dev = _get(db, dev_id)
        if dev is None:
            return go("/devices")
        name = dev.name
        try:
            new = retire(db, dev, user.username, form.get("reason") or "other", form.get("note") or "",
                         replace=form.get("replace") == "on")
        except ValueError as e:
            flash(request, str(e), "error")
            return go(f"/devices/{dev_id}")
        retired, new_id = dev.name, new.id if new else None
    log_action(request, user, "device_retire", f"{name} -> {retired}" + (f"; reemplazo #{new_id}" if new_id else ""))
    if new_id:
        from . import services
        services.pool.submit(services.backup_device, new_id, "manual", user.username)
        flash(request, _f("«{0}» quedó retirado como «{1}». Se dio de alta el equipo que lo reemplaza y se lanzó su primer respaldo.",
                          name, retired))
        return go(f"/devices/{new_id}")
    flash(request, _f("«{0}» quedó retirado como «{1}»: aislado y con todo su historial en Dispositivos › Retirados.", name, retired))
    return go(f"/devices/{dev_id}")


@router.get("/devices/{dev_id}/reinstate")
def reinstate_form(request: Request, dev_id: int):
    user = require(request, "admin")
    with SessionLocal() as db:
        dev = _get(db, dev_id)
        if dev is None or dev.retired_at is None:
            return go(f"/devices/{dev_id}")
        name_busy = db.scalar(select(Device.name).where(func.lower(Device.name) == (dev.orig_name or "").lower()))
        host_busy = db.scalar(select(Device.name).where(func.lower(Device.host) == (dev.orig_host or "").lower(),
                                                        Device.port == dev.port, active()))
        return render(request, "device_reinstate.html", user, device=dev, name_busy=name_busy, host_busy=host_busy)


@router.post("/devices/{dev_id}/reinstate")
async def reinstate_post(request: Request, dev_id: int):
    user = require(request, "admin")
    form = await request.form()
    with SessionLocal() as db:
        dev = _get(db, dev_id)
        if dev is None:
            return go("/devices")
        old = dev.name
        try:
            reinstate(db, dev, user.username, form.get("name") or "", form.get("host") or "", form.get("enabled") == "on")
        except ValueError as e:
            flash(request, str(e), "error")
            return go(f"/devices/{dev_id}")
        new_name = dev.name
    log_action(request, user, "device_reinstate", f"{old} -> {new_name}")
    flash(request, _f("«{0}» vuelve al servicio como «{1}».", old, new_name))
    return go(f"/devices/{dev_id}")


@router.get("/retired")
def retired_page(request: Request, q: str = ""):
    user = require(request)
    from .auth import device_scope
    scope = device_scope(user)
    with SessionLocal() as db:
        rows = [d for d in db.scalars(select(Device).where(Device.retired_at.is_not(None)).order_by(Device.retired_at.desc()))
                if (scope is None or d.id in scope) and (not q or matches(d, q))]
        reps = {d.id: d for d in db.scalars(select(Device).where(Device.id.in_([r.replaced_by_id for r in rows if r.replaced_by_id] or [0])))}
        out = [{"d": d, "rep": reps.get(d.replaced_by_id), "reason": _(REASONS.get(d.retired_reason or "other", REASONS["other"]))}
               for d in rows]
    return render(request, "devices_retired.html", user, rows=out, q=q)


def retired_count(user) -> int:
    from .auth import device_scope
    scope = device_scope(user)
    with SessionLocal() as db:
        ids = list(db.scalars(select(Device.id).where(Device.retired_at.is_not(None))))
    return len([i for i in ids if scope is None or i in scope])


def retired_hits(user, q: str) -> int:
    """Cuántos retirados coinciden con una búsqueda de la lista de Dispositivos (para ofrecer «Ver en Retirados»)."""
    if not q.strip():
        return 0
    from .auth import device_scope
    scope = device_scope(user)
    with SessionLocal() as db:
        return sum(1 for d in db.scalars(select(Device).where(Device.retired_at.is_not(None)))
                   if (scope is None or d.id in scope) and matches(d, q))


def links(dev: Device) -> dict:
    """Para la ficha: el equipo que reemplazó a este retirado o el retirado al que reemplaza este, y el enlace para
    comparar el último respaldo del retirado con el último del reemplazo (¿quedó configurado igual?)."""
    from .db import ConfigVersion

    def last(db, did):
        return db.scalar(select(ConfigVersion.id).where(ConfigVersion.device_id == did, ConfigVersion.kind == "running")
                         .order_by(ConfigVersion.id.desc()).limit(1))
    out = {"rep": None, "prev": None, "cmp": None, "reason": ""}
    with SessionLocal() as db:
        if dev.retired_at is not None:
            out["reason"] = _(REASONS.get(dev.retired_reason or "other", REASONS["other"]))
            rep = db.get(Device, dev.replaced_by_id) if dev.replaced_by_id else None
            if rep is not None:
                out["rep"] = {"id": rep.id, "name": rep.name}
                old_v, new_v = last(db, dev.id), last(db, rep.id)
                if old_v and new_v:
                    out["cmp"] = f"/diff?a={old_v}&b={new_v}"
        elif dev.replaces_id:
            prev = db.get(Device, dev.replaces_id)
            if prev is not None and prev.retired_at is not None:
                out["prev"] = {"id": prev.id, "name": prev.name}
                old_v, new_v = last(db, prev.id), last(db, dev.id)
                if old_v and new_v:
                    out["cmp"] = f"/diff?a={old_v}&b={new_v}"
    return out
