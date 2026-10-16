"""Rutas: firmware, fin de vida (EOL/EOS), análisis y recuperación ante desastres."""
from __future__ import annotations

import re

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from sqlalchemy import select

from . import charts, eol, firmware, reports, sysbackup
from .auth import Forbidden, device_scope, filter_ids, require
from .db import Device, DeviceGroup, EolEntry, FirmwareImage, SessionLocal
from .drivers import get_profile
from .web import flash, go, log_action, render

router = APIRouter()


# --------------------------------------------------------------------------- firmware
@router.get("/firmware")
def firmware_page(request: Request):
    user = require(request)
    scope = device_scope(user)
    with SessionLocal() as db:
        devs = [d for d in db.scalars(select(Device).order_by(Device.name))
                if (scope is None or d.id in scope) and firmware.supports_upgrade(d.device_type)]
        for d in devs:
            _ = d.group.name if d.group else None
        groups = db.scalars(select(DeviceGroup).order_by(DeviceGroup.name)).all()
    return render(request, "firmware.html", user, images=firmware.list_images(), devices=devs, groups=groups,
                  supported=sorted({p for p in ("cisco_ios", "cisco_xe", "cisco_nxos", "cisco_asa", "arista_eos",
                                                "juniper_junos")}))


@router.post("/firmware/upload")
async def firmware_upload(request: Request):
    user = require(request, "admin")
    form = await request.form()
    up = form["file"]
    try:
        img = firmware.save_image(up.filename, up.file, user.username, form.get("version"),
                                  form.get("device_type_filter"))
    except ValueError as e:
        flash(request, str(e), "error")
        return go("/firmware")
    log_action(request, user, "firmware_upload", f"{img.filename} md5={img.md5}")
    flash(request, f"Imagen {img.filename} cargada ({img.size:,} bytes, MD5 {img.md5})")
    return go("/firmware")


@router.post("/firmware/{image_id}/delete")
def firmware_delete(request: Request, image_id: int):
    user = require(request, "admin")
    firmware.delete_image(image_id)
    log_action(request, user, "firmware_delete", str(image_id))
    return go("/firmware")


@router.post("/firmware/upgrade")
async def firmware_upgrade(request: Request):
    from .main import _create_cr
    user = require(request, "operator")
    form = await request.form()
    requested = [int(x) for x in form.getlist("device_ids")]
    ids = filter_ids(user, requested)
    if len(ids) != len(requested):
        raise Forbidden()
    if not ids or not form.get("image_id"):
        flash(request, "Seleccione una imagen y al menos un equipo", "error")
        return go("/firmware")
    with SessionLocal() as db:
        img = db.get(FirmwareImage, int(form["image_id"]))
        bad = [d.name for d in db.scalars(select(Device).where(Device.id.in_(ids)))
               if not firmware.supports_upgrade(d.device_type)
               or (img.device_type_filter and not re.search(img.device_type_filter, d.device_type))]
    if bad:
        flash(request, f"La imagen no es compatible con: {', '.join(bad)}", "error")
        return go("/firmware")
    reload = form.get("reload") == "on"
    cr_id = _create_cr(user, f"Firmware {img.filename} en {len(ids)} equipo(s){' + reinicio' if reload else ''}",
                       "", {}, ids, kind="firmware", firmware_id=img.id, reload=reload)
    log_action(request, user, "firmware_request", f"#{cr_id} {img.filename} -> {ids}")
    flash(request, "Solicitud de actualización creada. Un administrador debe aprobarla.")
    return go(f"/changes/{cr_id}")


# --------------------------------------------------------------------------- EOL / EOS
@router.get("/eol")
def eol_page(request: Request, status: str = ""):
    user = require(request)
    rows = eol.report(device_scope(user))
    counts = {}
    for _, _, st in rows:
        counts[st] = counts.get(st, 0) + 1
    if status:
        rows = [r for r in rows if r[2] == status]
    with SessionLocal() as db:
        entries = db.scalars(select(EolEntry).order_by(EolEntry.vendor, EolEntry.model_pattern)).all()
    return render(request, "eol.html", user, rows=rows, entries=entries, counts=counts, status=status,
                  labels=eol.STATUS_LABEL, sev=eol.STATUS_SEV)


@router.post("/eol/entries")
async def eol_add(request: Request):
    user = require(request, "admin")
    form = await request.form()
    try:
        re.compile(form["model_pattern"])
        with SessionLocal() as db:
            db.add(EolEntry(vendor=form.get("vendor") or None, model_pattern=form["model_pattern"].strip(),
                            end_of_sale=eol._date(form.get("end_of_sale")),
                            end_of_sw_maintenance=eol._date(form.get("end_of_sw_maintenance")),
                            end_of_support=eol._date(form.get("end_of_support")),
                            replacement=form.get("replacement") or None, url=form.get("url") or None))
            db.commit()
    except (re.error, ValueError) as e:
        flash(request, f"No se pudo guardar: {e}", "error")
        return go("/eol")
    log_action(request, user, "eol_add", form["model_pattern"])
    flash(request, "Entrada EOL agregada")
    return go("/eol")


@router.post("/eol/import")
async def eol_import(request: Request):
    user = require(request, "admin")
    form = await request.form()
    created, errors = eol.import_csv((await form["file"].read()).decode("utf-8-sig"))
    log_action(request, user, "eol_import", f"{created} entradas")
    flash(request, f"{created} entradas EOL importadas")
    for e in errors[:10]:
        flash(request, e, "error")
    return go("/eol")


@router.post("/eol/entries/{eid}/delete")
def eol_delete(request: Request, eid: int):
    user = require(request, "admin")
    with SessionLocal() as db:
        e = db.get(EolEntry, eid)
        if e:
            db.delete(e)
            db.commit()
    log_action(request, user, "eol_delete", str(eid))
    return go("/eol")


@router.post("/eol/cisco-sync")
def eol_cisco_sync(request: Request):
    user = require(request, "admin")
    try:
        n, errors = eol.cisco_eox_sync()
        flash(request, f"{n} registros actualizados desde Cisco EoX")
        for e in errors[:5]:
            flash(request, e, "error")
    except Exception as e:  # noqa: BLE001
        flash(request, f"Error consultando Cisco EoX: {e}", "error")
    log_action(request, user, "eol_cisco_sync")
    return go("/eol")


# --------------------------------------------------------------------------- análisis
@router.get("/analytics")
def analytics_page(request: Request, days: int = 30):
    user = require(request)
    days = max(7, min(days, 365))
    data = reports.analytics(device_scope(user), days)
    label_every = max(1, days // 6)
    return render(request, "analytics.html", user, data=data, days=days,
                  c_days=charts.columns(data["per_day"], " cambios", label_every=label_every),
                  c_device=charts.bars(data["by_device"], " cambios"),
                  c_trigger=charts.bars(data["by_trigger"], " cambios"),
                  c_user=charts.bars(data["by_user"], " cambios"),
                  c_vendor=charts.bars(data["by_vendor"], " equipos"),
                  c_version=charts.bars(data["by_version"], " equipos"),
                  c_comp=charts.bars([(n, v) for n, v, _ in data["compliance"]], "%", vmax=100,
                                     link=[f"/compliance/policies/{p}" for _, _, p in data["compliance"]]),
                  c_eol=charts.bars(data["eol"], " equipos"))


# --------------------------------------------------------------------------- recuperación ante desastres
@router.get("/system")
def system_page(request: Request):
    user = require(request, "admin")
    from .config import DATABASE_URL, KEY_FILE
    backups = [(p.name, p.stat().st_size, p.stat().st_mtime) for p in sysbackup.list_backups()]
    from . import systime
    from .drivers import PROFILES, unknown_profiles
    zones: dict[str, list[str]] = {}
    for z in systime.timezones():
        zones.setdefault(z.split("/")[0] if "/" in z else "Otras", []).append(z)
    return render(request, "system.html", user, backups=backups, db_url=DATABASE_URL.split("@")[-1],
                  key_file=str(KEY_FILE), profiles_count=len(PROFILES),
                  vendors_count=len({p.vendor for p in PROFILES}), unknown=unknown_profiles(),
                  clock=systime.info(), zones=zones)


@router.post("/system/time")
async def system_time(request: Request):
    """Zona horaria, sincronización NTP y fecha/hora manual del servidor."""
    from . import systime
    user = require(request, "admin")
    form = await request.form()
    action = form.get("action")
    try:
        if action == "timezone":
            tz = form.get("timezone", "")
            systime.set_timezone(tz)
            log_action(request, user, "system_timezone", tz)
            flash(request, f"Zona horaria cambiada a {tz}. Las tareas programadas ya usan la nueva hora.")
            systime.restart_soon()
            # página de espera: el servicio se reinicia unos segundos para aplicar la zona
            return render(request, "restarting.html", user, tz=tz)
        elif action == "ntp":
            on = form.get("ntp") == "on"
            systime.set_ntp(on)
            log_action(request, user, "system_ntp", "activada" if on else "desactivada")
            flash(request, "Sincronización automática de hora (NTP) " + ("activada." if on else "desactivada."))
        elif action == "manual":
            systime.set_time(form.get("date", ""), form.get("time", ""))
            log_action(request, user, "system_time", f"{form.get('date')} {form.get('time')}")
            flash(request, f"Fecha y hora fijadas a {form.get('date')} {form.get('time')} (NTP desactivado).")
    except systime.TimeError as e:
        flash(request, f"No se pudo cambiar: {e}", "error")
    return go("/system#hora")


@router.post("/system/backup")
async def system_backup(request: Request):
    user = require(request, "admin")
    form = await request.form()
    try:
        path = sysbackup.create_backup(include_key=form.get("include_key") == "on")
        flash(request, f"Copia creada: {path.name}")
        log_action(request, user, "system_backup", path.name)
    except Exception as e:  # noqa: BLE001
        flash(request, f"Error creando la copia: {e}", "error")
    return go("/system")


@router.get("/system/backups/{name}")
def system_download(request: Request, name: str):
    user = require(request, "admin")
    path = sysbackup.BACKUP_DIR / name
    if not re.fullmatch(r"opennccm-\d{8}-\d{6}\.tar\.gz", name) or not path.exists():
        return go("/system")
    log_action(request, user, "system_backup_download", name)
    return FileResponse(path, filename=name, media_type="application/gzip")


@router.post("/system/backups/{name}/delete")
def system_delete(request: Request, name: str):
    user = require(request, "admin")
    if re.fullmatch(r"opennccm-\d{8}-\d{6}\.tar\.gz", name):
        (sysbackup.BACKUP_DIR / name).unlink(missing_ok=True)
        log_action(request, user, "system_backup_delete", name)
    return go("/system")


@router.get("/vendors")
def vendors_page(request: Request):
    user = require(request)
    from sqlalchemy import case, func

    from .db import ConfigVersion
    from .drivers import PROFILES
    rows = sorted(PROFILES, key=lambda p: (p.vendor.lower(), p.label))
    # "Comprobado en su red": equipos de la plataforma con al menos un respaldo correcto
    verified: dict[str, dict] = {}
    with SessionLocal() as db:
        q = (select(Device.device_type, func.count(func.distinct(Device.id)), func.min(ConfigVersion.created_at),
                    func.sum(case((Device.custom_backup_cmd.isnot(None), 1), else_=0)))
             .join(ConfigVersion, ConfigVersion.device_id == Device.id)
             .where(ConfigVersion.kind == "running")
             .group_by(Device.device_type))
        for dtype, n, first, custom in db.execute(q):
            verified[dtype] = {"devices": n, "first": first, "custom": bool(custom)}
    vendors = sorted({p.vendor for p in PROFILES}, key=str.lower)
    return render(request, "vendors.html", user, rows=rows, upgrade=firmware.supports_upgrade,
                  get_profile=get_profile, verified=verified, vendors=vendors)
